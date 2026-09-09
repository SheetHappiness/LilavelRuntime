"""P4-D deterministic Discord executor and explicit composition proof."""

from __future__ import annotations

import asyncio
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import discord
import pytest
from lilavel_contracts import ToolCall, ToolEffect, ToolResultStatus
from lilavel_core import (
    ModelRuntimeV3,
    ToolBatchCorrelation,
    ToolGenerationContext,
)
from lilavel_runtime import DeterministicToolSession

from lilavel_discord_edge import (
    DISCORD_SEND_MESSAGE_MAX_CHARS,
    DISCORD_SEND_MESSAGE_NAME,
    DISCORD_SEND_MESSAGE_PROVIDER_ALIAS,
    DISCORD_SEND_MESSAGE_SPEC,
    DiscordSendPreflightError,
    DiscordTextEdge,
    DiscordToolSessionFactory,
)

FIXTURE = Path(__file__).parent / "fixtures" / "discord_tool_sidecar.py"


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.edits: list[dict[str, Any]] = []

    async def edit(self, **kwargs: Any) -> FakeMessage:
        self.edits.append(kwargs)
        self.content = str(kwargs["content"])
        return self


class FakeTyping:
    async def __aenter__(self) -> FakeTyping:
        return self

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        del exc_type, exc_value, traceback


