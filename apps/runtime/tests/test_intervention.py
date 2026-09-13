"""COG-V1-E1 deterministic intervention and social-permission proofs."""

from __future__ import annotations

import inspect
from dataclasses import fields

import pytest
from lilavel_core import (
    LILAVEL_CHARACTER_V0,
    AttentionDecision,
    CognitionPolicyDecision,
    CognitionReasonCode,
    DispositionCandidate,
    InterventionDecision,
    WorkingState,
)

import lilavel_runtime.intervention as intervention_module
from lilavel_runtime import (
    COG_V1_E1_SCENARIOS,
    ActivityState,
    AlwaysNoneInterventionPolicy,
    AmbientInterventionExcluded,
    DeterministicInterventionPolicy,
    FloorState,
    FreshnessBucket,
    FreshnessClass,
    HandlingState,
    InterventionBudgetState,
    InterventionCandidate,
    InterventionScenario,
    InterventionValue,
    RecentSpeechState,
    SocialPermissionContext,
    SocialPermissionResult,
    SocialSensitivity,
    SpeakingSurfaceState,
    candidate_to_policy_decision,
    evaluate_always_none_baseline,
    evaluate_intervention_policy,
    evaluate_intervention_scenario,
)
from lilavel_runtime.semantic_actor import SemanticPriority, SemanticSourceKind


def _context(**overrides: object) -> SocialPermissionContext:
    values: dict[str, object] = {
        "priority": SemanticPriority.NON_USER,
        "source_kind": SemanticSourceKind.EXTERNAL,
        "speaking_surface": SpeakingSurfaceState.AVAILABLE,
        "freshness_class": FreshnessClass.REACTIVE,
        "freshness": FreshnessBucket.FRESH,
        "activity": ActivityState.CURRENT,
        "floor": FloorState.FREE,
        "recent_speech": RecentSpeechState.CLEAR,
        "intervention_budget": InterventionBudgetState.AVAILABLE,
        "handling": HandlingState.UNRESOLVED,
        "sensitivity": SocialSensitivity.ORDINARY,
        "response_obligation": False,
        "continuity_current": False,
    }
    values.update(overrides)
    return SocialPermissionContext(**values)  # type: ignore[arg-type]


def _candidate(
    decision: InterventionDecision,
    *reasons: CognitionReasonCode,
) -> InterventionCandidate:
    return InterventionCandidate(decision, reasons)


def _interject() -> InterventionCandidate:
    return _candidate(
        InterventionDecision.INTERJECT,
        CognitionReasonCode.CONTRADICTION,
        CognitionReasonCode.EVIDENCE_UPDATE,
    )


def test_intervention_contract_is_typed_bounded_and_has_no_interrupt_value() -> None:
    assert set(InterventionDecision) == {
        InterventionDecision.NONE,
        InterventionDecision.RESPOND,
        InterventionDecision.INTERJECT,
    }
    assert not hasattr(InterventionDecision, "INTERRUPT")
    assert (
        _candidate(
            InterventionDecision.INTERJECT,
            CognitionReasonCode.CONTRADICTION,
        ).semantic_value
        is InterventionValue.MATERIAL_CONTRIBUTION
    )
    with pytest.raises(ValueError, match="reason-code bound"):
        InterventionCandidate(
            InterventionDecision.INTERJECT,
            tuple(CognitionReasonCode)[:9],
        )
    with pytest.raises(ValueError, match="semantic_value"):
        InterventionCandidate(
            InterventionDecision.INTERJECT,
            (CognitionReasonCode.CONTRADICTION,),
            InterventionValue.RESPONSE_OBLIGATION,
        )


def test_silent_cognition_remains_valid_and_does_not_erase_proposal_state() -> None:
    decision = CognitionPolicyDecision(
        attention=AttentionDecision.THINK,
        intervention=InterventionDecision.NONE,
        working_state=WorkingState("retain an internal proposal", stance="curious"),
        reason_codes=(CognitionReasonCode.AMBIENT_CONTEXT,),
    )
    result = DeterministicInterventionPolicy().evaluate(
        _candidate(InterventionDecision.NONE, CognitionReasonCode.AMBIENT_CONTEXT),
        _context(),
    )
    assert decision.working_state is not None
    assert result.decision is InterventionDecision.NONE
    assert result.permitted is False


