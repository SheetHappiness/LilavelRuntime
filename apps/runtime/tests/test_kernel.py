"""Deterministic lifecycle and bounded-ingress proofs for LilavelRuntime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from lilavel_runtime import (
    ActionExecutor,
    CoreConversationRouter,
    DuplicateEnvironment,
    DuplicateTool,
    EventSource,
    EventSubmitter,
    LilavelRuntime,
    LilavelRuntimeError,
    Observation,
    RuntimeFailed,
    RuntimeNotRunning,
    RuntimeRegistrationClosed,
    RuntimeState,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    ToolSpec,
    WorldEvent,
)


def _tool_calls() -> list[ToolCall]:
    return []


def _observations() -> list[Observation]:
    return []


def _event(number: int) -> WorldEvent:
    return WorldEvent(f"event-{number}", EventSource("fixture"), "observation", {"n": number})


@pytest.mark.asyncio
async def test_zero_environment_runtime_stays_healthy_and_shuts_down_cleanly() -> None:
    runtime = LilavelRuntime(event_queue_size=2)

    await runtime.start()
    await asyncio.sleep(0)

    health = runtime.health()
    assert health.state is RuntimeState.RUNNING
    assert health.healthy is True
    assert health.environments == 0
    assert health.tools == 0
    assert health.accepted_events == 0
    assert health.processed_events == 0
    assert health.wake_decisions == 0

    await runtime.stop()

    assert runtime.health().state is RuntimeState.STOPPED
    assert runtime.health().failure_code is None


@pytest.mark.asyncio
async def test_context_manager_owns_clean_lifecycle() -> None:
    runtime = LilavelRuntime()
    async with runtime:
        assert runtime.state is RuntimeState.RUNNING
    assert runtime.state is RuntimeState.STOPPED


@dataclass(slots=True)
class _FixturePresence:
    started: bool = False
    stopped: bool = False
    inputs: list[str] = field(default_factory=lambda: [])
    closed: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self) -> None:
        self.started = True

    async def submit_user(self, text: str) -> str:
        self.inputs.append(text)
        return "completed"

    async def wait(self) -> None:
        await self.closed.wait()

    async def stop(self) -> None:
        self.stopped = True
        self.closed.set()


@pytest.mark.asyncio
async def test_runtime_owns_optional_local_presence_lifecycle_and_input() -> None:
    presence = _FixturePresence()
    runtime = LilavelRuntime(presence=presence)

    await runtime.start()
    assert presence.started
    assert await runtime.submit_user("hello") == "completed"
    await runtime.stop()

    assert presence.inputs == ["hello"]
    assert presence.stopped
    assert runtime.state is RuntimeState.STOPPED


@pytest.mark.asyncio
async def test_concurrent_stop_callers_share_one_settlement() -> None:
    runtime = LilavelRuntime()
    await runtime.start()

    await asyncio.gather(runtime.stop(), runtime.stop())

    assert runtime.state is RuntimeState.STOPPED
    assert runtime.health().failure_code is None


@pytest.mark.asyncio
async def test_unstarted_stop_is_clean_and_runtime_cannot_be_reused() -> None:
    runtime = LilavelRuntime()
    await runtime.stop()
    await runtime.stop()
    assert runtime.state is RuntimeState.STOPPED
    with pytest.raises(LilavelRuntimeError, match="cannot start runtime"):
        await runtime.start()


@pytest.mark.asyncio
async def test_observation_window_is_bounded_and_keeps_recent_admissions() -> None:
    runtime = LilavelRuntime(event_queue_size=1, observation_window_size=2)
    await runtime.start()

    receipts = [await runtime.admit_observation(_event(number)) for number in range(1, 4)]
    await runtime.stop()

    assert [item.event.event_id for item in runtime.recent_observations()] == [
        "event-2",
        "event-3",
    ]
    assert [receipt.status.value for receipt in receipts] == ["admitted", "admitted", "admitted"]
    assert runtime.health().accepted_events == 3
    assert runtime.health().processed_events == 3
    assert runtime.health().observation_window_size == 2


@pytest.mark.asyncio
async def test_admission_does_not_invoke_reactive_step_or_wake_side_effects() -> None:
    model_calls = 0

    def runtime_factory() -> Any:
        nonlocal model_calls
        model_calls += 1
        raise AssertionError("observation admission must not create a model runtime")

    router = CoreConversationRouter(runtime_factory=runtime_factory)
    adapter = _RecordingEnvironment()
    runtime = LilavelRuntime(event_router=router)
    runtime.register_environment(adapter)

    await runtime.start()
    receipt = await runtime.admit_observation(_event(1))
    await asyncio.sleep(0)

    assert runtime.recent_observations()[0].event.event_id == "event-1"
    assert receipt.event_id == "event-1"
    assert router.session_count == 0
    assert router.history("fixture", "unused") is None
    assert model_calls == 0
    assert adapter.actions == []
    assert runtime.active_route_count == 0
    assert runtime.health().reactive_steps == 0
    assert runtime.health().wake_decisions == 0
    await runtime.stop()


@dataclass(slots=True)
class _FixtureAdapter:
    stopped: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def environment_id(self) -> str:
        return "fixture-environment"

    async def run(self, submit: EventSubmitter) -> None:
        try:
            await submit(_event(1))
            await asyncio.Event().wait()
        finally:
            self.stopped.set()

    async def execute(self, call: ToolCall) -> ToolResult:
        return ToolResult(call.call_id, ToolResultStatus.OK, None)


@pytest.mark.asyncio
async def test_registered_environment_is_runtime_owned_and_settled() -> None:
    adapter = _FixtureAdapter()
    runtime = LilavelRuntime()
    runtime.register_environment(adapter)

    await runtime.start()
    while runtime.health().processed_events == 0:
        await asyncio.sleep(0)
    await runtime.stop()

    assert adapter.stopped.is_set()
    assert runtime.health().environments == 1
    assert runtime.state is RuntimeState.STOPPED


@dataclass(slots=True)
class _FailingAdapter:
    @property
    def environment_id(self) -> str:
        return "failing-environment"

    async def run(self, submit: EventSubmitter) -> None:
        del submit
        raise ValueError("fixture failure")

    async def execute(self, call: ToolCall) -> ToolResult:
        return ToolResult(call.call_id, ToolResultStatus.OK, None)


@pytest.mark.asyncio
async def test_owned_task_failure_fails_runtime_closed() -> None:
    runtime = LilavelRuntime()
    runtime.register_environment(_FailingAdapter())
    await runtime.start()
    while runtime.state is not RuntimeState.FAILED:
        await asyncio.sleep(0)

    assert runtime.health().healthy is False
    assert runtime.health().failure_code == "ExceptionGroup"
    with pytest.raises(RuntimeFailed):
        await runtime.stop()
    with pytest.raises(RuntimeNotRunning):
        await runtime.submit(_event(1))


@pytest.mark.asyncio
async def test_registration_closes_at_start_and_duplicates_are_rejected() -> None:
    runtime = LilavelRuntime()
    adapter = _FixtureAdapter()
    tool = ToolSpec("fixture", "Fixture tool", {"type": "object"})
    runtime.register_environment(adapter)
    runtime.register_tool(tool)

    with pytest.raises(DuplicateEnvironment):
        runtime.register_environment(adapter)
    with pytest.raises(DuplicateTool):
        runtime.register_tool(tool)

    await runtime.start()
    with pytest.raises(RuntimeRegistrationClosed):
        runtime.register_tool(ToolSpec("late", "Late tool", {}))
    assert runtime.tools() == (tool,)
    await runtime.stop()


@pytest.mark.asyncio
async def test_ingress_rejects_events_outside_running_state() -> None:
    runtime = LilavelRuntime()
    with pytest.raises(RuntimeNotRunning):
        await runtime.submit(_event(1))
    await runtime.start()
    await runtime.stop()
    with pytest.raises(RuntimeNotRunning):
        await runtime.submit(_event(2))


@dataclass(slots=True)
class _RecordingRouter:
    events: list[Observation] = field(default_factory=_observations)
    closed: bool = False

    async def route(self, observation: Observation, execute: ActionExecutor) -> None:
        self.events.append(observation)
        result = await execute(
            ToolCall(
                "action-1",
                "conversation.presentation.complete",
                {"event_id": observation.event.event_id, "text": "reply"},
            )
        )
        assert result.status is ToolResultStatus.OK

    async def close(self) -> None:
        self.closed = True


@dataclass(slots=True)
class _RecordingEnvironment:
    actions: list[ToolCall] = field(default_factory=_tool_calls)
    stopped: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def environment_id(self) -> str:
        return "fixture-environment"

    async def run(self, submit: EventSubmitter) -> None:
        try:
            await submit(
                WorldEvent(
                    "event-1",
                    EventSource(self.environment_id, "opaque-subject"),
                    "direct_message",
                    {"text": "hello"},
                )
            )
            await asyncio.Event().wait()
        finally:
            self.stopped.set()

    async def execute(self, call: ToolCall) -> ToolResult:
        self.actions.append(call)
        return ToolResult(call.call_id, ToolResultStatus.OK, {"status": "ok"})


@pytest.mark.asyncio
async def test_reactive_step_routes_only_after_explicit_admission_receipt() -> None:
    router = _RecordingRouter()
    adapter = _RecordingEnvironment()
    runtime = LilavelRuntime(event_router=router)
    runtime.register_environment(adapter)

    await runtime.start()
    receipt = await runtime.admit_observation(
        WorldEvent(
            "event-1",
            EventSource(adapter.environment_id, "opaque-subject"),
            "direct_message",
            {"text": "hello"},
        )
    )
    await asyncio.sleep(0)
    assert router.events == []
    assert adapter.actions == []

    await runtime.reactive_step(receipt)
    while runtime.active_route_count:
        await asyncio.sleep(0)
    await runtime.stop()

    assert [observation.event.event_id for observation in router.events] == ["event-1"]
    assert [call.tool_name for call in adapter.actions] == ["conversation.presentation.complete"]
    assert adapter.actions[0].model_trust == "untrusted"
    assert adapter.stopped.is_set()
    assert router.closed is True


@dataclass(slots=True)
class _CloseFailingRouter:
    async def route(self, observation: Observation, execute: ActionExecutor) -> None:
        del observation, execute

    async def close(self) -> None:
        raise ValueError("fixture router close failure")


@pytest.mark.asyncio
async def test_router_shutdown_failure_settles_supervisor_and_fails_closed() -> None:
    runtime = LilavelRuntime(event_router=_CloseFailingRouter())
    await runtime.start()

    with pytest.raises(ValueError, match="fixture router close failure"):
        await runtime.stop()

    assert runtime.state is RuntimeState.FAILED
    assert runtime.health().failure_code == "ValueError"
