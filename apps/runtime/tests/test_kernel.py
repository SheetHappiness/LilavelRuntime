"""Deterministic lifecycle and bounded-ingress proofs for LilavelRuntime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from lilavel_runtime import (
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
    ToolSpec,
    WakeDecision,
    WorldEvent,
)


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
