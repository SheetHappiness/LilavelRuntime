"""Application-owned deterministic fake tool session for the P4-B lifecycle seam."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from time import monotonic
from typing import Final

from lilavel_contracts import (
    ToolCall,
    ToolEffect,
    ToolResult,
    ToolResultStatus,
    ToolSpec,
)
from lilavel_core.tool_runtime import (
    DEFAULT_TOOL_EXECUTOR_DEADLINE,
    ApplicationToolSession,
    ToolBatchCorrelation,
    ToolGenerationContext,
    ToolSessionSettlement,
    ToolSessionUncontained,
)

TOOL_SESSION_EVIDENCE_CAPACITY: Final = 256

type FakeToolExecutor = Callable[[ToolBatchCorrelation, ToolCall, threading.Event], ToolResult]
type FakeToolDecision = Callable[[ToolBatchCorrelation, ToolCall], bool]


class _ToolSessionCancelled(RuntimeError):
    """Stop sequential dispatch without manufacturing a partial result batch."""


@dataclass(frozen=True, slots=True)
class ToolSessionEvidence:
    sequence: int
    generation_id: str
    epoch: int
    round: int
    call_id: str
    kind: str
    status_code: str | None = None
    effect: str | None = None


class DeterministicToolSession(ApplicationToolSession):
    """Generation-scoped fake executor with sequential dispatch and containment."""

    def __init__(
        self,
        context: ToolGenerationContext,
        specs: Sequence[ToolSpec],
        executors: Mapping[str, FakeToolExecutor],
        *,
        authorize: FakeToolDecision | None = None,
        validate: FakeToolDecision | None = None,
        executor_deadline: float = DEFAULT_TOOL_EXECUTOR_DEADLINE,
        containment_deadline: float = 1.0,
        evidence_capacity: int = TOOL_SESSION_EVIDENCE_CAPACITY,
    ) -> None:
        if not math.isfinite(executor_deadline) or executor_deadline <= 0:
            raise ValueError("executor_deadline must be positive")
        if not math.isfinite(containment_deadline) or containment_deadline <= 0:
            raise ValueError("containment_deadline must be positive")
        if evidence_capacity <= 0:
            raise ValueError("evidence_capacity must be positive")
        snapshot = tuple(specs)
        if len({spec.name for spec in snapshot}) != len(snapshot):
            raise ValueError("tool spec names must be unique")
        self._context = context
        self._specs = snapshot
        self._spec_names = frozenset(spec.name for spec in snapshot)
        self._executors = dict(executors)
        self._authorize = authorize
        self._validate = validate
        self._executor_deadline = executor_deadline
        self._containment_deadline = containment_deadline
        self._lock = threading.RLock()
        self._cancelled = threading.Event()
        self._settled = threading.Event()
        self._settled.set()
        self._settlement: ToolSessionSettlement = "idle"
        self._active_worker: threading.Thread | None = None
        self._active_call_cancelled: threading.Event | None = None
        self._active_round: int | None = None
        self._last_round = 0
        self._evidence: list[ToolSessionEvidence] = []
        self._evidence_capacity = evidence_capacity
        self._next_sequence = 1

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return self._specs

    @property
    def settlement(self) -> ToolSessionSettlement:
        with self._lock:
            return self._settlement

    def evidence(self) -> tuple[ToolSessionEvidence, ...]:
        with self._lock:
            return tuple(replace(record) for record in self._evidence)

    def execute_batch(
        self,
        correlation: ToolBatchCorrelation,
        calls: Sequence[ToolCall],
        cancelled: threading.Event,
    ) -> tuple[ToolResult, ...]:
        call_snapshot = tuple(calls)
        with self._lock:
            if correlation.context != self._context:
                raise ValueError("stale tool-session correlation")
            if correlation.round != self._last_round + 1 or not call_snapshot:
                raise ValueError("illegal tool-session round")
            if self._active_round is not None or self._cancelled.is_set():
                raise ValueError("tool session is not available")
            self._active_round = correlation.round
            self._last_round = correlation.round
            self._settlement = "running"
            self._settled.clear()

        results: list[ToolResult] = []
        try:
            for call in call_snapshot:
                # Provider order is observable and intentionally sequential.
                if cancelled.is_set() or self._cancelled.is_set():
                    with self._lock:
                        self._settlement = "cancelled"
                    break
                result = self._execute_one(correlation, call, cancelled)
                results.append(result)
            with self._lock:
                if self._settlement == "running":
                    self._settlement = (
                        "cancelled" if cancelled.is_set() or self._cancelled.is_set() else "settled"
                    )
            return tuple(results)
        except _ToolSessionCancelled:
            with self._lock:
                self._settlement = "cancelled"
            return tuple(results)
        except ToolSessionUncontained:
            with self._lock:
                self._settlement = "uncontained"
            raise
        except BaseException:
            with self._lock:
                self._settlement = "failed"
            raise
        finally:
            with self._lock:
                self._active_round = None
                if self._settlement != "uncontained":
                    self._active_worker = None
                    self._settled.set()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled.set()
            if self._active_call_cancelled is not None:
                self._active_call_cancelled.set()
            if self._settlement == "idle":
                self._settlement = "cancelled"

    def wait_settled(self, timeout: float | None = None) -> bool:
        return self._settled.wait(timeout)

    def _execute_one(
        self,
        correlation: ToolBatchCorrelation,
        call: ToolCall,
        cancelled: threading.Event,
    ) -> ToolResult:
        self._record(correlation, call, "requested")
        if call.argument_error is not None:
            return self._settled_result(
                correlation,
                call,
                ToolResult(
                    call.call_id,
                    ToolResultStatus.INVALID,
                    None,
                    reason_code=call.argument_error,
                ),
            )
        if call.tool_name not in self._spec_names or call.tool_name not in self._executors:
            return self._settled_result(
                correlation,
                call,
                ToolResult(
                    call.call_id,
                    ToolResultStatus.UNAVAILABLE,
                    None,
                    reason_code="tool_unavailable",
                ),
            )
        if self._validate is not None and not self._validate(correlation, call):
            return self._settled_result(
                correlation,
                call,
                ToolResult(
                    call.call_id,
                    ToolResultStatus.INVALID,
                    None,
                    reason_code="validation_failed",
                ),
            )
        if self._authorize is not None and not self._authorize(correlation, call):
            return self._settled_result(
                correlation,
                call,
                ToolResult(
                    call.call_id,
                    ToolResultStatus.DENIED,
                    None,
                    reason_code="authorization_denied",
                ),
            )

        executor = self._executors[call.tool_name]
        completed = threading.Event()
        call_cancelled = threading.Event()
        outcome: list[ToolResult] = []
        errors: list[BaseException] = []

        def invoke() -> None:
            try:
                outcome.append(executor(correlation, call, call_cancelled))
            except BaseException as error:
                errors.append(error)
            finally:
                completed.set()

        worker = threading.Thread(
            target=invoke,
            name=f"lilavel-fake-tool-{call.call_id}",
            daemon=True,
        )
        with self._lock:
            # Starting the executor and session cancellation share this lock. If
            # cancellation wins, no external work can begin after the fence.
            if cancelled.is_set() or self._cancelled.is_set():
                raise _ToolSessionCancelled
            self._active_worker = worker
            self._active_call_cancelled = call_cancelled
            self._record(correlation, call, "execution_started")
            worker.start()

        timed_out = False
        try:
            started = monotonic()
            while not completed.wait(0.01):
                if cancelled.is_set() or self._cancelled.is_set():
                    call_cancelled.set()
                    break
                if monotonic() - started >= self._executor_deadline:
                    timed_out = True
                    # The deadline belongs to this call, not the generation.
                    call_cancelled.set()
                    break

            if not completed.is_set() and not completed.wait(self._containment_deadline):
                self._record(correlation, call, "cleanup_uncontained")
                raise ToolSessionUncontained("fake executor ignored bounded cancellation")
            if timed_out:
                return self._settled_result(
                    correlation,
                    call,
                    ToolResult(
                        call.call_id,
                        ToolResultStatus.TIMED_OUT,
                        None,
                        reason_code="executor_timeout",
                        effect=ToolEffect.NONE,
                    ),
                )
            if errors:
                return self._settled_result(
                    correlation,
                    call,
                    ToolResult(
                        call.call_id,
                        ToolResultStatus.FAILED,
                        None,
                        reason_code="executor_failed",
                        effect=ToolEffect.NONE,
                    ),
                )
            if len(outcome) != 1 or outcome[0].call_id != call.call_id:
                return self._settled_result(
                    correlation,
                    call,
                    ToolResult(
                        call.call_id,
                        ToolResultStatus.FAILED,
                        None,
                        reason_code="invalid_executor_result",
                        effect=ToolEffect.NONE,
                    ),
                )
            return self._settled_result(correlation, call, outcome[0])
        finally:
            with self._lock:
                if self._active_worker is worker:
                    self._active_worker = None
                    self._active_call_cancelled = None

    def _settled_result(
        self, correlation: ToolBatchCorrelation, call: ToolCall, result: ToolResult
    ) -> ToolResult:
        self._record(
            correlation,
            call,
            "execution_settled",
            status_code=result.status.value,
            effect=result.effect.value,
        )
        return result

    def _record(
        self,
        correlation: ToolBatchCorrelation,
        call: ToolCall,
        kind: str,
        *,
        status_code: str | None = None,
        effect: str | None = None,
    ) -> None:
        with self._lock:
            self._evidence.append(
                ToolSessionEvidence(
                    sequence=self._next_sequence,
                    generation_id=correlation.context.generation_id,
                    epoch=correlation.context.epoch,
                    round=correlation.round,
                    call_id=call.call_id,
                    kind=kind,
                    status_code=status_code,
                    effect=effect,
                )
            )
            if len(self._evidence) > self._evidence_capacity:
                del self._evidence[0]
            self._next_sequence += 1


class DeterministicToolSessionFactory:
    """Application composition object for deterministic P4-B fixtures only."""

    def __init__(
        self,
        specs: Sequence[ToolSpec],
        executors: Mapping[str, FakeToolExecutor],
        *,
        authorize: FakeToolDecision | None = None,
        validate: FakeToolDecision | None = None,
        executor_deadline: float = DEFAULT_TOOL_EXECUTOR_DEADLINE,
        containment_deadline: float = 1.0,
    ) -> None:
        self._specs = tuple(specs)
        self._executors = dict(executors)
        self._authorize = authorize
        self._validate = validate
        self._executor_deadline = executor_deadline
        self._containment_deadline = containment_deadline
        self._lock = threading.Lock()
        self._sessions: list[DeterministicToolSession] = []

    @property
    def sessions(self) -> tuple[DeterministicToolSession, ...]:
        with self._lock:
            return tuple(self._sessions)

    def create(self, context: ToolGenerationContext) -> DeterministicToolSession:
        session = DeterministicToolSession(
            context,
            self._specs,
            self._executors,
            authorize=self._authorize,
            validate=self._validate,
            executor_deadline=self._executor_deadline,
            containment_deadline=self._containment_deadline,
        )
        with self._lock:
            self._sessions.append(session)
        return session
