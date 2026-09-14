"""Offline COG-V1-C scenario, counterfactual, and trajectory fixtures."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .cognition import (
    AttentionDecision,
    CognitionPolicyDecision,
    CognitionReasonCode,
    InterventionDecision,
    ResponseDisposition,
    WorkingState,
)
from .disposition import (
    DeterministicFastDispositionPolicy,
    DispositionCase,
    DispositionContext,
    DispositionResolution,
    DispositionRoute,
    DispositionRoutingEvidence,
    DispositionSource,
)

__all__ = [
    "COG_V1_C_COUNTERFACTUAL_PAIRS",
    "COG_V1_C_REFERENCE_DECISIONS",
    "COG_V1_C_SCENARIOS",
    "COG_V1_C_TRAJECTORIES",
    "DispositionCounterfactualPair",
    "DispositionScenario",
    "DispositionScenarioFamily",
    "DispositionTrajectoryFixture",
    "DispositionEvalResult",
    "evaluate_disposition_corpus",
    "evaluate_disposition_scenario",
]


class DispositionScenarioFamily(StrEnum):
    FAST = "fast"
    DELIBERATE = "deliberate"


@dataclass(frozen=True, slots=True)
class DispositionScenario:
    """One policy-level COG-V1-C scenario."""

    id: str
    family: DispositionScenarioFamily
    context: DispositionContext
    expected_route: DispositionRoute
    expected_interventions: frozenset[InterventionDecision]
    required_reason_codes: frozenset[CognitionReasonCode] = frozenset()
    expected_aim: str | None = None
    expected_desired_length: str | None = None
    expected_question_policy: str | None = None
    expected_stance: str | None = None
    expected_engagement: str | None = None
    expected_humor_allowed: bool | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("scenario id must be non-empty")
        if type(self.family) is not DispositionScenarioFamily:
            raise TypeError("scenario family must be a DispositionScenarioFamily")
        if type(self.context) is not DispositionContext:
            raise TypeError("scenario context must be a DispositionContext")
        if type(self.expected_route) is not DispositionRoute:
            raise TypeError("scenario route must be a DispositionRoute")
        object.__setattr__(self, "expected_interventions", frozenset(self.expected_interventions))
        object.__setattr__(self, "required_reason_codes", frozenset(self.required_reason_codes))
        if not self.expected_interventions:
            raise ValueError("scenario must allow at least one intervention")
        if not all(type(item) is InterventionDecision for item in self.expected_interventions):
            raise TypeError("scenario interventions must use InterventionDecision")
        if not all(type(item) is CognitionReasonCode for item in self.required_reason_codes):
            raise TypeError("scenario reasons must use CognitionReasonCode")


@dataclass(frozen=True, slots=True)
class DispositionEvalResult:
    scenario_id: str
    passed: bool
    checks: Mapping[str, bool]
    failures: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError("result scenario_id must be non-empty")
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))
        object.__setattr__(self, "failures", tuple(self.failures))
        if self.passed != (not self.failures):
            raise ValueError("result passed must match failures")


@dataclass(frozen=True, slots=True)
class DispositionCounterfactualPair:
    id: str
    first: DispositionContext
    second: DispositionContext

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("counterfactual pair id must be non-empty")
        if (
            type(self.first) is not DispositionContext
            or type(self.second) is not DispositionContext
        ):
            raise TypeError("counterfactual contexts must be DispositionContext values")


@dataclass(frozen=True, slots=True)
class DispositionTrajectoryFixture:
    id: str
    turns: tuple[DispositionContext, ...]
    expected_final_route: DispositionRoute
    expected_final_intervention: InterventionDecision
    required_final_reason: CognitionReasonCode | None = None
    expected_final_question_policy: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("trajectory id must be non-empty")
        object.__setattr__(self, "turns", tuple(self.turns))
        if len(self.turns) != 3:
            raise ValueError("trajectory fixtures must contain exactly three turns")
        if not all(type(turn) is DispositionContext for turn in self.turns):
            raise TypeError("trajectory turns must be DispositionContext values")
        if type(self.expected_final_route) is not DispositionRoute:
            raise TypeError("trajectory route must be a DispositionRoute")
        if type(self.expected_final_intervention) is not InterventionDecision:
            raise TypeError("trajectory intervention must be an InterventionDecision")
        if (
            self.required_final_reason is not None
            and type(self.required_final_reason) is not CognitionReasonCode
        ):
            raise TypeError("trajectory reason must use CognitionReasonCode")


def _direct(
    *,
    case: DispositionCase = DispositionCase.DEFAULT,
    **evidence: bool,
) -> DispositionContext:
    return DispositionContext(
        source=DispositionSource.DIRECT_USER,
        attention=AttentionDecision.THINK,
        routing_evidence=DispositionRoutingEvidence(**evidence),
        case=case,
    )


def _ambient(
    *,
    case: DispositionCase = DispositionCase.DEFAULT,
    **evidence: bool,
) -> DispositionContext:
    return DispositionContext(
        source=DispositionSource.AMBIENT,
        attention=AttentionDecision.THINK,
        routing_evidence=DispositionRoutingEvidence(**evidence),
        case=case,
    )


COG_V1_C_SCENARIOS: tuple[DispositionScenario, ...] = (
    DispositionScenario(
        "F01",
        DispositionScenarioFamily.FAST,
        _direct(case=DispositionCase.SIMPLE_DEFINITION),
        DispositionRoute.FAST,
        frozenset({InterventionDecision.RESPOND}),
        frozenset({CognitionReasonCode.SIMPLE_REQUEST}),
        expected_aim="answer",
        expected_desired_length="low",
        expected_question_policy="avoid",
    ),
    DispositionScenario(
        "F02",
        DispositionScenarioFamily.FAST,
        _direct(case=DispositionCase.SIMPLE_FACTUAL_QUERY),
        DispositionRoute.FAST,
        frozenset({InterventionDecision.RESPOND}),
        frozenset({CognitionReasonCode.SIMPLE_REQUEST}),
    ),
    DispositionScenario(
        "F03",
        DispositionScenarioFamily.FAST,
        _direct(case=DispositionCase.STRAIGHTFORWARD_TECHNICAL_EXPLANATION),
        DispositionRoute.FAST,
        frozenset({InterventionDecision.RESPOND}),
        frozenset({CognitionReasonCode.SIMPLE_REQUEST}),
    ),
    DispositionScenario(
        "F04",
        DispositionScenarioFamily.FAST,
        _direct(case=DispositionCase.CHEAP_REVERSIBLE_AMBIGUITY, cheap_reversible_assumption=True),
        DispositionRoute.FAST,
        frozenset({InterventionDecision.RESPOND}),
        frozenset({CognitionReasonCode.REVERSIBLE_ASSUMPTION}),
        expected_question_policy="avoid",
    ),
    DispositionScenario(
        "F05",
        DispositionScenarioFamily.FAST,
        _direct(case=DispositionCase.ORDINARY_ACKNOWLEDGMENT),
        DispositionRoute.FAST,
        frozenset({InterventionDecision.RESPOND}),
        expected_aim="acknowledge",
    ),
    DispositionScenario(
        "F06",
        DispositionScenarioFamily.FAST,
        _direct(case=DispositionCase.CLEAR_PLAYFUL_TURN),
        DispositionRoute.FAST,
        frozenset({InterventionDecision.RESPOND}),
        frozenset({CognitionReasonCode.PLAYFUL_CONTEXT}),
        expected_stance="playful",
        expected_desired_length="low",
        expected_humor_allowed=True,
    ),
    DispositionScenario(
        "D01",
        DispositionScenarioFamily.DELIBERATE,
        _direct(important_contradiction=True),
        DispositionRoute.DELIBERATE,
        frozenset({InterventionDecision.RESPOND}),
        frozenset({CognitionReasonCode.CONTRADICTION}),
    ),
    DispositionScenario(
        "D02",
        DispositionScenarioFamily.DELIBERATE,
        _direct(evidence_update=True),
        DispositionRoute.DELIBERATE,
        frozenset({InterventionDecision.RESPOND}),
        frozenset({CognitionReasonCode.EVIDENCE_UPDATE}),
    ),
    DispositionScenario(
        "D03",
        DispositionScenarioFamily.DELIBERATE,
        _direct(material_ambiguity=True),
        DispositionRoute.DELIBERATE,
        frozenset({InterventionDecision.RESPOND}),
        frozenset({CognitionReasonCode.AMBIGUITY_MATERIAL}),
    ),
    DispositionScenario(
        "D04",
        DispositionScenarioFamily.DELIBERATE,
        _direct(vulnerable_context=True),
        DispositionRoute.DELIBERATE,
        frozenset({InterventionDecision.RESPOND}),
        frozenset({CognitionReasonCode.VULNERABILITY}),
    ),
    DispositionScenario(
        "D05",
        DispositionScenarioFamily.DELIBERATE,
        _direct(high_social_stakes=True),
        DispositionRoute.DELIBERATE,
        frozenset({InterventionDecision.RESPOND}),
        frozenset({CognitionReasonCode.HIGH_SOCIAL_STAKES}),
    ),
    DispositionScenario(
        "D06",
        DispositionScenarioFamily.DELIBERATE,
        _direct(multiple_plausible_moves=True),
        DispositionRoute.DELIBERATE,
        frozenset({InterventionDecision.RESPOND}),
        frozenset({CognitionReasonCode.MULTIPLE_PLAUSIBLE_MOVES}),
    ),
    DispositionScenario(
        "D07",
        DispositionScenarioFamily.DELIBERATE,
        _ambient(intervention_uncertain=True),
        DispositionRoute.DELIBERATE,
        frozenset({InterventionDecision.NONE, InterventionDecision.INTERJECT}),
        frozenset({CognitionReasonCode.INTERVENTION_UNCERTAIN}),
    ),
)


def _deliberative_reference(scenario: DispositionScenario) -> CognitionPolicyDecision:
    reasons = tuple(scenario.required_reason_codes)
    if scenario.context.source is DispositionSource.AMBIENT:
        return CognitionPolicyDecision(
            attention=AttentionDecision.THINK,
            intervention=InterventionDecision.INTERJECT,
            working_state=WorkingState("add a bounded useful point"),
            response_disposition=ResponseDisposition(aim="answer", desired_length="low"),
            reason_codes=reasons,
        )
    return CognitionPolicyDecision(
        attention=AttentionDecision.THINK,
        intervention=InterventionDecision.RESPOND,
        working_state=WorkingState("respond to the materially important context"),
        response_disposition=ResponseDisposition(),
        reason_codes=reasons,
    )


def _reference_decisions() -> dict[str, CognitionPolicyDecision]:
    fast = DeterministicFastDispositionPolicy()
    return {
        scenario.id: (
            fast.decide(scenario.context)
            if scenario.family is DispositionScenarioFamily.FAST
            else _deliberative_reference(scenario)
        )
        for scenario in COG_V1_C_SCENARIOS
    }


COG_V1_C_REFERENCE_DECISIONS: Mapping[str, CognitionPolicyDecision] = MappingProxyType(
    _reference_decisions()
)


def evaluate_disposition_scenario(
    scenario: DispositionScenario,
    resolution: DispositionResolution,
) -> DispositionEvalResult:
    """Evaluate one resolution using policy fields rather than prose."""

    if type(scenario) is not DispositionScenario:
        raise TypeError("scenario must be a DispositionScenario")
    if type(resolution) is not DispositionResolution:
        raise TypeError("resolution must be a DispositionResolution")
    decision = resolution.decision
    disposition = decision.response_disposition
    state = decision.working_state
    checks: dict[str, bool] = {
        "route": resolution.route is scenario.expected_route,
        "intervention": decision.intervention in scenario.expected_interventions,
        "required_reasons": scenario.required_reason_codes.issubset(set(decision.reason_codes)),
    }
    if scenario.expected_aim is not None:
        checks["aim"] = disposition is not None and disposition.aim == scenario.expected_aim
    if scenario.expected_desired_length is not None:
        checks["desired_length"] = (
            disposition is not None
            and disposition.desired_length == scenario.expected_desired_length
        )
    if scenario.expected_question_policy is not None:
        checks["question_policy"] = (
            disposition is not None
            and disposition.question_policy == scenario.expected_question_policy
        )
    if scenario.expected_stance is not None:
        checks["stance"] = state is not None and state.stance == scenario.expected_stance
    if scenario.expected_engagement is not None:
        checks["engagement"] = (
            state is not None and state.engagement == scenario.expected_engagement
        )
    if scenario.expected_humor_allowed is not None:
        checks["humor_allowed"] = (
            disposition is not None and disposition.humor_allowed == scenario.expected_humor_allowed
        )
    failures = tuple(name for name, passed in checks.items() if not passed)
    return DispositionEvalResult(
        scenario_id=scenario.id,
        passed=not failures,
        checks=checks,
        failures=failures,
    )


def evaluate_disposition_corpus(
    resolutions: Mapping[str, DispositionResolution],
) -> tuple[DispositionEvalResult, ...]:
    """Evaluate exactly one resolution for each fixed COG-V1-C scenario."""

    expected_ids = {scenario.id for scenario in COG_V1_C_SCENARIOS}
    if set(resolutions) != expected_ids:
        raise ValueError("resolutions must contain every COG-V1-C scenario exactly once")
    return tuple(
        evaluate_disposition_scenario(scenario, resolutions[scenario.id])
        for scenario in COG_V1_C_SCENARIOS
    )


COG_V1_C_COUNTERFACTUAL_PAIRS: tuple[DispositionCounterfactualPair, ...] = (
    DispositionCounterfactualPair(
        "PAIR1",
        _direct(),
        _ambient(),
    ),
    DispositionCounterfactualPair(
        "PAIR2",
        _direct(case=DispositionCase.CHEAP_REVERSIBLE_AMBIGUITY, cheap_reversible_assumption=True),
        _direct(material_ambiguity=True),
    ),
    DispositionCounterfactualPair(
        "PAIR3",
        _direct(),
        _direct(important_contradiction=True),
    ),
    DispositionCounterfactualPair(
        "PAIR4",
        _direct(),
        _direct(case=DispositionCase.TAILORING_DISCUSSION),
    ),
)


COG_V1_C_TRAJECTORIES: tuple[DispositionTrajectoryFixture, ...] = (
    DispositionTrajectoryFixture(
        "T01",
        (
            _direct(),
            _direct(),
            _direct(evidence_update=True),
        ),
        DispositionRoute.DELIBERATE,
        InterventionDecision.RESPOND,
        CognitionReasonCode.EVIDENCE_UPDATE,
    ),
    DispositionTrajectoryFixture(
        "T02",
        (
            _direct(material_ambiguity=True),
            _direct(material_ambiguity=True),
            _direct(),
        ),
        DispositionRoute.FAST,
        InterventionDecision.RESPOND,
        expected_final_question_policy="avoid",
    ),
    DispositionTrajectoryFixture(
        "T03",
        (
            _ambient(intervention_uncertain=True),
            _ambient(intervention_uncertain=True),
            _ambient(),
        ),
        DispositionRoute.FAST,
        InterventionDecision.NONE,
    ),
)
