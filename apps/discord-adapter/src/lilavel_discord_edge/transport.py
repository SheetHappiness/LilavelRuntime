"""Small Discord transport primitives with safe message presentation."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import discord
from discord.abc import Messageable

from .diagnostics import DiscordDiagnostics

MAX_DISCORD_MESSAGE_CHARS: Final = 2000


@dataclass(frozen=True, slots=True)
class RateLimitObservation:
    """A surfaced HTTP 429; discord.py normally handles route backoff itself."""

    operation: Literal["send", "edit", "delete"]
    retry_after_s: float | None


@dataclass(slots=True)
class TransportMetrics:
    """Non-sensitive timings and surfaced rate-limit observations."""

    send_latencies_s: list[float] = field(default_factory=lambda: list[float]())
    edit_latencies_s: list[float] = field(default_factory=lambda: list[float]())
    delete_latencies_s: list[float] = field(default_factory=lambda: list[float]())
    rate_limits: list[RateLimitObservation] = field(
        default_factory=lambda: list[RateLimitObservation]()
    )


def split_discord_content(
    text: str,
    *,
    max_chars: int = MAX_DISCORD_MESSAGE_CHARS,
) -> tuple[str, ...]:
    """Split text on Python Unicode code-point boundaries under Discord's limit."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not text:
        return ()
    return tuple(text[index : index + max_chars] for index in range(0, len(text), max_chars))


class DiscordMessageSink:
    """Adapt Discord send/edit calls while forcing mention suppression."""

    def __init__(
        self,
        channel: Messageable,
        *,
        metrics: TransportMetrics | None = None,
        diagnostics: DiscordDiagnostics | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._channel = channel
        self.metrics = metrics if metrics is not None else TransportMetrics()
        self._diagnostics = diagnostics
        self._clock = clock

    async def create(self, content: str) -> Any:
        started = self._clock()
        try:
            if self._diagnostics is None:
                message = await self._channel.send(
                    content=content,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                with self._diagnostics.operation("POST message"):
                    message = await self._channel.send(
                        content=content,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
        except discord.HTTPException as error:
            self._record_rate_limit("send", error)
            raise
        self.metrics.send_latencies_s.append(self._clock() - started)
        return message

    async def edit(self, message: Any, content: str) -> Any:
        started = self._clock()
        try:
            if self._diagnostics is None:
                edited = await message.edit(
                    content=content,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                with self._diagnostics.operation("PATCH message"):
                    edited = await message.edit(
                        content=content,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
        except discord.HTTPException as error:
            self._record_rate_limit("edit", error)
            raise
        self.metrics.edit_latencies_s.append(self._clock() - started)
        return edited

    async def delete(self, message: Any) -> None:
        started = self._clock()
        try:
            if self._diagnostics is None:
                await message.delete()
            else:
                with self._diagnostics.operation("DELETE message"):
                    await message.delete()
        except discord.HTTPException as error:
            self._record_rate_limit("delete", error)
            raise
        self.metrics.delete_latencies_s.append(self._clock() - started)

    def _record_rate_limit(
        self, operation: Literal["send", "edit", "delete"], error: discord.HTTPException
    ) -> None:
        if getattr(error, "status", None) != 429:
            return
        retry_after = getattr(error, "retry_after", None)
        if not isinstance(retry_after, (int, float)):
            response = getattr(error, "response", None)
            headers = getattr(response, "headers", {})
            raw_retry_after: object = (
                headers.get("Retry-After") if hasattr(headers, "get") else None
            )
            if isinstance(raw_retry_after, (int, float)):
                retry_after = float(raw_retry_after)
            elif isinstance(raw_retry_after, str):
                try:
                    retry_after = float(raw_retry_after)
                except ValueError:
                    retry_after = None
            else:
                retry_after = None
        self.metrics.rate_limits.append(
            RateLimitObservation(
                operation,
                retry_after if isinstance(retry_after, (int, float)) else None,
            )
        )


def validate_edit_interval(edit_interval_s: float) -> None:
    if not math.isfinite(edit_interval_s) or edit_interval_s < 0:
        raise ValueError("edit_interval_s must be finite and nonnegative")
