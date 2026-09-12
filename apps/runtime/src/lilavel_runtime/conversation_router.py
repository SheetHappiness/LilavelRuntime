"""Core conversation routing owned by the persistent Lilavel runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from threading import Event, Thread
from typing import cast
from uuid import uuid4

from lilavel_contracts import ToolResultStatus
from lilavel_core import (
    ConversationCancelled,
    ConversationCompleted,
    ConversationCore,
    ConversationEvent,
    ConversationRun,
    ConversationRuntime,
    ConversationTextDelta,
    ModelRuntime,
)
from lilavel_core.production_cognition import create_conversation

from .contracts import ActionExecutor, Observation, ToolCall, ToolResult

PRESENTATION_OPEN = "conversation.presentation.open"
PRESENTATION_BIND = "conversation.presentation.bind"
PRESENTATION_WATCH = "conversation.presentation.watch"
PRESENTATION_DELTA = "conversation.presentation.delta"
PRESENTATION_COMPLETE = "conversation.presentation.complete"
PRESENTATION_INTERRUPTED = "conversation.presentation.interrupted"
PRESENTATION_FAILED = "conversation.presentation.failed"
PRESENTATION_ABORT = "conversation.presentation.abort"


RuntimeFactory = Callable[[], ConversationRuntime]
RouteRuntimeFactory = Callable[[tuple[str, str]], ConversationRuntime]
CoreFactory = Callable[[ConversationRuntime], ConversationCore]
SessionConfigurator = Callable[[ConversationRuntime, ConversationCore, tuple[str, str]], None]


@dataclass(slots=True)
class _ConversationSession:
    runtime: ConversationRuntime
    core: ConversationCore
    startup_task: asyncio.Task[None] | None = None


class _BridgeEnd:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error


class _ConversationEventBridge:
    """Consume the blocking Core iterator on one runtime-owned thread."""

    def __init__(self, run: ConversationRun, loop: asyncio.AbstractEventLoop) -> None:
        self._run = run
        self._loop = loop
        self.queue: asyncio.Queue[ConversationEvent | _BridgeEnd] = asyncio.Queue()
        self._thread = Thread(
            target=self._consume,
            name=f"lilavel-runtime-conversation-{run.run_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float) -> bool:
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _consume(self) -> None:
        error: BaseException | None = None
        try:
            for event in self._run.events():
                self._loop.call_soon_threadsafe(self.queue.put_nowait, event)
        except BaseException as exception:
            error = exception
        finally:
            self._loop.call_soon_threadsafe(self.queue.put_nowait, _BridgeEnd(error))


async def _await_blocking[BlockingResult](
    function: Callable[[], BlockingResult],
) -> BlockingResult:
    """Await one bounded blocking operation without the loop default executor.

    The router already owns daemon threads for Core and provider boundaries.  A
    small explicit bridge keeps shutdown independent from the event loop's
    default executor and lets a cancelled await leave only the bounded
    operation running in its own daemon thread.
    """

    completed = Event()
    outcome: list[tuple[BlockingResult | None, BaseException | None]] = []

    def run() -> None:
        try:
            result = function()
        except BaseException as error:
            outcome.append((None, error))
        else:
            outcome.append((result, None))
        finally:
            completed.set()

    Thread(target=run, name="lilavel-runtime-blocking-bridge", daemon=True).start()
    while not completed.is_set():
        await asyncio.sleep(0)
    result, error = outcome[0]
    if error is not None:
        raise error
    return cast(BlockingResult, result)


class CoreConversationRouter:
    """Map provider-neutral conversation subjects to Core-owned sessions."""

    def __init__(
        self,
        *,
        runtime_factory: RuntimeFactory = ModelRuntime,
        route_runtime_factory: RouteRuntimeFactory | None = None,
        core_factory: CoreFactory = create_conversation,
        session_configurator: SessionConfigurator | None = None,
        close_timeout_s: float = 15.0,
    ) -> None:
        if close_timeout_s <= 0:
            raise ValueError("close_timeout_s must be positive")
        self._runtime_factory = runtime_factory
        self._route_runtime_factory = route_runtime_factory
        self._core_factory = core_factory
        self._session_configurator = session_configurator
        self._close_timeout_s = close_timeout_s
        self._sessions: dict[tuple[str, str], _ConversationSession] = {}
        self._session_lock = asyncio.Lock()
        self._admission_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._active_runs: set[ConversationRun] = set()
        self._closing = False
        self._closed = False

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def conversation_key(self, environment: str, subject: str) -> str | None:
        session = self._sessions.get((environment, subject))
        return None if session is None else session.core.scope_id

    def history(self, environment: str, subject: str) -> tuple[object, ...] | None:
        session = self._sessions.get((environment, subject))
        return None if session is None else session.core.history

    async def route(self, observation: Observation, execute: ActionExecutor) -> None:
        if self._closing:
            return
        event = observation.event
        subject = event.source.subject
        text = event.payload.get("text")
        if subject is None or not isinstance(text, str) or not text:
            raise ValueError("conversation events require a non-empty subject and text")

        route_key = (event.source.environment, subject)
        run: ConversationRun | None = None
        bridge: _ConversationEventBridge | None = None
        event_task: asyncio.Task[ConversationEvent | _BridgeEnd] | None = None
        watch_task: asyncio.Task[ToolResult] | None = None
        presentation_open = False
        presentation_bound = False
        try:
            lock = self._admission_locks.setdefault(route_key, asyncio.Lock())
            async with lock:
                await self._execute(execute, PRESENTATION_OPEN, event.event_id)
                presentation_open = True
                session = await self._session_for(route_key)
                run = session.core.start_turn(text, supersede=True)
                self._active_runs.add(run)
                await self._execute(
                    execute,
                    PRESENTATION_BIND,
                    event.event_id,
                    run_id=run.run_id,
                )
                presentation_bound = True

            bridge = _ConversationEventBridge(run, asyncio.get_running_loop())
            bridge.start()
            watch_task = asyncio.create_task(
                self._watch(
                    execute,
                    self._call(PRESENTATION_WATCH, event.event_id, run_id=run.run_id),
                ),
                name=f"lilavel-presentation-watch-{run.run_id}",
            )
            while True:
                event_task = asyncio.create_task(
                    bridge.queue.get(), name=f"lilavel-core-event-{run.run_id}"
                )
                done, _ = await asyncio.wait(
                    (event_task, watch_task), return_when=asyncio.FIRST_COMPLETED
                )
                if watch_task in done:
                    result = watch_task.result()
                    if result.status is not ToolResultStatus.OK:
                        raise RuntimeError(
                            result.reason_code or "presentation watcher stopped unexpectedly"
                        )
                    raise RuntimeError("presentation watcher stopped unexpectedly")
                item = event_task.result()
                if isinstance(item, _BridgeEnd):
                    if item.error is not None:
                        raise item.error
                    await self._execute(
                        execute,
                        PRESENTATION_FAILED,
                        event.event_id,
                        run_id=run.run_id,
                        text=run.text,
                    )
                    return
                if isinstance(item, ConversationTextDelta):
                    await self._execute(
                        execute,
                        PRESENTATION_DELTA,
                        event.event_id,
                        run_id=run.run_id,
                        text=item.delta,
                    )
                elif isinstance(item, ConversationCompleted):
                    await self._execute(
                        execute,
                        PRESENTATION_COMPLETE,
                        event.event_id,
                        run_id=run.run_id,
                        text=item.text,
                    )
                    return
                elif isinstance(item, ConversationCancelled):
                    await self._execute(
                        execute,
                        PRESENTATION_INTERRUPTED,
                        event.event_id,
                        run_id=run.run_id,
                        text=item.text,
                        reason=item.reason,
                    )
                    return
                else:
                    await self._execute(
                        execute,
                        PRESENTATION_FAILED,
                        event.event_id,
                        run_id=run.run_id,
                        text=item.text,
                    )
                    return
        except asyncio.CancelledError:
            if run is not None and not run.settled:
                run.cancel()
            raise
        except BaseException:
            if run is not None and not run.settled:
                run.cancel()
            raise
        finally:
            if event_task is not None and not event_task.done():
                event_task.cancel()
            if watch_task is not None and not watch_task.done():
                watch_task.cancel()
            if event_task is not None:
                await asyncio.gather(event_task, return_exceptions=True)
            if watch_task is not None:
                await asyncio.gather(watch_task, return_exceptions=True)
            if bridge is not None:
                await _await_blocking(lambda: bridge.join(self._close_timeout_s))
            if run is not None:
                self._active_runs.discard(run)
            if presentation_open and not presentation_bound:
                await self._best_effort(execute, PRESENTATION_FAILED, event.event_id, text="")
            elif presentation_bound:
                await self._best_effort(
                    execute,
                    PRESENTATION_ABORT,
                    event.event_id,
                    run_id=run.run_id if run is not None else "unknown",
                )

    async def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        for run in tuple(self._active_runs):
            if not run.settled:
                run.cancel()
        if self._active_runs:
            return
        errors: list[BaseException] = []
        for session in tuple(self._sessions.values()):
            if session.startup_task is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(session.startup_task), self._close_timeout_s
                    )
                except BaseException as error:
                    errors.append(error)
            shutdown = getattr(session.runtime, "shutdown", None)
            if callable(shutdown):
                try:
                    await _await_blocking(cast(Callable[[], None], shutdown))
                except BaseException as error:
                    errors.append(error)
        self._closed = True
        if errors:
            error = errors[0]
            if isinstance(error, Exception):
                raise error
            raise RuntimeError("conversation router shutdown failed") from error

    async def _session_for(self, key: tuple[str, str]) -> _ConversationSession:
        async with self._session_lock:
            existing = self._sessions.get(key)
            if existing is not None:
                await self._await_startup(key, existing)
                return existing
            runtime = (
                self._route_runtime_factory(key)
                if self._route_runtime_factory is not None
                else self._runtime_factory()
            )
            core = self._core_factory(runtime)
            if self._session_configurator is not None:
                self._session_configurator(runtime, core, key)
            session = _ConversationSession(runtime=runtime, core=core)
            self._sessions[key] = session
            start = getattr(runtime, "start", None)
            if callable(start):
                session.startup_task = asyncio.create_task(
                    _await_blocking(cast(Callable[[], None], start)),
                    name=f"lilavel-conversation-start-{key[1]}",
                )
                await self._await_startup(key, session)
            return session

    async def _await_startup(self, key: tuple[str, str], session: _ConversationSession) -> None:
        task = session.startup_task
        if task is None:
            return
        try:
            await asyncio.shield(task)
        except BaseException:
            if self._sessions.get(key) is session:
                del self._sessions[key]
            shutdown = getattr(session.runtime, "shutdown", None)
            if callable(shutdown):
                await _await_blocking(cast(Callable[[], None], shutdown))
            raise
        finally:
            if task.done():
                session.startup_task = None

    @staticmethod
    def _call(action: str, event_id: str, **arguments: object) -> ToolCall:
        return ToolCall(
            call_id=str(uuid4()),
            tool_name=action,
            arguments={"event_id": event_id, **arguments},
        )

    async def _execute(
        self, execute: ActionExecutor, action: str, event_id: str, **arguments: object
    ) -> ToolResult:
        result = await execute(self._call(action, event_id, **arguments))
        if result.status is not ToolResultStatus.OK:
            raise RuntimeError(f"environment action {action!r} failed: {result.reason_code}")
        return result

    @staticmethod
    async def _watch(execute: ActionExecutor, call: ToolCall) -> ToolResult:
        return await execute(call)

    async def _best_effort(
        self, execute: ActionExecutor, action: str, event_id: str, **arguments: object
    ) -> None:
        with suppress(BaseException):
            await self._execute(execute, action, event_id, **arguments)
