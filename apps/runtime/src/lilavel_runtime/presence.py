"""Bounded persistent CLI presence built on Core and the V3 tool lifecycle."""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, cast
from uuid import uuid4

from lilavel_contracts import ToolCall, ToolEffect, ToolResult, ToolResultStatus, ToolSpec
from lilavel_core import (
    CanonicalMessage,
    ContextMessage,
    ConversationCancelled,
    ConversationCompleted,
    ConversationCore,
    ConversationEvent,
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
from lilavel_core.production_cognition import build_character_guidance

from .conversation_adapter import ConversationExecutionAdapter, ConversationExecutionResult
from .mind import MAX_INTENTION_TEXT_BYTES, MindIntention, MindProjection, MindState
from .semantic_actor import SemanticCancellationToken
from .tool_registry import ApplicationToolRegistry, ToolAuthorization, ToolBinding
from .tool_session import DeterministicToolSession, DeterministicToolSessionFactory

PRESENCE_SAY: Final = "presence.say"
PRESENCE_STAY_SILENT: Final = "presence.stay_silent"
MAX_PRESENCE_TEXT_BYTES: Final = 4_096
DEFAULT_IDLE_TIMEOUT_S: Final = 300.0
PRESENCE_EVIDENCE_CAPACITY: Final = 256
PRESENCE_ACTION_STATE_CAPACITY: Final = 64
MAX_APPRAISAL_CONTEXT_MESSAGES: Final = 12
MAX_APPRAISAL_RESULT_BYTES: Final = 4_096
APPRAISAL_CONTROL_GUIDANCE: Final[tuple[str, ...]] = (
    "This is a transient internal mind appraisal, not a user turn.",
    "Do not speak, call tools, write conversation history, or create memory.",
    'Return exactly one JSON object: {"action":"no_change"} or '
    '{"action":"create_intention","text":"..."}. '
    "Review the complete latest canonical user and assistant turn. The assistant "
    "message is evidence of what Lilavel already did, not just background context.",
    "An unfinished user situation is not automatically an unfinished Lilavel "
    "intention. Create at most one short intention only when a concrete future "
    "action for Lilavel remains after this turn, was not already performed in the "
    "assistant response, and could add new value later.",
    "Do not create an intention to repeat, paraphrase, or re-deliver advice, a "
    "reminder, an explanation, or a follow-up question already given. Do not "
    "create one merely because the topic may continue or because there is no "
    "specific future Lilavel action. Otherwise return no_change.",
)
AUTONOMOUS_CONTROL_GUIDANCE: Final[tuple[str, ...]] = (
    "This is a transient, noncanonical idle cognition opportunity.",
    "Act only on the specific runtime-owned intention supplied in this request.",
    "Choose exactly one terminal tool: presence.say(text) or "
    "presence.stay_silent(). Do not answer a current user turn or emit ordinary assistant text.",
)

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
    action_text: str | None = None
    action_succeeded: bool = False


@dataclass(frozen=True, slots=True)
class PresenceEvidence:
    sequence: int
    kind: str
    opportunity_id: str | None = None
    run_id: str | None = None
    result: str | None = None
    intention_id: str | None = None


@dataclass(slots=True)
class _ActionState:
    action: PresenceAction | None = None
    action_text: str | None = None
    succeeded: bool = False


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

    def action_text_for(self, generation_id: str) -> str | None:
        with self._lock:
            state = self._states.get(generation_id)
            return None if state is None else state.action_text

    def action_succeeded_for(self, generation_id: str) -> bool:
        with self._lock:
            state = self._states.get(generation_id)
            return False if state is None else state.succeeded

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
        with self._lock:
            state = self._states.get(correlation.context.generation_id)
            if state is not None:
                state.action_text = text.strip()
                state.succeeded = True
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
        with self._lock:
            state = self._states.get(correlation.context.generation_id)
            if state is not None:
                state.succeeded = True
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.NONE)


class MindAppraisalAction(StrEnum):
    NO_CHANGE = "no_change"
    CREATE_INTENTION = "create_intention"


@dataclass(frozen=True, slots=True)
class MindAppraisal:
    action: MindAppraisalAction
    text: str | None = None


@dataclass(frozen=True, slots=True)
class MindAppraisalOutcome:
    run_id: str
    status: AutonomousStatus
    appraisal: MindAppraisal
    generation_id: str | None


