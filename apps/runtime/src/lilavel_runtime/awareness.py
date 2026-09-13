"""Runtime-owned, bounded peripheral awareness for deterministic ``NOTE`` outcomes.

An :class:`AwarenessNote` is a short-lived record that an admitted observation
was noticed without being sent to cognition.  This module deliberately stores
only typed provenance and deterministic attention evidence.  It does not own
conversation history, raw event payloads, MindState, ObservationWindow,
ContextFrame, memory, model generation, effects, or temporal wake scheduling.

The buffer is process-local by design.  Awareness is working context rather
than memory and is allowed to disappear when the runtime process restarts.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Literal, Protocol

from lilavel_core import AttentionDecision, CognitionReasonCode

from .attention import AttentionVerdict
from .contracts import Observation

__all__ = [
    "AWARENESS_NOTE_TTL",
    "AwarenessAdmission",
    "AwarenessAdmissionStatus",
    "AwarenessBufferEvidence",
    "AwarenessClock",
    "AwarenessClockSource",
    "AwarenessNote",
    "AwarenessScope",
    "MAX_AWARENESS_ENVIRONMENT_BYTES",
    "MAX_AWARENESS_NOTES_PER_SCOPE",
    "MAX_AWARENESS_NOTES_TOTAL",
    "MAX_AWARENESS_NOTE_ID_BYTES",
    "MAX_AWARENESS_REASON_CODES",
    "MAX_AWARENESS_SCOPE_ID_BYTES",
    "MAX_AWARENESS_SOURCE_REF_BYTES",
    "MAX_AWARENESS_SOURCE_REFS",
    "MAX_AWARENESS_SURFACE_BYTES",
    "PeripheralAwarenessBuffer",
]


MAX_AWARENESS_NOTES_PER_SCOPE = 16
MAX_AWARENESS_NOTES_TOTAL = 64
MAX_AWARENESS_SOURCE_REFS = 2
MAX_AWARENESS_REASON_CODES = 8
MAX_AWARENESS_NOTE_ID_BYTES = 128
MAX_AWARENESS_SCOPE_ID_BYTES = 128
MAX_AWARENESS_ENVIRONMENT_BYTES = 128
MAX_AWARENESS_SURFACE_BYTES = 128
MAX_AWARENESS_SOURCE_REF_BYTES = 128
MAX_AWARENESS_EVIDENCE = 256
AWARENESS_NOTE_TTL = timedelta(minutes=5)


class AwarenessClock(Protocol):
    """Injected timezone-aware clock used for deterministic note retention."""

    def now(self) -> datetime: ...


type AwarenessClockSource = Callable[[], datetime] | AwarenessClock
AwarenessOutcome = Literal["admitted", "rejected", "failed"]


class AwarenessAdmissionStatus(StrEnum):
    """Outcome of one typed NOTE admission attempt."""

    ADMITTED = "admitted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class AwarenessScope:
    """Exact isolation key for one runtime awareness surface.

    ``scope_id`` is the runtime semantic scope.  ``environment_id`` and
    ``surface_id`` are retained separately because the same semantic scope can
    observe multiple environments and source subjects.  A buffer query must
    use all three values; there is no actor-wide cross-surface snapshot in A.
    """

    scope_id: str
    environment_id: str
    surface_id: str | None = None

    def __post_init__(self) -> None:
        _require_bounded_text(self.scope_id, "scope_id", MAX_AWARENESS_SCOPE_ID_BYTES)
        _require_bounded_text(
            self.environment_id, "environment_id", MAX_AWARENESS_ENVIRONMENT_BYTES
        )
        if self.surface_id is not None:
            _require_bounded_text(self.surface_id, "surface_id", MAX_AWARENESS_SURFACE_BYTES)

    @classmethod
    def from_observation(cls, scope_id: str, observation: Observation) -> AwarenessScope:
        """Derive the exact key from trusted runtime observation provenance."""

        if type(observation) is not Observation:
            raise TypeError("observation must be an Observation")
        return cls(scope_id, observation.event.source.environment, observation.event.source.subject)


@dataclass(frozen=True, slots=True)
class AwarenessNote:
    """One immutable, bounded, provenance-traceable peripheral NOTE."""

    note_id: str
    scope: AwarenessScope
    source_refs: tuple[str, ...]
    observed_at: datetime
    admitted_at: datetime
    expires_at: datetime
    reason_codes: tuple[CognitionReasonCode, ...]

    def __post_init__(self) -> None:
        _require_bounded_text(self.note_id, "note_id", MAX_AWARENESS_NOTE_ID_BYTES)
        if type(self.scope) is not AwarenessScope:
            raise TypeError("scope must be an AwarenessScope")
        if type(self.source_refs) is not tuple or not self.source_refs:
            raise ValueError("awareness notes require source references")
        if len(self.source_refs) > MAX_AWARENESS_SOURCE_REFS:
            raise ValueError("awareness source-reference bound exceeded")
        if len(set(self.source_refs)) != len(self.source_refs):
            raise ValueError("awareness source references must be unique")
        if not self.source_refs[0].startswith("observation:"):
            raise ValueError("first awareness source reference must be an observation")
        if len(self.source_refs) != 2 or not self.source_refs[1].startswith("event:"):
            raise ValueError("awareness notes require observation and event provenance")
        if not self.source_refs[0].removeprefix("observation:").strip():
            raise ValueError("observation provenance must identify an observation")
        if not self.source_refs[1].removeprefix("event:").strip():
            raise ValueError("event provenance must identify an event")
        for source_ref in self.source_refs:
            _require_bounded_text(source_ref, "source_ref", MAX_AWARENESS_SOURCE_REF_BYTES)
        for name, value in (
            ("observed_at", self.observed_at),
            ("admitted_at", self.admitted_at),
            ("expires_at", self.expires_at),
        ):
            _require_aware_datetime(value, name)
        if self.admitted_at < self.observed_at:
            raise ValueError("admitted_at cannot precede observed_at")
        if self.expires_at <= self.admitted_at:
            raise ValueError("expires_at must be after admitted_at")
        if type(self.reason_codes) is not tuple or not self.reason_codes:
            raise ValueError("awareness notes require reason codes")
        if len(self.reason_codes) > MAX_AWARENESS_REASON_CODES:
            raise ValueError("awareness reason-code bound exceeded")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("awareness reason codes must be unique")
        if not all(type(code) is CognitionReasonCode for code in self.reason_codes):
            raise TypeError("awareness reason codes must contain CognitionReasonCode values")

    @property
    def scope_id(self) -> str:
        """Expose the runtime semantic scope without flattening the key."""

        return self.scope.scope_id

    @property
    def environment_id(self) -> str:
        return self.scope.environment_id

    @property
    def surface_id(self) -> str | None:
        return self.scope.surface_id

    @property
    def observation_id(self) -> str:
        """Return the observation identity from the first source reference."""

        return self.source_refs[0].removeprefix("observation:")

    @property
    def event_id(self) -> str:
        """Return the event identity from the second source reference."""

        if len(self.source_refs) < 2:
            return ""
        return self.source_refs[1].removeprefix("event:")


@dataclass(frozen=True, slots=True)
class AwarenessAdmission:
    """Content-free result of one buffer admission attempt."""

    status: AwarenessAdmissionStatus
    note: AwarenessNote | None = field(default=None, repr=False)
    evicted_note_ids: tuple[str, ...] = ()
    expired_count: int = 0
    reason_code: str | None = None
    scope_count: int = 0
    total_count: int = 0

    def __post_init__(self) -> None:
        if type(self.status) is not AwarenessAdmissionStatus:
            raise TypeError("status must be an AwarenessAdmissionStatus")
        if self.note is not None and type(self.note) is not AwarenessNote:
            raise TypeError("note must be an AwarenessNote")
        if self.status is AwarenessAdmissionStatus.ADMITTED and self.note is None:
            raise ValueError("admitted awareness results require a note")
        if self.status is AwarenessAdmissionStatus.REJECTED and self.note is not None:
            raise ValueError("rejected awareness results cannot carry a note")
        if type(self.evicted_note_ids) is not tuple:
            raise TypeError("evicted_note_ids must be a tuple")
        for value in (self.expired_count, self.scope_count, self.total_count):
            if isinstance(value, bool) or value < 0:
                raise ValueError("awareness admission counts must be non-negative")
        if self.reason_code is not None:
            _require_bounded_text(self.reason_code, "reason_code", 128)


@dataclass(frozen=True, slots=True)
class AwarenessBufferEvidence:
    """Bounded content-free observability for one awareness operation."""

    outcome: AwarenessOutcome
    scope_count: int
    total_count: int
    evicted_count: int = 0
    expired_count: int = 0
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in {"admitted", "rejected", "failed"}:
            raise ValueError("invalid awareness outcome")
        for name, value in (
            ("scope_count", self.scope_count),
            ("total_count", self.total_count),
            ("evicted_count", self.evicted_count),
            ("expired_count", self.expired_count),
        ):
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.reason_code is not None:
            _require_bounded_text(self.reason_code, "reason_code", 128)


class PeripheralAwarenessBuffer:
    """Own short-lived NOTE outcomes in bounded, per-surface memory.

    Admission is synchronous and deterministic.  Notes are ordered oldest to
    newest in snapshots.  Per-scope overflow evicts the oldest note in that
    exact scope; global overflow evicts the oldest note across all scopes.
    Duplicate observations are not deduplicated in A: each NOTE admission
    attempt receives a new process-local note identity.  Deduplication and
    supersession belong to AWARE-V1-B.
    """

    def __init__(
        self,
        *,
        clock: AwarenessClockSource | None = None,
        ttl: timedelta = AWARENESS_NOTE_TTL,
        per_scope_capacity: int = MAX_AWARENESS_NOTES_PER_SCOPE,
        total_capacity: int = MAX_AWARENESS_NOTES_TOTAL,
        evidence_capacity: int = MAX_AWARENESS_EVIDENCE,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("awareness TTL must be positive")
        if (
            isinstance(per_scope_capacity, bool)
            or not 0 < per_scope_capacity <= MAX_AWARENESS_NOTES_PER_SCOPE
        ):
            raise ValueError("per_scope_capacity is outside the awareness bound")
        if isinstance(total_capacity, bool) or not 0 < total_capacity <= MAX_AWARENESS_NOTES_TOTAL:
            raise ValueError("total_capacity is outside the awareness bound")
        if (
            isinstance(evidence_capacity, bool)
            or not 0 < evidence_capacity <= MAX_AWARENESS_EVIDENCE
        ):
            raise ValueError("evidence_capacity is outside the awareness bound")
        if clock is not None and not callable(clock) and not callable(getattr(clock, "now", None)):
            raise TypeError("clock must be callable or expose now()")
        self._clock = clock or _utc_now
        self._ttl = ttl
        self._per_scope_capacity = per_scope_capacity
        self._total_capacity = total_capacity
        self._notes: OrderedDict[str, AwarenessNote] = OrderedDict()
        self._scope_notes: dict[AwarenessScope, OrderedDict[str, None]] = {}
        self._evidence: deque[AwarenessBufferEvidence] = deque(maxlen=evidence_capacity)
        self._next_note_sequence = 0
        self._lock = RLock()

    @property
    def per_scope_capacity(self) -> int:
        return self._per_scope_capacity

    @property
    def total_capacity(self) -> int:
        return self._total_capacity

    @property
    def ttl(self) -> timedelta:
        return self._ttl

    @staticmethod
    def scope_for(scope_id: str, observation: Observation) -> AwarenessScope:
        """Return the exact environment/surface key for an observation."""

        return AwarenessScope.from_observation(scope_id, observation)

    def admit(
        self,
        observation: Observation,
        verdict: AttentionVerdict,
        *,
        scope_id: str,
    ) -> AwarenessAdmission:
        """Admit one trusted NOTE verdict without copying its event payload."""

        if type(observation) is not Observation:
            raise TypeError("awareness admission accepts only Observations")
        if type(verdict) is not AttentionVerdict:
            raise TypeError("awareness admission accepts only AttentionVerdict values")
        if verdict.observation_id != observation.observation_id:
            raise ValueError("attention verdict does not match the observation")

        with self._lock:
            now = self._now()
            expired_count = self._expire(now)
            scope = self.scope_for(scope_id, observation)
            if verdict.decision is not AttentionDecision.NOTE:
                self._record_evidence(
                    AwarenessBufferEvidence(
                        "rejected",
                        self._scope_count(scope),
                        len(self._notes),
                        expired_count=expired_count,
                        reason_code="decision_not_note",
                    )
                )
                return AwarenessAdmission(
                    AwarenessAdmissionStatus.REJECTED,
                    expired_count=expired_count,
                    reason_code="decision_not_note",
                    scope_count=self._scope_count(scope),
                    total_count=len(self._notes),
                )

            self._next_note_sequence += 1
            note = AwarenessNote(
                note_id=f"awareness-note:{self._next_note_sequence}",
                scope=scope,
                source_refs=(
                    f"observation:{observation.observation_id}",
                    f"event:{observation.event.event_id}",
                ),
                observed_at=now,
                admitted_at=now,
                expires_at=now + self._ttl,
                reason_codes=verdict.reason_codes,
            )
            self._notes[note.note_id] = note
            self._scope_notes.setdefault(scope, OrderedDict())[note.note_id] = None
            evicted: list[str] = []
            scope_notes = self._scope_notes[scope]
            while len(scope_notes) > self._per_scope_capacity:
                evicted.append(self._evict_note(scope_notes.popitem(last=False)[0]))
            while len(self._notes) > self._total_capacity:
                oldest_note_id = next(iter(self._notes))
                evicted.append(self._evict_note(oldest_note_id))
            self._record_evidence(
                AwarenessBufferEvidence(
                    "admitted",
                    self._scope_count(scope),
                    len(self._notes),
                    evicted_count=len(evicted),
                    expired_count=expired_count,
                )
            )
            return AwarenessAdmission(
                AwarenessAdmissionStatus.ADMITTED,
                note=note,
                evicted_note_ids=tuple(evicted),
                expired_count=expired_count,
                scope_count=self._scope_count(scope),
                total_count=len(self._notes),
            )

    def snapshot(
        self, scope: AwarenessScope, *, now: datetime | None = None
    ) -> tuple[AwarenessNote, ...]:
        """Return an immutable oldest-to-newest snapshot for one exact scope."""

        if type(scope) is not AwarenessScope:
            raise TypeError("scope must be an AwarenessScope")
        with self._lock:
            self._expire(self._now() if now is None else _validated_now(now))
            scope_notes = self._scope_notes.get(scope)
            if scope_notes is None:
                return ()
            return tuple(self._notes[note_id] for note_id in scope_notes)

    def count(self, scope: AwarenessScope, *, now: datetime | None = None) -> int:
        """Return the current bounded count for one exact scope."""

        return len(self.snapshot(scope, now=now))

    def total_count(self, *, now: datetime | None = None) -> int:
        """Return the current process-local total after deterministic expiry."""

        with self._lock:
            self._expire(self._now() if now is None else _validated_now(now))
            return len(self._notes)

    def clear_scope(self, scope: AwarenessScope, *, now: datetime | None = None) -> int:
        """Clear one exact scope for lifecycle teardown or explicit reset."""

        if type(scope) is not AwarenessScope:
            raise TypeError("scope must be an AwarenessScope")
        with self._lock:
            self._expire(self._now() if now is None else _validated_now(now))
            scope_notes = self._scope_notes.pop(scope, None)
            if scope_notes is None:
                return 0
            removed = 0
            for note_id in tuple(scope_notes):
                if self._notes.pop(note_id, None) is not None:
                    removed += 1
            return removed

    def evidence(self) -> tuple[AwarenessBufferEvidence, ...]:
        """Return bounded content-free admission/overflow/expiry evidence."""

        with self._lock:
            return tuple(self._evidence)

    def _now(self) -> datetime:
        value = self._clock() if callable(self._clock) else self._clock.now()
        return _validated_now(value)

    def _expire(self, now: datetime) -> int:
        expired = [note_id for note_id, note in self._notes.items() if note.expires_at <= now]
        for note_id in expired:
            self._evict_note(note_id)
        if expired:
            self._record_evidence(
                AwarenessBufferEvidence(
                    "admitted",
                    0,
                    len(self._notes),
                    expired_count=len(expired),
                    reason_code="expired",
                )
            )
        return len(expired)

    def _evict_note(self, note_id: str) -> str:
        note = self._notes.pop(note_id)
        scope_notes = self._scope_notes[note.scope]
        scope_notes.pop(note_id, None)
        if not scope_notes:
            del self._scope_notes[note.scope]
        return note.note_id

    def _scope_count(self, scope: AwarenessScope) -> int:
        scope_notes = self._scope_notes.get(scope)
        return 0 if scope_notes is None else len(scope_notes)

    def _record_evidence(self, evidence: AwarenessBufferEvidence) -> None:
        self._evidence.append(evidence)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validated_now(value: datetime) -> datetime:
    if type(value) is not datetime:
        raise TypeError("awareness clock must return a datetime")
    _require_aware_datetime(value, "clock value")
    return value


def _require_aware_datetime(value: datetime, name: str) -> None:
    if type(value) is not datetime:
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_bounded_text(value: str, name: str, max_bytes: int) -> None:
    if type(value) is not str:
        raise TypeError(f"{name} must be text")
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} contains a control character")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} exceeds its bound")