def test_direct_user_is_outside_ambient_e1_and_excluded_from_eval() -> None:
    with pytest.raises(AmbientInterventionExcluded):
        DeterministicInterventionPolicy().evaluate(
            _candidate(InterventionDecision.RESPOND, CognitionReasonCode.SOCIAL_FOLLOWUP),
            _context(priority=SemanticPriority.USER, source_kind=SemanticSourceKind.USER),
        )

    direct = next(scenario for scenario in COG_V1_E1_SCENARIOS if scenario.excluded_from_e1)

    class _MustNotRun:
        def evaluate(self, candidate: object, context: object) -> object:
            del candidate, context
            raise AssertionError("excluded direct USER scenario entered E1")

    result = evaluate_intervention_scenario(_MustNotRun(), direct)
    assert result.passed is True
    assert result.predicted_decision is None
    assert result.checks["excluded_from_e1"] is True


@pytest.mark.parametrize(
    ("context", "reason"),
    (
        (
            _context(speaking_surface=SpeakingSurfaceState.UNAVAILABLE),
            CognitionReasonCode.NO_SPEAKING_SURFACE,
        ),
        (_context(floor=FloorState.BUSY), CognitionReasonCode.FLOOR_BUSY),
        (_context(freshness=FreshnessBucket.STALE), CognitionReasonCode.STALE_CONTEXT),
        (
            _context(intervention_budget=InterventionBudgetState.EXHAUSTED),
            CognitionReasonCode.INTERVENTION_BUDGET_EXHAUSTED,
        ),
    ),
)
def test_hard_current_state_denials_resolve_to_none(
    context: SocialPermissionContext,
    reason: CognitionReasonCode,
) -> None:
    result = DeterministicInterventionPolicy().evaluate(_interject(), context)
    assert result.decision is InterventionDecision.NONE
    assert reason in result.reason_codes
    assert result.permitted is False


def test_fresh_candidate_can_be_allowed_but_stale_revalidation_denies_it() -> None:
    policy = DeterministicInterventionPolicy()
    candidate = _interject()
    allowed = policy.evaluate(candidate, _context(freshness=FreshnessBucket.FRESH))
    denied = policy.revalidate(candidate, _context(freshness=FreshnessBucket.STALE))
    assert allowed.decision is InterventionDecision.INTERJECT
    assert denied.decision is InterventionDecision.NONE
    assert CognitionReasonCode.STALE_CONTEXT in denied.reason_codes


def test_freshness_is_per_class_and_aging_continuity_can_support_respond() -> None:
    policy = DeterministicInterventionPolicy()
    response = _candidate(InterventionDecision.RESPOND, CognitionReasonCode.SOCIAL_FOLLOWUP)
    assert (
        policy.decide(
            response,
            _context(
                freshness_class=FreshnessClass.CONTINUITY,
                freshness=FreshnessBucket.AGING,
                response_obligation=True,
            ),
        )
        is InterventionDecision.RESPOND
    )
    assert (
        policy.decide(
            response,
            _context(
                freshness_class=FreshnessClass.REACTIVE,
                freshness=FreshnessBucket.AGING,
                response_obligation=True,
            ),
        )
        is InterventionDecision.NONE
    )


def test_recent_speech_suppresses_interject_but_not_trusted_respond() -> None:
    policy = DeterministicInterventionPolicy()
    suppressed = policy.evaluate(
        _interject(),
        _context(recent_speech=RecentSpeechState.RECENT),
    )
    response = policy.evaluate(
        _candidate(InterventionDecision.RESPOND, CognitionReasonCode.SOCIAL_FOLLOWUP),
        _context(recent_speech=RecentSpeechState.RECENT, response_obligation=True),
    )
    assert suppressed.decision is InterventionDecision.NONE
    assert CognitionReasonCode.SOCIAL_BACKOFF in suppressed.reason_codes
    assert response.decision is InterventionDecision.RESPOND


