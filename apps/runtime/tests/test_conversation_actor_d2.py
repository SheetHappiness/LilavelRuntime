"""Deterministic MIND-1F-D2 CLI conversation-to-actor proofs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Queue
from typing import Any, cast

import pytest
from lilavel_core import (
    ContextMessage,
    ConversationCore,
    GenerationAccepted,
    GenerationCancelled,
    GenerationCompleted,
    GenerationEvent,
    ModelRequest,
    TextDelta,
)

from lilavel_runtime import (
    AutonomousCognitionRunner,
    AutonomousStatus,
    CoreConversationRouter,
    EventSource,
    EventSubmitter,
    FixedPresenceWakePolicy,
    LilavelRuntime,
    MindAppraisal,
    MindAppraisalAction,
    MindAppraisalOutcome,
    MindAppraiser,
    PersistentPresenceRuntime,
    PresenceOutput,
    SemanticCancellationToken,
    SemanticEpisodeStatus,
    SemanticPriority,
    SemanticSourceKind,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    WorldEvent,
)


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


class _Model:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.generations: list[_Generation] = []
        self.cancelled_ids: list[str] = []
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
        self.cancelled_ids.append(generation_id)
        generation = next(item for item in self.generations if item.generation_id == generation_id)
        generation.emit(GenerationCancelled(generation.generation_id, generation.epoch))
        return True


class _Appraiser:
    def admit(self) -> None:
        return None

    def run(self, history: tuple[ContextMessage, ...]) -> MindAppraisalOutcome:
        del history
        return MindAppraisalOutcome(
            "appraisal:fixture",
            AutonomousStatus.COMPLETED,
            MindAppraisal(MindAppraisalAction.NO_CHANGE),
            None,
        )

    def cancel(self) -> bool:
        return False


class _Runner:
    def admit(self) -> None:
        return None

    def run(self, intention: Any) -> Any:
        del intention
        raise AssertionError("legacy autonomous cognition must not run in this proof")

    def cancel(self) -> bool:
        return False


@dataclass(slots=True)
class _Sink:
    outputs: list[PresenceOutput] = field(default_factory=list[PresenceOutput])

    def publish(self, output: PresenceOutput) -> None:
        self.outputs.append(output)


@dataclass(slots=True)
class _Environment:
    actions: list[ToolCall] = field(default_factory=list[ToolCall])
    _watch_release: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def environment_id(self) -> str:
        return "discord"

    async def run(self, submit: EventSubmitter) -> None:
        del submit
        await asyncio.Event().wait()

    async def execute(self, call: ToolCall) -> ToolResult:
        self.actions.append(call)
        if call.tool_name == "conversation.presentation.watch":
            await self._watch_release.wait()
        return ToolResult(call.call_id, ToolResultStatus.OK, None)


def _event(event_id: str, subject: str, text: str) -> WorldEvent:
    return WorldEvent(event_id, EventSource("discord", subject), "direct_message", {"text": text})


def _finish(generation: _Generation, text: str) -> None:
    generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
    generation.emit(TextDelta(generation.generation_id, generation.epoch, text))
    generation.emit(GenerationCompleted(generation.generation_id, generation.epoch))


async def _wait_for(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


def _composition() -> tuple[LilavelRuntime, PersistentPresenceRuntime, _Model, _Environment, _Sink]:
    model = _Model()
    sink = _Sink()
    core = ConversationCore(model, scope_id="local-cli")
    presence = PersistentPresenceRuntime(
        model,
        core,
        cast(AutonomousCognitionRunner, _Runner()),
        sink,
        appraiser=cast(MindAppraiser, _Appraiser()),
        wake_policy=FixedPresenceWakePolicy(),
        idle_timeout_s=60,
    )
    environment = _Environment()
    router = CoreConversationRouter(
        runtime_factory=lambda: model,
        close_timeout_s=1,
    )
    runtime = LilavelRuntime(event_router=router, presence=presence, shutdown_timeout=2)
    runtime.register_environment(environment)
    return runtime, presence, model, environment, sink


@pytest.mark.asyncio
async def test_cli_user_is_one_actor_episode_and_core_is_canonical() -> None:
    runtime, presence, model, _, sink = _composition()
    await runtime.start()

    user = asyncio.create_task(runtime.submit_user("hello"))
    await _wait_for(lambda: len(model.generations) == 1)
    assert runtime.semantic_actor.active_count == 1
    assert runtime.semantic_actor.active_episode is not None
    assert runtime.semantic_actor.active_episode.priority.value == "user"

    _finish(model.generations[0], "reply")
    await _wait_for(user.done)
    assert await user == "completed"
    assert [(item.role, item.text) for item in presence.canonical_history] == [
        ("user", "hello"),
        ("assistant", "reply"),
    ]
    assert [item.kind for item in sink.outputs] == [
        "conversation_delta",
        "conversation_complete",
    ]
    assert (
        sum(
            item.status is SemanticEpisodeStatus.COMPLETED and item.request_id.startswith("cli:")
            for item in runtime.semantic_actor.evidence()
        )
        == 1
    )
    await runtime.stop()


@pytest.mark.asyncio
async def test_composed_presence_submission_is_bound_back_to_runtime_actor() -> None:
    runtime, presence, model, _, _ = _composition()
    await runtime.start()

    user = asyncio.create_task(presence.submit_user("bound"))
    await _wait_for(lambda: len(model.generations) == 1)
    _finish(model.generations[0], "reply")
    await _wait_for(user.done)
    assert await user == "completed"
    assert any(
        item.request_id.startswith("cli:") and item.status is SemanticEpisodeStatus.COMPLETED
        for item in runtime.semantic_actor.evidence()
    )
    await runtime.stop()


@pytest.mark.asyncio
async def test_cli_user_preempts_actor_non_user_work_before_core_starts() -> None:
    runtime, _, model, _, _ = _composition()
    await runtime.start()
    mind_started = asyncio.Event()
    mind_cancelled = asyncio.Event()
    allow_mind_return = asyncio.Event()

    async def mind(episode: Any, cancellation: SemanticCancellationToken) -> object:
        del episode
        mind_started.set()
        await cancellation.wait()
        mind_cancelled.set()
        await allow_mind_return.wait()
        return "mind"

    mind_admission = await runtime.semantic_actor.admit(
        runtime.semantic_actor.create_request(
            "mind-before-cli",
            source_kind=SemanticSourceKind.TEMPORAL,
            priority=SemanticPriority.NON_USER,
            executor=mind,
        )
    )
    await mind_started.wait()
    cli = asyncio.create_task(runtime.submit_user("priority cli"))
    await mind_cancelled.wait()
    assert model.generations == []
    allow_mind_return.set()
    assert (await mind_admission.wait()).status is SemanticEpisodeStatus.CANCELLED
    await _wait_for(lambda: len(model.generations) == 1)
    _finish(model.generations[0], "cli reply")
    await _wait_for(cli.done)
    assert await cli == "completed"
    await runtime.stop()


@pytest.mark.asyncio
async def test_temporal_non_user_work_waits_behind_actor_owned_cli_turn() -> None:
    runtime, _, model, _, _ = _composition()
    await runtime.start()
    cli = asyncio.create_task(runtime.submit_user("hold cli"))
    await _wait_for(lambda: len(model.generations) == 1)
    temporal_started = asyncio.Event()

    async def temporal(episode: Any, cancellation: SemanticCancellationToken) -> object:
        del episode, cancellation
        temporal_started.set()
        return "temporal"

    temporal_admission = await runtime.semantic_actor.admit(
        runtime.semantic_actor.create_request(
            "temporal-after-cli",
            source_kind=SemanticSourceKind.TEMPORAL,
            priority=SemanticPriority.NON_USER,
            executor=temporal,
        )
    )
    await asyncio.sleep(0)
    assert not temporal_started.is_set()
    _finish(model.generations[0], "cli reply")
    await _wait_for(cli.done)
    assert await cli == "completed"
    await temporal_started.wait()
    assert (await temporal_admission.wait()).status is SemanticEpisodeStatus.COMPLETED
    await runtime.stop()


@pytest.mark.asyncio
async def test_cli_and_discord_user_episodes_serialize_in_both_directions() -> None:
    runtime, presence, model, environment, _ = _composition()
    await runtime.start()

    cli = asyncio.create_task(runtime.submit_user("from cli"))
    await _wait_for(lambda: len(model.generations) == 1)
    discord_receipt = await runtime.admit_observation(
        _event("discord-1", "subject-a", "from discord")
    )
    await runtime.cognition_step(discord_receipt)
    assert len(model.generations) == 1
    assert runtime.semantic_actor.queued_count == 1
    _finish(model.generations[0], "cli reply")
    await _wait_for(lambda: len(model.generations) == 2)
    assert model.requests[1].messages == (ContextMessage("user", "from discord"),)
    _finish(model.generations[1], "discord reply")
    await _wait_for(cli.done)
    assert await cli == "completed"
    await _wait_for(lambda: runtime.active_route_count == 0)

    assert [(item.role, item.text) for item in presence.history] == [
        ("user", "from cli"),
        ("assistant", "cli reply"),
    ]
    assert runtime.semantic_actor.max_active == 1
    assert len([call for call in environment.actions if call.tool_name.endswith("complete")]) == 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_discord_then_cli_also_waits_without_environment_preference() -> None:
    runtime, presence, model, _, _ = _composition()
    await runtime.start()

    discord_receipt = await runtime.admit_observation(_event("discord-1", "subject-a", "discord"))
    await runtime.cognition_step(discord_receipt)
    await _wait_for(lambda: len(model.generations) == 1)
    cli = asyncio.create_task(runtime.submit_user("cli"))
    await asyncio.sleep(0)
    assert len(model.generations) == 1
    _finish(model.generations[0], "discord reply")
    await _wait_for(lambda: len(model.generations) == 2)
    assert model.requests[1].messages == (ContextMessage("user", "cli"),)
    _finish(model.generations[1], "cli reply")
    await _wait_for(cli.done)
    assert await cli == "completed"
    await _wait_for(lambda: runtime.active_route_count == 0)
    assert [(item.role, item.text) for item in presence.history] == [
        ("user", "cli"),
        ("assistant", "cli reply"),
    ]
    await runtime.stop()


@pytest.mark.asyncio
async def test_cli_replay_fence_and_distinct_identical_text_submissions() -> None:
    runtime, _, model, _, _ = _composition()
    await runtime.start()

    first = asyncio.create_task(runtime.submit_user("same", submission_id="submission-1"))
    await _wait_for(lambda: len(model.generations) == 1)
    _finish(model.generations[0], "one")
    await _wait_for(first.done)
    assert await first == "completed"
    assert await runtime.submit_user("different", submission_id="submission-1") == "completed"
    assert len(model.generations) == 1

    second = asyncio.create_task(runtime.submit_user("same"))
    await _wait_for(lambda: len(model.generations) == 2)
    _finish(model.generations[1], "two")
    await _wait_for(second.done)
    assert await second == "completed"
    assert (
        len(
            {
                item.request_id
                for item in runtime.semantic_actor.evidence()
                if item.request_id.startswith("cli:")
                and item.status is SemanticEpisodeStatus.QUEUED
            }
        )
        == 2
    )
    await runtime.stop()


@pytest.mark.asyncio
async def test_cli_cancellation_streams_interrupt_without_assistant_commit() -> None:
    runtime, presence, model, _, sink = _composition()
    await runtime.start()
    user = asyncio.create_task(runtime.submit_user("cancel me"))
    await _wait_for(lambda: len(model.generations) == 1)
    model.generations[0].emit(GenerationAccepted(model.generations[0].generation_id, 1))
    model.generations[0].emit(TextDelta(model.generations[0].generation_id, 1, "partial"))
    await _wait_for(lambda: any(item.kind == "conversation_delta" for item in sink.outputs))

    await runtime.stop()
    assert await user == "cancelled"
    assert model.cancelled_ids == ["generation-1"]
    assert [(item.role, item.text) for item in presence.canonical_history] == [
        ("user", "cancel me")
    ]
    assert [item.kind for item in sink.outputs] == [
        "conversation_delta",
        "conversation_interrupted",
    ]
    assert runtime.semantic_actor.active_count == 0
    assert runtime.semantic_actor.queued_count == 0
    assert model.closed


@pytest.mark.asyncio
async def test_cli_user_excludes_legacy_presence_lane_for_full_actor_lifetime() -> None:
    runtime, presence, model, _, _ = _composition()
    await runtime.start()

    user = asyncio.create_task(runtime.submit_user("hold legacy"))
    await _wait_for(lambda: len(model.generations) == 1)
    assert presence.actor_user_block_count == 1
    assert presence.legacy_lane_active is False
    _finish(model.generations[0], "done")
    await _wait_for(user.done)
    assert await user == "completed"
    assert presence.actor_user_block_count == 0
    await runtime.stop()
