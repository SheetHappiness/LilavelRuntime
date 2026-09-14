"""COG-V1-C selective-disposition policy and evaluation proofs."""

from __future__ import annotations

import inspect
from typing import Literal

import pytest

from lilavel_core import (
    AttentionDecision,
    CognitionPolicyDecision,
    CognitionReasonCode,
    DispositionCase,
    DispositionContext,
    DispositionFailureCode,
    DispositionResolution,
    DispositionRoute,
    DispositionRoutingEvidence,
    DispositionSource,
    FakeDeliberativeDispositionPolicy,
    InterventionDecision,
    ResponseDisposition,
    SelectiveDispositionPolicy,
    WorkingState,
    route_disposition,
    validate_disposition_decision,
)
from lilavel_core.disposition import DeterministicFastDispositionPolicy
from lilavel_core.disposition_eval import (
    COG_V1_C_COUNTERFACTUAL_PAIRS,
    COG_V1_C_REFERENCE_DECISIONS,
    COG_V1_C_SCENARIOS,
    COG_V1_C_TRAJECTORIES,
    DispositionScenarioFamily,
    evaluate_disposition_corpus,
)


def _context(
    *,
    source: DispositionSource = DispositionSource.DIRECT_USER,
    case: DispositionCase = DispositionCase.DEFAULT,
    **evidence: bool,
) -> DispositionContext:
    return DispositionContext(
        source=source,
        attention=AttentionDecision.THINK,
        routing_evidence=DispositionRoutingEvidence(**evidence),
        case=case,
    )


def _deliberative_direct_decision(
    reason: CognitionReasonCode,
    *,
    question_policy: Literal["avoid", "required", "invite"] = "avoid",
    stance: Literal["neutral", "curious", "skeptical", "playful", "supportive"] = "neutral",
    initiative: Literal["low", "normal", "high"] = "normal",
) -> CognitionPolicyDecision:
    return CognitionPolicyDecision(
        attention=AttentionDecision.THINK,
        intervention=InterventionDecision.RESPOND,
        working_state=WorkingState("handle the materially important context", stance=stance),
        response_disposition=ResponseDisposition(
            question_policy=question_policy,
            initiative=initiative,
        ),
        reason_codes=(reason,),
    )


def test_route_uses_the_exact_ordered_boolean_rules() -> None:
    assert set(DispositionRoute) == {DispositionRoute.FAST, DispositionRoute.DELIBERATE}
    fields = (
        "material_ambiguity",
        "important_contradiction",
        "evidence_update",
        "high_social_stakes",
        "multiple_plausible_moves",
        "vulnerable_context",
        "intervention_uncertain",
    )

    for field in fields:
        evidence = DispositionRoutingEvidence(**{field: True})
        assert route_disposition(evidence) is DispositionRoute.DELIBERATE

    assert route_disposition(DispositionRoutingEvidence()) is DispositionRoute.FAST
    assert (
        route_disposition(DispositionRoutingEvidence(cheap_reversible_assumption=True))
        is DispositionRoute.FAST
    )


def test_routing_evidence_is_immutable_and_boolean_only() -> None:
    evidence = DispositionRoutingEvidence(material_ambiguity=True)

    with pytest.raises(AttributeError):
        evidence.material_ambiguity = False  # type: ignore[misc]
    with pytest.raises(TypeError, match="must be a bool"):
        DispositionRoutingEvidence(material_ambiguity=1)  # type: ignore[arg-type]


def test_fast_and_deliberate_scenario_corpus_is_exactly_bounded() -> None:
    assert len(COG_V1_C_SCENARIOS) == 13
    assert [scenario.id for scenario in COG_V1_C_SCENARIOS] == [
        "F01",
        "F02",
        "F03",
        "F04",
        "F05",
        "F06",
        "D01",
        "D02",
        "D03",
        "D04",
        "D05",
        "D06",
        "D07",
    ]
    assert (
        sum(scenario.family is DispositionScenarioFamily.FAST for scenario in COG_V1_C_SCENARIOS)
        == 6
    )
    assert (
        sum(
            scenario.family is DispositionScenarioFamily.DELIBERATE
            for scenario in COG_V1_C_SCENARIOS
        )
        == 7
    )

    resolutions: dict[str, DispositionResolution] = {}
    for scenario in COG_V1_C_SCENARIOS:
        deliberative = (
            FakeDeliberativeDispositionPolicy(COG_V1_C_REFERENCE_DECISIONS[scenario.id])
            if scenario.family is DispositionScenarioFamily.DELIBERATE
            else None
        )
        resolutions[scenario.id] = SelectiveDispositionPolicy(
            deliberative_policy=deliberative,
        ).decide(scenario.context)

    results = evaluate_disposition_corpus(resolutions)
    assert len(results) == 13
    assert all(result.passed for result in results)
    assert all(result.failures == () for result in results)


