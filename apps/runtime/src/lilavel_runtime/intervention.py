"""Deterministic ambient intervention candidates and social permission.

This module is the boundary between cognition-time semantic value and a future
effect-time speaking decision.  It contains no model, prose, action, or
presentation capability.  A candidate is advisory typed data; the current
runtime-owned context is revalidated before any future externalization.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType

from lilavel_core import CognitionReasonCode, InterventionDecision

from .semantic_actor import SemanticPriority, SemanticSourceKind

MAX_INTERVENTION_REASON_CODES = 8


class InterventionValue(StrEnum):
    """The semantic value established by cognition for one ambient candidate."""

    NONE = "none"
    RESPONSE_OBLIGATION = "response_obligation"
    MATERIAL_CONTRIBUTION = "material_contribution"


class SpeakingSurfaceState(StrEnum):
    """Whether the current environment exposes an allowed speaking surface."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class FloorState(StrEnum):
    """Trusted current floor state, when an environment exposes one."""

    UNKNOWN = "unknown"
    FREE = "free"
    BUSY = "busy"


class ActivityState(StrEnum):
    """Whether the interaction that produced the candidate is still current."""

    UNKNOWN = "unknown"
    CURRENT = "current"
    NOT_CURRENT = "not_current"


class FreshnessClass(StrEnum):
    """Event classes with separately owned freshness lifetimes."""

    REACTIVE = "reactive"
    CONTINUITY = "continuity"
    INTERNAL = "internal"
    TEMPORAL = "temporal"


class FreshnessBucket(StrEnum):
    """A bounded age bucket minted by the owning runtime or adapter."""

    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"


class RecentSpeechState(StrEnum):
    """Bounded recent-speech state used only for unsolicited initiative."""

    CLEAR = "clear"
    RECENT = "recent"


class InterventionBudgetState(StrEnum):
    """Whether the runtime-owned unsolicited intervention budget remains."""

    AVAILABLE = "available"
    EXHAUSTED = "exhausted"


class HandlingState(StrEnum):
    """Whether the contribution is still unresolved in the trusted context."""

    UNKNOWN = "unknown"
    UNRESOLVED = "unresolved"
    HANDLED = "handled"


class SocialSensitivity(StrEnum):
    """A trusted coarse social-context guard for unsolicited entry."""

    ORDINARY = "ordinary"
    VULNERABLE = "vulnerable"


@dataclass(frozen=True, slots=True)
class FreshnessProfile:
    """Maximum age bucket permitted for each speaking mode."""

    respond_until: FreshnessBucket = FreshnessBucket.FRESH
    interject_until: FreshnessBucket = FreshnessBucket.FRESH

    def __post_init__(self) -> None:
        if type(self.respond_until) is not FreshnessBucket:
            raise TypeError("respond_until must be a FreshnessBucket")
        if type(self.interject_until) is not FreshnessBucket:
            raise TypeError("interject_until must be a FreshnessBucket")


_DEFAULT_FRESHNESS_PROFILES: Mapping[FreshnessClass, FreshnessProfile] = MappingProxyType(
    {
        # A direct ambient observation is only useful while it is fresh.
        FreshnessClass.REACTIVE: FreshnessProfile(),
        # Trusted continuity can remain actionable for one aging bucket, but
        # unsolicited entry still needs a fresh opportunity.
        FreshnessClass.CONTINUITY: FreshnessProfile(
            respond_until=FreshnessBucket.AGING,
            interject_until=FreshnessBucket.FRESH,
        ),
        FreshnessClass.INTERNAL: FreshnessProfile(),
        FreshnessClass.TEMPORAL: FreshnessProfile(
            respond_until=FreshnessBucket.AGING,
            interject_until=FreshnessBucket.FRESH,
        ),
    }
)


