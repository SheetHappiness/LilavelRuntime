"""Deterministic attention evidence extraction and policy.

Attention is deliberately narrower than intervention or disposition.  This
module only decides whether an admitted observation is worth cognition; the
existing runtime gate remains the only bridge to a cognition trigger.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from lilavel_core import (
    MAX_COGNITION_REASON_CODES,
    AttentionDecision,
    CognitionReasonCode,
)

from .contracts import (
    MAX_COGNITION_TRIGGER_OBSERVATIONS,
    NO_COGNITION,
    CognitionDecision,
    CognitionTrigger,
    Observation,
)

if TYPE_CHECKING:
    from .awareness import PeripheralAwarenessBuffer

__all__ = [
    "AttentionEvidence",
    "AttentionEvidenceExtractor",
    "AttentionInterestAffinity",
    "AttentionNovelty",
    "AttentionRelevance",
    "AttentionSignalProfile",
    "AttentionVerdict",
    "DeterministicAttentionCognitionGate",
    "DeterministicAttentionPolicy",
    "MAX_ATTENTION_EVENT_PROFILES",
    "MAX_ATTENTION_REASON_CODES",
]


MAX_ATTENTION_EVENT_PROFILES = 16
MAX_ATTENTION_EVENT_KIND_BYTES = 128
MAX_ATTENTION_REASON_CODES = MAX_COGNITION_REASON_CODES


class AttentionRelevance(StrEnum):
    """Trusted bounded relevance evidence."""

    UNKNOWN = "unknown"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class AttentionNovelty(StrEnum):
    """Trusted bounded novelty evidence."""

    UNKNOWN = "unknown"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class AttentionInterestAffinity(StrEnum):
    """Trusted character-interest evidence, when a runtime can establish it."""

    UNKNOWN = "unknown"
    NONE = "none"
    WEAK = "weak"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class AttentionSignalProfile:
    """Runtime-owned signals associated with one known event route.

    Profiles are configuration for the trusted runtime classifier.  They are
    keyed by exact event kinds and never read or copied values from a
    ``WorldEvent.payload``.  An adapter may only use a profile when its route
    semantics establish the signal independently of external payload data.
    """

    direct_address: bool = False
    critical_event: bool = False
    continuity: bool = False
    relevance: AttentionRelevance = AttentionRelevance.UNKNOWN
    novelty: AttentionNovelty = AttentionNovelty.UNKNOWN
    interest_affinity: AttentionInterestAffinity = AttentionInterestAffinity.UNKNOWN
    repetition: bool = False
    no_new_value: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "direct_address",
            "critical_event",
            "continuity",
            "repetition",
            "no_new_value",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be a bool")
        if type(self.relevance) is not AttentionRelevance:
            raise TypeError("relevance must be an AttentionRelevance")
        if type(self.novelty) is not AttentionNovelty:
            raise TypeError("novelty must be an AttentionNovelty")
        if type(self.interest_affinity) is not AttentionInterestAffinity:
            raise TypeError("interest_affinity must be an AttentionInterestAffinity")


@dataclass(frozen=True, slots=True)
class AttentionEvidence:
    """One immutable, bounded set of trusted evidence for an observation."""

    observation_id: str
    direct_address: bool = False
    critical_event: bool = False
    continuity: bool = False
    relevance: AttentionRelevance = AttentionRelevance.UNKNOWN
    novelty: AttentionNovelty = AttentionNovelty.UNKNOWN
    interest_affinity: AttentionInterestAffinity = AttentionInterestAffinity.UNKNOWN
    repetition: bool = False
    no_new_value: bool = False

    def __post_init__(self) -> None:
        if type(self.observation_id) is not str or not self.observation_id.strip():
            raise ValueError("observation_id must be non-empty text")
        AttentionSignalProfile(
            direct_address=self.direct_address,
            critical_event=self.critical_event,
            continuity=self.continuity,
            relevance=self.relevance,
            novelty=self.novelty,
            interest_affinity=self.interest_affinity,
            repetition=self.repetition,
            no_new_value=self.no_new_value,
        )


@dataclass(frozen=True, slots=True)
class AttentionVerdict:
    """A bounded per-observation attention result with explainable reasons."""

    observation_id: str
    decision: AttentionDecision
    reason_codes: tuple[CognitionReasonCode, ...]

    def __post_init__(self) -> None:
        if type(self.observation_id) is not str or not self.observation_id.strip():
            raise ValueError("observation_id must be non-empty text")
        if type(self.decision) is not AttentionDecision:
            raise TypeError("decision must be an AttentionDecision")
        if type(self.reason_codes) is not tuple or not self.reason_codes:
            raise ValueError("attention verdicts require reason codes")
        if len(self.reason_codes) > MAX_ATTENTION_REASON_CODES:
            raise ValueError("attention reason-code bound exceeded")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("attention reason codes must be unique")
        if not all(type(code) is CognitionReasonCode for code in self.reason_codes):
            raise TypeError("attention reason codes must contain CognitionReasonCode values")


class AttentionEvidenceExtractor:
    """Mint attention evidence from runtime-owned event-route semantics.

    The extractor intentionally ignores event trust labels and payloads.  The
    default known route is the existing direct-message adapter path.  Other
    profiles are opt-in runtime composition supplied by code that owns the
    corresponding event route, which keeps semantic claims out of external
    data.
    """

    __slots__ = ("_profiles",)
    _profiles: Mapping[str, AttentionSignalProfile]

    def __init__(
        self,
        trusted_event_profiles: Mapping[str, AttentionSignalProfile] | None = None,
    ) -> None:
        profiles: dict[str, AttentionSignalProfile] = {
            "direct_message": AttentionSignalProfile(direct_address=True),
        }
        if trusted_event_profiles is not None:
            for event_kind, profile in trusted_event_profiles.items():
                if (
                    type(event_kind) is not str
                    or not event_kind.strip()
                    or len(event_kind.encode("utf-8")) > MAX_ATTENTION_EVENT_KIND_BYTES
                ):
                    raise ValueError("trusted event profile kind must be bounded non-empty text")
                if type(profile) is not AttentionSignalProfile:
                    raise TypeError("trusted event profiles must contain signal profiles")
                if event_kind == "direct_message" and not profile.direct_address:
                    raise ValueError(
                        "the direct_message route must retain direct-address semantics"
                    )
                profiles[event_kind] = profile
        if len(profiles) > MAX_ATTENTION_EVENT_PROFILES:
            raise ValueError("attention event profile bound exceeded")
        object.__setattr__(self, "_profiles", MappingProxyType(profiles))

    def extract(self, observation: Observation) -> AttentionEvidence:
        """Extract only signals established by the observation's exact route."""

        if type(observation) is not Observation:
            raise TypeError("attention extraction accepts only Observations")
        profile = self._profiles.get(observation.event.kind, AttentionSignalProfile())
        return AttentionEvidence(
            observation_id=observation.observation_id,
            direct_address=profile.direct_address,
            critical_event=profile.critical_event,
            continuity=profile.continuity,
            relevance=profile.relevance,
            novelty=profile.novelty,
            interest_affinity=profile.interest_affinity,
            repetition=profile.repetition,
            no_new_value=profile.no_new_value,
        )


