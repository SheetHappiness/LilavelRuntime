"""Runtime-owned one-shot temporal intentions for MIND-1E."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from threading import RLock
from typing import Protocol

from .contracts import (
    CognitionTrigger,
    CognitionTriggerSource,
    TemporalProposal,
)

MIN_WAKE_DELAY = timedelta(seconds=1)
MAX_WAKE_HORIZON = timedelta(days=7)
MAX_PENDING_WAKE_INTENTS = 8
MAX_WAKE_HISTORY = 256


class TemporalClock(Protocol):
    """A wall-clock seam used only for absolute temporal deadlines."""

    def now(self) -> datetime: ...


type TemporalClockSource = Callable[[], datetime] | TemporalClock
type TemporalChangeListener = Callable[[], None]


class WakeIntentStatus(StrEnum):
    """One-shot wake lifecycle owned by the runtime."""

    PENDING = "pending"
    DUE = "due"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    DISPATCHED = "dispatched"


class TemporalApplicationStatus(StrEnum):
    """Settlement of temporal proposal admission."""

    NOT_REQUESTED = "not_requested"
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class WakeIntent:
    """A trusted, runtime-created one-shot wake record.

    The requested model time is deliberately absent. ``not_before`` is the
    normalized runtime-owned deadline after minimum-delay and horizon policy.
    """

    wake_intent_id: str
    scope_id: str
    reason: str
    not_before: datetime
    source_episode_id: str
    source_trigger_id: str
    intention_ref: str | None
    sequence: int
    status: WakeIntentStatus = WakeIntentStatus.PENDING

    def __post_init__(self) -> None:
        _require_text(self.wake_intent_id, "wake_intent_id")
        _require_text(self.scope_id, "scope_id")
        _require_text(self.reason, "reason")
        _require_text(self.source_episode_id, "source_episode_id")
        _require_text(self.source_trigger_id, "source_trigger_id")
        if type(self.not_before) is not datetime:
            raise TypeError("not_before must be a datetime")
        if self.not_before.tzinfo is None or self.not_before.utcoffset() is None:
            raise ValueError("wake intent deadline must be timezone-aware")
        if isinstance(self.sequence, bool) or self.sequence <= 0:
            raise ValueError("wake intent sequence must be positive")
        if type(self.status) is not WakeIntentStatus:
            raise TypeError("status must be a WakeIntentStatus")
        if self.intention_ref is not None:
            _require_text(self.intention_ref, "intention_ref")


@dataclass(frozen=True, slots=True)
class TemporalProposalApplication:
    """Per-proposal evidence without exposing scheduler internals."""

    proposal_index: int
    status: TemporalApplicationStatus
    wake_intent_id: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class TemporalApplication:
    """The trusted temporal portion of one proposal application."""

    status: TemporalApplicationStatus
    proposals: tuple[TemporalProposalApplication, ...] = ()
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedTemporalProposal:
    """Validated, normalized data waiting for the outer application commit."""

    proposal_index: int
    proposal: TemporalProposal
    normalized_not_before: datetime
    dedup_key: str


@dataclass(frozen=True, slots=True)
class TemporalPreparation:
    """Side-effect-free temporal validation for MIND-1D composition."""

    prepared: tuple[PreparedTemporalProposal, ...] = ()
    rejection: TemporalApplication | None = None
    reason_code: str | None = None

    @property
    def valid(self) -> bool:
        return self.rejection is None


class TemporalCoordinator:
    """Own bounded in-memory wake admission and due-trigger dispatch.

    Equivalent proposals deduplicate by runtime scope, normalized reason, and
    optional intention reference while the first equivalent wake is pending or
    due. The first accepted deadline wins. A later proposal after cancellation
    or dispatch is a new explicit one-shot request.
    """

    def __init__(
        self,
        *,
        scope_id: str,
        clock: TemporalClockSource | None = None,
        minimum_delay: timedelta = MIN_WAKE_DELAY,
        maximum_horizon: timedelta = MAX_WAKE_HORIZON,
        pending_capacity: int = MAX_PENDING_WAKE_INTENTS,
        runtime_instance_id: str = "runtime",
    ) -> None:
        _require_text(scope_id, "scope_id")
        _require_text(runtime_instance_id, "runtime_instance_id")
        if minimum_delay <= timedelta(0):
            raise ValueError("minimum_delay must be positive")
        if maximum_horizon < minimum_delay:
            raise ValueError("maximum_horizon must be at least minimum_delay")
        if (
            isinstance(pending_capacity, bool)
            or not 0 < pending_capacity <= MAX_PENDING_WAKE_INTENTS
        ):
            raise ValueError("pending_capacity is outside its bound")
        self._scope_id = scope_id
        self._clock = clock or _utc_now
        self._minimum_delay = minimum_delay
        self._maximum_horizon = maximum_horizon
        self._pending_capacity = pending_capacity
        self._runtime_instance_id = runtime_instance_id
        self._lock = RLock()
        self._next_sequence = 0
        self._intents: dict[str, WakeIntent] = {}
        self._dedup_keys: dict[str, str] = {}
        self._listeners: dict[int, TemporalChangeListener] = {}
        self._next_listener_id = 0

    @property
    def scope_id(self) -> str:
        return self._scope_id

    @property
    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for item in self._intents.values() if self._is_pending(item))

    def intents(self) -> tuple[WakeIntent, ...]:
        """Return bounded lifecycle snapshots in creation order."""

        with self._lock:
            return tuple(self._intents.values())

    def pending_intents(self) -> tuple[WakeIntent, ...]:
        with self._lock:
            return tuple(item for item in self._intents.values() if self._is_pending(item))

    def intent(self, wake_intent_id: str) -> WakeIntent | None:
        with self._lock:
            return self._intents.get(wake_intent_id)

    def now(self) -> datetime:
        """Return the normalized coordinator clock without changing state."""

        with self._lock:
            return self._now()

    def next_deadline(self) -> datetime | None:
        """Return the earliest pending wake deadline without changing state."""

        with self._lock:
            pending = [item for item in self._intents.values() if self._is_pending(item)]
            if not pending:
                return None
            return min(
                pending,
                key=lambda item: (item.not_before, item.sequence, item.wake_intent_id),
            ).not_before

    def subscribe(self, listener: TemporalChangeListener) -> Callable[[], None]:
        """Subscribe to wake-state changes and return an idempotent removal hook."""

        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            self._next_listener_id += 1
            listener_id = self._next_listener_id
            self._listeners[listener_id] = listener

        def unsubscribe() -> None:
            with self._lock:
                self._listeners.pop(listener_id, None)

        return unsubscribe

    def prepare(
        self,
        proposals: Sequence[TemporalProposal],
        *,
        source_episode_id: str,
        source_trigger_id: str,
    ) -> TemporalPreparation:
        """Validate and normalize proposals without changing scheduler state."""

        _require_text(source_episode_id, "source_episode_id")
        _require_text(source_trigger_id, "source_trigger_id")
        snapshot = tuple(proposals)
        if not snapshot:
            return TemporalPreparation()
        if len(snapshot) > 8:
            return self._rejection(snapshot, "temporal_proposal_bound_exceeded")

        with self._lock:
            now = self._now()
            normalized: list[PreparedTemporalProposal] = []
            new_keys: set[str] = set()
            pending_keys = {
                key
                for wake_id, key in self._dedup_keys.items()
                if self._is_pending(self._intents[wake_id])
            }
            for index, proposal in enumerate(snapshot):
                if type(proposal) is not TemporalProposal:
                    return self._rejection(snapshot, "unsupported_temporal_proposal")
                deadline = self._normalize_deadline(proposal.not_before, now)
                dedup_key = self._dedup_key(proposal)
                normalized.append(PreparedTemporalProposal(index, proposal, deadline, dedup_key))
                if dedup_key not in pending_keys:
                    new_keys.add(dedup_key)
            if self.pending_count + len(new_keys) > self._pending_capacity:
                return self._rejection(snapshot, "pending_wake_capacity")
            return TemporalPreparation(tuple(normalized))

    def commit(
        self,
        preparation: TemporalPreparation,
        *,
        source_episode_id: str,
        source_trigger_id: str,
    ) -> TemporalApplication:
        """Commit a previously prepared proposal batch atomically."""

        if not preparation.valid:
            return preparation.rejection or TemporalApplication(
                TemporalApplicationStatus.REJECTED,
                reason_code=preparation.reason_code or "temporal_preparation_rejected",
            )
        if not preparation.prepared:
            return TemporalApplication(TemporalApplicationStatus.NOT_REQUESTED)
        _require_text(source_episode_id, "source_episode_id")
        _require_text(source_trigger_id, "source_trigger_id")

        with self._lock:
            existing_by_key = {
                key: wake_id
                for wake_id, key in self._dedup_keys.items()
                if self._is_pending(self._intents[wake_id])
            }
            new_keys = {
                item.dedup_key
                for item in preparation.prepared
                if item.dedup_key not in existing_by_key
            }
            if self.pending_count + len(new_keys) > self._pending_capacity:
                return self._rejected_application(preparation.prepared, "pending_wake_capacity")

            records: list[TemporalProposalApplication] = []
            for item in preparation.prepared:
                existing_id = existing_by_key.get(item.dedup_key)
                if existing_id is not None:
                    records.append(
                        TemporalProposalApplication(
                            item.proposal_index,
                            TemporalApplicationStatus.DUPLICATE,
                            existing_id,
                            "equivalent_wake_pending",
                        )
                    )
                    continue

                self._next_sequence += 1
                wake_id = self._wake_id(item.dedup_key, self._next_sequence)
                intent = WakeIntent(
                    wake_id,
                    self._scope_id,
                    item.proposal.reason,
                    item.normalized_not_before,
                    source_episode_id,
                    source_trigger_id,
                    item.proposal.intention_ref,
                    self._next_sequence,
                )
                self._intents[wake_id] = intent
                self._dedup_keys[wake_id] = item.dedup_key
                existing_by_key[item.dedup_key] = wake_id
                records.append(
                    TemporalProposalApplication(
                        item.proposal_index,
                        TemporalApplicationStatus.APPLIED,
                        wake_id,
                    )
                )
            self._trim_history()
            status = (
                TemporalApplicationStatus.DUPLICATE
                if all(item.status is TemporalApplicationStatus.DUPLICATE for item in records)
                else TemporalApplicationStatus.APPLIED
            )
            listeners = tuple(self._listeners.values()) if new_keys else ()
        self._notify(listeners)
        return TemporalApplication(status, tuple(records))

    def apply(
        self,
        proposals: Sequence[TemporalProposal],
        *,
        source_episode_id: str,
        source_trigger_id: str,
    ) -> TemporalApplication:
        """Convenience admission for callers without a mixed application batch."""

        preparation = self.prepare(
            proposals,
            source_episode_id=source_episode_id,
            source_trigger_id=source_trigger_id,
        )
        return self.commit(
            preparation,
            source_episode_id=source_episode_id,
            source_trigger_id=source_trigger_id,
        )

    def cancel(self, wake_intent_id: str, *, supersede: bool = False) -> WakeIntent | None:
        """Cancel or supersede one pending wake through the explicit seam."""

        _require_text(wake_intent_id, "wake_intent_id")
        with self._lock:
            intent = self._intents.get(wake_intent_id)
            if intent is None or not self._is_pending(intent):
                return None
            status = WakeIntentStatus.SUPERSEDED if supersede else WakeIntentStatus.CANCELLED
            updated = replace(intent, status=status)
            self._intents[wake_intent_id] = updated
            self._trim_history()
            listeners = tuple(self._listeners.values())
        self._notify(listeners)
        return updated

    def poll_due(self, now: datetime | None = None) -> tuple[CognitionTrigger, ...]:
        """Fence each due wake once and emit only temporal cognition triggers.

        The returned triggers contain trusted IDs only. The caller feeds them to
        the normal serialized cognition runner; this method never runs a model,
        executes a tool, writes Core history, or acts on an environment.
        """

        current = self._normalize_now(now) if now is not None else self._now()
        with self._lock:
            due = sorted(
                (
                    item
                    for item in self._intents.values()
                    if item.status is WakeIntentStatus.PENDING and item.not_before <= current
                ),
                key=lambda item: (item.not_before, item.sequence, item.wake_intent_id),
            )
            triggers: list[CognitionTrigger] = []
            for intent in due:
                self._intents[intent.wake_intent_id] = replace(
                    intent, status=WakeIntentStatus.DISPATCHED
                )
                refs = [
                    intent.wake_intent_id,
                    intent.source_episode_id,
                    intent.source_trigger_id,
                ]
                if intent.intention_ref is not None:
                    refs.append(intent.intention_ref)
                triggers.append(
                    CognitionTrigger(
                        (),
                        intent.reason,
                        source=CognitionTriggerSource.TEMPORAL,
                        wake_intent_id=intent.wake_intent_id,
                        source_refs=tuple(refs),
                    )
                )
            listeners = tuple(self._listeners.values()) if due else ()
        self._notify(listeners)
        return tuple(triggers)

    def _normalize_deadline(self, requested: datetime, now: datetime) -> datetime:
        if requested <= now + self._minimum_delay:
            return now + self._minimum_delay
        return min(requested, now + self._maximum_horizon)

    def _now(self) -> datetime:
        source = self._clock
        value = source() if callable(source) else source.now()
        return self._normalize_now(value)

    @staticmethod
    def _normalize_now(value: datetime) -> datetime:
        if type(value) is not datetime:
            raise TypeError("temporal clock must return a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("temporal clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _rejection(self, proposals: Sequence[object], reason: str) -> TemporalPreparation:
        return TemporalPreparation(
            rejection=TemporalApplication(
                TemporalApplicationStatus.REJECTED,
                tuple(
                    TemporalProposalApplication(
                        index, TemporalApplicationStatus.REJECTED, reason_code=reason
                    )
                    for index, _ in enumerate(proposals)
                ),
                reason,
            ),
            reason_code=reason,
        )

    @staticmethod
    def _rejected_application(
        proposals: Sequence[PreparedTemporalProposal], reason: str
    ) -> TemporalApplication:
        return TemporalApplication(
            TemporalApplicationStatus.REJECTED,
            tuple(
                TemporalProposalApplication(
                    item.proposal_index,
                    TemporalApplicationStatus.REJECTED,
                    reason_code=reason,
                )
                for item in proposals
            ),
            reason,
        )

    def _dedup_key(self, proposal: TemporalProposal) -> str:
        material = "\x1f".join(
            (self._scope_id, proposal.reason, proposal.intention_ref or "")
        ).encode("utf-8")
        return sha256(material).hexdigest()

    def _wake_id(self, dedup_key: str, sequence: int) -> str:
        material = f"{self._runtime_instance_id}\x1f{dedup_key}\x1f{sequence}".encode()
        return f"wake:{sha256(material).hexdigest()[:32]}"

    @staticmethod
    def _is_pending(intent: WakeIntent) -> bool:
        return intent.status in {WakeIntentStatus.PENDING, WakeIntentStatus.DUE}

    def _trim_history(self) -> None:
        if len(self._intents) <= MAX_WAKE_HISTORY:
            return
        for wake_id, intent in tuple(self._intents.items()):
            if self._is_pending(intent):
                continue
            del self._intents[wake_id]
            del self._dedup_keys[wake_id]
            if len(self._intents) <= MAX_WAKE_HISTORY:
                break

    @staticmethod
    def _notify(listeners: Sequence[TemporalChangeListener]) -> None:
        for listener in listeners:
            try:
                listener()
            except Exception:
                # A wait notification must never change trusted temporal
                # admission or dispatch semantics.
                continue


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_text(value: str, name: str) -> None:
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > 128:
        raise ValueError(f"{name} must be non-empty bounded text")


__all__ = [
    "MAX_PENDING_WAKE_INTENTS",
    "MAX_WAKE_HISTORY",
    "MAX_WAKE_HORIZON",
    "MIN_WAKE_DELAY",
    "PreparedTemporalProposal",
    "TemporalApplication",
    "TemporalApplicationStatus",
    "TemporalClock",
    "TemporalClockSource",
    "TemporalChangeListener",
    "TemporalCoordinator",
    "TemporalPreparation",
    "TemporalProposalApplication",
    "WakeIntent",
    "WakeIntentStatus",
]
