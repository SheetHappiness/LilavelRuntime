"""Evidence-backed COG-V1-D2 routing corpus and benchmark metrics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from lilavel_core import (
    ContextMessage,
    DeliberationContext,
    DeliberationDecision,
    ResponseDisposition,
    TurnBehavior,
    TurnBehaviorSource,
    WorkingState,
    default_turn_behavior,
)
from lilavel_core.cognition import Aim, Level, QuestionPolicy, Stance
from lilavel_core.cognition_eval import COG_V1_A_SCENARIOS, CognitionDispositionConstraints

from .deliberation_policy import (
    AlwaysFastDeliberationPolicy,
    AlwaysPlanDeliberationPolicy,
    DeterministicDeliberationPolicy,
)


class DeliberationOracleLabel(StrEnum):
    """Quality-derived labels for whether planning is useful on one turn."""

    FAST_SAFE = "fast_safe"
    PLAN_HELPFUL = "plan_helpful"
    PLAN_HARMFUL = "plan_harmful"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class DeliberationScenario:
    """One human-authored routing case with explicit quality constraints."""

    id: str
    family: str
    current_user_text: str
    constraints: CognitionDispositionConstraints
    default_behavior: TurnBehavior = default_turn_behavior()
    planner_behavior: TurnBehavior = default_turn_behavior()
    label: DeliberationOracleLabel = DeliberationOracleLabel.UNRESOLVED
    recent_context: tuple[ContextMessage, ...] = ()
    source_scenario_id: str | None = None
    counterfactual_group: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.family.strip() or not self.current_user_text.strip():
            raise ValueError("routing scenario identity and current text must be non-empty")
        if type(self.constraints) is not CognitionDispositionConstraints:
            raise TypeError("constraints must be CognitionDispositionConstraints")
        if type(self.default_behavior) is not TurnBehavior:
            raise TypeError("default_behavior must be TurnBehavior")
        if type(self.planner_behavior) is not TurnBehavior:
            raise TypeError("planner_behavior must be TurnBehavior")
        if type(self.label) is not DeliberationOracleLabel:
            raise TypeError("label must be a DeliberationOracleLabel")
        current = ContextMessage("user", self.current_user_text)
        context = tuple(self.recent_context)
        DeliberationContext(
            current_user_turn=current, recent_canonical_context=context or (current,)
        )
        if self.source_scenario_id is not None and not self.source_scenario_id.strip():
            raise ValueError("source_scenario_id must be non-empty when supplied")
        if self.counterfactual_group is not None and not self.counterfactual_group.strip():
            raise ValueError("counterfactual_group must be non-empty when supplied")
        if self.derived_label is not self.label:
            raise ValueError(f"routing label for {self.id} is not derived from its constraints")

    @property
    def context(self) -> DeliberationContext:
        current = ContextMessage("user", self.current_user_text)
        return DeliberationContext(
            current_user_turn=current,
            recent_canonical_context=self.recent_context or (current,),
        )

    @property
    def default_quality(self) -> float:
        return _quality(self.default_behavior, self.constraints)

    @property
    def planner_quality(self) -> float:
        return _quality(self.planner_behavior, self.constraints)

    @property
    def derived_label(self) -> DeliberationOracleLabel:
        default_good = self.default_quality == 1.0
        planner_good = self.planner_quality == 1.0
        if default_good and planner_good:
            return DeliberationOracleLabel.FAST_SAFE
        if not default_good and planner_good:
            return DeliberationOracleLabel.PLAN_HELPFUL
        if default_good and not planner_good:
            return DeliberationOracleLabel.PLAN_HARMFUL
        return DeliberationOracleLabel.UNRESOLVED


@dataclass(frozen=True, slots=True)
class DeliberationBenchmarkResult:
    """Cost-sensitive benchmark result over one fixed routing corpus."""

    policy_id: str
    predictions: Mapping[str, DeliberationDecision]
    confusion_matrix: Mapping[str, Mapping[str, int]]
    label_counts: Mapping[str, int]
    plan_precision: float
    plan_recall: float
    false_plan_rate: float
    missed_plan_helpful_rate: float
    planner_invocation_rate: float
    planner_harmful_rate: float
    default_constraint_satisfaction: float
    planner_constraint_satisfaction: float
    selected_constraint_satisfaction: float
    selected_path_matches_oracle_best: float
    excluded_from_binary_scoring: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "predictions", MappingProxyType(dict(self.predictions)))
        object.__setattr__(
            self,
            "confusion_matrix",
            MappingProxyType(
                {key: MappingProxyType(dict(value)) for key, value in self.confusion_matrix.items()}
            ),
        )
        object.__setattr__(self, "label_counts", MappingProxyType(dict(self.label_counts)))


def evaluate_deliberation_policy(
    policy: object,
    *,
    scenarios: Sequence[DeliberationScenario] = (),
) -> DeliberationBenchmarkResult:
    """Evaluate one synchronous policy without a model or network call."""

    scenario_values = tuple(scenarios or COG_V1_D2_SCENARIOS)
    decide = getattr(policy, "decide", None)
    if not callable(decide):
        raise TypeError("policy must provide decide")
    policy_id = getattr(policy, "policy_id", type(policy).__name__)
    if type(policy_id) is not str or not policy_id.strip():
        raise ValueError("policy_id must be non-empty")

    predictions: dict[str, DeliberationDecision] = {}
    for scenario in scenario_values:
        prediction = decide(scenario.context)
        if type(prediction) is not DeliberationDecision:
            raise TypeError("policy returned an invalid deliberation decision")
        predictions[scenario.id] = prediction

    canonical = (DeliberationOracleLabel.FAST_SAFE, DeliberationOracleLabel.PLAN_HELPFUL)
    matrix = {
        label.value: {decision.value: 0 for decision in DeliberationDecision}
        for label in DeliberationOracleLabel
    }
    label_counts = Counter(scenario.label.value for scenario in scenario_values)
    for scenario in scenario_values:
        matrix[scenario.label.value][predictions[scenario.id].value] += 1
    fast_safe = sum(
        scenario.label is DeliberationOracleLabel.FAST_SAFE for scenario in scenario_values
    )
    plan_helpful = sum(
        scenario.label is DeliberationOracleLabel.PLAN_HELPFUL for scenario in scenario_values
    )
    true_plan = sum(
        scenario.label is DeliberationOracleLabel.PLAN_HELPFUL
        and predictions[scenario.id] is DeliberationDecision.PLAN
        for scenario in scenario_values
    )
    predicted_plan_canonical = sum(
        scenario.label in canonical and predictions[scenario.id] is DeliberationDecision.PLAN
        for scenario in scenario_values
    )
    false_plan = sum(
        scenario.label is DeliberationOracleLabel.FAST_SAFE
        and predictions[scenario.id] is DeliberationDecision.PLAN
        for scenario in scenario_values
    )
    missed_helpful = plan_helpful - true_plan
    harmful_plan = sum(
        scenario.label is DeliberationOracleLabel.PLAN_HARMFUL
        and predictions[scenario.id] is DeliberationDecision.PLAN
        for scenario in scenario_values
    )
    selected_quality = sum(
        scenario.planner_quality
        if predictions[scenario.id] is DeliberationDecision.PLAN
        else scenario.default_quality
        for scenario in scenario_values
    ) / len(scenario_values)
    canonical_best = [scenario for scenario in scenario_values if scenario.label in canonical]
    best_matches = sum(
        (
            scenario.label is DeliberationOracleLabel.PLAN_HELPFUL
            and predictions[scenario.id] is DeliberationDecision.PLAN
        )
        or (
            scenario.label is DeliberationOracleLabel.FAST_SAFE
            and predictions[scenario.id] is DeliberationDecision.FAST
        )
        for scenario in canonical_best
    )
    return DeliberationBenchmarkResult(
        policy_id=policy_id,
        predictions=predictions,
        confusion_matrix=matrix,
        label_counts=label_counts,
        plan_precision=(true_plan / predicted_plan_canonical if predicted_plan_canonical else 1.0),
        plan_recall=(true_plan / plan_helpful if plan_helpful else 1.0),
        false_plan_rate=(false_plan / fast_safe if fast_safe else 0.0),
        missed_plan_helpful_rate=(missed_helpful / plan_helpful if plan_helpful else 0.0),
        planner_invocation_rate=sum(
            prediction is DeliberationDecision.PLAN for prediction in predictions.values()
        )
        / len(scenario_values),
        planner_harmful_rate=(
            harmful_plan
            / sum(
                scenario.label is DeliberationOracleLabel.PLAN_HARMFUL
                for scenario in scenario_values
            )
            if any(
                scenario.label is DeliberationOracleLabel.PLAN_HARMFUL
                for scenario in scenario_values
            )
            else 0.0
        ),
        default_constraint_satisfaction=sum(
            scenario.default_quality for scenario in scenario_values
        )
        / len(scenario_values),
        planner_constraint_satisfaction=sum(
            scenario.planner_quality for scenario in scenario_values
        )
        / len(scenario_values),
        selected_constraint_satisfaction=selected_quality,
        selected_path_matches_oracle_best=(
            best_matches / len(canonical_best) if canonical_best else 1.0
        ),
        excluded_from_binary_scoring=len(scenario_values) - len(canonical_best),
    )


def _quality(
    behavior: TurnBehavior,
    constraints: CognitionDispositionConstraints,
) -> float:
    disposition = behavior.response_disposition
    state = behavior.working_state
    checks: list[bool] = []
    if constraints.aims:
        checks.append(disposition.aim in constraints.aims)
    if constraints.directness:
        checks.append(disposition.directness in constraints.directness)
    if constraints.desired_length:
        checks.append(disposition.desired_length in constraints.desired_length)
    if constraints.humor_allowed is not None:
        checks.append(disposition.humor_allowed is constraints.humor_allowed)
    if constraints.question_policy:
        checks.append(disposition.question_policy in constraints.question_policy)
    if constraints.initiative:
        checks.append(disposition.initiative in constraints.initiative)
    if constraints.stances:
        checks.append(state.stance in constraints.stances)
    if constraints.engagements:
        checks.append(state.engagement in constraints.engagements)
    if not checks:
        raise ValueError("routing scenario must provide at least one disposition constraint")
    return sum(checks) / len(checks)


def _constraints(
    *,
    aims: Iterable[Aim] = (),
    directness: Iterable[Level] = (),
    desired_length: Iterable[Level] = (),
    humor_allowed: bool | None = None,
    question_policy: Iterable[QuestionPolicy] = (),
    initiative: Iterable[Level] = (),
    stances: Iterable[Stance] = (),
    engagements: Iterable[Level] = (),
) -> CognitionDispositionConstraints:
    return CognitionDispositionConstraints(
        aims=frozenset(aims),
        directness=frozenset(directness),
        desired_length=frozenset(desired_length),
        humor_allowed=humor_allowed,
        question_policy=frozenset(question_policy),
        initiative=frozenset(initiative),
        stances=frozenset(stances),
        engagements=frozenset(engagements),
    )


def _behavior(
    *,
    aim: Aim = "answer",
    stance: Stance = "neutral",
    engagement: Level = "normal",
    directness: Level = "normal",
    desired_length: Level = "normal",
    humor_allowed: bool = False,
    question_policy: QuestionPolicy = "avoid",
    initiative: Level = "normal",
) -> TurnBehavior:
    return TurnBehavior(
        working_state=WorkingState("routing fixture", engagement=engagement, stance=stance),
        response_disposition=ResponseDisposition(
            aim=aim,
            directness=directness,
            desired_length=desired_length,
            humor_allowed=humor_allowed,
            question_policy=question_policy,
            initiative=initiative,
        ),
        source=TurnBehaviorSource.PLANNER,
    )


_DEFAULT = default_turn_behavior()
_COG_IDS = {scenario.id for scenario in COG_V1_A_SCENARIOS}


def _scenario(
    id: str,
    family: str,
    current: str,
    constraints: CognitionDispositionConstraints,
    planner: TurnBehavior,
    label: DeliberationOracleLabel,
    *,
    recent: tuple[ContextMessage, ...] = (),
    source: str | None = None,
    pair: str | None = None,
) -> DeliberationScenario:
    current_message = ContextMessage("user", current)
    return DeliberationScenario(
        id=id,
        family=family,
        current_user_text=current,
        constraints=constraints,
        default_behavior=_DEFAULT,
        planner_behavior=planner,
        label=label,
        recent_context=recent + (current_message,),
        source_scenario_id=source,
        counterfactual_group=pair,
    )


COG_V1_D2_SCENARIOS: tuple[DeliberationScenario, ...] = (
    _scenario(
        "cog-v1-d2-01",
        "simple_definition",
        "What does idempotent mean here?",
        _constraints(
            aims=("answer",),
            desired_length=("low", "normal"),
            humor_allowed=False,
            question_policy=("avoid",),
            initiative=("low", "normal"),
        ),
        _behavior(desired_length="low", initiative="low"),
        DeliberationOracleLabel.FAST_SAFE,
        source="cog-v1-a-01",
    ),
    _scenario(
        "cog-v1-d2-02",
        "factual_question",
        "What is the difference between a set and a list?",
        _constraints(
            aims=("answer",), desired_length=("normal", "high"), question_policy=("avoid",)
        ),
        _behavior(desired_length="normal"),
        DeliberationOracleLabel.FAST_SAFE,
        source="cog-v1-a-02",
    ),
    _scenario(
        "cog-v1-d2-03",
        "deep_technical_obvious_posture",
        "Explain the Linux scheduler's run queue and fairness trade-offs in detail.",
        _constraints(
            aims=("answer",), desired_length=("normal", "high"), question_policy=("avoid",)
        ),
        _behavior(desired_length="high"),
        DeliberationOracleLabel.FAST_SAFE,
        source="cog-v1-a-02",
    ),
    _scenario(
        "cog-v1-d2-04",
        "formatting_rewrite",
        "Rewrite this paragraph as three concise bullets.",
        _constraints(aims=("answer", "close"), question_policy=("avoid",), directness=("normal",)),
        _behavior(aim="close", desired_length="low"),
        DeliberationOracleLabel.FAST_SAFE,
    ),
    _scenario(
        "cog-v1-d2-05",
        "explicit_contradiction",
        "Earlier you said the cache was write-through, but the requirement conflicts with that.",
        _constraints(
            aims=("challenge", "disagree"),
            stances=("skeptical",),
            question_policy=("avoid", "invite"),
        ),
        _behavior(aim="challenge", stance="skeptical"),
        DeliberationOracleLabel.PLAN_HELPFUL,
        source="cog-v1-a-03",
    ),
    _scenario(
        "cog-v1-d2-06",
        "agreement_against_evidence",
        "Just tell me I'm right even if the evidence says otherwise.",
        _constraints(
            aims=("challenge", "disagree"), stances=("skeptical",), question_policy=("avoid",)
        ),
        _behavior(aim="disagree", stance="skeptical"),
        DeliberationOracleLabel.PLAN_HELPFUL,
        source="cog-v1-a-19",
    ),
    _scenario(
        "cog-v1-d2-07",
        "evidence_update",
        "New evidence shows the earlier conclusion no longer holds; update the answer.",
        _constraints(
            aims=("answer", "disagree"), stances=("neutral", "skeptical"), desired_length=("high",)
        ),
        _behavior(aim="disagree", stance="skeptical", desired_length="high"),
        DeliberationOracleLabel.PLAN_HELPFUL,
        source="cog-v1-a-04",
    ),
    _scenario(
        "cog-v1-d2-08",
        "material_ambiguity",
        "The missing budget constraint changes the recommendation; which option should I choose?",
        _constraints(
            aims=("clarify", "explore"),
            question_policy=("required", "invite"),
            initiative=("normal",),
        ),
        _behavior(aim="clarify", question_policy="required"),
        DeliberationOracleLabel.PLAN_HELPFUL,
        source="cog-v1-a-05",
    ),
    _scenario(
        "cog-v1-d2-09",
        "cheap_reversible_ambiguity",
        "Should I use a list or a table? Either is easy to revise later.",
        _constraints(
            aims=("answer", "explore"), question_policy=("avoid",), initiative=("normal", "high")
        ),
        _behavior(aim="explore", initiative="high"),
        DeliberationOracleLabel.FAST_SAFE,
        source="cog-v1-a-06",
        pair="ambiguity-cost",
    ),
    _scenario(
        "cog-v1-d2-10",
        "vulnerability_social_nuance",
        "I'm not sure how to tell my friend that I need some space.",
        _constraints(
            aims=("comfort", "acknowledge"),
            humor_allowed=False,
            question_policy=("avoid", "invite"),
            stances=("supportive",),
        ),
        _behavior(aim="comfort", stance="supportive"),
        DeliberationOracleLabel.PLAN_HELPFUL,
        source="cog-v1-a-08",
    ),
    _scenario(
        "cog-v1-d2-11",
        "playful_serious_ambiguity",
        "I was joking about quitting, but I'm actually serious about the next step.",
        _constraints(
            aims=("clarify", "acknowledge", "explore"),
            question_policy=("required", "invite"),
            stances=("neutral", "supportive"),
        ),
        _behavior(aim="clarify", question_policy="invite"),
        DeliberationOracleLabel.PLAN_HELPFUL,
    ),
    _scenario(
        "cog-v1-d2-12",
        "straightforward_playful",
        "You call that a plan? My houseplant has better project management.",
        _constraints(
            aims=("answer", "tease"),
            question_policy=("avoid", "invite"),
            stances=("neutral", "playful"),
        ),
        _behavior(aim="tease", stance="playful", humor_allowed=True),
        DeliberationOracleLabel.FAST_SAFE,
        source="cog-v1-a-07",
    ),
    _scenario(
        "cog-v1-d2-13",
        "relevant_tailoring_interest",
        "Given my constraints and the two options, which would you choose for this situation?",
        _constraints(
            aims=("explore", "answer"), stances=("curious",), initiative=("normal", "high")
        ),
        _behavior(aim="explore", stance="curious", initiative="high"),
        DeliberationOracleLabel.PLAN_HELPFUL,
        recent=(
            ContextMessage("user", "We have been comparing two jacket cuts for a formal event."),
            ContextMessage("assistant", "The choice depends on the event and the fit you want."),
        ),
    ),
    _scenario(
        "cog-v1-d2-14",
        "irrelevant_tailoring_keyword",
        "The tailor said the suit is ready for pickup on Friday.",
        _constraints(
            aims=("answer", "acknowledge"), question_policy=("avoid",), stances=("neutral",)
        ),
        _behavior(aim="acknowledge"),
        DeliberationOracleLabel.FAST_SAFE,
    ),
    _scenario(
        "cog-v1-d2-15",
        "stable_self_concept",
        "Who is Lilavel?",
        _constraints(aims=("answer",), question_policy=("avoid",), humor_allowed=False),
        _behavior(),
        DeliberationOracleLabel.FAST_SAFE,
        source="cog-v1-a-18",
    ),
    _scenario(
        "cog-v1-d2-16",
        "counterfactual_low_cost",
        "Should I put this explanation in a list or a table?",
        _constraints(
            aims=("answer", "explore"), question_policy=("avoid",), initiative=("normal", "high")
        ),
        _behavior(aim="explore", initiative="high"),
        DeliberationOracleLabel.FAST_SAFE,
        pair="ambiguity-cost",
    ),
    _scenario(
        "cog-v1-d2-17",
        "counterfactual_high_cost",
        "The missing audience detail changes the recommendation; which format should I choose?",
        _constraints(
            aims=("clarify", "explore"),
            question_policy=("required", "invite"),
            initiative=("normal",),
        ),
        _behavior(aim="clarify", question_policy="required"),
        DeliberationOracleLabel.PLAN_HELPFUL,
        pair="ambiguity-cost",
    ),
    _scenario(
        "cog-v1-d2-18",
        "delayed_multi_turn_context",
        "What should I say now?",
        _constraints(
            aims=("clarify", "comfort", "explore"),
            question_policy=("required", "invite"),
            stances=("supportive", "curious"),
        ),
        _behavior(aim="explore", stance="curious", question_policy="invite"),
        DeliberationOracleLabel.PLAN_HELPFUL,
        recent=(
            ContextMessage("user", "My manager changed the deadline without telling me."),
            ContextMessage(
                "assistant", "We still need to decide how to respond to the deadline change."
            ),
        ),
    ),
    _scenario(
        "cog-v1-d2-19",
        "technically_hard_obvious",
        "Compare Raft and Paxos failure handling and include the message-flow trade-offs.",
        _constraints(
            aims=("answer",), desired_length=("normal", "high"), question_policy=("avoid",)
        ),
        _behavior(desired_length="high"),
        DeliberationOracleLabel.FAST_SAFE,
    ),
    _scenario(
        "cog-v1-d2-20",
        "planner_harmful_overreaction",
        "What is 2 + 2?",
        _constraints(aims=("answer",), question_policy=("avoid",), stances=("neutral",)),
        _behavior(aim="challenge", stance="skeptical", question_policy="required"),
        DeliberationOracleLabel.PLAN_HARMFUL,
    ),
    _scenario(
        "cog-v1-d2-21",
        "unresolved_mixed_ambiguity",
        "Can you help me decide?",
        _constraints(
            aims=("clarify", "explore"),
            question_policy=("required", "invite"),
            stances=("curious", "supportive"),
        ),
        _behavior(aim="explore", question_policy="avoid"),
        DeliberationOracleLabel.UNRESOLVED,
    ),
)

if any(
    scenario.source_scenario_id not in _COG_IDS
    for scenario in COG_V1_D2_SCENARIOS
    if scenario.source_scenario_id
):
    raise RuntimeError("COG-V1-D2 source scenario must reuse the COG-V1-A corpus")
if len({scenario.id for scenario in COG_V1_D2_SCENARIOS}) != len(COG_V1_D2_SCENARIOS):
    raise RuntimeError("COG-V1-D2 scenario IDs must be unique")


__all__ = [
    "COG_V1_D2_SCENARIOS",
    "AlwaysFastDeliberationPolicy",
    "AlwaysPlanDeliberationPolicy",
    "DeliberationBenchmarkResult",
    "DeliberationOracleLabel",
    "DeliberationScenario",
    "DeterministicDeliberationPolicy",
    "evaluate_deliberation_policy",
]
