"""The runtime-owned, provider-neutral semantic admission kernel."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

MAX_SEMANTIC_REQUEST_ID_BYTES = 128
MAX_SEMANTIC_SCOPE_ID_BYTES = 128
MAX_SEMANTIC_MAILBOX_CAPACITY = 128
MAX_SEMANTIC_FENCE_CAPACITY = 256
MAX_SEMANTIC_EVIDENCE = 512


class SemanticActorError(RuntimeError):
    """Base class for semantic actor lifecycle and admission failures."""


class SemanticActorNotRunning(SemanticActorError):
    """The actor has not reached its admitting lifecycle state."""


class SemanticActorPoisoned(SemanticActorError):
    """The actor failed closed after uncertain active-work containment."""


class SemanticActorShutdownTimeout(SemanticActorError):
    """Owned semantic work did not settle before actor shutdown."""


class SemanticMailboxFull(SemanticActorError):
    """The bounded semantic mailbox rejected a new unique request."""


class SemanticScopeMismatch(SemanticActorError):
    """A request was prepared for a different runtime semantic scope."""


class SemanticActorState(StrEnum):
    """Lifecycle of one runtime-owned semantic actor."""

    NEW = "new"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    POISONED = "poisoned"


class SemanticSourceKind(StrEnum):
    """Small source vocabulary for future admission clients."""

    USER = "user"
    REACTIVE = "reactive"
    EXTERNAL = "external"
    TEMPORAL = "temporal"
    AUTONOMOUS = "autonomous"


class SemanticPriority(StrEnum):
    """The only priority distinction in MIND-1F-B."""

    USER = "user"
    NON_USER = "non_user"


class SemanticEpisodeStatus(StrEnum):
    """Lifecycle status visible through settlement and safe evidence."""

    QUEUED = "queued"
    ACTIVE = "active"
    CANCELLATION_REQUESTED = "cancellation_requested"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


class SemanticAdmissionStatus(StrEnum):
    """Result of submitting a semantic request to the bounded mailbox."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


class SemanticEpisodeExecutor(Protocol):
    """Provider-neutral executor supplied by a future semantic adapter."""

    async def __call__(
        self,
        episode: SemanticEpisode,
        cancellation: SemanticCancellationToken,
    ) -> object: ...


class SemanticCancellationToken:
    """Actor-owned cooperative cancellation state.

    The token is intentionally tiny. An executor can inspect ``is_requested``
    at admission boundaries or await ``wait`` while doing cooperative work.
    The actor requests cancellation before waiting for settlement; the token
    therefore also covers the race before an executor has bound its own handle.
    """

    __slots__ = ("_event", "_requested")

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._requested = False

    @property
    def is_requested(self) -> bool:
        return self._requested

    def request(self) -> bool:
        """Request cancellation and return whether this call changed state."""

        if self._requested:
            return False
        self._requested = True
        self._event.set()
        return True

    async def wait(self) -> None:
        """Wait until the actor requests cooperative cancellation."""

        await self._event.wait()


@dataclass(frozen=True, slots=True)
class SemanticEpisodeRequest:
    """Typed admission descriptor with no provider or content authority."""

    request_id: str
    source_kind: SemanticSourceKind
    priority: SemanticPriority
    scope_id: str
    actor_session_id: str
    request_sequence: int
    executor: SemanticEpisodeExecutor

    def __post_init__(self) -> None:
        _require_bounded_text(self.request_id, "request_id", MAX_SEMANTIC_REQUEST_ID_BYTES)
        _require_bounded_text(self.scope_id, "scope_id", MAX_SEMANTIC_SCOPE_ID_BYTES)
        _require_bounded_text(self.actor_session_id, "actor_session_id", 128)
        if type(self.source_kind) is not SemanticSourceKind:
            raise TypeError("source_kind must be a SemanticSourceKind")
        if type(self.priority) is not SemanticPriority:
            raise TypeError("priority must be a SemanticPriority")
        if isinstance(self.request_sequence, bool) or self.request_sequence <= 0:
            raise ValueError("request_sequence must be positive")
        if not callable(self.executor):
            raise TypeError("executor must be callable")


