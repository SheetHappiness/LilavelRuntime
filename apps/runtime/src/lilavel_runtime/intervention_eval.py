"""Human-authored, silence-first COG-V1-E1 intervention evaluation.

The corpus evaluates typed candidates and trusted current-state contexts.  It
does not call a provider, judge prose, inspect payload text, or use a scalar
initiative score.  Direct USER work is represented only to prove that it is
excluded from this ambient evaluator.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, cast

from lilavel_core import CognitionReasonCode, InterventionDecision

from .intervention import (
    ActivityState,
    DeterministicInterventionPolicy,
    FloorState,
    FreshnessBucket,
    FreshnessClass,
    HandlingState,
    InterventionBudgetState,
    InterventionCandidate,
    InterventionValue,
    RecentSpeechState,
    SocialPermissionContext,
    SocialPermissionResult,
    SocialSensitivity,
    SpeakingSurfaceState,
)
from .semantic_actor import SemanticPriority, SemanticSourceKind


class _InterventionEvaluator(Protocol):
    def evaluate(
        self,
        candidate: InterventionCandidate,
        context: SocialPermissionContext,
    ) -> SocialPermissionResult: ...


class InterventionScenarioFamily(StrEnum):
    """Stable review categories for the E1 corpus."""

    THINK_NOT_SPEAK = "think_not_speak"
    RESPONSE_OBLIGATION = "response_obligation"
    DIRECT_USER_EXCLUDED = "direct_user_excluded"
    MATERIAL_CONTRADICTION = "material_contradiction"
    HARMLESS_DIFFERENCE = "harmless_difference"
    FORGOTTEN_CONSTRAINT = "forgotten_constraint"
    HANDLED_CONSTRAINT = "handled_constraint"
    DISCUSSION_STUCK = "discussion_stuck"
    GROUP_PROGRESS = "group_progress"
    UNIQUE_INFORMATION = "unique_information"
    INTEREST_ONLY = "interest_only"
    WITTY_THOUGHT = "witty_thought"
    VULNERABLE_MOMENT = "vulnerable_moment"
    RECENT_SPEECH = "recent_speech"
    FRESHNESS = "freshness"
    NO_SPEAKING_SURFACE = "no_speaking_surface"
    FLOOR_BUSY = "floor_busy"
    RESPONSE_OVER_INTERJECT = "response_over_interject"
    INTERVENTION_BUDGET = "intervention_budget"
    CRITICAL_BACKOFF_UNSUPPORTED = "critical_backoff_unsupported"
    DELAYED_CONTINUITY = "delayed_continuity"
    LEXICAL_HANDLING = "lexical_handling"
    INTEREST_KEYWORD_TRAP = "interest_keyword_trap"


@dataclass(frozen=True, slots=True)
class InterventionScenario:
    """One deterministic scenario with no external payload or model output."""

    id: str
    family: InterventionScenarioFamily
    description: str
    candidate: InterventionCandidate
    context: SocialPermissionContext
    expected_decision: InterventionDecision
    required_reason_codes: tuple[CognitionReasonCode, ...] = ()
    forbidden_reason_codes: tuple[CognitionReasonCode, ...] = ()
    hard_social_denial: bool = False
    excluded_from_e1: bool = False
    counterfactual_group: str | None = None

    def __post_init__(self) -> None:
        if type(self.id) is not str or not self.id.strip():
            raise ValueError("scenario id must be non-empty")
        if type(self.family) is not InterventionScenarioFamily:
            raise TypeError("scenario family must be an InterventionScenarioFamily")
        if type(self.description) is not str or not self.description.strip():
            raise ValueError("scenario description must be non-empty")
        if type(self.candidate) is not InterventionCandidate:
            raise TypeError("scenario candidate must be an InterventionCandidate")
        if type(self.context) is not SocialPermissionContext:
            raise TypeError("scenario context must be a SocialPermissionContext")
        if type(self.expected_decision) is not InterventionDecision:
            raise TypeError("scenario expected_decision must be an InterventionDecision")
        for name in ("required_reason_codes", "forbidden_reason_codes"):
            values = tuple(getattr(self, name))
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must be unique")
            if not all(type(code) is CognitionReasonCode for code in values):
                raise TypeError(f"{name} must contain CognitionReasonCode values")
            if len(values) > 8:
                raise ValueError(f"{name} exceeds the reason-code bound")
            object.__setattr__(self, name, values)
        if set(self.required_reason_codes).intersection(self.forbidden_reason_codes):
            raise ValueError("a scenario reason code cannot be both required and forbidden")
        if type(self.hard_social_denial) is not bool:
            raise TypeError("hard_social_denial must be a bool")
        if type(self.excluded_from_e1) is not bool:
            raise TypeError("excluded_from_e1 must be a bool")
        if self.counterfactual_group is not None and not self.counterfactual_group.strip():
            raise ValueError("counterfactual_group must be non-empty when supplied")
        if self.excluded_from_e1:
            if self.context.priority is not SemanticPriority.USER:
                raise ValueError("excluded E1 scenarios must be direct USER priority")
        elif self.context.priority is SemanticPriority.USER:
            raise ValueError("ambient scenarios cannot use USER priority")


@dataclass(frozen=True, slots=True)
class InterventionScenarioResult:
    """Bounded evidence for one corpus evaluation."""

    scenario_id: str
    predicted_decision: InterventionDecision | None
    passed: bool
    checks: Mapping[str, bool]
    failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))
        object.__setattr__(self, "failures", tuple(self.failures))


@dataclass(frozen=True, slots=True)
class InterventionMetrics:
    """Intervention-specific metrics; raw accuracy is intentionally absent."""

    scenario_count: int
    evaluated_scenario_count: int
    excluded_scenario_count: int
    confusion_matrix: Mapping[str, Mapping[str, int]]
    interject_precision: float
    interject_recall: float
    respond_recall: float
    false_interject_count: int
    false_interject_rate: float
    silence_preservation_rate: float
    hard_social_violation_count: int
    intervention_rate: float

    def __post_init__(self) -> None:
        for name in (
            "scenario_count",
            "evaluated_scenario_count",
            "excluded_scenario_count",
            "false_interject_count",
            "hard_social_violation_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in (
            "interject_precision",
            "interject_recall",
            "respond_recall",
            "false_interject_rate",
            "silence_preservation_rate",
            "intervention_rate",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between zero and one")
        object.__setattr__(
            self,
            "confusion_matrix",
            MappingProxyType(
                {
                    actual: MappingProxyType(dict(predicted))
                    for actual, predicted in self.confusion_matrix.items()
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class InterventionBenchmarkResult:
    """Full deterministic E1 report for one policy or baseline."""

    policy_id: str
    scenario_results: tuple[InterventionScenarioResult, ...]
    metrics: InterventionMetrics

    def __post_init__(self) -> None:
        if type(self.policy_id) is not str or not self.policy_id.strip():
            raise ValueError("policy_id must be non-empty")
        object.__setattr__(self, "scenario_results", tuple(self.scenario_results))


class AlwaysNoneInterventionPolicy:
    """Intentional silence baseline for proactive-system comparison."""

    policy_id = "always_none"

    def evaluate(
        self,
        candidate: InterventionCandidate,
        context: SocialPermissionContext,
    ) -> SocialPermissionResult:
        if type(candidate) is not InterventionCandidate:
            raise TypeError("candidate must be an InterventionCandidate")
        if type(context) is not SocialPermissionContext:
            raise TypeError("context must be a SocialPermissionContext")
        reasons = _baseline_reasons(candidate, context)
        return SocialPermissionResult(
            candidate_decision=candidate.decision,
            decision=InterventionDecision.NONE,
            permitted=False,
            reason_codes=reasons,
        )

    def revalidate(
        self,
        candidate: InterventionCandidate,
        context: SocialPermissionContext,
    ) -> SocialPermissionResult:
        return self.evaluate(candidate, context)

    def decide(
        self,
        candidate: InterventionCandidate,
        context: SocialPermissionContext,
    ) -> InterventionDecision:
        return self.evaluate(candidate, context).decision


def evaluate_intervention_scenario(
    policy: object,
    scenario: InterventionScenario,
) -> InterventionScenarioResult:
    """Evaluate one scenario without invoking any model or external effect."""

    if not callable(getattr(policy, "evaluate", None)):
        raise TypeError("policy must provide evaluate")
    if type(scenario) is not InterventionScenario:
        raise TypeError("scenario must be an InterventionScenario")
    if scenario.excluded_from_e1:
        return InterventionScenarioResult(
            scenario.id,
            None,
            True,
            {"excluded_from_e1": True},
        )

    evaluator = cast(_InterventionEvaluator, policy)
    result = evaluator.evaluate(scenario.candidate, scenario.context)
    if type(result) is not SocialPermissionResult:
        raise TypeError("policy returned an invalid SocialPermissionResult")
    checks: dict[str, bool] = {
        "decision_allowed": result.decision is scenario.expected_decision,
        "required_reasons_present": set(scenario.required_reason_codes).issubset(
            set(result.reason_codes)
        ),
        "forbidden_reasons_absent": not set(scenario.forbidden_reason_codes).intersection(
            result.reason_codes
        ),
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    return InterventionScenarioResult(
        scenario.id,
        result.decision,
        not failures,
        checks,
        failures,
    )


def evaluate_intervention_policy(
    policy: object | None = None,
    *,
    scenarios: Sequence[InterventionScenario] = (),
) -> InterventionBenchmarkResult:
    """Evaluate one deterministic policy over the fixed E1 corpus."""

    selected_policy = policy or DeterministicInterventionPolicy()
    scenario_values = tuple(scenarios or COG_V1_E1_SCENARIOS)
    policy_id = getattr(selected_policy, "policy_id", type(selected_policy).__name__)
    if type(policy_id) is not str or not policy_id.strip():
        raise ValueError("policy_id must be non-empty")
    results = tuple(
        evaluate_intervention_scenario(selected_policy, scenario) for scenario in scenario_values
    )
    metrics = _metrics(scenario_values, results)
    return InterventionBenchmarkResult(policy_id, results, metrics)


def evaluate_always_none_baseline(
    *, scenarios: Sequence[InterventionScenario] = ()
) -> InterventionBenchmarkResult:
    """Run the explicit ALWAYS_NONE silence baseline."""

    return evaluate_intervention_policy(AlwaysNoneInterventionPolicy(), scenarios=scenarios)


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
    reasons: tuple[CognitionReasonCode, ...],
    *,
    value: InterventionValue | None = None,
) -> InterventionCandidate:
    return InterventionCandidate(decision, reasons, value)


_NONE = _candidate(
    InterventionDecision.NONE,
    (CognitionReasonCode.AMBIENT_CONTEXT,),
)
_RESPOND = _candidate(
    InterventionDecision.RESPOND,
    (CognitionReasonCode.SOCIAL_FOLLOWUP,),
)
_INTERJECT_CONTRADICTION = _candidate(
    InterventionDecision.INTERJECT,
    (CognitionReasonCode.CONTRADICTION, CognitionReasonCode.EVIDENCE_UPDATE),
)
_INTERJECT_FORGOTTEN = _candidate(
    InterventionDecision.INTERJECT,
    (CognitionReasonCode.CONSTRAINT_FORGOTTEN, CognitionReasonCode.HIGH_RELEVANCE),
)
_INTERJECT_STUCK = _candidate(
    InterventionDecision.INTERJECT,
    (CognitionReasonCode.DISCUSSION_STUCK, CognitionReasonCode.UNIQUE_INFORMATION),
)
_INTERJECT_UNIQUE = _candidate(
    InterventionDecision.INTERJECT,
    (CognitionReasonCode.UNIQUE_INFORMATION, CognitionReasonCode.HIGH_RELEVANCE),
)
_INTERJECT_INTEREST = _candidate(
    InterventionDecision.INTERJECT,
    (CognitionReasonCode.INTEREST_AFFINITY,),
)
_INTERJECT_JOKE = _candidate(
    InterventionDecision.INTERJECT,
    (CognitionReasonCode.PLAYFUL_CONTEXT,),
)
_INTERJECT_HARMLESS = _candidate(
    InterventionDecision.INTERJECT,
    (CognitionReasonCode.HIGH_RELEVANCE,),
)
_INTERJECT_PROGRESS = _candidate(
    InterventionDecision.INTERJECT,
    (CognitionReasonCode.NO_NEW_VALUE,),
)


COG_V1_E1_SCENARIOS: tuple[InterventionScenario, ...] = (
    InterventionScenario(
        "cog-v1-e1-01",
        InterventionScenarioFamily.THINK_NOT_SPEAK,
        "Ambient observation merits THINK but has no speaking value.",
        _NONE,
        _context(),
        InterventionDecision.NONE,
        (CognitionReasonCode.AMBIENT_CONTEXT,),
    ),
    InterventionScenario(
        "cog-v1-e1-02",
        InterventionScenarioFamily.RESPONSE_OBLIGATION,
        "A trusted continuity obligation requests a follow-up.",
        _RESPOND,
        _context(
            freshness_class=FreshnessClass.CONTINUITY,
            response_obligation=True,
            continuity_current=True,
        ),
        InterventionDecision.RESPOND,
        (CognitionReasonCode.RESPONSE_OBLIGATION,),
        counterfactual_group="response-obligation",
    ),
    InterventionScenario(
        "cog-v1-e1-03",
        InterventionScenarioFamily.DIRECT_USER_EXCLUDED,
        "Direct USER remains fixed THINK plus RESPOND in the conversation route.",
        _RESPOND,
        _context(priority=SemanticPriority.USER, source_kind=SemanticSourceKind.USER),
        InterventionDecision.RESPOND,
        excluded_from_e1=True,
    ),
    InterventionScenario(
        "cog-v1-e1-04",
        InterventionScenarioFamily.MATERIAL_CONTRADICTION,
        "A contradiction materially changes the group's decision.",
        _INTERJECT_CONTRADICTION,
        _context(),
        InterventionDecision.INTERJECT,
        (CognitionReasonCode.CONTRADICTION,),
        counterfactual_group="materiality",
    ),
    InterventionScenario(
        "cog-v1-e1-05",
        InterventionScenarioFamily.HARMLESS_DIFFERENCE,
        "The same topic contains only a harmless difference with no material conflict.",
        _INTERJECT_HARMLESS,
        _context(),
        InterventionDecision.NONE,
        (CognitionReasonCode.INTERRUPTION_COST_HIGH,),
        counterfactual_group="materiality",
    ),
    InterventionScenario(
        "cog-v1-e1-06",
        InterventionScenarioFamily.FORGOTTEN_CONSTRAINT,
        "An important unresolved constraint was forgotten.",
        _INTERJECT_FORGOTTEN,
        _context(),
        InterventionDecision.INTERJECT,
        (CognitionReasonCode.CONSTRAINT_FORGOTTEN,),
        counterfactual_group="handled-state",
    ),
    InterventionScenario(
        "cog-v1-e1-07",
        InterventionScenarioFamily.HANDLED_CONSTRAINT,
        "The same important constraint has already been raised and handled.",
        _INTERJECT_FORGOTTEN,
        _context(handling=HandlingState.HANDLED),
        InterventionDecision.NONE,
        (CognitionReasonCode.ALREADY_HANDLED,),
        hard_social_denial=True,
        counterfactual_group="handled-state",
    ),
    InterventionScenario(
        "cog-v1-e1-08",
        InterventionScenarioFamily.DISCUSSION_STUCK,
        "A discussion loop is stuck and the new contribution can resolve it.",
        _INTERJECT_STUCK,
        _context(),
        InterventionDecision.INTERJECT,
        (CognitionReasonCode.DISCUSSION_STUCK,),
    ),
    InterventionScenario(
        "cog-v1-e1-09",
        InterventionScenarioFamily.GROUP_PROGRESS,
        "The group is making progress without needing Lilavel's entry.",
        _INTERJECT_PROGRESS,
        _context(),
        InterventionDecision.NONE,
        (CognitionReasonCode.INTERRUPTION_COST_HIGH,),
    ),
    InterventionScenario(
        "cog-v1-e1-10",
        InterventionScenarioFamily.UNIQUE_INFORMATION,
        "Lilavel has unique relevant information unavailable to the others.",
        _INTERJECT_UNIQUE,
        _context(),
        InterventionDecision.INTERJECT,
        (CognitionReasonCode.UNIQUE_INFORMATION,),
    ),
    InterventionScenario(
        "cog-v1-e1-11",
        InterventionScenarioFamily.INTEREST_ONLY,
        "The topic is interesting to Lilavel but offers no material contribution.",
        _INTERJECT_INTEREST,
        _context(),
        InterventionDecision.NONE,
        (CognitionReasonCode.INTERRUPTION_COST_HIGH,),
    ),
    InterventionScenario(
        "cog-v1-e1-12",
        InterventionScenarioFamily.WITTY_THOUGHT,
        "A possible joke is only a witty thought, without an obligation.",
        _INTERJECT_JOKE,
        _context(),
        InterventionDecision.NONE,
        (CognitionReasonCode.INTERRUPTION_COST_HIGH,),
    ),
    InterventionScenario(
        "cog-v1-e1-13",
        InterventionScenarioFamily.VULNERABLE_MOMENT,
        "An unsolicited insertion would be costly in a vulnerable social moment.",
        _INTERJECT_UNIQUE,
        _context(sensitivity=SocialSensitivity.VULNERABLE),
        InterventionDecision.NONE,
        (CognitionReasonCode.VULNERABILITY,),
        hard_social_denial=True,
    ),
    InterventionScenario(
        "cog-v1-e1-14",
        InterventionScenarioFamily.RECENT_SPEECH,
        "Recent Lilavel speech suppresses an otherwise valid unsolicited entry.",
        _INTERJECT_CONTRADICTION,
        _context(recent_speech=RecentSpeechState.RECENT),
        InterventionDecision.NONE,
        (CognitionReasonCode.SOCIAL_BACKOFF,),
        hard_social_denial=True,
        counterfactual_group="recent-speech",
    ),
    InterventionScenario(
        "cog-v1-e1-15",
        InterventionScenarioFamily.RECENT_SPEECH,
        "The same unsolicited evidence is eligible when recent speech is clear.",
        _INTERJECT_CONTRADICTION,
        _context(),
        InterventionDecision.INTERJECT,
        (CognitionReasonCode.CONTRADICTION,),
        counterfactual_group="recent-speech",
    ),
    InterventionScenario(
        "cog-v1-e1-16",
        InterventionScenarioFamily.FRESHNESS,
        "A semantically valid candidate is stale at effect-time revalidation.",
        _INTERJECT_CONTRADICTION,
        _context(freshness=FreshnessBucket.STALE),
        InterventionDecision.NONE,
        (CognitionReasonCode.STALE_CONTEXT,),
        hard_social_denial=True,
        counterfactual_group="freshness",
    ),
    InterventionScenario(
        "cog-v1-e1-17",
        InterventionScenarioFamily.FRESHNESS,
        "The same candidate remains fresh and eligible.",
        _INTERJECT_CONTRADICTION,
        _context(freshness=FreshnessBucket.FRESH),
        InterventionDecision.INTERJECT,
        (CognitionReasonCode.CONTRADICTION,),
        counterfactual_group="freshness",
    ),
    InterventionScenario(
        "cog-v1-e1-18",
        InterventionScenarioFamily.NO_SPEAKING_SURFACE,
        "The environment currently exposes no allowed speaking surface.",
        _INTERJECT_CONTRADICTION,
        _context(speaking_surface=SpeakingSurfaceState.UNAVAILABLE),
        InterventionDecision.NONE,
        (CognitionReasonCode.NO_SPEAKING_SURFACE,),
        hard_social_denial=True,
    ),
    InterventionScenario(
        "cog-v1-e1-19",
        InterventionScenarioFamily.FLOOR_BUSY,
        "Trusted floor state says another speaker currently owns the floor.",
        _INTERJECT_CONTRADICTION,
        _context(floor=FloorState.BUSY),
        InterventionDecision.NONE,
        (CognitionReasonCode.FLOOR_BUSY,),
        hard_social_denial=True,
    ),
    InterventionScenario(
        "cog-v1-e1-20",
        InterventionScenarioFamily.RESPONSE_OVER_INTERJECT,
        "A trusted response remains allowed after recent Lilavel speech.",
        _RESPOND,
        _context(
            freshness_class=FreshnessClass.CONTINUITY,
            recent_speech=RecentSpeechState.RECENT,
            response_obligation=True,
            continuity_current=True,
        ),
        InterventionDecision.RESPOND,
        (CognitionReasonCode.RESPONSE_OBLIGATION,),
    ),
    InterventionScenario(
        "cog-v1-e1-21",
        InterventionScenarioFamily.INTERVENTION_BUDGET,
        "The runtime-owned unsolicited intervention budget is exhausted.",
        _INTERJECT_CONTRADICTION,
        _context(intervention_budget=InterventionBudgetState.EXHAUSTED),
        InterventionDecision.NONE,
        (CognitionReasonCode.INTERVENTION_BUDGET_EXHAUSTED,),
        hard_social_denial=True,
    ),
    InterventionScenario(
        "cog-v1-e1-22",
        InterventionScenarioFamily.CRITICAL_BACKOFF_UNSUPPORTED,
        "A critical event has no separate urgency bypass in the current E1 contract.",
        _candidate(
            InterventionDecision.INTERJECT,
            (CognitionReasonCode.CRITICAL_EVENT, CognitionReasonCode.HIGH_RELEVANCE),
        ),
        _context(recent_speech=RecentSpeechState.RECENT),
        InterventionDecision.NONE,
        (CognitionReasonCode.SOCIAL_BACKOFF,),
        hard_social_denial=True,
    ),
    InterventionScenario(
        "cog-v1-e1-23",
        InterventionScenarioFamily.DELAYED_CONTINUITY,
        "A delayed multi-turn context remains current through trusted continuity.",
        _RESPOND,
        _context(
            freshness_class=FreshnessClass.CONTINUITY,
            freshness=FreshnessBucket.AGING,
            response_obligation=True,
            continuity_current=True,
        ),
        InterventionDecision.RESPOND,
        (CognitionReasonCode.RESPONSE_OBLIGATION,),
        counterfactual_group="response-obligation",
    ),
    InterventionScenario(
        "cog-v1-e1-24",
        InterventionScenarioFamily.LEXICAL_HANDLING,
        "A familiar constraint reference is already handled.",
        _INTERJECT_FORGOTTEN,
        _context(handling=HandlingState.HANDLED),
        InterventionDecision.NONE,
        (CognitionReasonCode.ALREADY_HANDLED,),
        hard_social_denial=True,
        counterfactual_group="lexical-handling",
    ),
    InterventionScenario(
        "cog-v1-e1-25",
        InterventionScenarioFamily.LEXICAL_HANDLING,
        "A lexically similar constraint reference remains unresolved.",
        _INTERJECT_FORGOTTEN,
        _context(handling=HandlingState.UNRESOLVED),
        InterventionDecision.INTERJECT,
        (CognitionReasonCode.CONSTRAINT_FORGOTTEN,),
        counterfactual_group="lexical-handling",
    ),
    InterventionScenario(
        "cog-v1-e1-26",
        InterventionScenarioFamily.INTEREST_KEYWORD_TRAP,
        "A tailoring keyword alone does not create an initiative candidate.",
        _INTERJECT_INTEREST,
        _context(),
        InterventionDecision.NONE,
        (CognitionReasonCode.INTERRUPTION_COST_HIGH,),
    ),
    InterventionScenario(
        "cog-v1-e1-27",
        InterventionScenarioFamily.RESPONSE_OBLIGATION,
        "The same response-shaped evidence has no trusted obligation.",
        _RESPOND,
        _context(),
        InterventionDecision.NONE,
        (CognitionReasonCode.NO_RESPONSE_OBLIGATION,),
        hard_social_denial=True,
        counterfactual_group="response-obligation",
    ),
    InterventionScenario(
        "cog-v1-e1-28",
        InterventionScenarioFamily.FLOOR_BUSY,
        "A free trusted floor permits a material unsolicited contribution.",
        _INTERJECT_CONTRADICTION,
        _context(floor=FloorState.FREE),
        InterventionDecision.INTERJECT,
        (CognitionReasonCode.CONTRADICTION,),
        counterfactual_group="floor-state",
    ),
    InterventionScenario(
        "cog-v1-e1-29",
        InterventionScenarioFamily.FLOOR_BUSY,
        "Changing only the trusted floor to busy denies the same contribution.",
        _INTERJECT_CONTRADICTION,
        _context(floor=FloorState.BUSY),
        InterventionDecision.NONE,
        (CognitionReasonCode.FLOOR_BUSY,),
        hard_social_denial=True,
        counterfactual_group="floor-state",
    ),
    InterventionScenario(
        "cog-v1-e1-30",
        InterventionScenarioFamily.MATERIAL_CONTRADICTION,
        "Changing only semantic evidence from material conflict to harmless difference "
        "silences entry.",
        _INTERJECT_HARMLESS,
        _context(),
        InterventionDecision.NONE,
        (CognitionReasonCode.INTERRUPTION_COST_HIGH,),
        counterfactual_group="materiality-harmless",
    ),
)


def _baseline_reasons(
    candidate: InterventionCandidate,
    context: SocialPermissionContext,
) -> tuple[CognitionReasonCode, ...]:
    if context.speaking_surface is SpeakingSurfaceState.UNAVAILABLE:
        return (CognitionReasonCode.NO_SPEAKING_SURFACE, *candidate.reason_codes[:7])
    if context.floor is FloorState.BUSY:
        return (CognitionReasonCode.FLOOR_BUSY, *candidate.reason_codes[:7])
    if context.freshness is FreshnessBucket.STALE:
        return (CognitionReasonCode.STALE_CONTEXT, *candidate.reason_codes[:7])
    if context.intervention_budget is InterventionBudgetState.EXHAUSTED:
        return (CognitionReasonCode.INTERVENTION_BUDGET_EXHAUSTED, *candidate.reason_codes[:7])
    if candidate.decision is InterventionDecision.RESPOND and not (
        context.response_obligation or context.continuity_current
    ):
        return (CognitionReasonCode.NO_RESPONSE_OBLIGATION, *candidate.reason_codes[:7])
    if candidate.decision is InterventionDecision.INTERJECT:
        if context.recent_speech is RecentSpeechState.RECENT:
            return (CognitionReasonCode.SOCIAL_BACKOFF, *candidate.reason_codes[:7])
        if context.handling is HandlingState.HANDLED:
            return (CognitionReasonCode.ALREADY_HANDLED, *candidate.reason_codes[:7])
        if context.sensitivity is SocialSensitivity.VULNERABLE:
            return (CognitionReasonCode.VULNERABILITY, *candidate.reason_codes[:7])
        return (CognitionReasonCode.INTERRUPTION_COST_HIGH, *candidate.reason_codes[:7])
    return candidate.reason_codes


def _metrics(
    scenarios: Sequence[InterventionScenario],
    results: Sequence[InterventionScenarioResult],
) -> InterventionMetrics:
    evaluated = [
        (scenario, result)
        for scenario, result in zip(scenarios, results, strict=True)
        if not scenario.excluded_from_e1
    ]
    matrix = {
        actual.value: {predicted.value: 0 for predicted in InterventionDecision}
        for actual in InterventionDecision
    }
    for scenario, result in evaluated:
        assert result.predicted_decision is not None
        matrix[scenario.expected_decision.value][result.predicted_decision.value] += 1

    expected_interject = sum(
        scenario.expected_decision is InterventionDecision.INTERJECT for scenario, _ in evaluated
    )
    predicted_interject = sum(
        result.predicted_decision is InterventionDecision.INTERJECT for _, result in evaluated
    )
    true_interject = sum(
        scenario.expected_decision is InterventionDecision.INTERJECT
        and result.predicted_decision is InterventionDecision.INTERJECT
        for scenario, result in evaluated
    )
    expected_respond = sum(
        scenario.expected_decision is InterventionDecision.RESPOND for scenario, _ in evaluated
    )
    true_respond = sum(
        scenario.expected_decision is InterventionDecision.RESPOND
        and result.predicted_decision is InterventionDecision.RESPOND
        for scenario, result in evaluated
    )
    expected_none = sum(
        scenario.expected_decision is InterventionDecision.NONE for scenario, _ in evaluated
    )
    preserved_none = sum(
        scenario.expected_decision is InterventionDecision.NONE
        and result.predicted_decision is InterventionDecision.NONE
        for scenario, result in evaluated
    )
    false_interject = sum(
        scenario.expected_decision is not InterventionDecision.INTERJECT
        and result.predicted_decision is InterventionDecision.INTERJECT
        for scenario, result in evaluated
    )
    violations = sum(
        scenario.hard_social_denial and result.predicted_decision is not InterventionDecision.NONE
        for scenario, result in evaluated
    )
    return InterventionMetrics(
        scenario_count=len(scenarios),
        evaluated_scenario_count=len(evaluated),
        excluded_scenario_count=len(scenarios) - len(evaluated),
        confusion_matrix=matrix,
        interject_precision=(true_interject / predicted_interject if predicted_interject else 0.0),
        interject_recall=(true_interject / expected_interject if expected_interject else 0.0),
        respond_recall=(true_respond / expected_respond if expected_respond else 0.0),
        false_interject_count=false_interject,
        false_interject_rate=(
            false_interject / (len(evaluated) - expected_interject)
            if len(evaluated) > expected_interject
            else 0.0
        ),
        silence_preservation_rate=(preserved_none / expected_none if expected_none else 0.0),
        hard_social_violation_count=violations,
        intervention_rate=(
            (
                len(evaluated)
                - sum(
                    result.predicted_decision is InterventionDecision.NONE
                    for _, result in evaluated
                )
            )
            / len(evaluated)
            if evaluated
            else 0.0
        ),
    )


if len({scenario.id for scenario in COG_V1_E1_SCENARIOS}) != len(COG_V1_E1_SCENARIOS):
    raise RuntimeError("COG-V1-E1 scenario IDs must be unique")


__all__ = [
    "AlwaysNoneInterventionPolicy",
    "COG_V1_E1_SCENARIOS",
    "InterventionBenchmarkResult",
    "InterventionMetrics",
    "InterventionScenario",
    "InterventionScenarioFamily",
    "InterventionScenarioResult",
    "evaluate_always_none_baseline",
    "evaluate_intervention_policy",
    "evaluate_intervention_scenario",
]
