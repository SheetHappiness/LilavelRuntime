"""Offline selective disposition cognition after an attention ``THINK``.

COG-V1-C chooses between a deterministic fast policy and a provider-neutral
deliberative seam.  It does not generate response text, own attention, mutate
the character canon, call tools, or enter the production conversation path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .cognition import (
    MAX_COGNITION_REASON_CODES,
    AttentionDecision,
    CognitionPolicyDecision,
    CognitionReasonCode,
    InterventionDecision,
    ResponseDisposition,
    WorkingState,
)

__all__ = [
    "DeliberativeDispositionPolicy",
    "DeterministicFastDispositionPolicy",
    "DispositionCase",
    "DispositionContext",
    "DispositionContextKind",
    "DispositionContractError",
    "DispositionFailure",
    "DispositionFailureCode",
    "DispositionResolution",
    "DispositionRoute",
    "DispositionRoutingEvidence",
    "DispositionSource",
    "FakeDeliberativeDispositionPolicy",
    "SelectiveDispositionPolicy",
    "choose_disposition_route",
    "route_disposition",
    "select_disposition_route",
    "validate_disposition_decision",
]


class DispositionRoute(StrEnum):
    """The only two COG-V1-C route choices."""

    FAST = "fast"
    DELIBERATE = "deliberate"


class DispositionSource(StrEnum):
    """The bounded source class used for intervention validation."""

    DIRECT_USER = "direct_user"
    AMBIENT = "ambient"


class DispositionCase(StrEnum):
    """Trusted, bounded context classifications used by the fast policy."""

    DEFAULT = "default"
    SIMPLE_DEFINITION = "simple_definition"
    SIMPLE_FACTUAL_QUERY = "simple_factual_query"
    STRAIGHTFORWARD_TECHNICAL_EXPLANATION = "straightforward_technical_explanation"
    ORDINARY_ACKNOWLEDGMENT = "ordinary_acknowledgment"
    CLEAR_PLAYFUL_TURN = "clear_playful_turn"
    CHEAP_REVERSIBLE_AMBIGUITY = "cheap_reversible_ambiguity"
    TAILORING_DISCUSSION = "tailoring_discussion"


# These aliases keep the context vocabulary easy to discover without adding
# another enum or another semantic lane.
DispositionContextKind = DispositionCase


class DispositionContractError(ValueError):
    """A fail-closed violation of the COG-V1-C input/output contract."""


class DispositionFailureCode(StrEnum):
    """Bounded failure metadata retained by a disposition resolution."""

    POLICY_ERROR = "policy_error"
    INVALID_DECISION = "invalid_decision"
    MISSING_POLICY = "missing_policy"


@dataclass(frozen=True, slots=True)
class DispositionFailure:
    """Typed, content-free policy failure evidence."""

    code: DispositionFailureCode

    def __post_init__(self) -> None:
        if type(self.code) is not DispositionFailureCode:
            raise TypeError("failure code must be a DispositionFailureCode")


@dataclass(frozen=True, slots=True)
class DispositionRoutingEvidence:
    """The complete bounded boolean input to route selection."""

    material_ambiguity: bool = False
    important_contradiction: bool = False
    evidence_update: bool = False
    high_social_stakes: bool = False
    multiple_plausible_moves: bool = False
    vulnerable_context: bool = False
    intervention_uncertain: bool = False
    cheap_reversible_assumption: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "material_ambiguity",
            "important_contradiction",
            "evidence_update",
            "high_social_stakes",
            "multiple_plausible_moves",
            "vulnerable_context",
            "intervention_uncertain",
            "cheap_reversible_assumption",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be a bool")


@dataclass(frozen=True, slots=True)
class DispositionContext:
    """Bounded structured context presented to either disposition policy."""

    source: DispositionSource
    attention: AttentionDecision
    routing_evidence: DispositionRoutingEvidence = field(default_factory=DispositionRoutingEvidence)
    case: DispositionCase = DispositionCase.DEFAULT

    def __post_init__(self) -> None:
        if type(self.source) is not DispositionSource:
            raise TypeError("source must be a DispositionSource")
        if type(self.attention) is not AttentionDecision:
            raise TypeError("attention must be an AttentionDecision")
        if type(self.routing_evidence) is not DispositionRoutingEvidence:
            raise TypeError("routing_evidence must be a DispositionRoutingEvidence")
        if type(self.case) is not DispositionCase:
            raise TypeError("case must be a DispositionCase")


@dataclass(frozen=True, slots=True)
class DispositionResolution:
    """The bounded result of one selective disposition decision."""

    route: DispositionRoute
    decision: CognitionPolicyDecision
    failure: DispositionFailure | None = None

    def __post_init__(self) -> None:
        if type(self.route) is not DispositionRoute:
            raise TypeError("route must be a DispositionRoute")
        if type(self.decision) is not CognitionPolicyDecision:
            raise TypeError("decision must be a CognitionPolicyDecision")
        if self.failure is not None and type(self.failure) is not DispositionFailure:
            raise TypeError("failure must be a DispositionFailure or None")

    @property
    def attention(self) -> AttentionDecision:
        """Expose the canonical attention value without copying the contract."""

        return self.decision.attention

    @property
    def intervention(self) -> InterventionDecision:
        """Expose the canonical intervention value without copying the contract."""

        return self.decision.intervention

    @property
    def working_state(self) -> WorkingState | None:
        return self.decision.working_state

    @property
    def response_disposition(self) -> ResponseDisposition | None:
        return self.decision.response_disposition

    @property
    def reason_codes(self) -> tuple[CognitionReasonCode, ...]:
        return self.decision.reason_codes

    @property
    def used_fallback(self) -> bool:
        return self.failure is not None


def route_disposition(evidence: DispositionRoutingEvidence) -> DispositionRoute:
    """Select FAST or DELIBERATE using the exact ordered boolean rules."""

    if type(evidence) is not DispositionRoutingEvidence:
        raise TypeError("evidence must be a DispositionRoutingEvidence")

    if evidence.material_ambiguity:
        return DispositionRoute.DELIBERATE
    if evidence.important_contradiction:
        return DispositionRoute.DELIBERATE
    if evidence.evidence_update:
        return DispositionRoute.DELIBERATE
    if evidence.high_social_stakes:
        return DispositionRoute.DELIBERATE
    if evidence.multiple_plausible_moves:
        return DispositionRoute.DELIBERATE
    if evidence.vulnerable_context:
        return DispositionRoute.DELIBERATE
    if evidence.intervention_uncertain:
        return DispositionRoute.DELIBERATE
    return DispositionRoute.FAST


select_disposition_route = route_disposition
choose_disposition_route = route_disposition


@runtime_checkable
class DeliberativeDispositionPolicy(Protocol):
    """Provider-neutral seam for one structured deliberative decision."""

    def decide(self, context: DispositionContext, /) -> CognitionPolicyDecision:
        """Return one existing ``CognitionPolicyDecision`` only."""

        ...


class DeterministicFastDispositionPolicy:
    """Pure, provider-free disposition policy for unambiguous THINK turns."""

    __slots__ = ()

    def decide(self, context: DispositionContext, /) -> CognitionPolicyDecision:
        _validate_context(context)

        if context.source is DispositionSource.AMBIENT:
            if context.case is DispositionCase.TAILORING_DISCUSSION:
                reasons = (
                    CognitionReasonCode.AMBIENT_CONTEXT,
                    CognitionReasonCode.INTEREST_AFFINITY,
                )
            else:
                reasons = (
                    CognitionReasonCode.AMBIENT_CONTEXT,
                    CognitionReasonCode.NO_NEW_VALUE,
                )
            return CognitionPolicyDecision(
                attention=AttentionDecision.THINK,
                intervention=InterventionDecision.NONE,
                reason_codes=reasons,
            )

        state = WorkingState("respond to the current user turn")
        disposition = ResponseDisposition()
        reasons: tuple[CognitionReasonCode, ...] = (CognitionReasonCode.DIRECT_ADDRESS,)

        if context.case in {
            DispositionCase.SIMPLE_DEFINITION,
            DispositionCase.SIMPLE_FACTUAL_QUERY,
        }:
            disposition = ResponseDisposition(
                aim="answer",
                directness="high",
                desired_length="low",
                humor_allowed=False,
                question_policy="avoid",
                initiative="low",
            )
            reasons = (
                CognitionReasonCode.DIRECT_ADDRESS,
                CognitionReasonCode.SIMPLE_REQUEST,
            )
        elif context.case is DispositionCase.STRAIGHTFORWARD_TECHNICAL_EXPLANATION:
            disposition = ResponseDisposition(
                aim="answer",
                directness="high",
                desired_length="normal",
                humor_allowed=False,
                question_policy="avoid",
                initiative="normal",
            )
            reasons = (
                CognitionReasonCode.DIRECT_ADDRESS,
                CognitionReasonCode.SIMPLE_REQUEST,
            )
        elif context.case is DispositionCase.ORDINARY_ACKNOWLEDGMENT:
            disposition = ResponseDisposition(
                aim="acknowledge",
                directness="normal",
                desired_length="low",
                humor_allowed=False,
                question_policy="avoid",
                initiative="low",
            )
        elif context.case is DispositionCase.CLEAR_PLAYFUL_TURN:
            state = WorkingState("respond to the current user turn", stance="playful")
            disposition = ResponseDisposition(
                aim="tease",
                directness="normal",
                desired_length="low",
                humor_allowed=True,
                question_policy="avoid",
                initiative="low",
            )
            reasons = (
                CognitionReasonCode.DIRECT_ADDRESS,
                CognitionReasonCode.PLAYFUL_CONTEXT,
            )
        elif context.case is DispositionCase.CHEAP_REVERSIBLE_AMBIGUITY:
            reasons = (
                CognitionReasonCode.DIRECT_ADDRESS,
                CognitionReasonCode.REVERSIBLE_ASSUMPTION,
            )
        elif context.case is DispositionCase.TAILORING_DISCUSSION:
            state = WorkingState(
                "respond to the current user turn",
                engagement="high",
                stance="curious",
            )
            reasons = (
                CognitionReasonCode.DIRECT_ADDRESS,
                CognitionReasonCode.INTEREST_AFFINITY,
            )

        return CognitionPolicyDecision(
            attention=AttentionDecision.THINK,
            intervention=InterventionDecision.RESPOND,
            working_state=state,
            response_disposition=disposition,
            reason_codes=reasons,
        )


class FakeDeliberativeDispositionPolicy:
    """Deterministic test double for the deliberative policy seam."""

    __slots__ = ("_decision", "_calls")

    def __init__(self, decision: CognitionPolicyDecision) -> None:
        if type(decision) is not CognitionPolicyDecision:
            raise TypeError("decision must be a CognitionPolicyDecision")
        self._decision = decision
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    def decide(self, context: DispositionContext, /) -> CognitionPolicyDecision:
        _validate_context(context)
        self._calls += 1
        return self._decision


class SelectiveDispositionPolicy:
    """Choose and validate one offline disposition policy after ``THINK``."""

    __slots__ = ("_fast_policy", "_deliberative_policy")

    def __init__(
        self,
        *,
        fast_policy: DeterministicFastDispositionPolicy | None = None,
        deliberative_policy: DeliberativeDispositionPolicy | None = None,
    ) -> None:
        selected_fast = fast_policy or DeterministicFastDispositionPolicy()
        if not callable(getattr(selected_fast, "decide", None)):
            raise TypeError("fast_policy must expose decide(context)")
        if deliberative_policy is not None and not callable(
            getattr(deliberative_policy, "decide", None)
        ):
            raise TypeError("deliberative_policy must expose decide(context)")
        self._fast_policy = selected_fast
        self._deliberative_policy = deliberative_policy

    def decide(self, context: DispositionContext, /) -> DispositionResolution:
        """Return a validated policy decision or a source-safe fallback."""

        _validate_context(context)
        route = route_disposition(context.routing_evidence)
        policy: DeliberativeDispositionPolicy | None
        policy = self._fast_policy if route is DispositionRoute.FAST else self._deliberative_policy

        if policy is None:
            return self._fallback(
                context,
                route,
                DispositionFailure(DispositionFailureCode.MISSING_POLICY),
            )

        try:
            decision = policy.decide(context)
        except Exception:
            return self._fallback(
                context,
                route,
                DispositionFailure(DispositionFailureCode.POLICY_ERROR),
            )

        try:
            validate_disposition_decision(context, decision)
        except (DispositionContractError, TypeError, ValueError):
            return self._fallback(
                context,
                route,
                DispositionFailure(DispositionFailureCode.INVALID_DECISION),
            )
        return DispositionResolution(route=route, decision=decision)

    def resolve(self, context: DispositionContext, /) -> DispositionResolution:
        """Named alias for callers that treat the coordinator as a resolver."""

        return self.decide(context)

    def _fallback(
        self,
        context: DispositionContext,
        route: DispositionRoute,
        failure: DispositionFailure,
    ) -> DispositionResolution:
        if context.source is DispositionSource.DIRECT_USER:
            decision = _safe_direct_user_decision()
        else:
            decision = _safe_ambient_decision()
        return DispositionResolution(
            route=route,
            decision=decision,
            failure=failure,
        )


def validate_disposition_decision(
    context: DispositionContext,
    decision: CognitionPolicyDecision,
) -> None:
    """Validate COG-V1-C semantic and source invariants, fail closed."""

    _validate_context(context)
    if type(decision) is not CognitionPolicyDecision:
        raise TypeError("decision must be a CognitionPolicyDecision")
    if decision.attention is not AttentionDecision.THINK:
        raise DispositionContractError("COG-V1-C decisions require Attention THINK")
    if context.source is DispositionSource.DIRECT_USER:
        if decision.intervention is not InterventionDecision.RESPOND:
            raise DispositionContractError("direct USER decisions require RESPOND")
    elif decision.intervention is InterventionDecision.RESPOND:
        raise DispositionContractError("ambient decisions cannot use RESPOND")

    intervention = decision.intervention
    if intervention in {InterventionDecision.RESPOND, InterventionDecision.INTERJECT}:
        if decision.working_state is None or decision.response_disposition is None:
            raise DispositionContractError(
                "speaking decisions require WorkingState and ResponseDisposition"
            )
    elif decision.response_disposition is not None:
        raise DispositionContractError("NONE decisions require no ResponseDisposition")

    state = decision.working_state
    if state is not None:
        _validate_working_state(state)
    disposition = decision.response_disposition
    if disposition is not None:
        _validate_response_disposition(disposition)

    if len(decision.reason_codes) > MAX_COGNITION_REASON_CODES or not all(
        type(code) is CognitionReasonCode for code in decision.reason_codes
    ):
        raise DispositionContractError("reason codes must use the bounded cognition enum")


def _validate_context(context: DispositionContext) -> None:
    if type(context) is not DispositionContext:
        raise TypeError("context must be a DispositionContext")
    if context.attention is not AttentionDecision.THINK:
        raise DispositionContractError("COG-V1-C is valid only after Attention THINK")


def _validate_working_state(state: WorkingState) -> None:
    if type(state) is not WorkingState:
        raise DispositionContractError("working_state must be a WorkingState")
    if type(state.focus) is not str or not state.focus.strip():
        raise DispositionContractError("working_state focus must be bounded non-empty text")
    if len(state.focus.encode("utf-8")) > 256:
        raise DispositionContractError("working_state focus is too long")
    if state.engagement not in {"low", "normal", "high"}:
        raise DispositionContractError("working_state engagement is invalid")
    if state.stance not in {"neutral", "curious", "skeptical", "playful", "supportive"}:
        raise DispositionContractError("working_state stance is invalid")


def _validate_response_disposition(disposition: ResponseDisposition) -> None:
    if type(disposition) is not ResponseDisposition:
        raise DispositionContractError("response_disposition must be a ResponseDisposition")
    if disposition.aim not in {
        "answer",
        "acknowledge",
        "clarify",
        "challenge",
        "tease",
        "comfort",
        "disagree",
        "explore",
        "close",
    }:
        raise DispositionContractError("response_disposition aim is invalid")
    if disposition.directness not in {"low", "normal", "high"}:
        raise DispositionContractError("response_disposition directness is invalid")
    if disposition.desired_length not in {"low", "normal", "high"}:
        raise DispositionContractError("response_disposition desired_length is invalid")
    if type(disposition.humor_allowed) is not bool:
        raise DispositionContractError("response_disposition humor_allowed must be a bool")
    if disposition.question_policy not in {"avoid", "required", "invite"}:
        raise DispositionContractError("response_disposition question_policy is invalid")
    if disposition.initiative not in {"low", "normal", "high"}:
        raise DispositionContractError("response_disposition initiative is invalid")


def _safe_direct_user_decision() -> CognitionPolicyDecision:
    return CognitionPolicyDecision(
        attention=AttentionDecision.THINK,
        intervention=InterventionDecision.RESPOND,
        working_state=WorkingState(
            focus="respond to the current user turn",
            engagement="normal",
            stance="neutral",
        ),
        response_disposition=ResponseDisposition(
            aim="answer",
            directness="normal",
            desired_length="normal",
            humor_allowed=False,
            question_policy="avoid",
            initiative="normal",
        ),
        reason_codes=(CognitionReasonCode.DIRECT_ADDRESS,),
    )


def _safe_ambient_decision() -> CognitionPolicyDecision:
    return CognitionPolicyDecision(
        attention=AttentionDecision.THINK,
        intervention=InterventionDecision.NONE,
        working_state=None,
        response_disposition=None,
        reason_codes=(
            CognitionReasonCode.AMBIENT_CONTEXT,
            CognitionReasonCode.NO_NEW_VALUE,
        ),
    )
