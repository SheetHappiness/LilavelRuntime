"""Core-owned conversation semantics above the provider-neutral model runtime."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from queue import Queue
from threading import Event, Lock, RLock, Thread
from typing import Final, Literal, Protocol, runtime_checkable
from uuid import uuid4

from .persistence import (
    CanonicalMessage,
    ConversationStore,
    EvidenceRecord,
    PersistenceError,
    SQLiteConversationStore,
    validate_scope_id,
)
from .sidecar_protocol import (
    ContextMessage,
    GenerationCancelled,
    GenerationCompleted,
    GenerationEvent,
    ModelRequest,
    ProtocolError,
    TextDelta,
)
from .sidecar_protocol import GenerationFailed as GenerationFailedEvent


class ConversationError(RuntimeError):
    """Base class for semantic conversation failures."""


class ConversationBusy(ConversationError):
    """A new turn was requested without allowing the active turn to supersede."""


class RuntimeGeneration(Protocol):
    """The small generation surface consumed by ``ConversationRun``."""

    generation_id: str
    epoch: int

    def events(self) -> Iterator[GenerationEvent]:
        """Yield the runtime's correlated generation events."""
        ...


class ConversationRuntime(Protocol):
    """The lifecycle surface needed from ``ModelRuntime``."""

    def generate(self, request: ModelRequest) -> RuntimeGeneration:
        """Start one generation for a complete caller-owned request."""
        ...

    def cancel(self, generation_id: str) -> bool:
        """Request cancellation for one runtime generation."""
        ...


@runtime_checkable
class RunAwareConversationRuntime(ConversationRuntime, Protocol):
    """Optional correlation seam implemented only by explicit V3 runtimes."""

    def generate_for_run(
        self,
        request: ModelRequest,
        *,
        scope_id: str,
        logical_run_id: str,
    ) -> RuntimeGeneration: ...


class ConversationContextComposer(Protocol):
    """Compose canonical history into the next provider-neutral model input."""

    def compose(self, history: tuple[ContextMessage, ...]) -> Sequence[ContextMessage]:
        """Return the ordered role/text context for one model request."""
        ...


class FullHistoryContextComposer:
    """The initial composition policy: send the complete canonical history."""

    def compose(self, history: tuple[ContextMessage, ...]) -> tuple[ContextMessage, ...]:
        return history


RUNTIME_EVIDENCE_CAPACITY: Final = 256

type RuntimeEvidenceKind = Literal[
    "turn_accepted",
    "generation_bound",
    "cancel_requested",
    "delta_discarded",
    "generation_terminal",
    "assistant_commit",
    "run_terminal",
]
type RuntimeEvidenceResult = Literal[
    "completed",
    "cancelled",
    "superseded",
    "failed",
    "committed",
]
type RuntimeEvidenceReason = Literal[
    "cancelled",
    "superseded",
    "not_current",
    "settled",
    "context",
    "runtime",
    "runtime_cancel",
    "persistence",
    "invalid_result",
    "generation_exhausted",
    "semantic",
]


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceRecord:
    """Safe, ephemeral evidence for one Core conversation lifecycle fact.

    ``sequence`` is the authoritative in-trace ordering primitive.  The
    record intentionally contains only Core-owned IDs, safe statuses/codes,
    and aggregate counts; it never contains message or provider payloads.
    """

    sequence: int
    kind: RuntimeEvidenceKind
    scope_id: str
    run_id: str
    user_message_id: str
    generation_id: str | None = None
    epoch: int | None = None
    assistant_message_id: str | None = None
    result: RuntimeEvidenceResult | None = None
    reason: RuntimeEvidenceReason | None = None
    failure_code: str | None = None
    discarded_count: int = 0
    discarded_bytes: int = 0


