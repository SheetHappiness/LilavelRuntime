"""RUNTIME-H1 bounded replay and long-run application proofs."""

from __future__ import annotations

import asyncio
import pickle
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from lilavel_contracts import ToolCall, ToolEffect, ToolResult, ToolResultStatus, ToolSpec
from lilavel_core import ToolBatchCorrelation

from lilavel_runtime import (
    ActionProposal,
    ActionProposalKind,
    ApplicationPermit,
    ApplicationToolRegistry,
    CognitionCandidate,
    CognitionEpisode,
    CognitionEpisodeRunner,
    CognitionOutcome,
    CognitionTrigger,
    DeterministicToolSessionFactory,
    EventSource,
    MindState,
    MindStateProvenance,
    Observation,
    ObservationWindow,
    ProposalApplicationCoordinator,
    ProposalApplicationResult,
    ProposalApplicationStatus,
    StateProposal,
    StateProposalKind,
    TemporalCoordinator,
    TemporalProposal,
    ToolBinding,
    WorldEvent,
)


class _FixtureEngine:
    def __init__(self, candidate: CognitionCandidate) -> None:
        self.candidate = candidate

    async def run(self, episode: CognitionEpisode) -> object:
        del episode
        return self.candidate


def _observation() -> Observation:
    return Observation(
        "observation-h1",
        1,
        WorldEvent(
            "event-h1",
            EventSource("fixture", "opaque-subject"),
            "direct_message",
            {"text": "fixture"},
        ),
    )


def _action_boundary(
    state: MindState,
    executor: Callable[[ToolBatchCorrelation, ToolCall, threading.Event], ToolResult],
    *,
    fence_capacity: int = 4,
    temporal: TemporalCoordinator | None = None,
    state_provenance: MindStateProvenance | None = None,
    executor_deadline: float = 0.05,
) -> ProposalApplicationCoordinator:
    spec = ToolSpec(
        "fixture.emit",
        "Emit one bounded fixture action.",
        {
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 128}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    registry = ApplicationToolRegistry([ToolBinding(spec, executor)])
    factory = DeterministicToolSessionFactory(
        registry,
        exposed_tool_names=(spec.name,),
        executor_deadline=executor_deadline,
        containment_deadline=0.2,
    )
    return ProposalApplicationCoordinator(
        state,
        scope_id="fixture-scope",
        state_provenance=state_provenance,
        tool_registry=registry,
        tool_session_factory=factory,
        action_tool_names={ActionProposalKind.SPEAK: spec.name},
        temporal_coordinator=temporal,
        fence_capacity=fence_capacity,
    )


def _runner(
    boundary: ProposalApplicationCoordinator,
    candidate: CognitionCandidate,
    state: MindState,
) -> CognitionEpisodeRunner:
    window = ObservationWindow(4)
    window.admit(_observation())
    return CognitionEpisodeRunner(
        _FixtureEngine(candidate),
        window,
        state,
        scope_id="fixture-scope",
        application_authority=boundary.application_authority,
    )


async def _outcome(runner: CognitionEpisodeRunner, number: int) -> CognitionOutcome:
    outcome = await runner.run(CognitionTrigger(("observation-h1",), f"h1-{number}"))
    assert outcome is not None
    assert outcome.application_permit is not None
    return outcome


def _confirmed_executor(
    calls: list[str],
) -> Callable[[ToolBatchCorrelation, ToolCall, threading.Event], ToolResult]:
    def execute(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        calls.append(call.call_id)
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.CONFIRMED)

    return execute


async def _wait_for_event(event: threading.Event) -> None:
    while not event.is_set():
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_h1_1000_sequential_applications_remain_live_and_bounded() -> None:
    calls: list[str] = []
    state = MindState()
    boundary = _action_boundary(state, _confirmed_executor(calls), fence_capacity=4)
    runner = _runner(
        boundary,
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "work"),)),
        state,
    )

    results: list[ProposalApplicationResult] = []
    for number in range(1_001):
        results.append(boundary.apply_sync(await _outcome(runner, number)))

    assert len(results) == 1_001
    assert all(result.status is ProposalApplicationStatus.APPLIED for result in results)
    assert len(calls) == 1_001
    assert len({result.application_id for result in results}) == 1_001
    assert boundary.retained_replay_count <= 4
    assert boundary.application_count <= 8
    assert boundary.retired_application_sequence > 0
    assert all(result.reason_code != "application_fence_full" for result in results)