def parse_mind_appraisal(raw: str) -> MindAppraisal:
    """Parse the one bounded JSON result accepted by MIND-0."""

    if len(raw.encode("utf-8")) > MAX_APPRAISAL_RESULT_BYTES:
        return MindAppraisal(MindAppraisalAction.NO_CHANGE)
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (TypeError, ValueError):
        return MindAppraisal(MindAppraisalAction.NO_CHANGE)
    if not isinstance(value, dict):
        return MindAppraisal(MindAppraisalAction.NO_CHANGE)
    parsed = cast(dict[str, object], value)
    if not isinstance(parsed.get("action"), str):
        return MindAppraisal(MindAppraisalAction.NO_CHANGE)
    action = parsed["action"]
    text = parsed.get("text")
    if action == MindAppraisalAction.NO_CHANGE.value and set(parsed) == {"action"}:
        return MindAppraisal(MindAppraisalAction.NO_CHANGE)
    if (
        action == MindAppraisalAction.CREATE_INTENTION.value
        and set(parsed) == {"action", "text"}
        and isinstance(text, str)
        and text.strip()
        and len(text.encode("utf-8")) <= MAX_INTENTION_TEXT_BYTES
    ):
        return MindAppraisal(MindAppraisalAction.CREATE_INTENTION, text.strip())
    return MindAppraisal(MindAppraisalAction.NO_CHANGE)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate JSON key")
    return dict(pairs)


