"""Core-owned lifecycle for the local model sidecar."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from queue import Empty, Full, Queue
from typing import BinaryIO, Final, Literal, cast

from .process_containment import (
    ProcessContainment,
    ProcessContainmentError,
    attach_process_containment,
    process_creation_flags,
)
from .sidecar_protocol import (
    MAX_FRAME_BYTES,
    MAX_RESPONSE_BYTES,
    CancelCommand,
    GenerateCommand,
    GenerationAccepted,
    GenerationCancelled,
    GenerationCompleted,
    GenerationEvent,
    HealthEvent,
    ModelRequest,
    ProtocolError,
    ProtocolErrorCode,
    ReadyEvent,
    RuntimeState,
    ShutdownCommand,
    ShutdownEvent,
    TextDelta,
    encode_command,
    parse_event,
)
from .sidecar_protocol import GenerationFailed as GenerationFailedEvent

DEFAULT_EVENT_QUEUE_SIZE: Final = 128
DEFAULT_STARTUP_TIMEOUT: Final = 30.0
DEFAULT_SHUTDOWN_TIMEOUT: Final = 5.0
DEFAULT_CANCELLATION_TIMEOUT: Final = 7.0
DEFAULT_WRITE_TIMEOUT: Final = 5.0
PHYSICAL_EVIDENCE_CAPACITY: Final = 256
_READ_CHUNK_SIZE: Final = 4096


class ModelRuntimeError(RuntimeError):
    """Base class for Core-side runtime failures."""


class RuntimeNotReady(ModelRuntimeError):
    """The sidecar has not completed its ready handshake."""


class RuntimeBusy(ModelRuntimeError):
    """A generation is already active."""


class RuntimeShuttingDown(ModelRuntimeError):
    """The runtime has closed admission while shutting down."""


class RuntimeStartTimeout(ModelRuntimeError):
    """The sidecar did not become ready before the bounded startup deadline."""


class ShutdownTimeout(ModelRuntimeError):
    """The sidecar did not exit before the bounded shutdown deadline."""


class CancellationTimeout(ModelRuntimeError):
    """The sidecar did not settle cancellation before the bounded deadline."""


class SidecarCrashed(ModelRuntimeError):
    """The sidecar exited or its pipe became unusable before completion."""


class ProtocolViolation(ModelRuntimeError):
    """The sidecar emitted malformed or invalidly correlated protocol data."""


class EventQueueOverflow(ProtocolViolation):
    """The caller did not consume a bounded generation event queue quickly enough."""


class GenerationFailed(ModelRuntimeError):
    """The sidecar reported a terminal generation failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RuntimeHealth:
    state: RuntimeState
    pid: int | None
    error: str | None


@dataclass(frozen=True, slots=True)
class GenerationResult:
    generation_id: str
    status: Literal["completed", "cancelled"]
    text: str


type PhysicalEvidenceKind = Literal[
    "generation_created",
    "protocol_event",
    "generation_terminal",
]
type PhysicalProtocolEvent = Literal[
    "accepted",
    "text_delta",
    "completed",
    "cancelled",
    "failed",
]
type PhysicalEvidenceResult = Literal["completed", "cancelled", "failed"]


@dataclass(frozen=True, slots=True)
class PhysicalGenerationEvidenceRecord:
    """Safe, bounded evidence for one physical Core↔sidecar generation.

    The protocol path supplies the sidecar's ``requestId`` from Core's
    ``generation_id``.  It is therefore intentionally represented by the
    existing generation identity rather than duplicated as another field.
    Text deltas contribute only aggregate counts and byte lengths.
    """

    sequence: int
    kind: PhysicalEvidenceKind
    generation_id: str
    epoch: int
    protocol_event: PhysicalProtocolEvent | None = None
    result: PhysicalEvidenceResult | None = None
    failure_code: str | None = None
    event_count: int = 0
    event_bytes: int = 0


