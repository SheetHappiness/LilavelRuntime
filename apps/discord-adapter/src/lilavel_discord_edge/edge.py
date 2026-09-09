"""DM-only Discord environment wired through the persistent runtime."""

from __future__ import annotations

import asyncio
import os
import time
import weakref
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Any, Final, cast
from uuid import uuid4

import discord
from lilavel_contracts import ToolResultStatus
from lilavel_core import (
    ApplicationToolSessionFactory,
    ConversationCore,
    ConversationRuntime,
    ModelRuntime,
    ModelRuntimeV3,
)
from lilavel_core.production_cognition import create_conversation
from lilavel_runtime import (
    PRESENTATION_ABORT,
    PRESENTATION_BIND,
    PRESENTATION_COMPLETE,
    PRESENTATION_DELTA,
    PRESENTATION_FAILED,
    PRESENTATION_INTERRUPTED,
    PRESENTATION_OPEN,
    PRESENTATION_WATCH,
    CoreConversationRouter,
    DirectMessageWakePolicy,
    EventSource,
    EventSubmitter,
    LilavelRuntime,
    RuntimeState,
    ToolCall,
    ToolResult,
    WorldEvent,
)

from .diagnostics import (
    DiscordDiagnostics,
    PresentationReason,
    PresentationStatus,
    attach_http_trace,
)
from .presenter import DEFAULT_EDIT_INTERVAL_S, ReplyPresenter
from .semantic import DEFAULT_SEMANTIC_LOOKAHEAD_S, DEFAULT_SEMANTIC_MAX_TAIL_CHARS
from .tool import DiscordToolSessionFactory
from .transport import DiscordMessageSink, TransportMetrics, validate_edit_interval

DISCORD_TOKEN_ENV: Final = "LILAVEL_DISCORD_BOT_TOKEN"
EDIT_INTERVAL_ENV: Final = "LILAVEL_DISCORD_EDIT_INTERVAL_S"
SEMANTIC_STREAMING_ENV: Final = "LILAVEL_DISCORD_SEMANTIC_STREAMING"
DEFAULT_CLOSE_TIMEOUT_S: Final = 15.0
DEFAULT_DEDUPE_CAPACITY: Final = 4096
DISCORD_ENVIRONMENT_ID: Final = "discord"


class MissingDiscordToken(RuntimeError):
    """No supported Discord bot token was configured."""


def read_discord_token(environ: Mapping[str, str] | None = None) -> str | None:
    source = os.environ if environ is None else environ
    value = source.get(DISCORD_TOKEN_ENV)
    if value is None:
        return None
    token = value.strip()
    return token or None