class _RuntimeEvidenceTrace:
    """Bounded Core-owned storage for immutable runtime evidence snapshots."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("runtime evidence capacity must be positive")
        self._records: deque[RuntimeEvidenceRecord] = deque(maxlen=capacity)
        self._next_sequence = 1
        self._discarded: dict[tuple[str, str, int, RuntimeEvidenceReason], int] = {}

    def append(
        self,
        *,
        kind: RuntimeEvidenceKind,
        scope_id: str,
        run_id: str,
        user_message_id: str,
        generation_id: str | None = None,
        epoch: int | None = None,
        assistant_message_id: str | None = None,
        result: RuntimeEvidenceResult | None = None,
        reason: RuntimeEvidenceReason | None = None,
        failure_code: str | None = None,
    ) -> None:
        record = RuntimeEvidenceRecord(
            sequence=self._next_sequence,
            kind=kind,
            scope_id=scope_id,
            run_id=run_id,
            user_message_id=user_message_id,
            generation_id=generation_id,
            epoch=epoch,
            assistant_message_id=assistant_message_id,
            result=result,
            reason=reason,
            failure_code=failure_code,
        )
        self._next_sequence += 1
        self._append_record(record)

    def append_delta_discarded(
        self,
        *,
        scope_id: str,
        run_id: str,
        user_message_id: str,
        generation_id: str,
        epoch: int,
        reason: Literal["not_current", "cancelled", "settled"],
        delta_bytes: int,
    ) -> None:
        key = (run_id, generation_id, epoch, reason)
        sequence = self._discarded.get(key)
        if sequence is not None:
            for index, record in enumerate(self._records):
                if record.sequence == sequence:
                    self._records[index] = replace(
                        record,
                        discarded_count=record.discarded_count + 1,
                        discarded_bytes=record.discarded_bytes + delta_bytes,
                    )
                    return
            del self._discarded[key]

        record = RuntimeEvidenceRecord(
            sequence=self._next_sequence,
            kind="delta_discarded",
            scope_id=scope_id,
            run_id=run_id,
            user_message_id=user_message_id,
            generation_id=generation_id,
            epoch=epoch,
            reason=reason,
            discarded_count=1,
            discarded_bytes=delta_bytes,
        )
        self._next_sequence += 1
        self._append_record(record)
        self._discarded[key] = record.sequence

    def snapshot(self) -> tuple[RuntimeEvidenceRecord, ...]:
        # Copy records as well as the container so even deliberate object-level
        # mutation of a returned frozen dataclass cannot affect retained state.
        return tuple(replace(record) for record in self._records)

    def contains(self, *, kind: RuntimeEvidenceKind, run_id: str) -> bool:
        return any(record.kind == kind and record.run_id == run_id for record in self._records)

    def _append_record(self, record: RuntimeEvidenceRecord) -> None:
        if len(self._records) == self._records.maxlen:
            evicted = self._records[0]
            if evicted.kind == "delta_discarded":
                assert evicted.generation_id is not None
                assert evicted.epoch is not None
                assert evicted.reason in {"not_current", "cancelled", "settled"}
                key = (evicted.run_id, evicted.generation_id, evicted.epoch, evicted.reason)
                if self._discarded.get(key) == evicted.sequence:
                    del self._discarded[key]
        self._records.append(record)


type ConversationCancellationReason = Literal["cancelled", "superseded"]
type ConversationStatus = Literal["completed", "cancelled", "superseded", "failed"]
type _DeltaAppendResult = Literal["accepted", "cancelled", "settled"]


@dataclass(frozen=True, slots=True)
class ConversationTextDelta:
    """One transient assistant delta that was surfaced incrementally."""

    run_id: str
    delta: str


@dataclass(frozen=True, slots=True)
class ConversationCompleted:
    """A successfully completed assistant response."""

    run_id: str
    text: str


@dataclass(frozen=True, slots=True)
class ConversationCancelled:
    """A cancelled or superseded run whose candidate was not committed."""

    run_id: str
    text: str
    reason: ConversationCancellationReason


@dataclass(frozen=True, slots=True)
class ConversationFailed:
    """A run that could not produce a canonical assistant response."""

    run_id: str
    text: str
    error: PersistenceError | None = None


type ConversationEvent = (
    ConversationTextDelta | ConversationCompleted | ConversationCancelled | ConversationFailed
)


@dataclass(frozen=True, slots=True)
class ConversationOutcome:
    """The terminal semantic result of one conversation run."""

    run_id: str
    status: ConversationStatus
    text: str
    error: PersistenceError | None = None


class ConversationRun:
    """One user turn and its transient assistant generation.

    The run owns a semantic event stream, while ``ConversationCore`` owns the
    canonical history.  The underlying runtime generation is deliberately
    private so protocol and provider fields do not become conversation API.
    """

    def __init__(
        self,
        core: ConversationCore,
        run_id: str,
        predecessor: ConversationRun | None,
        user_message_id: str,
        assistant_message_id: str,
    ) -> None:
        self.run_id = run_id
        self.user_message_id = user_message_id
        self.assistant_message_id = assistant_message_id
        self._core = core
        self._predecessor = predecessor
        self._events: Queue[ConversationEvent] = Queue()
        self._settled_event = Event()
        self._lock = Lock()
        self._outcome: ConversationOutcome | None = None
        self._candidate_parts: list[str] = []
        self._cancel_requested = False
        self._cancel_reason: ConversationCancellationReason = "cancelled"
        self._runtime_generation: RuntimeGeneration | None = None
        self._cancel_sent = False
        self._assistant_committed = False
        self._assistant_commit_reserved = False
        self._consumer_active = False
        self._worker = Thread(
            target=self._execute,
            name=f"lilavel-conversation-{run_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._worker.start()

    def events(self) -> Iterator[ConversationEvent]:
        """Yield semantic deltas followed by exactly one terminal event."""

        with self._lock:
            if self._consumer_active:
                raise ConversationError("conversation run events already have a consumer")
            self._consumer_active = True
        try:
            while True:
                event = self._events.get()
                yield event
                if isinstance(
                    event,
                    (ConversationCompleted, ConversationCancelled, ConversationFailed),
                ):
                    return
        finally:
            with self._lock:
                self._consumer_active = False

    def __iter__(self) -> Iterator[ConversationEvent]:
        return self.events()

    def wait(self, timeout: float | None = None) -> ConversationOutcome:
        """Wait for the terminal semantic outcome without draining events."""

        if not self._settled_event.wait(timeout):
            raise TimeoutError("conversation run did not complete before the deadline")
        with self._lock:
            if self._outcome is None:
                raise ConversationError("conversation run ended without an outcome")
            return self._outcome

    def cancel(self) -> bool:
        """Cancel this run without committing its candidate assistant text."""

        return self.request_cancel("cancelled")

    @property
    def text(self) -> str:
        """Return the candidate text surfaced so far, whether or not it commits."""

        with self._lock:
            return "".join(self._candidate_parts)

    @property
    def settled(self) -> bool:
        with self._lock:
            return self._outcome is not None

    @property
    def outcome(self) -> ConversationOutcome | None:
        with self._lock:
            return self._outcome

    def wait_for_settlement(self) -> None:
        self._settled_event.wait()

    def request_cancel(self, reason: ConversationCancellationReason) -> bool:
        generation: RuntimeGeneration | None = None
        with self._lock:
            if self._outcome is not None:
                return False
            if not self._cancel_requested:
                self._cancel_requested = True
                self._cancel_reason = reason
            if self._runtime_generation is not None and not self._cancel_sent:
                self._cancel_sent = True
                generation = self._runtime_generation
        if generation is not None:
            self._send_runtime_cancel(generation)
        return True

    def _send_runtime_cancel(self, generation: RuntimeGeneration) -> None:
        try:
            self._core.runtime_cancel(self, generation, self._cancel_reason_now())
        except Exception:
            # A failed physical cancellation is a failed semantic run.  The
            # runtime remains responsible for settling/poisoning its handle.
            self._settle_failed(reason="runtime_cancel")

    def _cancel_requested_now(self) -> bool:
        with self._lock:
            return self._cancel_requested

    def _cancel_reason_now(self) -> ConversationCancellationReason:
        with self._lock:
            return self._cancel_reason

    def _set_runtime_generation(self, generation: RuntimeGeneration) -> None:
        send_cancel = False
        with self._lock:
            self._runtime_generation = generation
            if self._cancel_requested and not self._cancel_sent:
                self._cancel_sent = True
                send_cancel = True
        self._core.record_generation_bound(self, generation)
        if send_cancel:
            self._send_runtime_cancel(generation)

    def _execute(self) -> None:
        try:
            if self._predecessor is not None:
                self._predecessor.wait_for_settlement()

            if self._cancel_requested_now():
                self._settle_cancelled(self._cancel_reason_now())
                return

            try:
                request = self._core.build_model_request()
            except Exception:
                # This includes the existing structured-context validation
                # boundary.  The accepted user remains canonical.
                self._settle_failed(reason="context")
                return

            if self._cancel_requested_now():
                self._settle_cancelled(self._cancel_reason_now())
                return

            try:
                generation = self._core.runtime_generate(self, request)
            except Exception:
                self._settle_failed(reason="runtime")
                return

            self._set_runtime_generation(generation)
            self._consume_generation(generation)
        except Exception:
            self._settle_failed(reason="semantic")

    def _consume_generation(self, generation: RuntimeGeneration) -> None:
        for event in generation.events():
            if isinstance(event, TextDelta):
                self._core.publish_delta(
                    self,
                    generation_id=event.generation_id,
                    epoch=event.epoch,
                    delta=event.delta,
                )
            elif isinstance(event, GenerationCompleted):
                self._core.record_generation_terminal(
                    self,
                    generation_id=generation.generation_id,
                    epoch=generation.epoch,
                    result="completed",
                )
                try:
                    commit = self._core.commit_assistant(self)
                except PersistenceError as error:
                    self._settle_failed(error, reason="persistence")
                    return
                if commit == "committed":
                    self._settle_completed()
                elif commit == "superseded":
                    self._settle_cancelled("superseded")
                elif commit == "cancelled":
                    self._settle_cancelled("cancelled")
                else:
                    self._settle_failed(reason="invalid_result")
                return
            elif isinstance(event, GenerationCancelled):
                self._core.record_generation_terminal(
                    self,
                    generation_id=generation.generation_id,
                    epoch=generation.epoch,
                    result="cancelled",
                )
                self._settle_cancelled(self._cancel_reason_now())
                return
            elif isinstance(event, GenerationFailedEvent):
                self._core.record_generation_terminal(
                    self,
                    generation_id=generation.generation_id,
                    epoch=generation.epoch,
                    result="failed",
                    failure_code=event.code,
                )
                self._settle_failed(reason="runtime", failure_code=event.code)
                return
            # ``accepted`` is a runtime admission event, not conversation
            # semantics.  Other non-generation events are ignored likewise.

        # A conforming runtime always supplies a terminal generation event.
        # Do not leave a semantic run hanging if a test/runtime substitute does
        # not honor that contract.
        if not self.settled:
            self._settle_failed(reason="generation_exhausted")

    def append_delta_if_live(self, delta: str) -> _DeltaAppendResult:
        with self._lock:
            if self._outcome is not None:
                return "settled"
            if self._cancel_requested:
                return "cancelled"
            self._candidate_parts.append(delta)
            self._events.put(ConversationTextDelta(self.run_id, delta))
            return "accepted"

    def _candidate_text(self) -> str:
        with self._lock:
            return "".join(self._candidate_parts)

    def _settle_completed(self) -> None:
        text = self._candidate_text()
        self._settle(
            ConversationOutcome(self.run_id, "completed", text),
            ConversationCompleted(self.run_id, text),
        )

    def _settle_cancelled(self, reason: ConversationCancellationReason) -> None:
        text = self._candidate_text()
        status: ConversationStatus = "superseded" if reason == "superseded" else "cancelled"
        self._settle(
            ConversationOutcome(self.run_id, status, text),
            ConversationCancelled(self.run_id, text, reason),
        )

    def _settle_failed(
        self,
        error: PersistenceError | None = None,
        *,
        reason: RuntimeEvidenceReason | None = None,
        failure_code: str | None = None,
    ) -> None:
        text = self._candidate_text()
        self._settle(
            ConversationOutcome(self.run_id, "failed", text, error),
            ConversationFailed(self.run_id, text, error),
            reason=reason,
            failure_code=failure_code,
        )

    def _settle(
        self,
        outcome: ConversationOutcome,
        event: ConversationEvent,
        *,
        reason: RuntimeEvidenceReason | None = None,
        failure_code: str | None = None,
    ) -> None:
        with self._lock:
            if self._outcome is not None:
                return
            self._outcome = outcome
            self._events.put(event)
            self._settled_event.set()
        self._core.record_run_terminal(
            self,
            outcome,
            reason=reason,
            failure_code=failure_code,
        )
        self._core.run_settled(self)

    def reserve_assistant_commit(
        self,
    ) -> tuple[Literal["new", "already", "cancelled", "invalid"], ContextMessage | None]:
        """Reserve one validated assistant message for Core's history append."""

        with self._lock:
            if self._outcome is not None or self._cancel_requested:
                return "cancelled", None
            if self._assistant_committed or self._assistant_commit_reserved:
                return "already", None
            try:
                message = ContextMessage("assistant", "".join(self._candidate_parts))
            except ProtocolError:
                return "invalid", None
            self._assistant_commit_reserved = True
            return "new", message

    def mark_assistant_committed(self) -> None:
        with self._lock:
            self._assistant_commit_reserved = False
            self._assistant_committed = True

    def release_assistant_commit(self) -> None:
        with self._lock:
            self._assistant_commit_reserved = False


