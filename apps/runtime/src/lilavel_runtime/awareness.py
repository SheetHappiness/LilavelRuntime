"""Runtime-owned, bounded peripheral awareness for deterministic ``NOTE`` outcomes.

An :class:`AwarenessNote` is a short-lived record that an admitted observation
was noticed without being sent to cognition.  This module deliberately stores
only typed provenance, deterministic attention evidence, and bounded lifecycle
metadata.  It does not own conversation history, raw event payloads,
MindState, ObservationWindow, ContextFrame, memory, model generation, effects,
or temporal wake scheduling.

The buffer is process-local by design.  Awareness is working context rather
than memory and is allowed to disappear when the runtime process restarts.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
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
    "AwarenessCompaction",
    "AwarenessHandledAuthority",
    "AwarenessKey",
    "AwarenessNote",
    "AwarenessNoteStatus",
    "AwarenessReconciliation",
    "AwarenessReconciliationStatus",
    "AwarenessScope",
    "MAX_AWARENESS_ENVIRONMENT_BYTES",
    "MAX_AWARENESS_KEY_BYTES",
    "MAX_AWARENESS_NOTES_PER_SCOPE",
    "MAX_AWARENESS_NOTES_TOTAL",
    "MAX_AWARENESS_NOTE_ID_BYTES",
    "MAX_AWARENESS_OCCURRENCE_COUNT",
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
MAX_AWARENESS_KEY_BYTES = 128
MAX_AWARENESS_OCCURRENCE_COUNT = 1_000_000
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


class AwarenessNoteStatus(StrEnum):
    """Internal lifecycle state of a bounded awareness record."""

    ACTIVE = "active"
    HANDLED = "handled"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class AwarenessReconciliationStatus(StrEnum):
    """Outcome of trusted handled-state reconciliation."""

    HANDLED = "handled"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class AwarenessKey:
    """Optional trusted adapter/runtime identity metadata for one NOTE.

    ``dedup_key`` identifies equivalent occurrences.  ``supersession_key``
    identifies a state family in which a later NOTE replaces an older one.
    Either value may be omitted, but an empty key object is not meaningful.
    Plain strings are intentionally not accepted by ``PeripheralAwarenessBuffer``
    as a substitute for this typed boundary.
    """

    dedup_key: str | None = None
    supersession_key: str | None = None

    def __post_init__(self) -> None:
        if self.dedup_key is None and self.supersession_key is None:
            raise ValueError("awareness keys require dedup_key or supersession_key")
        if self.dedup_key is not None:
            _require_bounded_text(self.dedup_key, "dedup_key", MAX_AWARENESS_KEY_BYTES)
        if self.supersession_key is not None:
            _require_bounded_text(
                self.supersession_key,
                "supersession_key",
                MAX_AWARENESS_KEY_BYTES,
            )


@dataclass(frozen=True, slots=True)
class AwarenessScope:
    """Exact isolation key for one runtime awareness surface.

    ``scope_id`` is the runtime semantic scope.  ``environment_id`` and
    ``surface_id`` are retained separately because the same semantic scope can
    observe multiple environments and source subjects.  A buffer query must
    use all three values; there is no actor-wide cross-surface snapshot.
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
    """One immutable, bounded, provenance-traceable peripheral NOTE.

    Public active snapshots contain only ``ACTIVE`` notes.  Lifecycle fields
    remain on the immutable value so internal transitions cannot be represented
    as arbitrary mutable patches.  Handled and superseded records are removed
    by the next structural compaction; expiry is a separate transition.
    """

    note_id: str
    scope: AwarenessScope
    source_refs: tuple[str, ...]
    observed_at: datetime
    admitted_at: datetime
    expires_at: datetime
    reason_codes: tuple[CognitionReasonCode, ...]
    last_seen_at: datetime | None = None
    occurrence_count: int = 1
    dedup_key: str | None = None
    supersession_key: str | None = None
    status: AwarenessNoteStatus = AwarenessNoteStatus.ACTIVE
    handled_at: datetime | None = None
    superseded_by: str | None = None

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
        last_seen_at = self.last_seen_at
        if last_seen_at is None:
            object.__setattr__(self, "last_seen_at", self.admitted_at)
            last_seen_at = self.admitted_at
        else:
            _require_aware_datetime(last_seen_at, "last_seen_at")
        if self.expires_at <= last_seen_at:
            raise ValueError("expires_at must be after last_seen_at")
        if isinstance(self.occurrence_count, bool) or not (
            0 < self.occurrence_count <= MAX_AWARENESS_OCCURRENCE_COUNT
        ):
            raise ValueError("awareness occurrence count is outside its bound")
        if self.occurrence_count == 1 and self.admitted_at < self.observed_at:
            raise ValueError("admitted_at cannot precede observed_at for a new note")
        if self.dedup_key is not None:
            _require_bounded_text(self.dedup_key, "dedup_key", MAX_AWARENESS_KEY_BYTES)
        if self.supersession_key is not None:
            _require_bounded_text(
                self.supersession_key,
                "supersession_key",
                MAX_AWARENESS_KEY_BYTES,
            )
        if type(self.status) is not AwarenessNoteStatus:
            raise TypeError("status must be an AwarenessNoteStatus")
        if self.handled_at is not None:
            _require_aware_datetime(self.handled_at, "handled_at")
        if self.superseded_by is not None:
            _require_bounded_text(self.superseded_by, "superseded_by", MAX_AWARENESS_NOTE_ID_BYTES)
        if self.status is AwarenessNoteStatus.HANDLED:
            if self.handled_at is None or self.superseded_by is not None:
                raise ValueError("handled notes require handled_at and no superseded_by")
        elif self.status is AwarenessNoteStatus.SUPERSEDED:
            if self.superseded_by is None or self.handled_at is not None:
                raise ValueError("superseded notes require superseded_by and no handled_at")
        elif self.status is AwarenessNoteStatus.ACTIVE and (
            self.handled_at is not None or self.superseded_by is not None
        ):
            raise ValueError("active notes cannot carry terminal lifecycle fields")
        elif self.status is AwarenessNoteStatus.EXPIRED and (
            self.handled_at is not None or self.superseded_by is not None
        ):
            raise ValueError("expired notes cannot carry terminal lifecycle fields")

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
        """Return the current observation identity from provenance."""

        return self.source_refs[0].removeprefix("observation:")

    @property
    def event_id(self) -> str:
        """Return the current event identity from provenance."""

        return self.source_refs[1].removeprefix("event:")