class _FreshnessRank(IntEnum):
    FRESH = 0
    AGING = 1
    STALE = 2


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """Reviewable per-class freshness mapping with no universal timeout."""

    profiles: Mapping[FreshnessClass, FreshnessProfile] = _DEFAULT_FRESHNESS_PROFILES

    def __post_init__(self) -> None:
        normalized = dict(self.profiles)
        if set(normalized) != set(FreshnessClass):
            raise ValueError("freshness profiles must cover every FreshnessClass")
        if not all(type(key) is FreshnessClass for key in normalized):
            raise TypeError("freshness profile keys must be FreshnessClass values")
        if not all(type(value) is FreshnessProfile for value in normalized.values()):
            raise TypeError("freshness profiles must contain FreshnessProfile values")
        object.__setattr__(self, "profiles", MappingProxyType(normalized))

    def allows(
        self,
        event_class: FreshnessClass,
        decision: InterventionDecision,
        age: FreshnessBucket,
    ) -> bool:
        """Return whether the supplied age bucket remains eligible."""

        if type(event_class) is not FreshnessClass:
            raise TypeError("event_class must be a FreshnessClass")
        if type(decision) is not InterventionDecision:
            raise TypeError("decision must be an InterventionDecision")
        if type(age) is not FreshnessBucket:
            raise TypeError("age must be a FreshnessBucket")
        if age is FreshnessBucket.STALE:
            return False
        if decision is InterventionDecision.NONE:
            return True
        profile = self.profiles[event_class]
        maximum = (
            profile.respond_until
            if decision is InterventionDecision.RESPOND
            else profile.interject_until
        )
        return _FreshnessRank[age.name] <= _FreshnessRank[maximum.name]


@dataclass(frozen=True, slots=True)
class InterventionCandidate:
    """Advisory cognition output with no raw content or effect authority.

    ``decision`` is a semantic request, not permission to speak.  The
    candidate contains only bounded enums and reason codes; in particular it
    cannot carry a prompt, user text, planner output, or generated prose.
    """

    decision: InterventionDecision
    reason_codes: tuple[CognitionReasonCode, ...]
    semantic_value: InterventionValue | None = None

    def __post_init__(self) -> None:
        if type(self.decision) is not InterventionDecision:
            raise TypeError("decision must be an InterventionDecision")
        if type(self.reason_codes) is not tuple or not self.reason_codes:
            raise ValueError("intervention candidates require reason codes")
        if len(self.reason_codes) > MAX_INTERVENTION_REASON_CODES:
            raise ValueError("intervention candidate reason-code bound exceeded")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("intervention candidate reason codes must be unique")
        if not all(type(code) is CognitionReasonCode for code in self.reason_codes):
            raise TypeError("intervention candidate reason codes must contain CognitionReasonCode")

        value = self.semantic_value
        if value is None:
            value = {
                InterventionDecision.NONE: InterventionValue.NONE,
                InterventionDecision.RESPOND: InterventionValue.RESPONSE_OBLIGATION,
                InterventionDecision.INTERJECT: InterventionValue.MATERIAL_CONTRIBUTION,
            }[self.decision]
            object.__setattr__(self, "semantic_value", value)
        elif type(value) is not InterventionValue:
            raise TypeError("semantic_value must be an InterventionValue")
        expected = {
            InterventionDecision.NONE: InterventionValue.NONE,
            InterventionDecision.RESPOND: InterventionValue.RESPONSE_OBLIGATION,
            InterventionDecision.INTERJECT: InterventionValue.MATERIAL_CONTRIBUTION,
        }[self.decision]
        if value is not expected:
            raise ValueError("candidate decision and semantic_value do not agree")

    @property
    def requested_decision(self) -> InterventionDecision:
        """Name the decision as a request rather than as permission."""

        return self.decision


@dataclass(frozen=True, slots=True)
class SocialPermissionContext:
    """Trusted, bounded current state used for effect-time revalidation.

    This object intentionally has no payload, prompt, user text, planner text,
    score, timestamp, or generated content.  The owner of each route mints
    these coarse signals independently of external claims.
    """

    priority: SemanticPriority = SemanticPriority.NON_USER
    source_kind: SemanticSourceKind = SemanticSourceKind.EXTERNAL
    speaking_surface: SpeakingSurfaceState = SpeakingSurfaceState.UNAVAILABLE
    freshness_class: FreshnessClass = FreshnessClass.REACTIVE
    freshness: FreshnessBucket = FreshnessBucket.STALE
    activity: ActivityState = ActivityState.UNKNOWN
    floor: FloorState = FloorState.UNKNOWN
    recent_speech: RecentSpeechState = RecentSpeechState.CLEAR
    intervention_budget: InterventionBudgetState = InterventionBudgetState.AVAILABLE
    handling: HandlingState = HandlingState.UNKNOWN
    sensitivity: SocialSensitivity = SocialSensitivity.ORDINARY
    response_obligation: bool = False
    continuity_current: bool = False

    def __post_init__(self) -> None:
        if type(self.priority) is not SemanticPriority:
            raise TypeError("priority must be a SemanticPriority")
        if type(self.source_kind) is not SemanticSourceKind:
            raise TypeError("source_kind must be a SemanticSourceKind")
        for field_name, expected_type in (
            ("speaking_surface", SpeakingSurfaceState),
            ("freshness_class", FreshnessClass),
            ("freshness", FreshnessBucket),
            ("activity", ActivityState),
            ("floor", FloorState),
            ("recent_speech", RecentSpeechState),
            ("intervention_budget", InterventionBudgetState),
            ("handling", HandlingState),
            ("sensitivity", SocialSensitivity),
        ):
            if type(getattr(self, field_name)) is not expected_type:
                raise TypeError(f"{field_name} must be a {expected_type.__name__}")
        if type(self.response_obligation) is not bool:
            raise TypeError("response_obligation must be a bool")
        if type(self.continuity_current) is not bool:
            raise TypeError("continuity_current must be a bool")


