"""Minimal persistent-agent process lifecycle and bounded event ingress."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast
from uuid import uuid4

from .attention import DeterministicAttentionCognitionGate
from .cognition_episode import CognitionEpisodeRunner
from .contracts import (
    NO_COGNITION,
    CognitionDecision,
    CognitionEngine,
    CognitionGate,
    CognitionTrigger,
    CognitionTriggerSource,
    EnvironmentAdapter,
    EventRouter,
    Observation,
    ObservationReceipt,
    ObservationReceiptStatus,
    ObservationWindow,
    RuntimePresence,
    ToolSpec,
    WorldEvent,
)
from .conversation_adapter import ConversationExecutionAdapter
from .mind import MindState
from .mind_convergence import MindExecutionAdapter
from .proposal_application import ProposalApplicationCoordinator
from .semantic_actor import (
    SemanticActor,
    SemanticActorState,
    SemanticAdmission,
    SemanticAdmissionStatus,
    SemanticCancellationToken,
    SemanticEpisode,
    SemanticPriority,
    SemanticSourceKind,
)
from .temporal import TemporalCoordinator
from .temporal_host import TemporalHost, TemporalHostState


class RuntimeState(StrEnum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class LilavelRuntimeError(RuntimeError):
    """Base class for persistent-kernel lifecycle failures."""


class RuntimeRegistrationClosed(LilavelRuntimeError):
    pass


class RuntimeNotRunning(LilavelRuntimeError):
    pass


class RuntimeFailed(LilavelRuntimeError):
    pass


class RuntimeShutdownTimeout(RuntimeFailed):
    pass


class ObservationUnavailable(LilavelRuntimeError):
    """An explicit response step referred to an evicted or mismatched receipt."""


class ReactiveStepUnavailable(LilavelRuntimeError):
    """No explicit response route is configured for the runtime."""


class MindCompositionUnavailable(LilavelRuntimeError):
    """The optional new Mind/temporal composition was not configured."""


class DuplicateEnvironment(LilavelRuntimeError):
    pass


class DuplicateTool(LilavelRuntimeError):
    pass


class _ActorConversationPresence(Protocol):
    async def begin_actor_user(self) -> None: ...

    async def end_actor_user(self) -> None: ...

    async def execute_actor_user(
        self,
        text: str,
        conversation_executor: ConversationExecutionAdapter,
        cancellation: SemanticCancellationToken,
    ) -> object: ...


def _actor_conversation_presence(
    presence: RuntimePresence | None,
) -> _ActorConversationPresence | None:
    if presence is None:
        return None
    if not all(
        callable(getattr(presence, name, None))
        for name in ("begin_actor_user", "end_actor_user", "execute_actor_user")
    ):
        return None
    return cast(_ActorConversationPresence, presence)


@dataclass(frozen=True, slots=True)
class LilavelRuntimeHealth:
    state: RuntimeState
    healthy: bool
    environments: int
    tools: int
    queue_size: int
    queue_capacity: int
    accepted_events: int
    processed_events: int
    wake_decisions: int
    cognition_decisions: int
    cognition_triggers: int
    reactive_steps: int
    observation_window_size: int
    failure_code: str | None
    semantic_actor_state: SemanticActorState


class LilavelRuntime:
    """Top-level owner of Lilavel's process lifecycle.

    The kernel admits immutable world events into a bounded observation window.
    An explicit caller-owned compatibility step may later route one admitted
    observation to a conversational subsystem and back to its source
    environment. Admission itself never starts that step.
    """

    def __init__(
        self,
        *,
        event_queue_size: int = 128,
        observation_window_size: int | None = None,
        event_router: EventRouter | None = None,
        cognition_gate: CognitionGate | None = None,
        presence: RuntimePresence | None = None,
        semantic_actor: SemanticActor | None = None,
        mind_executor: MindExecutionAdapter | None = None,
        cognition_engine: CognitionEngine | None = None,
        mind_state: MindState | None = None,
        proposal_application_coordinator: ProposalApplicationCoordinator | None = None,
        temporal_coordinator: TemporalCoordinator | None = None,
        shutdown_timeout: float = 5.0,
    ) -> None:
        if event_queue_size <= 0:
            raise ValueError("event_queue_size must be positive")
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be positive")
        if observation_window_size is None:
            observation_window_size = event_queue_size
        if isinstance(observation_window_size, bool) or observation_window_size <= 0:
            raise ValueError("observation_window_size must be positive")
        self._state = RuntimeState.NEW
        self._queue: asyncio.Queue[Observation] = asyncio.Queue(maxsize=event_queue_size)
        self._observation_window = ObservationWindow(observation_window_size)
        self._event_router = event_router
        self._conversation_executor = ConversationExecutionAdapter(event_router)
        self._cognition_gate = (
            DeterministicAttentionCognitionGate() if cognition_gate is None else cognition_gate
        )
        self._presence = presence
        self._semantic_actor = semantic_actor or SemanticActor(scope_id="runtime")
        if mind_executor is not None and (
            cognition_engine is not None
            or mind_state is not None
            or proposal_application_coordinator is not None
        ):
            raise ValueError("mind_executor cannot be combined with Mind component inputs")
        if cognition_engine is not None and (
            mind_state is None or proposal_application_coordinator is None
        ):
            raise ValueError(
                "cognition_engine requires mind_state and proposal_application_coordinator"
            )
        if cognition_engine is None and (
            mind_state is not None or proposal_application_coordinator is not None
        ):
            raise ValueError(
                "mind_state and proposal_application_coordinator require cognition_engine"
            )
        if cognition_engine is not None:
            assert mind_state is not None
            assert proposal_application_coordinator is not None
            mind_executor = MindExecutionAdapter(
                CognitionEpisodeRunner(
                    cognition_engine,
                    self._observation_window,
                    mind_state,
                    scope_id=self._semantic_actor.scope_id,
                    application_authority=proposal_application_coordinator.application_authority,
                ),
                proposal_application_coordinator,
            )
        if temporal_coordinator is not None and mind_executor is None:
            raise ValueError("temporal_coordinator requires a configured Mind executor")
        self._mind_executor = mind_executor
        self._temporal_coordinator = temporal_coordinator
        self._temporal_host = (
            TemporalHost(temporal_coordinator, self.submit_cognition)
            if temporal_coordinator is not None
            else None
        )
        self._shutdown_timeout = shutdown_timeout
        self._environments: dict[str, EnvironmentAdapter] = {}
        self._tools: dict[str, ToolSpec] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._started = asyncio.Event()
        self._stop_requested = asyncio.Event()
        self._admission_closed = asyncio.Event()
        self._submissions_settled = asyncio.Event()
        self._submissions_settled.set()
        self._supervisor: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._ingress_task: asyncio.Task[None] | None = None
        self._adapter_tasks: tuple[asyncio.Task[None], ...] = ()
        self._user_exclusion_tasks: set[asyncio.Task[None]] = set()
        self._route_failure: BaseException | None = None
        self._failure: BaseException | None = None
        self._pending_submissions = 0
        self._accepted_events = 0
        self._processed_events = 0
        self._wake_decisions = 0
        self._cognition_decisions = 0
        self._cognition_triggers = 0
        self._reactive_steps = 0
        self._next_observation_sequence = 0
        bind_actor_user_submitter = getattr(self._presence, "bind_actor_user_submitter", None)
        if callable(bind_actor_user_submitter):
            bind_actor_user_submitter(self.submit_user)
        bind_actor_internal_submitter = getattr(
            self._presence, "bind_actor_internal_submitter", None
        )
        if callable(bind_actor_internal_submitter):
            bind_actor_internal_submitter(self.submit_internal_cognition)

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def active_route_count(self) -> int:
        """Return active or queued reactive work owned by the actor."""

        return self._semantic_actor.active_count + self._semantic_actor.queued_count

    @property
    def observation_window(self) -> ObservationWindow:
        return self._observation_window

    @property
    def failure(self) -> BaseException | None:
        return self._failure or self._route_failure

    @property
    def semantic_actor(self) -> SemanticActor:
        """Return the one character-wide actor owned by this runtime."""

        return self._semantic_actor

    @property
    def mind_executor(self) -> MindExecutionAdapter | None:
        """Return the optional new Mind executor beneath the actor."""

        return self._mind_executor

    @property
    def temporal_host(self) -> TemporalHost | None:
        """Return the runtime-owned temporal deadline host, when configured."""

        return self._temporal_host

    @property
    def temporal_coordinator(self) -> TemporalCoordinator | None:
        """Return the optional runtime-owned temporal coordinator."""

        return self._temporal_coordinator

    async def submit_cognition(self, trigger: CognitionTrigger) -> SemanticAdmission:
        """Admit non-user cognition through the one semantic actor."""

        if type(trigger) is not CognitionTrigger:
            raise TypeError("trigger must be a CognitionTrigger")
        async with self._lifecycle_lock:
            if self._state is not RuntimeState.RUNNING:
                raise RuntimeNotRunning(f"runtime is {self._state.value}")
            executor = self._mind_executor
            if executor is None:
                raise MindCompositionUnavailable("runtime has no configured Mind executor")
            source_kind = {
                CognitionTriggerSource.EXTERNAL: SemanticSourceKind.EXTERNAL,
                CognitionTriggerSource.TEMPORAL: SemanticSourceKind.TEMPORAL,
                CognitionTriggerSource.INTERNAL: SemanticSourceKind.INTERNAL,
            }[trigger.source]
            request_id = (
                trigger.trigger_id
                if trigger.source is not CognitionTriggerSource.INTERNAL
                else f"internal:{trigger.trigger_id}"
            )

            async def execute(
                episode: SemanticEpisode, cancellation: SemanticCancellationToken
            ) -> object:
                del episode
                return await executor.execute(trigger, cancellation)

            request = self._semantic_actor.create_request(
                request_id,
                source_kind=source_kind,  # type: ignore[arg-type]
                priority=SemanticPriority.NON_USER,
                executor=execute,
            )
            return await self._semantic_actor.submit(request)

    submit_mind_trigger = submit_cognition
    submit_trigger = submit_cognition

    async def submit_internal_cognition(self, trigger: CognitionTrigger) -> SemanticAdmission:
        """Runtime-owned callback for local/internal cognition producers."""

        if trigger.source is not CognitionTriggerSource.INTERNAL:
            raise ValueError("internal cognition submission requires an INTERNAL trigger")
        return await self.submit_cognition(trigger)

    def register_environment(self, adapter: EnvironmentAdapter) -> None:
        self._ensure_registration_open()
        environment_id = adapter.environment_id
        if not environment_id or not environment_id.strip():
            raise ValueError("environment_id must be non-empty")
        if environment_id in self._environments:
            raise DuplicateEnvironment(environment_id)
        self._environments[environment_id] = adapter

    def register_tool(self, tool: ToolSpec) -> None:
        self._ensure_registration_open()
        if tool.name in self._tools:
            raise DuplicateTool(tool.name)
        self._tools[tool.name] = tool

    def tools(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools.values())

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._state is not RuntimeState.NEW:
                raise LilavelRuntimeError(f"cannot start runtime from {self._state.value}")
            self._state = RuntimeState.STARTING
            self._supervisor = asyncio.create_task(
                self._supervise(), name="lilavel-runtime-supervisor"
            )
        try:
            await self._started.wait()
        except asyncio.CancelledError:
            with suppress(LilavelRuntimeError):
                await asyncio.shield(self.stop())
            raise
        if self.health().state is RuntimeState.FAILED:
            raise RuntimeFailed("runtime failed during startup") from self._failure

    async def admit_observation(self, event: WorldEvent) -> ObservationReceipt:
        """Admit one world event without starting cognition or a response.

        Admission waits for bounded ingress capacity, adopts the immutable
        event into the recent observation window, and returns a receipt. No
        wake policy, router, model, Core turn, tool, or scheduler is invoked.
        """

        if type(event) is not WorldEvent:
            raise TypeError("event must be a WorldEvent")

        async with self._lifecycle_lock:
            if self._state is not RuntimeState.RUNNING:
                raise RuntimeNotRunning(f"runtime is {self._state.value}")
            self._next_observation_sequence += 1
            observation = Observation(
                observation_id=str(uuid4()),
                sequence=self._next_observation_sequence,
                event=event,
            )
            self._pending_submissions += 1
            self._submissions_settled.clear()

        try:
            async with asyncio.TaskGroup() as group:
                put_task = group.create_task(self._queue.put(observation))
                close_task = group.create_task(self._admission_closed.wait())
                done, pending = await asyncio.wait(
                    (put_task, close_task), return_when=asyncio.FIRST_COMPLETED
                )
                admitted = put_task in done
                for task in pending:
                    task.cancel()
            if not admitted:
                raise RuntimeNotRunning("runtime admission closed while event was waiting")
            self._observation_window.admit(observation)
            self._accepted_events += 1
            return ObservationReceipt(
                observation_id=observation.observation_id,
                event_id=event.event_id,
                sequence=observation.sequence,
                status=ObservationReceiptStatus.ADMITTED,
            )
        finally:
            self._pending_submissions -= 1
            if self._pending_submissions == 0:
                self._submissions_settled.set()

    async def submit(self, event: WorldEvent) -> ObservationReceipt:
        """Compatibility alias for environment adapters' admission callback."""

        return await self.admit_observation(event)

    def recent_observations(self) -> tuple[Observation, ...]:
        """Return the bounded, noncanonical window of recent admissions."""

        return self._observation_window.snapshot()

    async def cognition_step(
        self, receipt: ObservationReceipt
    ) -> CognitionDecision | CognitionTrigger:
        """Explicitly gate one admitted observation before routing cognition.

        This step is intentionally separate from admission. A negative gate
        outcome is complete and has no actor, router, Core, model, tool, or
        action side effects. A positive outcome submits the existing explicit
        reactive route to the character-wide actor; the router remains
        responsible for ConversationCore work.
        """

        if type(receipt) is not ObservationReceipt:
            raise TypeError("receipt must be an ObservationReceipt")
        async with self._lifecycle_lock:
            if self._state is not RuntimeState.RUNNING:
                raise RuntimeNotRunning(f"runtime is {self._state.value}")
            observation = self._observation_for_receipt(receipt)
            decision = self._cognition_gate.decide((observation,))
            self._cognition_decisions += 1
            if decision is NO_COGNITION:
                return decision
            if type(decision) is not CognitionTrigger or decision.observation_ids != (
                observation.observation_id,
            ):
                raise RuntimeFailed("cognition gate returned an invalid single-observation trigger")

            if observation.event.kind != "direct_message":
                mind_executor = self._mind_executor
                if mind_executor is None:
                    raise MindCompositionUnavailable(
                        "ambient THINK requires the existing MIND cognition composition"
                    )

                async def execute_ambient(
                    episode: SemanticEpisode, cancellation: SemanticCancellationToken
                ) -> object:
                    del episode
                    return await mind_executor.execute(decision, cancellation)

                request = self._semantic_actor.create_request(
                    decision.trigger_id,
                    source_kind=SemanticSourceKind.EXTERNAL,
                    priority=SemanticPriority.NON_USER,
                    executor=execute_ambient,
                )
                await self._semantic_actor.submit(request)
                self._cognition_triggers += 1
                return decision

            conversation_executor = self._conversation_executor
            if self._event_router is None:
                raise ReactiveStepUnavailable("runtime has no explicit reactive response route")
            adapter = self._environments.get(observation.event.source.environment)
            if adapter is None:
                raise RuntimeFailed(
                    f"no registered environment for {observation.event.source.environment!r}"
                )

            async def execute(
                episode: SemanticEpisode, cancellation: SemanticCancellationToken
            ) -> object:
                del episode
                try:
                    return await conversation_executor.execute(
                        observation, adapter.execute, cancellation
                    )
                except Exception as error:
                    self._route_failure = error
                    raise

            actor_presence = _actor_conversation_presence(self._presence)
            if actor_presence is not None:
                await actor_presence.begin_actor_user()

            async def execute_actor_user(
                episode: SemanticEpisode, cancellation: SemanticCancellationToken
            ) -> object:
                return await execute(episode, cancellation)

            try:
                request = self._semantic_actor.create_request(
                    decision.trigger_id,
                    source_kind=SemanticSourceKind.USER,
                    priority=SemanticPriority.USER,
                    executor=execute_actor_user,
                )
                admission = await self._semantic_actor.submit(request)
            except BaseException:
                if actor_presence is not None:
                    await actor_presence.end_actor_user()
                raise
            if (
                admission.status is not SemanticAdmissionStatus.ACCEPTED
                and actor_presence is not None
            ):
                await actor_presence.end_actor_user()
            elif (
                admission.status is SemanticAdmissionStatus.ACCEPTED and actor_presence is not None
            ):
                self._track_user_exclusion(admission, actor_presence)
            self._cognition_triggers += 1
            self._reactive_steps += 1
        await asyncio.sleep(0)

        return decision

    async def reactive_step(self, receipt: ObservationReceipt) -> None:
        """Compatibility alias for the explicit gate-and-react step."""

        await self.cognition_step(receipt)

    def _observation_for_receipt(self, receipt: ObservationReceipt) -> Observation:
        observation = self._observation_window.get(receipt.observation_id)
        if (
            observation is None
            or observation.event.event_id != receipt.event_id
            or observation.sequence != receipt.sequence
            or receipt.status is not ObservationReceiptStatus.ADMITTED
        ):
            raise ObservationUnavailable(
                f"observation receipt is unavailable: {receipt.observation_id}"
            )
        return observation

    async def submit_user(self, text: str, *, submission_id: str | None = None) -> str:
        """Admit one normal CLI user turn through the character-wide actor.

        ``submission_id`` is an opaque runtime-owned identity hook for replay
        fencing. If omitted, every call is a new submission, including when
        its text is identical to a previous call.
        """

        async with self._lifecycle_lock:
            if self._state is not RuntimeState.RUNNING:
                raise RuntimeNotRunning(f"runtime is {self._state.value}")
            presence = self._presence
        if presence is None:
            raise RuntimeNotRunning("runtime has no local presence component")
        actor_presence = _actor_conversation_presence(presence)
        if actor_presence is None:
            # Compatibility for external RuntimePresence implementations that
            # do not expose a Core session. The canonical PersistentPresence
            # composition below always takes the actor-owned branch.
            return await presence.submit_user(text)
        if not text.strip():
            raise ValueError("user input must be non-empty")
        if submission_id is None:
            submission_id = str(uuid4())
        if not submission_id.strip():
            raise ValueError("submission_id must be non-empty")
        request_id = f"cli:{submission_id.strip()}"
        await actor_presence.begin_actor_user()

        async def execute(
            episode: SemanticEpisode, cancellation: SemanticCancellationToken
        ) -> object:
            del episode
            return await actor_presence.execute_actor_user(
                text.strip(), self._conversation_executor, cancellation
            )

        try:
            request = self._semantic_actor.create_request(
                request_id,
                source_kind=SemanticSourceKind.USER,
                priority=SemanticPriority.USER,
                executor=execute,
            )
            admission = await self._semantic_actor.submit(request)
        except BaseException:
            await actor_presence.end_actor_user()
            raise
        if admission.status is not SemanticAdmissionStatus.ACCEPTED:
            await actor_presence.end_actor_user()
            settlement = await admission.wait()
            return settlement.status.value
        try:
            settlement = await admission.wait()
            return settlement.status.value
        finally:
            await asyncio.shield(self._release_user_exclusion(admission, actor_presence))

    def _track_user_exclusion(
        self, admission: SemanticAdmission, presence: _ActorConversationPresence
    ) -> None:
        task = asyncio.create_task(
            self._release_user_exclusion(admission, presence),
            name=f"lilavel-user-exclusion-{admission.sequence}",
        )
        self._user_exclusion_tasks.add(task)
        task.add_done_callback(self._user_exclusion_tasks.discard)

    @staticmethod
    async def _release_user_exclusion(
        admission: SemanticAdmission, presence: _ActorConversationPresence
    ) -> None:
        try:
            await admission.wait()
        finally:
            await presence.end_actor_user()

    async def stop(self) -> None:
        shutdown_task: asyncio.Task[None] | None = None
        while True:
            wait_for_start = False
            failed_supervisor: asyncio.Task[None] | None = None
            async with self._lifecycle_lock:
                if self._state is RuntimeState.NEW:
                    self._state = RuntimeState.STOPPED
                    self._admission_closed.set()
                    stop_components = True
                    shutdown_task = None
                else:
                    stop_components = False
                if not stop_components and self._state is RuntimeState.STOPPED:
                    return
                if not stop_components and self._state is RuntimeState.FAILED:
                    failed_supervisor = self._supervisor
                    shutdown_task = None
                if not stop_components and self._state is RuntimeState.STARTING:
                    wait_for_start = True
                    shutdown_task = None
                elif not stop_components and self._state is RuntimeState.STOPPING:
                    shutdown_task = self._shutdown_task
                elif not stop_components:
                    self._state = RuntimeState.STOPPING
                    self._admission_closed.set()
                    supervisor = self._supervisor
                    if supervisor is None:
                        self._state = RuntimeState.FAILED
                        raise RuntimeFailed("runtime supervisor is missing")
                    shutdown_task = asyncio.create_task(
                        self._settle_shutdown(supervisor), name="lilavel-runtime-shutdown"
                    )
                    self._shutdown_task = shutdown_task
            if stop_components:
                if self._temporal_host is not None:
                    await self._temporal_host.stop()
                await self._semantic_actor.stop()
                return
            if wait_for_start:
                await self._started.wait()
                continue
            if failed_supervisor is not None:
                if not failed_supervisor.done():
                    await asyncio.shield(failed_supervisor)
                raise RuntimeFailed("runtime is failed") from self._failure
            break
        if shutdown_task is None:
            self._state = RuntimeState.FAILED
            raise RuntimeFailed("runtime shutdown task is missing")
        await asyncio.shield(shutdown_task)

    async def _settle_shutdown(self, supervisor: asyncio.Task[None]) -> None:
        router_error: BaseException | None = None
        try:
            async with asyncio.timeout(self._shutdown_timeout):
                if self._temporal_host is not None:
                    try:
                        await self._temporal_host.stop()
                    except BaseException as error:
                        router_error = error
                for task in self._adapter_tasks:
                    task.cancel()
                if self._adapter_tasks:
                    await asyncio.gather(*self._adapter_tasks, return_exceptions=True)
                await self._submissions_settled.wait()
                await self._queue.join()
                ingress_task = self._ingress_task
                if ingress_task is not None and not ingress_task.done():
                    ingress_task.cancel()
                    await asyncio.gather(ingress_task, return_exceptions=True)
                try:
                    await self._semantic_actor.stop()
                except BaseException as error:
                    if router_error is None:
                        router_error = error
                if (
                    self._semantic_actor.state is SemanticActorState.POISONED
                    and router_error is None
                ):
                    router_error = self._semantic_actor.failure or RuntimeFailed(
                        "semantic actor poisoned during shutdown"
                    )
                if self._user_exclusion_tasks:
                    await asyncio.gather(*self._user_exclusion_tasks, return_exceptions=True)
                    self._user_exclusion_tasks.clear()
                if self._event_router is not None:
                    try:
                        await self._event_router.close()
                    except BaseException as error:
                        router_error = error
                if self._presence is not None:
                    try:
                        await self._presence.stop()
                    except BaseException as error:
                        if router_error is None:
                            router_error = error
                self._stop_requested.set()
                await supervisor
        except TimeoutError as error:
            self._state = RuntimeState.FAILED
            self._failure = error
            self._admission_closed.set()
            supervisor.cancel()
            raise RuntimeShutdownTimeout(
                "runtime tasks did not settle before shutdown deadline"
            ) from error

        if router_error is not None:
            if self._failure is None:
                self._failure = router_error
            self._state = RuntimeState.FAILED
            if isinstance(router_error, Exception):
                raise router_error
            raise RuntimeFailed("conversation router shutdown failed") from router_error

        if self.health().state is RuntimeState.FAILED:
            raise RuntimeFailed("runtime failed during shutdown") from self._failure

    def health(self) -> LilavelRuntimeHealth:
        return LilavelRuntimeHealth(
            state=self._state,
            healthy=(
                self._state is RuntimeState.RUNNING
                and self._semantic_actor.state is SemanticActorState.RUNNING
                and (
                    self._temporal_host is None
                    or self._temporal_host.state is TemporalHostState.RUNNING
                )
            ),
            environments=len(self._environments),
            tools=len(self._tools),
            queue_size=self._queue.qsize(),
            queue_capacity=self._queue.maxsize,
            accepted_events=self._accepted_events,
            processed_events=self._processed_events,
            wake_decisions=self._wake_decisions,
            cognition_decisions=self._cognition_decisions,
            cognition_triggers=self._cognition_triggers,
            reactive_steps=self._reactive_steps,
            observation_window_size=len(self._observation_window),
            failure_code=type(self.failure).__name__ if self.failure is not None else None,
            semantic_actor_state=self._semantic_actor.state,
        )

    async def __aenter__(self) -> LilavelRuntime:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc_value, traceback
        await self.stop()

    def _ensure_registration_open(self) -> None:
        if self._state is not RuntimeState.NEW:
            raise RuntimeRegistrationClosed("registration closes when startup begins")

    async def _supervise(self) -> None:
        try:
            await self._semantic_actor.start()
            if self._presence is not None:
                await self._presence.start()
            async with asyncio.TaskGroup() as group:
                group.create_task(
                    self._watch_semantic_actor(), name="lilavel-runtime-semantic-supervisor"
                )
                self._ingress_task = group.create_task(
                    self._consume_events(), name="lilavel-runtime-ingress"
                )
                if self._presence is not None:
                    group.create_task(
                        self._run_presence(self._presence), name="lilavel-runtime-presence"
                    )
                self._adapter_tasks = tuple(
                    group.create_task(
                        self._run_adapter(adapter),
                        name=f"lilavel-environment-{environment_id}",
                    )
                    for environment_id, adapter in self._environments.items()
                )
                self._state = RuntimeState.RUNNING
                if self._temporal_host is not None:
                    await self._temporal_host.start()
                self._started.set()
                await self._stop_requested.wait()
        except BaseExceptionGroup as error:
            if self._failure is None:
                self._failure = error
            self._state = RuntimeState.FAILED
            self._admission_closed.set()
            self._started.set()
        except asyncio.CancelledError:
            if self._state is not RuntimeState.FAILED:
                self._state = RuntimeState.FAILED
            self._admission_closed.set()
            raise
        except BaseException as error:
            if self._failure is None:
                self._failure = error
            self._state = RuntimeState.FAILED
            self._admission_closed.set()
            self._started.set()
        else:
            if self.health().state is RuntimeState.STOPPING:
                if self._semantic_actor.state is SemanticActorState.POISONED:
                    self._state = RuntimeState.FAILED
                    if self._failure is None:
                        self._failure = self._semantic_actor.failure
                else:
                    self._state = RuntimeState.STOPPED
        finally:
            if self._temporal_host is not None and self._temporal_host.state.value not in {
                "new",
                "stopped",
            }:
                with suppress(BaseException):
                    await self._temporal_host.stop()
            if self._state is RuntimeState.FAILED:
                with suppress(BaseException):
                    await self._semantic_actor.stop()
            if self._event_router is not None:
                try:
                    await self._event_router.close()
                except BaseException as error:
                    if self._failure is None:
                        self._failure = error
                        self._state = RuntimeState.FAILED
            if self._state is RuntimeState.FAILED and self._presence is not None:
                with suppress(BaseException):
                    await self._presence.stop()

    async def _watch_semantic_actor(self) -> None:
        """Propagate actor poison through the runtime supervisor."""

        failure_task = asyncio.create_task(
            self._semantic_actor.wait_for_failure(),
            name="lilavel-runtime-semantic-failure-wait",
        )
        stop_task = asyncio.create_task(
            self._stop_requested.wait(), name="lilavel-runtime-semantic-stop-wait"
        )
        try:
            done, _ = await asyncio.wait(
                (failure_task, stop_task), return_when=asyncio.FIRST_COMPLETED
            )
            if failure_task not in done:
                return
            failure = failure_task.result()
            if self._state in {
                RuntimeState.STOPPING,
                RuntimeState.STOPPED,
                RuntimeState.FAILED,
            }:
                return
            self._failure = failure
            self._state = RuntimeState.FAILED
            self._admission_closed.set()
            raise RuntimeFailed("semantic actor poisoned") from failure
        finally:
            for task in (failure_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(failure_task, stop_task, return_exceptions=True)

    async def _run_adapter(self, adapter: EnvironmentAdapter) -> None:
        await adapter.run(self.submit)
        if self._state is RuntimeState.RUNNING:
            raise RuntimeFailed(f"environment {adapter.environment_id!r} stopped unexpectedly")

    async def _run_presence(self, presence: RuntimePresence) -> None:
        await presence.wait()
        if self._state is RuntimeState.RUNNING:
            raise RuntimeFailed("local presence stopped unexpectedly")

    async def _consume_events(self) -> None:
        while True:
            await self._queue.get()
            try:
                self._processed_events += 1
            finally:
                self._queue.task_done()
