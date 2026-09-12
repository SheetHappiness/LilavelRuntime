"""Deterministic MIND-1D proposal-application proofs A–J."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest
from lilavel_contracts import ToolCall, ToolEffect, ToolResult, ToolResultStatus, ToolSpec
from lilavel_core import ConversationCore, ModelRuntime, ToolBatchCorrelation

from lilavel_runtime import (
    ActionApplicationStatus,
    ActionProposal,
    ActionProposalKind,
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
    ProposalApplicationStatus,
    StateApplicationStatus,
    StateProposal,
    StateProposalKind,
    ToolAuthorization,
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
        "observation-1",
        1,
        WorldEvent(
            "event-1",
            EventSource("fixture", "opaque-subject"),
            "direct_message",
            {"text": "fixture message"},
        ),
    )


async def _run_outcome(
    candidate: CognitionCandidate,
    state: MindState | None = None,
) -> tuple[CognitionOutcome, MindState]:
    actual_state = state or MindState()
    window = ObservationWindow(4)
    window.admit(_observation())
    runner = CognitionEpisodeRunner(
        _FixtureEngine(candidate), window, actual_state, scope_id="fixture-scope"
    )
    outcome = await runner.run(CognitionTrigger(("observation-1",), "fixture"))
    assert outcome is not None
    return outcome, actual_state


type Executor = Callable[[ToolBatchCorrelation, ToolCall, threading.Event], ToolResult]


def _action_boundary(
    state: MindState,
    executor: Executor,
    *,
    spec: ToolSpec | None = None,
    authorize: Callable[[ToolBatchCorrelation, ToolCall], ToolAuthorization] | None = None,
) -> tuple[ProposalApplicationCoordinator, DeterministicToolSessionFactory]:
    actual_spec = spec or ToolSpec(
        "fixture.emit",
        "Emit one bounded fixture action.",
        {
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 64}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    registry = ApplicationToolRegistry([ToolBinding(actual_spec, executor, authorize=authorize)])
    factory = DeterministicToolSessionFactory(
        registry,
        exposed_tool_names=(actual_spec.name,),
        executor_deadline=0.05,
        containment_deadline=0.2,
    )
    return (
        ProposalApplicationCoordinator(
            state,
            scope_id="fixture-scope",
            tool_registry=registry,
            tool_session_factory=factory,
            action_tool_names={ActionProposalKind.SPEAK: actual_spec.name},
            runtime_instance_id="fixture-runtime",
        ),
        factory,
    )


@pytest.mark.asyncio
async def test_a_valid_state_proposal_uses_trusted_atomic_delta_and_increments_version() -> None:
    proposal = StateProposal(StateProposalKind.CREATE_INTENTION, "follow up later")
    outcome, state = await _run_outcome(CognitionCandidate(state_proposals=(proposal,)))
    boundary = ProposalApplicationCoordinator(
        state,
        scope_id="fixture-scope",
        state_provenance=MindStateProvenance("user-1", "assistant-1"),
    )

    result = await boundary.apply(outcome)

    assert result.status is ProposalApplicationStatus.APPLIED
    assert result.state.status is StateApplicationStatus.APPLIED
    assert result.state.version_before == 0
    assert result.state.version_after == 1
    assert state.version == 1
    assert [item.text for item in state.intentions()] == ["follow up later"]
    assert result.state.proposals[0].intention_id == state.intentions()[0].intention_id
    assert boundary.application_count == 1


@pytest.mark.asyncio
async def test_b_stale_state_proposal_fails_closed_without_mutation() -> None:
    state = MindState()
    outcome, _ = await _run_outcome(
        CognitionCandidate(
            state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, "stale"),)
        ),
        state,
    )
    existing = state.create_intention(
        "newer state", user_message_id="user-current", assistant_message_id="assistant-current"
    )
    assert existing is not None
    before = state.snapshot()
    boundary = ProposalApplicationCoordinator(
        state,
        scope_id="fixture-scope",
        state_provenance=MindStateProvenance("user-1", "assistant-1"),
    )

    result = boundary.apply_sync(outcome)

    assert result.status is ProposalApplicationStatus.STALE
    assert result.state.status is StateApplicationStatus.STALE
    assert result.state.reason_code == "state_version_conflict"
    assert state.snapshot() == before


@pytest.mark.asyncio
async def test_b_state_batch_capacity_failure_is_all_or_nothing() -> None:
    state = MindState(intentions_capacity=1)
    existing = state.create_intention(
        "already active", user_message_id="user-current", assistant_message_id="assistant-current"
    )
    assert existing is not None
    outcome, _ = await _run_outcome(
        CognitionCandidate(
            state_proposals=(
                StateProposal(StateProposalKind.CREATE_INTENTION, "first"),
                StateProposal(StateProposalKind.CREATE_INTENTION, "second"),
            )
        ),
        state,
    )
    before = state.snapshot()
    boundary = ProposalApplicationCoordinator(
        state,
        scope_id="fixture-scope",
        state_provenance=MindStateProvenance("user-1", "assistant-1"),
    )

    result = boundary.apply_sync(outcome)

    assert result.status is ProposalApplicationStatus.REJECTED
    assert result.state.reason_code == "state_capacity_exceeded"
    assert state.snapshot() == before


@pytest.mark.asyncio
async def test_c_valid_action_compiles_to_trusted_p4_call_only_at_application() -> None:
    calls: list[ToolCall] = []

    def executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        calls.append(call)
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.CONFIRMED)

    state = MindState()
    outcome, _ = await _run_outcome(
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "hello"),)),
        state,
    )
    boundary, factory = _action_boundary(state, executor)
    assert calls == []

    result = boundary.apply_sync(outcome)

    assert result.status is ProposalApplicationStatus.APPLIED
    assert result.actions.status is ActionApplicationStatus.APPLIED
    assert len(calls) == 1
    assert calls[0].tool_name == "fixture.emit"
    assert calls[0].arguments == {"text": "hello"}
    assert result.actions.proposals[0].status is ToolResultStatus.OK
    assert result.actions.proposals[0].effect is ToolEffect.CONFIRMED
    assert factory.sessions[0].evidence()[-1].effect == ToolEffect.CONFIRMED.value


@pytest.mark.asyncio
async def test_d_denied_action_has_zero_executor_calls_and_zero_effects() -> None:
    calls: list[str] = []

    def executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        calls.append(call.call_id)
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.CONFIRMED)

    outcome, state = await _run_outcome(
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "denied"),))
    )
    boundary, _ = _action_boundary(
        state,
        executor,
        authorize=lambda correlation, call: ToolAuthorization.DENIED,
    )

    result = boundary.apply_sync(outcome)

    assert result.actions.status is ActionApplicationStatus.REJECTED
    assert result.actions.proposals[0].status is ToolResultStatus.DENIED
    assert result.actions.proposals[0].effect is ToolEffect.NONE
    assert calls == []


@pytest.mark.asyncio
async def test_d_invalid_action_is_rejected_by_front_loaded_schema_validation() -> None:
    calls: list[str] = []

    def executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        calls.append(call.call_id)
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.CONFIRMED)

    spec = ToolSpec(
        "fixture.short",
        "One-character action.",
        {"type": "object", "properties": {"text": {"type": "string", "maxLength": 1}}},
    )
    outcome, state = await _run_outcome(
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "too long"),))
    )
    boundary, _ = _action_boundary(state, executor, spec=spec)

    result = boundary.apply_sync(outcome)

    assert result.status is ProposalApplicationStatus.REJECTED
    assert result.actions.proposals[0].status is ToolResultStatus.INVALID
    assert result.actions.proposals[0].reason_code == "string_too_long"
    assert calls == []


@pytest.mark.asyncio
async def test_e_unknown_effect_is_preserved_and_never_retried() -> None:
    attempts: list[str] = []

    def executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        attempts.append(call.call_id)
        return ToolResult(
            call.call_id,
            ToolResultStatus.TIMED_OUT,
            None,
            reason_code="executor_timeout",
            effect=ToolEffect.UNKNOWN,
        )

    outcome, state = await _run_outcome(
        CognitionCandidate(
            action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "ambiguous"),)
        )
    )
    boundary, _ = _action_boundary(state, executor)

    first = boundary.apply_sync(outcome)
    second = boundary.apply_sync(outcome)

    assert first.actions.proposals[0].status is ToolResultStatus.TIMED_OUT
    assert first.actions.proposals[0].effect is ToolEffect.UNKNOWN
    assert second.status is ProposalApplicationStatus.DUPLICATE
    assert attempts == [first.actions.proposals[0].call_id]


@pytest.mark.asyncio
async def test_f_duplicate_application_is_fenced_before_second_external_attempt() -> None:
    attempts: list[str] = []

    def executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        attempts.append(call.call_id)
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.CONFIRMED)

    outcome, state = await _run_outcome(
        CognitionCandidate(action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "once"),))
    )
    boundary, _ = _action_boundary(state, executor)

    first = await boundary.apply(outcome)
    second = await boundary.apply(outcome)

    assert first.status is ProposalApplicationStatus.APPLIED
    assert second.status is ProposalApplicationStatus.DUPLICATE
    assert boundary.application_id_for(outcome) == first.application_id
    assert attempts == [first.actions.proposals[0].call_id]


@pytest.mark.asyncio
async def test_g_invalid_member_in_mixed_batch_prevents_state_and_external_effects() -> None:
    attempts: list[str] = []

    def executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        attempts.append(call.call_id)
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.CONFIRMED)

    spec = ToolSpec(
        "fixture.single",
        "One-character action.",
        {
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 1}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    state = MindState()
    outcome, _ = await _run_outcome(
        CognitionCandidate(
            state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, "must not apply"),),
            action_proposals=(
                ActionProposal(ActionProposalKind.SPEAK, "x"),
                ActionProposal(ActionProposalKind.SPEAK, "too long"),
            ),
        ),
        state,
    )
    boundary, _ = _action_boundary(state, executor, spec=spec)

    result = boundary.apply_sync(outcome)

    assert result.status is ProposalApplicationStatus.REJECTED
    assert result.state.status is StateApplicationStatus.REJECTED
    assert result.actions.status is ActionApplicationStatus.REJECTED
    assert state.version == 0
    assert state.intentions() == ()
    assert attempts == []


@pytest.mark.asyncio
async def test_h_partial_external_application_records_each_settlement_without_rollback_claim() -> (
    None
):
    attempts: list[str] = []

    def executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        attempts.append(call.call_id)
        if len(attempts) == 1:
            return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.CONFIRMED)
        return ToolResult(
            call.call_id,
            ToolResultStatus.FAILED,
            None,
            reason_code="executor_failed",
            effect=ToolEffect.NONE,
        )

    outcome, state = await _run_outcome(
        CognitionCandidate(
            action_proposals=(
                ActionProposal(ActionProposalKind.SPEAK, "first"),
                ActionProposal(ActionProposalKind.SPEAK, "second"),
            )
        )
    )
    boundary, _ = _action_boundary(state, executor)

    result = boundary.apply_sync(outcome)

    assert result.status is ProposalApplicationStatus.PARTIAL
    assert result.actions.status is ActionApplicationStatus.PARTIAL
    assert [item.status for item in result.actions.proposals] == [
        ToolResultStatus.OK,
        ToolResultStatus.FAILED,
    ]
    assert [item.effect for item in result.actions.proposals] == [
        ToolEffect.CONFIRMED,
        ToolEffect.NONE,
    ]
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_i_application_does_not_write_canonical_conversation_history() -> None:
    outcome, state = await _run_outcome(
        CognitionCandidate(
            state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, "private state"),)
        )
    )
    core = ConversationCore(ModelRuntime(), scope_id="conversation-isolation")
    boundary = ProposalApplicationCoordinator(
        state,
        scope_id="fixture-scope",
        state_provenance=MindStateProvenance("user-1", "assistant-1"),
    )

    result = boundary.apply_sync(outcome)

    assert result.status is ProposalApplicationStatus.APPLIED
    assert core.history == ()
    assert "private state" not in repr(core.history)


@pytest.mark.asyncio
async def test_j_running_mind_1c_still_has_no_state_or_action_effect() -> None:
    state = MindState()
    window = ObservationWindow(4)
    window.admit(_observation())
    runner = CognitionEpisodeRunner(
        _FixtureEngine(
            CognitionCandidate(
                state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, "inert"),),
                action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "inert"),),
            )
        ),
        window,
        state,
        scope_id="fixture-scope",
    )

    outcome = await runner.run(CognitionTrigger(("observation-1",), "fixture"))

    assert outcome is not None
    assert outcome.is_completed
    assert state.version == 0
    assert state.intentions() == ()


def test_inert_outcome_without_runner_completion_proof_is_ineligible() -> None:
    outcome = CognitionOutcome("episode-manual", "trigger-manual")
    boundary = ProposalApplicationCoordinator(MindState(), scope_id="fixture-scope")

    result = boundary.apply_sync(outcome)

    assert result.status is ProposalApplicationStatus.INELIGIBLE
    assert result.reason_code == "cognition_not_completed"
