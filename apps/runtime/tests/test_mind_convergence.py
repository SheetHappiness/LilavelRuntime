"""Deterministic MIND-1F-C convergence proofs."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from lilavel_runtime import (
    ActionProposal,
    ActionProposalKind,
    CognitionCandidate,
    CognitionEpisode,
    CognitionEpisodeRunner,
    CognitionOutcome,
    CognitionTrigger,
    EventSource,
    LilavelRuntime,
    MindExecutionAdapter,
    MindExecutionResult,
    MindExecutionStatus,
    MindState,
    Observation,
    ObservationWindow,
    ProposalApplicationCoordinator,
    ProposalApplicationResult,
    ProposalApplicationStatus,
    SemanticActor,
    SemanticCancellationToken,
    SemanticEpisode,
    SemanticEpisodeStatus,
    SemanticPriority,
    SemanticSourceKind,
    TemporalCoordinator,
    TemporalDispatchStatus,
    TemporalHostState,
    TemporalProposal,
    WakeIntentStatus,
    WorldEvent,
)


@dataclass(slots=True)
class _FakeClock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class _MindEngine:
    def __init__(
        self,
        candidate_factory: Callable[[int], CognitionCandidate],
        *,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
        fail: bool = False,
    ) -> None:
        self._candidate_factory = candidate_factory
        self.started = started
        self.release = release
        self.fail = fail
        self.invocations = 0
        self.active = 0
        self.max_active = 0

    async def run(self, episode: CognitionEpisode) -> object:
        del episode
        self.invocations += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.started is not None:
            self.started.set()
        try:
            if self.release is not None:
                await self.release.wait()
            if self.fail:
                raise RuntimeError("fixture cognition failure")
            return self._candidate_factory(self.invocations)
        finally:
            self.active -= 1


class _BlockingApplicationCoordinator(ProposalApplicationCoordinator):
    def __init__(
        self,
        mind_state: MindState,
        *,
        started: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__(mind_state, scope_id="runtime")
        self.started = started
        self.release = release

    def apply_sync(self, outcome: CognitionOutcome) -> ProposalApplicationResult:
        self.started.set()
        self.release.wait(1.0)
        return super().apply_sync(outcome)


async def _wait_until(predicate: Callable[[], bool]) -> None:
    for _ in range(300):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("fixture condition did not become true")


def _observation() -> Observation:
    return Observation(
        "observation-1",
        1,
        WorldEvent(
            "event-1",
            EventSource("fixture", "opaque-subject"),
            "direct_message",
            {"text": "fixture"},
        ),
    )


def _wake(clock: _FakeClock, reason: str, seconds: float = 10) -> TemporalProposal:
    return TemporalProposal(
        reason,
        clock.now() + timedelta(seconds=seconds),
        intention_ref=f"intention-{reason}",
    )


def _composition(
    engine: _MindEngine,
    clock: _FakeClock,
    *,
    with_temporal_host: bool = True,
    shutdown_timeout: float = 1.0,
) -> tuple[
    LilavelRuntime,
    MindState,
    TemporalCoordinator,
    ProposalApplicationCoordinator,
]:
    state = MindState()
    temporal = TemporalCoordinator(scope_id="runtime", clock=clock.now)
    application = ProposalApplicationCoordinator(
        state,
        scope_id="runtime",
        temporal_coordinator=temporal if with_temporal_host else None,
    )
    runtime = LilavelRuntime(
        cognition_engine=engine,
        mind_state=state,
        proposal_application_coordinator=application,
        temporal_coordinator=temporal if with_temporal_host else None,
        shutdown_timeout=shutdown_timeout,
    )
    return runtime, state, temporal, application


@pytest.mark.asyncio
async def test_a_b_generic_trigger_uses_actor_and_fences_duplicate_application() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    engine = _MindEngine(lambda _: CognitionCandidate())
    runtime, state, _, application = _composition(engine, clock, with_temporal_host=False)
    await runtime.start()
    receipt = await runtime.admit_observation(
        WorldEvent(
            "event-1",
            EventSource("fixture", "opaque-subject"),
            "direct_message",
            {"text": "fixture"},
        )
    )
    trigger = CognitionTrigger((receipt.observation_id,), "generic_fixture")

    first = await runtime.submit_cognition(trigger)
    second = await runtime.submit_cognition(trigger)
    first_settlement = await first.wait()
    await second.wait()

    assert first_settlement.status is SemanticEpisodeStatus.COMPLETED
    assert second.status.value == "duplicate"
    assert isinstance(first_settlement.result, MindExecutionResult)
    assert first_settlement.result.status is MindExecutionStatus.COMPLETED_QUIET
    assert engine.invocations == 1
    assert application.application_count == 1
    assert state.version == 0
    await runtime.stop()


@pytest.mark.asyncio
async def test_c_temporal_trigger_reaches_same_actor_executor_and_application() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))

    def candidate(number: int) -> CognitionCandidate:
        return (
            CognitionCandidate(temporal_proposals=(_wake(clock, "first-wake"),))
            if number == 1
            else CognitionCandidate()
        )

    engine = _MindEngine(candidate)
    runtime, _, temporal, application = _composition(engine, clock)
    await runtime.start()
    receipt = await runtime.admit_observation(_observation().event)
    external = CognitionTrigger((receipt.observation_id,), "external_fixture")
    first = await runtime.submit_cognition(external)
    first_settlement = await first.wait()
    assert first_settlement.status is SemanticEpisodeStatus.COMPLETED
    wake_id = temporal.pending_intents()[0].wake_intent_id

    clock.advance(11)
    assert runtime.temporal_host is not None
    runtime.temporal_host.wake()
    await _wait_until(lambda: engine.invocations == 2)
    await _wait_until(lambda: len(application.applications()) == 2)

    assert temporal.intent(wake_id).status is WakeIntentStatus.DISPATCHED  # type: ignore[union-attr]
    assert runtime.temporal_host.evidence()[0].status is TemporalDispatchStatus.ADMITTED
    assert runtime.semantic_actor.max_active == 1
    await runtime.stop()
    assert runtime.temporal_host.state is TemporalHostState.STOPPED


@pytest.mark.asyncio
async def test_d_earlier_wake_interrupts_deadline_wait_and_dispatches_first() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    engine = _MindEngine(lambda _: CognitionCandidate())
    runtime, _, temporal, _ = _composition(engine, clock)
    await runtime.start()
    later = temporal.apply(
        (_wake(clock, "later", seconds=20),),
        source_episode_id="episode-later",
        source_trigger_id="trigger-later",
    )
    earlier = temporal.apply(
        (_wake(clock, "earlier", seconds=2),),
        source_episode_id="episode-earlier",
        source_trigger_id="trigger-earlier",
    )
    later_id = later.proposals[0].wake_intent_id
    earlier_id = earlier.proposals[0].wake_intent_id
    assert later_id is not None and earlier_id is not None
    await asyncio.sleep(0)

    clock.advance(3)
    host = runtime.temporal_host
    assert host is not None
    host.wake()
    await _wait_until(lambda: len(host.evidence()) == 1)

    assert host.evidence()[0].wake_intent_id == earlier_id
    assert temporal.intent(later_id).status is WakeIntentStatus.PENDING  # type: ignore[union-attr]
    await runtime.stop()


@pytest.mark.asyncio
async def test_e_cancelled_pending_wake_is_not_submitted() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    engine = _MindEngine(lambda _: CognitionCandidate())
    runtime, _, temporal, _ = _composition(engine, clock)
    await runtime.start()
    result = temporal.apply(
        (_wake(clock, "cancel-me", seconds=10),),
        source_episode_id="episode-cancel",
        source_trigger_id="trigger-cancel",
    )
    wake_id = result.proposals[0].wake_intent_id
    assert wake_id is not None
    assert temporal.cancel(wake_id) is not None
    clock.advance(20)
    assert runtime.temporal_host is not None
    runtime.temporal_host.wake()
    await asyncio.sleep(0)

    assert runtime.temporal_host.evidence() == ()
    assert engine.invocations == 0
    await runtime.stop()


@pytest.mark.asyncio
async def test_f_temporal_mind_queues_behind_active_external_mind() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    started = asyncio.Event()
    release = asyncio.Event()
    engine = _MindEngine(lambda _: CognitionCandidate(), started=started, release=release)
    runtime, _, temporal, _ = _composition(engine, clock)
    await runtime.start()
    receipt = await runtime.admit_observation(_observation().event)
    first = await runtime.submit_cognition(
        CognitionTrigger((receipt.observation_id,), "active_external")
    )
    await started.wait()
    result = temporal.apply(
        (_wake(clock, "queued-temporal", seconds=1),),
        source_episode_id="episode-temporal",
        source_trigger_id="trigger-temporal",
    )
    assert result.proposals[0].wake_intent_id is not None
    clock.advance(2)
    assert runtime.temporal_host is not None
    runtime.temporal_host.wake()
    await _wait_until(lambda: runtime.semantic_actor.queued_count == 1)

    assert engine.invocations == 1
    assert runtime.semantic_actor.active_count == 1
    release.set()
    assert (await first.wait()).status is SemanticEpisodeStatus.COMPLETED
    await _wait_until(lambda: engine.invocations == 2)
    assert engine.max_active == 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_g_actor_cancellation_cancels_runner_before_successor_admission() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    engine = _MindEngine(lambda _: CognitionCandidate(), started=started, release=release)
    state = MindState()
    window = ObservationWindow(4)
    window.admit(_observation())
    runner = CognitionEpisodeRunner(engine, window, state, scope_id="runtime")
    application = ProposalApplicationCoordinator(state, scope_id="runtime")
    adapter = MindExecutionAdapter(runner, application)
    actor = SemanticActor(scope_id="runtime", settlement_timeout=0.2)
    trigger = CognitionTrigger(("observation-1",), "cancel_fixture")

    async def first_executor(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode
        return await adapter.execute(trigger, cancellation)

    async def successor_executor(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode, cancellation
        return "successor"

    await actor.start()
    active = await actor.admit(
        actor.create_request(
            trigger.trigger_id,
            source_kind=SemanticSourceKind.EXTERNAL,
            priority=SemanticPriority.NON_USER,
            executor=first_executor,
        )
    )
    await started.wait()
    successor = await actor.admit(
        actor.create_request(
            "user-successor",
            source_kind=SemanticSourceKind.USER,
            priority=SemanticPriority.USER,
            executor=successor_executor,
        )
    )

    assert (await active.wait()).status is SemanticEpisodeStatus.CANCELLED
    assert (await successor.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert runner.last_status is not None
    assert engine.active == 0
    assert application.application_count == 0
    release.set()
    await actor.stop()


@pytest.mark.asyncio
async def test_g_application_cancellation_waits_for_application_settlement() -> None:
    started = asyncio.Event()
    engine = _MindEngine(lambda _: CognitionCandidate(), started=started)
    state = MindState()
    window = ObservationWindow(4)
    window.admit(_observation())
    runner = CognitionEpisodeRunner(engine, window, state, scope_id="runtime")
    application_started = threading.Event()
    application_release = threading.Event()
    application = _BlockingApplicationCoordinator(
        state,
        started=application_started,
        release=application_release,
    )
    adapter = MindExecutionAdapter(runner, application)
    cancellation = SemanticCancellationToken()
    task = asyncio.create_task(
        adapter.execute(CognitionTrigger(("observation-1",), "application_cancel"), cancellation)
    )
    await started.wait()
    for _ in range(300):
        if application_started.is_set():
            break
        await asyncio.sleep(0)
    assert application_started.is_set()

    cancellation.request()
    await asyncio.sleep(0)
    assert task.done() is False
    application_release.set()
    result = await task

    assert result.status is MindExecutionStatus.CANCELLED
    assert application.application_count == 1


@pytest.mark.asyncio
async def test_h_quiet_cognition_settles_without_effects() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    engine = _MindEngine(lambda _: CognitionCandidate())
    runtime, state, _, application = _composition(engine, clock, with_temporal_host=False)
    await runtime.start()
    receipt = await runtime.admit_observation(_observation().event)
    admission = await runtime.submit_cognition(
        CognitionTrigger((receipt.observation_id,), "quiet_fixture")
    )
    settlement = await admission.wait()

    assert settlement.status is SemanticEpisodeStatus.COMPLETED
    assert isinstance(settlement.result, MindExecutionResult)
    assert settlement.result.status is MindExecutionStatus.COMPLETED_QUIET
    assert application.application_count == 1
    assert state.version == 0
    await runtime.stop()


@pytest.mark.asyncio
async def test_i_runner_failure_does_not_reach_application_and_lane_recovers() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    engine = _MindEngine(lambda _: CognitionCandidate(), fail=True)
    runtime, _, _, application = _composition(engine, clock, with_temporal_host=False)
    await runtime.start()
    receipt = await runtime.admit_observation(_observation().event)
    failed = await runtime.submit_cognition(
        CognitionTrigger((receipt.observation_id,), "failure_fixture")
    )
    settlement = await failed.wait()

    assert settlement.status is SemanticEpisodeStatus.FAILED
    assert application.application_count == 0
    await runtime.stop()


@pytest.mark.asyncio
async def test_j_application_rejection_is_terminal_without_retry() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    engine = _MindEngine(
        lambda _: CognitionCandidate(
            action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "rejected"),)
        )
    )
    runtime, _, _, application = _composition(engine, clock, with_temporal_host=False)
    await runtime.start()
    receipt = await runtime.admit_observation(_observation().event)
    admission = await runtime.submit_cognition(
        CognitionTrigger((receipt.observation_id,), "application_rejection")
    )
    settlement = await admission.wait()

    assert settlement.status is SemanticEpisodeStatus.COMPLETED
    assert isinstance(settlement.result, MindExecutionResult)
    assert settlement.result.status is MindExecutionStatus.APPLICATION_REJECTED
    assert settlement.result.application_status is ProposalApplicationStatus.REJECTED
    assert application.application_count == 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_k_shutdown_stops_temporal_host_and_settles_active_and_queued_mind() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    started = asyncio.Event()
    release = asyncio.Event()
    engine = _MindEngine(lambda _: CognitionCandidate(), started=started, release=release)
    runtime, _, temporal, _ = _composition(engine, clock, shutdown_timeout=1.0)
    await runtime.start()
    receipt = await runtime.admit_observation(_observation().event)
    active = await runtime.submit_cognition(
        CognitionTrigger((receipt.observation_id,), "shutdown-active")
    )
    await started.wait()
    queued = await runtime.submit_cognition(
        CognitionTrigger((receipt.observation_id,), "shutdown-queued")
    )
    pending = temporal.apply(
        (_wake(clock, "shutdown-pending", seconds=20),),
        source_episode_id="episode-shutdown",
        source_trigger_id="trigger-shutdown",
    )
    pending_id = pending.proposals[0].wake_intent_id
    assert pending_id is not None

    await runtime.stop()

    assert runtime.state.value == "stopped"
    assert runtime.temporal_host is not None
    assert runtime.temporal_host.state is TemporalHostState.STOPPED
    assert runtime.semantic_actor.state.value == "stopped"
    assert (await active.wait()).status is SemanticEpisodeStatus.CANCELLED
    assert (await queued.wait()).status is SemanticEpisodeStatus.CANCELLED
    assert temporal.intent(pending_id).status is WakeIntentStatus.PENDING  # type: ignore[union-attr]
    assert engine.active == 0
