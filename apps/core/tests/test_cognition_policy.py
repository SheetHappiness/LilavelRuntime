"""Deterministic COG-V1-A cognition-policy contract and corpus proofs."""

from __future__ import annotations

import pytest

from lilavel_core import (
    LILAVEL_CHARACTER_V0,
    MAX_COGNITION_REASON_CODES,
    AttentionDecision,
    CognitionPolicyDecision,
    CognitionReasonCode,
    InterventionDecision,
    ResponseDisposition,
    WorkingState,
    compile_guidance,
)
from lilavel_core.cognition_eval import (
    COG_V1_A_REFERENCE_DECISIONS,
    COG_V1_A_SCENARIOS,
    CognitionScenario,
    CognitionScenarioSource,
    evaluate_cognition_corpus,
    evaluate_cognition_policy,
)


def test_policy_contract_rejects_impossible_combinations() -> None:
    with pytest.raises(ValueError, match="DROP/NOTE"):
        CognitionPolicyDecision(
            AttentionDecision.DROP,
            InterventionDecision.RESPOND,
            reason_codes=(CognitionReasonCode.LOW_RELEVANCE,),
        )

    with pytest.raises(ValueError, match="speaking interventions"):
        CognitionPolicyDecision(
            AttentionDecision.THINK,
            InterventionDecision.INTERJECT,
            reason_codes=(CognitionReasonCode.HIGH_RELEVANCE,),
        )

    with pytest.raises(ValueError, match="NONE intervention"):
        CognitionPolicyDecision(
            AttentionDecision.THINK,
            InterventionDecision.NONE,
            response_disposition=ResponseDisposition(),
            reason_codes=(CognitionReasonCode.AMBIENT_CONTEXT,),
        )


def test_think_none_is_valid_for_non_speaking_cognition() -> None:
    decision = CognitionPolicyDecision(
        AttentionDecision.THINK,
        InterventionDecision.NONE,
        working_state=WorkingState("retain the relevant context", stance="curious"),
        reason_codes=(CognitionReasonCode.AMBIENT_CONTEXT,),
    )

    assert decision.response_disposition is None
    assert decision.working_state is not None


def test_reason_codes_are_bounded_stable_and_not_arbitrary_text() -> None:
    with pytest.raises(ValueError, match="require a reason code"):
        CognitionPolicyDecision(AttentionDecision.THINK, InterventionDecision.NONE)

    with pytest.raises(ValueError, match="reason-code bound"):
        CognitionPolicyDecision(
            AttentionDecision.THINK,
            InterventionDecision.NONE,
            reason_codes=tuple(CognitionReasonCode)[: MAX_COGNITION_REASON_CODES + 1],
        )

    with pytest.raises(TypeError, match="CognitionReasonCode"):
        CognitionPolicyDecision(
            AttentionDecision.THINK,
            InterventionDecision.NONE,
            reason_codes=("direct_address",),  # type: ignore[arg-type]
        )


def test_direct_user_scenarios_cannot_encode_optional_silence() -> None:
    for scenario in COG_V1_A_SCENARIOS:
        if scenario.source_class == CognitionScenarioSource.DIRECT_USER:
            assert scenario.expected_attention == frozenset({AttentionDecision.THINK})
            assert scenario.expected_intervention == frozenset({InterventionDecision.RESPOND})


def test_reference_corpus_passes_in_stable_order() -> None:
    results = evaluate_cognition_corpus(COG_V1_A_REFERENCE_DECISIONS)

    assert len(COG_V1_A_SCENARIOS) == 20
    assert [result.scenario_id for result in results] == [
        scenario.id for scenario in COG_V1_A_SCENARIOS
    ]
    assert all(result.passed for result in results)
    assert all(result.failures == () for result in results)


def test_corpus_contains_every_required_family_and_policy_counterexample() -> None:
    ids = {scenario.id for scenario in COG_V1_A_SCENARIOS}
    assert ids == {f"cog-v1-a-{number:02d}" for number in range(1, 21)}

    irrelevant = next(s for s in COG_V1_A_SCENARIOS if s.id == "cog-v1-a-13")
    tailoring = next(s for s in COG_V1_A_SCENARIOS if s.id == "cog-v1-a-14")
    curiosity_trap = next(s for s in COG_V1_A_SCENARIOS if s.id == "cog-v1-a-20")
    vulnerable = next(s for s in COG_V1_A_SCENARIOS if s.id == "cog-v1-a-08")

    assert CognitionReasonCode.INTEREST_AFFINITY in irrelevant.forbidden_reason_codes
    assert CognitionReasonCode.INTEREST_AFFINITY in tailoring.required_reason_codes
    assert curiosity_trap.disposition_constraints is not None
    assert curiosity_trap.disposition_constraints.question_policy == frozenset({"avoid"})
    assert vulnerable.disposition_constraints is not None
    assert vulnerable.disposition_constraints.humor_allowed is False


def test_selected_policy_disposition_compiles_through_existing_core_guidance() -> None:
    scenario = next(item for item in COG_V1_A_SCENARIOS if item.id == "cog-v1-a-03")
    decision = COG_V1_A_REFERENCE_DECISIONS[scenario.id]

    guidance = compile_guidance(
        # The policy selects only dynamic behavior; identity remains the existing Core canon.
        LILAVEL_CHARACTER_V0,
        state=decision.working_state,
        disposition=decision.response_disposition,
    )

    assert any("stance: skeptical" in block for block in guidance)
    assert any("aim: challenge" in block for block in guidance)


@pytest.mark.parametrize(
    ("scenario_id", "bad_change"),
    (
        ("cog-v1-a-01", "silence"),
        ("cog-v1-a-09", "interject"),
        ("cog-v1-a-13", "interest"),
        ("cog-v1-a-20", "question"),
    ),
)
def test_policy_harness_catches_key_antipatterns(scenario_id: str, bad_change: str) -> None:
    scenario = next(item for item in COG_V1_A_SCENARIOS if item.id == scenario_id)
    reference = COG_V1_A_REFERENCE_DECISIONS[scenario_id]
    if bad_change == "silence":
        bad = CognitionPolicyDecision(
            AttentionDecision.THINK,
            InterventionDecision.NONE,
            reason_codes=reference.reason_codes,
        )
    elif bad_change == "interject":
        bad = CognitionPolicyDecision(
            AttentionDecision.THINK,
            InterventionDecision.INTERJECT,
            response_disposition=ResponseDisposition(aim="answer"),
            reason_codes=reference.reason_codes,
        )
    elif bad_change == "interest":
        bad = CognitionPolicyDecision(
            reference.attention,
            reference.intervention,
            reason_codes=(*reference.reason_codes, CognitionReasonCode.INTEREST_AFFINITY),
        )
    else:
        bad = CognitionPolicyDecision(
            reference.attention,
            reference.intervention,
            working_state=reference.working_state,
            response_disposition=ResponseDisposition(
                aim="answer",
                question_policy="invite",
            ),
            reason_codes=reference.reason_codes,
        )

    assert not evaluate_cognition_policy(scenario, bad).passed


def test_corpus_schema_rejects_invalid_direct_user_expectation() -> None:
    with pytest.raises(ValueError, match="direct-user"):
        CognitionScenario(
            id="bad-direct",
            source_class=CognitionScenarioSource.DIRECT_USER,
            input_context="A direct user case.",
            expected_attention=frozenset({AttentionDecision.THINK}),
            expected_intervention=frozenset({InterventionDecision.NONE}),
            required_reason_codes=frozenset({CognitionReasonCode.DIRECT_ADDRESS}),
        )
