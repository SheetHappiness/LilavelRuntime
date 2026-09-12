"""Runtime-owned deadline-driven host for one-shot temporal cognition."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum

from .contracts import CognitionTrigger, CognitionTriggerSource
from .semantic_actor import (
    SemanticActorError,
    SemanticAdmission,
    SemanticAdmissionStatus,
    SemanticMailboxFull,
)
from .temporal import MAX_WAKE_HISTORY, TemporalCoordinator

__all__ = [
    "TemporalDispatchEvidence",
    "TemporalDispatchStatus",
    "TemporalHost",
    "TemporalHostError",
    "TemporalHostShutdownTimeout",
    "TemporalHostState",
]

type TemporalTriggerSubmitter = Callable[[CognitionTrigger], Awaitable[SemanticAdmission]]


class TemporalHostError(RuntimeError):
    """Base class for temporal host lifecycle failures."""


class TemporalHostShutdownTimeout(TemporalHostError):
    """The host worker did not settle before its bounded shutdown deadline."""


class TemporalHostState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class TemporalDispatchStatus(StrEnum):
    ADMITTED = "admitted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class TemporalDispatchEvidence:
    """Bounded wake/trigger dispatch evidence without temporal reason text."""

    wake_intent_id: str
    trigger_id: str
    status: TemporalDispatchStatus
    reason_code: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.wake_intent_id, "wake_intent_id"),
            (self.trigger_id, "trigger_id"),
        ):
            if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > 128:
                raise ValueError(f"{name} must be non-empty bounded text")
        if type(self.status) is not TemporalDispatchStatus:
            raise TypeError("status must be a TemporalDispatchStatus")
        if self.reason_code is not None and (
            type(self.reason_code) is not str
            or not self.reason_code.strip()
            or len(self.reason_code.encode("utf-8")) > 128
        ):
            raise ValueError("reason_code must be non-empty bounded text")


class TemporalHost:
    """Turn coordinator deadlines into one-shot actor admissions.

    The host has no model, runner, application, environment, or tool access.
    It waits on the earliest coordinator deadline or a narrow coordinator
    change notification. ``wake`` is a deterministic test seam for clocks
    whose time can advance without a real-time event.
    """

    def __init__(
        self,
        coordinator: TemporalCoordinator,
        submit_trigger: TemporalTriggerSubmitter,
        *,
        shutdown_timeout: float = 2.0,
        evidence_capacity: int = MAX_WAKE_HISTORY,
    ) -> None:
        if type(coordinator) is not TemporalCoordinator:
            raise TypeError("coordinator must be a TemporalCoordinator")
        if not callable(submit_trigger):
            raise TypeError("submit_trigger must be callable")
        if isinstance(shutdown_timeout, bool) or shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be positive")
        if isinstance(evidence_capacity, bool) or not 0 < evidence_capacity <= MAX_WAKE_HISTORY:
            raise ValueError("evidence_capacity is outside its bound")
        self._coordinator = coordinator
        self._submit_trigger = submit_trigger
        self._shutdown_timeout = shutdown_timeout
        self._state = TemporalHostState.NEW
        self._signal = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._evidence: list[TemporalDispatchEvidence] = []
        self._evidence_capacity = evidence_capacity
        self._failure: BaseException | None = None

    @property
    def state(self) -> TemporalHostState:
        return self._state

    @property
    def failure(self) -> BaseException | None:
        return self._failure

    def evidence(self) -> tuple[TemporalDispatchEvidence, ...]:
        return tuple(self._evidence)

    def wake(self) -> None:
        """Wake the deadline wait; useful for deterministic external clocks."""

        self._signal.set()

    async def start(self) -> None:
        if self._state is not TemporalHostState.NEW:
            raise TemporalHostError(f"cannot start temporal host from {self._state.value}")
        loop = asyncio.get_running_loop()

        def on_change() -> None:
            try:
                loop.call_soon_threadsafe(self.wake)
            except RuntimeError:
                return

        self._unsubscribe = self._coordinator.subscribe(on_change)
        self._state = TemporalHostState.RUNNING
        self._worker = asyncio.create_task(self._run(), name="lilavel-temporal-host")

    async def stop(self) -> None:
        if self._state is TemporalHostState.NEW:
            self._state = TemporalHostState.STOPPED
            return
        if self._state is TemporalHostState.STOPPED:
            return
        self._state = TemporalHostState.STOPPING
        self.wake()
        worker = self._worker
        if worker is None:
            self._state = TemporalHostState.FAILED
            raise TemporalHostError("temporal host worker is missing")
        try:
            await asyncio.shield(
                asyncio.wait_for(asyncio.shield(worker), timeout=self._shutdown_timeout)
            )
        except TimeoutError as error:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            self._state = TemporalHostState.FAILED
            raise TemporalHostShutdownTimeout(
                "temporal host did not settle before shutdown deadline"
            ) from error
        finally:
            self._remove_subscription()
        if self._failure is not None:
            failure = self._failure
            raise TemporalHostError("temporal host failed before shutdown") from failure
        if self._state is TemporalHostState.STOPPING:
            self._state = TemporalHostState.STOPPED

    async def dispatch_due(self) -> tuple[TemporalDispatchEvidence, ...]:
        """Poll once through the coordinator seam and submit each due trigger once."""

        if self._state is not TemporalHostState.RUNNING:
            return ()
        triggers = self._coordinator.poll_due()
        records: list[TemporalDispatchEvidence] = []
        for trigger in triggers:
            records.append(await self._dispatch(trigger))
        return tuple(records)

    async def _run(self) -> None:
        try:
            while self._state is TemporalHostState.RUNNING:
                self._signal.clear()
                deadline = self._coordinator.next_deadline()
                if deadline is None:
                    await self._signal.wait()
                    continue
                delay = (deadline - self._coordinator.now()).total_seconds()
                if delay > 0:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self._signal.wait(), timeout=delay)
                    continue
                await self.dispatch_due()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._failure = error
            self._state = TemporalHostState.FAILED

    async def _dispatch(self, trigger: CognitionTrigger) -> TemporalDispatchEvidence:
        if (
            type(trigger) is not CognitionTrigger
            or trigger.source is not CognitionTriggerSource.TEMPORAL
        ):
            return TemporalDispatchEvidence(
                trigger.wake_intent_id or "wake:invalid",
                trigger.trigger_id,
                TemporalDispatchStatus.FAILED,
                "invalid_temporal_trigger",
            )
        wake_id = trigger.wake_intent_id
        assert wake_id is not None
        try:
            admission = await self._submit_trigger(trigger)
        except SemanticMailboxFull:
            record = TemporalDispatchEvidence(
                wake_id, trigger.trigger_id, TemporalDispatchStatus.REJECTED, "actor_mailbox_full"
            )
        except SemanticActorError as error:
            reason = {
                "SemanticActorPoisoned": "actor_poisoned",
                "SemanticActorNotRunning": "actor_not_running",
            }.get(type(error).__name__, "actor_admission_failed")
            record = TemporalDispatchEvidence(
                wake_id, trigger.trigger_id, TemporalDispatchStatus.FAILED, reason
            )
        except Exception:
            record = TemporalDispatchEvidence(
                wake_id, trigger.trigger_id, TemporalDispatchStatus.FAILED, "dispatch_failed"
            )
        else:
            record = self._admission_evidence(trigger, admission)
        self._evidence.append(record)
        del self._evidence[: -self._evidence_capacity]
        return record

    @staticmethod
    def _admission_evidence(
        trigger: CognitionTrigger, admission: SemanticAdmission
    ) -> TemporalDispatchEvidence:
        status = {
            SemanticAdmissionStatus.ACCEPTED: TemporalDispatchStatus.ADMITTED,
            SemanticAdmissionStatus.DUPLICATE: TemporalDispatchStatus.DUPLICATE,
            SemanticAdmissionStatus.REJECTED: TemporalDispatchStatus.REJECTED,
        }[admission.status]
        return TemporalDispatchEvidence(
            trigger.wake_intent_id or "wake:invalid",
            trigger.trigger_id,
            status,
            admission.reason_code,
        )

    def _remove_subscription(self) -> None:
        unsubscribe = self._unsubscribe
        self._unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()
