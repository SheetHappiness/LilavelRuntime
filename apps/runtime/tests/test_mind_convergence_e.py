"""Deterministic MIND-1F-E legacy-cognition retirement proofs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Queue
from typing import Any

import pytest
from lilavel_core import (
    ConversationCore,
    GenerationAccepted,
    GenerationCancelled,
    GenerationCompleted,
    GenerationEvent,
    ModelRequest,
    TextDelta,
)

from lilavel_runtime import (
    ActionProposal,
    ActionProposalKind,
    CognitionCandidate,
    CognitionEpisode,
    CognitionTrigger,
    CognitionTriggerSource,
    FixedPresenceWakePolicy,
    LilavelRuntime,
    LocalCognitionEngine,
    MindState,
    PersistentPresenceRuntime,
    PresenceOutput,
    PresenceToolSessionFactory,
    ProposalApplicationCoordinator,
    SemanticEpisodeStatus,
    StateProposal,
    StateProposalKind,
)
from lilavel_runtime.cognition_model import APPRAISAL_REASON, IDLE_REASON


class _Generation:
    def __init__(self, generation_id: str) -> None:
        self.generation_id = generation_id
        self.epoch = 1
        self._events: Queue[GenerationEvent] = Queue()

    def emit(self, event: GenerationEvent) -> None:
        self._events.put(event)

    def events(self) -> Any:
        while True:
            event = self._events.get()
            yield event
            if isinstance(event, (GenerationCompleted, GenerationCancelled)):
                return


class _ConversationModel:
    def __init__(self) -> None:
        self.generations: list[_Generation] = []
        self.requests: list[ModelRequest] = []
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def shutdown(self) -> None:
        self.closed = True

    def generate(self, request: ModelRequest) -> _Generation:
        self.requests.append(request)
        generation = _Generation(f"generation-{len(self.generations) + 1}")
        self.generations.append(generation)
        return generation

    def generate_for_run(
        self,
        request: ModelRequest,
        *,
        scope_id: str,
        logical_run_id: str,
    ) -> _Generation:
        del scope_id, logical_run_id
        return self.generate(request)

    def cancel(self, generation_id: str) -> bool:
        generation = next(item for item in self.generations if item.generation_id == generation_id)
        generation.emit(GenerationCancelled(generation.generation_id, generation.epoch))
        return True


@dataclass(slots=True)
class _Sink:
    outputs: list[PresenceOutput] = field(default_factory=lambda: list[PresenceOutput]())

    def publish(self, output: PresenceOutput) -> None:
        self.outputs.append(output)


class _BlockingSink:
    def __init__(self) -> None:
        import threading

        self.outputs: list[PresenceOutput] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def publish(self, output: PresenceOutput) -> None:
        if output.kind == "autonomous":
            self.started.set()
            self.release.wait(1.0)
        self.outputs.append(output)


class _Engine:
    def __init__(
        self,
        candidate: Callable[[CognitionEpisode], CognitionCandidate],
        *,
        block: asyncio.Event | None = None,
    ) -> None:
        self._candidate = candidate
        self._block = block
        self.triggers: list[CognitionTrigger] = []
        self.active = 0
        self.max_active = 0

    async def run(self, episode: CognitionEpisode) -> object:
        self.triggers.append(episode.trigger)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self._block is not None:
                await self._block.wait()
            return self._candidate(episode)
        finally:
            self.active -= 1


def _composition(
    engine: Any | None,
    *,
    sink: _Sink | _BlockingSink | None = None,
    wake: bool = False,
    idle_timeout_s: float = 1.0,
) -> tuple[LilavelRuntime, PersistentPresenceRuntime, _ConversationModel, MindState, Any]:
    actual_sink = sink or _Sink()
    model = _ConversationModel()
    state = MindState()
    core = ConversationCore(model, scope_id="local-cli")
    presence = PersistentPresenceRuntime(
        model,
        core,
        actual_sink,
        mind_state=state,
        wake_policy=FixedPresenceWakePolicy(wake=wake),
        idle_timeout_s=idle_timeout_s,
    )
    tools = PresenceToolSessionFactory(actual_sink)
    application = ProposalApplicationCoordinator(
        state,
        scope_id="runtime",
        state_provenance=presence.state_provenance_for,
        tool_registry=tools.registry,
        tool_session_factory=tools,
        action_tool_names={
            ActionProposalKind.SPEAK: "presence.say",
            ActionProposalKind.STAY_SILENT: "presence.stay_silent",
        },
    )
    cognition_engine = (
        LocalCognitionEngine(model, presence.history_for_cognition) if engine is None else engine
    )
    runtime = LilavelRuntime(
        presence=presence,
        cognition_engine=cognition_engine,
        mind_state=state,
        proposal_application_coordinator=application,
        shutdown_timeout=2,
    )
    return runtime, presence, model, state, application


def _finish(generation: _Generation, text: str) -> None:
    generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
    generation.emit(TextDelta(generation.generation_id, generation.epoch, text))
    generation.emit(GenerationCompleted(generation.generation_id, generation.epoch))


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_internal_appraisal_is_actor_owned_and_applies_trusted_state() -> None:
    def candidate(episode: CognitionEpisode) -> CognitionCandidate:
        assert episode.trigger.reason == APPRAISAL_REASON
        return CognitionCandidate(
            state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, "finish later"),)
        )

    engine = _Engine(candidate)
    runtime, presence, model, state, application = _composition(engine)
    await runtime.start()

    user = asyncio.create_task(runtime.submit_user("there is unfinished work"))
    await _wait_for(lambda: len(model.generations) == 1)
    _finish(model.generations[0], "acknowledged")
    await _wait_for(user.done)
    assert await user == "completed"
    await _wait_for(lambda: len(state.intentions()) == 1)
    await _wait_for(lambda: any(item.kind == "intention_created" for item in presence.evidence()))

    assert len(engine.triggers) == 1
    assert engine.triggers[0].source is CognitionTriggerSource.INTERNAL
    assert application.application_count == 1
    assert any(item.kind == "appraisal_queued" for item in presence.evidence())
    assert any(item.kind == "intention_created" for item in presence.evidence())
    assert [(item.role, item.text) for item in presence.canonical_history] == [
        ("user", "there is unfinished work"),
        ("assistant", "acknowledged"),
    ]
    await runtime.stop()


@pytest.mark.asyncio
async def test_local_cognition_engine_uses_one_actor_owned_model_generation() -> None:
    runtime, presence, model, state, application = _composition(None)
    await runtime.start()

    user = asyncio.create_task(runtime.submit_user("evaluate this turn"))
    await _wait_for(lambda: len(model.generations) == 1)
    _finish(model.generations[0], "acknowledged")
    await _wait_for(user.done)
    assert await user == "completed"
    await _wait_for(lambda: len(model.generations) == 2)
    _finish(model.generations[1], '{"action":"create_intention","text":"follow up"}')
    await _wait_for(lambda: len(state.intentions()) == 1)

    assert len(model.requests) == 2
    assert model.requests[1].messages == tuple(presence.history)
    assert application.application_count == 1
    assert [(item.role, item.text) for item in presence.canonical_history] == [
        ("user", "evaluate this turn"),
        ("assistant", "acknowledged"),
    ]
    await runtime.stop()


@pytest.mark.asyncio
async def test_quiet_internal_appraisal_has_no_effect_and_no_retry() -> None:
    engine = _Engine(lambda episode: CognitionCandidate())
    runtime, presence, model, state, application = _composition(engine)
    await runtime.start()

    user = asyncio.create_task(runtime.submit_user("closed matter"))
    await _wait_for(lambda: len(model.generations) == 1)
    _finish(model.generations[0], "done")
    await _wait_for(user.done)
    assert await user == "completed"
    await _wait_for(lambda: any(item.kind == "appraisal_settled" for item in presence.evidence()))
    await asyncio.sleep(0.01)

    assert len(engine.triggers) == 1
    assert state.version == 0
    assert application.application_count == 1
    assert not any(item.kind == "intention_created" for item in presence.evidence())
    await runtime.stop()


@pytest.mark.asyncio
async def test_user_preempts_active_internal_cognition_before_successor_generation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingEngine(_Engine):
        async def run(self, episode: CognitionEpisode) -> object:
            self.triggers.append(episode.trigger)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            started.set()
            try:
                await release.wait()
                return CognitionCandidate()
            finally:
                self.active -= 1

    engine = BlockingEngine(lambda _: CognitionCandidate())
    runtime, _, model, _, _ = _composition(engine)
    await runtime.start()
    trigger = CognitionTrigger(
        (), "internal_fixture", source=CognitionTriggerSource.INTERNAL, source_refs=("fixture:1",)
    )
    internal = await runtime.submit_internal_cognition(trigger)
    await started.wait()

    user = asyncio.create_task(runtime.submit_user("priority"))
    await _wait_for(lambda: len(model.generations) == 1)
    assert not user.done()
    assert (await internal.wait()).status is SemanticEpisodeStatus.CANCELLED
    _finish(model.generations[0], "priority reply")
    await _wait_for(user.done)
    assert await user == "completed"
    assert engine.max_active == 1
    release.set()
    await runtime.stop()


@pytest.mark.asyncio
async def test_idle_speak_uses_p4_application_and_marks_intention_expressed() -> None:
    def candidate(episode: CognitionEpisode) -> CognitionCandidate:
        if episode.trigger.reason == APPRAISAL_REASON:
            return CognitionCandidate(
                state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, "say later"),)
            )
        assert episode.trigger.reason == IDLE_REASON
        return CognitionCandidate(
            action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "hello from actor"),)
        )

    engine = _Engine(candidate)
    runtime, presence, model, state, application = _composition(
        engine, wake=True, idle_timeout_s=0.01
    )
    await runtime.start()
    user = asyncio.create_task(runtime.submit_user("create an intention"))
    await _wait_for(lambda: len(model.generations) == 1)
    _finish(model.generations[0], "noted")
    await _wait_for(user.done)
    assert await user == "completed"
    await _wait_for(
        lambda: any(item.kind == "self_action_recorded" for item in presence.evidence())
    )

    assert len(engine.triggers) == 2
    assert len([item for item in presence.evidence() if item.kind == "idle_opportunity"]) == 1
    assert [item.text for item in presence.mind_state.self_actions()] == ["hello from actor"]
    assert state.intentions()[0].status.value == "expressed"
    assert [item for item in presence.evidence() if item.kind == "cognition_settled"]
    assert [item.text for item in presence.canonical_history] == ["create an intention", "noted"]
    assert application.application_count == 2
    await runtime.stop()


@pytest.mark.asyncio
async def test_stay_silent_is_a_terminal_no_effect_application() -> None:
    def candidate(episode: CognitionEpisode) -> CognitionCandidate:
        if episode.trigger.reason == APPRAISAL_REASON:
            return CognitionCandidate(
                state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, "stay quiet"),)
            )
        return CognitionCandidate(
            action_proposals=(ActionProposal(ActionProposalKind.STAY_SILENT),)
        )

    engine = _Engine(candidate)
    runtime, presence, model, state, _ = _composition(engine, wake=True, idle_timeout_s=0.01)
    await runtime.start()
    user = asyncio.create_task(runtime.submit_user("create a quiet intention"))
    await _wait_for(lambda: len(model.generations) == 1)
    _finish(model.generations[0], "noted")
    await _wait_for(user.done)
    assert await user == "completed"
    await _wait_for(lambda: any(item.kind == "cognition_settled" for item in presence.evidence()))

    assert not [item for item in presence.evidence() if item.kind == "self_action_recorded"]
    assert not [item for item in presence.mind_state.self_actions()]
    assert state.intentions()[0].status.value == "active"
    await runtime.stop()


@pytest.mark.asyncio
async def test_application_in_progress_waits_for_settlement_before_user_successor() -> None:
    sink = _BlockingSink()
    engine = _Engine(
        lambda _: CognitionCandidate(
            action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "effect"),)
        )
    )
    runtime, _, model, _, application = _composition(engine, sink=sink)
    await runtime.start()
    trigger = CognitionTrigger(
        (),
        "application_fixture",
        source=CognitionTriggerSource.INTERNAL,
        source_refs=("fixture:2",),
    )
    internal = await runtime.submit_internal_cognition(trigger)
    await asyncio.sleep(0)
    await sink_started(sink)

    user = asyncio.create_task(runtime.submit_user("after effect"))
    await asyncio.sleep(0.01)
    assert not user.done()
    sink.release.set()
    assert (await internal.wait()).status is SemanticEpisodeStatus.CANCELLED
    await _wait_for(lambda: len(model.generations) == 1)
    _finish(model.generations[0], "after")
    await _wait_for(user.done)
    assert await user == "completed"
    assert application.application_count == 1
    assert [item.text for item in sink.outputs if item.kind == "autonomous"] == ["effect"]
    await runtime.stop()


async def sink_started(sink: _BlockingSink) -> None:
    await _wait_for(sink.started.is_set)


@pytest.mark.asyncio
async def test_internal_replay_fence_and_distinct_opportunities() -> None:
    engine = _Engine(lambda _: CognitionCandidate())
    runtime, _, _, _, application = _composition(engine)
    await runtime.start()
    first_trigger = CognitionTrigger(
        (), "replay", source=CognitionTriggerSource.INTERNAL, source_refs=("fixture:replay",)
    )
    first = await runtime.submit_internal_cognition(first_trigger)
    duplicate = await runtime.submit_internal_cognition(first_trigger)
    assert (await first.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert (await duplicate.wait()).status is SemanticEpisodeStatus.COMPLETED

    second_trigger = CognitionTrigger(
        (), "replay", source=CognitionTriggerSource.INTERNAL, source_refs=("fixture:distinct",)
    )
    second = await runtime.submit_internal_cognition(second_trigger)
    assert (await second.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert len(engine.triggers) == 2
    assert application.application_count == 2
    await runtime.stop()


def test_internal_trigger_is_bounded_and_stable_without_observation_payload() -> None:
    trigger = CognitionTrigger(
        (),
        "internal fixture",
        source=CognitionTriggerSource.INTERNAL,
        source_refs=("conversation:runtime-run",),
    )
    equivalent = CognitionTrigger(
        (),
        "internal fixture",
        source=CognitionTriggerSource.INTERNAL,
        source_refs=("conversation:runtime-run",),
    )
    assert trigger.trigger_id == equivalent.trigger_id
    assert trigger.observation_ids == ()
    assert trigger.wake_intent_id is None


@pytest.mark.asyncio
async def test_shutdown_settles_active_internal_work_without_orphaning_presence() -> None:
    block = asyncio.Event()
    engine = _Engine(lambda _: CognitionCandidate(), block=block)
    runtime, presence, _, _, _ = _composition(engine)
    await runtime.start()
    trigger = CognitionTrigger(
        (), "shutdown", source=CognitionTriggerSource.INTERNAL, source_refs=("fixture:shutdown",)
    )
    admission = await runtime.submit_internal_cognition(trigger)
    await _wait_for(lambda: engine.active == 1)

    await runtime.stop()
    assert (await admission.wait()).status is SemanticEpisodeStatus.CANCELLED
    assert presence.legacy_lane_active is False
    assert engine.active == 0