def test_response_obligation_requires_independent_trusted_context() -> None:
    policy = DeterministicInterventionPolicy()
    candidate = _candidate(InterventionDecision.RESPOND, CognitionReasonCode.SOCIAL_FOLLOWUP)
    denied = policy.evaluate(candidate, _context())
    allowed = policy.evaluate(candidate, _context(response_obligation=True))
    continuous = policy.evaluate(candidate, _context(continuity_current=True))
    assert denied.decision is InterventionDecision.NONE
    assert CognitionReasonCode.NO_RESPONSE_OBLIGATION in denied.reason_codes
    assert allowed.decision is InterventionDecision.RESPOND
    assert continuous.decision is InterventionDecision.RESPOND


def test_material_contribution_may_interject_but_interest_or_humor_alone_cannot() -> None:
    policy = DeterministicInterventionPolicy()
    assert policy.decide(_interject(), _context()) is InterventionDecision.INTERJECT
    for candidate in (
        _candidate(InterventionDecision.INTERJECT, CognitionReasonCode.INTEREST_AFFINITY),
        _candidate(InterventionDecision.INTERJECT, CognitionReasonCode.PLAYFUL_CONTEXT),
    ):
        result = policy.evaluate(candidate, _context())
        assert result.decision is InterventionDecision.NONE
        assert CognitionReasonCode.INTERRUPTION_COST_HIGH in result.reason_codes


def test_handled_and_vulnerable_unsolicited_cases_are_silent() -> None:
    policy = DeterministicInterventionPolicy()
    handled = policy.evaluate(_interject(), _context(handling=HandlingState.HANDLED))
    unknown = policy.evaluate(_interject(), _context(handling=HandlingState.UNKNOWN))
    vulnerable = policy.evaluate(_interject(), _context(sensitivity=SocialSensitivity.VULNERABLE))
    assert handled.decision is InterventionDecision.NONE
    assert CognitionReasonCode.ALREADY_HANDLED in handled.reason_codes
    assert unknown.decision is InterventionDecision.NONE
    assert CognitionReasonCode.STALE_CONTEXT in unknown.reason_codes
    assert vulnerable.decision is InterventionDecision.NONE
    assert CognitionReasonCode.VULNERABILITY in vulnerable.reason_codes


def test_critical_event_has_no_unsupported_backoff_bypass() -> None:
    candidate = _candidate(
        InterventionDecision.INTERJECT,
        CognitionReasonCode.CRITICAL_EVENT,
        CognitionReasonCode.HIGH_RELEVANCE,
    )
    result = DeterministicInterventionPolicy().evaluate(
        candidate,
        _context(recent_speech=RecentSpeechState.RECENT),
    )
    assert result.decision is InterventionDecision.NONE
    assert CognitionReasonCode.SOCIAL_BACKOFF in result.reason_codes


def test_new_evidence_objects_have_no_raw_text_or_effect_authority() -> None:
    for value_type in (
        InterventionCandidate,
        SocialPermissionContext,
        SocialPermissionResult,
    ):
        names = {field.name for field in fields(value_type)}
        assert not names.intersection({"text", "prompt", "content", "prose", "output"})
        assert not names.intersection(
            {"ActionProposal", "ProposalApplicationCoordinator", "PresentationAction"}
        )


def test_e1_policy_has_no_generation_randomness_or_presentation_path() -> None:
    source = inspect.getsource(intervention_module)
    for forbidden in (
        "import random",
        "generate_for_run",
        "ModelRuntime",
        "ActionProposal",
        "ProposalApplicationCoordinator",
        "PRESENTATION_",
        "submit_cognition",
    ):
        assert forbidden not in source
    assert set(SemanticPriority) == {SemanticPriority.USER, SemanticPriority.NON_USER}


