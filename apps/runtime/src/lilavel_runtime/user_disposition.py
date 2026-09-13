"""Run-bound direct USER disposition resolution for COG-V1-D1/D2."""

from __future__ import annotations

import asyncio
from enum import StrEnum
from time import monotonic_ns

from lilavel_core import (
    MAX_DELIBERATION_CONTEXT_MESSAGES,
    CognitionPolicyDecision,
    ConversationCore,
    ConversationRun,
    DeliberationContext,
    DeliberationDecision,
    DeliberationPolicy,
    DispositionCandidate,
    TurnBehavior,
    TurnBehaviorResolution,
    TurnBehaviorResolutionOutcome,
    TurnBehaviorSource,
    default_turn_behavior,
)

from .cognition_model import DispositionPlanner, candidate_to_policy_decision
from .semantic_actor import SemanticCancellationToken


class DeliberationMode(StrEnum):
    """The explicit production routing modes for direct USER turns."""

    DEFAULT_ONLY = "default_only"
    ALWAYS_PLAN = "always_plan"
    SELECTIVE = "selective"


class PlannerFallbackReason(StrEnum):
    """Bounded reasons for safely retaining the default behavior."""

    REJECTED = "planner_rejected"
    PROVIDER_FAILURE = "planner_provider_failure"
    TIMEOUT = "planner_timeout"
    CANCELLED = "planner_cancelled"
    UNEXPECTED = "planner_unexpected_error"


class DeliberationFallbackReason(StrEnum):
    """Bounded reasons for the optional router's safe FAST fallback."""

    POLICY_FAILURE = "deliberation_policy_failed"


class UserDispositionResolver:
    """Resolve one accepted USER run without owning admission or history."""

    def __init__(
        self,
        *,
        mode: DeliberationMode = DeliberationMode.DEFAULT_ONLY,
        planner: DispositionPlanner | None = None,
        policy: DeliberationPolicy | None = None,
    ) -> None:
        if type(mode) is not DeliberationMode:
            raise TypeError("mode must be a DeliberationMode")
        if mode is DeliberationMode.ALWAYS_PLAN and planner is None:
            raise ValueError("ALWAYS_PLAN requires a DispositionPlanner")
        if mode is DeliberationMode.SELECTIVE and planner is None:
            raise ValueError("SELECTIVE requires a DispositionPlanner")
        if mode is DeliberationMode.SELECTIVE and policy is None:
            raise ValueError("SELECTIVE requires an injected DeliberationPolicy")
        if policy is not None and not callable(getattr(policy, "decide", None)):
            raise TypeError("policy must provide decide")
        self._mode = mode
        self._planner = planner
        self._policy = policy

    @property
    def mode(self) -> DeliberationMode:
        return self._mode

    async def resolve(
        self,
        core: ConversationCore,
        run: ConversationRun,
        cancellation: SemanticCancellationToken | None = None,
    ) -> TurnBehaviorResolution:
        """Resolve exactly once for one prepared run, with safe fallback."""

        default = default_turn_behavior()
        if self._mode is DeliberationMode.DEFAULT_ONLY:
            resolution = TurnBehaviorResolution(
                behavior=default,
                planner_invoked=False,
                outcome=TurnBehaviorResolutionOutcome.NOT_INVOKED,
                deliberation_decision=DeliberationDecision.FAST,
                deliberation_mode=self._mode.value,
            )
            core.record_turn_behavior_resolution(run, resolution)
            return resolution

        if cancellation is not None and cancellation.is_requested:
            raise asyncio.CancelledError

        policy_name: str | None = None
        if self._mode is DeliberationMode.SELECTIVE:
            policy = self._policy
            assert policy is not None
            policy_name = _policy_identity(policy)
            try:
                decision = policy.decide(_deliberation_context(core))
                if type(decision) is not DeliberationDecision:
                    raise TypeError("deliberation policy returned an invalid decision")
            except asyncio.CancelledError:
                raise
            except Exception:
                resolution = TurnBehaviorResolution(
                    behavior=default,
                    planner_invoked=False,
                    outcome=TurnBehaviorResolutionOutcome.NOT_INVOKED,
                    deliberation_decision=DeliberationDecision.FAST,
                    deliberation_mode=self._mode.value,
                    deliberation_policy=policy_name,
                    deliberation_fallback_reason=DeliberationFallbackReason.POLICY_FAILURE.value,
                )
                core.record_turn_behavior_resolution(run, resolution)
                return resolution
            if cancellation is not None and cancellation.is_requested:
                raise asyncio.CancelledError
        else:
            decision = DeliberationDecision.PLAN
            policy_name = self._mode.value

        if decision is DeliberationDecision.FAST:
            resolution = TurnBehaviorResolution(
                behavior=default,
                planner_invoked=False,
                outcome=TurnBehaviorResolutionOutcome.NOT_INVOKED,
                deliberation_decision=decision,
                deliberation_mode=self._mode.value,
                deliberation_policy=policy_name,
            )
            core.record_turn_behavior_resolution(run, resolution)
            return resolution

        planner = self._planner
        assert planner is not None
        started_ns = monotonic_ns()
        try:
            candidate = await planner.plan_candidate(
                core.history[-1].text,
                recent_context=core.history,
                scope_id=core.scope_id,
                logical_run_id=run.run_id,
            )
        except asyncio.CancelledError:
            if _task_is_being_cancelled() or (
                cancellation is not None and cancellation.is_requested
            ):
                raise
            return self._fallback(
                core,
                run,
                default,
                PlannerFallbackReason.CANCELLED,
                started_ns,
                decision=decision,
                policy_name=policy_name,
            )
        except TimeoutError:
            return self._fallback(
                core,
                run,
                default,
                PlannerFallbackReason.TIMEOUT,
                started_ns,
                decision=decision,
                policy_name=policy_name,
            )
        except Exception as error:
            if getattr(error, "semantic_uncontained", False) is True:
                raise
            reason = (
                PlannerFallbackReason.PROVIDER_FAILURE
                if isinstance(error, RuntimeError)
                else PlannerFallbackReason.UNEXPECTED
            )
            return self._fallback(
                core,
                run,
                default,
                reason,
                started_ns,
                decision=decision,
                policy_name=policy_name,
            )

        duration_ms = _duration_ms(started_ns)
        if candidate is None:
            resolution = TurnBehaviorResolution(
                behavior=default,
                planner_invoked=True,
                outcome=TurnBehaviorResolutionOutcome.REJECTED,
                fallback_reason=PlannerFallbackReason.REJECTED.value,
                planner_duration_ms=duration_ms,
                deliberation_decision=decision,
                deliberation_mode=self._mode.value,
                deliberation_policy=policy_name,
            )
            core.record_turn_behavior_resolution(run, resolution)
            return resolution

        behavior = _candidate_to_behavior(candidate)
        resolution = TurnBehaviorResolution(
            behavior=behavior,
            planner_invoked=True,
            outcome=TurnBehaviorResolutionOutcome.ACCEPTED,
            planner_duration_ms=duration_ms,
            materially_differs_from_default=behavior.materially_differs_from(default),
            deliberation_decision=decision,
            deliberation_mode=self._mode.value,
            deliberation_policy=policy_name,
        )
        core.record_turn_behavior_resolution(run, resolution)
        return resolution

    def _fallback(
        self,
        core: ConversationCore,
        run: ConversationRun,
        default: TurnBehavior,
        reason: PlannerFallbackReason,
        started_ns: int,
        *,
        decision: DeliberationDecision,
        policy_name: str | None,
    ) -> TurnBehaviorResolution:
        resolution = TurnBehaviorResolution(
            behavior=default,
            planner_invoked=True,
            outcome=TurnBehaviorResolutionOutcome.FALLBACK,
            fallback_reason=reason.value,
            planner_duration_ms=_duration_ms(started_ns),
            deliberation_decision=decision,
            deliberation_mode=self._mode.value,
            deliberation_policy=policy_name,
        )
        core.record_turn_behavior_resolution(run, resolution)
        return resolution


