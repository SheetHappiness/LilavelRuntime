"""Deterministic MIND-1F-D1 conversation-to-actor proofs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from queue import Queue
from typing import Any

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
    CognitionTrigger,
    ConversationExecutionAdapter,
    CoreConversationRouter,
    EventSource,
    EventSubmitter,
    LilavelRuntime,
    Observation,
    SemanticActor,
    SemanticCancellationToken,
    SemanticEpisode,
    SemanticEpisodeStatus,
    SemanticPriority,
    SemanticSourceKind,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    WorldEvent,
)


class _Generation:
    def __init__(self, generation_id: str, epoch: int) -> None:
        self.generation_id = generation_id
        self.epoch = epoch
        self._events: Queue[GenerationEvent] = Queue()

    def emit(self, event: GenerationEvent) -> None:
        self._events.put(event)

    def events(self) -> Any:
        while True:
            event = self._events.get()
            yield event
            if isinstance(event, (GenerationCompleted, GenerationCancelled)):
                return


class _Runtime:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.generations: list[_Generation] = []
        self.cancelled_ids: list[str] = []
        self.shutdown_called = False

    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        self.shutdown_called = True

    def generate(self, request: ModelRequest) -> _Generation:
        self.requests.append(request)
        generation = _Generation(f"generation-{len(self.generations) + 1}", 1)
        self.generations.append(generation)
        return generation

    def cancel(self, generation_id: str) -> bool:
        self.cancelled_ids.append(generation_id)
        generation = next(item for item in self.generations if item.generation_id == generation_id)
        generation.emit(GenerationCancelled(generation.generation_id, generation.epoch))
        return True


@dataclass(slots=True)
class _Environment:
    actions: list[ToolCall] = field(default_factory=lambda: list[ToolCall]())

    @property
    def environment_id(self) -> str:
        return "fixture"

    async def run(self, submit: EventSubmitter) -> None:
        del submit
        await asyncio.Event().wait()

    async def execute(self, call: ToolCall) -> ToolResult:
        self.actions.append(call)
        if call.tool_name == "conversation.presentation.watch":
            await asyncio.Event().wait()
        return ToolResult(call.call_id, ToolResultStatus.OK, None)


def _event(event_id: str, subject: str, text: str) -> WorldEvent:
    return WorldEvent(event_id, EventSource("fixture", subject), "direct_message", {"text": text})


def _finish(generation: _Generation, text: str) -> None:
    generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
    generation.emit(TextDelta(generation.generation_id, generation.epoch, text))
    generation.emit(GenerationCompleted(generation.generation_id, generation.epoch))


async def _wait_until(predicate: Any) -> None:
    deadline = asyncio.get_running_loop().time() + 2.0
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("fixture condition did not become true")
        await asyncio.sleep(0)


def _runtime(
    model_runtime: _Runtime,
    environment: _Environment,
    *,
    core_factory: Any = ConversationCore,
) -> LilavelRuntime:
    router = CoreConversationRouter(
        runtime_factory=lambda: model_runtime,
        core_factory=core_factory,
        close_timeout_s=1.0,
    )
    runtime = LilavelRuntime(event_router=router, shutdown_timeout=2.0)
    runtime.register_environment(environment)
    return runtime


@pytest.mark.asyncio
async def test_reactive_conversation_is_admitted_by_actor_once_and_core_remains_canonical() -> None:
    model_runtime = _Runtime()
    environment = _Environment()
    runtime = _runtime(model_runtime, environment)
    await runtime.start()

    receipt = await runtime.admit_observation(_event("event-a", "subject-a", "hello"))
    decision = await runtime.cognition_step(receipt)
    duplicate = await runtime.cognition_step(receipt)

    assert isinstance(decision, CognitionTrigger)
    assert duplicate == decision
    await _wait_until(lambda: len(model_runtime.generations) == 1)
    _finish(model_runtime.generations[0], "reply")
    await _wait_until(lambda: runtime.active_route_count == 0)

    assert model_runtime.requests[0].messages == (ContextMessage("user", "hello"),)
    assert runtime.semantic_actor.max_active == 1
    assert runtime.semantic_actor.evidence()[-1].priority is SemanticPriority.USER
    assert sum(
        item.status is SemanticEpisodeStatus.DUPLICATE for item in runtime.semantic_actor.evidence()
    )
    assert runtime.health().reactive_steps == 2
    assert runtime.health().cognition_triggers == 2
    await runtime.stop()


@pytest.mark.asyncio
async def test_user_preempts_non_user_and_waits_for_mind_settlement_before_core() -> None:
    model_runtime = _Runtime()
    environment = _Environment()
    runtime = _runtime(model_runtime, environment)
    await runtime.start()

    mind_started = asyncio.Event()
    mind_settled = asyncio.Event()
    allow_mind_return = asyncio.Event()

    async def mind_executor(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode
        mind_started.set()
        await cancellation.wait()
        mind_settled.set()
        await allow_mind_return.wait()
        return "mind"

    mind = await runtime.semantic_actor.admit(
        runtime.semantic_actor.create_request(
            "mind-episode",
            source_kind=SemanticSourceKind.TEMPORAL,
            priority=SemanticPriority.NON_USER,
            executor=mind_executor,
        )
    )
    await mind_started.wait()

    receipt = await runtime.admit_observation(_event("event-user", "subject-a", "hello"))
    await runtime.cognition_step(receipt)
    await mind_settled.wait()
    assert model_runtime.generations == []
    assert runtime.semantic_actor.max_active == 1

    allow_mind_return.set()
    assert (await mind.wait()).status is SemanticEpisodeStatus.CANCELLED
    await _wait_until(lambda: len(model_runtime.generations) == 1)
    _finish(model_runtime.generations[0], "reply")
    await _wait_until(lambda: runtime.active_route_count == 0)

    assert model_runtime.requests[0].messages == (ContextMessage("user", "hello"),)
    assert model_runtime.cancelled_ids == []
    await runtime.stop()


@pytest.mark.asyncio
async def test_temporal_non_user_work_waits_behind_active_user_conversation() -> None:
    model_runtime = _Runtime()
    environment = _Environment()
    runtime = _runtime(model_runtime, environment)
    await runtime.start()

    receipt = await runtime.admit_observation(_event("event-user", "subject-a", "hello"))
    await runtime.cognition_step(receipt)
    await _wait_until(lambda: len(model_runtime.generations) == 1)

    temporal_started = asyncio.Event()

    async def temporal_executor(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode, cancellation
        temporal_started.set()
        return "temporal"

    temporal = await runtime.semantic_actor.admit(
        runtime.semantic_actor.create_request(
            "temporal-episode",
            source_kind=SemanticSourceKind.TEMPORAL,
            priority=SemanticPriority.NON_USER,
            executor=temporal_executor,
        )
    )
    await asyncio.sleep(0)
    assert temporal_started.is_set() is False

    _finish(model_runtime.generations[0], "reply")
    await temporal_started.wait()
    assert (await temporal.wait()).status is SemanticEpisodeStatus.COMPLETED
    await _wait_until(lambda: runtime.active_route_count == 0)
    await runtime.stop()


@pytest.mark.asyncio
async def test_shutdown_settles_active_conversation_and_queued_non_user_work() -> None:
    model_runtime = _Runtime()
    environment = _Environment()
    runtime = _runtime(model_runtime, environment)
    await runtime.start()

    receipt = await runtime.admit_observation(_event("event-user", "subject-a", "hello"))
    await runtime.cognition_step(receipt)
    await _wait_until(lambda: len(model_runtime.generations) == 1)

    async def queued_temporal_executor(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode, cancellation
        return "queued"

    temporal = await runtime.semantic_actor.admit(
        runtime.semantic_actor.create_request(
            "queued-temporal",
            source_kind=SemanticSourceKind.TEMPORAL,
            priority=SemanticPriority.NON_USER,
            executor=queued_temporal_executor,
        )
    )
    await runtime.stop()

    assert runtime.state.value == "stopped"
    assert model_runtime.cancelled_ids == ["generation-1"]
    assert (await temporal.wait()).status is SemanticEpisodeStatus.CANCELLED
    assert runtime.semantic_actor.active_count == 0
    assert runtime.semantic_actor.queued_count == 0
    assert runtime.semantic_actor.max_active == 1
    assert runtime.semantic_actor.state.value == "stopped"
    queued_evidence = [
        item for item in runtime.semantic_actor.evidence() if item.request_id == "queued-temporal"
    ]
    assert queued_evidence
    assert queued_evidence[-1].status is SemanticEpisodeStatus.CANCELLED
    assert queued_evidence[-1].priority is SemanticPriority.NON_USER


@pytest.mark.asyncio
async def test_uncontainable_conversation_cancellation_poisoned_actor_rejects_user_successor() -> (
    None
):
    router_started = asyncio.Event()

    class UncontainableRouter:
        async def route(self, observation: Observation, execute: Any) -> None:
            del observation, execute
            router_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as error:
                raise RuntimeError("route did not contain") from error

        async def close(self) -> None:
            return None

    actor = SemanticActor(scope_id="runtime", settlement_timeout=0.01)
    adapter = ConversationExecutionAdapter(UncontainableRouter())
    observation = Observation("observation", 1, _event("event", "subject", "hello"))
    calls: list[str] = []

    async def conversation_executor(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode

        async def execute(call: ToolCall) -> ToolResult:
            del call
            return ToolResult("call", ToolResultStatus.OK, None)

        return await adapter.execute(
            observation,
            execute,
            cancellation,
        )

    async def successor_executor(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode, cancellation
        calls.append("successor")
        return "must-not-run"

    await actor.start()
    active = await actor.admit(
        actor.create_request(
            "conversation-uncertain",
            source_kind=SemanticSourceKind.EXTERNAL,
            priority=SemanticPriority.NON_USER,
            executor=conversation_executor,
        )
    )
    await _wait_until(lambda: actor.active_count == 1)
    await router_started.wait()
    successor = await actor.admit(
        actor.create_request(
            "user-successor",
            source_kind=SemanticSourceKind.USER,
            priority=SemanticPriority.USER,
            executor=successor_executor,
        )
    )

    active_settlement = await active.wait()
    successor_settlement = await successor.wait()
    assert active_settlement.status is SemanticEpisodeStatus.FAILED, actor.evidence()
    assert successor_settlement.status is SemanticEpisodeStatus.CANCELLED, actor.evidence()
    assert calls == []
    assert actor.state.value == "poisoned"
    await actor.stop()