type _CommitResult = Literal["committed", "superseded", "cancelled", "invalid"]


class ConversationCore:
    """Own canonical role/text history and coordinate one active run."""

    def __init__(
        self,
        runtime: ConversationRuntime,
        *,
        composer: ConversationContextComposer | None = None,
        trusted_guidance: Sequence[str] | Callable[[], Sequence[str]] = (),
        scope_id: str | None = None,
        store: ConversationStore | None = None,
        runtime_evidence_capacity: int = RUNTIME_EVIDENCE_CAPACITY,
    ) -> None:
        self._runtime = runtime
        self._composer = composer or FullHistoryContextComposer()
        self._trusted_guidance = (
            trusted_guidance if callable(trusted_guidance) else tuple(trusted_guidance)
        )
        self._lock = RLock()
        self._runtime_evidence = _RuntimeEvidenceTrace(runtime_evidence_capacity)
        self._store = store or SQLiteConversationStore(":memory:")
        self._scope_id = validate_scope_id(scope_id if scope_id is not None else str(uuid4()))
        self._canonical_messages: list[CanonicalMessage] = list(
            self._store.load_canonical_messages(self._scope_id)
        )
        self._active: ConversationRun | None = None

    @property
    def scope_id(self) -> str:
        """Return the provider-neutral durable scope identity."""

        return self._scope_id

    @property
    def history(self) -> tuple[ContextMessage, ...]:
        """Return canonical user and successfully completed assistant messages."""

        with self._lock:
            return tuple(
                ContextMessage(message.role, message.text) for message in self._canonical_messages
            )

    @property
    def canonical_history(self) -> tuple[CanonicalMessage, ...]:
        """Return canonical messages with their durable Core-owned identities."""

        with self._lock:
            return tuple(self._canonical_messages)

    @property
    def evidence_records(self) -> tuple[EvidenceRecord, ...]:
        """Load raw provenance records for audit/testing."""

        with self._lock:
            return self._store.load_evidence(self._scope_id)

    def runtime_evidence(self) -> tuple[RuntimeEvidenceRecord, ...]:
        """Return an immutable snapshot of bounded ephemeral Core diagnostics.

        This is a runtime trace, not canonical history or durable provenance.
        It is safe for tests and debugging to inspect after a run settles.
        """

        with self._lock:
            return self._runtime_evidence.snapshot()

    @property
    def active_run(self) -> ConversationRun | None:
        with self._lock:
            if self._active is None or self._active.settled:
                return None
            return self._active

    def start_turn(self, text: str, *, supersede: bool = True) -> ConversationRun:
        """Accept one user message and start its assistant run.

        The user message is appended before any model call.  If another run is
        active, the new run becomes the logical active run immediately and the
        previous physical generation is cancelled before the new run asks the
        single-generation runtime to start work.
        """

        user_message = ContextMessage("user", text)
        user_message_id = str(uuid4())
        with self._lock:
            previous = self._active
            if previous is not None and previous.settled:
                previous = None
                self._active = None
            if previous is not None and not supersede:
                raise ConversationBusy("a conversation run is already active")

            canonical_user = self._store.append_user_message(
                scope_id=self._scope_id,
                message_id=user_message_id,
                text=user_message.text,
            )
            self._canonical_messages.append(canonical_user)
            run = ConversationRun(
                self,
                str(uuid4()),
                previous,
                canonical_user.message_id,
                str(uuid4()),
            )
            self._active = run
            self._runtime_evidence.append(
                kind="turn_accepted",
                scope_id=self._scope_id,
                run_id=run.run_id,
                user_message_id=run.user_message_id,
            )

        if previous is not None:
            previous.request_cancel("superseded")
        run.start()
        return run

    def build_model_request(self) -> ModelRequest:
        with self._lock:
            history = tuple(
                ContextMessage(message.role, message.text) for message in self._canonical_messages
            )
        composed = tuple(self._composer.compose(history))
        guidance = self._trusted_guidance
        blocks = tuple(guidance()) if callable(guidance) else guidance
        return ModelRequest(messages=composed, system_prompt=blocks)

    def publish_delta(
        self,
        run: ConversationRun,
        *,
        generation_id: str,
        epoch: int,
        delta: str,
    ) -> None:
        with self._lock:
            if self._active is not run:
                self._runtime_evidence.append_delta_discarded(
                    scope_id=self._scope_id,
                    run_id=run.run_id,
                    user_message_id=run.user_message_id,
                    generation_id=generation_id,
                    epoch=epoch,
                    reason="not_current",
                    delta_bytes=len(delta.encode("utf-8")),
                )
                return
            result = run.append_delta_if_live(delta)
            if result != "accepted":
                self._runtime_evidence.append_delta_discarded(
                    scope_id=self._scope_id,
                    run_id=run.run_id,
                    user_message_id=run.user_message_id,
                    generation_id=generation_id,
                    epoch=epoch,
                    reason=result,
                    delta_bytes=len(delta.encode("utf-8")),
                )

    def record_generation_bound(self, run: ConversationRun, generation: RuntimeGeneration) -> None:
        with self._lock:
            self._record_generation_bound_locked(run, generation)

    def _record_generation_bound_locked(
        self, run: ConversationRun, generation: RuntimeGeneration
    ) -> None:
        if self._runtime_evidence.contains(kind="generation_bound", run_id=run.run_id):
            return
        self._runtime_evidence.append(
            kind="generation_bound",
            scope_id=self._scope_id,
            run_id=run.run_id,
            user_message_id=run.user_message_id,
            generation_id=generation.generation_id,
            epoch=generation.epoch,
        )

    def record_generation_terminal(
        self,
        run: ConversationRun,
        *,
        generation_id: str,
        epoch: int,
        result: Literal["completed", "cancelled", "failed"],
        failure_code: str | None = None,
    ) -> None:
        with self._lock:
            self._runtime_evidence.append(
                kind="generation_terminal",
                scope_id=self._scope_id,
                run_id=run.run_id,
                user_message_id=run.user_message_id,
                generation_id=generation_id,
                epoch=epoch,
                result=result,
                reason="runtime" if result == "failed" else None,
                failure_code=failure_code,
            )

    def commit_assistant(self, run: ConversationRun) -> _CommitResult:
        with self._lock:
            if self._active is not run:
                return "superseded"
            reservation, assistant_message = run.reserve_assistant_commit()
            if reservation == "already":
                return "committed"
            if reservation == "cancelled":
                return "cancelled"
            if reservation == "invalid" or assistant_message is None:
                return "invalid"
            try:
                canonical_assistant = self._store.append_assistant_message(
                    scope_id=self._scope_id,
                    message_id=run.assistant_message_id,
                    text=assistant_message.text,
                )
            except Exception:
                run.release_assistant_commit()
                raise
            self._canonical_messages.append(canonical_assistant)
            run.mark_assistant_committed()
            self._runtime_evidence.append(
                kind="assistant_commit",
                scope_id=self._scope_id,
                run_id=run.run_id,
                user_message_id=run.user_message_id,
                assistant_message_id=canonical_assistant.message_id,
                result="committed",
            )
            return "committed"

    def record_run_terminal(
        self,
        run: ConversationRun,
        outcome: ConversationOutcome,
        *,
        reason: RuntimeEvidenceReason | None,
        failure_code: str | None,
    ) -> None:
        with self._lock:
            self._runtime_evidence.append(
                kind="run_terminal",
                scope_id=self._scope_id,
                run_id=run.run_id,
                user_message_id=run.user_message_id,
                result=outcome.status,
                reason=reason,
                failure_code=failure_code,
            )

    def run_settled(self, run: ConversationRun) -> None:
        with self._lock:
            if self._active is run:
                self._active = None

    def runtime_generate(self, run: ConversationRun, request: ModelRequest) -> RuntimeGeneration:
        """Bridge one semantic request without making tool transport a Core concern."""

        if isinstance(self._runtime, RunAwareConversationRuntime):
            return self._runtime.generate_for_run(
                request,
                scope_id=self._scope_id,
                logical_run_id=run.run_id,
            )
        return self._runtime.generate(request)

    def runtime_cancel(
        self,
        run: ConversationRun,
        generation: RuntimeGeneration,
        reason: ConversationCancellationReason,
    ) -> bool:
        """Record and bridge one physical cancellation request."""

        with self._lock:
            self._record_generation_bound_locked(run, generation)
            self._runtime_evidence.append(
                kind="cancel_requested",
                scope_id=self._scope_id,
                run_id=run.run_id,
                user_message_id=run.user_message_id,
                generation_id=generation.generation_id,
                epoch=generation.epoch,
                reason=reason,
            )
        return self._runtime.cancel(generation.generation_id)
