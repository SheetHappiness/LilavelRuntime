"""Offline evaluation helpers for model-backed disposition candidates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lilavel_core import CognitionPolicyDecision, DispositionCandidate
from lilavel_core.cognition_eval import (
    COG_V1_A_SCENARIOS,
    CognitionPolicyEvalResult,
    CognitionScenario,
    CognitionScenarioSource,
    evaluate_cognition_policy,
)

from .cognition_model import DispositionPlanner, candidate_to_policy_decision

COG_V1_C_SCENARIOS: tuple[CognitionScenario, ...] = tuple(
    scenario
    for scenario in COG_V1_A_SCENARIOS
    if scenario.source_class is CognitionScenarioSource.DIRECT_USER
)


@dataclass(frozen=True, slots=True)
class DispositionPlannerEvalResult:
    """One bounded planner result and its policy-corpus evaluation."""

    scenario_id: str
    candidate: DispositionCandidate | None = None
    policy_result: CognitionPolicyEvalResult | None = None
    failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError("scenario_id must be non-empty")
        object.__setattr__(self, "failures", tuple(self.failures))
        if self.policy_result is not None and self.policy_result.scenario_id != self.scenario_id:
            raise ValueError("policy result scenario does not match planner result")

    @property
    def passed(self) -> bool:
        """Whether the candidate was valid and met the corpus constraints."""

        return not self.failures and self.policy_result is not None and self.policy_result.passed


def evaluate_disposition_candidate(
    scenario: CognitionScenario,
    candidate: DispositionCandidate | None,
) -> DispositionPlannerEvalResult:
    """Evaluate one parsed candidate without making a provider call."""

    if type(scenario) is not CognitionScenario:
        raise TypeError("scenario must be a CognitionScenario")
    if candidate is None:
        return DispositionPlannerEvalResult(scenario.id, failures=("invalid_candidate",))
    if type(candidate) is not DispositionCandidate:
        raise TypeError("candidate must be a DispositionCandidate or None")
    decision: CognitionPolicyDecision = candidate_to_policy_decision(candidate)
    policy_result = evaluate_cognition_policy(scenario, decision)
    return DispositionPlannerEvalResult(
        scenario_id=scenario.id,
        candidate=candidate,
        policy_result=policy_result,
        failures=policy_result.failures,
    )


def evaluate_disposition_corpus(
    candidates: Mapping[str, DispositionCandidate | None],
    *,
    scenarios: Sequence[CognitionScenario] = COG_V1_C_SCENARIOS,
) -> tuple[DispositionPlannerEvalResult, ...]:
    """Evaluate exactly one candidate for each supplied corpus scenario."""

    scenario_values = tuple(scenarios)
    expected_ids = {scenario.id for scenario in scenario_values}
    if set(candidates) != expected_ids:
        raise ValueError("candidates must contain every evaluation scenario exactly once")
    return tuple(
        evaluate_disposition_candidate(scenario, candidates[scenario.id])
        for scenario in scenario_values
    )


async def run_disposition_planner_eval(
    planner: DispositionPlanner,
    *,
    scenarios: Sequence[CognitionScenario] = COG_V1_C_SCENARIOS,
) -> tuple[DispositionPlannerEvalResult, ...]:
    """Run a planner over a fixed corpus and return reviewable results.

    This helper is intentionally opt-in and caller-owned; it is not part of
    the production USER conversation path.
    """

    results: list[DispositionPlannerEvalResult] = []
    for scenario in scenarios:
        candidate = await planner.plan_candidate(
            scenario.input_context,
            scope_id=f"disposition-eval:{scenario.id}",
            logical_run_id=f"disposition-eval:{scenario.id}",
        )
        results.append(evaluate_disposition_candidate(scenario, candidate))
    return tuple(results)


__all__ = [
    "COG_V1_C_SCENARIOS",
    "DispositionPlannerEvalResult",
    "evaluate_disposition_candidate",
    "evaluate_disposition_corpus",
    "run_disposition_planner_eval",
]
