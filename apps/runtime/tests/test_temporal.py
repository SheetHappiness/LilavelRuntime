"""Deterministic MIND-1E proofs A–J."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from lilavel_runtime import (
    CognitionCandidate,
    CognitionEpisode,
    CognitionEpisodeRunner,
    CognitionOutcome,
    CognitionTrigger,
    CognitionTriggerSource,
    EventSource,
    MindState,
    Observation,
    ObservationWindow,
    ProposalApplicationCoordinator,
    ProposalApplicationStatus,
    TemporalApplicationStatus,
    TemporalCoordinator,
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


class _FixtureEngine:
    def __init__(
        self,
        candidate: CognitionCandidate,
        *,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
        fail: bool = False,
    ) -> None:
        self.candidate = candidate
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
            return self.candidate
        finally:
            self.active -= 1


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


def _temporal_proposal(clock: _FakeClock, *, seconds: float = 10) -> TemporalProposal:
    return TemporalProposal(
        "reconsider fixture intention",
        clock.now() + timedelta(seconds=seconds),
        intention_ref="intention-fixture",
    )


async def _run_outcome(
    proposal: TemporalProposal,
) -> tuple[CognitionOutcome, MindState]:
    state = MindState()
    window = ObservationWindow(4)
    window.admit(_observation())
    runner = CognitionEpisodeRunner(
        _FixtureEngine(CognitionCandidate(temporal_proposals=(proposal,))),
        window,
        state,
        scope_id="fixture-scope",
    )
    outcome = await runner.run(CognitionTrigger(("observation-1",), "fixture"))
    assert outcome is not None
    return outcome, state


@pytest.mark.asyncio
async def test_a_future_wake_is_inert_until_trusted_application() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    proposal = _temporal_proposal(clock)
    outcome, state = await _run_outcome(proposal)
    coordinator = TemporalCoordinator(scope_id="fixture-scope", clock=clock.now)
    assert coordinator.pending_intents() == ()

    boundary = ProposalApplicationCoordinator(
        state,
        scope_id="fixture-scope",
        temporal_coordinator=coordinator,
    )
    outcome = boundary.application_authority.bind_outcome(outcome)
    result = boundary.apply_sync(outcome)

    assert result.status is ProposalApplicationStatus.APPLIED
    assert result.temporal.status is TemporalApplicationStatus.APPLIED
    pending = coordinator.pending_intents()
    assert len(pending) == 1
    assert pending[0].status is WakeIntentStatus.PENDING
    assert pending[0].not_before == clock.now() + timedelta(seconds=10)


def test_b_invalid_or_out_of_policy_temporal_input_is_deterministic() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    coordinator = TemporalCoordinator(scope_id="fixture-scope", clock=clock.now)

    invalid = coordinator.prepare(
        cast(tuple[TemporalProposal, ...], (object(),)),
        source_episode_id="episode-1",
        source_trigger_id="trigger-1",  # type: ignore[tuple-item]
    )
    assert not invalid.valid
    assert invalid.reason_code == "unsupported_temporal_proposal"
    assert coordinator.pending_count == 0

    past = TemporalProposal("past request", clock.now() - timedelta(days=1))
    result = coordinator.apply(
        (past,), source_episode_id="episode-2", source_trigger_id="trigger-2"
    )
    assert result.status is TemporalApplicationStatus.APPLIED
    assert coordinator.pending_intents()[0].not_before == clock.now() + timedelta(seconds=1)

    far_future = TemporalProposal("far future request", clock.now() + timedelta(days=30))
    far_result = coordinator.apply(
        (far_future,), source_episode_id="episode-3", source_trigger_id="trigger-3"
    )
    assert far_result.status is TemporalApplicationStatus.APPLIED
    far_id = far_result.proposals[0].wake_intent_id
    assert far_id is not None
    far_intent = coordinator.intent(far_id)
    assert far_intent is not None
    assert far_intent.not_before == clock.now() + timedelta(days=7)


@pytest.mark.asyncio
async def test_c_due_wake_emits_cognition_only() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    proposal = _temporal_proposal(clock, seconds=2)
    outcome, state = await _run_outcome(proposal)
    temporal = TemporalCoordinator(scope_id="fixture-scope", clock=clock.now)
    boundary = ProposalApplicationCoordinator(
        state, scope_id="fixture-scope", temporal_coordinator=temporal
    )
    outcome = boundary.application_authority.bind_outcome(outcome)
    boundary.apply_sync(outcome)
    clock.advance(3)

    triggers = temporal.poll_due()

    assert len(triggers) == 1
    trigger = triggers[0]
    assert trigger.source is CognitionTriggerSource.TEMPORAL
    assert trigger.observation_ids == ()
    assert trigger.wake_intent_id is not None
    assert temporal.intent(trigger.wake_intent_id).status is WakeIntentStatus.DISPATCHED  # type: ignore[union-attr]

    engine = _FixtureEngine(CognitionCandidate())
    runner = CognitionEpisodeRunner(engine, ObservationWindow(4), state, scope_id="fixture-scope")
    assert await runner.run(trigger) is not None
    assert engine.invocations == 1


def test_d_duplicate_due_poll_is_fenced() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    coordinator = TemporalCoordinator(scope_id="fixture-scope", clock=clock.now)
    result = coordinator.apply(
        (
            _temporal_proposal(clock, seconds=1),
            TemporalProposal(
                "later fixture wake",
                clock.now() + timedelta(seconds=2),
                intention_ref="intention-later",
            ),
        ),
        source_episode_id="episode-1",
        source_trigger_id="trigger-1",
    )
    assert result.status is TemporalApplicationStatus.APPLIED
    clock.advance(2)

    first = coordinator.poll_due()
    second = coordinator.poll_due()

    assert len(first) == 2
    assert first[0].source_refs[0] == result.proposals[0].wake_intent_id
    assert first[1].source_refs[0] == result.proposals[1].wake_intent_id
    assert second == ()


@pytest.mark.asyncio
async def test_e_due_temporal_episode_waits_for_the_existing_serial_lane() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    temporal = TemporalCoordinator(scope_id="fixture-scope", clock=clock.now)
    result = temporal.apply(
        (_temporal_proposal(clock, seconds=1),),
        source_episode_id="episode-source",
        source_trigger_id="trigger-source",
    )
    assert result.proposals[0].wake_intent_id is not None
    clock.advance(2)
    temporal_trigger = temporal.poll_due()[0]

    started = asyncio.Event()
    release = asyncio.Event()
    engine = _FixtureEngine(CognitionCandidate(), started=started, release=release)
    window = ObservationWindow(4)
    window.admit(_observation())
    runner = CognitionEpisodeRunner(engine, window, MindState(), scope_id="fixture-scope")
    first = asyncio.create_task(runner.run(CognitionTrigger(("observation-1",), "external")))
    await started.wait()
    second = asyncio.create_task(runner.run(temporal_trigger))
    await asyncio.sleep(0)

    assert engine.invocations == 1
    assert not second.done()
    release.set()
    await first
    await second
    assert engine.invocations == 2
    assert engine.max_active == 1


def test_f_pending_wake_can_only_be_cancelled_through_the_runtime_seam() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    coordinator = TemporalCoordinator(scope_id="fixture-scope", clock=clock.now)
    result = coordinator.apply(
        (_temporal_proposal(clock, seconds=10),),
        source_episode_id="episode-1",
        source_trigger_id="trigger-1",
    )
    wake_id = result.proposals[0].wake_intent_id
    assert wake_id is not None

    cancelled = coordinator.cancel(wake_id)
    assert cancelled is not None and cancelled.status is WakeIntentStatus.CANCELLED
    clock.advance(20)
    assert coordinator.poll_due() == ()


def test_g_equivalent_pending_proposals_deduplicate_without_rescheduling() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    coordinator = TemporalCoordinator(scope_id="fixture-scope", clock=clock.now)
    first = coordinator.apply(
        (_temporal_proposal(clock, seconds=10),),
        source_episode_id="episode-1",
        source_trigger_id="trigger-1",
    )
    first_id = first.proposals[0].wake_intent_id
    assert first_id is not None
    clock.advance(1)
    second = coordinator.apply(
        (_temporal_proposal(clock, seconds=100),),
        source_episode_id="episode-2",
        source_trigger_id="trigger-2",
    )

    assert second.status is TemporalApplicationStatus.DUPLICATE
    assert second.proposals[0].wake_intent_id == first_id
    assert len(coordinator.pending_intents()) == 1
    assert coordinator.pending_intents()[0].not_before == datetime(2030, 1, 1, 0, 0, 10, tzinfo=UTC)


@pytest.mark.asyncio
async def test_h_dispatched_wake_is_not_resurrected_after_cognition_failure() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    coordinator = TemporalCoordinator(scope_id="fixture-scope", clock=clock.now)
    result = coordinator.apply(
        (_temporal_proposal(clock, seconds=1),),
        source_episode_id="episode-1",
        source_trigger_id="trigger-1",
    )
    clock.advance(2)
    trigger = coordinator.poll_due()[0]
    runner = CognitionEpisodeRunner(
        _FixtureEngine(CognitionCandidate(), fail=True),
        ObservationWindow(4),
        MindState(),
        scope_id="fixture-scope",
    )

    assert await runner.run(trigger) is None
    assert coordinator.poll_due() == ()
    wake_id = result.proposals[0].wake_intent_id
    assert wake_id is not None
    intent = coordinator.intent(wake_id)
    assert intent is not None
    assert intent.status is WakeIntentStatus.DISPATCHED


@pytest.mark.asyncio
async def test_i_temporal_trigger_preserves_only_trusted_provenance_refs() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    coordinator = TemporalCoordinator(scope_id="fixture-scope", clock=clock.now)
    result = coordinator.apply(
        (_temporal_proposal(clock),),
        source_episode_id="episode-source",
        source_trigger_id="trigger-source",
    )
    clock.advance(20)
    trigger = coordinator.poll_due()[0]
    assert trigger.source_refs[:3] == (
        result.proposals[0].wake_intent_id,
        "episode-source",
        "trigger-source",
    )
    assert trigger.observation_ids == ()
    assert trigger.reason == "reconsider fixture intention"
    assert not hasattr(trigger, "trusted_guidance")


@pytest.mark.asyncio
async def test_j_existing_cognition_outcome_remains_inert_before_application() -> None:
    clock = _FakeClock(datetime(2030, 1, 1, tzinfo=UTC))
    proposal = _temporal_proposal(clock)
    state = MindState()
    window = ObservationWindow(4)
    window.admit(_observation())
    runner = CognitionEpisodeRunner(
        _FixtureEngine(CognitionCandidate(temporal_proposals=(proposal,))),
        window,
        state,
        scope_id="fixture-scope",
    )

    outcome = await runner.run(CognitionTrigger(("observation-1",), "fixture"))

    assert outcome is not None
    assert outcome.temporal_proposals == (proposal,)
    assert state.version == 0