# The conceptual name is retained as a simple alias; there is one contract,
# not a duplicate context type.
InterventionContext = SocialPermissionContext


@dataclass(frozen=True, slots=True)
class SocialPermissionResult:
    """The deterministic result of one current-state revalidation."""

    candidate_decision: InterventionDecision
    decision: InterventionDecision
    permitted: bool
    reason_codes: tuple[CognitionReasonCode, ...]

    def __post_init__(self) -> None:
        if type(self.candidate_decision) is not InterventionDecision:
            raise TypeError("candidate_decision must be an InterventionDecision")
        if type(self.decision) is not InterventionDecision:
            raise TypeError("decision must be an InterventionDecision")
        if type(self.permitted) is not bool:
            raise TypeError("permitted must be a bool")
        if type(self.reason_codes) is not tuple or not self.reason_codes:
            raise ValueError("social permission results require reason codes")
        if len(self.reason_codes) > MAX_INTERVENTION_REASON_CODES:
            raise ValueError("social permission reason-code bound exceeded")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("social permission reason codes must be unique")
        if not all(type(code) is CognitionReasonCode for code in self.reason_codes):
            raise TypeError("social permission reason codes must contain CognitionReasonCode")
        if self.permitted != (self.decision is not InterventionDecision.NONE):
            raise ValueError("permitted must match the resolved speaking decision")


class AmbientInterventionExcluded(ValueError):
    """Direct USER work is owned by the fixed conversation route, not E1."""


_INTERJECT_SUPPORTING_REASONS = frozenset(
    {
        CognitionReasonCode.CONTRADICTION,
        CognitionReasonCode.EVIDENCE_UPDATE,
        CognitionReasonCode.AMBIGUITY_MATERIAL,
        CognitionReasonCode.CRITICAL_EVENT,
        CognitionReasonCode.CONSTRAINT_FORGOTTEN,
        CognitionReasonCode.DISCUSSION_STUCK,
        CognitionReasonCode.UNIQUE_INFORMATION,
    }
)