def test_direct_user_disposition_contract_remains_fixed() -> None:
    decision = candidate_to_policy_decision(
        DispositionCandidate(
            aim="answer",
            stance="neutral",
            engagement="normal",
            directness="normal",
            desired_length="normal",
            humor_allowed=False,
            question_policy="avoid",
            initiative="normal",
            reason_codes=(CognitionReasonCode.DIRECT_ADDRESS,),
        )
    )
    assert decision.attention is AttentionDecision.THINK
    assert decision.intervention is InterventionDecision.RESPOND


def test_e1_policy_does_not_mutate_character_canon() -> None:
    before = LILAVEL_CHARACTER_V0
    policy = DeterministicInterventionPolicy()
    policy.evaluate(_interject(), _context())
    policy.evaluate(
        _candidate(InterventionDecision.RESPOND, CognitionReasonCode.SOCIAL_FOLLOWUP),
        _context(response_obligation=True),
    )
    assert LILAVEL_CHARACTER_V0 is before


def test_corpus_is_silence_first_and_reports_counterfactual_metrics() -> None:
    report = evaluate_intervention_policy()
    metrics = report.metrics
    assert len(COG_V1_E1_SCENARIOS) == 30
    assert metrics.scenario_count == 30
    assert metrics.excluded_scenario_count == 1
    assert metrics.evaluated_scenario_count == 29
    assert all(result.passed for result in report.scenario_results)
    assert metrics.interject_precision == 1.0
    assert metrics.interject_recall == 1.0
    assert metrics.respond_recall == 1.0
    assert metrics.false_interject_count == 0
    assert metrics.false_interject_rate == 0.0
    assert metrics.silence_preservation_rate == 1.0
    assert metrics.hard_social_violation_count == 0
    assert (
        metrics.confusion_matrix[InterventionDecision.NONE.value][InterventionDecision.NONE.value]
        > 0
    )
    assert (
        metrics.confusion_matrix[InterventionDecision.RESPOND.value][
            InterventionDecision.RESPOND.value
        ]
        > 0
    )
    assert (
        metrics.confusion_matrix[InterventionDecision.INTERJECT.value][
            InterventionDecision.INTERJECT.value
        ]
        > 0
    )


def test_corpus_contains_matched_counterfactual_pairs() -> None:
    groups: dict[str, list[InterventionScenario]] = {}
    for scenario in COG_V1_E1_SCENARIOS:
        if scenario.counterfactual_group is not None:
            groups.setdefault(scenario.counterfactual_group, []).append(scenario)
    paired = {name: values for name, values in groups.items() if len(values) >= 2}
    assert len(paired) == 7
    assert {scenario.id for scenario in paired["recent-speech"]} == {
        "cog-v1-e1-14",
        "cog-v1-e1-15",
    }
    assert {scenario.id for scenario in paired["freshness"]} == {
        "cog-v1-e1-16",
        "cog-v1-e1-17",
    }
    assert {scenario.id for scenario in paired["floor-state"]} == {
        "cog-v1-e1-28",
        "cog-v1-e1-29",
    }
    for group in ("recent-speech", "freshness", "floor-state", "handled-state"):
        values = paired[group]
        assert values[0].candidate == values[1].candidate


def test_always_none_baseline_preserves_silence_without_false_interjects() -> None:
    report = evaluate_always_none_baseline()
    metrics = report.metrics
    assert report.policy_id == AlwaysNoneInterventionPolicy.policy_id
    assert metrics.interject_precision == 0.0
    assert metrics.interject_recall == 0.0
    assert metrics.respond_recall == 0.0
    assert metrics.false_interject_count == 0
    assert metrics.silence_preservation_rate == 1.0
    assert metrics.hard_social_violation_count == 0
    assert metrics.intervention_rate == 0.0


def test_source_kind_does_not_create_a_third_semantic_lane() -> None:
    assert SemanticSourceKind.USER in set(SemanticSourceKind)
    assert SemanticSourceKind.INTERNAL in set(SemanticSourceKind)
    assert SemanticPriority.NON_USER in set(SemanticPriority)