class DeterministicAttentionPolicy:
    """Apply ordered, reviewable attention rules without scoring or randomness."""

    def evaluate(self, evidence: AttentionEvidence) -> AttentionVerdict:
        if type(evidence) is not AttentionEvidence:
            raise TypeError("attention policy accepts only AttentionEvidence")
        reasons = _reason_codes(evidence)

        # Hard cognition-worthy evidence always outranks suppressors.
        if (
            evidence.direct_address
            or evidence.critical_event
            or evidence.continuity
            or (
                evidence.relevance is AttentionRelevance.HIGH
                and evidence.novelty is AttentionNovelty.HIGH
            )
        ):
            decision = AttentionDecision.THINK
        # Obvious noise and unchanged repetition are suppressed.
        elif evidence.relevance is AttentionRelevance.LOW or (
            evidence.repetition and evidence.no_new_value
        ):
            decision = AttentionDecision.DROP
        # Peripheral context, including affinity alone, is observed but not run.
        else:
            decision = AttentionDecision.NOTE

        return AttentionVerdict(evidence.observation_id, decision, reasons)


class _AwarenessAdmitter(Protocol):
    def admit(
        self,
        observation: Observation,
        verdict: AttentionVerdict,
        *,
        scope_id: str,
    ) -> object: ...