@dataclass(frozen=True, slots=True)
class SemanticEpisode:
    """The bounded identity supplied to one specialized semantic executor."""

    request_id: str
    sequence: int
    source_kind: SemanticSourceKind
    priority: SemanticPriority
    scope_id: str
    actor_session_id: str

    def __post_init__(self) -> None:
        _require_bounded_text(self.request_id, "request_id", MAX_SEMANTIC_REQUEST_ID_BYTES)
        _require_bounded_text(self.scope_id, "scope_id", MAX_SEMANTIC_SCOPE_ID_BYTES)
        _require_bounded_text(self.actor_session_id, "actor_session_id", 128)
        if isinstance(self.sequence, bool) or self.sequence <= 0:
            raise ValueError("sequence must be positive")
        if type(self.source_kind) is not SemanticSourceKind:
            raise TypeError("source_kind must be a SemanticSourceKind")
        if type(self.priority) is not SemanticPriority:
            raise TypeError("priority must be a SemanticPriority")


@dataclass(frozen=True, slots=True)
class SemanticSettlement:
    """Explicit terminal settlement; opaque executor results are not repr'd."""

    request_id: str
    sequence: int
    status: SemanticEpisodeStatus
    reason_code: str | None = None
    result: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_bounded_text(self.request_id, "request_id", MAX_SEMANTIC_REQUEST_ID_BYTES)
        if isinstance(self.sequence, bool) or self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if type(self.status) is not SemanticEpisodeStatus:
            raise TypeError("status must be a SemanticEpisodeStatus")
        if self.reason_code is not None:
            _require_bounded_text(self.reason_code, "reason_code", 128)


@dataclass(frozen=True, slots=True)
class SemanticActorEvidence:
    """Correlation-safe actor evidence without semantic content or results."""

    sequence: int
    request_id: str
    source_kind: SemanticSourceKind
    priority: SemanticPriority
    status: SemanticEpisodeStatus
    cancellation_requested: bool = False
    preempted: bool = False
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _require_bounded_text(self.request_id, "request_id", MAX_SEMANTIC_REQUEST_ID_BYTES)
        if isinstance(self.sequence, bool) or self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if type(self.source_kind) is not SemanticSourceKind:
            raise TypeError("source_kind must be a SemanticSourceKind")
        if type(self.priority) is not SemanticPriority:
            raise TypeError("priority must be a SemanticPriority")
        if type(self.status) is not SemanticEpisodeStatus:
            raise TypeError("status must be a SemanticEpisodeStatus")
        if self.reason_code is not None:
            _require_bounded_text(self.reason_code, "reason_code", 128)


@dataclass(frozen=True, slots=True)
class SemanticAdmission:
    """An admission receipt whose future remains owned by the actor."""

    request_id: str
    sequence: int
    status: SemanticAdmissionStatus
    reason_code: str | None
    _settlement_future: asyncio.Future[SemanticSettlement] = field(repr=False, compare=False)
    episode: SemanticEpisode | None = field(default=None, repr=False, compare=False)

    async def wait(self) -> SemanticSettlement:
        """Wait for actor settlement without allowing caller cancellation to detach it."""

        return await asyncio.shield(self._settlement_future)


@dataclass(slots=True)
class _Record:
    request: SemanticEpisodeRequest
    episode: SemanticEpisode
    token: SemanticCancellationToken
    future: asyncio.Future[SemanticSettlement]
    status: SemanticEpisodeStatus = SemanticEpisodeStatus.QUEUED
    cancellation_requested: bool = False
    preempted: bool = False
    execution_task: asyncio.Task[object] | None = None


