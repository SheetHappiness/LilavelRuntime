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

from .tool_registry import (
    ApplicationToolRegistry,
    ToolAuthorization,
    ToolBinding,
    ToolExposureSnapshot,
    validate_tool_arguments,
)

TOOL_SESSION_EVIDENCE_CAPACITY: Final = 256
_SAFE_REASON_CODES = frozenset(
    {
        "invalid_json",
        "invalid_shape",
        "tool_unavailable",
        "missing_required",
        "invalid_type",
        "enum_violation",
        "unexpected_property",
        "string_too_short",
        "string_too_long",
        "number_below_minimum",
        "number_above_maximum",
        "validation_failed",
        "authorization_denied",
        "executor_timeout",
        "executor_failed",
        "invalid_executor_result",
        "discord_preflight_failed",
        "discord_rejected",
        "discord_delivery_unknown",
        "cancelled_before_send",
    }
)

type FakeToolExecutor = Callable[[ToolBatchCorrelation, ToolCall, threading.Event], ToolResult]
type FakeToolDecision = Callable[[ToolBatchCorrelation, ToolCall], bool]


def _unavailable_executor(
    correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
) -> ToolResult:
    del correlation, cancelled
    return ToolResult(
        call.call_id,
        ToolResultStatus.UNAVAILABLE,
        None,
        reason_code="tool_unavailable",
    )


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
    reason_code: str | None = None
    effect: str | None = None


class DeterministicToolSession(ApplicationToolSession):
    """Generation-scoped fake executor with sequential dispatch and containment."""

    def __init__(
        self,
        context: ToolGenerationContext,
        specs: Sequence[ToolSpec] | ToolExposureSnapshot,
        executors: Mapping[str, FakeToolExecutor] | None = None,
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
        if isinstance(specs, ToolExposureSnapshot):
            if executors is not None:
                raise ValueError("executors cannot accompany an exposure snapshot")
            exposure = specs
        else:
            if executors is None:
                raise ValueError("executors are required for legacy session construction")
            snapshot = tuple(specs)
            if len({spec.name for spec in snapshot}) != len(snapshot):
                raise ValueError("tool spec names must be unique")
            bindings = tuple(
                ToolBinding(spec, executors[spec.name])
                for spec in snapshot
                if spec.name in executors
            )
            missing = tuple(spec for spec in snapshot if spec.name not in executors)
            registry = ApplicationToolRegistry(bindings)
            for spec in missing:
                registry.register(
                    ToolBinding(
                        spec,
                        _unavailable_executor,
                        is_available=lambda correlation: False,
                    )
                )
            exposure = registry.snapshot(tuple(spec.name for spec in snapshot))
        self._context = context
        self._exposure = exposure
        self._specs = exposure.specs
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
        binding = self._exposure.binding_for(call.tool_name)
        if binding is None:
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
        reason = validate_tool_arguments(binding.spec, call.arguments)
        if reason is not None:
            return self._settled_result(
                correlation,
                call,
                ToolResult(
                    call.call_id,
                    ToolResultStatus.INVALID,
                    None,
                    reason_code=reason,
                ),
            )
        if self._validate is not None:
            try:
                valid = self._validate(correlation, call)
            except BaseException:
                valid = False
            if not valid:
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

        decision = self._authorize_call(binding, correlation, call)
        if decision is not ToolAuthorization.ALLOWED:
            return self._settled_result(
                correlation,
                call,
                ToolResult(
                    call.call_id,
                    (
                        ToolResultStatus.DENIED
                        if decision is ToolAuthorization.DENIED
                        else ToolResultStatus.UNAVAILABLE
                    ),
                    None,
                    reason_code=(
                        "authorization_denied"
                        if decision is ToolAuthorization.DENIED
                        else "tool_unavailable"
                    ),
                ),
            )

        executor = binding.executor
        completed = threading.Event()
        call_cancelled = threading.Event()
        outcome: list[object] = []
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
            if not self._is_available(binding, correlation):
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
                        effect=ToolEffect.UNKNOWN,
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
            result = outcome[0] if len(outcome) == 1 else None
            if not isinstance(result, ToolResult) or result.call_id != call.call_id:
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
            return self._settled_result(
                correlation,
                call,
                self._normalize_executor_result(call, result),
            )
        finally:
            with self._lock:
                if self._active_worker is worker:
                    self._active_worker = None
                    self._active_call_cancelled = None

    def _authorize_call(
        self,
        binding: ToolBinding,
        correlation: ToolBatchCorrelation,
        call: ToolCall,
    ) -> ToolAuthorization:
        """Evaluate trusted policy and liveness without trusting model fields."""

        if binding.is_available is not None:
            try:
                if not binding.is_available(correlation):
                    return ToolAuthorization.UNAVAILABLE
            except BaseException:
                return ToolAuthorization.UNAVAILABLE
        if binding.authorize is not None:
            try:
                decision = binding.authorize(correlation, call)
            except BaseException:
                return ToolAuthorization.UNAVAILABLE
            if decision is not ToolAuthorization.ALLOWED:
                if decision not in {
                    ToolAuthorization.DENIED,
                    ToolAuthorization.UNAVAILABLE,
                }:
                    return ToolAuthorization.UNAVAILABLE
                return decision
        if self._authorize is not None:
            try:
                if not self._authorize(correlation, call):
                    return ToolAuthorization.DENIED
            except BaseException:
                return ToolAuthorization.UNAVAILABLE
        # Recheck liveness immediately before the executor is admitted.
        return ToolAuthorization.ALLOWED

    @staticmethod
    def _is_available(binding: ToolBinding, correlation: ToolBatchCorrelation) -> bool:
        if binding.is_available is None:
            return True
        try:
            return binding.is_available(correlation)
        except BaseException:
            return False

    @staticmethod
    def _normalize_executor_result(call: ToolCall, result: ToolResult) -> ToolResult:
        """Keep executor-provided results typed and strip unsafe reason text."""

        reason = result.reason_code if result.reason_code in _SAFE_REASON_CODES else None
        if result.status is ToolResultStatus.OK:
            return ToolResult(
                call.call_id,
                ToolResultStatus.OK,
                result.output,
                effect=result.effect,
            )
        if result.status is ToolResultStatus.FAILED:
            return ToolResult(
                call.call_id,
                ToolResultStatus.FAILED,
                None,
                reason_code=reason or "executor_failed",
                effect=result.effect,
            )
        if result.status is ToolResultStatus.TIMED_OUT:
            return ToolResult(
                call.call_id,
                ToolResultStatus.TIMED_OUT,
                None,
                reason_code=reason or "executor_timeout",
                effect=result.effect,
            )
        return ToolResult(
            call.call_id,
            ToolResultStatus.FAILED,
            None,
            reason_code="invalid_executor_result",
            effect=ToolEffect.NONE,
        )

    def _settled_result(
        self, correlation: ToolBatchCorrelation, call: ToolCall, result: ToolResult
    ) -> ToolResult:
        self._record(
            correlation,
            call,
            "execution_settled",
            status_code=result.status.value,
            reason_code=result.reason_code,
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
        reason_code: str | None = None,
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
                    reason_code=reason_code,
                    effect=effect,
                )
            )
            if len(self._evidence) > self._evidence_capacity:
                del self._evidence[0]
            self._next_sequence += 1