@dataclass(frozen=True, slots=True)
class AwarenessCompaction:
    """Content-free structural removal report from one compaction pass."""

    expired_note_ids: tuple[str, ...] = ()
    handled_note_ids: tuple[str, ...] = ()
    superseded_note_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, values in (
            ("expired_note_ids", self.expired_note_ids),
            ("handled_note_ids", self.handled_note_ids),
            ("superseded_note_ids", self.superseded_note_ids),
        ):
            _require_note_id_tuple(values, name)

    @property
    def removed_count(self) -> int:
        return (
            len(self.expired_note_ids) + len(self.handled_note_ids) + len(self.superseded_note_ids)
        )

    @property
    def compacted_note_ids(self) -> tuple[str, ...]:
        return self.expired_note_ids + self.handled_note_ids + self.superseded_note_ids


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
    coalesced: bool = False
    superseded_note_ids: tuple[str, ...] = ()
    compacted_note_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.status) is not AwarenessAdmissionStatus:
            raise TypeError("status must be an AwarenessAdmissionStatus")
        if self.note is not None and type(self.note) is not AwarenessNote:
            raise TypeError("note must be an AwarenessNote")
        if self.status is AwarenessAdmissionStatus.ADMITTED and self.note is None:
            raise ValueError("admitted awareness results require a note")
        if self.status is AwarenessAdmissionStatus.REJECTED and self.note is not None:
            raise ValueError("rejected awareness results cannot carry a note")
        _require_note_id_tuple(self.evicted_note_ids, "evicted_note_ids")
        _require_note_id_tuple(self.superseded_note_ids, "superseded_note_ids")
        _require_note_id_tuple(self.compacted_note_ids, "compacted_note_ids")
        if type(self.coalesced) is not bool:
            raise TypeError("coalesced must be a bool")
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
    coalesced_count: int = 0
    superseded_count: int = 0
    handled_count: int = 0
    compacted_count: int = 0

    def __post_init__(self) -> None:
        if self.outcome not in {"admitted", "rejected", "failed"}:
            raise ValueError("invalid awareness outcome")
        for name, value in (
            ("scope_count", self.scope_count),
            ("total_count", self.total_count),
            ("evicted_count", self.evicted_count),
            ("expired_count", self.expired_count),
            ("coalesced_count", self.coalesced_count),
            ("superseded_count", self.superseded_count),
            ("handled_count", self.handled_count),
            ("compacted_count", self.compacted_count),
        ):
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.reason_code is not None:
            _require_bounded_text(self.reason_code, "reason_code", 128)


