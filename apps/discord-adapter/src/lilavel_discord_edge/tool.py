"""Explicit Discord model-tool composition and one-shot send executor."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any, Final

import discord
from lilavel_contracts import ToolCall, ToolEffect, ToolResult, ToolResultStatus, ToolSpec
from lilavel_core import (
    DEFAULT_TOOL_EXECUTOR_DEADLINE,
    ApplicationToolSession,
    ToolBatchCorrelation,
    ToolGenerationContext,
)
from lilavel_runtime import (
    ApplicationToolRegistry,
    DeterministicToolSession,
    ToolAuthorization,
    ToolBinding,
)

from .transport import MAX_DISCORD_MESSAGE_CHARS

DISCORD_SEND_MESSAGE_NAME: Final = "discord.send_message"
DISCORD_SEND_MESSAGE_PROVIDER_ALIAS: Final = "discord_send_message"
DISCORD_SEND_MESSAGE_MAX_CHARS: Final = MAX_DISCORD_MESSAGE_CHARS

DISCORD_SEND_MESSAGE_SPEC: Final = ToolSpec(
    DISCORD_SEND_MESSAGE_NAME,
    "Send one bounded message to the current trusted one-to-one Discord DM.",
    {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": DISCORD_SEND_MESSAGE_MAX_CHARS,
            }
        },
        "required": ["text"],
        "additionalProperties": False,
    },
)


class DiscordSendPreflightError(RuntimeError):
    """A testable/local failure known to occur before a Discord request."""


class _DiscordSendMessageExecutor:
    """Bridge one synchronous tool worker to the adapter's asyncio loop.

    The coroutine is never cancelled merely because the tool caller cancelled.
    The worker waits for the already-submitted send to settle, so the P4-B
    executor barrier observes the real outcome. A cancellation before the
    submission fence does not schedule a request.
    """

    def __init__(
        self,
        channel: Any,
        loop: asyncio.AbstractEventLoop | None,
        *,
        channel_resolver: Callable[[], Any | None] | None = None,
        availability: Callable[[], bool] | None = None,
        send_authorizer: Callable[[], bool] | None = None,
        loop_resolver: Callable[[], asyncio.AbstractEventLoop | None] | None = None,
    ) -> None:
        if channel_resolver is not None and channel is not None:
            raise ValueError("channel and channel_resolver are mutually exclusive")
        self._channel_resolver = channel_resolver or (lambda: channel)
        self._availability = availability
        self._loop_resolver = loop_resolver
        self._send_authorizer = send_authorizer
        self._loop = loop
        self._attempts = 0
        self._attempts_lock = threading.Lock()

    @property
    def send_attempt_count(self) -> int:
        """Return the safe count of admitted Discord send operations."""

        with self._attempts_lock:
            return self._attempts

    def __call__(
        self,
        correlation: ToolBatchCorrelation,
        call: ToolCall,
        cancelled: threading.Event,
    ) -> ToolResult:
        del correlation
        arguments = call.arguments
        text = None if arguments is None else arguments.get("text")
        if not isinstance(text, str) or not text:
            return ToolResult(
                call.call_id,
                ToolResultStatus.FAILED,
                None,
                reason_code="discord_preflight_failed",
                effect=ToolEffect.NONE,
            )
        if cancelled.is_set():
            return ToolResult(
                call.call_id,
                ToolResultStatus.FAILED,
                None,
                reason_code="cancelled_before_send",
                effect=ToolEffect.NONE,
            )

        if self._availability is not None:
            try:
                available = self._availability()
            except BaseException:
                available = False
            if not available:
                return ToolResult(
                    call.call_id,
                    ToolResultStatus.FAILED,
                    None,
                    reason_code="discord_preflight_failed",
                    effect=ToolEffect.NONE,
                )
        try:
            channel = self._channel_resolver()
        except BaseException:
            channel = None
        if channel is None:
            return ToolResult(
                call.call_id,
                ToolResultStatus.FAILED,
                None,
                reason_code="discord_preflight_failed",
                effect=ToolEffect.NONE,
            )
        if self._send_authorizer is not None:
            try:
                authorized = self._send_authorizer()
            except BaseException:
                authorized = False
            if not authorized:
                return ToolResult(
                    call.call_id,
                    ToolResultStatus.FAILED,
                    None,
                    reason_code="discord_preflight_failed",
                    effect=ToolEffect.NONE,
                )

        loop = self._resolved_loop()
        if loop is None or loop.is_closed():
            return ToolResult(
                call.call_id,
                ToolResultStatus.FAILED,
                None,
                reason_code="discord_preflight_failed",
                effect=ToolEffect.NONE,
            )
        completed = threading.Event()
        outcome: list[object] = []
        coroutine = self._settle_send(channel, text, outcome, completed)
        try:
            asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            return ToolResult(
                call.call_id,
                ToolResultStatus.FAILED,
                None,
                reason_code="discord_preflight_failed",
                effect=ToolEffect.NONE,
            )

        # Cancellation is intentionally observed but does not cancel an
        # already submitted Discord operation. The worker waits for the
        # already-submitted send to settle, so the P4-B executor barrier
        # observes the real outcome. A cancellation before the submission
        # fence returned above without scheduling a request.
        while not completed.wait(0.01):
            pass
        if not outcome:
            return self._unknown(call, "discord_delivery_unknown")
        result = outcome[0]
        if isinstance(result, DiscordSendPreflightError):
            return ToolResult(
                call.call_id,
                ToolResultStatus.FAILED,
                None,
                reason_code="discord_preflight_failed",
                effect=ToolEffect.NONE,
            )
        if isinstance(result, discord.HTTPException):
            return self._http_failure(call, result)
        if isinstance(result, (ConnectionError, OSError, TimeoutError, asyncio.CancelledError)):
            return self._unknown(call, "discord_delivery_unknown")
        if isinstance(result, BaseException):
            # Once submitted, an opaque client/library failure cannot establish
            # whether Discord accepted the request.
            return self._unknown(call, "discord_delivery_unknown")
        return ToolResult(
            call.call_id,
            ToolResultStatus.OK,
            {"status": "sent"},
            effect=ToolEffect.CONFIRMED,
        )

    def _resolved_loop(self) -> asyncio.AbstractEventLoop | None:
        if self._loop_resolver is not None:
            try:
                return self._loop_resolver()
            except BaseException:
                return None
        return self._loop

    async def _send(self, channel: Any, text: str) -> Any:
        with self._attempts_lock:
            self._attempts += 1
        return await channel.send(
            content=text,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _settle_send(
        self,
        channel: Any,
        text: str,
        outcome: list[object],
        completed: threading.Event,
    ) -> None:
        try:
            outcome.append(await self._send(channel, text))
        except BaseException as error:
            # The worker consumes this typed boundary immediately; no raw
            # exception is returned as tool output or evidence.
            outcome.append(error)
        finally:
            completed.set()

    @staticmethod
    def _unknown(call: ToolCall, reason_code: str) -> ToolResult:
        return ToolResult(
            call.call_id,
            ToolResultStatus.FAILED,
            None,
            reason_code=reason_code,
            effect=ToolEffect.UNKNOWN,
        )

    @classmethod
    def _http_failure(cls, call: ToolCall, error: discord.HTTPException) -> ToolResult:
        status = getattr(error, "status", None)
        if isinstance(status, int) and 400 <= status < 500:
            return ToolResult(
                call.call_id,
                ToolResultStatus.FAILED,
                None,
                reason_code="discord_rejected",
                effect=ToolEffect.NONE,
            )
        return cls._unknown(call, "discord_delivery_unknown")


class DiscordToolSessionFactory:
    """Bind one model-exposable send capability to one trusted DM channel."""

    def __init__(
        self,
        channel: Any | None,
        loop: asyncio.AbstractEventLoop | None = None,
        *,
        executor_deadline: float = DEFAULT_TOOL_EXECUTOR_DEADLINE,
        containment_deadline: float = 1.0,
        channel_resolver: Callable[[], Any | None] | None = None,
        availability: Callable[[], bool] | None = None,
        send_authorizer: Callable[[], bool] | None = None,
        loop_resolver: Callable[[], asyncio.AbstractEventLoop | None] | None = None,
    ) -> None:
        self._channel = channel
        self._channel_resolver = channel_resolver or (lambda: channel)
        self._availability = availability
        self._loop = loop
        self._loop_resolver = loop_resolver
        self._trusted_scope_id: str | None = None
        self._lock = threading.Lock()
        self._executor_deadline = executor_deadline
        self._containment_deadline = containment_deadline
        executor = _DiscordSendMessageExecutor(
            channel,
            loop,
            channel_resolver=channel_resolver,
            availability=availability,
            send_authorizer=send_authorizer,
            loop_resolver=loop_resolver,
        )
        self._registry = ApplicationToolRegistry(
            [
                ToolBinding(
                    DISCORD_SEND_MESSAGE_SPEC,
                    executor,
                    authorize=self._authorize,
                    is_available=self._is_available,
                )
            ]
        )
        self._executor = executor
        self._sessions: list[DeterministicToolSession] = []

    @property
    def send_attempt_count(self) -> int:
        """Return the aggregate count without exposing destination or body."""

        return self._executor.send_attempt_count

    @property
    def registry(self) -> ApplicationToolRegistry:
        return self._registry

    @property
    def sessions(self) -> tuple[DeterministicToolSession, ...]:
        with self._lock:
            return tuple(self._sessions)

    def bind_scope(self, scope_id: str) -> None:
        if not scope_id or not scope_id.strip():
            raise ValueError("trusted scope must be non-empty")
        with self._lock:
            if self._trusted_scope_id not in (None, scope_id):
                raise ValueError("Discord tool scope cannot be rebound")
            self._trusted_scope_id = scope_id

    def create(self, context: ToolGenerationContext) -> ApplicationToolSession:
        with self._lock:
            if self._trusted_scope_id != context.scope_id:
                raise ValueError("Discord tool scope is not bound to this Core session")
        session = DeterministicToolSession(
            context,
            self._registry.snapshot((DISCORD_SEND_MESSAGE_NAME,)),
            executor_deadline=self._executor_deadline,
            containment_deadline=self._containment_deadline,
        )
        with self._lock:
            self._sessions.append(session)
        return session

    def _is_available(self, correlation: ToolBatchCorrelation) -> bool:
        with self._lock:
            scope_matches = self._trusted_scope_id == correlation.context.scope_id
        try:
            dynamic_available = self._availability is None or self._availability()
            channel = self._channel_resolver()
            loop = self._resolved_loop()
        except BaseException:
            return False
        return (
            scope_matches
            and loop is not None
            and not loop.is_closed()
            and dynamic_available
            and callable(getattr(channel, "send", None))
        )

    def _authorize(self, correlation: ToolBatchCorrelation, call: ToolCall) -> ToolAuthorization:
        with self._lock:
            scope_matches = self._trusted_scope_id == correlation.context.scope_id
        if not scope_matches:
            return ToolAuthorization.DENIED
        if self._availability is not None:
            try:
                if not self._availability():
                    return ToolAuthorization.UNAVAILABLE
            except BaseException:
                return ToolAuthorization.UNAVAILABLE
        # The schema is the first fence; this second check prevents a future
        # executor caller from smuggling a destination into this binding.
        if call.arguments is None or set(call.arguments) != {"text"}:
            return ToolAuthorization.DENIED
        return ToolAuthorization.ALLOWED

    def _resolved_loop(self) -> asyncio.AbstractEventLoop | None:
        if self._loop_resolver is not None:
            try:
                return self._loop_resolver()
            except BaseException:
                return None
        return self._loop


__all__ = [
    "DISCORD_SEND_MESSAGE_MAX_CHARS",
    "DISCORD_SEND_MESSAGE_NAME",
    "DISCORD_SEND_MESSAGE_PROVIDER_ALIAS",
    "DISCORD_SEND_MESSAGE_SPEC",
    "DiscordSendPreflightError",
    "DiscordToolSessionFactory",
]