@pytest.mark.asyncio
async def test_h1_recent_duplicate_is_fenced_before_external_effect() -> None:
    calls: list[str] = []
    state = MindState()
    boundary = _action_boundary(state, _confirmed_executor(calls))
    runner = _runner(
        boundary,
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "once"),)),
        state,
    )
    outcome = await _outcome(runner, 1)

    first = boundary.apply_sync(outcome)
    second = boundary.apply_sync(outcome)

    assert first.status is ProposalApplicationStatus.APPLIED
    assert second.status is ProposalApplicationStatus.DUPLICATE
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_h1_duplicate_after_rotation_is_retired_and_new_work_applies() -> None:
    calls: list[str] = []
    state = MindState()
    boundary = _action_boundary(state, _confirmed_executor(calls), fence_capacity=2)
    runner = _runner(
        boundary,
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "work"),)),
        state,
    )
    first = await _outcome(runner, 1)
    assert boundary.apply_sync(first).status is ProposalApplicationStatus.APPLIED
    second = await _outcome(runner, 2)
    assert boundary.apply_sync(second).status is ProposalApplicationStatus.APPLIED
    old_epoch = first.application_permit.epoch_id  # type: ignore[union-attr]

    third = await _outcome(runner, 3)
    assert third.application_permit is not None
    assert third.application_permit.epoch_id != old_epoch
    assert boundary.apply_sync(third).status is ProposalApplicationStatus.APPLIED

    replay = boundary.apply_sync(first)
    assert replay.status is ProposalApplicationStatus.REJECTED
    assert replay.reason_code == "application_permit_retired"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_h1_concurrent_duplicate_crosses_effect_boundary_once() -> None:
    calls: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def execute(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        calls.append(call.call_id)
        started.set()
        release.wait(1.0)
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.CONFIRMED)

    state = MindState()
    boundary = _action_boundary(state, execute)
    runner = _runner(
        boundary,
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "once"),)),
        state,
    )
    outcome = await _outcome(runner, 1)

    first_task = asyncio.create_task(boundary.apply(outcome))
    await _wait_for_event(started)
    second_task = asyncio.create_task(boundary.apply(outcome))
    release.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert sorted((first.status, second.status), key=lambda item: item.value) == sorted(
        (ProposalApplicationStatus.APPLIED, ProposalApplicationStatus.DUPLICATE),
        key=lambda item: item.value,
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_h1_confirmed_and_unknown_effects_are_never_retried() -> None:
    confirmed_calls: list[str] = []
    confirmed_state = MindState()
    confirmed_boundary = _action_boundary(confirmed_state, _confirmed_executor(confirmed_calls))
    confirmed_runner = _runner(
        confirmed_boundary,
        CognitionCandidate(
            action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "confirmed"),)
        ),
        confirmed_state,
    )
    confirmed = await _outcome(confirmed_runner, 1)
    assert (
        confirmed_boundary.apply_sync(confirmed).actions.proposals[0].effect is ToolEffect.CONFIRMED
    )
    assert confirmed_boundary.apply_sync(confirmed).status is ProposalApplicationStatus.DUPLICATE

    unknown_calls: list[str] = []

    def unknown_executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        unknown_calls.append(call.call_id)
        return ToolResult(
            call.call_id,
            ToolResultStatus.TIMED_OUT,
            None,
            reason_code="executor_timeout",
            effect=ToolEffect.UNKNOWN,
        )

    unknown_state = MindState()
    unknown_boundary = _action_boundary(unknown_state, unknown_executor)
    unknown_runner = _runner(
        unknown_boundary,
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "unknown"),)),
        unknown_state,
    )
    unknown = await _outcome(unknown_runner, 1)
    first = unknown_boundary.apply_sync(unknown)
    replay = unknown_boundary.apply_sync(unknown)

    assert first.actions.proposals[0].effect is ToolEffect.UNKNOWN
    assert replay.status is ProposalApplicationStatus.DUPLICATE
    assert len(confirmed_calls) == 1
    assert len(unknown_calls) == 1


