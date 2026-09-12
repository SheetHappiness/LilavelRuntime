"""Minimal persistent-agent process lifecycle and bounded event ingress."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from .contracts import (
    NO_COGNITION,
    CognitionDecision,
    CognitionGate,
    CognitionTrigger,
    DirectMessageCognitionGate,
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


class DuplicateEnvironment(LilavelRuntimeError):
    pass


class DuplicateTool(LilavelRuntimeError):
    pass


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
        self._cognition_gate = (
            DirectMessageCognitionGate() if cognition_gate is None else cognition_gate
        )
        self._presence = presence
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
        self._route_tasks: set[asyncio.Task[None]] = set()
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

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def active_route_count(self) -> int:
        return len(self._route_tasks)

    @property
    def observation_window(self) -> ObservationWindow:
        return self._observation_window

    @property
    def failure(self) -> BaseException | None:
        return self._failure or self._route_failure

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
        outcome is complete and has no router, Core, model, tool, or action
        side effects. A positive outcome schedules the existing explicit
        reactive route, which remains responsible for ConversationCore work.
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
            router = self._event_router
            if router is None:
                raise ReactiveStepUnavailable("runtime has no explicit reactive response route")
            adapter = self._environments.get(observation.event.source.environment)
            if adapter is None:
                raise RuntimeFailed(
                    f"no registered environment for {observation.event.source.environment!r}"
                )
            task = asyncio.create_task(
                router.route(observation, adapter.execute),
                name=f"lilavel-reactive-{observation.event.event_id}",
            )
            self._route_tasks.add(task)
            task.add_done_callback(self._route_done)
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

    async def submit_user(self, text: str) -> str:
        """Route local user priority through the one runtime-owned presence lane."""

        async with self._lifecycle_lock:
            if self._state is not RuntimeState.RUNNING:
                raise RuntimeNotRunning(f"runtime is {self._state.value}")
            presence = self._presence
        if presence is None:
            raise RuntimeNotRunning("runtime has no local presence component")
        return await presence.submit_user(text)

    async def stop(self) -> None:
        while True:
            wait_for_start = False
            async with self._lifecycle_lock:
                if self._state is RuntimeState.NEW:
                    self._state = RuntimeState.STOPPED
                    self._admission_closed.set()
                    return
                if self._state is RuntimeState.STOPPED:
                    return
                if self._state is RuntimeState.FAILED:
                    raise RuntimeFailed("runtime is failed") from self._failure
                if self._state is RuntimeState.STARTING:
                    wait_for_start = True
                    shutdown_task = None
                elif self._state is RuntimeState.STOPPING:
                    shutdown_task = self._shutdown_task
                else:
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
            if wait_for_start:
                await self._started.wait()
                continue
            break
        if shutdown_task is None:
            self._state = RuntimeState.FAILED
            raise RuntimeFailed("runtime shutdown task is missing")
        await asyncio.shield(shutdown_task)

    async def _settle_shutdown(self, supervisor: asyncio.Task[None]) -> None:
        router_error: BaseException | None = None
        try:
            async with asyncio.timeout(self._shutdown_timeout):
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
                route_tasks = tuple(self._route_tasks)
                for task in route_tasks:
                    task.cancel()
                if route_tasks:
                    await asyncio.gather(*route_tasks, return_exceptions=True)
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
            healthy=self._state is RuntimeState.RUNNING,
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
            if self._presence is not None:
                await self._presence.start()
            async with asyncio.TaskGroup() as group:
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
                self._started.set()
                await self._stop_requested.wait()
        except BaseExceptionGroup as error:
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
            self._failure = error
            self._state = RuntimeState.FAILED
            self._admission_closed.set()
            self._started.set()
        else:
            if self.health().state is RuntimeState.STOPPING:
                self._state = RuntimeState.STOPPED
        finally:
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

    def _route_done(self, task: asyncio.Task[None]) -> None:
        self._route_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._route_failure = error