class MindAppraiser:
    """One cancellable, tool-free transient appraisal generation."""

    def __init__(self, runtime: RunAwareConversationRuntime) -> None:
        self._runtime = runtime
        self._lock = threading.Lock()
        self._active: RuntimeGeneration | None = None
        self._admitted = False
        self._cancel_requested = False

    def admit(self) -> None:
        with self._lock:
            if self._admitted:
                raise RuntimeError("mind appraisal is already admitted")
            self._admitted = True
            self._cancel_requested = False

    def run(self, history: Sequence[ContextMessage]) -> MindAppraisalOutcome:
        run_id = f"appraisal:{uuid4()}"
        recent = tuple(history[-MAX_APPRAISAL_CONTEXT_MESSAGES:])
        request = ModelRequest(
            messages=recent if recent else None,
            prompt=None if recent else "Review the available canonical conversation.",
            system_prompt=(*build_character_guidance(), *APPRAISAL_CONTROL_GUIDANCE),
        )
        try:
            handle = self._runtime.generate_for_run(
                request, scope_id="local-presence", logical_run_id=run_id
            )
        except Exception:
            with self._lock:
                self._admitted = False
            return MindAppraisalOutcome(
                run_id, AutonomousStatus.FAILED, MindAppraisal(MindAppraisalAction.NO_CHANGE), None
            )
        with self._lock:
            self._active = handle
            cancel_requested = self._cancel_requested
        if cancel_requested:
            self._runtime.cancel(handle.generation_id)

        parts: list[str] = []
        result_bytes = 0
        status = AutonomousStatus.FAILED
        appraisal = MindAppraisal(MindAppraisalAction.NO_CHANGE)
        try:
            for event in handle.events():
                if isinstance(event, TextDelta):
                    result_bytes += len(event.delta.encode("utf-8"))
                    if result_bytes <= MAX_APPRAISAL_RESULT_BYTES:
                        parts.append(event.delta)
                elif isinstance(event, GenerationCompleted):
                    status = AutonomousStatus.COMPLETED
                    if result_bytes <= MAX_APPRAISAL_RESULT_BYTES:
                        appraisal = parse_mind_appraisal("".join(parts).strip())
                elif isinstance(event, GenerationCancelled):
                    status = AutonomousStatus.CANCELLED
                elif isinstance(event, GenerationFailedEvent):
                    status = AutonomousStatus.FAILED
            return MindAppraisalOutcome(run_id, status, appraisal, handle.generation_id)
        except Exception:
            return MindAppraisalOutcome(
                run_id,
                AutonomousStatus.FAILED,
                MindAppraisal(MindAppraisalAction.NO_CHANGE),
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


class AutonomousCognitionRunner:
    """Thin transient-run coordinator; ModelRuntime remains the lifecycle owner."""

    def __init__(
        self,
        runtime: RunAwareConversationRuntime,
        tools: PresenceToolSessionFactory,
    ) -> None:
        self._runtime = runtime
        self._tools = tools
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

    def run(self, intention: MindIntention) -> AutonomousOutcome:
        run_id = f"autonomous:{uuid4()}"
        request = ModelRequest(
            prompt=(
                f"Runtime-owned active intention (context, not a user turn):\n{intention.text}"
            ),
            system_prompt=(*build_character_guidance(), *AUTONOMOUS_CONTROL_GUIDANCE),
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
            return AutonomousOutcome(
                run_id,
                status,
                action,
                handle.generation_id,
                self._tools.action_text_for(handle.generation_id),
                self._tools.action_succeeded_for(handle.generation_id),
            )
        except Exception:
            return AutonomousOutcome(
                run_id,
                AutonomousStatus.FAILED,
                self._tools.action_for(handle.generation_id),
                handle.generation_id,
                self._tools.action_text_for(handle.generation_id),
                self._tools.action_succeeded_for(handle.generation_id),
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
        mind_state: MindState | None = None,
        appraiser: MindAppraiser | None = None,
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
        self._mind_state = mind_state or MindState()
        self._appraiser = appraiser or MindAppraiser(model_runtime)
        self._wake_policy = wake_policy or FixedPresenceWakePolicy()
        self._conversation_execution = ConversationExecutionAdapter()
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
        self._actor_user_submitter: Callable[[str], Awaitable[str]] | None = None
        self._actor_user_blocks = 0
        self._legacy_active = False
        self._legacy_settled = asyncio.Event()
        self._legacy_settled.set()

    @property
    def history(self) -> tuple[ContextMessage, ...]:
        return self._core.history

    @property
    def canonical_history(self) -> tuple[CanonicalMessage, ...]:
        return self._core.canonical_history

    @property
    def conversation_core(self) -> ConversationCore:
        """Expose the CLI Core session to the runtime composition boundary."""

        return self._core

    def evidence(self) -> tuple[PresenceEvidence, ...]:
        return tuple(self._evidence)

    @property
    def mind_state(self) -> MindState:
        return self._mind_state

    @property
    def mind_projection(self) -> MindProjection:
        return self._mind_state.projection()

    @property
    def actor_user_block_count(self) -> int:
        """Return the number of actor USER episodes excluding legacy lanes."""

        return self._actor_user_blocks

    @property
    def legacy_lane_active(self) -> bool:
        """Return whether transitional appraisal/autonomy work is running."""

        return self._legacy_active

    def bind_actor_user_submitter(self, submitter: Callable[[str], Awaitable[str]]) -> None:
        """Route direct callers through the runtime actor when composed."""

        if not callable(submitter):
            raise TypeError("submitter must be callable")
        if self._actor_user_submitter is not None and self._actor_user_submitter is not submitter:
            raise RuntimeError("presence actor submitter is already bound")
        self._actor_user_submitter = submitter

    async def begin_actor_user(self) -> None:
        """Exclude legacy appraisal/autonomy before an actor USER episode."""

        if self._task is None or self._closing:
            raise RuntimeError("presence runtime is not accepting actor work")
        self._actor_user_blocks += 1
        try:
            self._idle_latched = True
            self._last_activity = self._now()
            conversation = self._active_conversation
            if conversation is not None and not conversation.settled:
                await _await_daemon(conversation.cancel)
            await _await_daemon(self._runner.cancel)
            await _await_daemon(self._appraiser.cancel)
            await self._legacy_settled.wait()
        except BaseException:
            self._actor_user_blocks -= 1
            raise

    async def end_actor_user(self) -> None:
        """Release one actor-owned USER exclusion after full settlement."""

        if self._actor_user_blocks > 0:
            self._actor_user_blocks -= 1
        self._last_activity = self._now()

    async def execute_actor_user(
        self,
        text: str,
        conversation_executor: ConversationExecutionAdapter,
        cancellation: SemanticCancellationToken,
    ) -> ConversationExecutionResult:
        """Execute one actor-admitted CLI turn and then its legacy appraisal.

        This method is an execution callback, not an admission queue. The
        character-wide actor owns ordering, cancellation, replay fencing, and
        settlement; Presence supplies only its Core session and presentation
        sink, then keeps the transitional appraisal inside the actor episode.
        """

        result = await conversation_executor.execute_core_turn(
            self._core,
            text,
            self._present_conversation_event,
            cancellation,
        )
        if result.outcome.status == "completed":
            await self._run_actor_appraisal(result.run, cancellation)
        return result

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
        submitter = self._actor_user_submitter
        if submitter is not None:
            return await submitter(text)
        return await self._submit_legacy_user(text)

    async def _submit_legacy_user(self, text: str) -> str:
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
        await _await_daemon(self._appraiser.cancel)
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
        await _await_daemon(self._appraiser.cancel)
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
        self._legacy_active = True
        self._legacy_settled.clear()
        try:
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
        finally:
            self._legacy_active = False
            self._legacy_settled.set()

    def _consume_user(self, text: str) -> str:
        result: ConversationExecutionResult | None = None
        try:
            result = self._conversation_execution.execute_core_turn_sync(
                self._core,
                text,
                self._present_conversation_event,
                on_run=self._set_active_conversation,
            )
            if result.outcome.status == "completed":
                self._run_appraisal(result.run)
            return result.outcome.status
        finally:
            if result is None or self._active_conversation is result.run:
                self._active_conversation = None

    def _set_active_conversation(self, run: ConversationRun) -> None:
        self._active_conversation = run

    def _run_appraisal(self, run: ConversationRun) -> None:
        self._record("appraisal_admitted", run_id=run.run_id)
        self._appraiser.admit()
        outcome = self._appraiser.run(self._core.history)
        self._record(
            "appraisal_settled",
            run_id=run.run_id,
            result=outcome.status.value,
        )
        if (
            outcome.status is AutonomousStatus.COMPLETED
            and outcome.appraisal.action is MindAppraisalAction.CREATE_INTENTION
            and outcome.appraisal.text is not None
        ):
            intention = self._mind_state.create_intention(
                outcome.appraisal.text,
                user_message_id=run.user_message_id,
                assistant_message_id=run.assistant_message_id,
            )
            if intention is not None:
                self._record(
                    "intention_created",
                    run_id=run.run_id,
                    result=outcome.appraisal.action.value,
                    intention_id=intention.intention_id,
                )

    async def _idle_opportunity(self) -> None:
        self._legacy_active = True
        self._legacy_settled.clear()
        try:
            self._idle_latched = True
            self._opportunity_sequence += 1
            opportunity = IdleOpportunity(
                str(uuid4()),
                self._opportunity_sequence,
                max(0.0, self._now() - self._last_activity),
            )
            self._record("idle_opportunity", opportunity_id=opportunity.opportunity_id)
            decision = await self._wake_policy.decide(opportunity)
            self._record(
                "wake_decision",
                opportunity_id=opportunity.opportunity_id,
                result="wake" if decision.wake else "no_wake",
            )
            if (
                self._actor_user_blocks > 0
                or not decision.wake
                or self._consecutive_autonomous >= self._max_consecutive
            ):
                return
            intention = self._mind_state.active_intention()
            if intention is None:
                return
            self._consecutive_autonomous += 1
            self._record(
                "cognition_admitted",
                opportunity_id=opportunity.opportunity_id,
                intention_id=intention.intention_id,
            )
            self._runner.admit()
            outcome = await _await_daemon(lambda: self._runner.run(intention))
            if (
                outcome.action is PresenceAction.SAY
                and outcome.action_succeeded
                and outcome.action_text is not None
            ):
                action = self._mind_state.mark_expressed(
                    intention.intention_id, outcome.action_text
                )
                if action is not None:
                    self._record(
                        "self_action_recorded",
                        opportunity_id=opportunity.opportunity_id,
                        run_id=outcome.run_id,
                        intention_id=intention.intention_id,
                    )
            self._record(
                "cognition_settled",
                opportunity_id=opportunity.opportunity_id,
                run_id=outcome.run_id,
                result=outcome.status.value,
                intention_id=intention.intention_id,
            )
            self._last_activity = self._now()
        finally:
            self._legacy_active = False
            self._legacy_settled.set()

    async def _run_actor_appraisal(
        self, run: ConversationRun, cancellation: SemanticCancellationToken
    ) -> None:
        appraisal_task = asyncio.create_task(
            _await_daemon(lambda: self._run_appraisal(run)),
            name=f"lilavel-cli-appraisal-{run.run_id}",
        )
        cancellation_task = asyncio.create_task(
            cancellation.wait(), name=f"lilavel-cli-appraisal-cancel-{run.run_id}"
        )
        try:
            done, _ = await asyncio.wait(
                (appraisal_task, cancellation_task), return_when=asyncio.FIRST_COMPLETED
            )
            if appraisal_task in done:
                appraisal_task.result()
                return
            await _await_daemon(self._appraiser.cancel)
            await appraisal_task
        finally:
            cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)

    def _present_conversation_event(self, event: ConversationEvent) -> None:
        if isinstance(event, ConversationTextDelta):
            self._sink.publish(PresenceOutput("conversation_delta", event.delta))
        elif isinstance(event, ConversationCompleted):
            self._sink.publish(PresenceOutput("conversation_complete", ""))
        elif isinstance(event, ConversationCancelled):
            self._sink.publish(PresenceOutput("conversation_interrupted", ""))
        else:
            assert isinstance(event, ConversationFailed)
            self._sink.publish(PresenceOutput("conversation_failed", ""))

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
        intention_id: str | None = None,
    ) -> None:
        self._evidence.append(
            PresenceEvidence(
                self._evidence_sequence,
                kind,
                opportunity_id,
                run_id,
                result,
                intention_id,
            )
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