@pytest.mark.asyncio
async def test_h1_state_temporal_and_mixed_retired_replays_have_zero_effects() -> None:
    calls: list[str] = []
    state = MindState()
    temporal = TemporalCoordinator(scope_id="fixture-scope")
    boundary = _action_boundary(
        state,
        _confirmed_executor(calls),
        fence_capacity=2,
        temporal=temporal,
        state_provenance=MindStateProvenance("user", "assistant"),
    )
    state_runner = _runner(
        boundary,
        CognitionCandidate(
            state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, "state"),)
        ),
        state,
    )
    state_outcome = await _outcome(state_runner, 1)
    assert boundary.apply_sync(state_outcome).status is ProposalApplicationStatus.APPLIED
    state_version = state.version

    quiet_runner = _runner(boundary, CognitionCandidate(), state)
    assert (
        boundary.apply_sync(await _outcome(quiet_runner, 2)).status
        is ProposalApplicationStatus.NO_PROPOSALS
    )
    assert (
        boundary.apply_sync(await _outcome(quiet_runner, 3)).status
        is ProposalApplicationStatus.NO_PROPOSALS
    )
    assert boundary.apply_sync(state_outcome).status is ProposalApplicationStatus.REJECTED
    assert state.version == state_version

    temporal_state = MindState()
    temporal_coordinator = TemporalCoordinator(scope_id="fixture-scope")
    temporal_boundary = _action_boundary(
        temporal_state,
        _confirmed_executor([]),
        fence_capacity=2,
        temporal=temporal_coordinator,
    )
    temporal_runner = _runner(
        temporal_boundary,
        CognitionCandidate(
            temporal_proposals=(
                TemporalProposal(
                    "reconsider",
                    datetime.now(UTC) + timedelta(seconds=10),
                    "intention",
                ),
            )
        ),
        temporal_state,
    )
    temporal_outcome = await _outcome(temporal_runner, 10)
    assert (
        temporal_boundary.apply_sync(temporal_outcome).status is ProposalApplicationStatus.APPLIED
    )
    pending = temporal_coordinator.pending_count
    temporal_quiet = _runner(temporal_boundary, CognitionCandidate(), temporal_state)
    assert (
        temporal_boundary.apply_sync(await _outcome(temporal_quiet, 11)).status
        is ProposalApplicationStatus.NO_PROPOSALS
    )
    assert (
        temporal_boundary.apply_sync(await _outcome(temporal_quiet, 12)).status
        is ProposalApplicationStatus.NO_PROPOSALS
    )
    assert (
        temporal_boundary.apply_sync(temporal_outcome).status is ProposalApplicationStatus.REJECTED
    )
    assert temporal_coordinator.pending_count == pending


@pytest.mark.asyncio
async def test_h1_mixed_retired_replay_cannot_repeat_any_effect_boundary() -> None:
    calls: list[str] = []
    state = MindState()
    temporal = TemporalCoordinator(scope_id="fixture-scope")
    boundary = _action_boundary(
        state,
        _confirmed_executor(calls),
        fence_capacity=1,
        temporal=temporal,
        state_provenance=MindStateProvenance("user", "assistant"),
    )
    runner = _runner(
        boundary,
        CognitionCandidate(
            state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, "mixed"),),
            action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "mixed"),),
            temporal_proposals=(
                TemporalProposal(
                    "mixed wake",
                    datetime.now(UTC) + timedelta(seconds=10),
                    "mixed-intention",
                ),
            ),
        ),
        state,
    )
    outcome = await _outcome(runner, 1)
    assert boundary.apply_sync(outcome).status is ProposalApplicationStatus.APPLIED
    version = state.version
    pending = temporal.pending_count
    assert len(calls) == 1

    quiet = _runner(boundary, CognitionCandidate(), state)
    assert (
        boundary.apply_sync(await _outcome(quiet, 2)).status
        is ProposalApplicationStatus.NO_PROPOSALS
    )
    assert boundary.apply_sync(outcome).status is ProposalApplicationStatus.REJECTED
    assert state.version == version
    assert temporal.pending_count == pending
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_h1_application_cancellation_settles_before_propagation() -> None:
    calls: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def execute(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        calls.append(call.call_id)
        started.set()
        release.wait(1.0)
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.CONFIRMED)

    state = MindState()
    boundary = _action_boundary(state, execute)
    runner = _runner(
        boundary,
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "cancel"),)),
        state,
    )
    outcome = await _outcome(runner, 1)
    task = asyncio.create_task(boundary.apply(outcome))
    await _wait_for_event(started)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert boundary.retained_replay_count == 1
    assert boundary.applications()[0].actions.proposals[0].effect is ToolEffect.CONFIRMED
    assert boundary.apply_sync(outcome).status is ProposalApplicationStatus.DUPLICATE
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_h1_tampering_invalidates_permit_before_any_effect() -> None:
    calls: list[str] = []
    state = MindState()
    boundary = _action_boundary(state, _confirmed_executor(calls))
    runner = _runner(
        boundary,
        CognitionCandidate(
            action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "original"),)
        ),
        state,
    )
    outcome = await _outcome(runner, 1)
    tampered = replace(
        outcome,
        action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "tampered"),),
    )

    result = boundary.apply_sync(tampered)

    assert result.status is ProposalApplicationStatus.REJECTED
    assert result.reason_code == "application_permit_mismatch"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["state", "temporal"])
