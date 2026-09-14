"""COG-V1-D2 selective deliberation contracts, routing, and evidence proofs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from lilavel_core import (
    CognitionReasonCode,
    ConversationCore,
    DeliberationDecision,
    DispositionCandidate,
    GenerationCompleted,
    GenerationEvent,
    ModelRequest,
    TextDelta,
    TurnBehaviorSource,
)
from lilavel_core.cognition import Aim

from lilavel_runtime import (
    COG_V1_D2_SCENARIOS,
    AlwaysFastDeliberationPolicy,
    AlwaysPlanDeliberationPolicy,
    DeliberationMode,
    DeterministicDeliberationPolicy,
    SemanticCancellationToken,
    UserDispositionResolver,
    evaluate_deliberation_policy,
)
from lilavel_runtime.conversation_adapter import (
    ConversationExecutionAdapter,
    ConversationExecutionResult,
)


@dataclass
class _Generation:
    generation_id: str
    events_to_emit: tuple[GenerationEvent, ...]
    epoch: int = 1

    def events(self) -> Iterator[GenerationEvent]:
        yield from self.events_to_emit


class _Runtime:
    def __init__(self) -> None:
        self.calls = 0

    def generate_for_run(
        self,
        request: ModelRequest,
        *,
        scope_id: str,
        logical_run_id: str,
    ) -> _Generation:
        del request, scope_id
        self.calls += 1
        generation_id = f"response-{self.calls}-{logical_run_id}"
        return _Generation(
            generation_id,
            (TextDelta(generation_id, 1, "response"), GenerationCompleted(generation_id, 1)),
        )

    def generate(self, request: ModelRequest) -> _Generation:
        return self.generate_for_run(request, scope_id="scope", logical_run_id="legacy")

    def cancel(self, generation_id: str) -> bool:
        del generation_id
        return True


class _Planner:
    def __init__(self, candidate: DispositionCandidate | None) -> None:
        self.candidate = candidate
        self.calls = 0

    async def plan_candidate(self, *args: object, **kwargs: object) -> DispositionCandidate | None:
        del args, kwargs
        self.calls += 1
        return self.candidate


class _Policy:
    policy_id = "test_injected_policy"

    def __init__(self, decision: DeliberationDecision) -> None:
        self.decision = decision
        self.contexts: list[object] = []

    def decide(self, context: object) -> DeliberationDecision:
        self.contexts.append(context)
        return self.decision


class _FailingPolicy:
    policy_id = "test_failing_policy"

    def decide(self, context: object) -> DeliberationDecision:
        del context
        raise RuntimeError("router fixture failure")


def _candidate(*, aim: Aim = "challenge") -> DispositionCandidate:
    return DispositionCandidate(
        aim=aim,
        stance="skeptical",
        engagement="normal",
        directness="normal",
        desired_length="normal",
        humor_allowed=False,
        question_policy="avoid",
        initiative="normal",
        reason_codes=(CognitionReasonCode.CONTRADICTION,),
    )


def _core(runtime: _Runtime) -> ConversationCore:
    return ConversationCore(
        runtime,
        trusted_guidance=("[character canon]",),
        turn_guidance=lambda behavior: (f"[behavior] {behavior.working_state.focus}",),
        scope_id="d2-test",
    )


async def _execute(
    runtime: _Runtime,
    resolver: UserDispositionResolver,
    text: str = "current turn",
) -> tuple[ConversationCore, _Runtime, ConversationExecutionResult]:
    core = _core(runtime)
    result = await ConversationExecutionAdapter(disposition_resolver=resolver).execute_core_turn(
        core,
        text,
        lambda event: None,
        SemanticCancellationToken(),
    )
    return core, runtime, result


@pytest.mark.asyncio
async def test_fast_path_invokes_zero_planners_and_binds_default_behavior() -> None:
    runtime = _Runtime()
    planner = _Planner(_candidate())
    policy = _Policy(DeliberationDecision.FAST)
    resolver = UserDispositionResolver(
        mode=DeliberationMode.SELECTIVE,
        planner=planner,  # type: ignore[arg-type]
        policy=policy,
    )

    core, _, result = await _execute(runtime, resolver)

    assert result.outcome.status == "completed"
    assert runtime.calls == 1
    assert planner.calls == 0
    assert result.run.behavior is not None
    assert result.run.behavior.source is TurnBehaviorSource.DEFAULT
    record = next(item for item in core.d1_evidence() if item.kind == "disposition_resolved")
    assert record.deliberation_decision == "fast"
    assert record.deliberation_mode == "selective"
    assert record.deliberation_policy == "test_injected_policy"
    assert record.planner_invoked is False


@pytest.mark.asyncio
async def test_plan_path_invokes_exactly_one_planner_and_binds_its_behavior() -> None:
    runtime = _Runtime()
    planner = _Planner(_candidate())
    resolver = UserDispositionResolver(
        mode=DeliberationMode.SELECTIVE,
        planner=planner,  # type: ignore[arg-type]
        policy=_Policy(DeliberationDecision.PLAN),
    )

    core, _, result = await _execute(runtime, resolver)

    assert result.outcome.status == "completed"
    assert runtime.calls == 1
    assert planner.calls == 1
    assert result.run.behavior is not None
    assert result.run.behavior.source is TurnBehaviorSource.PLANNER
    record = next(item for item in core.d1_evidence() if item.kind == "disposition_resolved")
    assert record.deliberation_decision == "plan"
    assert record.planner_invoked is True
    assert record.planner_outcome == "accepted"


@pytest.mark.asyncio
async def test_plan_rejection_falls_back_without_retry() -> None:
    runtime = _Runtime()
    planner = _Planner(None)
    resolver = UserDispositionResolver(
        mode=DeliberationMode.SELECTIVE,
        planner=planner,  # type: ignore[arg-type]
        policy=_Policy(DeliberationDecision.PLAN),
    )

    core, _, result = await _execute(runtime, resolver)

    assert result.outcome.status == "completed"
    assert runtime.calls == 1
    assert planner.calls == 1
    assert result.run.behavior is not None
    assert result.run.behavior.source is TurnBehaviorSource.DEFAULT
    record = next(item for item in core.d1_evidence() if item.kind == "disposition_resolved")
    assert record.planner_outcome == "rejected"
    assert record.planner_fallback_reason == "planner_rejected"


@pytest.mark.asyncio
async def test_selective_policy_failure_fails_closed_to_fast_default() -> None:
    runtime = _Runtime()
    planner = _Planner(_candidate())
    resolver = UserDispositionResolver(
        mode=DeliberationMode.SELECTIVE,
        planner=planner,  # type: ignore[arg-type]
        policy=_FailingPolicy(),  # type: ignore[arg-type]
    )

    core, _, result = await _execute(runtime, resolver)

    assert result.outcome.status == "completed"
    assert runtime.calls == 1
    assert planner.calls == 0
    record = next(item for item in core.d1_evidence() if item.kind == "disposition_resolved")
    assert record.deliberation_decision == "fast"
    assert record.deliberation_fallback_reason == "deliberation_policy_failed"
    assert record.planner_invoked is False


@pytest.mark.asyncio
async def test_default_only_and_always_plan_preserve_d1_modes() -> None:
    default_runtime = _Runtime()
    default_planner = _Planner(_candidate())
    default_core, _, default_result = await _execute(
        default_runtime,
        UserDispositionResolver(
            mode=DeliberationMode.DEFAULT_ONLY,
            planner=default_planner,  # type: ignore[arg-type]
        ),
    )
    assert default_result.run.behavior is not None
    assert default_result.run.behavior.source is TurnBehaviorSource.DEFAULT
    assert default_runtime.calls == 1
    assert default_planner.calls == 0
    assert (
        next(
            item for item in default_core.d1_evidence() if item.kind == "disposition_resolved"
        ).deliberation_decision
        == "fast"
    )

    plan_runtime = _Runtime()
    plan_planner = _Planner(_candidate())
    _, _, plan_result = await _execute(
        plan_runtime,
        UserDispositionResolver(
            mode=DeliberationMode.ALWAYS_PLAN,
            planner=plan_planner,  # type: ignore[arg-type]
        ),
    )
    assert plan_result.run.behavior is not None
    assert plan_result.run.behavior.source is TurnBehaviorSource.PLANNER
    assert plan_runtime.calls == 1
    assert plan_planner.calls == 1


def test_oracle_is_quality_based_not_structural_difference_alone() -> None:
    formatting = next(item for item in COG_V1_D2_SCENARIOS if item.id == "cog-v1-d2-04")
    assert formatting.default_behavior.materially_differs_from(formatting.planner_behavior)
    assert formatting.label.value == "fast_safe"
    assert formatting.default_quality == 1.0
    assert formatting.planner_quality == 1.0


def test_all_required_baselines_report_cost_sensitive_metrics() -> None:
    always_fast = evaluate_deliberation_policy(AlwaysFastDeliberationPolicy())
    always_plan = evaluate_deliberation_policy(AlwaysPlanDeliberationPolicy())
    rules = evaluate_deliberation_policy(DeterministicDeliberationPolicy())

    assert always_fast.planner_invocation_rate == 0.0
    assert always_fast.plan_recall == 0.0
    assert always_fast.missed_plan_helpful_rate == 1.0
    assert always_plan.planner_invocation_rate == 1.0
    assert always_plan.plan_recall == 1.0
    assert always_plan.planner_harmful_rate == 1.0
    assert rules.plan_precision == 1.0
    assert rules.plan_recall == 1.0
    assert rules.false_plan_rate == 0.0
    assert rules.planner_invocation_rate < always_plan.planner_invocation_rate
    assert rules.selected_path_matches_oracle_best > always_fast.selected_path_matches_oracle_best
    assert rules.confusion_matrix["plan_helpful"]["plan"] == 9
    assert rules.confusion_matrix["fast_safe"]["fast"] == 10
    assert rules.excluded_from_binary_scoring == 2


def test_rule_policy_distinguishes_counterfactuals_and_bounded_context() -> None:
    policy = DeterministicDeliberationPolicy()
    low = next(item for item in COG_V1_D2_SCENARIOS if item.id == "cog-v1-d2-16")
    high = next(item for item in COG_V1_D2_SCENARIOS if item.id == "cog-v1-d2-17")
    delayed = next(item for item in COG_V1_D2_SCENARIOS if item.id == "cog-v1-d2-18")
    technical = next(item for item in COG_V1_D2_SCENARIOS if item.id == "cog-v1-d2-19")
    irrelevant = next(item for item in COG_V1_D2_SCENARIOS if item.id == "cog-v1-d2-14")
    contradiction = next(item for item in COG_V1_D2_SCENARIOS if item.id == "cog-v1-d2-05")

    assert policy.decide(low.context) is DeliberationDecision.FAST
    assert policy.decide(high.context) is DeliberationDecision.PLAN
    assert policy.decide(delayed.context) is DeliberationDecision.PLAN
    assert policy.decide(technical.context) is DeliberationDecision.FAST
    assert policy.decide(irrelevant.context) is DeliberationDecision.FAST
    assert policy.decide(contradiction.context) is DeliberationDecision.PLAN
    assert len(delayed.context.recent_canonical_context) <= 4
    assert delayed.context.current_user_turn.text == "What should I say now?"


def test_dataset_has_human_authored_label_distribution_and_counterfactual_pair() -> None:
    labels = {item.label.value for item in COG_V1_D2_SCENARIOS}
    assert labels == {"fast_safe", "plan_helpful", "plan_harmful", "unresolved"}
    for group in ("ambiguity-cost", "tailoring-context"):
        pair = [item for item in COG_V1_D2_SCENARIOS if item.counterfactual_group == group]
        assert pair
        assert {item.label.value for item in pair} == {"fast_safe", "plan_helpful"}


def test_core_has_one_canonical_fast_plan_route_and_bounded_reason_vocabulary() -> None:
    import lilavel_core

    assert set(DeliberationDecision) == {
        DeliberationDecision.FAST,
        DeliberationDecision.PLAN,
    }
    assert not any(
        hasattr(lilavel_core, name)
        for name in (
            "DispositionRoute",
            "SelectiveDispositionPolicy",
            "DeterministicFastDispositionPolicy",
            "DeliberativeDispositionPolicy",
        )
    )
    assert {
        CognitionReasonCode.SIMPLE_REQUEST,
        CognitionReasonCode.HIGH_SOCIAL_STAKES,
        CognitionReasonCode.MULTIPLE_PLAUSIBLE_MOVES,
        CognitionReasonCode.INTERVENTION_UNCERTAIN,
        CognitionReasonCode.REVERSIBLE_ASSUMPTION,
    }.issubset(set(CognitionReasonCode))
