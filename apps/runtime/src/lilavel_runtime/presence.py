"""Bounded persistent CLI presence built on Core and the V3 tool lifecycle."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol
from uuid import uuid4

from lilavel_contracts import ToolCall, ToolEffect, ToolResult, ToolResultStatus, ToolSpec
from lilavel_core import (
    ContextMessage,
    ConversationCancelled,
    ConversationCompleted,
    ConversationCore,
    ConversationFailed,
    ConversationRun,
    ConversationTextDelta,
    GenerationCancelled,
    GenerationCompleted,
    GenerationFailedEvent,
    ModelRequest,
    RunAwareConversationRuntime,
    RuntimeGeneration,
    TextDelta,
    ToolBatchCorrelation,
    ToolGenerationContext,
)

from .tool_registry import ApplicationToolRegistry, ToolAuthorization, ToolBinding
from .tool_session import DeterministicToolSession, DeterministicToolSessionFactory

PRESENCE_SAY: Final = "presence.say"
PRESENCE_STAY_SILENT: Final = "presence.stay_silent"
MAX_PRESENCE_TEXT_BYTES: Final = 4_096
DEFAULT_IDLE_TIMEOUT_S: Final = 300.0
DEFAULT_RECENT_CONTEXT_MESSAGES: Final = 12
PRESENCE_EVIDENCE_CAPACITY: Final = 256
PRESENCE_ACTION_STATE_CAPACITY: Final = 64

SAY_SPEC = ToolSpec(
    PRESENCE_SAY,
    "Say one brief message in the local CLI, then yield.",
    {
        "type": "object",
        "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 2000}},
        "required": ["text"],
        "additionalProperties": False,
    },
)
STAY_SILENT_SPEC = ToolSpec(
    PRESENCE_STAY_SILENT,
    "Deliberately say nothing and yield.",
    {"type": "object", "properties": {}, "additionalProperties": False},
)


class PresenceAction(StrEnum):
    SAY = "say"
    STAY_SILENT = "stay_silent"


class AutonomousStatus(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    INVALID = "invalid"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IdleOpportunity:
    opportunity_id: str
    sequence: int
    idle_for_s: float


@dataclass(frozen=True, slots=True)
class PresenceWakeDecision:
    wake: bool
    reason: str


class PresenceWakePolicy(Protocol):
    async def decide(self, opportunity: IdleOpportunity) -> PresenceWakeDecision: ...


class FixedPresenceWakePolicy:
    """Deterministic production/test hook; the safe default is NO_WAKE."""

    def __init__(self, *, wake: bool = False) -> None:
        self._wake = wake

    async def decide(self, opportunity: IdleOpportunity) -> PresenceWakeDecision:
        del opportunity
        return PresenceWakeDecision(self._wake, "configured_wake" if self._wake else "no_wake")


class WakeAfterIdleOpportunitiesPolicy:
    """Deterministically reject earlier opportunities and wake at the threshold."""

    def __init__(self, wake_after: int) -> None:
        if wake_after <= 0:
            raise ValueError("wake_after must be positive")
        self._wake_after = wake_after

    async def decide(self, opportunity: IdleOpportunity) -> PresenceWakeDecision:
        wake = opportunity.sequence >= self._wake_after
        return PresenceWakeDecision(wake, "threshold_reached" if wake else "before_threshold")


@dataclass(frozen=True, slots=True)
class PresenceOutput:
    kind: str
    text: str


class PresenceOutputSink(Protocol):
    """Thread-safe local output boundary used by conversation and tool workers."""

    def publish(self, output: PresenceOutput) -> None: ...


@dataclass(frozen=True, slots=True)
class AutonomousOutcome:
    run_id: str
    status: AutonomousStatus
    action: PresenceAction | None
    generation_id: str | None


@dataclass(frozen=True, slots=True)
class PresenceEvidence:
    sequence: int
    kind: str
    opportunity_id: str | None = None
    run_id: str | None = None
    result: str | None = None


@dataclass(slots=True)
class _ActionState:
    action: PresenceAction | None = None


class PresenceToolSessionFactory:
    """Expose only terminal presence tools, and only to autonomous run identities."""

    def __init__(self, sink: PresenceOutputSink) -> None:
        self._sink = sink
        self._lock = threading.Lock()
        self._permitted_runs: set[str] = set()
        self._states: dict[str, _ActionState] = {}
        registry = ApplicationToolRegistry(
            (
                ToolBinding(SAY_SPEC, self._execute_say, authorize=self._authorize_action),
                ToolBinding(
                    STAY_SILENT_SPEC,
                    self._execute_silence,
                    authorize=self._authorize_action,
                ),
            )
        )
        self._presence = DeterministicToolSessionFactory(
            registry, exposed_tool_names=(PRESENCE_SAY, PRESENCE_STAY_SILENT)
        )
        self._empty = DeterministicToolSessionFactory((), {})

    def permit_run(self, logical_run_id: str) -> None:
        """Grant one application-owned autonomous tool-exposure capability."""

        if not logical_run_id.startswith("autonomous:"):
            raise ValueError("presence run identity is invalid")
        with self._lock:
            self._permitted_runs.add(logical_run_id)

    def revoke_run(self, logical_run_id: str) -> None:
        with self._lock:
            self._permitted_runs.discard(logical_run_id)

    def create(self, context: ToolGenerationContext) -> DeterministicToolSession:
        with self._lock:
            if context.logical_run_id not in self._permitted_runs:
                return self._empty.create(context)
            self._permitted_runs.remove(context.logical_run_id)
            if len(self._states) >= PRESENCE_ACTION_STATE_CAPACITY:
                del self._states[next(iter(self._states))]
            self._states[context.generation_id] = _ActionState()
        return self._presence.create(context)

    def action_for(self, generation_id: str) -> PresenceAction | None:
        with self._lock:
            state = self._states.get(generation_id)
            return None if state is None else state.action

    def _authorize_action(
        self, correlation: ToolBatchCorrelation, call: ToolCall
    ) -> ToolAuthorization:
        del call
        with self._lock:
            available = correlation.context.generation_id in self._states
        return ToolAuthorization.ALLOWED if available else ToolAuthorization.UNAVAILABLE

    def _reserve(self, generation_id: str, action: PresenceAction) -> bool:
        with self._lock:
            state = self._states.get(generation_id)
            if state is None or state.action is not None:
                return False
            state.action = action
            return True

    def _execute_say(
        self,
        correlation: ToolBatchCorrelation,
        call: ToolCall,
        cancelled: threading.Event,
    ) -> ToolResult:
        if cancelled.is_set():
            return ToolResult(
                call.call_id,
                ToolResultStatus.UNAVAILABLE,
                None,
                reason_code="tool_unavailable",
                effect=ToolEffect.NONE,
            )
        arguments = call.arguments
        text = None if arguments is None else arguments.get("text")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text.encode("utf-8")) > MAX_PRESENCE_TEXT_BYTES
        ):
            return ToolResult(
                call.call_id,
                ToolResultStatus.INVALID,
                None,
                reason_code="invalid_arguments",
                effect=ToolEffect.NONE,
            )
        if not self._reserve(correlation.context.generation_id, PresenceAction.SAY):
            return ToolResult(
                call.call_id,
                ToolResultStatus.UNAVAILABLE,
                None,
                reason_code="tool_unavailable",
                effect=ToolEffect.NONE,
            )
        self._sink.publish(PresenceOutput("autonomous", text.strip()))
        return ToolResult(
            call.call_id,
            ToolResultStatus.OK,
            {"delivered": True},
            effect=ToolEffect.CONFIRMED,
        )

    def _execute_silence(
        self,
        correlation: ToolBatchCorrelation,
        call: ToolCall,
        cancelled: threading.Event,
    ) -> ToolResult:
        if cancelled.is_set() or not self._reserve(
            correlation.context.generation_id, PresenceAction.STAY_SILENT
        ):
            return ToolResult(
                call.call_id,
                ToolResultStatus.UNAVAILABLE,
                None,
                reason_code="tool_unavailable",
                effect=ToolEffect.NONE,
            )
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.NONE)


class AutonomousCognitionRunner:
    """Thin transient-run coordinator; ModelRuntime remains the lifecycle owner."""

    def __init__(
        self,
        runtime: RunAwareConversationRuntime,
        tools: PresenceToolSessionFactory,
        *,
        recent_context_messages: int = DEFAULT_RECENT_CONTEXT_MESSAGES,
    ) -> None:
        if recent_context_messages <= 0:
            raise ValueError("recent_context_messages must be positive")
        self._runtime = runtime
        self._tools = tools
        self._recent_context_messages = recent_context_messages
        self._lock = threading.Lock()
        self._active: RuntimeGeneration | None = None
        self._admitted = False
        self._cancel_requested = False

    def admit(self) -> None:
        """Reserve the one autonomous lane before its worker thread starts."""

        with self._lock:
            if self._admitted:
                raise RuntimeError("autonomous cognition is already admitted")
            self._admitted = True
            self._cancel_requested = False

    def run(self, history: Sequence[ContextMessage]) -> AutonomousOutcome:
        run_id = f"autonomous:{uuid4()}"
        recent = tuple(history[-self._recent_context_messages :])
        request = ModelRequest(
            messages=recent if recent else None,
            prompt=None if recent else "A bounded idle opportunity is available.",
            system_prompt=(
                "This is a transient, noncanonical idle cognition opportunity.",
                "Choose exactly one terminal tool: presence.say(text) or "
                "presence.stay_silent(). Do not answer with ordinary assistant text.",
            ),
        )
        self._tools.permit_run(run_id)
        try:
            handle = self._runtime.generate_for_run(
                request, scope_id="local-presence", logical_run_id=run_id
            )
        except Exception:
            self._tools.revoke_run(run_id)
            with self._lock:
                self._admitted = False
            return AutonomousOutcome(run_id, AutonomousStatus.FAILED, None, None)
        with self._lock:
            self._active = handle
            cancel_requested = self._cancel_requested
        if cancel_requested:
            self._runtime.cancel(handle.generation_id)
        status = AutonomousStatus.FAILED
        try:
            for event in handle.events():
                # Provider text before or after the terminal action is intentionally
                # consumed and discarded. The tool executor is the only semantic sink.
                if isinstance(event, TextDelta):
                    continue
                if isinstance(event, GenerationCompleted):
                    action = self._tools.action_for(handle.generation_id)
                    status = (
                        AutonomousStatus.COMPLETED
                        if action is not None
                        else AutonomousStatus.INVALID
                    )
                elif isinstance(event, GenerationCancelled):
                    status = AutonomousStatus.CANCELLED
                elif isinstance(event, GenerationFailedEvent):
                    status = AutonomousStatus.FAILED
            action = self._tools.action_for(handle.generation_id)
            return AutonomousOutcome(run_id, status, action, handle.generation_id)
        except Exception:
            return AutonomousOutcome(
                run_id,
                AutonomousStatus.FAILED,
                self._tools.action_for(handle.generation_id),
                handle.generation_id,
            )
        finally:
            with self._lock:
                if self._active is handle:
                    self._active = None
                self._admitted = False
                self._cancel_requested = False

    def cancel(self) -> bool:
        with self._lock:
            if self._admitted:
                self._cancel_requested = True
            handle = self._active
        if handle is None:
            return self._admitted
        return self._runtime.cancel(handle.generation_id)


@dataclass(slots=True)
class _UserSubmission:
    text: str
    future: asyncio.Future[str]


class PersistentPresenceRuntime:
    """One local cognition lane with monotonic idle admission and user priority."""

    def __init__(
        self,
        model_runtime: RunAwareConversationRuntime,
        core: ConversationCore,
        autonomous_runner: AutonomousCognitionRunner,
        sink: PresenceOutputSink,
        *,
        wake_policy: PresenceWakePolicy | None = None,
        idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
        input_queue_size: int = 16,
        max_consecutive_autonomous: int = 1,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if idle_timeout_s <= 0 or input_queue_size <= 0 or max_consecutive_autonomous <= 0:
            raise ValueError("presence bounds must be positive")
        self._model = model_runtime
        self._core = core
        self._runner = autonomous_runner
        self._sink = sink
        self._wake_policy = wake_policy or FixedPresenceWakePolicy()
        self._idle_timeout_s = idle_timeout_s
        self._max_consecutive = max_consecutive_autonomous
        self._clock = clock
        self._queue: asyncio.Queue[_UserSubmission | None] = asyncio.Queue(input_queue_size)
        self._task: asyncio.Task[None] | None = None
        self._active_conversation: ConversationRun | None = None
        self._last_activity = 0.0
        self._idle_latched = False
        self._consecutive_autonomous = 0
        self._opportunity_sequence = 0
        self._evidence: deque[PresenceEvidence] = deque(maxlen=PRESENCE_EVIDENCE_CAPACITY)
        self._evidence_sequence = 1
        self._closing = False

    @property
    def history(self) -> tuple[ContextMessage, ...]:
        return self._core.history

    def evidence(self) -> tuple[PresenceEvidence, ...]:
        return tuple(self._evidence)

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("presence runtime already started")
        start = getattr(self._model, "start", None)
        if callable(start):
            # Process containment is established by the runtime-owning thread.
            # Startup is bounded by ModelRuntime's deadline.
            start()
        self._last_activity = self._now()
        self._task = asyncio.create_task(self._run(), name="lilavel-persistent-presence")

    async def submit_user(self, text: str) -> str:
        if self._task is None or self._closing:
            raise RuntimeError("presence runtime is not accepting input")
        if not text or not text.strip():
            raise ValueError("user input must be non-empty")
        self._idle_latched = False
        self._consecutive_autonomous = 0
        self._last_activity = self._now()
        # User activity is priority STEER. Both cancellation calls are idempotent;
        # successor admission remains serialized by the single queue consumer.
        conversation = self._active_conversation
        if conversation is not None and not conversation.settled:
            await _await_daemon(conversation.cancel)
        await _await_daemon(self._runner.cancel)
        future = asyncio.get_running_loop().create_future()
        await self._queue.put(_UserSubmission(text.strip(), future))
        return await future

    async def stop(self) -> None:
        if self._task is None:
            return
        self._closing = True
        conversation = self._active_conversation
        if conversation is not None and not conversation.settled:
            await _await_daemon(conversation.cancel)
        await _await_daemon(self._runner.cancel)
        task = self._task
        if not task.done():
            await self._queue.put(None)
        try:
            await task
        finally:
            shutdown = getattr(self._model, "shutdown", None)
            if callable(shutdown):
                shutdown()
            self._task = None

    async def wait(self) -> None:
        task = self._task
        if task is None:
            raise RuntimeError("presence runtime is not started")
        await asyncio.shield(task)

    async def _run(self) -> None:
        while True:
            timeout = None if self._idle_latched else self._remaining_idle()
            try:
                if timeout is None:
                    submission = await self._queue.get()
                else:
                    submission = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except TimeoutError:
                await self._idle_opportunity()
                continue
            try:
                if submission is None:
                    return
                await self._run_user(submission)
            finally:
                self._queue.task_done()

    async def _run_user(self, submission: _UserSubmission) -> None:
        try:
            outcome = await _await_daemon(lambda: self._consume_user(submission.text))
        except BaseException as error:
            if not submission.future.done():
                submission.future.set_exception(error)
        else:
            if not submission.future.done():
                submission.future.set_result(outcome)
        finally:
            self._last_activity = self._now()

    def _consume_user(self, text: str) -> str:
        run = self._core.start_turn(text, supersede=True)
        self._active_conversation = run
        try:
            for event in run.events():
                if isinstance(event, ConversationTextDelta):
                    self._sink.publish(PresenceOutput("conversation_delta", event.delta))
                elif isinstance(event, ConversationCompleted):
                    self._sink.publish(PresenceOutput("conversation_complete", ""))
                elif isinstance(event, ConversationCancelled):
                    self._sink.publish(PresenceOutput("conversation_interrupted", ""))
                else:
                    assert isinstance(event, ConversationFailed)
                    self._sink.publish(PresenceOutput("conversation_failed", ""))
            return run.wait(0).status
        finally:
            if self._active_conversation is run:
                self._active_conversation = None

    async def _idle_opportunity(self) -> None:
        self._idle_latched = True
        self._opportunity_sequence += 1
        opportunity = IdleOpportunity(
            str(uuid4()), self._opportunity_sequence, max(0.0, self._now() - self._last_activity)
        )
        self._record("idle_opportunity", opportunity_id=opportunity.opportunity_id)
        decision = await self._wake_policy.decide(opportunity)
        self._record(
            "wake_decision",
            opportunity_id=opportunity.opportunity_id,
            result="wake" if decision.wake else "no_wake",
        )
        if not decision.wake or self._consecutive_autonomous >= self._max_consecutive:
            return
        self._consecutive_autonomous += 1
        self._record("cognition_admitted", opportunity_id=opportunity.opportunity_id)
        self._runner.admit()
        outcome = await _await_daemon(lambda: self._runner.run(self._core.history))
        self._record(
            "cognition_settled",
            opportunity_id=opportunity.opportunity_id,
            run_id=outcome.run_id,
            result=outcome.status.value,
        )
        self._last_activity = self._now()

    def _remaining_idle(self) -> float:
        return max(0.0, self._idle_timeout_s - (self._now() - self._last_activity))

    def _now(self) -> float:
        return self._clock() if self._clock is not None else asyncio.get_running_loop().time()

    def _record(
        self,
        kind: str,
        *,
        opportunity_id: str | None = None,
        run_id: str | None = None,
        result: str | None = None,
    ) -> None:
        self._evidence.append(
            PresenceEvidence(self._evidence_sequence, kind, opportunity_id, run_id, result)
        )
        self._evidence_sequence += 1


async def _await_daemon[T](function: Callable[[], T]) -> T:
    """Await blocking runtime work without a process-lifetime thread pool."""

    completed = threading.Event()
    outcome: list[tuple[T | None, BaseException | None]] = []

    def invoke() -> None:
        try:
            result = function()
        except BaseException as error:
            outcome.append((None, error))
        else:
            outcome.append((result, None))
        finally:
            completed.set()

    threading.Thread(target=invoke, name="lilavel-presence-bridge", daemon=True).start()
    while not completed.is_set():
        await asyncio.sleep(0)
    result, error = outcome[0]
    if error is not None:
        raise error
    return result  # type: ignore[return-value]