def test_fast_policy_is_pure_and_makes_no_deliberative_call() -> None:
    deliberative = FakeDeliberativeDispositionPolicy(
        _deliberative_direct_decision(CognitionReasonCode.CONTRADICTION)
    )
    policy = SelectiveDispositionPolicy(deliberative_policy=deliberative)

    result = policy.decide(
        _context(case=DispositionCase.SIMPLE_DEFINITION),
    )

    assert result.route is DispositionRoute.FAST
    assert result.intervention is InterventionDecision.RESPOND
    assert result.failure is None
    assert deliberative.calls == 0
    assert result.response_disposition is not None
    assert result.response_disposition.aim == "answer"
    assert result.response_disposition.directness == "high"
    assert result.response_disposition.desired_length == "low"
    assert result.response_disposition.humor_allowed is False
    assert result.response_disposition.question_policy == "avoid"
    assert result.response_disposition.initiative == "low"


def test_fast_specializations_match_the_exact_policy_fields() -> None:
    policy = DeterministicFastDispositionPolicy()

    technical = policy.decide(_context(case=DispositionCase.STRAIGHTFORWARD_TECHNICAL_EXPLANATION))
    assert technical.working_state == WorkingState("respond to the current user turn")
    assert technical.response_disposition == ResponseDisposition(
        aim="answer",
        directness="high",
        desired_length="normal",
        humor_allowed=False,
        question_policy="avoid",
        initiative="normal",
    )

    acknowledgment = policy.decide(_context(case=DispositionCase.ORDINARY_ACKNOWLEDGMENT))
    assert acknowledgment.response_disposition == ResponseDisposition(
        aim="acknowledge",
        directness="normal",
        desired_length="low",
        humor_allowed=False,
        question_policy="avoid",
        initiative="low",
    )

    playful = policy.decide(_context(case=DispositionCase.CLEAR_PLAYFUL_TURN))
    assert playful.working_state == WorkingState(
        "respond to the current user turn", stance="playful"
    )
    assert playful.response_disposition == ResponseDisposition(
        aim="tease",
        directness="normal",
        desired_length="low",
        humor_allowed=True,
        question_policy="avoid",
        initiative="low",
    )


def test_direct_user_default_fast_behavior_is_exact() -> None:
    result = SelectiveDispositionPolicy().decide(_context())

    assert result.route is DispositionRoute.FAST
    assert result.intervention is InterventionDecision.RESPOND
    assert result.working_state == WorkingState(
        focus="respond to the current user turn",
        engagement="normal",
        stance="neutral",
    )
    assert result.response_disposition == ResponseDisposition(
        aim="answer",
        directness="normal",
        desired_length="normal",
        humor_allowed=False,
        question_policy="avoid",
        initiative="normal",
    )


def test_cheap_reversible_ambiguity_does_not_force_a_question() -> None:
    result = SelectiveDispositionPolicy().decide(
        _context(
            case=DispositionCase.CHEAP_REVERSIBLE_AMBIGUITY,
            cheap_reversible_assumption=True,
        )
    )

    assert result.route is DispositionRoute.FAST
    assert result.response_disposition is not None
    assert result.response_disposition.question_policy == "avoid"
    assert CognitionReasonCode.REVERSIBLE_ASSUMPTION in result.reason_codes


def test_material_ambiguity_can_allow_clarification_on_deliberate_path() -> None:
    deliberative = FakeDeliberativeDispositionPolicy(
        _deliberative_direct_decision(
            CognitionReasonCode.AMBIGUITY_MATERIAL,
            question_policy="required",
        )
    )
    result = SelectiveDispositionPolicy(deliberative_policy=deliberative).decide(
        _context(material_ambiguity=True),
    )

    assert result.route is DispositionRoute.DELIBERATE
    assert result.response_disposition is not None
    assert result.response_disposition.question_policy == "required"
    assert deliberative.calls == 1


def test_deliberative_policy_is_a_structured_provider_neutral_seam() -> None:
    decision = _deliberative_direct_decision(CognitionReasonCode.EVIDENCE_UPDATE)
    deliberative = FakeDeliberativeDispositionPolicy(decision)
    context = _context(evidence_update=True)

    assert isinstance(deliberative, object)
    result = SelectiveDispositionPolicy(deliberative_policy=deliberative).decide(context)

    assert result.route is DispositionRoute.DELIBERATE
    assert result.decision is decision
    assert result.reason_codes == (CognitionReasonCode.EVIDENCE_UPDATE,)
    assert deliberative.calls == 1


