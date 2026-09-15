"""Opt-in, one-shot Discord idle initiative composition."""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import threading
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from lilavel_core import (
    ContextMessage,
    ConversationCore,
    ConversationRun,
    RunAwareConversationRuntime,
)
from lilavel_runtime import (
    ActionProposalKind,
    ActivityState,
    AmbientSpeechRolloutMode,
    CognitionEvidenceStage,
    CognitionModelEvidence,
    CognitionOutcome,
    CognitionTrigger,
    CognitionTriggerSource,
    DeterministicInterventionPolicy,
    FloorState,
    FreshnessBucket,
    FreshnessClass,
    HandlingState,
    IntentionKind,
    IntentionStatus,
    MindExecutionResult,
    MindExecutionStatus,
    MindIntention,
    MindState,
    ProactiveCapabilityState,
    SemanticAdmission,
    SemanticPriority,
    SemanticSourceKind,
    SocialPermissionContext,
    SocialSensitivity,
    SpeakingSurfaceState,
    SpeechAccounting,
)
from lilavel_runtime.cognition_model import APPRAISAL_REASON, IDLE_REASON
from lilavel_runtime.proposal_application import (
    MindStateProvenance,
    ProposalApplicationCoordinator,
    SpeechRevalidationStatus,
)
from lilavel_runtime.semantic_actor import SemanticCancellationToken

from .tool import DISCORD_SEND_MESSAGE_NAME, DiscordToolSessionFactory

MAX_PROACTIVE_EVIDENCE = 256
MAX_PROACTIVE_HISTORY = 64
PROACTIVE_DIAGNOSTICS_ENV = "LILAVEL_DISCORD_PROACTIVE_DIAGNOSTICS"
_SAFE_DIAGNOSTIC_KINDS = frozenset(
    {
        "appraisal_completed",
        "appraisal_generation_completed",
        "appraisal_generation_failed",
        "appraisal_parse_create_intention",
        "appraisal_parse_invalid",
        "appraisal_parse_no_change",
        "appraisal_started",
        "cognition_submitted",
        "idle_armed",
        "idle_cancelled_by_user",
        "idle_expired",
        "idle_generation_completed",
        "idle_generation_failed",
        "idle_ineligible",
        "idle_parse_invalid",
        "idle_parse_fulfill",
        "idle_parse_speak",
        "idle_parse_stay_silent",
        "intention_absent",
        "intention_present",
        "send_confirmed",
        "send_failed",
        "send_unknown",
        "silence",
        "speech_allowed",
        "speech_denied",
        "target_bound",
        "target_disabled_multiple_subjects",
    }
)
_SAFE_DIAGNOSTIC_RESULTS = frozenset(
    {
        "admission_failed",
        "application_failed",
        "application_rejected",
        "cancelled",
        "completed_quiet",
        "completed_with_application",
        "confirmed",
        "denied",
        "failed",
        "fulfillment_not_proposed",
        "invalid",
        "no_change",
        "none",
        "provider_or_runtime",
        "settlement_failed",
        "speech_permission_denied",
        "stay_silent",
        "speak",
        "unknown",
        "uncontained",
    }
)