def _candidate_to_behavior(candidate: DispositionCandidate) -> TurnBehavior:
    decision: CognitionPolicyDecision = candidate_to_policy_decision(candidate)
    if decision.working_state is None or decision.response_disposition is None:
        raise RuntimeError("planner candidate did not produce direct-user behavior")
    return TurnBehavior(
        working_state=decision.working_state,
        response_disposition=decision.response_disposition,
        reason_codes=decision.reason_codes,
        source=TurnBehaviorSource.PLANNER,
    )


def _duration_ms(started_ns: int) -> int:
    return max(0, (monotonic_ns() - started_ns) // 1_000_000)


def _task_is_being_cancelled() -> bool:
    task = asyncio.current_task()
    return task is not None and getattr(task, "cancelling", lambda: 0)() > 0


def _deliberation_context(core: ConversationCore) -> DeliberationContext:
    history = core.history
    if not history:
        raise RuntimeError("accepted USER turn has no canonical context")
    return DeliberationContext(
        current_user_turn=history[-1],
        recent_canonical_context=history[-MAX_DELIBERATION_CONTEXT_MESSAGES:],
    )


def _policy_identity(policy: DeliberationPolicy) -> str:
    candidate = getattr(policy, "policy_id", None)
    value = candidate if type(candidate) is str and candidate.strip() else type(policy).__name__
    if not value.strip() or len(value.encode("utf-8")) > 128:
        raise ValueError("deliberation policy identity is invalid")
    return value


__all__ = [
    "DeliberationMode",
    "DeliberationFallbackReason",
    "PlannerFallbackReason",
    "UserDispositionResolver",
]