class SemanticActor:
    """One character-wide serialized semantic admission authority.

    The actor owns one worker task and a bounded mailbox. User-priority work
    enters a dedicated higher-priority lane and requests cooperative
    cancellation of active non-user work. The successor is never run until
    the active executor task has settled or the actor has failed closed as
    poisoned when containment is uncertain.

    Request fences are scoped to the current actor session. Settled history is
    never evicted inside a session: when the bounded settled-plus-in-flight
    capacity is reached, new unique requests are rejected until the actor is
    idle; then a fresh session ID is issued and the old session is retired.
    Old request descriptors are rejected by session mismatch rather than being
    re-executed. This is not restart-safe idempotency; a caller that rebuilds
    the same raw request identity in a new runtime actor session must provide
    its own durable effect fence.
    """

    def __init__(
        self,
        *,
        scope_id: str,
        mailbox_capacity: int = 16,
        fence_capacity: int = MAX_SEMANTIC_FENCE_CAPACITY,
        evidence_capacity: int = MAX_SEMANTIC_EVIDENCE,
        settlement_timeout: float = 1.0,
        actor_session_id: str | None = None,
    ) -> None:
        _require_bounded_text(scope_id, "scope_id", MAX_SEMANTIC_SCOPE_ID_BYTES)
        if (
            isinstance(mailbox_capacity, bool)
            or not 0 < mailbox_capacity <= MAX_SEMANTIC_MAILBOX_CAPACITY
        ):
            raise ValueError("mailbox_capacity is outside its bound")
        if (
            isinstance(fence_capacity, bool)
            or not 0 < fence_capacity <= MAX_SEMANTIC_FENCE_CAPACITY
        ):
            raise ValueError("fence_capacity is outside its bound")
        if (
            isinstance(evidence_capacity, bool)
            or not 0 < evidence_capacity <= MAX_SEMANTIC_EVIDENCE
        ):
            raise ValueError("evidence_capacity is outside its bound")
        if isinstance(settlement_timeout, bool) or settlement_timeout <= 0:
            raise ValueError("settlement_timeout must be positive")
        session_id = actor_session_id or f"actor:{uuid4()}"
        _require_bounded_text(session_id, "actor_session_id", 128)

        self._scope_id = scope_id
        self._mailbox_capacity = mailbox_capacity
        self._fence_capacity = fence_capacity
        self._evidence_capacity = evidence_capacity
        self._settlement_timeout = settlement_timeout
        self._session_id = session_id
        self._state = SemanticActorState.NEW
        self._user_queue: deque[_Record] = deque()
        self._non_user_queue: deque[_Record] = deque()
        self._in_flight: dict[tuple[str, str], _Record] = {}
        self._settled: OrderedDict[tuple[str, str], SemanticSettlement] = OrderedDict()
        self._evidence: deque[SemanticActorEvidence] = deque(maxlen=evidence_capacity)
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._active: _Record | None = None
        self._uncontained: set[asyncio.Task[object]] = set()
        self._next_request_sequence = 0
        self._next_episode_sequence = 0
        self._max_active = 0

    @property
    def scope_id(self) -> str:
        return self._scope_id

    @property
    def actor_session_id(self) -> str:
        return self._session_id

    @property
    def state(self) -> SemanticActorState:
        return self._state

    @property
    def active_episode(self) -> SemanticEpisode | None:
        return self._active.episode if self._active is not None else None

    @property
    def active_count(self) -> int:
        return 1 if self._active is not None else 0

    @property
    def max_active(self) -> int:
        return self._max_active

    @property
    def queued_count(self) -> int:
        return len(self._user_queue) + len(self._non_user_queue)

    @property
    def mailbox_capacity(self) -> int:
        return self._mailbox_capacity

    @property
    def settled_history_count(self) -> int:
        return len(self._settled)

    def evidence(self) -> tuple[SemanticActorEvidence, ...]:
        """Return bounded lifecycle metadata only."""

        return tuple(self._evidence)

    def create_request(
        self,
        request_id: str,
        *,
        source_kind: SemanticSourceKind,
        priority: SemanticPriority,
        executor: SemanticEpisodeExecutor,
        scope_id: str | None = None,
    ) -> SemanticEpisodeRequest:
        """Issue a session-bound descriptor for one future admission."""

        self._next_request_sequence += 1
        return SemanticEpisodeRequest(
            request_id=request_id,
            source_kind=source_kind,
            priority=priority,
            scope_id=self._scope_id if scope_id is None else scope_id,
            actor_session_id=self._session_id,
            request_sequence=self._next_request_sequence,
            executor=executor,
        )

    new_request = create_request

    async def start(self) -> None:
        """Start the actor-owned mailbox worker."""

        if self._state is not SemanticActorState.NEW:
            raise SemanticActorError(f"cannot start actor from {self._state.value}")
        self._state = SemanticActorState.RUNNING
        self._worker = asyncio.create_task(self._run(), name="lilavel-semantic-actor")

    async def admit(self, request: SemanticEpisodeRequest) -> SemanticAdmission:
        """Admit, fence, or deterministically reject one semantic request."""

        if type(request) is not SemanticEpisodeRequest:
            raise TypeError("request must be a SemanticEpisodeRequest")
        key = (request.actor_session_id, request.request_id)

        existing = self._in_flight.get(key)
        if existing is not None:
            self._record_request_evidence(
                existing,
                SemanticEpisodeStatus.DUPLICATE,
                reason_code="request_already_admitted",
            )
            return SemanticAdmission(
                request.request_id,
                existing.episode.sequence,
                SemanticAdmissionStatus.DUPLICATE,
                "request_already_admitted",
                existing.future,
                existing.episode,
            )

        settled = self._settled.get(key)
        if settled is not None:
            future = _resolved_future(settled)
            self._record_request_evidence_from_request(
                request,
                SemanticEpisodeStatus.DUPLICATE,
                sequence=settled.sequence,
                reason_code="request_already_settled",
            )
            return SemanticAdmission(
                request.request_id,
                settled.sequence,
                SemanticAdmissionStatus.DUPLICATE,
                "request_already_settled",
                future,
                None,
            )

        if request.scope_id != self._scope_id:
            return self._rejected_admission(request, "scope_mismatch")
        if request.actor_session_id != self._session_id:
            return self._rejected_admission(request, "actor_session_mismatch")
        if self._state is SemanticActorState.POISONED:
            return self._rejected_admission(request, "actor_poisoned")
        if self._state is not SemanticActorState.RUNNING:
            return self._rejected_admission(request, "actor_not_running")
        if len(self._settled) + len(self._in_flight) >= self._fence_capacity:
            return self._rejected_admission(request, "fence_capacity")
        if self.queued_count >= self._mailbox_capacity:
            raise SemanticMailboxFull(
                f"semantic mailbox is full at capacity {self._mailbox_capacity}"
            )

        self._next_episode_sequence += 1
        episode = SemanticEpisode(
            request_id=request.request_id,
            sequence=self._next_episode_sequence,
            source_kind=request.source_kind,
            priority=request.priority,
            scope_id=request.scope_id,
            actor_session_id=request.actor_session_id,
        )
        record = _Record(
            request,
            episode,
            SemanticCancellationToken(),
            asyncio.get_running_loop().create_future(),
        )
        self._in_flight[key] = record
        if request.priority is SemanticPriority.USER:
            self._user_queue.append(record)
        else:
            self._non_user_queue.append(record)
        self._record_request_evidence(record, SemanticEpisodeStatus.QUEUED)

        active = self._active
        if (
            request.priority is SemanticPriority.USER
            and active is not None
            and active.request.priority is SemanticPriority.NON_USER
        ):
            self._request_cancellation(active, preempted=True, reason_code="user_priority")
        self._wake.set()
        return SemanticAdmission(
            request.request_id,
            episode.sequence,
            SemanticAdmissionStatus.ACCEPTED,
            None,
            record.future,
            episode,
        )

    async def submit(self, request: SemanticEpisodeRequest) -> SemanticAdmission:
        """Compatibility alias for future admission clients."""

        return await self.admit(request)

    async def stop(self) -> None:
        """Close admission, settle owned work, and never admit after shutdown."""

        if self._state is SemanticActorState.NEW:
            self._state = SemanticActorState.STOPPED
            return
        if self._state is SemanticActorState.STOPPED:
            return

        self._state = SemanticActorState.STOPPING
        self._reject_queued("actor_shutdown")
        if self._active is not None:
            self._request_cancellation(
                self._active,
                preempted=False,
                reason_code="actor_shutdown",
            )
        self._wake.set()
        worker = self._worker
        if worker is None:
            self._state = SemanticActorState.POISONED
            raise SemanticActorShutdownTimeout("semantic actor worker is missing")
        try:
            await asyncio.shield(
                asyncio.wait_for(asyncio.shield(worker), timeout=self._settlement_timeout * 2)
            )
        except TimeoutError as error:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            self._state = SemanticActorState.POISONED
            raise SemanticActorShutdownTimeout(
                "semantic actor work did not settle before shutdown deadline"
            ) from error
        if self._uncontained:
            self._state = SemanticActorState.POISONED
            raise SemanticActorShutdownTimeout("semantic actor retains uncontained work")
        if self._state is SemanticActorState.STOPPING:
            self._state = SemanticActorState.STOPPED

    async def _run(self) -> None:
        try:
            while True:
                if self._state is SemanticActorState.POISONED:
                    self._reject_queued("actor_poisoned")
                    return
                if not self.queued_count:
                    self._wake.clear()
                    if self._state is SemanticActorState.STOPPING:
                        return
                    await self._wake.wait()
                    continue
                if self._state is SemanticActorState.STOPPING:
                    self._reject_queued("actor_shutdown")
                    continue

                record = self._pop_next()
                self._active = record
                self._max_active = max(self._max_active, 1)
                record.status = SemanticEpisodeStatus.ACTIVE
                self._record_request_evidence(record, SemanticEpisodeStatus.ACTIVE)
                status, result, reason_code, uncontained = await self._execute(record)
                self._active = None
                self._settle(record, status, result=result, reason_code=reason_code)
                if uncontained:
                    self._state = SemanticActorState.POISONED
                    self._reject_queued("actor_poisoned")
                    return
                self._maybe_rotate_session()
        except asyncio.CancelledError:
            if self._active is not None:
                record = self._active
                self._active = None
                self._request_cancellation(record, preempted=False, reason_code="worker_cancelled")
                self._settle(
                    record,
                    SemanticEpisodeStatus.CANCELLED,
                    reason_code="worker_cancelled",
                )
            self._reject_queued("actor_shutdown")
            raise
        except BaseException:
            if self._active is not None:
                record = self._active
                self._active = None
                self._settle(record, SemanticEpisodeStatus.FAILED, reason_code="actor_failed")
            self._state = SemanticActorState.POISONED
            self._reject_queued("actor_poisoned")

    async def _execute(
        self, record: _Record
    ) -> tuple[SemanticEpisodeStatus, object | None, str | None, bool]:
        async def invoke() -> object:
            return await record.request.executor(record.episode, record.token)

        execution = asyncio.create_task(
            invoke(), name=f"lilavel-semantic-episode-{record.episode.sequence}"
        )
        record.execution_task = execution
        cancellation = asyncio.create_task(
            record.token.wait(), name=f"lilavel-semantic-cancel-{record.episode.sequence}"
        )
        try:
            done, _ = await asyncio.wait(
                (execution, cancellation), return_when=asyncio.FIRST_COMPLETED
            )
            if execution in done:
                return self._execution_result(record, execution)

            record.status = SemanticEpisodeStatus.CANCELLATION_REQUESTED
            self._record_request_evidence(
                record,
                SemanticEpisodeStatus.CANCELLATION_REQUESTED,
                reason_code="cancellation_requested",
            )
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(execution), timeout=self._settlement_timeout
                )
            except TimeoutError:
                contained = await self._cancel_and_join(execution, record)
                if not contained:
                    return (
                        SemanticEpisodeStatus.FAILED,
                        None,
                        "uncontainable_settlement",
                        True,
                    )
                return SemanticEpisodeStatus.CANCELLED, None, "cancelled", False
            except asyncio.CancelledError:
                return SemanticEpisodeStatus.CANCELLED, None, "cancelled", False
            except BaseException as error:
                if getattr(error, "semantic_uncontained", False) is True:
                    return SemanticEpisodeStatus.FAILED, None, "uncontainable_settlement", True
                return SemanticEpisodeStatus.FAILED, None, "executor_failed", False
            del result
            return SemanticEpisodeStatus.CANCELLED, None, "cancelled", False
        except asyncio.CancelledError:
            record.token.request()
            contained = await self._cancel_and_join(execution, record)
            if not contained:
                return SemanticEpisodeStatus.FAILED, None, "uncontainable_settlement", True
            return SemanticEpisodeStatus.CANCELLED, None, "worker_cancelled", False
        finally:
            cancellation.cancel()
            await asyncio.gather(cancellation, return_exceptions=True)
            record.execution_task = None

    @staticmethod
    def _execution_result(
        record: _Record, execution: asyncio.Task[object]
    ) -> tuple[SemanticEpisodeStatus, object | None, str | None, bool]:
        try:
            result = execution.result()
        except asyncio.CancelledError:
            return SemanticEpisodeStatus.CANCELLED, None, "cancelled", False
        except BaseException as error:
            if getattr(error, "semantic_uncontained", False) is True:
                return SemanticEpisodeStatus.FAILED, None, "uncontainable_settlement", True
            return SemanticEpisodeStatus.FAILED, None, "executor_failed", False
        if record.cancellation_requested:
            return SemanticEpisodeStatus.CANCELLED, None, "cancelled_after_request", False
        executor_status = getattr(result, "semantic_status", None)
        if type(executor_status) is SemanticEpisodeStatus:
            return (
                executor_status,
                result,
                getattr(result, "reason_code", None),
                False,
            )
        return SemanticEpisodeStatus.COMPLETED, result, None, False

    async def _cancel_and_join(self, execution: asyncio.Task[object], record: _Record) -> bool:
        del record
        execution.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(execution), timeout=self._settlement_timeout)
        except TimeoutError:
            self._uncontained.add(execution)
            execution.add_done_callback(self._uncontained_done)
            return False
        except BaseException as error:
            if getattr(error, "semantic_uncontained", False) is True:
                self._uncontained.add(execution)
                execution.add_done_callback(self._uncontained_done)
                return False
            return True
        return True

    def _uncontained_done(self, task: asyncio.Task[object]) -> None:
        self._uncontained.discard(task)
        with suppress(asyncio.CancelledError):
            task.exception()

    def _pop_next(self) -> _Record:
        if self._user_queue:
            return self._user_queue.popleft()
        return self._non_user_queue.popleft()

    def _request_cancellation(self, record: _Record, *, preempted: bool, reason_code: str) -> None:
        record.cancellation_requested = True
        record.preempted = record.preempted or preempted
        record.token.request()
        if record.status in {
            SemanticEpisodeStatus.ACTIVE,
            SemanticEpisodeStatus.CANCELLATION_REQUESTED,
        }:
            record.status = SemanticEpisodeStatus.CANCELLATION_REQUESTED
            self._record_request_evidence(
                record,
                SemanticEpisodeStatus.CANCELLATION_REQUESTED,
                reason_code=reason_code,
            )

    def _settle(
        self,
        record: _Record,
        status: SemanticEpisodeStatus,
        *,
        result: object | None = None,
        reason_code: str | None = None,
    ) -> None:
        record.status = status
        settlement = SemanticSettlement(
            record.request.request_id,
            record.episode.sequence,
            status,
            reason_code,
            result if status is SemanticEpisodeStatus.COMPLETED else None,
        )
        key = (record.request.actor_session_id, record.request.request_id)
        self._in_flight.pop(key, None)
        self._settled[key] = SemanticSettlement(
            record.request.request_id,
            record.episode.sequence,
            status,
            reason_code,
        )
        if not record.future.done():
            record.future.set_result(settlement)
        self._record_request_evidence(record, status, reason_code=reason_code)

    def _reject_queued(self, reason_code: str) -> None:
        while self._user_queue:
            self._settle(
                self._user_queue.popleft(),
                SemanticEpisodeStatus.CANCELLED,
                reason_code=reason_code,
            )
        while self._non_user_queue:
            self._settle(
                self._non_user_queue.popleft(),
                SemanticEpisodeStatus.CANCELLED,
                reason_code=reason_code,
            )

    def _maybe_rotate_session(self) -> None:
        if (
            len(self._settled) < self._fence_capacity
            or self._active is not None
            or self.queued_count
        ):
            return
        self._session_id = f"actor:{uuid4()}"
        self._settled.clear()

    def _rejected_admission(
        self, request: SemanticEpisodeRequest, reason_code: str
    ) -> SemanticAdmission:
        future = _resolved_future(
            SemanticSettlement(
                request.request_id,
                0,
                SemanticEpisodeStatus.REJECTED,
                reason_code,
            )
        )
        self._record_request_evidence_from_request(
            request,
            SemanticEpisodeStatus.REJECTED,
            sequence=0,
            reason_code=reason_code,
        )
        return SemanticAdmission(
            request.request_id,
            0,
            SemanticAdmissionStatus.REJECTED,
            reason_code,
            future,
            None,
        )

    def _record_request_evidence(
        self,
        record: _Record,
        status: SemanticEpisodeStatus,
        *,
        reason_code: str | None = None,
    ) -> None:
        self._evidence.append(
            SemanticActorEvidence(
                record.episode.sequence,
                record.request.request_id,
                record.request.source_kind,
                record.request.priority,
                status,
                record.cancellation_requested,
                record.preempted,
                reason_code,
            )
        )

    def _record_request_evidence_from_request(
        self,
        request: SemanticEpisodeRequest,
        status: SemanticEpisodeStatus,
        *,
        sequence: int,
        reason_code: str,
    ) -> None:
        self._evidence.append(
            SemanticActorEvidence(
                sequence,
                request.request_id,
                request.source_kind,
                request.priority,
                status,
                False,
                False,
                reason_code,
            )
        )


