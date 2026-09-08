"""DM-only Discord ingress and Core-owned conversation orchestration."""

from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import nullcontext, suppress
from dataclasses import dataclass, field
from threading import Thread
from typing import Any, Final, cast
from uuid import uuid4

import discord
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

from .diagnostics import (
    DiscordDiagnostics,
    PresentationReason,
    PresentationStatus,
    attach_http_trace,
)
from .presenter import DEFAULT_EDIT_INTERVAL_S, ReplyPresenter
from .semantic import DEFAULT_SEMANTIC_LOOKAHEAD_S, DEFAULT_SEMANTIC_MAX_TAIL_CHARS
from .transport import DiscordMessageSink, TransportMetrics, validate_edit_interval

DISCORD_TOKEN_ENV: Final = "LILAVEL_DISCORD_BOT_TOKEN"
EDIT_INTERVAL_ENV: Final = "LILAVEL_DISCORD_EDIT_INTERVAL_S"
SEMANTIC_STREAMING_ENV: Final = "LILAVEL_DISCORD_SEMANTIC_STREAMING"
DEFAULT_CLOSE_TIMEOUT_S: Final = 15.0
DEFAULT_DEDUPE_CAPACITY: Final = 4096


class MissingDiscordToken(RuntimeError):
    """No supported Discord bot token was configured."""


def read_discord_token(environ: Mapping[str, str] | None = None) -> str | None:
    """Read only the namespaced token variable, without logging its value."""

    source = os.environ if environ is None else environ
    value = source.get(DISCORD_TOKEN_ENV)
    if value is None:
        return None
    token = value.strip()
    return token or None


def read_edit_interval_from_environment(environ: Mapping[str, str] | None = None) -> float:
    """Read the optional operator pacing override without changing library defaults."""

    source = os.environ if environ is None else environ
    raw_value = source.get(EDIT_INTERVAL_ENV)
    if raw_value is None:
        return DEFAULT_EDIT_INTERVAL_S
    try:
        value = float(raw_value.strip())
    except (TypeError, ValueError) as error:
        raise ValueError(f"{EDIT_INTERVAL_ENV} must be a finite, nonnegative number") from error
    try:
        validate_edit_interval(value)
    except ValueError as error:
        raise ValueError(f"{EDIT_INTERVAL_ENV} must be a finite, nonnegative number") from error
    return value