class _PhysicalEvidenceTrace:
    """Bounded storage for immutable physical generation evidence snapshots."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("physical evidence capacity must be positive")
        self._records: deque[PhysicalGenerationEvidenceRecord] = deque(maxlen=capacity)
        self._next_sequence = 1
        self._text_delta_sequences: dict[tuple[str, int], int] = {}
        self._terminal_sequences: dict[tuple[str, int], int] = {}

    def append_generation_created(self, *, generation_id: str, epoch: int) -> None:
        self._append(
            PhysicalGenerationEvidenceRecord(
                sequence=self._next_sequence,
                kind="generation_created",
                generation_id=generation_id,
                epoch=epoch,
            )
        )

    def append_protocol_event(
        self,
        *,
        generation_id: str,
        epoch: int,
        protocol_event: PhysicalProtocolEvent,
        failure_code: str | None = None,
        event_bytes: int = 0,
    ) -> None:
        key = (generation_id, epoch)
        if protocol_event == "text_delta":
            sequence = self._text_delta_sequences.get(key)
            if sequence is not None:
                for index, record in enumerate(self._records):
                    if record.sequence == sequence:
                        self._records[index] = replace(
                            record,
                            event_count=record.event_count + 1,
                            event_bytes=record.event_bytes + event_bytes,
                        )
                        return
                del self._text_delta_sequences[key]

        record = PhysicalGenerationEvidenceRecord(
            sequence=self._next_sequence,
            kind="protocol_event",
            generation_id=generation_id,
            epoch=epoch,
            protocol_event=protocol_event,
            failure_code=failure_code,
            event_count=1,
            event_bytes=event_bytes,
        )
        self._append(record)
        if protocol_event == "text_delta":
            self._text_delta_sequences[key] = record.sequence

    def append_generation_terminal(
        self,
        *,
        generation_id: str,
        epoch: int,
        result: PhysicalEvidenceResult,
        failure_code: str | None = None,
    ) -> None:
        key = (generation_id, epoch)
        if key in self._terminal_sequences:
            return
        record = PhysicalGenerationEvidenceRecord(
            sequence=self._next_sequence,
            kind="generation_terminal",
            generation_id=generation_id,
            epoch=epoch,
            result=result,
            failure_code=failure_code,
        )
        self._append(record)
        self._terminal_sequences[key] = record.sequence

    def snapshot(self) -> tuple[PhysicalGenerationEvidenceRecord, ...]:
        return tuple(replace(record) for record in self._records)

    def _append(self, record: PhysicalGenerationEvidenceRecord) -> None:
        if len(self._records) == self._records.maxlen:
            evicted = self._records[0]
            key = (evicted.generation_id, evicted.epoch)
            if (
                evicted.kind == "protocol_event"
                and evicted.protocol_event == "text_delta"
                and self._text_delta_sequences.get(key) == evicted.sequence
            ):
                del self._text_delta_sequences[key]
            if (
                evicted.kind == "generation_terminal"
                and self._terminal_sequences.get(key) == evicted.sequence
            ):
                del self._terminal_sequences[key]
        self._records.append(record)
        self._next_sequence += 1


class GenerationHandle:
    """A streamed generation owned by one ModelRuntime instance.

    ``events()`` is the sole streaming consumer and must be consumed while a
    generation is running.  ``wait()`` waits only for terminal settlement; it
    does not drain the bounded event queue.  If the queue fills, the runtime
    fails closed and retains the terminal failure for delivery after queued
    events drain.  Text includes only deltas accepted into the queue, so a
    cancelled generation exposes the partial text received before cancellation.
    """

    def __init__(self, generation_id: str, epoch: int, queue_size: int) -> None:
        self.generation_id = generation_id
        self.epoch = epoch
        self._events: Queue[GenerationEvent] = Queue(maxsize=queue_size)
        self._terminal = threading.Event()
        self._lock = threading.Lock()
        self._text_parts: list[str] = []
        self._text_bytes = 0
        self._result: GenerationResult | None = None
        self._failure: ModelRuntimeError | None = None
        self._terminal_event: (
            GenerationCompleted | GenerationCancelled | GenerationFailedEvent | None
        ) = None
        self._terminal_delivered = False
        self._settled = False
        self._consumer_active = False

    def events(self) -> Iterator[GenerationEvent]:
        """Yield accepted, delta, and terminal events in wire order.

        Only one iterator may consume a handle.  A terminal event is retained
        out of band so it is still observable when the bounded event queue was
        full at the point of failure.
        """
        with self._lock:
            if self._consumer_active:
                raise ModelRuntimeError("generation events already have a consumer")
            self._consumer_active = True
        try:
            while True:
                try:
                    event = self._events.get(timeout=0.1)
                except Empty:
                    if self._terminal.is_set():
                        with self._lock:
                            # Producers serialize queue insertion and settlement
                            # under this lock.  Recheck the queue while holding
                            # it so a final delta cannot appear after fallback
                            # terminal delivery.
                            if self._terminal_delivered:
                                return
                            if not self._events.empty() or self._terminal_event is None:
                                continue

                            self._terminal_delivered = True
                            terminal_event = self._terminal_event
                        yield terminal_event
                        return
                    continue
                yield event
        finally:
            with self._lock:
                self._consumer_active = False

    def __iter__(self) -> Iterator[GenerationEvent]:
        return self.events()

    def wait(self, timeout: float | None = None) -> GenerationResult:
        """Wait for the terminal event; failures are raised as Core errors."""
        if not self._terminal.wait(timeout):
            raise TimeoutError("generation did not complete before the deadline")
        with self._lock:
            if self._failure is not None:
                raise self._failure
            if self._result is None:
                raise ModelRuntimeError("generation ended without a result")
            return self._result

    @property
    def text(self) -> str:
        with self._lock:
            return "".join(self._text_parts)

    @property
    def settled(self) -> bool:
        with self._lock:
            return self._settled

    def offer(
        self, event: GenerationEvent
    ) -> Literal["accepted", "queue_full", "output_limit", "settled"]:
        """Offer one non-terminal wire event without exceeding local bounds."""
        with self._lock:
            if self._settled:
                return "settled"
            delta_bytes = 0
            delta_text: str | None = None
            if isinstance(event, TextDelta):
                delta_bytes = len(event.delta.encode("utf-8"))
                delta_text = event.delta
                if self._text_bytes + delta_bytes > MAX_RESPONSE_BYTES:
                    return "output_limit"
            try:
                self._events.put_nowait(event)
            except Full:
                return "queue_full"
            if delta_text is not None:
                self._text_parts.append(delta_text)
                self._text_bytes += delta_bytes
            return "accepted"

    def complete(self, event: GenerationCompleted | GenerationCancelled) -> bool:
        with self._lock:
            if self._settled:
                return False
            self._result = GenerationResult(
                generation_id=self.generation_id,
                status="completed" if isinstance(event, GenerationCompleted) else "cancelled",
                text="".join(self._text_parts),
            )
            self._terminal_event = event
            self._settled = True
            self._terminal.set()
            return True

    def fail(self, event: GenerationFailedEvent, error: ModelRuntimeError) -> bool:
        with self._lock:
            if self._settled:
                return False
            self._failure = error
            self._terminal_event = event
            self._settled = True
            self._terminal.set()
            return True


type _GenerationPhase = Literal[
    "created",
    "accepted",
    "streaming",
    "tool_wait",
    "continuing",
    "cancelling",
    "terminal",
]


@dataclass(slots=True)
class _PendingGeneration:
    handle: GenerationHandle
    generation_id: str
    epoch: int
    phase: _GenerationPhase = "created"
    accepted_seen: bool = False
    cancel_timer: threading.Timer | None = None


class ModelRuntime:
    """Start and supervise one persistent local model sidecar process."""

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        sidecar_dir: Path | None = None,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
        cancellation_timeout: float = DEFAULT_CANCELLATION_TIMEOUT,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
        event_queue_size: int = DEFAULT_EVENT_QUEUE_SIZE,
        physical_evidence_capacity: int = PHYSICAL_EVIDENCE_CAPACITY,
    ) -> None:
        if not all(
            math.isfinite(value) and value > 0
            for value in (
                startup_timeout,
                shutdown_timeout,
                cancellation_timeout,
                write_timeout,
            )
        ):
            raise ValueError("runtime timeouts must be positive")
        if type(event_queue_size) is not int or event_queue_size <= 0:
            raise ValueError("event_queue_size must be a positive integer")
        if type(physical_evidence_capacity) is not int or physical_evidence_capacity <= 0:
            raise ValueError("physical_evidence_capacity must be a positive integer")
        self._command = tuple(command) if command is not None else _default_command()
        self._sidecar_dir = sidecar_dir or _default_sidecar_dir()
        self._startup_timeout = startup_timeout
        self._shutdown_timeout = shutdown_timeout
        self._cancellation_timeout = cancellation_timeout
        self._write_timeout = write_timeout
        self._event_queue_size = event_queue_size
        self._physical_evidence = _PhysicalEvidenceTrace(physical_evidence_capacity)
        self._lock = threading.RLock()
        # Serializes admission and the corresponding wire command.  Holding
        # this lock for the bounded write prevents shutdown from closing
        # admission before an already-reserved generation reaches stdin.
        self._command_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._write_broken = False
        self._state: RuntimeState = "new"
        self._process: subprocess.Popen[bytes] | None = None
        self._containment: ProcessContainment | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._shutdown_complete = threading.Event()
        self._active: _PendingGeneration | None = None
        self._next_epoch = 0
        self._failure: ModelRuntimeError | None = None
        self._shutdown_requested = False
        self._shutdown_event_seen = False
        self._reader_eof = False
        self._process_exit_code: int | None = None
        self._last_cancel: tuple[str, int] | None = None

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._state == "ready"

    def health(self) -> RuntimeHealth:
        with self._lock:
            return RuntimeHealth(
                state=self._state,
                pid=self._process.pid if self._process is not None else None,
                error=str(self._failure) if self._failure is not None else None,
            )

    def physical_evidence(self) -> tuple[PhysicalGenerationEvidenceRecord, ...]:
        """Return a bounded snapshot of safe physical sidecar evidence.

        This trace is process-ephemeral and independent from canonical
        conversation history.  It contains no request text, provider payload,
        auth material, or exception body.
        """

        with self._lock:
            return self._physical_evidence.snapshot()

    def start(self) -> None:
        with self._lock:
            if self._state in {"ready", "busy"}:
                return
            if self._state == "shutting_down":
                raise RuntimeShuttingDown("runtime is shutting down")
            if self._state == "closed":
                raise ModelRuntimeError("runtime is closed")
            if self._state == "failed":
                raise self._failure or ModelRuntimeError("runtime has failed")
            if self._state == "starting":
                pass
            else:
                self._state = "starting"
                self._failure = None
                self._shutdown_requested = False
                self._shutdown_event_seen = False
                self._process_exit_code = None
                self._last_cancel = None
                self._write_broken = False
                self._ready.clear()
                self._shutdown_complete.clear()
                try:
                    creation_flags = process_creation_flags()
                    popen_options: dict[str, object] = {
                        "cwd": str(self._sidecar_dir),
                        "stdin": subprocess.PIPE,
                        "stdout": subprocess.PIPE,
                        "stderr": subprocess.PIPE,
                        "bufsize": 0,
                    }
                    if creation_flags:
                        popen_options["creationflags"] = creation_flags
                    process = subprocess.Popen(self._command, **popen_options)  # type: ignore[arg-type]
                except OSError as error:
                    failure = SidecarCrashed(f"could not start sidecar: {error.__class__.__name__}")
                    self._state = "failed"
                    self._failure = failure
                    self._ready.set()
                    raise failure from error
                if process.stdout is None or process.stdin is None:
                    process.kill()
                    failure = SidecarCrashed("sidecar pipes were not created")
                    self._state = "failed"
                    self._failure = failure
                    self._ready.set()
                    raise failure
                try:
                    containment = attach_process_containment(
                        process,
                        suspended=bool(creation_flags),
                    )
                except (OSError, ProcessContainmentError) as error:
                    _terminate_process(process)
                    _close_pipes(process)
                    failure = SidecarCrashed("could not establish sidecar process containment")
                    self._state = "failed"
                    self._failure = failure
                    self._ready.set()
                    raise failure from error
                self._process = process
                self._containment = containment
                self._reader_thread = threading.Thread(
                    target=self._read_stdout,
                    args=(process.stdout,),
                    name="lilavel-sidecar-reader",
                    daemon=True,
                )
                self._reader_thread.start()
                if process.stderr is not None:
                    self._stderr_thread = threading.Thread(
                        target=self._drain_stderr,
                        args=(process.stderr,),
                        name="lilavel-sidecar-stderr",
                        daemon=True,
                    )
                    self._stderr_thread.start()
        self._ready.wait(self._startup_timeout)
        if not self.ready:
            with self._lock:
                failure = self._failure
            if failure is not None:
                raise failure
            if self.health().state == "shutting_down":
                raise RuntimeShuttingDown("runtime shutdown began during startup")
            timeout = RuntimeStartTimeout("sidecar did not become ready")
            self._fail_runtime(timeout)
            raise timeout

    def generate(self, request: ModelRequest) -> GenerationHandle:
        with self._command_lock:
            return self._generate_impl(request)

    def _generate_impl(self, request: ModelRequest) -> GenerationHandle:
        with self._lock:
            self._ensure_ready()
            if self._active is not None:
                raise RuntimeBusy("a generation is already active")
            self._next_epoch += 1
            generation_id = str(uuid.uuid4())
            handle = GenerationHandle(generation_id, self._next_epoch, self._event_queue_size)
            pending = _PendingGeneration(handle, generation_id, self._next_epoch)
            try:
                command = GenerateCommand(
                    generation_id,
                    self._next_epoch,
                    prompt=request.prompt,
                    messages=request.messages,
                    system_prompt=request.system_prompt,
                )
            except (AttributeError, TypeError) as error:
                raise ProtocolViolation("invalid generation request") from error
            self._active = pending
            self._state = "busy"
            self._last_cancel = None
            self._physical_evidence.append_generation_created(
                generation_id=generation_id,
                epoch=self._next_epoch,
            )
        try:
            self._write_command(command)
        except ProtocolError as error:
            failure = ProtocolViolation(f"could not encode generation request: {error.reason}")
            with self._lock:
                if self._active is pending:
                    self._fail_pending_locked(failure)
            raise failure from error
        except OSError as error:
            failure = SidecarCrashed("sidecar input pipe failed")
            with self._lock:
                if self._active is pending:
                    self._fail_pending_locked(failure)
                process = self._fail_runtime_locked(failure)
            if process is not None:
                _terminate_process(process)
            raise failure from error
        return handle

    def cancel(self, generation_id: str) -> bool:
        with self._command_lock:
            return self._cancel_impl(generation_id)

    def _cancel_impl(self, generation_id: str) -> bool:
        with self._lock:
            pending = self._active
            if pending is None:
                # Cancellation is idempotent for the most recent request.  A
                # sidecar can deliver ``cancelled`` synchronously while the
                # caller is still deciding whether to retry the call.
                return self._last_cancel is not None and self._last_cancel[0] == generation_id
            if pending.generation_id != generation_id or pending.phase == "terminal":
                return False
            if pending.phase == "cancelling":
                self._last_cancel = (pending.generation_id, pending.epoch)
                return True
            pending.phase = "cancelling"
            self._last_cancel = (pending.generation_id, pending.epoch)
            self._arm_cancellation_watchdog_locked(pending)
            command = CancelCommand(pending.generation_id, pending.epoch)
        try:
            self._write_command(command)
        except OSError as error:
            failure = SidecarCrashed("sidecar input pipe failed during cancellation")
            with self._lock:
                if self._active is pending:
                    self._fail_pending_locked(failure)
                process = self._fail_runtime_locked(failure)
            if process is not None:
                _terminate_process(process)
            raise failure from error
        return True

    def shutdown(self) -> None:
        with self._command_lock:
            self._shutdown_impl()

    def _shutdown_impl(self) -> None:
        cancel_command: CancelCommand | None = None
        shutdown_command = ShutdownCommand()
        with self._lock:
            if self._state == "closed":
                return
            if self._state == "new":
                self._state = "closed"
                return
            if self._state == "failed":
                failed_at_start = True
                process = self._process
                containment = self._containment
                self._shutdown_requested = True
                pending = self._active
                if pending is not None:
                    self._fail_pending_locked(self._failure or SidecarCrashed("runtime failed"))
                self._shutdown_complete.set()
            else:
                failed_at_start = False
                # Admission closes while holding the same lock used by
                # generate().  No new request can slip between this point and
                # teardown, even if the caller is concurrently generating.
                self._shutdown_requested = True
                self._state = "shutting_down"
                # Wake a concurrent start() waiter so it observes the typed
                # admission-closed state instead of waiting for startup timeout.
                self._ready.set()
                self._shutdown_event_seen = False
                self._process_exit_code = None
                process = self._process
                containment = self._containment
                pending = self._active
                if pending is not None and pending.phase != "terminal":
                    pending.phase = "cancelling"
                    self._last_cancel = (pending.generation_id, pending.epoch)
                    self._arm_cancellation_watchdog_locked(pending)
                    cancel_command = CancelCommand(pending.generation_id, pending.epoch)

        if failed_at_start:
            containment_ok = True
            if process is not None and process.poll() is None:
                containment_ok = _terminate_owned_process(process, containment)
            elif containment is not None:
                containment_ok = _close_ownership(containment)
            if process is not None:
                _close_pipes(process)
            with self._lock:
                self._process = None
                if containment_ok:
                    self._containment = None
            return

        deadline = time.monotonic() + self._shutdown_timeout
        shutdown_failure: ModelRuntimeError | None = None
        commands: list[CancelCommand | ShutdownCommand] = []
        if cancel_command is not None:
            commands.append(cancel_command)
        commands.append(shutdown_command)
        for command in commands:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                shutdown_failure = ShutdownTimeout("sidecar did not shut down before the deadline")
                break
            try:
                self._write_command(command, timeout=min(self._write_timeout, remaining))
            except OSError:
                shutdown_failure = SidecarCrashed("sidecar input pipe failed during shutdown")
                with self._lock:
                    process_to_terminate = self._fail_runtime_locked(shutdown_failure)
                if process_to_terminate is not None:
                    _terminate_process(process_to_terminate)
                break

        timed_out = False
        containment_ok = True
        if process is not None:
            remaining = max(0.0, deadline - time.monotonic())
            if shutdown_failure is None:
                completed = self._shutdown_complete.wait(remaining)
                timed_out = not completed
            try:
                if timed_out:
                    raise subprocess.TimeoutExpired(self._command[0], 0)
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
                # Settle the timed-out operation before containment changes
                # the child's exit code. Its forced exit is a consequence of
                # this deadline, not a new spontaneous SidecarCrashed result.
                with self._lock:
                    if self._failure is None:
                        timeout_failure = shutdown_failure or ShutdownTimeout(
                            "sidecar did not shut down before the deadline"
                        )
                        self._state = "failed"
                        self._failure = timeout_failure
                        self._fail_pending_locked(timeout_failure)
                        self._ready.set()
                        self._shutdown_complete.set()
                containment_ok = _terminate_owned_process(process, containment)
            exit_code = process.poll()
            if not timed_out:
                containment_ok = _close_ownership(containment)
            elif containment is not None:
                containment_ok = _close_ownership(containment) and containment_ok
            reader = self._reader_thread
            if reader is not None:
                reader.join(timeout=max(0.0, deadline - time.monotonic()))
            _close_pipes(process)
        else:
            exit_code = None
            containment_ok = _close_ownership(containment)

        joined_shutdown_failure = self._await_shutdown_settlement(deadline)
        if shutdown_failure is None and joined_shutdown_failure is not None:
            shutdown_failure = joined_shutdown_failure

        with self._lock:
            observed_failure = self._failure
            shutdown_ack = self._shutdown_event_seen
            pending_unsettled = self._active is not None
            if self._active is not None:
                pending_failure = shutdown_failure or observed_failure
                if pending_failure is None and timed_out:
                    pending_failure = ShutdownTimeout("generation did not settle before shutdown")
                if pending_failure is None:
                    pending_failure = SidecarCrashed(
                        "generation did not emit a terminal event before shutdown"
                    )
                self._fail_pending_locked(pending_failure)

            if shutdown_failure is None:
                if timed_out:
                    shutdown_failure = ShutdownTimeout(
                        "sidecar did not shut down before the deadline"
                    )
                elif observed_failure is not None:
                    shutdown_failure = observed_failure
                elif process is None:
                    shutdown_failure = SidecarCrashed("sidecar process was unavailable")
                elif exit_code != 0:
                    shutdown_failure = SidecarCrashed("sidecar exited abnormally during shutdown")
                elif not self._reader_eof:
                    shutdown_failure = SidecarCrashed("sidecar reader did not reach valid EOF")
                elif not shutdown_ack:
                    shutdown_failure = SidecarCrashed(
                        "sidecar exited without shutdown acknowledgement"
                    )
                elif pending_unsettled:
                    shutdown_failure = SidecarCrashed(
                        "generation did not emit a terminal event before shutdown"
                    )
                elif not containment_ok:
                    shutdown_failure = SidecarCrashed(
                        "sidecar process containment could not be confirmed"
                    )

            if shutdown_failure is None:
                self._state = "closed"
                self._failure = None
            else:
                self._state = "failed"
                self._failure = shutdown_failure
            self._process = None
            if containment_ok:
                self._containment = None
            self._shutdown_complete.set()

        if shutdown_failure is not None:
            if isinstance(shutdown_failure, ShutdownTimeout):
                raise shutdown_failure
            raise shutdown_failure

    def _await_shutdown_settlement(self, deadline: float) -> ModelRuntimeError | None:
        """Allow an extended runtime to join application-owned work before closure."""

        del deadline
        return None

    def _ensure_ready(self) -> None:
        if self._shutdown_requested or self._state == "shutting_down":
            raise RuntimeShuttingDown("runtime is shutting down")
        if self._state != "ready":
            if self._state == "busy":
                raise RuntimeBusy("a generation is already active")
            raise RuntimeNotReady(f"runtime is {self._state}")

    def _write_command(
        self,
        command: GenerateCommand | CancelCommand | ShutdownCommand,
        *,
        timeout: float | None = None,
    ) -> None:
        payload = encode_command(command)
        with self._lock:
            process = self._process
            if process is None or process.stdin is None:
                raise OSError("sidecar stdin is unavailable")
            stream = process.stdin

        with self._write_lock:
            if self._write_broken:
                raise OSError("sidecar input pipe is unavailable")
            completed = threading.Event()
            failure: list[BaseException] = []

            def write() -> None:
                try:
                    stream.write(payload)
                    stream.flush()
                except (OSError, ValueError) as error:
                    failure.append(error)
                finally:
                    completed.set()

            threading.Thread(target=write, name="lilavel-sidecar-writer", daemon=True).start()
            wait_timeout = self._write_timeout if timeout is None else timeout
            if not completed.wait(wait_timeout):
                with self._lock:
                    self._write_broken = True
                raise OSError("sidecar input pipe write timed out")
            if failure:
                with self._lock:
                    self._write_broken = True
                raise OSError("sidecar input pipe failed") from failure[0]

    def _read_stdout(self, stream: BinaryIO) -> None:
        reached_eof = False
        try:
            for frame in _iter_frames(stream):
                self._dispatch(parse_event(frame))
            reached_eof = True
        except ProtocolError as error:
            self._fail_runtime(ProtocolViolation(f"invalid sidecar protocol: {error.reason}"))
        except BaseException as error:
            # This background thread is a containment boundary: no unexpected
            # exception (including thread termination) may certify shutdown.
            self._fail_runtime(SidecarCrashed(f"sidecar output failed: {error.__class__.__name__}"))
        finally:
            with self._lock:
                self._reader_eof = reached_eof
                expected_shutdown = self._shutdown_requested
                process = self._process
                self._process_exit_code = process.poll() if process is not None else None
                exit_code = self._process_exit_code
                self._shutdown_complete.set()
            if expected_shutdown:
                if exit_code not in (None, 0):
                    self._fail_runtime(SidecarCrashed("sidecar exited abnormally during shutdown"))
            else:
                self._fail_runtime(SidecarCrashed("sidecar exited before shutdown"))

    def _drain_stderr(self, stream: BinaryIO) -> None:
        while stream.read(_READ_CHUNK_SIZE):
            pass

    def _dispatch(self, event: GenerationEvent) -> None:
        if isinstance(event, ReadyEvent):
            with self._lock:
                if self._state == "shutting_down":
                    # Startup may complete after shutdown closes admission.
                    # The handshake is still valid, but it must not reopen
                    # admission or strand the start() waiter.
                    self._ready.set()
                    return
                if self._state != "starting":
                    self._fail_runtime_locked(ProtocolViolation("unexpected ready event"))
                    return
                self._state = "ready"
                self._ready.set()
            return
        if isinstance(event, HealthEvent):
            return
        if isinstance(event, ShutdownEvent):
            with self._lock:
                if not self._shutdown_requested:
                    self._fail_runtime_locked(ProtocolViolation("unexpected shutdown event"))
                    return
                self._shutdown_event_seen = True
            return
        if isinstance(event, GenerationFailedEvent) and event.generation_id is None:
            with self._lock:
                failure: ModelRuntimeError = GenerationFailed(event.code)
                self._fail_runtime_locked(failure)
                self._ready.set()
            return
        cancel_after_limit = False
        cancel_identity: tuple[str, int] | None = None
        if isinstance(event, GenerationFailedEvent):
            if event.generation_id is None or event.epoch is None:
                self._fail_runtime(ProtocolViolation("failed event has no generation identity"))
                return
            generation_id = event.generation_id
            epoch = event.epoch
        else:
            generation_id = event.generation_id
            epoch = event.epoch
        with self._lock:
            pending = self._active
            if pending is None:
                if epoch <= self._next_epoch:
                    return
                self._fail_runtime_locked(ProtocolViolation("future generation event"))
                return
            if generation_id != pending.generation_id:
                if epoch < pending.epoch:
                    return
                self._fail_runtime_locked(ProtocolViolation("generation identity mismatch"))
                return
            if epoch != pending.epoch:
                if epoch < pending.epoch:
                    return
                self._fail_runtime_locked(ProtocolViolation("generation epoch mismatch"))
                return

            protocol_event = _physical_protocol_event(event)
            if protocol_event is not None:
                self._physical_evidence.append_protocol_event(
                    generation_id=pending.generation_id,
                    epoch=pending.epoch,
                    protocol_event=protocol_event,
                    failure_code=event.code if isinstance(event, GenerationFailedEvent) else None,
                    event_bytes=(
                        len(event.delta.encode("utf-8")) if isinstance(event, TextDelta) else 0
                    ),
                )

            if isinstance(event, GenerationAccepted):
                # Cancellation is a caller intent, not proof that the request
                # was never admitted by the sidecar.  ``accepted`` may race
                # with the cancel command exactly once.
                if (
                    pending.phase not in {"created", "cancelling"}
                    or pending.accepted_seen
                    or pending.handle.offer(event) != "accepted"
                ):
                    self._fail_runtime_locked(ProtocolViolation("invalid accepted transition"))
                else:
                    pending.accepted_seen = True
                    if pending.phase == "created":
                        pending.phase = "accepted"
            elif isinstance(event, TextDelta):
                if pending.phase in {"cancelling", "terminal"}:
                    return
                if pending.phase not in {"accepted", "streaming"}:
                    self._fail_runtime_locked(ProtocolViolation("delta before accepted"))
                else:
                    offer_result = pending.handle.offer(event)
                    if offer_result == "queue_full":
                        self._fail_runtime_locked(
                            EventQueueOverflow("generation event buffer is full")
                        )
                    elif offer_result == "output_limit":
                        pending.phase = "cancelling"
                        pending.handle.fail(
                            GenerationFailedEvent(
                                pending.generation_id, pending.epoch, "output_limit"
                            ),
                            GenerationFailed("output_limit"),
                        )
                        self._last_cancel = (pending.generation_id, pending.epoch)
                        self._arm_cancellation_watchdog_locked(pending)
                        cancel_after_limit = True
                        cancel_identity = (pending.generation_id, pending.epoch)
                    elif offer_result == "accepted":
                        pending.phase = "streaming"
            elif isinstance(event, GenerationCompleted):
                if pending.phase not in {"accepted", "streaming", "cancelling"}:
                    self._fail_runtime_locked(ProtocolViolation("invalid completion transition"))
                else:
                    pending.phase = "terminal"
                    pending.handle.complete(event)
                    self._physical_evidence.append_generation_terminal(
                        generation_id=pending.generation_id,
                        epoch=pending.epoch,
                        result="completed",
                    )
                    self._finish_pending_locked(pending)
            elif isinstance(event, GenerationCancelled):
                if pending.phase not in {"accepted", "streaming", "cancelling"}:
                    self._fail_runtime_locked(ProtocolViolation("invalid cancellation transition"))
                else:
                    pending.phase = "terminal"
                    pending.handle.complete(event)
                    self._physical_evidence.append_generation_terminal(
                        generation_id=pending.generation_id,
                        epoch=pending.epoch,
                        result="cancelled",
                    )
                    self._finish_pending_locked(pending)
            else:
                if event.code in {
                    "cleanup_timeout",
                    "cleanup_error",
                    "shutdown_timeout",
                    "cancellation_timeout",
                }:
                    # These codes mean provider cleanup was not confirmed.  A
                    # fresh generation cannot be admitted on this process.
                    self._fail_runtime_locked(GenerationFailed(event.code))
                else:
                    pending.phase = "terminal"
                    pending.handle.fail(event, GenerationFailed(event.code))
                    self._physical_evidence.append_generation_terminal(
                        generation_id=pending.generation_id,
                        epoch=pending.epoch,
                        result="failed",
                        failure_code=event.code,
                    )
                    self._finish_pending_locked(pending)
        if cancel_after_limit:
            try:
                if cancel_identity is None:
                    raise OSError("missing generation identity")
                self._write_command(CancelCommand(*cancel_identity))
            except OSError:
                self._fail_runtime(SidecarCrashed("sidecar input pipe failed after output limit"))

    def _finish_pending_locked(self, pending: _PendingGeneration) -> None:
        if self._active is pending:
            self._active = None
            if pending.cancel_timer is not None:
                pending.cancel_timer.cancel()
                pending.cancel_timer = None
            if self._state == "busy":
                self._state = "ready"

    def _fail_pending_locked(self, failure: ModelRuntimeError) -> None:
        pending = self._active
        if pending is None:
            return
        already_terminal = pending.phase == "terminal"
        pending.phase = "terminal"
        event = GenerationFailedEvent(
            pending.generation_id,
            pending.epoch,
            _failure_code(failure),
        )
        pending.handle.fail(event, failure)
        if not already_terminal:
            self._physical_evidence.append_generation_terminal(
                generation_id=pending.generation_id,
                epoch=pending.epoch,
                result="failed",
                failure_code=_physical_failure_code(failure),
            )
        self._finish_pending_locked(pending)

    def _arm_cancellation_watchdog_locked(self, pending: _PendingGeneration) -> None:
        if pending.cancel_timer is not None:
            return
        timer = threading.Timer(
            self._cancellation_timeout,
            self._cancellation_expired,
            args=(pending.generation_id, pending.epoch),
        )
        timer.daemon = True
        pending.cancel_timer = timer
        timer.start()

    def _cancellation_expired(self, generation_id: str, epoch: int) -> None:
        with self._lock:
            pending = self._active
            if (
                pending is None
                or pending.generation_id != generation_id
                or pending.epoch != epoch
                or pending.phase != "cancelling"
            ):
                return
            failure = CancellationTimeout("sidecar did not settle cancellation before the deadline")
            process = self._fail_runtime_locked(failure)
        if process is not None:
            _terminate_process(process)

    def _fail_runtime(self, failure: ModelRuntimeError) -> None:
        with self._lock:
            process = self._fail_runtime_locked(failure)
        if process is not None:
            _terminate_process(process)

    def _fail_runtime_locked(self, failure: ModelRuntimeError) -> subprocess.Popen[bytes] | None:
        if self._state in {"closed", "failed"}:
            return None
        self._state = "failed"
        self._failure = failure
        self._ready.set()
        self._shutdown_complete.set()
        pending = self._active
        if pending is not None:
            self._fail_pending_locked(failure)
        process = self._process
        containment = self._containment
        if process is not None and (process.poll() is None or containment is not None):
            # The reader thread may be the caller and cannot synchronously
            # terminate a process while other teardown code is waiting on it.
            threading.Thread(
                target=_terminate_owned_process,
                args=(process, containment),
                daemon=True,
            ).start()
        return None


def _default_sidecar_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "model-sidecar"


def _failure_code(failure: ModelRuntimeError) -> ProtocolErrorCode:
    if isinstance(failure, CancellationTimeout):
        return "cancellation_timeout"
    if isinstance(failure, ShutdownTimeout):
        return "shutdown_timeout"
    if isinstance(failure, GenerationFailed):
        if failure.code in {
            "busy",
            "not_ready",
            "closed",
            "not_active",
            "startup_error",
            "provider_error",
            "unsupported_output",
            "protocol_error",
            "output_limit",
            "cancelled",
            "cleanup_timeout",
            "cleanup_error",
            "shutdown_timeout",
            "cancellation_timeout",
        }:
            return cast(ProtocolErrorCode, failure.code)
        return "provider_error"
    if isinstance(failure, ProtocolViolation):
        return "protocol_error"
    return "provider_error"


def _physical_protocol_event(event: GenerationEvent) -> PhysicalProtocolEvent | None:
    if isinstance(event, GenerationAccepted):
        return "accepted"
    if isinstance(event, TextDelta):
        return "text_delta"
    if isinstance(event, GenerationCompleted):
        return "completed"
    if isinstance(event, GenerationCancelled):
        return "cancelled"
    if isinstance(event, GenerationFailedEvent):
        return "failed"
    return None


def _physical_failure_code(failure: ModelRuntimeError) -> str:
    if isinstance(failure, EventQueueOverflow):
        return "event_queue_overflow"
    if isinstance(failure, CancellationTimeout):
        return "cancellation_timeout"
    if isinstance(failure, ShutdownTimeout):
        return "shutdown_timeout"
    if isinstance(failure, GenerationFailed):
        return failure.code
    if isinstance(failure, ProtocolViolation):
        return "protocol_error"
    if isinstance(failure, SidecarCrashed):
        return "sidecar_crashed"
    return "runtime_error"


def _default_command() -> tuple[str, ...]:
    executable = shutil.which("npx") or shutil.which("npx.cmd")
    if executable is None:
        executable = "npx.cmd" if os.name == "nt" else "npx"
    return (executable, "--yes", "bun@1.4.0", "run", "protocol")


def _iter_frames(stream: BinaryIO) -> Iterator[bytes]:
    buffered = bytearray()
    while True:
        chunk = stream.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        line_start = 0
        for index, value in enumerate(chunk):
            if value != 0x0A:
                continue
            line = bytes(buffered) + chunk[line_start:index]
            buffered.clear()
            line_start = index + 1
            if line.endswith(b"\r"):
                line = line[:-1]
            if len(line) > MAX_FRAME_BYTES or not line:
                raise ProtocolError(
                    "frame_too_large" if len(line) > MAX_FRAME_BYTES else "malformed"
                )
            yield line
        remainder = chunk[line_start:]
        if remainder:
            if len(buffered) + len(remainder) > MAX_FRAME_BYTES:
                raise ProtocolError("frame_too_large")
            buffered.extend(remainder)
    if buffered:
        raise ProtocolError("malformed")


def _terminate_owned_process(
    process: subprocess.Popen[bytes], containment: ProcessContainment | None
) -> bool:
    """Terminate the sidecar and its owned descendants when available."""

    if containment is not None:
        try:
            return containment.terminate(timeout=1.0)
        except (OSError, ProcessContainmentError):
            return False
    _terminate_process(process)
    return process.poll() is not None


def _close_ownership(containment: ProcessContainment | None) -> bool:
    if containment is None:
        return True
    try:
        return containment.close()
    except (OSError, ProcessContainmentError):
        return False


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        with suppress(OSError):
            process.kill()


def _close_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            with suppress(OSError, ValueError):
                stream.close()