def test_direct_user_always_responds_and_ambient_never_responds() -> None:
    direct_none = CognitionPolicyDecision(
        attention=AttentionDecision.THINK,
        intervention=InterventionDecision.NONE,
        working_state=WorkingState("retain the relevant context"),
        reason_codes=(CognitionReasonCode.NO_NEW_VALUE,),
    )
    direct_result = SelectiveDispositionPolicy(
        deliberative_policy=FakeDeliberativeDispositionPolicy(direct_none),
    ).decide(_context(important_contradiction=True))
    assert direct_result.intervention is InterventionDecision.RESPOND
    assert direct_result.failure is not None
    assert direct_result.failure.code is DispositionFailureCode.INVALID_DECISION

    ambient_respond = CognitionPolicyDecision(
        attention=AttentionDecision.THINK,
        intervention=InterventionDecision.RESPOND,
        working_state=WorkingState("interrupt the ambient conversation"),
        response_disposition=ResponseDisposition(),
        reason_codes=(CognitionReasonCode.INTERVENTION_UNCERTAIN,),
    )
    ambient_result = SelectiveDispositionPolicy(
        deliberative_policy=FakeDeliberativeDispositionPolicy(ambient_respond),
    ).decide(_context(source=DispositionSource.AMBIENT, intervention_uncertain=True))
    assert ambient_result.intervention is InterventionDecision.NONE
    assert ambient_result.working_state is None
    assert ambient_result.response_disposition is None
    assert ambient_result.failure is not None


def test_policy_failures_use_source_specific_fallbacks_without_swallowing_failure() -> None:
    class RaisingPolicy:
        def decide(self, context: DispositionContext) -> CognitionPolicyDecision:
            del context
            raise RuntimeError("fixture failure")

    direct = SelectiveDispositionPolicy(deliberative_policy=RaisingPolicy()).decide(
        _context(evidence_update=True),
    )
    assert direct.intervention is InterventionDecision.RESPOND
    assert direct.working_state == WorkingState(
        "respond to the current user turn",
        engagement="normal",
        stance="neutral",
    )
    assert direct.response_disposition == ResponseDisposition()
    assert direct.failure is not None
    assert direct.failure.code is DispositionFailureCode.POLICY_ERROR

    ambient = SelectiveDispositionPolicy(deliberative_policy=RaisingPolicy()).decide(
        _context(source=DispositionSource.AMBIENT, intervention_uncertain=True),
    )
    assert ambient.intervention is InterventionDecision.NONE
    assert ambient.working_state is None
    assert ambient.response_disposition is None
    assert ambient.failure is not None
    assert ambient.failure.code is DispositionFailureCode.POLICY_ERROR


def test_semantic_validator_fails_closed_for_think_and_speaking_invariants() -> None:
    direct = _context()

    missing_state = CognitionPolicyDecision(
        attention=AttentionDecision.THINK,
        intervention=InterventionDecision.RESPOND,
        response_disposition=ResponseDisposition(),
        reason_codes=(CognitionReasonCode.DIRECT_ADDRESS,),
    )
    with pytest.raises(ValueError, match="WorkingState"):
        validate_disposition_decision(direct, missing_state)

    with pytest.raises(ValueError, match="response disposition"):
        CognitionPolicyDecision(
            attention=AttentionDecision.THINK,
            intervention=InterventionDecision.INTERJECT,
            working_state=WorkingState("interject carefully"),
            reason_codes=(CognitionReasonCode.INTERVENTION_UNCERTAIN,),
        )

    note_decision = CognitionPolicyDecision(
        attention=AttentionDecision.NOTE,
        intervention=InterventionDecision.NONE,
        reason_codes=(CognitionReasonCode.NO_NEW_VALUE,),
    )
    with pytest.raises(ValueError, match="THINK"):
        validate_disposition_decision(direct, note_decision)

    with pytest.raises(ValueError, match="THINK"):
        SelectiveDispositionPolicy().decide(
            DispositionContext(
                source=DispositionSource.DIRECT_USER,
                attention=AttentionDecision.NOTE,
            )
        )


def test_initiative_is_independent_from_curious_stance() -> None:
    decision = _deliberative_direct_decision(
        CognitionReasonCode.MULTIPLE_PLAUSIBLE_MOVES,
        stance="curious",
        initiative="low",
    )
    result = SelectiveDispositionPolicy(
        deliberative_policy=FakeDeliberativeDispositionPolicy(decision),
    ).decide(_context(multiple_plausible_moves=True))

    assert result.working_state is not None
    assert result.working_state.stance == "curious"
    assert result.response_disposition is not None
    assert result.response_disposition.initiative == "low"
    assert result.response_disposition.question_policy == "avoid"