def read_edit_interval_from_environment(environ: Mapping[str, str] | None = None) -> float:
    source = os.environ if environ is None else environ
    raw_value = source.get(EDIT_INTERVAL_ENV)
    if raw_value is None:
        return DEFAULT_EDIT_INTERVAL_S
    try:
        value = float(raw_value.strip())
        validate_edit_interval(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{EDIT_INTERVAL_ENV} must be a finite, nonnegative number") from error
    return value


def read_semantic_streaming_from_environment(
    environ: Mapping[str, str] | None = None,
) -> bool:
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
    intents = discord.Intents.none()
    intents.dm_messages = True
    return intents


def is_direct_message(message: Any) -> bool:
    return isinstance(getattr(message, "channel", None), discord.DMChannel)


@dataclass(frozen=True, slots=True)
class _OpaqueConversationIdentity:
    value: str = field(default_factory=lambda: str(uuid4()))


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


@dataclass(slots=True)
class _PendingPresentation:
    event_id: str
    channel: Any
    typing_context: Any
    presenter: ReplyPresenter | None = None
    run_id: str | None = None
    diagnostic_context: AbstractContextManager[None] | None = None


class _DiscordEnvironment:
    """Discord-only observation and presentation implementation."""

    environment_id = DISCORD_ENVIRONMENT_ID

    def __init__(
        self,
        *,
        client: discord.Client,
        message_filter: Callable[[Any], bool],
        token_provider: Callable[[], str | None],
        edit_interval_s: float,
        dedupe_capacity: int,
        clock: Callable[[], float],
        diagnostics: DiscordDiagnostics | None,
        semantic_streaming: bool,
        semantic_lookahead_s: float,
        semantic_max_tail_chars: int,
    ) -> None:
        self._client = client
        self._message_filter = message_filter
        self._token_provider = token_provider
        self._edit_interval_s = edit_interval_s
        self._clock = clock
        self._diagnostics = diagnostics
        self._semantic_streaming = semantic_streaming
        self._semantic_lookahead_s = semantic_lookahead_s
        self._semantic_max_tail_chars = semantic_max_tail_chars
        self._dedupe = _MessageIdDeduplicator(dedupe_capacity)
        self._subjects: dict[str, _OpaqueConversationIdentity] = {}
        self._subject_channels: dict[str, Any] = {}
        self._routes: dict[str, Any] = {}
        self._presentations: dict[str, _PendingPresentation] = {}
        self._submit: EventSubmitter | None = None
        self._explicit_token: str | None = None
        self._ready = asyncio.Event()
        self._run_stopped = asyncio.Event()
        self._startup_error: BaseException | None = None

    def register_handler(self, handler: Callable[[Any], Any]) -> None:
        async def on_message(message: discord.Message) -> None:
            await handler(message)

        self._client.event(on_message)

    async def run(self, submit: EventSubmitter) -> None:
        self._submit = submit
        start = getattr(self._client, "start", None)
        if not callable(start):
            self._ready.set()
            try:
                await self._run_stopped.wait()
            finally:
                self._submit = None
            return
        token = self._explicit_token or self._token_provider()
        if not token:
            error = MissingDiscordToken(f"set {DISCORD_TOKEN_ENV} before starting the edge")
            self._startup_error = error
            self._ready.set()
            raise error
        self._ready.set()
        try:
            await cast(Callable[..., Awaitable[None]], start)(token, reconnect=True)
        finally:
            self._submit = None
            self._run_stopped.set()

    async def wait_ready(self) -> None:
        await self._ready.wait()
        if self._startup_error is not None:
            raise self._startup_error

    async def wait_stopped(self) -> None:
        await self._run_stopped.wait()

    def set_token(self, token: str | None) -> None:
        self._explicit_token = token

    async def handle_message(self, message: Any) -> None:
        submit = self._submit
        if submit is None or not self._message_filter(message):
            return
        author = getattr(message, "author", None)
        if getattr(author, "bot", False):
            return
        user = self._client.user
        if user is not None and getattr(author, "id", None) == getattr(user, "id", None):
            return
        if not self._dedupe.claim(getattr(message, "id", "")):
            return

        channel = message.channel
        channel_key = str(channel.id)
        subject = self._subjects.setdefault(channel_key, _OpaqueConversationIdentity())
        self._subject_channels[subject.value] = channel
        event_id = str(uuid4())
        self._routes[event_id] = channel
        try:
            await submit(
                WorldEvent(
                    event_id,
                    EventSource(self.environment_id, subject.value),
                    "direct_message",
                    {"text": str(message.content)},
                )
            )
        except BaseException:
            self._routes.pop(event_id, None)
            raise

    def subject_for_channel(self, channel_id: object) -> str | None:
        identity = self._subjects.get(str(channel_id))
        return None if identity is None else identity.value

    def channel_for_subject(self, subject: str) -> Any | None:
        return self._subject_channels.get(subject)

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            event_id = self._text_argument(call, "event_id")
            if call.tool_name == PRESENTATION_OPEN:
                channel = self._routes[event_id]
                typing_context = channel.typing()
                await typing_context.__aenter__()
                self._presentations[event_id] = _PendingPresentation(
                    event_id=event_id,
                    channel=channel,
                    typing_context=typing_context,
                )
            elif call.tool_name == PRESENTATION_BIND:
                state = self._presentations[event_id]
                state.run_id = self._text_argument(call, "run_id")
                state.diagnostic_context = (
                    self._diagnostics.presentation_context(state.run_id)
                    if self._diagnostics is not None
                    else nullcontext()
                )
                state.diagnostic_context.__enter__()
                state.presenter = self._make_presenter(state.channel)
                state.presenter.start()
            elif call.tool_name == PRESENTATION_WATCH:
                error = await self._presenter(event_id).wait_for_error()
                return ToolResult(
                    call.call_id, ToolResultStatus.FAILED, None, reason_code=type(error).__name__
                )
            elif call.tool_name == PRESENTATION_DELTA:
                self._presenter(event_id).append_delta(self._text_argument(call, "text"))
            elif call.tool_name == PRESENTATION_COMPLETE:
                state = self._presentations[event_id]
                await self._presenter(event_id).complete(self._text_argument(call, "text"))
                await self._finish(state, "completed", None)
            elif call.tool_name == PRESENTATION_INTERRUPTED:
                state = self._presentations[event_id]
                reason = self._optional_text_argument(call, "reason")
                await self._presenter(event_id).interrupted(self._text_argument(call, "text"))
                await self._finish(state, "interrupted", cast(PresentationReason | None, reason))
            elif call.tool_name == PRESENTATION_FAILED:
                state = self._presentations[event_id]
                if state.presenter is None:
                    state.presenter = self._make_presenter(state.channel)
                await state.presenter.failed(self._optional_text_argument(call, "text") or "")
                await self._finish(state, "failed", "core_failed")
            elif call.tool_name == PRESENTATION_ABORT:
                state = self._presentations.get(event_id)
                if state is not None:
                    await self._cleanup(state)
            else:
                return ToolResult(
                    call.call_id, ToolResultStatus.INVALID, None, reason_code="unsupported_action"
                )
        except BaseException as error:
            state = self._presentations.get(str((call.arguments or {}).get("event_id", "")))
            if state is not None:
                await self._cleanup(state)
            return ToolResult(
                call.call_id, ToolResultStatus.FAILED, None, reason_code=type(error).__name__
            )
        return ToolResult(call.call_id, ToolResultStatus.OK, {"status": "ok"})

    async def close(self) -> None:
        self._run_stopped.set()
        for state in tuple(self._presentations.values()):
            await self._cleanup(state)
        await self._client.close()

    def _make_presenter(self, channel: Any) -> ReplyPresenter:
        return ReplyPresenter(
            DiscordMessageSink(
                channel,
                metrics=TransportMetrics(),
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

    def _presenter(self, event_id: str) -> ReplyPresenter:
        presenter = self._presentations[event_id].presenter
        if presenter is None:
            raise RuntimeError("presentation is not bound")
        return presenter

    async def _finish(
        self,
        state: _PendingPresentation,
        status: PresentationStatus,
        reason: PresentationReason | None,
    ) -> None:
        presenter = state.presenter
        if self._diagnostics is not None and presenter is not None:
            self._diagnostics.presentation_outcome(
                status=status,
                reason=reason,
                flush_count=presenter.flush_count,
                terminal_flush_count=presenter.terminal_flush_count,
            )
        await self._cleanup(state)

    async def _cleanup(self, state: _PendingPresentation) -> None:
        if self._presentations.pop(state.event_id, None) is None:
            return
        try:
            if state.presenter is not None:
                await state.presenter.shutdown()
        finally:
            if state.diagnostic_context is not None:
                state.diagnostic_context.__exit__(None, None, None)
            await state.typing_context.__aexit__(None, None, None)
            self._routes.pop(state.event_id, None)

    @staticmethod
    def _text_argument(call: ToolCall, name: str) -> str:
        value = (call.arguments or {}).get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be non-empty text")
        return value

    @staticmethod
    def _optional_text_argument(call: ToolCall, name: str) -> str | None:
        value = (call.arguments or {}).get(name)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{name} must be text")
        return value


RuntimeFactory = Callable[[], ConversationRuntime]
CoreFactory = Callable[[ConversationRuntime], ConversationCore]
MessageFilter = Callable[[Any], bool]
ToolRuntimeFactory = Callable[[Any, ApplicationToolSessionFactory], ConversationRuntime]


class DiscordTextEdge:
    """Application composition root for the runtime-owned Discord environment."""

    def __init__(
        self,
        *,
        client: discord.Client | None = None,
        runtime_factory: RuntimeFactory = ModelRuntime,
        core_factory: CoreFactory = create_conversation,
        tool_enabled: bool = False,
        tool_runtime_factory: ToolRuntimeFactory | None = None,
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
        if type(tool_enabled) is not bool:
            raise TypeError("tool_enabled must be a bool")
        validate_edit_interval(edit_interval_s)
        validate_edit_interval(semantic_lookahead_s)
        if isinstance(semantic_max_tail_chars, bool) or semantic_max_tail_chars <= 0:
            raise ValueError("semantic_max_tail_chars must be positive")
        if client is None:
            options: dict[str, Any] = {
                "intents": make_dm_intents(),
                "status": discord.Status.online,
            }
            if diagnostics is not None:
                options["http_trace"] = diagnostics.trace_config
            resolved_client = discord.Client(**options)
        else:
            resolved_client = client
            attach_http_trace(resolved_client, diagnostics)
        self._client = resolved_client
        self._semantic_streaming = semantic_streaming
        self._environment = _DiscordEnvironment(
            client=resolved_client,
            message_filter=message_filter,
            token_provider=token_provider,
            edit_interval_s=edit_interval_s,
            dedupe_capacity=dedupe_capacity,
            clock=clock,
            diagnostics=diagnostics,
            semantic_streaming=semantic_streaming,
            semantic_lookahead_s=semantic_lookahead_s,
            semantic_max_tail_chars=semantic_max_tail_chars,
        )

        tool_factories: dict[int, DiscordToolSessionFactory] = {}
        self._tool_factories: weakref.WeakSet[DiscordToolSessionFactory] = weakref.WeakSet()

        def route_runtime_factory(route_key: tuple[str, str]) -> ConversationRuntime:
            channel = self._environment.channel_for_subject(route_key[1])
            if channel is None:
                raise RuntimeError("trusted Discord DM scope is unavailable")
            if tool_runtime_factory is None:
                factory = DiscordToolSessionFactory(
                    channel,
                    asyncio.get_running_loop(),
                )
                runtime = ModelRuntimeV3(tool_session_factory=factory)
            else:
                factory = DiscordToolSessionFactory(
                    channel,
                    asyncio.get_running_loop(),
                )
                runtime = tool_runtime_factory(channel, factory)
            tool_factories[id(runtime)] = factory
            self._tool_factories.add(factory)
            return runtime

        def configure_tool_runtime(
            runtime: ConversationRuntime,
            core: ConversationCore,
            route_key: tuple[str, str],
        ) -> None:
            del route_key
            factory = tool_factories.get(id(runtime))
            if factory is None:
                raise RuntimeError("tool runtime was not created by trusted composition")
            factory.bind_scope(core.scope_id)

        self._router = CoreConversationRouter(
            runtime_factory=runtime_factory,
            route_runtime_factory=route_runtime_factory if tool_enabled else None,
            core_factory=core_factory,
            session_configurator=configure_tool_runtime if tool_enabled else None,
            close_timeout_s=close_timeout_s,
        )
        self._runtime = LilavelRuntime(
            wake_policy=DirectMessageWakePolicy(),
            event_router=self._router,
            shutdown_timeout=close_timeout_s,
        )
        self._runtime.register_environment(self._environment)
        self._start_lock = asyncio.Lock()
        self._closed = False
        self._failure_observed = False
        self._environment.register_handler(self.handle_message)

    @property
    def client(self) -> discord.Client:
        return self._client

    @property
    def active_task_count(self) -> int:
        return self._runtime.active_route_count

    @property
    def observation_count(self) -> int:
        return self._runtime.health().accepted_events

    @property
    def session_count(self) -> int:
        return self._router.session_count

    def conversation_key_for_channel(self, channel_id: object) -> str | None:
        subject = self._environment.subject_for_channel(channel_id)
        if subject is None:
            return None
        return self._router.conversation_key(DISCORD_ENVIRONMENT_ID, subject)

    def conversation_history_for_channel(self, channel_id: object) -> tuple[object, ...] | None:
        subject = self._environment.subject_for_channel(channel_id)
        if subject is None:
            return None
        return self._router.history(DISCORD_ENVIRONMENT_ID, subject)

    def tool_proof_evidence(self) -> tuple[dict[str, object], ...]:
        """Return bounded, redacted evidence for explicit tool proof runs."""

        snapshots: list[dict[str, object]] = []
        for factory in self._tool_factories:
            records: list[dict[str, object]] = []
            for session in factory.sessions:
                records.extend(
                    {
                        "generation_id": record.generation_id,
                        "epoch": record.epoch,
                        "round": record.round,
                        "kind": record.kind,
                        "status_code": record.status_code,
                        "effect": record.effect,
                        "authorization": record.authorization,
                    }
                    for record in session.evidence()
                )
            snapshots.append(
                {
                    "exposed_tools": tuple(spec.name for spec in factory.registry.tools()),
                    "send_attempt_count": factory.send_attempt_count,
                    "sessions": tuple(records),
                }
            )
        return tuple(snapshots)

    async def start(self, token: str | None = None) -> None:
        self._environment.set_token(token)
        try:
            await self._ensure_started()
            await self._environment.wait_stopped()
        except MissingDiscordToken:
            self._failure_observed = True
            raise
        finally:
            await self.close()

    async def handle_message(self, message: Any) -> None:
        await self._ensure_started()
        await self._environment.handle_message(message)

    async def wait_idle(self, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        stable = 0
        while stable < 2:
            if self._runtime.failure is not None:
                self._failure_observed = True
                raise RuntimeError("Discord runtime routing failed") from self._runtime.failure
            idle = self._runtime.health().queue_size == 0 and self.active_task_count == 0
            stable = stable + 1 if idle else 0
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Discord runtime did not become idle")
            await asyncio.sleep(0)

    async def close(self) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        try:
            await self._runtime.stop()
        except BaseException as error:
            if not self._failure_observed:
                errors.append(error)
        try:
            await self._environment.close()
        except BaseException as error:
            errors.append(error)
        self._closed = True
        if errors:
            raise RuntimeError("Discord edge shutdown did not complete cleanly") from errors[0]

    async def _ensure_started(self) -> None:
        async with self._start_lock:
            if self._runtime.state is RuntimeState.NEW:
                await self._runtime.start()
            await self._environment.wait_ready()