class FakeChannel:
    def __init__(
        self,
        channel_id: str = "trusted-dm-channel",
        *,
        failure: str | None = None,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.id = channel_id
        self.failure = failure
        self.started = started
        self.release = release
        self.attempts = 0
        self.sends: list[dict[str, Any]] = []
        self.messages: list[FakeMessage] = []

    def typing(self) -> FakeTyping:
        return FakeTyping()

    async def send(self, **kwargs: Any) -> FakeMessage:
        if self.failure == "preflight":
            raise DiscordSendPreflightError("fixture preflight failure")
        self.attempts += 1
        self.sends.append(kwargs)
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        if self.failure == "ambiguous":
            raise ConnectionError("fixture transport ambiguity")
        message = FakeMessage(str(kwargs["content"]))
        self.messages.append(message)
        return message


class FakeClient:
    def __init__(self) -> None:
        self.user: Any = type("User", (), {"id": "bot", "bot": True})()
        self.handlers: dict[str, Any] = {}
        self.closed = False

    def event(self, coroutine: Any) -> Any:
        self.handlers[coroutine.__name__] = coroutine
        return coroutine

    async def close(self) -> None:
        self.closed = True


@dataclass
class InboundMessage:
    id: str
    channel: FakeChannel
    author: Any
    content: str


def inbound(channel: FakeChannel, message_id: str = "message-1") -> InboundMessage:
    author = type("User", (), {"id": "human", "bot": False})()
    return InboundMessage(message_id, channel, author, "hello")


def context() -> ToolGenerationContext:
    return ToolGenerationContext("runtime", "trusted-core-scope", "run", "generation", 1)


def correlation() -> ToolBatchCorrelation:
    return ToolBatchCorrelation(context(), 1)


def make_factory(
    channel: FakeChannel,
    *,
    loop: asyncio.AbstractEventLoop,
) -> DiscordToolSessionFactory:
    factory = DiscordToolSessionFactory(
        channel,
        loop,
        executor_deadline=0.2,
        containment_deadline=0.2,
    )
    factory.bind_scope(context().scope_id)
    return factory


async def execute(
    factory: DiscordToolSessionFactory,
    call: ToolCall,
    *,
    cancelled: threading.Event | None = None,
) -> tuple[Any, Any]:
    session = factory.create(context())
    cancellation = cancelled or threading.Event()
    result: list[tuple[Any, ...]] = []

    def run() -> None:
        result.append(session.execute_batch(correlation(), (call,), cancellation))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    while worker.is_alive():
        await asyncio.sleep(0.01)
    worker.join()
    return session, result[0]


def assert_mentions_suppressed(arguments: dict[str, Any]) -> None:
    allowed = arguments["allowed_mentions"]
    assert isinstance(allowed, discord.AllowedMentions)
    assert allowed.to_dict() == {"parse": []}


def test_spec_is_content_only_and_provider_alias_remains_transport_local() -> None:
    assert DISCORD_SEND_MESSAGE_NAME == "discord.send_message"
    assert DISCORD_SEND_MESSAGE_PROVIDER_ALIAS == "discord_send_message"
    assert DISCORD_SEND_MESSAGE_SPEC.name == DISCORD_SEND_MESSAGE_NAME
    assert DISCORD_SEND_MESSAGE_SPEC.input_schema["properties"] == {
        "text": {
            "type": "string",
            "minLength": 1,
            "maxLength": DISCORD_SEND_MESSAGE_MAX_CHARS,
        }
    }
    assert "channel_id" not in repr(DISCORD_SEND_MESSAGE_SPEC.input_schema)
    assert "user_id" not in repr(DISCORD_SEND_MESSAGE_SPEC.input_schema)


def test_wrong_core_scope_fails_closed_without_a_discord_attempt() -> None:
    async def scenario() -> None:
        channel = FakeChannel()
        factory = make_factory(channel, loop=asyncio.get_running_loop())
        foreign_context = ToolGenerationContext(
            "runtime", "different-core-scope", "run", "generation", 1
        )

        with pytest.raises(ValueError, match="scope"):
            factory.create(foreign_context)
        assert channel.attempts == 0

    asyncio.run(scenario())


def test_allowed_scoped_send_is_confirmed_once_with_mentions_disabled() -> None:
    async def scenario() -> None:
        channel = FakeChannel()
        factory = make_factory(channel, loop=asyncio.get_running_loop())
        session, results = await execute(
            factory,
            ToolCall("call-1", DISCORD_SEND_MESSAGE_NAME, {"text": "hello <@everyone>"}),
        )

        assert len(results) == 1
        assert results[0].status is ToolResultStatus.OK
        assert results[0].effect is ToolEffect.CONFIRMED
        assert channel.attempts == 1
        assert len(channel.sends) == 1
        assert channel.sends[0]["content"] == "hello <@everyone>"
        assert_mentions_suppressed(channel.sends[0])
        assert [record.kind for record in session.evidence()] == [
            "requested",
            "execution_started",
            "execution_settled",
        ]

    asyncio.run(scenario())


def test_unexposed_snapshot_produces_zero_discord_attempts() -> None:
    async def scenario() -> None:
        channel = FakeChannel()
        factory = make_factory(channel, loop=asyncio.get_running_loop())
        session = DeterministicToolSession(context(), factory.registry.snapshot(()))
        result: list[tuple[Any, ...]] = []

        def run() -> None:
            result.append(
                session.execute_batch(
                    correlation(),
                    (ToolCall("call-1", DISCORD_SEND_MESSAGE_NAME, {"text": "blocked"}),),
                    threading.Event(),
                )
            )

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        while worker.is_alive():
            await asyncio.sleep(0.01)
        worker.join()
        results = result[0]

        assert results[0].status is ToolResultStatus.UNAVAILABLE
        assert results[0].effect is ToolEffect.NONE
        assert channel.attempts == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ({"text": "injected", "channel_id": "attacker"}, "unexpected_property"),
        ({"text": "x" * (DISCORD_SEND_MESSAGE_MAX_CHARS + 1)}, "string_too_long"),
    ],
)
def test_invalid_model_arguments_never_override_scope_or_send(
    arguments: dict[str, Any], reason: str
) -> None:
    async def scenario() -> None:
        channel = FakeChannel()
        factory = make_factory(channel, loop=asyncio.get_running_loop())
        _, results = await execute(
            factory,
            ToolCall("call-1", DISCORD_SEND_MESSAGE_NAME, arguments),
        )

        assert results[0].status is ToolResultStatus.INVALID
        assert results[0].reason_code == reason
        assert results[0].effect is ToolEffect.NONE
        assert channel.attempts == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("failure", "effect", "reason"),
    [
        ("preflight", ToolEffect.NONE, "discord_preflight_failed"),
        ("ambiguous", ToolEffect.UNKNOWN, "discord_delivery_unknown"),
    ],
)
def test_failure_mapping_is_conservative_and_never_retries(
    failure: str, effect: ToolEffect, reason: str
) -> None:
    async def scenario() -> None:
        channel = FakeChannel(failure=failure)
        factory = make_factory(channel, loop=asyncio.get_running_loop())
        _, results = await execute(
            factory,
            ToolCall("call-1", DISCORD_SEND_MESSAGE_NAME, {"text": "one attempt"}),
        )

        assert results[0].status is ToolResultStatus.FAILED
        assert results[0].effect is effect
        assert results[0].reason_code == reason
        assert channel.attempts == (0 if failure == "preflight" else 1)

    asyncio.run(scenario())


def test_cancellation_before_send_has_no_effect() -> None:
    async def scenario() -> None:
        channel = FakeChannel()
        factory = make_factory(channel, loop=asyncio.get_running_loop())
        cancelled = threading.Event()
        cancelled.set()
        session, results = await execute(
            factory,
            ToolCall("call-1", DISCORD_SEND_MESSAGE_NAME, {"text": "not sent"}),
            cancelled=cancelled,
        )

        assert results == ()
        assert session.settlement == "cancelled"
        assert channel.attempts == 0

    asyncio.run(scenario())