class DeterministicInterventionPolicy:
    """Resolve semantic candidates through ordered social-permission rules.

    ``INTERJECT`` has a stricter contract than ``RESPOND``: it needs explicit
    material-contribution evidence plus a current, unresolved, fresh,
    backoff-free opportunity.  No weighted score, threshold jitter, keyword
    trigger, or model call is involved.
    """

    policy_id = "deterministic_intervention_v1"

    def __init__(self, *, freshness_policy: FreshnessPolicy | None = None) -> None:
        self._freshness_policy = freshness_policy or FreshnessPolicy()

    def evaluate(
        self,
        candidate: InterventionCandidate,
        context: SocialPermissionContext,
    ) -> SocialPermissionResult:
        """Revalidate one candidate against trusted current state."""

        if type(candidate) is not InterventionCandidate:
            raise TypeError("candidate must be an InterventionCandidate")
        if type(context) is not SocialPermissionContext:
            raise TypeError("context must be a SocialPermissionContext")
        if (
            context.priority is SemanticPriority.USER
            or context.source_kind is SemanticSourceKind.USER
        ):
            raise AmbientInterventionExcluded(
                "direct USER work is fixed by the conversation route and excluded from E1"
            )

        if context.speaking_surface is SpeakingSurfaceState.UNAVAILABLE:
            return _denied(candidate, CognitionReasonCode.NO_SPEAKING_SURFACE)
        if context.floor is FloorState.BUSY:
            return _denied(candidate, CognitionReasonCode.FLOOR_BUSY)
        if context.activity is not ActivityState.CURRENT:
            return _denied(candidate, CognitionReasonCode.STALE_CONTEXT)
        if not self._freshness_policy.allows(
            context.freshness_class, candidate.decision, context.freshness
        ):
            return _denied(candidate, CognitionReasonCode.STALE_CONTEXT)
        if context.intervention_budget is InterventionBudgetState.EXHAUSTED:
            return _denied(candidate, CognitionReasonCode.INTERVENTION_BUDGET_EXHAUSTED)

        if candidate.decision is InterventionDecision.NONE:
            return _allowed(candidate, InterventionDecision.NONE)

        if candidate.decision is InterventionDecision.RESPOND:
            if not (context.response_obligation or context.continuity_current):
                return _denied(candidate, CognitionReasonCode.NO_RESPONSE_OBLIGATION)
            return _allowed(
                candidate,
                InterventionDecision.RESPOND,
                CognitionReasonCode.RESPONSE_OBLIGATION,
            )

        # Unsolicited initiative is intentionally the strictest branch.
        if context.handling is HandlingState.HANDLED:
            return _denied(candidate, CognitionReasonCode.ALREADY_HANDLED)
        if context.handling is not HandlingState.UNRESOLVED:
            return _denied(candidate, CognitionReasonCode.STALE_CONTEXT)
        if context.sensitivity is SocialSensitivity.VULNERABLE:
            return _denied(candidate, CognitionReasonCode.VULNERABILITY)
        if context.recent_speech is RecentSpeechState.RECENT:
            return _denied(candidate, CognitionReasonCode.SOCIAL_BACKOFF)
        if not _INTERJECT_SUPPORTING_REASONS.intersection(candidate.reason_codes):
            return _denied(candidate, CognitionReasonCode.INTERRUPTION_COST_HIGH)
        if CognitionReasonCode.INTERRUPTION_COST_HIGH in candidate.reason_codes:
            return _denied(candidate, CognitionReasonCode.INTERRUPTION_COST_HIGH)
        return _allowed(candidate, InterventionDecision.INTERJECT)

    def revalidate(
        self,
        candidate: InterventionCandidate,
        context: SocialPermissionContext,
    ) -> SocialPermissionResult:
        """Explicitly named effect-time revalidation alias for future E2 callers."""

        return self.evaluate(candidate, context)

    def decide(
        self,
        candidate: InterventionCandidate,
        context: SocialPermissionContext,
    ) -> InterventionDecision:
        """Return only the allowed decision for small policy compositions."""

        return self.evaluate(candidate, context).decision


def _bounded_reasons(
    *reason_groups: tuple[CognitionReasonCode, ...],
) -> tuple[CognitionReasonCode, ...]:
    reasons: list[CognitionReasonCode] = []
    for group in reason_groups:
        for code in group:
            if code not in reasons and len(reasons) < MAX_INTERVENTION_REASON_CODES:
                reasons.append(code)
    return tuple(reasons)


def _denied(
    candidate: InterventionCandidate,
    reason: CognitionReasonCode,
) -> SocialPermissionResult:
    return SocialPermissionResult(
        candidate_decision=candidate.decision,
        decision=InterventionDecision.NONE,
        permitted=False,
        reason_codes=_bounded_reasons((reason,), candidate.reason_codes),
    )


def _allowed(
    candidate: InterventionCandidate,
    decision: InterventionDecision,
    *additional_reasons: CognitionReasonCode,
) -> SocialPermissionResult:
    reasons = _bounded_reasons(
        tuple(additional_reasons),
        candidate.reason_codes,
    )
    return SocialPermissionResult(
        candidate_decision=candidate.decision,
        decision=decision,
        permitted=decision is not InterventionDecision.NONE,
        reason_codes=reasons,
    )


__all__ = [
    "ActivityState",
    "AmbientInterventionExcluded",
    "DeterministicInterventionPolicy",
    "FloorState",
    "FreshnessBucket",
    "FreshnessClass",
    "FreshnessPolicy",
    "FreshnessProfile",
    "HandlingState",
    "InterventionCandidate",
    "InterventionContext",
    "InterventionValue",
    "InterventionBudgetState",
    "MAX_INTERVENTION_REASON_CODES",
    "RecentSpeechState",
    "SocialPermissionContext",
    "SocialPermissionResult",
    "SocialSensitivity",
    "SpeakingSurfaceState",
]
