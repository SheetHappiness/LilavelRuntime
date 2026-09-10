"""Deterministic lifecycle and bounded-ingress proofs for LilavelRuntime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from lilavel_runtime import (
    ActionExecutor,
    DirectMessageWakePolicy,
    DuplicateEnvironment,
    DuplicateTool,
    EventSource,
    EventSubmitter,
    LilavelRuntime,
    LilavelRuntimeError,
    RuntimeFailed,
    RuntimeNotRunning,
    RuntimeRegistrationClosed,
    RuntimeShutdownTimeout,
    RuntimeState,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    ToolSpec,
    WakeDecision,
    WorldEvent,
)


def _world_events() -> list[WorldEvent]:
    return []


def _tool_calls() -> list[ToolCall]:
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


@dataclass(slots=True)
class _BlockingPolicy:
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def decide(self, event: WorldEvent) -> WakeDecision:
        del event
        self.entered.set()
        await self.release.wait()
        return WakeDecision(wake=False)


@pytest.mark.asyncio
async def test_bounded_ingress_applies_async_backpressure() -> None:
    policy = _BlockingPolicy()
    runtime = LilavelRuntime(event_queue_size=1, wake_policy=policy)
    await runtime.start()

    await runtime.submit(_event(1))
    await policy.entered.wait()
    await runtime.submit(_event(2))
    blocked = asyncio.create_task(runtime.submit(_event(3)))
    await asyncio.sleep(0)

    assert runtime.health().queue_size == 1
    assert blocked.done() is False

    policy.release.set()
    await asyncio.wait_for(blocked, timeout=1)
    await runtime.stop()

    assert runtime.health().accepted_events == 3
    assert runtime.health().processed_events == 3


@pytest.mark.asyncio
async def test_shutdown_rejects_a_submit_blocked_by_backpressure() -> None:
    policy = _BlockingPolicy()
    runtime = LilavelRuntime(event_queue_size=1, wake_policy=policy)
    await runtime.start()
    await runtime.submit(_event(1))
    await policy.entered.wait()
    await runtime.submit(_event(2))
    blocked = asyncio.create_task(runtime.submit(_event(3)))
    await asyncio.sleep(0)

    stopping = asyncio.create_task(runtime.stop())
    with pytest.raises(RuntimeNotRunning, match="admission closed"):
        await blocked
    policy.release.set()
    await stopping

    assert runtime.state is RuntimeState.STOPPED
    assert runtime.health().accepted_events == 2
    assert runtime.health().processed_events == 2


@pytest.mark.asyncio
async def test_unsettled_ingress_fails_shutdown_closed_at_deadline() -> None:
    policy = _BlockingPolicy()
    runtime = LilavelRuntime(wake_policy=policy, shutdown_timeout=0.01)
    await runtime.start()
    await runtime.submit(_event(1))
    await policy.entered.wait()

    with pytest.raises(RuntimeShutdownTimeout):
        await runtime.stop()
    await asyncio.sleep(0)

    assert runtime.state is RuntimeState.FAILED
    assert runtime.health().healthy is False
    with pytest.raises(LilavelRuntimeError):
        await runtime.start()


@dataclass(slots=True)
class _WakeAllPolicy:
    async def decide(self, event: WorldEvent) -> WakeDecision:
        del event
        return WakeDecision(wake=True, reason="fixture")


@pytest.mark.asyncio
async def test_wake_policy_is_observed_without_starting_other_subsystems() -> None:
    runtime = LilavelRuntime(wake_policy=_WakeAllPolicy())
    await runtime.start()
    await runtime.submit(_event(1))
    await runtime.stop()

    health = runtime.health()
    assert health.processed_events == 1
    assert health.wake_decisions == 1


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
    events: list[WorldEvent] = field(default_factory=_world_events)
    closed: bool = False

    async def route(self, event: WorldEvent, execute: ActionExecutor) -> None:
        self.events.append(event)
        result = await execute(
            ToolCall(
                "action-1",
                "conversation.presentation.complete",
                {"event_id": event.event_id, "text": "reply"},
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
async def test_positive_wake_routes_once_to_source_environment_action_boundary() -> None:
    router = _RecordingRouter()
    adapter = _RecordingEnvironment()
    runtime = LilavelRuntime(
        wake_policy=DirectMessageWakePolicy(),
        event_router=router,
    )
    runtime.register_environment(adapter)

    await runtime.start()
    while not adapter.actions:
        await asyncio.sleep(0)
    await runtime.stop()

    assert [event.event_id for event in router.events] == ["event-1"]
    assert [call.tool_name for call in adapter.actions] == ["conversation.presentation.complete"]
    assert adapter.actions[0].model_trust == "untrusted"
    assert adapter.stopped.is_set()
    assert router.closed is True


@dataclass(slots=True)
class _CloseFailingRouter:
    async def route(self, event: WorldEvent, execute: ActionExecutor) -> None:
        del event, execute

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
