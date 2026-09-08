"""Minimal persistent-agent process lifecycle and bounded event ingress."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum

from .contracts import (
    EnvironmentAdapter,
    EventRouter,
    NeverWakePolicy,
    ToolSpec,
    WakePolicy,
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
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class _StopIngress:
    pass


_STOP_INGRESS = _StopIngress()


class LilavelRuntime:
    """Top-level owner of Lilavel's process lifecycle.

    The kernel classifies observations and, when configured, owns their routing
    to a conversational subsystem and back to the source environment.
    """

    def __init__(
        self,
        *,
        event_queue_size: int = 128,
        wake_policy: WakePolicy | None = None,
        event_router: EventRouter | None = None,
        shutdown_timeout: float = 5.0,
    ) -> None:
        if event_queue_size <= 0:
            raise ValueError("event_queue_size must be positive")
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be positive")
        self._state = RuntimeState.NEW
        self._queue: asyncio.Queue[WorldEvent | _StopIngress] = asyncio.Queue(
            maxsize=event_queue_size
        )
        self._wake_policy = wake_policy or NeverWakePolicy()
        self._event_router = event_router
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
        self._adapter_tasks: tuple[asyncio.Task[None], ...] = ()
        self._route_tasks: set[asyncio.Task[None]] = set()
        self._route_failure: BaseException | None = None
        self._failure: BaseException | None = None
        self._pending_submissions = 0
        self._accepted_events = 0
        self._processed_events = 0
        self._wake_decisions = 0

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def active_route_count(self) -> int:
        return len(self._route_tasks)

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

    async def submit(self, event: WorldEvent) -> None:
        """Admit one observation, waiting when the bounded queue is full."""

        async with self._lifecycle_lock:
            if self._state is not RuntimeState.RUNNING:
                raise RuntimeNotRunning(f"runtime is {self._state.value}")
            self._pending_submissions += 1
            self._submissions_settled.clear()

        try:
            async with asyncio.TaskGroup() as group:
                put_task = group.create_task(self._queue.put(event))
                close_task = group.create_task(self._admission_closed.wait())
                done, pending = await asyncio.wait(
                    (put_task, close_task), return_when=asyncio.FIRST_COMPLETED
                )
                admitted = put_task in done
                for task in pending:
                    task.cancel()
            if not admitted:
                raise RuntimeNotRunning("runtime admission closed while event was waiting")
            self._accepted_events += 1
        finally:
            self._pending_submissions -= 1
            if self._pending_submissions == 0:
                self._submissions_settled.set()

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
                await self._queue.put(_STOP_INGRESS)
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
            async with asyncio.TaskGroup() as group:
                group.create_task(self._consume_events(group), name="lilavel-runtime-ingress")
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

    async def _run_adapter(self, adapter: EnvironmentAdapter) -> None:
        await adapter.run(self.submit)
        if self._state is RuntimeState.RUNNING:
            raise RuntimeFailed(f"environment {adapter.environment_id!r} stopped unexpectedly")

    async def _consume_events(self, group: asyncio.TaskGroup) -> None:
        while True:
            item = await self._queue.get()
            try:
                if isinstance(item, _StopIngress):
                    return
                decision = await self._wake_policy.decide(item)
                self._processed_events += 1
                if decision.wake:
                    self._wake_decisions += 1
                    if self._event_router is None:
                        continue
                    adapter = self._environments.get(item.source.environment)
                    if adapter is None:
                        raise RuntimeFailed(
                            f"no registered environment for {item.source.environment!r}"
                        )
                    task = group.create_task(
                        self._event_router.route(item, adapter.execute),
                        name=f"lilavel-route-{item.event_id}",
                    )
                    self._route_tasks.add(task)
                    task.add_done_callback(self._route_done)
            finally:
                self._queue.task_done()

    def _route_done(self, task: asyncio.Task[None]) -> None:
        self._route_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._route_failure = error