class DeterministicAttentionCognitionGate:
    """Bridge per-observation attention verdicts to the existing cognition seam."""

    def __init__(
        self,
        *,
        extractor: AttentionEvidenceExtractor | None = None,
        policy: DeterministicAttentionPolicy | None = None,
        max_observations: int = MAX_COGNITION_TRIGGER_OBSERVATIONS,
        awareness_buffer: PeripheralAwarenessBuffer | None = None,
        scope_id: str = "runtime",
    ) -> None:
        if (
            isinstance(max_observations, bool)
            or not 0 < max_observations <= MAX_COGNITION_TRIGGER_OBSERVATIONS
        ):
            raise ValueError("max_observations must be between 1 and the cognition trigger bound")
        self._extractor = extractor or AttentionEvidenceExtractor()
        self._policy = policy or DeterministicAttentionPolicy()
        self._max_observations = max_observations
        if type(scope_id) is not str or not scope_id.strip():
            raise ValueError("scope_id must be non-empty text")
        self._awareness_buffer: _AwarenessAdmitter | None = awareness_buffer
        self._awareness_scope_id = scope_id

    @property
    def awareness_buffer(self) -> _AwarenessAdmitter | None:
        """Return the optional runtime-owned NOTE admission dependency."""

        return self._awareness_buffer

    def bind_awareness_buffer(
        self,
        awareness_buffer: PeripheralAwarenessBuffer,
        *,
        scope_id: str,
    ) -> None:
        """Bind the runtime-owned NOTE owner without changing policy semantics."""

        if not callable(getattr(awareness_buffer, "admit", None)):
            raise TypeError("awareness_buffer must provide admit()")
        if type(scope_id) is not str or not scope_id.strip():
            raise ValueError("scope_id must be non-empty text")
        if self._awareness_buffer is not None and self._awareness_buffer is not awareness_buffer:
            raise ValueError("attention gate is already bound to another awareness buffer")
        self._awareness_buffer = awareness_buffer
        self._awareness_scope_id = scope_id

    def evaluate(self, observations: Sequence[Observation]) -> tuple[AttentionVerdict, ...]:
        """Return one independent verdict per unique observation in input order."""

        verdicts: list[AttentionVerdict] = []
        seen_ids: set[str] = set()
        for observation in observations:
            if type(observation) is not Observation:
                raise TypeError("cognition gates accept only Observations")
            if observation.observation_id in seen_ids:
                continue
            seen_ids.add(observation.observation_id)
            verdicts.append(self._policy.evaluate(self._extractor.extract(observation)))
        return tuple(verdicts)

    def decide(self, observations: Sequence[Observation]) -> CognitionDecision | CognitionTrigger:
        verdicts = self.evaluate(observations)
        self._admit_notes(observations, verdicts)
        thinking_ids = [
            verdict.observation_id
            for verdict in verdicts
            if verdict.decision is AttentionDecision.THINK
        ]
        if not thinking_ids:
            return NO_COGNITION

        # Keep direct-user work on its existing route.  Ambient notes/drops,
        # and ambient THINK items, never get smuggled into a direct trigger.
        direct_ids: list[str] = []
        seen_direct_ids: set[str] = set()
        for observation in observations:
            if (
                observation.observation_id in thinking_ids
                and observation.event.kind == "direct_message"
                and observation.observation_id not in seen_direct_ids
            ):
                seen_direct_ids.add(observation.observation_id)
                direct_ids.append(observation.observation_id)
        selected_ids = direct_ids or thinking_ids
        selected_ids = selected_ids[: self._max_observations]
        if direct_ids:
            return CognitionTrigger(tuple(selected_ids), reason="explicit_direct_message")
        selected_verdicts = {
            verdict.observation_id: verdict
            for verdict in verdicts
            if verdict.observation_id in selected_ids
        }
        reason_codes: list[str] = []
        for observation_id in selected_ids:
            for code in selected_verdicts[observation_id].reason_codes:
                if code.value not in reason_codes:
                    reason_codes.append(code.value)
        return CognitionTrigger(
            tuple(selected_ids),
            reason="attention:" + ",".join(reason_codes[:MAX_ATTENTION_REASON_CODES]),
        )

    def _admit_notes(
        self,
        observations: Sequence[Observation],
        verdicts: Sequence[AttentionVerdict],
    ) -> None:
        """Admit NOTE verdicts only; buffer failures fail closed to no retention."""

        buffer = self._awareness_buffer
        if buffer is None:
            return
        by_id: dict[str, Observation] = {}
        for observation in observations:
            by_id.setdefault(observation.observation_id, observation)
        for verdict in verdicts:
            if verdict.decision is not AttentionDecision.NOTE:
                continue
            observation = by_id[verdict.observation_id]
            try:
                buffer.admit(observation, verdict, scope_id=self._awareness_scope_id)
            except Exception:
                # Awareness is optional peripheral state.  A malformed or
                # unavailable buffer must never turn NOTE into cognition or
                # break an existing THINK/USER route.
                continue


def _reason_codes(evidence: AttentionEvidence) -> tuple[CognitionReasonCode, ...]:
    reasons: list[CognitionReasonCode] = []

    def add(code: CognitionReasonCode) -> None:
        if code not in reasons and len(reasons) < MAX_ATTENTION_REASON_CODES:
            reasons.append(code)

    if evidence.direct_address:
        add(CognitionReasonCode.DIRECT_ADDRESS)
    if evidence.critical_event:
        add(CognitionReasonCode.CRITICAL_EVENT)
    if evidence.continuity:
        add(CognitionReasonCode.SOCIAL_FOLLOWUP)
    if evidence.relevance is AttentionRelevance.HIGH:
        add(CognitionReasonCode.HIGH_RELEVANCE)
    if evidence.novelty is AttentionNovelty.HIGH:
        add(CognitionReasonCode.HIGH_NOVELTY)
    if evidence.interest_affinity in {
        AttentionInterestAffinity.WEAK,
        AttentionInterestAffinity.STRONG,
    }:
        add(CognitionReasonCode.INTEREST_AFFINITY)
    if evidence.repetition:
        add(CognitionReasonCode.REPETITION)
    if evidence.no_new_value:
        add(CognitionReasonCode.NO_NEW_VALUE)
    if evidence.relevance is AttentionRelevance.LOW:
        add(CognitionReasonCode.LOW_RELEVANCE)
    if not reasons:
        add(CognitionReasonCode.AMBIENT_CONTEXT)
        add(CognitionReasonCode.NOT_ADDRESSED)
    return tuple(reasons)