async def test_h1_state_and_temporal_tampering_invalidates_permit(kind: str) -> None:
    state = MindState()
    temporal = TemporalCoordinator(scope_id="fixture-scope")
    boundary = ProposalApplicationCoordinator(
        state,
        scope_id="fixture-scope",
        temporal_coordinator=temporal,
        state_provenance=MindStateProvenance("user", "assistant"),
        fence_capacity=4,
    )
    if kind == "state":
        candidate = CognitionCandidate(
            state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, "original"),)
        )
    else:
        candidate = CognitionCandidate(
            temporal_proposals=(
                TemporalProposal(
                    "original",
                    datetime.now(UTC) + timedelta(seconds=10),
                    "intention",
                ),
            )
        )
    runner = _runner(boundary, candidate, state)
    outcome = await _outcome(runner, 1)
    if kind == "state":
        tampered = replace(
            outcome,
            state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, "tampered"),),
        )
    else:
        original = outcome.temporal_proposals[0]
        tampered = replace(
            outcome,
            temporal_proposals=(
                TemporalProposal(
                    "tampered",
                    original.not_before + timedelta(seconds=1),
                    "tampered-intention",
                ),
            ),
        )

    result = boundary.apply_sync(tampered)

    assert result.status is ProposalApplicationStatus.REJECTED
    assert result.reason_code == "application_permit_mismatch"
    assert state.version == 0
    assert temporal.pending_count == 0


@pytest.mark.asyncio
async def test_h1_forged_visible_permit_is_rejected_before_effects() -> None:
    calls: list[str] = []
    state = MindState()
    boundary = _action_boundary(state, _confirmed_executor(calls))
    runner = _runner(
        boundary,
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "forged"),)),
        state,
    )
    outcome = await _outcome(runner, 1)
    genuine = outcome.application_permit
    assert genuine is not None
    forged = ApplicationPermit(
        genuine.epoch_id,
        genuine.sequence,
        genuine.scope_id,
        genuine.episode_id,
        genuine.trigger_id,
        genuine.proposal_digest,
        object(),
    )

    result = boundary.apply_sync(replace(outcome, application_permit=forged))

    assert result.status is ProposalApplicationStatus.REJECTED
    assert result.reason_code == "application_permit_invalid"
    assert calls == []
    with pytest.raises(TypeError):
        pickle.dumps(genuine)


@pytest.mark.asyncio
async def test_h1_unused_permit_is_retired_without_outstanding_set() -> None:
    calls: list[str] = []
    state = MindState()
    boundary = _action_boundary(state, _confirmed_executor(calls), fence_capacity=2)
    runner = _runner(
        boundary,
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "work"),)),
        state,
    )
    unused = await _outcome(runner, 1)
    first = await _outcome(runner, 2)
    second = await _outcome(runner, 3)
    assert boundary.apply_sync(first).status is ProposalApplicationStatus.APPLIED
    assert boundary.apply_sync(second).status is ProposalApplicationStatus.APPLIED
    rotating = await _outcome(runner, 4)
    assert boundary.apply_sync(rotating).status is ProposalApplicationStatus.APPLIED

    retired = boundary.apply_sync(unused)

    assert retired.status is ProposalApplicationStatus.REJECTED
    assert retired.reason_code == "application_permit_retired"
    assert len(calls) == 3
    assert boundary.retained_replay_count <= 2


@pytest.mark.asyncio
async def test_h1_rotation_waits_for_application_settlement() -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def execute(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        calls.append(call.call_id)
        started.set()
        release.wait(1.0)
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.CONFIRMED)

    state = MindState()
    boundary = _action_boundary(state, execute, fence_capacity=1, executor_deadline=2.0)
    runner = _runner(
        boundary,
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "work"),)),
        state,
    )
    first = await _outcome(runner, 1)
    first_task = asyncio.create_task(boundary.apply(first))
    await _wait_for_event(started)
    old_epoch = boundary.replay_epoch_id
    assert boundary.replay_epoch_id == old_epoch
    release.set()
    assert (await first_task).status is ProposalApplicationStatus.APPLIED
    second = await _outcome(runner, 2)
    assert second.application_permit is not None
    assert second.application_permit.epoch_id != old_epoch
    assert boundary.apply_sync(second).status is ProposalApplicationStatus.APPLIED
    assert len(calls) == 2
