"""Deterministic tests for the discord.py send/edit boundary."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import discord

from lilavel_discord_edge import DiscordMessageSink, TransportMetrics


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.edits: list[dict[str, Any]] = []
        self.deleted = False

    async def edit(self, **kwargs: Any) -> FakeMessage:
        self.edits.append(kwargs)
        self.content = cast(str, kwargs["content"])
        return self

    async def delete(self) -> None:
        self.deleted = True


class FakeChannel:
    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []
        self.messages: list[FakeMessage] = []

    async def send(self, **kwargs: Any) -> FakeMessage:
        self.sends.append(kwargs)
        message = FakeMessage(cast(str, kwargs["content"]))
        self.messages.append(message)
        return message


def test_create_and_edit_always_disable_mentions() -> None:
    async def scenario() -> None:
        channel = FakeChannel()
        metrics = TransportMetrics()
        sink = DiscordMessageSink(cast(Any, channel), metrics=metrics)

        message = await sink.create("@everyone <@123>")
        await sink.edit(message, "@here <@&456>")

        allowed = [channel.sends[0]["allowed_mentions"], message.edits[0]["allowed_mentions"]]
        assert all(isinstance(value, discord.AllowedMentions) for value in allowed)
        assert all(value.to_dict() == {"parse": []} for value in allowed)
        assert len(metrics.send_latencies_s) == 1
        assert len(metrics.edit_latencies_s) == 1

    asyncio.run(scenario())