class DeterministicToolSessionFactory:
    """Explicit application composition for deterministic V3 tool fixtures."""

    def __init__(
        self,
        specs: Sequence[ToolSpec] | ApplicationToolRegistry,
        executors: Mapping[str, FakeToolExecutor] | None = None,
        *,
        exposed_tool_names: Sequence[str] | None = None,
        authorize: FakeToolDecision | None = None,
        validate: FakeToolDecision | None = None,
        executor_deadline: float = DEFAULT_TOOL_EXECUTOR_DEADLINE,
        containment_deadline: float = 1.0,
    ) -> None:
        if isinstance(specs, ApplicationToolRegistry):
            if executors is not None:
                raise ValueError("executors cannot accompany an application registry")
            self._registry = specs
            self._exposed_tool_names = tuple(exposed_tool_names or ())
        else:
            if executors is None:
                raise ValueError("executors are required for legacy fixture construction")
            snapshot = tuple(specs)
            if len({spec.name for spec in snapshot}) != len(snapshot):
                raise ValueError("tool spec names must be unique")
            bindings = tuple(
                ToolBinding(spec, executors.get(spec.name, _unavailable_executor))
                for spec in snapshot
            )
            missing = frozenset(spec.name for spec in snapshot) - executors.keys()
            self._registry = ApplicationToolRegistry(
                tuple(
                    ToolBinding(
                        binding.spec,
                        binding.executor,
                        is_available=(lambda correlation: False)
                        if binding.spec.name in missing
                        else None,
                    )
                    for binding in bindings
                )
            )
            self._exposed_tool_names = (
                tuple(spec.name for spec in snapshot)
                if exposed_tool_names is None
                else tuple(exposed_tool_names)
            )
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

    @property
    def registry(self) -> ApplicationToolRegistry:
        return self._registry

    def create(self, context: ToolGenerationContext) -> DeterministicToolSession:
        session = DeterministicToolSession(
            context,
            self._registry.snapshot(self._exposed_tool_names),
            authorize=self._authorize,
            validate=self._validate,
            executor_deadline=self._executor_deadline,
            containment_deadline=self._containment_deadline,
        )
        with self._lock:
            self._sessions.append(session)
        return session