def read_proactive_diagnostics_from_environment(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Read the explicit opt-in switch for content-free proactive diagnostics."""

    source = os.environ if environ is None else environ
    raw_value = source.get(PROACTIVE_DIAGNOSTICS_ENV)
    if raw_value is None or raw_value.strip() == "0":
        return False
    if raw_value.strip() == "1":
        return True
    raise ValueError(f"{PROACTIVE_DIAGNOSTICS_ENV} must be '0' or '1'")


class DiscordProactiveDiagnostics:
    """Opt-in JSONL stderr sink for bounded proactive evidence."""

    def __init__(
        self,
        emit: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        self._emit = _emit_proactive_jsonl if emit is None else emit
        self._sequence = 0
        self._lock = threading.Lock()

    def emit_startup(self, *, proactive_enabled: bool, idle_timeout_s: float) -> None:
        self._record(
            {
                "kind": "startup",
                "proactive_enabled": proactive_enabled,
                "configured_idle_interval_s": idle_timeout_s,
                "diagnostics_enabled": True,
                "cognition_tools_exposed": False,
            }
        )

    def emit_evidence(self, evidence: DiscordProactiveEvidence) -> None:
        kind = evidence.kind if evidence.kind in _SAFE_DIAGNOSTIC_KINDS else "unknown"
        result = evidence.result
        if result not in _SAFE_DIAGNOSTIC_RESULTS:
            result = None
        self._record({"kind": kind, "result": result})

    def _record(self, fields: Mapping[str, object]) -> None:
        with self._lock:
            self._sequence += 1
            event = {
                "component": "proactive",
                "sequence": self._sequence,
                **dict(fields),
            }
        with suppress(BaseException):
            self._emit(event)


def proactive_diagnostics_from_environment() -> DiscordProactiveDiagnostics | None:
    """Create the stderr sink only for an explicit diagnostics opt-in."""

    if not read_proactive_diagnostics_from_environment():
        return None
    return DiscordProactiveDiagnostics()


def _emit_proactive_jsonl(event: Mapping[str, object]) -> None:
    print(
        json.dumps(dict(event), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


@dataclass(frozen=True, slots=True)
class DiscordProactiveEvidence:
    """Content-free evidence for the bounded smoke experiment."""

    sequence: int
    kind: str
    result: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or self.sequence <= 0:
            raise ValueError("evidence sequence must be positive")
        if not self.kind or len(self.kind.encode("utf-8")) > 64:
            raise ValueError("evidence kind is invalid")
        if self.result is not None and (not self.result or len(self.result.encode("utf-8")) > 128):
            raise ValueError("evidence result is invalid")


@dataclass(slots=True)
class _IdleAttempt:
    generation: int
    user_epoch: int
    subject: str
    intention_id: str
    intention_kind: IntentionKind
    trigger_id: str
    send_claimed: bool = False
    speech_evidence_before: int = 0


@dataclass(frozen=True, slots=True)
class _AppraisalRecord:
    user_epoch: int
    subject: str
    history: tuple[ContextMessage, ...]
    provenance: MindStateProvenance


class DiscordProactivePresence:
    """RuntimePresence for one explicit opt-in Discord smoke experiment.

    This component owns only the process-local target binding, one idle timer,
    and transient appraisal snapshots. Cognition admission and execution stay
    on the runtime's existing actor and Mind executor.
    """

    def __init__(
        self,
        model_runtime: RunAwareConversationRuntime,
        mind_state: MindState,
        *,
        idle_timeout_s: float,
        evidence_sink: Callable[[DiscordProactiveEvidence], None] | None = None,
    ) -> None:
        if not callable(getattr(model_runtime, "generate_for_run", None)):
            raise TypeError("model_runtime must provide generate_for_run")
        if type(mind_state) is not MindState:
            raise TypeError("mind_state must be a MindState")
        if isinstance(idle_timeout_s, bool) or not math.isfinite(idle_timeout_s):
            raise ValueError("idle_timeout_s must be finite")
        if not 1.0 <= idle_timeout_s <= 600.0:
            raise ValueError("idle_timeout_s must be between 1 and 600 seconds")
        self._model_runtime = model_runtime
        self._mind_state = mind_state
        self._idle_timeout_s = idle_timeout_s
        self._actor_internal_submitter: (
            Callable[[CognitionTrigger], Awaitable[SemanticAdmission]] | None
        ) = None
        self._runtime_state: Callable[[], object] | None = None
        self._user_work: Callable[[], bool] | None = None
        self._application: ProposalApplicationCoordinator | None = None
        self._speech_accounting: SpeechAccounting | None = None
        self._target_subject: str | None = None
        self._target_channel: Any | None = None
        self._target_disabled = False
        self._closing = False
        self._started = False
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._idle_task: asyncio.Task[None] | None = None
        self._idle_generation = 0
        self._user_epoch = 0
        self._idle_attempt: _IdleAttempt | None = None
        self._appraisal_records: dict[str, _AppraisalRecord] = {}
        self._internal_tasks: set[asyncio.Task[None]] = set()
        self._evidence_sink = evidence_sink
        self._evidence: deque[DiscordProactiveEvidence] = deque(maxlen=MAX_PROACTIVE_EVIDENCE)
        self._evidence_sequence = 1
        self._lock = threading.RLock()

    @property
    def mind_state(self) -> MindState:
        return self._mind_state

    @property
    def idle_timeout_s(self) -> float:
        return self._idle_timeout_s

    @property
    def target_bound(self) -> bool:
        with self._lock:
            return self._target_subject is not None and not self._target_disabled

    @property
    def target_disabled(self) -> bool:
        with self._lock:
            return self._target_disabled

    def proactive_capability_state(self) -> ProactiveCapabilityState:
        """Return the current trusted state used by context composition."""

        with self._lock:
            target_bound = self._target_subject is not None and not self._target_disabled
        return ProactiveCapabilityState(True, target_bound, self._idle_timeout_s)

    @property
    def idle_timer_active(self) -> bool:
        task = self._idle_task
        return task is not None and not task.done()

    def event_loop(self) -> asyncio.AbstractEventLoop | None:
        """Return the loop that owns the currently started smoke runtime."""

        return self._event_loop

    def evidence(self) -> tuple[DiscordProactiveEvidence, ...]:
        with self._lock:
            return tuple(self._evidence)

    def bind_runtime(
        self,
        runtime_state: Callable[[], object],
        user_work_active_or_queued: Callable[[], bool],
    ) -> None:
        if not callable(runtime_state) or not callable(user_work_active_or_queued):
            raise TypeError("runtime state and USER work providers must be callable")
        if self._started:
            raise RuntimeError("runtime bindings must be installed before startup")
        self._runtime_state = runtime_state
        self._user_work = user_work_active_or_queued

    def bind_actor_internal_submitter(
        self,
        submitter: Callable[[CognitionTrigger], Awaitable[SemanticAdmission]],
    ) -> None:
        if not callable(submitter):
            raise TypeError("submitter must be callable")
        if (
            self._actor_internal_submitter is not None
            and self._actor_internal_submitter is not submitter
        ):
            raise RuntimeError("proactive actor submitter is already bound")
        self._actor_internal_submitter = submitter

    def bind_target(self, subject: str, channel: Any) -> None:
        """Bind the first supported DM subject, or disable on ambiguity."""

        if not subject or channel is None:
            return
        with self._lock:
            if self._closing or self._target_disabled:
                return
            if self._target_subject is None:
                self._target_subject = subject
                self._target_channel = channel
                self._record_locked("target_bound")
                return
            if self._target_subject == subject:
                self._target_channel = channel
                return
            self._target_disabled = True
            self._target_channel = None
            self._idle_generation += 1
            self._idle_attempt = None
            self._record_locked("target_disabled_multiple_subjects")
            task = self._idle_task
            self._idle_task = None
        if task is not None and not task.done():
            task.cancel()

    def channel_for_send(self) -> Any | None:
        with self._lock:
            if not self._can_send_locked():
                return None
            return self._target_channel

    def can_send(self) -> bool:
        with self._lock:
            return self._can_send_locked()

    def claim_send(self) -> bool:
        """Atomically consume the one external send slot for this attempt."""

        with self._lock:
            if not self._can_send_locked() or self._idle_attempt is None:
                return False
            if self._idle_attempt.send_claimed:
                return False
            self._idle_attempt.send_claimed = True
            return True

    def state_provenance_for(self, outcome: CognitionOutcome) -> MindStateProvenance | None:
        """Expose only current successful USER provenance to state application."""

        if type(outcome) is not CognitionOutcome:
            return None
        record = self._appraisal_records.get(outcome.trigger_id)
        if record is None:
            return None
        with self._lock:
            if (
                self._closing
                or self._target_disabled
                or self._target_subject != record.subject
                or self._user_epoch != record.user_epoch
            ):
                return None
        return record.provenance

    def history_for_trigger(self, trigger_id: str) -> tuple[ContextMessage, ...] | None:
        record = self._appraisal_records.get(trigger_id)
        return None if record is None else record.history

    def record_cognition_evidence(self, evidence: CognitionModelEvidence) -> None:
        """Translate runtime cognition evidence into the adapter's safe lifecycle."""

        if type(evidence) is not CognitionModelEvidence:
            return
        if evidence.stage is CognitionEvidenceStage.GENERATION_COMPLETED:
            kind = (
                "appraisal_generation_completed"
                if evidence.reason == APPRAISAL_REASON
                else "idle_generation_completed"
            )
            self._record(kind)
            return
        if evidence.stage is CognitionEvidenceStage.GENERATION_FAILED:
            kind = (
                "appraisal_generation_failed"
                if evidence.reason == APPRAISAL_REASON
                else "idle_generation_failed"
            )
            self._record(kind, evidence.result)
            return
        parse_kind = {
            (APPRAISAL_REASON, "no_change"): "appraisal_parse_no_change",
            (APPRAISAL_REASON, "create_intention"): "appraisal_parse_create_intention",
            (APPRAISAL_REASON, "invalid"): "appraisal_parse_invalid",
            (IDLE_REASON, "speak"): "idle_parse_speak",
            (IDLE_REASON, "stay_silent"): "idle_parse_stay_silent",
            (IDLE_REASON, "fulfill"): "idle_parse_fulfill",
            (IDLE_REASON, "invalid"): "idle_parse_invalid",
        }
        parse_result = evidence.result
        if parse_result is None:
            return
        parse_kind = parse_kind.get((evidence.reason, parse_result))
        if parse_kind is not None:
            self._record(parse_kind)

    def resolve_speech_context(
        self,
        outcome: CognitionOutcome,
        index: int,
        proposal: object,
    ) -> SocialPermissionContext | None:
        del index, proposal
        accounting = self._speech_accounting
        if accounting is None:
            return None
        recent, budget = accounting.snapshot()
        with self._lock:
            current = self._idle_attempt
            active_intention = self._mind_state.active_intention()
            live = current is not None and current.trigger_id == outcome.trigger_id
            target_valid = self._target_subject is not None and not self._target_disabled
            user_work = self._user_work_active_or_queued()
            runtime_running = self._runtime_is_running()
            intention_live = (
                live
                and active_intention is not None
                and current is not None
                and active_intention.intention_id == current.intention_id
            )
        eligible = live and target_valid and not user_work and runtime_running and intention_live
        return SocialPermissionContext(
            priority=SemanticPriority.NON_USER,
            source_kind=SemanticSourceKind.INTERNAL,
            speaking_surface=(
                SpeakingSurfaceState.AVAILABLE
                if target_valid and live
                else SpeakingSurfaceState.UNAVAILABLE
            ),
            freshness_class=FreshnessClass.CONTINUITY,
            freshness=FreshnessBucket.FRESH if eligible else FreshnessBucket.STALE,
            activity=ActivityState.CURRENT if eligible else ActivityState.NOT_CURRENT,
            floor=FloorState.BUSY if user_work else FloorState.FREE,
            recent_speech=recent,
            intervention_budget=budget,
            handling=HandlingState.UNRESOLVED if intention_live else HandlingState.UNKNOWN,
            sensitivity=SocialSensitivity.ORDINARY,
            response_obligation=eligible
            and active_intention is not None
            and active_intention.kind is IntentionKind.DEFERRED_COMMITMENT,
            continuity_current=eligible,
        )

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("proactive presence is already started")
        if self._runtime_state is None or self._user_work is None:
            raise RuntimeError("proactive runtime bindings are incomplete")
        self._event_loop = asyncio.get_running_loop()
        start = getattr(self._model_runtime, "start", None)
        if callable(start):
            start()
        self._closing = False
        self._started = True
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._wait_for_stop(), name="lilavel-discord-proactive-presence"
        )

    async def _wait_for_stop(self) -> None:
        await self._stop_event.wait()

    async def stop(self) -> None:
        self._closing = True
        self._invalidate_idle()
        self._stop_event.set()
        task = self._task
        if task is not None:
            await task
            self._task = None
        if self._internal_tasks:
            await asyncio.gather(*self._internal_tasks, return_exceptions=True)
            self._internal_tasks.clear()
        shutdown = getattr(self._model_runtime, "shutdown", None)
        if callable(shutdown):
            shutdown()
        self._event_loop = None

    async def wait(self) -> None:
        task = self._task
        if task is None:
            raise RuntimeError("proactive presence is not started")
        await asyncio.shield(task)

    async def submit_user(self, text: str) -> str:
        del text
        raise RuntimeError("Discord USER work is admitted from the DM observation route")

    async def begin_actor_user(self) -> None:
        if self._closing:
            raise RuntimeError("proactive presence is closing")
        with self._lock:
            self._user_epoch += 1
            had_pending = self._idle_task is not None and not self._idle_task.done()
            had_pending = had_pending or self._idle_attempt is not None
            self._idle_generation += 1
            self._idle_attempt = None
            task = self._idle_task
            self._idle_task = None
            if had_pending:
                self._record_locked("idle_cancelled_by_user")
        if task is not None and not task.done():
            task.cancel()

    async def end_actor_user(self) -> None:
        return

    async def execute_actor_user(
        self,
        text: str,
        conversation_executor: Any,
        cancellation: SemanticCancellationToken,
    ) -> object:
        del text, conversation_executor, cancellation
        raise RuntimeError("Discord USER work must retain its trusted DM route")

    async def on_user_turn_completed(
        self,
        subject: str,
        core: ConversationCore,
        run: ConversationRun,
    ) -> None:
        submitter = self._actor_internal_submitter
        with self._lock:
            if (
                self._closing
                or self._target_disabled
                or self._target_subject != subject
                or submitter is None
            ):
                return
            user_epoch = self._user_epoch
        history = tuple(core.history)
        trigger = CognitionTrigger(
            (),
            APPRAISAL_REASON,
            source=CognitionTriggerSource.INTERNAL,
            source_refs=(f"conversation:{run.run_id}",),
        )
        if len(self._appraisal_records) >= MAX_PROACTIVE_HISTORY:
            oldest = next(iter(self._appraisal_records), None)
            if oldest is not None:
                del self._appraisal_records[oldest]
        self._appraisal_records[trigger.trigger_id] = _AppraisalRecord(
            user_epoch,
            subject,
            history,
            MindStateProvenance(run.user_message_id, run.assistant_message_id),
        )
        self._record("appraisal_started")
        try:
            admission = await submitter(trigger)
        except BaseException:
            self._record("appraisal_completed", "admission_failed")
            self._record("intention_absent")
            return
        self._record("cognition_submitted", admission.status.value)
        self._track_internal(
            self._settle_appraisal(admission, trigger.trigger_id, user_epoch, subject)
        )

    def _track_internal(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.create_task(
            self._await_internal(coroutine), name="lilavel-discord-proactive-settlement"
        )
        self._internal_tasks.add(task)
        task.add_done_callback(self._finish_internal_task)

    @staticmethod
    async def _await_internal(coroutine: Awaitable[None]) -> None:
        await coroutine

    async def _settle_appraisal(
        self,
        admission: SemanticAdmission,
        trigger_id: str,
        user_epoch: int,
        subject: str,
    ) -> None:
        settlement = await admission.wait()
        result = settlement.result
        result_code = (
            result.status.value
            if isinstance(result, MindExecutionResult)
            else settlement.status.value
        )
        self._record("appraisal_completed", result_code)
        if not isinstance(result, MindExecutionResult) or result.status not in {
            MindExecutionStatus.COMPLETED_QUIET,
            MindExecutionStatus.COMPLETED_WITH_APPLICATION,
        }:
            self._record("intention_absent")
            return
        with self._lock:
            current = (
                not self._closing
                and not self._target_disabled
                and self._target_subject == subject
                and self._user_epoch == user_epoch
            )
        intention = self._mind_state.active_intention()
        if not current or intention is None or intention.status is not IntentionStatus.ACTIVE:
            self._record("intention_absent")
            return
        self._record("intention_present")
        await self._arm_idle(subject, user_epoch, intention)

    async def _arm_idle(
        self,
        subject: str,
        user_epoch: int,
        intention: MindIntention,
    ) -> None:
        with self._lock:
            if (
                self._closing
                or self._target_disabled
                or self._target_subject != subject
                or self._user_epoch != user_epoch
            ):
                return
            self._idle_generation += 1
            generation = self._idle_generation
            previous = self._idle_task
            self._idle_attempt = None
            task = asyncio.create_task(
                self._idle_after(generation, user_epoch, subject, intention.intention_id),
                name="lilavel-discord-proactive-idle",
            )
            self._idle_task = task
            self._record_locked("idle_armed")
        if previous is not None and not previous.done():
            previous.cancel()

    async def _idle_after(
        self,
        generation: int,
        user_epoch: int,
        subject: str,
        intention_id: str,
    ) -> None:
        try:
            await asyncio.sleep(self._idle_timeout_s)
            await self._expire_idle(generation, user_epoch, subject, intention_id)
        except asyncio.CancelledError:
            return
        finally:
            current = asyncio.current_task()
            if self._idle_task is current:
                self._idle_task = None

    async def _expire_idle(
        self,
        generation: int,
        user_epoch: int,
        subject: str,
        intention_id: str,
    ) -> None:
        self._record("idle_expired")
        submitter = self._actor_internal_submitter
        with self._lock:
            eligible = (
                submitter is not None
                and not self._closing
                and self._runtime_is_running()
                and not self._target_disabled
                and self._target_subject == subject
                and self._idle_generation == generation
                and self._user_epoch == user_epoch
                and not self._user_work_active_or_queued()
            )
        intention = self._mind_state.active_intention()
        if intention is None or intention.status is not IntentionStatus.ACTIVE:
            eligible = False
        if intention is not None and intention.intention_id != intention_id:
            eligible = False
        if not eligible or submitter is None or intention is None:
            self._record("idle_ineligible")
            return

        opportunity_id = str(uuid4())
        trigger = CognitionTrigger(
            (),
            IDLE_REASON,
            source=CognitionTriggerSource.INTERNAL,
            source_refs=(f"idle:{opportunity_id}", intention_id),
        )
        with self._lock:
            if not self._runtime_is_running() or self._idle_generation != generation:
                self._record("idle_ineligible")
                return
            self._idle_attempt = _IdleAttempt(
                generation,
                user_epoch,
                subject,
                intention_id,
                intention.kind,
                trigger.trigger_id,
                speech_evidence_before=(
                    len(self._application.speech_evidence()) if self._application is not None else 0
                ),
            )
        try:
            admission = await submitter(trigger)
        except BaseException:
            with self._lock:
                self._idle_attempt = None
            self._record("idle_ineligible", "admission_failed")
            return
        self._record("cognition_submitted", admission.status.value)
        self._track_internal(self._settle_idle(admission, trigger.trigger_id, generation))

    async def _settle_idle(
        self,
        admission: SemanticAdmission,
        trigger_id: str,
        generation: int,
    ) -> None:
        settlement = await admission.wait()
        result = settlement.result
        with self._lock:
            attempt = self._idle_attempt
            current = (
                attempt is not None
                and attempt.trigger_id == trigger_id
                and attempt.generation == generation
            )
        if not current or attempt is None:
            return
        silence = isinstance(result, MindExecutionResult) and (
            (
                result.applied_action_kind is None
                and result.status is MindExecutionStatus.COMPLETED_QUIET
            )
            or (
                result.applied_action_kind is not None
                and result.applied_action_kind.value == "stay_silent"
            )
        )
        if silence:
            if attempt.intention_kind is IntentionKind.DEFERRED_COMMITMENT:
                self._record("idle_ineligible", "fulfillment_not_proposed")
            else:
                self._record("silence")
        else:
            evidence = self._latest_speech_evidence(attempt)
            if evidence is not None and evidence.revalidation is SpeechRevalidationStatus.DENIED:
                self._record("speech_denied", evidence.denial_reason)
            elif evidence is not None and evidence.revalidation is SpeechRevalidationStatus.ALLOWED:
                self._record("speech_allowed")
                if getattr(evidence.effect, "value", None) == "unknown":
                    self._record("send_unknown")
                elif getattr(evidence.effect, "value", None) == "confirmed":
                    self._record("send_confirmed")
                    self._consume_confirmed_attempt(attempt, result)
                else:
                    self._record("send_failed", getattr(evidence.effect, "value", None))
            elif evidence is not None and getattr(evidence.effect, "value", None) == "unknown":
                self._record("send_unknown")
            elif evidence is not None and getattr(evidence.effect, "value", None) == "confirmed":
                self._record("send_confirmed")
                self._consume_confirmed_attempt(attempt, result)
            elif evidence is not None:
                self._record("send_failed", getattr(evidence.effect, "value", None))
            else:
                result_code = (
                    result.status.value
                    if isinstance(result, MindExecutionResult)
                    else "settlement_failed"
                )
                self._record("idle_ineligible", result_code)
        with self._lock:
            if self._idle_attempt is attempt:
                self._idle_attempt = None

    def _consume_confirmed_attempt(self, attempt: _IdleAttempt, result: object) -> None:
        if not isinstance(result, MindExecutionResult):
            return
        text = result.applied_action_text
        if not isinstance(text, str) or not text.strip():
            return
        intention = self._mind_state.active_intention()
        if intention is None or intention.intention_id != attempt.intention_id:
            return
        self._mind_state.mark_expressed(intention.intention_id, text)

    def _latest_speech_evidence(self, attempt: _IdleAttempt) -> Any | None:
        application = self._application
        if application is None:
            return None
        evidence = application.speech_evidence()
        if len(evidence) <= attempt.speech_evidence_before:
            return None
        return evidence[-1]

    def bind_application(self, application: ProposalApplicationCoordinator) -> None:
        if type(application) is not ProposalApplicationCoordinator:
            raise TypeError("application must be a ProposalApplicationCoordinator")
        self._application = application
        self._speech_accounting = application.speech_accounting

    def _invalidate_idle(self) -> None:
        with self._lock:
            self._idle_generation += 1
            self._idle_attempt = None
            task = self._idle_task
            self._idle_task = None
        if task is not None and not task.done():
            task.cancel()

    def _user_work_active_or_queued(self) -> bool:
        callback = self._user_work
        if callback is None:
            return True
        try:
            return callback()
        except BaseException:
            return True

    def _runtime_is_running(self) -> bool:
        callback = self._runtime_state
        if callback is None:
            return False
        try:
            return getattr(callback(), "value", None) == "running"
        except BaseException:
            return False

    def _can_send_locked(self) -> bool:
        attempt = self._idle_attempt
        return (
            attempt is not None
            and not attempt.send_claimed
            and not self._closing
            and not self._target_disabled
            and self._target_subject == attempt.subject
            and self._target_channel is not None
            and self._idle_generation == attempt.generation
            and self._user_epoch == attempt.user_epoch
            and not self._user_work_active_or_queued()
            and self._runtime_is_running()
        )

    def _finish_internal_task(self, task: asyncio.Task[None]) -> None:
        self._internal_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    def _record(self, kind: str, result: str | None = None) -> None:
        with self._lock:
            self._record_locked(kind, result)

    def _record_locked(self, kind: str, result: str | None = None) -> None:
        evidence = DiscordProactiveEvidence(self._evidence_sequence, kind, result)
        self._evidence.append(evidence)
        self._evidence_sequence += 1
        sink = self._evidence_sink
        if sink is not None:
            with suppress(BaseException):
                sink(evidence)


def create_proactive_application(
    presence: DiscordProactivePresence,
    *,
    factory: DiscordToolSessionFactory | None = None,
) -> tuple[ProposalApplicationCoordinator, DiscordToolSessionFactory]:
    """Compose the existing Discord P4 send tool for the trusted target."""

    if factory is None:
        factory = DiscordToolSessionFactory(
            None,
            channel_resolver=presence.channel_for_send,
            availability=presence.can_send,
            send_authorizer=presence.claim_send,
            loop_resolver=presence.event_loop,
        )
        factory.bind_scope("runtime")
    application = ProposalApplicationCoordinator(
        presence.mind_state,
        scope_id="runtime",
        state_provenance=presence.state_provenance_for,
        tool_registry=factory.registry,
        tool_session_factory=factory,
        action_tool_names={
            # STAY_SILENT remains an inert existing action and needs no
            # Discord tool binding. The only effectful route is the existing
            # send_message schema.
            ActionProposalKind.SPEAK: DISCORD_SEND_MESSAGE_NAME,
        },
        ambient_speech_mode=AmbientSpeechRolloutMode.LIVE,
        speech_context_resolver=presence.resolve_speech_context,
        speech_policy=DeterministicInterventionPolicy(),
    )
    return application, factory


__all__ = [
    "DiscordProactiveDiagnostics",
    "DiscordProactiveEvidence",
    "DiscordProactivePresence",
    "MAX_PROACTIVE_EVIDENCE",
    "PROACTIVE_DIAGNOSTICS_ENV",
    "create_proactive_application",
    "proactive_diagnostics_from_environment",
    "read_proactive_diagnostics_from_environment",
]