def test_tailoring_interest_is_contextual_not_keyword_triggered() -> None:
    ordinary = SelectiveDispositionPolicy().decide(_context())
    tailoring = SelectiveDispositionPolicy().decide(
        _context(case=DispositionCase.TAILORING_DISCUSSION),
    )
    ambient_tailoring = SelectiveDispositionPolicy().decide(
        _context(source=DispositionSource.AMBIENT, case=DispositionCase.TAILORING_DISCUSSION),
    )

    assert CognitionReasonCode.INTEREST_AFFINITY not in ordinary.reason_codes
    assert CognitionReasonCode.INTEREST_AFFINITY in tailoring.reason_codes
    assert tailoring.working_state is not None
    assert tailoring.working_state.engagement == "high"
    assert tailoring.working_state.stance == "curious"
    assert ambient_tailoring.intervention is InterventionDecision.NONE
    assert ambient_tailoring.response_disposition is None


def test_exact_counterfactual_pair_count_and_semantics() -> None:
    assert len(COG_V1_C_COUNTERFACTUAL_PAIRS) == 4

    pair1, pair2, pair3, pair4 = COG_V1_C_COUNTERFACTUAL_PAIRS
    coordinator = SelectiveDispositionPolicy(
        deliberative_policy=FakeDeliberativeDispositionPolicy(
            _deliberative_direct_decision(CognitionReasonCode.AMBIGUITY_MATERIAL)
        )
    )
    assert coordinator.decide(pair1.first).intervention is InterventionDecision.RESPOND
    assert coordinator.decide(pair1.second).intervention is InterventionDecision.NONE
    assert coordinator.decide(pair2.first).route is DispositionRoute.FAST
    assert coordinator.decide(pair2.second).route is DispositionRoute.DELIBERATE
    assert coordinator.decide(pair3.first).intervention is InterventionDecision.RESPOND
    assert coordinator.decide(pair3.second).route is DispositionRoute.DELIBERATE
    assert CognitionReasonCode.INTEREST_AFFINITY not in coordinator.decide(pair4.first).reason_codes
    assert CognitionReasonCode.INTEREST_AFFINITY in coordinator.decide(pair4.second).reason_codes


def test_exact_three_trajectory_fixtures_preserve_only_new_evidence() -> None:
    assert len(COG_V1_C_TRAJECTORIES) == 3
    assert [fixture.id for fixture in COG_V1_C_TRAJECTORIES] == ["T01", "T02", "T03"]

    for fixture in COG_V1_C_TRAJECTORIES:
        reason = fixture.required_final_reason or CognitionReasonCode.NO_NEW_VALUE
        deliberative = FakeDeliberativeDispositionPolicy(
            _deliberative_direct_decision(reason),
        )
        results = [
            SelectiveDispositionPolicy(deliberative_policy=deliberative).decide(context)
            for context in fixture.turns
        ]
        final = results[-1]
        assert final.route is fixture.expected_final_route
        assert final.intervention is fixture.expected_final_intervention
        if fixture.required_final_reason is not None:
            assert fixture.required_final_reason in final.reason_codes
        if fixture.expected_final_question_policy is not None:
            assert final.response_disposition is not None
            assert (
                final.response_disposition.question_policy == fixture.expected_final_question_policy
            )


def test_anti_caricature_policy_fields() -> None:
    simple = SelectiveDispositionPolicy().decide(
        _context(case=DispositionCase.SIMPLE_DEFINITION),
    )
    default = SelectiveDispositionPolicy().decide(_context())

    assert simple.response_disposition is not None
    assert simple.response_disposition.humor_allowed is False
    assert simple.response_disposition.aim == "answer"
    assert simple.response_disposition.question_policy == "avoid"
    assert simple.response_disposition.initiative == "low"
    assert default.response_disposition is not None
    assert default.response_disposition.humor_allowed is False
    assert default.response_disposition.initiative == "normal"
    assert default.working_state is not None
    assert default.working_state.stance == "neutral"
    assert default.working_state.focus == "respond to the current user turn"
    assert default.response_disposition.aim == "answer"
    assert default.response_disposition.question_policy == "avoid"
    assert default.response_disposition.initiative == "normal"
    assert "I am" not in default.working_state.focus
    assert "therapy" not in default.working_state.focus.casefold()
    assert "menswear" not in default.working_state.focus.casefold()
    assert "sarcasm" not in default.working_state.focus.casefold()
    assert "explain the joke" not in default.working_state.focus.casefold()
    assert "biography" not in default.working_state.focus.casefold()


def test_production_composition_does_not_import_or_invoke_selective_disposition() -> None:
    from lilavel_core import production_cognition

    source = inspect.getsource(production_cognition)
    assert "lilavel_core.disposition" not in source
    assert "SelectiveDispositionPolicy" not in source
    assert "build_turn_guidance" in source