def test_cancellation_in_flight_waits_for_ambiguous_send_and_records_unknown() -> None:
    async def scenario() -> None:
        started = threading.Event()
        release = threading.Event()
        channel = FakeChannel(failure="ambiguous", started=started, release=release)
        factory = make_factory(channel, loop=asyncio.get_running_loop())
        cancelled = threading.Event()
        session = cast(DeterministicToolSession, factory.create(context()))
        result: list[tuple[Any, ...]] = []

        def run() -> None:
            result.append(
                session.execute_batch(
                    correlation(),
                    (ToolCall("call-1", DISCORD_SEND_MESSAGE_NAME, {"text": "maybe sent"}),),
                    cancelled,
                )
            )

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        while not started.is_set():
            await asyncio.sleep(0.01)
        cancelled.set()
        release.set()
        while worker.is_alive():
            await asyncio.sleep(0.01)
        worker.join()
        results = result[0]

        assert results[0].status is ToolResultStatus.FAILED
        assert results[0].effect is ToolEffect.UNKNOWN
        assert session.settlement == "cancelled"
        assert channel.attempts == 1
        assert len([item for item in session.evidence() if item.kind == "execution_settled"]) == 1

    asyncio.run(scenario())


def tool_runtime_factory(
    channel: Any,
    session_factory: Any,
    mode: str,
    runtimes: list[ModelRuntimeV3],
    factories: list[DiscordToolSessionFactory],
) -> ModelRuntimeV3:
    del channel
    runtime = ModelRuntimeV3(
        command=(sys.executable, str(FIXTURE), mode),
        sidecar_dir=FIXTURE.parent,
        tool_session_factory=session_factory,
        startup_timeout=2.0,
        shutdown_timeout=2.0,
        cancellation_timeout=1.0,
        tool_result_wait_deadline=1.0,
        tool_generation_deadline=3.0,
        tool_settlement_deadline=0.5,
    )
    runtimes.append(runtime)
    factories.append(session_factory)
    return runtime


async def wait_idle(edge: DiscordTextEdge, timeout: float = 3.0) -> None:
    await asyncio.wait_for(edge.wait_idle(timeout), timeout + 1.0)


@pytest.mark.parametrize(
    ("mode", "expected_tool_status", "expected_effect", "expected_final"),
    [
        ("allowed", ToolResultStatus.OK, ToolEffect.CONFIRMED, "sent"),
        ("inject-destination", ToolResultStatus.INVALID, ToolEffect.NONE, "tool rejected"),
    ],
)
def test_explicit_tool_enabled_dm_composition_runs_real_boundary_without_history_tool_transcript(
    mode: str,
    expected_tool_status: ToolResultStatus,
    expected_effect: ToolEffect,
    expected_final: str,
) -> None:
    async def scenario() -> None:
        client = FakeClient()
        channel = FakeChannel()
        runtimes: list[ModelRuntimeV3] = []
        factories: list[DiscordToolSessionFactory] = []
        cores: list[Any] = []

        def core_factory(runtime: Any) -> Any:
            from lilavel_core import ConversationCore

            core = ConversationCore(runtime)
            cores.append(core)
            return core

        edge = DiscordTextEdge(
            client=cast(Any, client),
            core_factory=core_factory,
            tool_enabled=True,
            tool_runtime_factory=lambda destination, session_factory: tool_runtime_factory(
                destination, session_factory, mode, runtimes, factories
            ),
            message_filter=lambda message: True,
            edit_interval_s=0.0,
            close_timeout_s=2.0,
            semantic_streaming=False,
            semantic_lookahead_s=0.0,
        )
        try:
            await client.handlers["on_message"](inbound(channel))
            await wait_idle(edge)

            assert len(runtimes) == 1
            assert len(factories) == 1
            assert factories[0].registry.tools() == (DISCORD_SEND_MESSAGE_SPEC,)
            assert len(factories[0].sessions) == 1
            settled = [
                record
                for record in factories[0].sessions[0].evidence()
                if record.kind == "execution_settled"
            ]
            assert len(settled) == 1
            assert settled[0].status_code == expected_tool_status.value
            assert settled[0].effect == expected_effect.value
            assert channel.messages[-1].content == expected_final
            tool_sends = [
                item for item in channel.sends if item["content"] == "tool message <@everyone>"
            ]
            assert len(tool_sends) == (1 if mode == "allowed" else 0)
            for item in channel.sends:
                assert_mentions_suppressed(item)
            assert cores[0].history[-1].text == expected_final
            assert all(message.text != "tool message <@everyone>" for message in cores[0].history)
            assert all("channel_id" not in repr(request) for request in runtimes[0].tool_evidence())
        finally:
            await edge.close()

    asyncio.run(scenario())