@dataclass(frozen=True, slots=True)
class AwarenessReconciliation:
    """Result of one trusted runtime handled-state transition."""

    status: AwarenessReconciliationStatus
    note_id: str
    scope_count: int = 0
    total_count: int = 0
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not AwarenessReconciliationStatus:
            raise TypeError("status must be an AwarenessReconciliationStatus")
        _require_bounded_text(self.note_id, "note_id", MAX_AWARENESS_NOTE_ID_BYTES)
        for value in (self.scope_count, self.total_count):
            if isinstance(value, bool) or value < 0:
                raise ValueError("awareness reconciliation counts must be non-negative")
        if self.reason_code is not None:
            _require_bounded_text(self.reason_code, "reason_code", 128)


class AwarenessHandledAuthority:
    """Process-local capability required to mark a note handled.

    The constructor requires a buffer-private token.  A model result or raw
    adapter value cannot reconstruct this authority through the public type.
    """

    __slots__ = ("_owner_token",)

    def __init__(self, owner_token: object) -> None:
        self._owner_token = owner_token

    def __repr__(self) -> str:
        return "AwarenessHandledAuthority()"

    def belongs_to(self, owner_token: object) -> bool:
        """Check ownership without exposing the private capability token."""

        return self._owner_token is owner_token


class PeripheralAwarenessBuffer:
    """Own short-lived NOTE outcomes in bounded, per-surface memory.

    Admission is synchronous and deterministic.  Active snapshots are ordered
    oldest-to-newest by ``last_seen_at`` with ``note_id`` as a stable tie
    breaker.  The default dedup identity is a hash of trusted runtime event
    identity and route metadata; it never reads payload values.  Validated
    runtime/adapter code may provide an :class:`AwarenessKey` for richer
    coalescing or explicit supersession.

    Duplicate coalescing keeps the original ``note_id`` and first
    ``admitted_at``, replaces the bounded provenance pair and reason codes with
    the latest trusted occurrence, refreshes ``last_seen_at``/TTL, and
    saturating-increments ``occurrence_count``.  A distinct note with the same
    explicit ``supersession_key`` in the same exact scope marks the older note
    superseded and retains only the newer active state after compaction.
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
        self._dedup_index: dict[tuple[AwarenessScope, str], str] = {}
        self._supersession_index: dict[tuple[AwarenessScope, str], str] = {}
        self._evidence: deque[AwarenessBufferEvidence] = deque(maxlen=evidence_capacity)
        self._next_note_sequence = 0
        self._authority_token = object()
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

    def handled_authority(self) -> AwarenessHandledAuthority:
        """Mint the buffer-bound authority for a trusted runtime call site."""

        return AwarenessHandledAuthority(self._authority_token)

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
        awareness_key: AwarenessKey | None = None,
    ) -> AwarenessAdmission:
        """Admit one trusted NOTE verdict without copying its event payload."""

        if type(observation) is not Observation:
            raise TypeError("awareness admission accepts only Observations")
        if type(verdict) is not AttentionVerdict:
            raise TypeError("awareness admission accepts only AttentionVerdict values")
        if verdict.observation_id != observation.observation_id:
            raise ValueError("attention verdict does not match the observation")
        if awareness_key is not None and type(awareness_key) is not AwarenessKey:
            raise TypeError("awareness_key must be an AwarenessKey")

        with self._lock:
            now = self._now()
            initial_compaction = self._compact(now, record_evidence=False)
            scope = self.scope_for(scope_id, observation)
            if verdict.decision is not AttentionDecision.NOTE:
                self._record_evidence(
                    AwarenessBufferEvidence(
                        "rejected",
                        self._active_scope_count(scope),
                        self._active_count(),
                        expired_count=len(initial_compaction.expired_note_ids),
                        compacted_count=initial_compaction.removed_count,
                        reason_code="decision_not_note",
                    )
                )
                return AwarenessAdmission(
                    AwarenessAdmissionStatus.REJECTED,
                    expired_count=len(initial_compaction.expired_note_ids),
                    reason_code="decision_not_note",
                    scope_count=self._active_scope_count(scope),
                    total_count=self._active_count(),
                    compacted_note_ids=initial_compaction.compacted_note_ids,
                )

            dedup_key, supersession_key = self._resolve_keys(
                scope, observation, verdict, awareness_key
            )
            duplicate_id = self._dedup_index.get((scope, dedup_key))
            if duplicate_id is not None:
                existing = self._notes.get(duplicate_id)
                if existing is not None and existing.status is AwarenessNoteStatus.ACTIVE:
                    updated = replace(
                        existing,
                        source_refs=(
                            f"observation:{observation.observation_id}",
                            f"event:{observation.event.event_id}",
                        ),
                        observed_at=now,
                        last_seen_at=now,
                        expires_at=now + self._ttl,
                        reason_codes=verdict.reason_codes,
                        occurrence_count=min(
                            MAX_AWARENESS_OCCURRENCE_COUNT,
                            existing.occurrence_count + 1,
                        ),
                    )
                    self._notes[duplicate_id] = updated
                    compacted = initial_compaction.compacted_note_ids
                    self._record_evidence(
                        AwarenessBufferEvidence(
                            "admitted",
                            self._active_scope_count(scope),
                            self._active_count(),
                            expired_count=len(initial_compaction.expired_note_ids),
                            reason_code="coalesced",
                            coalesced_count=1,
                            compacted_count=len(compacted),
                        )
                    )
                    return AwarenessAdmission(
                        AwarenessAdmissionStatus.ADMITTED,
                        note=updated,
                        expired_count=len(initial_compaction.expired_note_ids),
                        scope_count=self._active_scope_count(scope),
                        total_count=self._active_count(),
                        coalesced=True,
                        compacted_note_ids=compacted,
                    )

            self._next_note_sequence += 1
            note_id = f"awareness-note:{self._next_note_sequence}"
            superseded_ids: tuple[str, ...] = ()
            if supersession_key is not None:
                previous_id = self._supersession_index.get((scope, supersession_key))
                previous = self._notes.get(previous_id) if previous_id is not None else None
                if previous is not None and previous.status is AwarenessNoteStatus.ACTIVE:
                    self._notes[previous.note_id] = replace(
                        previous,
                        status=AwarenessNoteStatus.SUPERSEDED,
                        superseded_by=note_id,
                    )
                    self._dedup_index.pop((scope, previous.dedup_key or ""), None)
                    self._supersession_index.pop((scope, supersession_key), None)
                    superseded_ids = (previous.note_id,)

            note = AwarenessNote(
                note_id=note_id,
                scope=scope,
                source_refs=(
                    f"observation:{observation.observation_id}",
                    f"event:{observation.event.event_id}",
                ),
                observed_at=now,
                admitted_at=now,
                expires_at=now + self._ttl,
                reason_codes=verdict.reason_codes,
                last_seen_at=now,
                dedup_key=dedup_key,
                supersession_key=supersession_key,
            )
            self._notes[note.note_id] = note
            self._dedup_index[(scope, dedup_key)] = note.note_id
            if supersession_key is not None:
                self._supersession_index[(scope, supersession_key)] = note.note_id

            after_transition = self._compact(now, record_evidence=False)
            evicted: list[str] = []
            evicted.extend(self._enforce_bounds(scope))
            compacted_ids = _ordered_unique(
                (*initial_compaction.compacted_note_ids, *after_transition.compacted_note_ids)
            )
            self._record_evidence(
                AwarenessBufferEvidence(
                    "admitted",
                    self._active_scope_count(scope),
                    self._active_count(),
                    evicted_count=len(evicted),
                    expired_count=len(initial_compaction.expired_note_ids)
                    + len(after_transition.expired_note_ids),
                    reason_code="superseded" if superseded_ids else None,
                    superseded_count=len(superseded_ids),
                    compacted_count=len(compacted_ids),
                )
            )
            return AwarenessAdmission(
                AwarenessAdmissionStatus.ADMITTED,
                note=note,
                evicted_note_ids=tuple(evicted),
                expired_count=len(initial_compaction.expired_note_ids)
                + len(after_transition.expired_note_ids),
                scope_count=self._active_scope_count(scope),
                total_count=self._active_count(),
                superseded_note_ids=superseded_ids,
                compacted_note_ids=compacted_ids,
            )

    def snapshot(
        self, scope: AwarenessScope, *, now: datetime | None = None
    ) -> tuple[AwarenessNote, ...]:
        """Return an immutable oldest-to-newest active snapshot for one scope."""

        if type(scope) is not AwarenessScope:
            raise TypeError("scope must be an AwarenessScope")
        with self._lock:
            self._compact(self._now() if now is None else _validated_now(now))
            return self._active_snapshot(scope)

    def snapshot_active(
        self, scope: AwarenessScope, *, now: datetime | None = None
    ) -> tuple[AwarenessNote, ...]:
        """Read one exact active scope without lifecycle mutation.

        Unlike the general lifecycle ``snapshot`` operation, this seam is used
        by context projection. It filters expired records in the returned
        immutable view but does not compact records or append evidence.
        """

        if type(scope) is not AwarenessScope:
            raise TypeError("scope must be an AwarenessScope")
        current = self._now() if now is None else _validated_now(now)
        with self._lock:
            return self._active_snapshot(scope, now=current)

    def count(self, scope: AwarenessScope, *, now: datetime | None = None) -> int:
        """Return the current bounded active count for one exact scope."""

        return len(self.snapshot(scope, now=now))

    def total_count(self, *, now: datetime | None = None) -> int:
        """Return the current bounded active count after deterministic compaction."""

        with self._lock:
            self._compact(self._now() if now is None else _validated_now(now))
            return self._active_count()

    def compact(self, *, now: datetime | None = None) -> AwarenessCompaction:
        """Remove expired, handled, and superseded records deterministically."""

        with self._lock:
            return self._compact(self._now() if now is None else _validated_now(now))

    def mark_handled(
        self,
        note_id: str,
        *,
        authority: AwarenessHandledAuthority,
        now: datetime | None = None,
    ) -> AwarenessReconciliation:
        """Mark one active note handled through the trusted runtime authority."""

        _require_bounded_text(note_id, "note_id", MAX_AWARENESS_NOTE_ID_BYTES)
        self._validate_authority(authority)
        with self._lock:
            current = self._compact(self._now() if now is None else _validated_now(now))
            note = self._notes.get(note_id)
            if note is None or note.status is not AwarenessNoteStatus.ACTIVE:
                self._record_evidence(
                    AwarenessBufferEvidence(
                        "rejected",
                        self._active_scope_count(note.scope) if note is not None else 0,
                        self._active_count(),
                        expired_count=len(current.expired_note_ids),
                        reason_code="handled_note_not_active",
                        compacted_count=current.removed_count,
                    )
                )
                return AwarenessReconciliation(
                    AwarenessReconciliationStatus.NOT_FOUND,
                    note_id,
                    scope_count=self._active_scope_count(note.scope) if note is not None else 0,
                    total_count=self._active_count(),
                    reason_code="handled_note_not_active",
                )

            handled_at = self._now() if now is None else _validated_now(now)
            self._notes[note_id] = replace(
                note,
                status=AwarenessNoteStatus.HANDLED,
                handled_at=handled_at,
            )
            self._dedup_index.pop((note.scope, note.dedup_key or ""), None)
            if note.supersession_key is not None:
                self._supersession_index.pop((note.scope, note.supersession_key), None)
            self._record_evidence(
                AwarenessBufferEvidence(
                    "admitted",
                    self._active_scope_count(note.scope),
                    self._active_count(),
                    expired_count=len(current.expired_note_ids),
                    reason_code="handled",
                    handled_count=1,
                    compacted_count=current.removed_count,
                )
            )
            return AwarenessReconciliation(
                AwarenessReconciliationStatus.HANDLED,
                note_id,
                scope_count=self._active_scope_count(note.scope),
                total_count=self._active_count(),
            )

    def mark_handled_by_key(
        self,
        scope: AwarenessScope,
        awareness_key: AwarenessKey,
        *,
        authority: AwarenessHandledAuthority,
        now: datetime | None = None,
    ) -> AwarenessReconciliation:
        """Handle an active note by an explicit scoped dedup key."""

        if type(scope) is not AwarenessScope:
            raise TypeError("scope must be an AwarenessScope")
        if type(awareness_key) is not AwarenessKey:
            raise TypeError("awareness_key must be an AwarenessKey")
        if awareness_key.dedup_key is None:
            raise ValueError("mark_handled_by_key requires a dedup_key")
        self._validate_authority(authority)
        with self._lock:
            self._compact(self._now() if now is None else _validated_now(now))
            note_id = self._dedup_index.get((scope, awareness_key.dedup_key))
        if note_id is None:
            digest = sha256(awareness_key.dedup_key.encode("utf-8")).hexdigest()[:32]
            missing_id = f"awareness-key:{digest}"
            return AwarenessReconciliation(
                AwarenessReconciliationStatus.NOT_FOUND,
                missing_id,
                scope_count=0,
                total_count=self.total_count(now=now),
                reason_code="handled_key_not_found",
            )
        return self.mark_handled(note_id, authority=authority, now=now)

    def clear_scope(self, scope: AwarenessScope, *, now: datetime | None = None) -> int:
        """Clear one exact scope for lifecycle teardown or explicit reset."""

        if type(scope) is not AwarenessScope:
            raise TypeError("scope must be an AwarenessScope")
        with self._lock:
            self._compact(self._now() if now is None else _validated_now(now))
            removed = 0
            for note_id, note in tuple(self._notes.items()):
                if note.scope != scope:
                    continue
                self._remove_note(note_id)
                removed += 1
            return removed

    def evidence(self) -> tuple[AwarenessBufferEvidence, ...]:
        """Return bounded content-free admission/overflow/expiry evidence."""

        with self._lock:
            return tuple(self._evidence)

    def _now(self) -> datetime:
        value = self._clock() if callable(self._clock) else self._clock.now()
        return _validated_now(value)

    def _resolve_keys(
        self,
        scope: AwarenessScope,
        observation: Observation,
        verdict: AttentionVerdict,
        awareness_key: AwarenessKey | None,
    ) -> tuple[str, str | None]:
        explicit_dedup = awareness_key.dedup_key if awareness_key is not None else None
        dedup_key = explicit_dedup or _default_dedup_key(scope, observation, verdict)
        supersession_key = awareness_key.supersession_key if awareness_key is not None else None
        _require_bounded_text(dedup_key, "dedup_key", MAX_AWARENESS_KEY_BYTES)
        if supersession_key is not None:
            _require_bounded_text(
                supersession_key,
                "supersession_key",
                MAX_AWARENESS_KEY_BYTES,
            )
        return dedup_key, supersession_key

    def _compact(self, now: datetime, *, record_evidence: bool = True) -> AwarenessCompaction:
        expired: list[str] = []
        handled: list[str] = []
        superseded: list[str] = []
        for note_id, note in tuple(self._notes.items()):
            if note.status is AwarenessNoteStatus.HANDLED:
                handled.append(note_id)
            elif note.status is AwarenessNoteStatus.SUPERSEDED:
                superseded.append(note_id)
            elif note.status is AwarenessNoteStatus.ACTIVE and note.expires_at <= now:
                expired.append(note_id)
        for note_id in (*expired, *handled, *superseded):
            self._remove_note(note_id)
        result = AwarenessCompaction(tuple(expired), tuple(handled), tuple(superseded))
        if record_evidence and result.removed_count:
            reason = (
                "expired"
                if result.expired_note_ids
                else "handled"
                if result.handled_note_ids
                else "superseded"
            )
            self._record_evidence(
                AwarenessBufferEvidence(
                    "admitted",
                    self._active_count(),
                    self._active_count(),
                    expired_count=len(result.expired_note_ids),
                    reason_code=reason,
                    handled_count=len(result.handled_note_ids),
                    superseded_count=len(result.superseded_note_ids),
                    compacted_count=result.removed_count,
                )
            )
        return result

    def _enforce_bounds(self, scope: AwarenessScope) -> list[str]:
        evicted: list[str] = []
        while self._active_scope_count(scope) > self._per_scope_capacity:
            oldest = self._oldest_active_id(scope)
            if oldest is None:
                break
            evicted.append(self._remove_note(oldest))
        while self._active_count() > self._total_capacity:
            oldest = self._oldest_active_id()
            if oldest is None:
                break
            evicted.append(self._remove_note(oldest))
        return evicted

    def _oldest_active_id(self, scope: AwarenessScope | None = None) -> str | None:
        candidates = (
            note
            for note in self._notes.values()
            if note.status is AwarenessNoteStatus.ACTIVE and (scope is None or note.scope == scope)
        )
        oldest = min(candidates, key=_note_order_key, default=None)
        return None if oldest is None else oldest.note_id

    def _remove_note(self, note_id: str) -> str:
        note = self._notes.pop(note_id)
        if note.dedup_key is not None:
            dedup_index_key = (note.scope, note.dedup_key)
            if self._dedup_index.get(dedup_index_key) == note_id:
                self._dedup_index.pop(dedup_index_key, None)
        if note.supersession_key is not None:
            supersession_index_key = (note.scope, note.supersession_key)
            if self._supersession_index.get(supersession_index_key) == note_id:
                self._supersession_index.pop(supersession_index_key, None)
        return note.note_id

    def _active_snapshot(
        self, scope: AwarenessScope, *, now: datetime | None = None
    ) -> tuple[AwarenessNote, ...]:
        return tuple(
            sorted(
                (
                    note
                    for note in self._notes.values()
                    if note.scope == scope
                    and note.status is AwarenessNoteStatus.ACTIVE
                    and (now is None or note.expires_at > now)
                ),
                key=_note_order_key,
            )
        )

    def _active_scope_count(self, scope: AwarenessScope) -> int:
        return sum(
            note.status is AwarenessNoteStatus.ACTIVE and note.scope == scope
            for note in self._notes.values()
        )

    def _active_count(self) -> int:
        return sum(note.status is AwarenessNoteStatus.ACTIVE for note in self._notes.values())

    def _validate_authority(self, authority: AwarenessHandledAuthority) -> None:
        if type(authority) is not AwarenessHandledAuthority:
            raise TypeError("authority must be an AwarenessHandledAuthority")
        if not authority.belongs_to(self._authority_token):
            raise ValueError("authority belongs to another awareness buffer")

    def _record_evidence(self, evidence: AwarenessBufferEvidence) -> None:
        self._evidence.append(evidence)


def _default_dedup_key(
    scope: AwarenessScope,
    observation: Observation,
    verdict: AttentionVerdict,
) -> str:
    """Hash only bounded runtime metadata, never the external event payload."""

    material = "\x1f".join(
        (
            scope.scope_id,
            scope.environment_id,
            scope.surface_id or "",
            observation.event.event_id,
            observation.event.kind,
            *(code.value for code in verdict.reason_codes),
        )
    ).encode("utf-8")
    return sha256(material).hexdigest()


def _note_order_key(note: AwarenessNote) -> tuple[datetime, str]:
    return (note.last_seen_at or note.admitted_at, note.note_id)


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _require_note_id_tuple(values: tuple[str, ...], name: str) -> None:
    if type(values) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    if len(values) > MAX_AWARENESS_NOTES_TOTAL:
        raise ValueError(f"{name} bound exceeded")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must contain unique note IDs")
    for value in values:
        _require_bounded_text(value, f"{name} item", MAX_AWARENESS_NOTE_ID_BYTES)


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
