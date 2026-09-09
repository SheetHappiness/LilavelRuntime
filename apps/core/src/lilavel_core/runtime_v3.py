"""Explicit opt-in V3 model host with bounded application tool continuation."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, replace
from typing import BinaryIO, Final, Literal, cast

from lilavel_contracts import ToolCall, ToolResult

from .runtime import (
    GenerationHandle,
    ModelRuntime,
    ModelRuntimeError,
    ProtocolViolation,
    SidecarCrashed,
    _iter_frames,  # pyright: ignore[reportPrivateUsage]
    _PendingGeneration,  # pyright: ignore[reportPrivateUsage]
    _terminate_owned_process,  # pyright: ignore[reportPrivateUsage]
)
from .sidecar_protocol import (
    GenerateCommand,
    GenerationCancelled,
    GenerationCompleted,
    ModelRequest,
    ProtocolError,
    TextDelta,
)
from .sidecar_protocol import (
    GenerationFailed as GenerationFailedEvent,
)
from .sidecar_protocol_v3 import (
    GenerateToolsCommand,
    ToolCallsEvent,
    ToolResultsCommand,
    V3Command,
    encode_v3_command,
    parse_v3_event,
)
from .tool_runtime import (
    DEFAULT_TOOL_GENERATION_DEADLINE,
    DEFAULT_TOOL_RESULT_WAIT_DEADLINE,
    MAX_TOOL_CALLS_PER_BATCH,
    MAX_TOOL_CALLS_PER_GENERATION,
    MAX_TOOL_ROUNDS_PER_GENERATION,
    ApplicationToolSession,
    ApplicationToolSessionFactory,
    ToolBatchCorrelation,
    ToolGenerationContext,
    ToolSessionUncontained,
)

TOOL_EVIDENCE_CAPACITY: Final = 256

type ToolEvidenceKind = Literal[
    "tool_requested",
    "execution_started",
    "execution_settled",
    "result_consumed",
    "result_discarded",
    "joined_settlement",
]
type JoinedSettlement = Literal["not_applicable", "settled", "cancelled", "failed", "uncontained"]


class ToolLoopError(ModelRuntimeError):
    """Base class for deterministic V3 tool lifecycle failure."""


class ToolResultWaitTimeout(ToolLoopError):
    """The application did not produce a result batch before the bound."""


class ToolGenerationTimeout(ToolLoopError):
    """A tool-enabled generation exceeded its overall wall-clock bound."""


class ToolExecutorUncontained(ToolLoopError):
    """The application executor could not be confirmed settled."""


@dataclass(frozen=True, slots=True)
class ToolLifecycleEvidenceRecord:
    sequence: int
    kind: ToolEvidenceKind
    generation_id: str
    epoch: int
    round: int | None = None
    call_count: int = 0
    result_code: str | None = None
    settlement: JoinedSettlement | None = None
    raw_correspondence: Literal["pass"] | None = None


@dataclass(slots=True)
class _ToolGenerationState:
    context: ToolGenerationContext
    session: ApplicationToolSession | None
    cancelled: threading.Event
    next_round: int = 1
    total_calls: int = 0
    pending_calls: tuple[ToolCall, ...] | None = None
    result_batch_consumed: bool = False
    executor_thread: threading.Thread | None = None
    result_timer: threading.Timer | None = None
    overall_timer: threading.Timer | None = None
    deferred_terminal: object | None = None
    deferred_failure: ModelRuntimeError | None = None
    failure_coordinator_started: bool = False


class ModelRuntimeV3(ModelRuntime):
    """Inactive-by-default V3 host; one identity spans bounded provider turns."""

    def __init__(
        self,
        *,
        tool_session_factory: ApplicationToolSessionFactory | None = None,
        tool_result_wait_deadline: float = DEFAULT_TOOL_RESULT_WAIT_DEADLINE,
        tool_generation_deadline: float = DEFAULT_TOOL_GENERATION_DEADLINE,
        tool_settlement_deadline: float = 10.0,
        tool_evidence_capacity: int = TOOL_EVIDENCE_CAPACITY,
        **kwargs: object,
    ) -> None:
        if (
            tool_result_wait_deadline <= 0
            or tool_generation_deadline <= 0
            or tool_settlement_deadline <= 0
        ):
            raise ValueError("tool-loop deadlines must be positive")
        if tool_evidence_capacity <= 0:
            raise ValueError("tool evidence capacity must be positive")
        if "command" not in kwargs:
            kwargs["command"] = _default_v3_command()
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._tool_session_factory = tool_session_factory
        self._tool_result_wait_deadline = tool_result_wait_deadline
        self._tool_generation_deadline = tool_generation_deadline
        self._tool_settlement_deadline = tool_settlement_deadline
        self._runtime_instance_id = str(uuid.uuid4())
        self._tool_state: _ToolGenerationState | None = None
        self._tool_evidence: deque[ToolLifecycleEvidenceRecord] = deque(
            maxlen=tool_evidence_capacity
        )
        self._tool_evidence_sequence = 1

    def tool_evidence(self) -> tuple[ToolLifecycleEvidenceRecord, ...]:
        with self._lock:
            return tuple(replace(record) for record in self._tool_evidence)

    def generate(self, request: ModelRequest) -> GenerationHandle:
        """Start an explicit V3 generation without application tools."""

        return self.generate_for_run(request, scope_id="unscoped", logical_run_id="unscoped")

    def generate_for_run(
        self,
        request: ModelRequest,
        *,
        scope_id: str,
        logical_run_id: str,
    ) -> GenerationHandle:
        """Bind application-local correlation before exposing any tool snapshot."""

        with self._command_lock:
            with self._lock:
                self._ensure_ready()
                if self._active is not None:
                    raise ProtocolViolation("a V3 generation is already active")
                self._next_epoch += 1
                generation_id = str(uuid.uuid4())
                epoch = self._next_epoch
                handle = GenerationHandle(generation_id, epoch, self._event_queue_size)
                pending = _PendingGeneration(handle, generation_id, epoch)
                context = ToolGenerationContext(
                    runtime_instance_id=self._runtime_instance_id,
                    scope_id=scope_id,
                    logical_run_id=logical_run_id,
                    generation_id=generation_id,
                    epoch=epoch,
                )
                try:
                    session = (
                        None
                        if self._tool_session_factory is None
                        else self._tool_session_factory.create(context)
                    )
                    command = GenerateToolsCommand(
                        generation_id,
                        epoch,
                        request,
                        () if session is None else session.specs,
                    )
                except (AttributeError, TypeError, ValueError) as error:
                    raise ProtocolViolation("invalid V3 generation request") from error
                state = _ToolGenerationState(context, session, threading.Event())
                self._active = pending
                self._tool_state = state
                self._state = "busy"
                self._last_cancel = None
                self._physical_evidence.append_generation_created(
                    generation_id=generation_id,
                    epoch=epoch,
                )
                if session is not None:
                    timer = threading.Timer(
                        self._tool_generation_deadline,
                        self._tool_generation_expired,
                        args=(generation_id, epoch),
                    )
                    timer.daemon = True
                    state.overall_timer = timer
                    timer.start()
            try:
                self._write_command(command)
            except ProtocolError as error:
                failure = ProtocolViolation(
                    f"could not encode V3 generation request: {error.reason}"
                )
                self._fail_runtime(failure)
                raise failure from error
            except OSError as error:
                failure = SidecarCrashed("sidecar input pipe failed")
                self._fail_runtime(failure)
                raise failure from error
            return handle

    def _write_command(
        self,
        command: V3Command | GenerateCommand,
        *,
        timeout: float | None = None,
    ) -> None:
        if isinstance(command, GenerateCommand):
            raise ProtocolError("V2 generate is unavailable on the V3 host")
        payload = encode_v3_command(command)
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

            threading.Thread(target=write, name="lilavel-v3-sidecar-writer", daemon=True).start()
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
                self._dispatch(parse_v3_event(frame))
            reached_eof = True
        except ProtocolError as error:
            self._fail_runtime(ProtocolViolation(f"invalid V3 sidecar protocol: {error.reason}"))
        except BaseException as error:
            self._fail_runtime(
                SidecarCrashed(f"V3 sidecar output failed: {error.__class__.__name__}")
            )
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
                    self._fail_runtime(
                        SidecarCrashed("V3 sidecar exited abnormally during shutdown")
                    )
            else:
                self._fail_runtime(SidecarCrashed("V3 sidecar exited before shutdown"))

    def _dispatch(self, event: object) -> None:
        if isinstance(event, ToolCallsEvent):
            self._admit_tool_calls(event)
            return

        if isinstance(
            event,
            (GenerationCompleted, GenerationCancelled, GenerationFailedEvent),
        ):
            with self._lock:
                pending = self._active
                state = self._tool_state
                if (
                    pending is not None
                    and state is not None
                    and _matches(pending, event)
                    and state.pending_calls is not None
                ):
                    if isinstance(event, GenerationCompleted):
                        self._fail_runtime_locked(
                            ProtocolViolation("completion while tool results are pending")
                        )
                        return
                    state.cancelled.set()
                    if state.session is not None:
                        state.session.cancel()
                    if (
                        state.executor_thread is not None
                        and state.session is not None
                        and not state.session.wait_settled(0)
                    ):
                        state.deferred_terminal = event
                        return
                    state.pending_calls = None
                    if state.result_timer is not None:
                        state.result_timer.cancel()
                        state.result_timer = None
                    pending.phase = "cancelling"

        with self._lock:
            pending = self._active
            if pending is not None and isinstance(event, (TextDelta, GenerationCompleted)):
                if pending.phase == "continuing":
                    pending.phase = "accepted"
                elif pending.phase == "tool_wait":
                    self._fail_runtime_locked(
                        ProtocolViolation("provider output while tool results are pending")
                    )
                    return
        super()._dispatch(cast(object, event))  # type: ignore[arg-type]

    def _admit_tool_calls(self, event: ToolCallsEvent) -> None:
        with self._lock:
            pending = self._active
            state = self._tool_state
            if pending is None or state is None:
                self._fail_runtime_locked(ProtocolViolation("tool batch without active generation"))
                return
            if not _matches(pending, event):
                if event.epoch < pending.epoch:
                    return
                self._fail_runtime_locked(ProtocolViolation("tool batch identity mismatch"))
                return
            if (
                state.session is None
                or pending.phase not in {"accepted", "streaming", "continuing"}
                or event.round != state.next_round
                or state.pending_calls is not None
                or state.result_batch_consumed
                or len(event.calls) > MAX_TOOL_CALLS_PER_BATCH
                or state.total_calls + len(event.calls) > MAX_TOOL_CALLS_PER_GENERATION
                or event.round > MAX_TOOL_ROUNDS_PER_GENERATION
                or len({call.call_id for call in event.calls}) != len(event.calls)
            ):
                self._fail_runtime_locked(ProtocolViolation("illegal V3 tool batch"))
                return
            state.pending_calls = event.calls
            state.result_batch_consumed = False
            state.total_calls += len(event.calls)
            pending.phase = "tool_wait"
            self._append_tool_evidence(
                "tool_requested",
                pending,
                round=event.round,
                call_count=len(event.calls),
                raw_correspondence="pass",
            )
            timer = threading.Timer(
                self._tool_result_wait_deadline,
                self._tool_result_wait_expired,
                args=(pending.generation_id, pending.epoch, event.round),
            )
            timer.daemon = True
            state.result_timer = timer
            timer.start()
            worker = threading.Thread(
                target=self._execute_tool_batch,
                args=(pending, state, event),
                name=f"lilavel-tool-round-{event.round}",
                daemon=True,
            )
            state.executor_thread = worker
            self._append_tool_evidence(
                "execution_started", pending, round=event.round, call_count=len(event.calls)
            )
            worker.start()

    def _execute_tool_batch(
        self,
        pending: _PendingGeneration,
        state: _ToolGenerationState,
        event: ToolCallsEvent,
    ) -> None:
        session = state.session
        assert session is not None
        try:
            results = session.execute_batch(
                ToolBatchCorrelation(state.context, event.round), event.calls, state.cancelled
            )
            self._append_tool_evidence_threadsafe(
                "execution_settled",
                pending,
                round=event.round,
                call_count=len(results),
                result_code=session.settlement,
            )
            self._submit_tool_results(pending, state, event, results)
        except ToolSessionUncontained:
            self._append_tool_evidence_threadsafe(
                "joined_settlement", pending, settlement="uncontained"
            )
            self._fail_runtime(ToolExecutorUncontained("tool executor did not settle"))
        except BaseException:
            self._append_tool_evidence_threadsafe("joined_settlement", pending, settlement="failed")
            self._fail_runtime(ToolLoopError("application tool session failed"))
        finally:
            self._release_deferred_terminal(pending, state)

    def _submit_tool_results(
        self,
        pending: _PendingGeneration,
        state: _ToolGenerationState,
        event: ToolCallsEvent,
        results: tuple[ToolResult, ...],
    ) -> None:
        with self._command_lock:
            with self._lock:
                if (
                    self._active is not pending
                    or self._tool_state is not state
                    or state.cancelled.is_set()
                    or pending.phase != "tool_wait"
                    or state.pending_calls != event.calls
                    or state.result_batch_consumed
                ):
                    self._append_tool_evidence(
                        "result_discarded", pending, round=event.round, call_count=len(results)
                    )
                    return
                expected = tuple(call.call_id for call in event.calls)
                actual = tuple(result.call_id for result in results)
                if actual != expected:
                    self._fail_runtime_locked(
                        ProtocolViolation("application result batch does not match pending calls")
                    )
                    return
                # Consume/fence the immutable batch before the continuation write.
                state.result_batch_consumed = True
                state.pending_calls = None
                state.next_round += 1
                pending.phase = "continuing"
                if state.result_timer is not None:
                    state.result_timer.cancel()
                    state.result_timer = None
                command = ToolResultsCommand(
                    pending.generation_id, pending.epoch, event.round, results
                )
                self._append_tool_evidence(
                    "result_consumed", pending, round=event.round, call_count=len(results)
                )
                state.result_batch_consumed = False
            try:
                self._write_command(command)
            except (OSError, ProtocolError):
                self._fail_runtime(SidecarCrashed("tool-result continuation write failed"))

    def _release_deferred_terminal(
        self, pending: _PendingGeneration, state: _ToolGenerationState
    ) -> None:
        event: object | None = None
        with self._lock:
            if self._active is pending and self._tool_state is state:
                state.executor_thread = None
                if state.session is not None and state.session.settlement == "uncontained":
                    state.deferred_terminal = None
                    return
                event = state.deferred_terminal
                state.deferred_terminal = None
                if event is not None:
                    state.pending_calls = None
                    if state.result_timer is not None:
                        state.result_timer.cancel()
                        state.result_timer = None
                    pending.phase = "cancelling"
        if event is not None:
            super()._dispatch(cast(object, event))  # type: ignore[arg-type]

    def _cancel_impl(self, generation_id: str) -> bool:
        with self._lock:
            pending = self._active
            state = self._tool_state
            if pending is not None and pending.generation_id == generation_id and state is not None:
                state.cancelled.set()
                if state.session is not None:
                    state.session.cancel()
        return super()._cancel_impl(generation_id)

    def _shutdown_impl(self) -> None:
        with self._lock:
            state = self._tool_state
            if state is not None:
                state.cancelled.set()
                if state.session is not None:
                    state.session.cancel()
        super()._shutdown_impl()

    def _await_shutdown_settlement(self, deadline: float) -> ModelRuntimeError | None:
        with self._lock:
            pending = self._active
            state = self._tool_state
            session = None if state is None else state.session
        if pending is None or state is None or session is None:
            return None

        remaining = min(
            self._tool_settlement_deadline,
            max(0.0, deadline - time.monotonic()),
        )
        if not session.wait_settled(remaining):
            return ToolExecutorUncontained("tool executor did not settle during shutdown")

        event: object | None = None
        with self._lock:
            if self._active is pending and self._tool_state is state:
                event = state.deferred_terminal
                state.deferred_terminal = None
                if event is not None:
                    state.pending_calls = None
                    if state.result_timer is not None:
                        state.result_timer.cancel()
                        state.result_timer = None
                    pending.phase = "cancelling"
        if event is not None:
            super()._dispatch(cast(object, event))  # type: ignore[arg-type]
        return None

    def _finish_pending_locked(self, pending: _PendingGeneration) -> None:
        state = self._tool_state if self._active is pending else None
        if state is not None:
            if state.result_timer is not None:
                state.result_timer.cancel()
            if state.overall_timer is not None:
                state.overall_timer.cancel()
            settlement: JoinedSettlement = "not_applicable"
            if state.session is not None:
                raw = state.session.settlement
                settlement = (
                    "uncontained"
                    if raw == "uncontained"
                    else "failed"
                    if raw == "failed"
                    else "cancelled"
                    if raw == "cancelled"
                    else "settled"
                )
            self._append_tool_evidence("joined_settlement", pending, settlement=settlement)
            self._tool_state = None
        super()._finish_pending_locked(pending)

    def _fail_runtime_locked(self, failure: ModelRuntimeError) -> subprocess.Popen[bytes] | None:
        """Fence admission now and clear active state only after both legs settle."""

        pending = self._active
        state = self._tool_state
        session = None if state is None else state.session
        if pending is None or state is None or session is None:
            return super()._fail_runtime_locked(failure)

        # Fence an admitted executor thread before observing settlement.  The
        # worker may have been started by the host but not yet entered the
        # session, whose initial settlement is still ``idle``.
        state.cancelled.set()
        session.cancel()
        if session.wait_settled(0):
            return super()._fail_runtime_locked(failure)
        if state.deferred_failure is None:
            state.deferred_failure = failure
        if not state.failure_coordinator_started:
            state.failure_coordinator_started = True
            threading.Thread(
                target=self._settle_failed_join,
                args=(pending, state),
                name="lilavel-v3-failure-join",
                daemon=True,
            ).start()
            process = self._process
            containment = self._containment
            if process is not None and (process.poll() is None or containment is not None):
                threading.Thread(
                    target=_terminate_owned_process,
                    args=(process, containment),
                    name="lilavel-v3-failed-containment",
                    daemon=True,
                ).start()
        return None

    def _fail_pending_locked(self, failure: ModelRuntimeError) -> None:
        state = self._tool_state
        session = None if state is None else state.session
        if state is not None and session is not None:
            state.cancelled.set()
            session.cancel()
            if not session.wait_settled(0):
                self._fail_runtime_locked(failure)
                return
        super()._fail_pending_locked(failure)

    def _settle_failed_join(self, pending: _PendingGeneration, state: _ToolGenerationState) -> None:
        session = state.session
        assert session is not None
        settled = session.wait_settled(self._tool_settlement_deadline)
        with self._lock:
            if self._active is not pending or self._tool_state is not state:
                return
            failure = state.deferred_failure or ToolLoopError("V3 joined settlement failed")
            if not settled or session.settlement == "uncontained":
                self._poison_uncontained_locked(pending, state)
                return
            # Bypass the override only after provider failure fencing and
            # application settlement are both confirmed.
            ModelRuntime._fail_runtime_locked(self, failure)

    def _poison_uncontained_locked(
        self, pending: _PendingGeneration, state: _ToolGenerationState
    ) -> None:
        failure = ToolExecutorUncontained("tool executor did not settle")
        self._state = "failed"
        self._failure = failure
        self._ready.set()
        self._shutdown_complete.set()
        state.cancelled.set()
        if state.result_timer is not None:
            state.result_timer.cancel()
            state.result_timer = None
        if state.overall_timer is not None:
            state.overall_timer.cancel()
            state.overall_timer = None
        already_terminal = pending.phase == "terminal"
        pending.phase = "terminal"
        pending.handle.fail(
            GenerationFailedEvent(pending.generation_id, pending.epoch, "cleanup_timeout"),
            failure,
        )
        if not already_terminal:
            self._physical_evidence.append_generation_terminal(
                generation_id=pending.generation_id,
                epoch=pending.epoch,
                result="failed",
                failure_code="tool_executor_uncontained",
            )
        self._append_tool_evidence("joined_settlement", pending, settlement="uncontained")

    def _tool_result_wait_expired(self, generation_id: str, epoch: int, round: int) -> None:
        with self._lock:
            pending = self._active
            state = self._tool_state
            if (
                pending is None
                or state is None
                or pending.generation_id != generation_id
                or pending.epoch != epoch
                or pending.phase != "tool_wait"
                or state.next_round != round
            ):
                return
            state.cancelled.set()
            if state.session is not None:
                state.session.cancel()
        self._fail_runtime(ToolResultWaitTimeout("tool result wait deadline exceeded"))

    def _tool_generation_expired(self, generation_id: str, epoch: int) -> None:
        with self._lock:
            pending = self._active
            state = self._tool_state
            if (
                pending is None
                or state is None
                or pending.generation_id != generation_id
                or pending.epoch != epoch
            ):
                return
            state.cancelled.set()
            if state.session is not None:
                state.session.cancel()
        self._fail_runtime(ToolGenerationTimeout("tool generation deadline exceeded"))

    def _append_tool_evidence_threadsafe(
        self,
        kind: ToolEvidenceKind,
        pending: _PendingGeneration,
        *,
        round: int | None = None,
        call_count: int = 0,
        result_code: str | None = None,
        settlement: JoinedSettlement | None = None,
        raw_correspondence: Literal["pass"] | None = None,
    ) -> None:
        with self._lock:
            self._append_tool_evidence(
                kind,
                pending,
                round=round,
                call_count=call_count,
                result_code=result_code,
                settlement=settlement,
                raw_correspondence=raw_correspondence,
            )

    def _append_tool_evidence(
        self,
        kind: ToolEvidenceKind,
        pending: _PendingGeneration,
        *,
        round: int | None = None,
        call_count: int = 0,
        result_code: str | None = None,
        settlement: JoinedSettlement | None = None,
        raw_correspondence: Literal["pass"] | None = None,
    ) -> None:
        self._tool_evidence.append(
            ToolLifecycleEvidenceRecord(
                sequence=self._tool_evidence_sequence,
                kind=kind,
                generation_id=pending.generation_id,
                epoch=pending.epoch,
                round=round,
                call_count=call_count,
                result_code=result_code,
                settlement=settlement,
                raw_correspondence=raw_correspondence,
            )
        )
        self._tool_evidence_sequence += 1


def _matches(pending: _PendingGeneration, event: object) -> bool:
    return (
        getattr(event, "generation_id", None) == pending.generation_id
        and getattr(event, "epoch", None) == pending.epoch
    )


def _default_v3_command() -> tuple[str, ...]:
    executable = shutil.which("npx") or shutil.which("npx.cmd")
    if executable is None:
        executable = "npx.cmd" if os.name == "nt" else "npx"
    return (executable, "--yes", "bun@1.4.0", "run", "protocol:v3")