def _resolved_future(settlement: SemanticSettlement) -> asyncio.Future[SemanticSettlement]:
    future = asyncio.get_running_loop().create_future()
    future.set_result(settlement)
    return future


def _require_bounded_text(value: str, name: str, maximum_bytes: int) -> None:
    if not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ValueError(f"{name} exceeds its byte bound")


__all__ = [
    "MAX_SEMANTIC_EVIDENCE",
    "MAX_SEMANTIC_FENCE_CAPACITY",
    "MAX_SEMANTIC_MAILBOX_CAPACITY",
    "MAX_SEMANTIC_REQUEST_ID_BYTES",
    "MAX_SEMANTIC_SCOPE_ID_BYTES",
    "SemanticActor",
    "SemanticActorError",
    "SemanticActorEvidence",
    "SemanticActorPoisoned",
    "SemanticActorShutdownTimeout",
    "SemanticActorState",
    "SemanticActorNotRunning",
    "SemanticAdmission",
    "SemanticAdmissionStatus",
    "SemanticCancellationToken",
    "SemanticEpisode",
    "SemanticEpisodeExecutor",
    "SemanticEpisodeRequest",
    "SemanticEpisodeStatus",
    "SemanticMailboxFull",
    "SemanticPriority",
    "SemanticScopeMismatch",
    "SemanticSettlement",
    "SemanticSourceKind",
]