def read_semantic_streaming_from_environment(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Read the default-on semantic presentation switch.

    The explicit ``0`` value is the rollback/diagnostic seam; an absent
    variable keeps the normal edge behavior enabled.
    """

    source = os.environ if environ is None else environ
    raw_value = source.get(SEMANTIC_STREAMING_ENV)
    if raw_value is None:
        return True
    if raw_value.strip() == "0":
        return False
    if raw_value.strip() == "1":
        return True
    raise ValueError(f"{SEMANTIC_STREAMING_ENV} must be '0' or '1'")


def make_dm_intents() -> discord.Intents:
    """Request only the standard Direct Messages gateway intent."""

    intents = discord.Intents.none()
    # Discord calls this bit DIRECT_MESSAGES; discord.py exposes it as
    # ``dm_messages`` in its 2.7.1 Python API.
    intents.dm_messages = True
    return intents


def is_direct_message(message: Any) -> bool:
    return isinstance(getattr(message, "channel", None), discord.DMChannel)


@dataclass(frozen=True, slots=True)
class _OpaqueConversationIdentity:
    value: str = field(default_factory=lambda: str(uuid4()))


@dataclass(slots=True)
class _ConversationSession:
    identity: _OpaqueConversationIdentity
    runtime: ConversationRuntime
    core: ConversationCore
    startup_task: asyncio.Task[None] | None = None


class _MessageIdDeduplicator:
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("dedupe capacity must be positive")
        self._capacity = capacity
        self._seen: OrderedDict[str, None] = OrderedDict()

    def claim(self, message_id: object) -> bool:
        key = str(message_id)
        if key in self._seen:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = None
        if len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return True


class _BridgeEnd:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error


class _ConversationEventBridge:
    """Consume the blocking Core iterator on one owned thread."""

    def __init__(self, run: ConversationRun, loop: asyncio.AbstractEventLoop) -> None:
        self._run = run
        self._loop = loop
        self.queue: asyncio.Queue[ConversationEvent | _BridgeEnd] = asyncio.Queue()
        self._thread = Thread(
            target=self._consume,
            name=f"lilavel-discord-run-{run.run_id}",
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
                self._put(event)
        except BaseException as exception:
            error = exception
        finally:
            self._put(_BridgeEnd(error))

    def _put(self, item: ConversationEvent | _BridgeEnd) -> None:
        with suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self.queue.put_nowait, item)


RuntimeFactory = Callable[[], ConversationRuntime]
CoreFactory = Callable[[ConversationRuntime], ConversationCore]
MessageFilter = Callable[[Any], bool]


class DiscordTextEdge:
    """Own Discord lifecycle and map each DM channel to an opaque Core session."""

    def __init__(
        self,
        *,
        client: discord.Client | None = None,
        runtime_factory: RuntimeFactory = ModelRuntime,
        core_factory: CoreFactory = create_conversation,
        message_filter: MessageFilter = is_direct_message,
        token_provider: Callable[[], str | None] = read_discord_token,
        edit_interval_s: float = DEFAULT_EDIT_INTERVAL_S,
        dedupe_capacity: int = DEFAULT_DEDUPE_CAPACITY,
        close_timeout_s: float = DEFAULT_CLOSE_TIMEOUT_S,
        clock: Callable[[], float] = time.perf_counter,
        diagnostics: DiscordDiagnostics | None = None,
        semantic_streaming: bool = True,
        semantic_lookahead_s: float = DEFAULT_SEMANTIC_LOOKAHEAD_S,
        semantic_max_tail_chars: int = DEFAULT_SEMANTIC_MAX_TAIL_CHARS,
    ) -> None:
        if close_timeout_s <= 0:
            raise ValueError("close_timeout_s must be positive")
        validate_edit_interval(edit_interval_s)
        validate_edit_interval(semantic_lookahead_s)
        if isinstance(semantic_max_tail_chars, bool) or semantic_max_tail_chars <= 0:
            raise ValueError("semantic_max_tail_chars must be positive")
        if client is None:
            client_options: dict[str, Any] = {
                "intents": make_dm_intents(),
                "status": discord.Status.online,
            }
            if diagnostics is not None:
                client_options["http_trace"] = diagnostics.trace_config
            self._client = discord.Client(**client_options)
        else:
            self._client = client
            attach_http_trace(self._client, diagnostics)
        self._runtime_factory = runtime_factory
        self._core_factory = core_factory
        self._message_filter = message_filter
        self._token_provider = token_provider
        self._edit_interval_s = edit_interval_s
        self._close_timeout_s = close_timeout_s
        self._clock = clock
        self._diagnostics = diagnostics
        self._semantic_streaming = semantic_streaming
        self._semantic_lookahead_s = semantic_lookahead_s
        self._semantic_max_tail_chars = semantic_max_tail_chars
        self._dedupe = _MessageIdDeduplicator(dedupe_capacity)
        self._sessions: dict[str, _ConversationSession] = {}
        self._session_lock = asyncio.Lock()
        self._admission_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False
        self._closed = False
        self._register_handlers()

    @property
    def client(self) -> discord.Client:
        return self._client

    @property
    def active_task_count(self) -> int:
        return len(self._tasks)

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def conversation_key_for_channel(self, channel_id: object) -> str | None:
        session = self._sessions.get(str(channel_id))
        return None if session is None else session.identity.value

    async def start(self, token: str | None = None) -> None:
        """Connect and let discord.py own reconnect/resume handling."""

        resolved_token = token or self._token_provider()
        if not resolved_token:
            raise MissingDiscordToken(f"set {DISCORD_TOKEN_ENV} before starting the edge")
        try:
            await self._client.start(resolved_token, reconnect=True)
        finally:
            await self.close()

    async def handle_message(self, message: Any) -> None:
        """Admit one MESSAGE_CREATE candidate and schedule its owned response task."""

        if self._closing or not self._message_filter(message):
            return
        author = getattr(message, "author", None)
        if getattr(author, "bot", False):
            return
        user = self._client.user
        if user is not None and getattr(author, "id", None) == getattr(user, "id", None):
            return
        if not self._dedupe.claim(getattr(message, "id", "")):
            return
        task = asyncio.create_task(
            self._process_message(message),
            name=f"lilavel-discord-message-{getattr(message, 'id', 'unknown')}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    async def wait_idle(self, timeout: float = 5.0) -> None:
        """Test/diagnostic helper that waits for currently owned response tasks."""

        tasks = tuple(self._tasks)
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout)

    async def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        close_errors: list[BaseException] = []
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), self._close_timeout_s
                )
            except BaseException as error:
                close_errors.append(error)

        for session in tuple(self._sessions.values()):
            startup_task = session.startup_task
            if startup_task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(startup_task), self._close_timeout_s)
                except BaseException as error:
                    close_errors.append(error)
            try:
                await self._shutdown_runtime(session.runtime)
            except BaseException as error:
                close_errors.append(error)

        try:
            await self._client.close()
        except BaseException as error:
            close_errors.append(error)
        finally:
            self._closed = True
        if close_errors:
            raise RuntimeError("Discord edge shutdown did not complete cleanly") from close_errors[
                0
            ]

    def _register_handlers(self) -> None:
        async def on_message(message: discord.Message) -> None:
            await self.handle_message(message)

        self._client.event(on_message)

    async def _process_message(self, message: Any) -> None:
        channel = message.channel
        run: ConversationRun | None = None
        typing_context: Any | None = None
        try:
            channel_lock = self._admission_locks.setdefault(str(channel.id), asyncio.Lock())
            async with channel_lock:
                entered_context: Any = channel.typing()
                await entered_context.__aenter__()
                typing_context = entered_context
                session = await self._session_for_channel(channel)
                run = session.core.start_turn(message.content, supersede=True)
            await self._present_run(channel, run)
        except asyncio.CancelledError:
            if run is not None and not run.settled:
                run.cancel()
            raise
        except Exception:
            if run is None:
                await self._present_failure(channel)
            else:
                raise
        finally:
            if typing_context is not None:
                await typing_context.__aexit__(None, None, None)

    async def _session_for_channel(self, channel: Any) -> _ConversationSession:
        channel_id = str(channel.id)
        async with self._session_lock:
            existing = self._sessions.get(channel_id)
            if existing is not None:
                await self._await_startup(channel_id, existing)
                return existing
            runtime = self._runtime_factory()
            session = _ConversationSession(
                identity=_OpaqueConversationIdentity(),
                runtime=runtime,
                core=self._core_factory(runtime),
            )
            self._sessions[channel_id] = session
            start = getattr(runtime, "start", None)
            if callable(start):
                startup_task = asyncio.create_task(
                    asyncio.to_thread(cast(Callable[[], None], start)),
                    name=f"lilavel-discord-start-{channel_id}",
                )
                session.startup_task = startup_task
                startup_task.add_done_callback(self._observe_task)
                await self._await_startup(channel_id, session)
            return session

    async def _await_startup(self, channel_id: str, session: _ConversationSession) -> None:
        startup_task = session.startup_task
        if startup_task is None:
            return
        try:
            await asyncio.shield(startup_task)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._sessions.get(channel_id) is session:
                del self._sessions[channel_id]
            await self._shutdown_runtime(session.runtime)
            raise
        finally:
            if startup_task.done():
                session.startup_task = None

    async def _shutdown_runtime(self, runtime: ConversationRuntime) -> None:
        shutdown = getattr(runtime, "shutdown", None)
        if callable(shutdown):
            await asyncio.to_thread(shutdown)

    async def _present_run(self, channel: Any, run: ConversationRun) -> None:
        metrics = TransportMetrics()
        presenter = ReplyPresenter(
            DiscordMessageSink(
                channel,
                metrics=metrics,
                diagnostics=self._diagnostics,
                clock=self._clock,
            ),
            edit_interval_s=self._edit_interval_s,
            clock=self._clock,
            diagnostics=self._diagnostics,
            semantic_streaming=self._semantic_streaming,
            semantic_lookahead_s=self._semantic_lookahead_s,
            semantic_max_tail_chars=self._semantic_max_tail_chars,
        )
        bridge = _ConversationEventBridge(run, asyncio.get_running_loop())
        event_task: asyncio.Task[ConversationEvent | _BridgeEnd] | None = None
        presenter_error_task: asyncio.Task[BaseException] | None = None
        presentation_status: PresentationStatus = "failed"
        presentation_reason: PresentationReason | None = "missing_terminal"
        diagnostic_context = (
            self._diagnostics.presentation_context(run.run_id)
            if self._diagnostics is not None
            else nullcontext()
        )
        with diagnostic_context:
            try:
                presenter.start()
                bridge.start()
                presenter_error_task = asyncio.create_task(
                    presenter.wait_for_error(),
                    name="lilavel-discord-reply-presenter-error",
                )
                while True:
                    event_task = asyncio.create_task(
                        bridge.queue.get(),
                        name="lilavel-discord-event-bridge-get",
                    )
                    done, _ = await asyncio.wait(
                        (event_task, presenter_error_task),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if presenter_error_task in done:
                        raise presenter_error_task.result()
                    item = event_task.result()
                    if isinstance(item, _BridgeEnd):
                        if item.error is not None or not presenter.finalized:
                            presentation_reason = (
                                "bridge_error" if item.error is not None else "missing_terminal"
                            )
                            await presenter.failed(run.text)
                        presentation_status = "failed"
                        return
                    if isinstance(item, ConversationTextDelta):
                        presenter.append_delta(item.delta)
                    elif isinstance(item, ConversationCompleted):
                        await presenter.complete(item.text)
                        presentation_status = "completed"
                        presentation_reason = None
                        return
                    elif isinstance(item, ConversationCancelled):
                        await presenter.interrupted(item.text)
                        presentation_status = "interrupted"
                        presentation_reason = item.reason
                        return
                    else:
                        await presenter.failed(item.text)
                        presentation_status = "failed"
                        presentation_reason = "core_failed"
                        return
            except asyncio.CancelledError:
                presentation_status = "interrupted"
                presentation_reason = "edge_cancelled"
                if not run.settled:
                    run.cancel()
                raise
            except BaseException:
                presentation_status = "failed"
                presentation_reason = "presenter_error"
                if not run.settled:
                    run.cancel()
                raise
            finally:
                if event_task is not None and not event_task.done():
                    event_task.cancel()
                if presenter_error_task is not None and not presenter_error_task.done():
                    presenter_error_task.cancel()
                if event_task is not None:
                    await asyncio.gather(event_task, return_exceptions=True)
                if presenter_error_task is not None:
                    await asyncio.gather(presenter_error_task, return_exceptions=True)
                await presenter.shutdown()
                await asyncio.to_thread(bridge.join, self._close_timeout_s)
                if self._diagnostics is not None:
                    self._diagnostics.presentation_outcome(
                        status=presentation_status,
                        reason=presentation_reason,
                        flush_count=presenter.flush_count,
                        terminal_flush_count=presenter.terminal_flush_count,
                    )

    async def _present_failure(self, channel: Any) -> None:
        presenter = ReplyPresenter(
            DiscordMessageSink(channel, clock=self._clock, diagnostics=self._diagnostics),
            edit_interval_s=self._edit_interval_s,
            clock=self._clock,
            diagnostics=self._diagnostics,
            semantic_streaming=self._semantic_streaming,
            semantic_lookahead_s=self._semantic_lookahead_s,
            semantic_max_tail_chars=self._semantic_max_tail_chars,
        )
        await presenter.failed("")

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        self._observe_task(task)

    @staticmethod
    def _observe_task(task: asyncio.Task[Any]) -> None:
        with suppress(BaseException):
            task.exception()
