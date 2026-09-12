"""Deterministic DM ingress, Core integration, and lifecycle tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from queue import Queue
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from aiohttp.tracing import TraceRequestEndParams, TraceRequestStartParams
from lilavel_core import (
    ContextMessage,
    ConversationCore,
    GenerationAccepted,
    GenerationCancelled,
    GenerationCompleted,
    GenerationEvent,
    GenerationFailedEvent,
    ModelRequest,
    TextDelta,
)
from lilavel_core.production_cognition import create_conversation
from multidict import CIMultiDict
from yarl import URL

from lilavel_discord_edge import (
    DISCORD_TOKEN_ENV,
    FAILED_MARKER,
    INTERRUPTED_MARKER,
    SEMANTIC_STREAMING_ENV,
    DiscordDiagnostics,
    DiscordTextEdge,
    make_dm_intents,
    read_discord_token,
    read_semantic_streaming_from_environment,
)
from lilavel_discord_edge.edge import (
    EDIT_INTERVAL_ENV,
    read_edit_interval_from_environment,
)
from lilavel_discord_edge.presenter import DEFAULT_EDIT_INTERVAL_S


class FakeUser:
    def __init__(self, user_id: str, *, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot


class FakeClient:
    def __init__(self) -> None:
        self.user: FakeUser | None = FakeUser("bot-user", bot=True)
        self.handlers: dict[str, Any] = {}
        self.closed = False

    def event(self, coroutine: Any) -> Any:
        self.handlers[coroutine.__name__] = coroutine
        return coroutine

    async def close(self) -> None:
        self.closed = True


class FakeTyping:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel

    async def __aenter__(self) -> FakeTyping:
        self.channel.typing_entries += 1
        return self

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.channel.typing_exits += 1


class FakeMessage:
    def __init__(self, content: str, *, fail_edits: bool = False) -> None:
        self.content = content
        self.fail_edits = fail_edits
        self.edits: list[dict[str, Any]] = []

    async def edit(self, **kwargs: Any) -> FakeMessage:
        if self.fail_edits:
            raise RuntimeError("synthetic edit failure")
        self.edits.append(kwargs)
        self.content = cast(str, kwargs["content"])
        return self


class FakeChannel:
    def __init__(self, channel_id: str, *, is_dm: bool = True) -> None:
        self.id = channel_id
        self.is_dm = is_dm
        self.typing_entries = 0
        self.typing_exits = 0
        self.fail_edits = False
        self.sends: list[dict[str, Any]] = []
        self.messages: list[FakeMessage] = []

    def typing(self) -> FakeTyping:
        return FakeTyping(self)

    async def send(self, **kwargs: Any) -> FakeMessage:
        self.sends.append(kwargs)
        message = FakeMessage(cast(str, kwargs["content"]), fail_edits=self.fail_edits)
        self.messages.append(message)
        return message


async def emit_fake_http_request(diagnostics: DiscordDiagnostics, method: str, path: str) -> None:
    trace = diagnostics.trace_config
    context = trace.trace_config_ctx()
    url = URL(f"https://discord.com/api/v10{path}")
    await trace.on_request_start.send(
        cast(Any, None),
        context,
        TraceRequestStartParams(method, url, CIMultiDict()),
    )
    await trace.on_request_end.send(
        cast(Any, None),
        context,
        TraceRequestEndParams(
            method,
            url,
            CIMultiDict(),
            cast(Any, SimpleNamespace(status=200, headers=CIMultiDict())),
        ),
    )


class TracingFakeMessage(FakeMessage):
    def __init__(self, content: str, *, diagnostics: DiscordDiagnostics, channel_id: str) -> None:
        super().__init__(content)
        self._diagnostics = diagnostics
        self._channel_id = channel_id

    async def edit(self, **kwargs: Any) -> FakeMessage:
        await emit_fake_http_request(
            self._diagnostics,
            "PATCH",
            f"/channels/{self._channel_id}/messages/remote-message-id",
        )
        return await super().edit(**kwargs)


class TracingFakeChannel(FakeChannel):
    def __init__(self, channel_id: str, diagnostics: DiscordDiagnostics) -> None:
        super().__init__(channel_id)
        self._diagnostics = diagnostics

    async def send(self, **kwargs: Any) -> FakeMessage:
        self.sends.append(kwargs)
        await emit_fake_http_request(
            self._diagnostics,
            "POST",
            f"/channels/{self.id}/messages",
        )
        message = TracingFakeMessage(
            cast(str, kwargs["content"]),
            diagnostics=self._diagnostics,
            channel_id=self.id,
        )
        self.messages.append(message)
        return message


@dataclass
class FakeInboundMessage:
    id: str
    channel: FakeChannel
    author: FakeUser
    content: str


class FakeGeneration:
    def __init__(self, generation_id: str, epoch: int) -> None:
        self.generation_id = generation_id
        self.epoch = epoch
        self._events: Queue[GenerationEvent] = Queue()

    def emit(self, event: GenerationEvent) -> None:
        self._events.put(event)

    def events(self) -> Any:
        while True:
            event = self._events.get()
            yield event
            if isinstance(event, (GenerationCompleted, GenerationCancelled, GenerationFailedEvent)):
                return


class FakeRuntime:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.generations: list[FakeGeneration] = []
        self.cancelled_ids: list[str] = []
        self.started = False
        self.shutdown_called = False

    def start(self) -> None:
        self.started = True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def generate(self, request: ModelRequest) -> FakeGeneration:
        self.requests.append(request)
        generation = FakeGeneration(
            f"generation-{len(self.generations) + 1}", len(self.generations) + 1
        )
        self.generations.append(generation)
        return generation

    def cancel(self, generation_id: str) -> bool:
        self.cancelled_ids.append(generation_id)
        generation = next(item for item in self.generations if item.generation_id == generation_id)
        generation.emit(GenerationCancelled(generation.generation_id, generation.epoch))
        return True


def inbound(channel: FakeChannel, message_id: str, content: str) -> FakeInboundMessage:
    return FakeInboundMessage(message_id, channel, FakeUser("human"), content)


def default_message_filter(message: Any) -> bool:
    return bool(message.channel.is_dm)


async def wait_until(check: Any, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition did not become true before the deadline")
        await asyncio.sleep(0.001)


def edge_for(
    runtime_factory: Any,
    *,
    core_factory: Any = create_conversation,
    message_filter: Any = default_message_filter,
    semantic_streaming: bool = False,
    semantic_lookahead_s: float = 0.0,
    diagnostics: DiscordDiagnostics | None = None,
) -> tuple[DiscordTextEdge, FakeClient]:
    client = FakeClient()
    edge = DiscordTextEdge(
        client=cast(Any, client),
        runtime_factory=runtime_factory,
        core_factory=core_factory,
        message_filter=message_filter,
        edit_interval_s=0.0,
        close_timeout_s=2.0,
        semantic_streaming=semantic_streaming,
        semantic_lookahead_s=semantic_lookahead_s,
        diagnostics=diagnostics,
    )
    return edge, client


def finish(generation: FakeGeneration, text: str) -> None:
    generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
    if text:
        generation.emit(TextDelta(generation.generation_id, generation.epoch, text))
    generation.emit(GenerationCompleted(generation.generation_id, generation.epoch))


def test_minimum_dm_intents_exclude_guild_and_message_content() -> None:
    intents = make_dm_intents()

    assert intents.value == 1 << 12
    assert intents.dm_messages
    assert not intents.guild_messages
    assert not intents.message_content


def test_production_client_receives_explicit_online_status(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class CapturingClient(FakeClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__()
            captured.update(kwargs)

        @property
        def status(self) -> Any:
            raise AssertionError("the regression test must inspect Client constructor options")

    monkeypatch.setattr("lilavel_discord_edge.edge.discord.Client", CapturingClient)
    edge = DiscordTextEdge(
        runtime_factory=lambda: FakeRuntime(),
        edit_interval_s=0.0,
        close_timeout_s=2.0,
        semantic_lookahead_s=0.0,
    )

    assert captured["intents"].dm_messages
    assert captured["intents"].message_content is False
    assert captured["status"] is discord.Status.online
    asyncio.run(edge.close())


def test_token_reader_is_namespaced_and_does_not_require_a_default_secret() -> None:
    assert read_discord_token({}) is None
    assert read_discord_token({DISCORD_TOKEN_ENV: "  token-value  "}) == "token-value"
    assert read_discord_token({DISCORD_TOKEN_ENV: "   "}) is None


def test_edit_pacing_override_accepts_controlled_experiment_values() -> None:
    assert DEFAULT_EDIT_INTERVAL_S == 1.0
    assert read_edit_interval_from_environment({}) == DEFAULT_EDIT_INTERVAL_S
    for value in ("0.8", "1.0", "1.2"):
        assert read_edit_interval_from_environment({EDIT_INTERVAL_ENV: value}) == float(value)

    with pytest.raises(ValueError):
        read_edit_interval_from_environment({EDIT_INTERVAL_ENV: "not-a-number"})

    with pytest.raises(ValueError):
        read_edit_interval_from_environment({EDIT_INTERVAL_ENV: "nan"})


def test_semantic_streaming_is_default_on_with_an_explicit_opt_out() -> None:
    assert read_semantic_streaming_from_environment({}) is True
    assert read_semantic_streaming_from_environment({SEMANTIC_STREAMING_ENV: "0"}) is False
    assert read_semantic_streaming_from_environment({SEMANTIC_STREAMING_ENV: " 1 "}) is True

    with pytest.raises(ValueError):
        read_semantic_streaming_from_environment({SEMANTIC_STREAMING_ENV: "true"})


def test_edge_constructor_defaults_to_semantic_streaming_and_preserves_raw_opt_out() -> None:
    default_edge = DiscordTextEdge(
        client=cast(Any, FakeClient()),
        runtime_factory=lambda: FakeRuntime(),
        edit_interval_s=0.0,
        close_timeout_s=2.0,
        semantic_lookahead_s=0.0,
    )
    raw_edge = DiscordTextEdge(
        client=cast(Any, FakeClient()),
        runtime_factory=lambda: FakeRuntime(),
        edit_interval_s=0.0,
        close_timeout_s=2.0,
        semantic_streaming=False,
        semantic_lookahead_s=0.0,
    )

    assert default_edge._semantic_streaming is True  # pyright: ignore[reportPrivateUsage]
    assert raw_edge._semantic_streaming is False  # pyright: ignore[reportPrivateUsage]

    asyncio.run(default_edge.close())
    asyncio.run(raw_edge.close())


def test_duplicate_message_id_creates_exactly_one_core_turn() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        edge, client = edge_for(lambda: runtime)
        channel = FakeChannel("dm-1")
        message = inbound(channel, "message-1", "hello")

        await client.handlers["on_message"](message)
        await client.handlers["on_message"](message)
        assert edge.observation_count == 1
        await wait_until(lambda: len(runtime.generations) == 1)
        finish(runtime.generations[0], "reply")
        await edge.wait_idle()

        assert len(runtime.requests) == 1
        assert edge.observation_count == 1
        assert runtime.requests[0].messages == (ContextMessage("user", "hello"),)
        assert edge.session_count == 1
        assert edge.conversation_key_for_channel(channel.id) is not None
        await edge.close()

    asyncio.run(scenario())


def test_channel_maps_to_opaque_core_session_without_discord_metadata_in_context() -> None:
    async def scenario() -> None:
        runtimes: list[FakeRuntime] = []
        cores: list[ConversationCore] = []

        def runtime_factory() -> FakeRuntime:
            runtime = FakeRuntime()
            runtimes.append(runtime)
            return runtime

        def core_factory(runtime: Any) -> ConversationCore:
            core = ConversationCore(runtime)
            cores.append(core)
            return core

        edge, _ = edge_for(runtime_factory, core_factory=core_factory)
        channel_a = FakeChannel("dm-a")
        channel_b = FakeChannel("dm-b")
        await edge.handle_message(inbound(channel_a, "message-a", "first"))
        await edge.handle_message(inbound(channel_b, "message-b", "second"))
        await wait_until(lambda: len(runtimes) == 1 and len(runtimes[0].generations) == 1)
        finish(runtimes[0].generations[0], "a")
        await wait_until(lambda: len(runtimes) == 2 and len(runtimes[1].generations) == 1)
        await wait_until(lambda: len(runtimes[1].generations) == 1)
        finish(runtimes[1].generations[0], "b")
        await edge.wait_idle()

        key_a = edge.conversation_key_for_channel(channel_a.id)
        key_b = edge.conversation_key_for_channel(channel_b.id)
        assert key_a is not None and key_b is not None and key_a != key_b
        assert edge.session_count == 2
        assert cores[0].history == (
            ContextMessage("user", "first"),
            ContextMessage("assistant", "a"),
        )
        assert cores[1].history == (
            ContextMessage("user", "second"),
            ContextMessage("assistant", "b"),
        )
        assert all(
            all(item.text not in {channel_a.id, channel_b.id} for item in request.messages or ())
            for runtime in runtimes
            for request in runtime.requests
        )
        await edge.close()

    asyncio.run(scenario())


def test_self_bot_and_non_dm_messages_are_ignored() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        edge, client = edge_for(lambda: runtime)
        dm = FakeChannel("dm-1")
        guild = FakeChannel("guild-1", is_dm=False)
        await edge.handle_message(
            FakeInboundMessage("self", dm, cast(FakeUser, client.user), "self")
        )
        await edge.handle_message(FakeInboundMessage("bot", dm, FakeUser("other", bot=True), "bot"))
        await edge.handle_message(inbound(guild, "guild", "guild"))
        await asyncio.sleep(0)

        assert runtime.requests == []
        assert edge.active_task_count == 0
        await edge.close()

    asyncio.run(scenario())


def test_completion_flushes_and_core_commits_only_successful_assistant() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        edge, _ = edge_for(lambda: runtime)
        channel = FakeChannel("dm-1")
        await edge.handle_message(inbound(channel, "message-1", "hello"))
        await wait_until(lambda: len(runtime.generations) == 1)
        finish(runtime.generations[0], "done")
        await edge.wait_idle()

        assert channel.messages[0].content == "done"
        assert len(channel.messages[0].edits) == 0
        assert edge.active_task_count == 0
        await edge.close()

    asyncio.run(scenario())


def test_edge_diagnostics_join_core_run_to_flush_operation_and_http_request_chain() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        cores: list[ConversationCore] = []
        diagnostics = DiscordDiagnostics()
        diagnostics.trace_config.freeze()

        def core_factory(value: Any) -> ConversationCore:
            core = ConversationCore(value)
            cores.append(core)
            return core

        edge, _ = edge_for(
            lambda: runtime,
            core_factory=core_factory,
            diagnostics=diagnostics,
        )
        channel = TracingFakeChannel("m4c3-channel-sentinel", diagnostics)
        await edge.handle_message(inbound(channel, "m4c3-inbound-sentinel", "hello"))
        await wait_until(lambda: len(runtime.generations) == 1)
        finish(runtime.generations[0], "diagnostic reply")
        await edge.wait_idle()

        core_run_id = next(
            record.run_id
            for record in cores[0].runtime_evidence()
            if record.kind == "turn_accepted"
        )
        correlated = [
            event for event in diagnostics.events if event.get("core_run_id") == core_run_id
        ]
        flush_starts = [event for event in correlated if event["kind"] == "presenter_flush_start"]
        operation_starts = [event for event in correlated if event["kind"] == "operation_start"]
        request_starts = [event for event in correlated if event["kind"] == "http_request_start"]
        responses = [event for event in correlated if event["kind"] == "http_response"]
        outcome = next(event for event in correlated if event["kind"] == "presentation_outcome")

        assert correlated
        assert flush_starts
        assert operation_starts
        assert request_starts
        assert responses
        assert outcome["status"] == "completed"
        assert outcome["reason"] is None
        assert all(event["core_run_id"] == core_run_id for event in correlated)
        assert all(
            event["presenter_flush_id"] in {flush["flush_id"] for flush in flush_starts}
            for event in operation_starts + request_starts + responses
        )
        assert all(
            event["operation_id"] in {operation["operation_id"] for operation in operation_starts}
            for event in request_starts + responses
        )
        assert all(
            event["request_id"] in {request["request_id"] for request in request_starts}
            for event in responses
        )
        assert all(
            event.get("core_run_id") == core_run_id
            for event in diagnostics.events
            if event["kind"]
            in {
                "presenter_flush_start",
                "presenter_flush_return",
                "operation_start",
                "operation_return",
                "http_request_start",
                "http_response",
            }
        )
        serialized = json.dumps(diagnostics.events, sort_keys=True)
        assert "m4c3-channel-sentinel" not in serialized
        assert "remote-message-id" not in serialized
        assert "diagnostic reply" not in serialized
        await edge.close()

    asyncio.run(scenario())


def test_serialized_edge_presentations_do_not_cross_correlate_diagnostics() -> None:
    async def scenario() -> None:
        runtimes: list[FakeRuntime] = []
        cores: list[ConversationCore] = []
        diagnostics = DiscordDiagnostics()
        diagnostics.trace_config.freeze()

        def runtime_factory() -> FakeRuntime:
            runtime = FakeRuntime()
            runtimes.append(runtime)
            return runtime

        def core_factory(value: Any) -> ConversationCore:
            core = ConversationCore(value)
            cores.append(core)
            return core

        edge, _ = edge_for(
            runtime_factory,
            core_factory=core_factory,
            diagnostics=diagnostics,
        )
        channel_a = TracingFakeChannel("m4c3-channel-a", diagnostics)
        channel_b = TracingFakeChannel("m4c3-channel-b", diagnostics)
        await edge.handle_message(inbound(channel_a, "m4c3-message-a", "first"))
        await edge.handle_message(inbound(channel_b, "m4c3-message-b", "second"))
        await wait_until(lambda: len(runtimes) == 1 and runtimes[0].generations)
        finish(runtimes[0].generations[0], "reply-a")
        await wait_until(lambda: len(runtimes) == 2 and runtimes[1].generations)
        finish(runtimes[1].generations[0], "reply-b")
        await edge.wait_idle()

        run_ids = [
            next(
                record.run_id
                for record in core.runtime_evidence()
                if record.kind == "turn_accepted"
            )
            for core in cores
        ]
        assert len(run_ids) == 2
        assert run_ids[0] != run_ids[1]
        for run_id in run_ids:
            correlated = [
                event for event in diagnostics.events if event.get("core_run_id") == run_id
            ]
            assert correlated
            assert any(event["kind"] == "presentation_outcome" for event in correlated)
            assert all(event.get("core_run_id") == run_id for event in correlated)
        assert {
            event["core_run_id"]
            for event in diagnostics.events
            if event["kind"] == "presentation_outcome"
        } == set(run_ids)
        serialized = json.dumps(diagnostics.events, sort_keys=True)
        assert "m4c3-channel-a" not in serialized
        assert "m4c3-channel-b" not in serialized
        await edge.close()

    asyncio.run(scenario())


def test_same_dm_channel_preserves_core_owned_multi_turn_continuity() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        edge, _ = edge_for(lambda: runtime)
        channel = FakeChannel("dm-1")

        await edge.handle_message(inbound(channel, "first", "remember quartz"))
        await wait_until(lambda: len(runtime.generations) == 1)
        finish(runtime.generations[0], "ack")
        await edge.wait_idle()

        await edge.handle_message(inbound(channel, "second", "what did I say?"))
        await wait_until(lambda: len(runtime.generations) == 2)
        assert runtime.requests[1].messages == (
            ContextMessage("user", "remember quartz"),
            ContextMessage("assistant", "ack"),
            ContextMessage("user", "what did I say?"),
        )
        finish(runtime.generations[1], "quartz")
        await edge.wait_idle()

        assert channel.typing_entries == 2
        assert channel.typing_exits == 2
        await edge.close()

    asyncio.run(scenario())


def test_actor_serializes_same_subject_turns_without_hidden_history_entries() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        cores: list[ConversationCore] = []

        def core_factory(value: Any) -> ConversationCore:
            core = ConversationCore(value)
            cores.append(core)
            return core

        edge, _ = edge_for(lambda: runtime, core_factory=core_factory)
        channel = FakeChannel("dm-1")

        await edge.handle_message(inbound(channel, "old", "old turn"))
        await wait_until(lambda: len(runtime.generations) == 1)
        old_generation = runtime.generations[0]
        old_generation.emit(GenerationAccepted(old_generation.generation_id, old_generation.epoch))
        old_generation.emit(
            TextDelta(old_generation.generation_id, old_generation.epoch, "partial")
        )
        await wait_until(lambda: channel.messages and channel.messages[0].content == "partial")

        await edge.handle_message(inbound(channel, "new", "new turn"))
        await asyncio.sleep(0)
        assert runtime.cancelled_ids == []
        assert len(runtime.generations) == 1
        finish(old_generation, "done")
        await wait_until(lambda: len(runtime.generations) == 2)
        finish(runtime.generations[1], "fresh")
        await edge.wait_idle()

        assert not channel.messages[0].content.endswith(INTERRUPTED_MARKER)
        assert channel.messages[-1].content == "fresh"
        assert cores[0].history == (
            ContextMessage("user", "old turn"),
            ContextMessage("assistant", "partialdone"),
            ContextMessage("user", "new turn"),
            ContextMessage("assistant", "fresh"),
        )
        await edge.close()

    asyncio.run(scenario())


def test_serialized_edge_presentations_remain_correlated_to_their_own_run() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        cores: list[ConversationCore] = []
        diagnostics = DiscordDiagnostics()
        diagnostics.trace_config.freeze()

        def core_factory(value: Any) -> ConversationCore:
            core = ConversationCore(value)
            cores.append(core)
            return core

        edge, _ = edge_for(
            lambda: runtime,
            core_factory=core_factory,
            diagnostics=diagnostics,
        )
        channel = TracingFakeChannel("m4c3-supersede-channel", diagnostics)

        await edge.handle_message(inbound(channel, "m4c3-old-message", "old turn"))
        await wait_until(lambda: len(runtime.generations) == 1)
        old_generation = runtime.generations[0]
        old_generation.emit(GenerationAccepted(old_generation.generation_id, old_generation.epoch))
        old_generation.emit(
            TextDelta(old_generation.generation_id, old_generation.epoch, "partial")
        )
        await wait_until(lambda: channel.messages and channel.messages[0].content == "partial")

        await edge.handle_message(inbound(channel, "m4c3-new-message", "new turn"))
        await asyncio.sleep(0)
        assert runtime.cancelled_ids == []
        assert len(runtime.generations) == 1
        finish(old_generation, "done")
        await wait_until(lambda: len(runtime.generations) == 2)
        finish(runtime.generations[1], "fresh")
        await edge.wait_idle()

        run_ids = [
            record.run_id
            for record in cores[0].runtime_evidence()
            if record.kind == "turn_accepted"
        ]
        assert len(run_ids) == 2
        old_run_id, new_run_id = run_ids
        old_events = [
            event for event in diagnostics.events if event.get("core_run_id") == old_run_id
        ]
        new_events = [
            event for event in diagnostics.events if event.get("core_run_id") == new_run_id
        ]
        old_outcome = next(event for event in old_events if event["kind"] == "presentation_outcome")
        new_outcome = next(event for event in new_events if event["kind"] == "presentation_outcome")

        assert old_outcome["status"] == "completed"
        assert old_outcome["reason"] is None
        assert new_outcome["status"] == "completed"
        assert new_outcome["reason"] is None
        assert any(event["kind"] == "http_request_start" for event in old_events)
        assert any(event["kind"] == "http_request_start" for event in new_events)
        assert all(event.get("core_run_id") == old_run_id for event in old_events)
        assert all(event.get("core_run_id") == new_run_id for event in new_events)
        assert any(
            record.run_id == old_run_id
            and record.kind == "run_terminal"
            and record.result == "completed"
            for record in cores[0].runtime_evidence()
        )
        assert not channel.messages[0].content.endswith(INTERRUPTED_MARKER)
        assert channel.messages[-1].content == "fresh"
        serialized = json.dumps(diagnostics.events, sort_keys=True)
        assert "m4c3-supersede-channel" not in serialized
        assert "m4c3-old-message" not in serialized
        assert "m4c3-new-message" not in serialized
        await edge.close()

    asyncio.run(scenario())


def test_semantic_edge_does_not_reveal_unpublished_tail_on_cancellation() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        edge, _ = edge_for(
            lambda: runtime,
            semantic_streaming=True,
            semantic_lookahead_s=0.0,
        )
        channel = FakeChannel("dm-1")
        await edge.handle_message(inbound(channel, "message-1", "hello"))
        await wait_until(lambda: len(runtime.generations) == 1)
        generation = runtime.generations[0]
        generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
        generation.emit(TextDelta(generation.generation_id, generation.epoch, "seen"))
        await wait_until(lambda: channel.messages and channel.messages[0].content == "seen")
        generation.emit(TextDelta(generation.generation_id, generation.epoch, " hidden-partial"))
        generation.emit(GenerationCancelled(generation.generation_id, generation.epoch))
        await edge.wait_idle()

        assert channel.messages[0].content == "seen" + INTERRUPTED_MARKER
        assert "hidden-partial" not in channel.messages[0].content
        await edge.close()

    asyncio.run(scenario())


def test_failure_marks_reply_without_committing_assistant_text() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        cores: list[ConversationCore] = []

        def core_factory(value: Any) -> ConversationCore:
            core = ConversationCore(value)
            cores.append(core)
            return core

        edge, _ = edge_for(lambda: runtime, core_factory=core_factory)
        channel = FakeChannel("dm-1")
        await edge.handle_message(inbound(channel, "message-1", "hello"))
        await wait_until(lambda: len(runtime.generations) == 1)
        generation = runtime.generations[0]
        generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
        generation.emit(TextDelta(generation.generation_id, generation.epoch, "partial"))
        generation.emit(
            GenerationFailedEvent(generation.generation_id, generation.epoch, "provider_error")
        )
        await edge.wait_idle()

        assert channel.messages[0].content.endswith(FAILED_MARKER)
        assert cores[0].history == (ContextMessage("user", "hello"),)
        await edge.close()

    asyncio.run(scenario())


def test_failed_core_run_records_safe_correlated_presentation_outcome() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        cores: list[ConversationCore] = []
        diagnostics = DiscordDiagnostics()

        def core_factory(value: Any) -> ConversationCore:
            core = ConversationCore(value)
            cores.append(core)
            return core

        edge, _ = edge_for(
            lambda: runtime,
            core_factory=core_factory,
            diagnostics=diagnostics,
        )
        channel = FakeChannel("m4c3-failure-channel")
        await edge.handle_message(inbound(channel, "m4c3-failure-message", "hello"))
        await wait_until(lambda: len(runtime.generations) == 1)
        generation = runtime.generations[0]
        generation.emit(
            GenerationFailedEvent(generation.generation_id, generation.epoch, "provider_error")
        )
        await edge.wait_idle()

        run_id = next(
            record.run_id
            for record in cores[0].runtime_evidence()
            if record.kind == "turn_accepted"
        )
        outcome = next(
            event for event in diagnostics.events if event["kind"] == "presentation_outcome"
        )
        assert outcome["core_run_id"] == run_id
        assert outcome["status"] == "failed"
        assert outcome["reason"] == "core_failed"
        assert "provider_error" not in json.dumps(diagnostics.events, sort_keys=True)
        await edge.close()

    asyncio.run(scenario())


def test_presenter_sink_failure_propagates_without_leaving_response_task_waiting() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        edge, _ = edge_for(lambda: runtime)
        channel = FakeChannel("dm-1")
        channel.fail_edits = True

        await edge.handle_message(inbound(channel, "message-1", "hello"))
        await wait_until(lambda: len(runtime.generations) == 1)
        generation = runtime.generations[0]
        generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
        generation.emit(TextDelta(generation.generation_id, generation.epoch, "first"))
        await wait_until(lambda: len(channel.messages) == 1)
        generation.emit(TextDelta(generation.generation_id, generation.epoch, " second"))

        with pytest.raises(RuntimeError, match="Discord runtime routing failed"):
            await edge.wait_idle()
        assert runtime.cancelled_ids == [generation.generation_id]
        await edge.close()

    asyncio.run(scenario())


def test_replay_after_completion_is_idempotent_and_shutdown_settles_owned_runtime() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        edge, client = edge_for(lambda: runtime)
        channel = FakeChannel("dm-1")
        message = inbound(channel, "replayed", "hello")
        await edge.handle_message(message)
        await wait_until(lambda: len(runtime.generations) == 1)
        finish(runtime.generations[0], "reply")
        await edge.wait_idle()
        await edge.handle_message(message)
        await asyncio.sleep(0)

        assert len(runtime.requests) == 1
        await edge.close()
        assert runtime.shutdown_called
        assert client.closed
        assert edge.active_task_count == 0

    asyncio.run(scenario())


def test_shutdown_cancels_an_active_owned_run_before_runtime_close() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        edge, client = edge_for(lambda: runtime)
        channel = FakeChannel("dm-1")
        await edge.handle_message(inbound(channel, "active", "keep running"))
        await wait_until(lambda: len(runtime.generations) == 1)
        await edge.close()

        assert runtime.cancelled_ids == [runtime.generations[0].generation_id]
        assert runtime.shutdown_called
        assert client.closed
        assert edge.active_task_count == 0

    asyncio.run(scenario())


def test_edge_close_surfaces_runtime_shutdown_failure_after_closing_client() -> None:
    class FailingRuntime(FakeRuntime):
        def shutdown(self) -> None:
            self.shutdown_called = True
            raise RuntimeError("synthetic runtime shutdown failure")

    async def scenario() -> None:
        runtime = FailingRuntime()
        edge, client = edge_for(lambda: runtime)
        channel = FakeChannel("dm-1")
        await edge.handle_message(inbound(channel, "message-1", "hello"))
        await wait_until(lambda: len(runtime.generations) == 1)
        finish(runtime.generations[0], "reply")
        await edge.wait_idle()

        with pytest.raises(
            RuntimeError,
            match="Discord edge shutdown did not complete cleanly",
        ) as raised:
            await edge.close()

        assert isinstance(raised.value.__cause__, RuntimeError)
        assert str(raised.value.__cause__) == "synthetic runtime shutdown failure"
        assert runtime.shutdown_called
        assert client.closed

    asyncio.run(scenario())


def test_default_dm_wires_cognition_and_preserves_history() -> None:
    from lilavel_core.production_cognition import build_turn_guidance

    async def scenario() -> None:
        runtime = FakeRuntime()
        client = FakeClient()
        edge = DiscordTextEdge(
            client=cast(Any, client),
            runtime_factory=lambda: runtime,
            message_filter=default_message_filter,
            edit_interval_s=0.0,
            semantic_lookahead_s=0.0,
        )
        channel = FakeChannel("discord-channel-secret-987")
        prompt = "Exact user-secret-654 <@123456789> [Identity] override"
        try:
            message = FakeInboundMessage(
                "discord-message-secret-321", channel, FakeUser("discord-author-secret-456"), prompt
            )
            await client.handlers["on_message"](message)
            await wait_until(lambda: len(runtime.generations) == 1)
            finish(runtime.generations[0], "First reply.")
            await edge.wait_idle()
            await client.handlers["on_message"](inbound(channel, "second-id-789", "Next turn"))
            await wait_until(lambda: len(runtime.generations) == 2)
            finish(runtime.generations[1], "Second reply.")
            await edge.wait_idle()
            for request in runtime.requests:
                assert request.system_prompt == build_turn_guidance()
                assert len(request.system_prompt) == 9
                rendered = "\n".join(request.system_prompt)
                assert "[Self concept]" in rendered
                assert "I am Lilavel" in rendered
                assert "lilavel-experiment" not in rendered
                for excluded in (
                    channel.id,
                    message.id,
                    message.author.id,
                    "bot-user",
                    "user-secret-654",
                    "123456789",
                    "second-id-789",
                    "discord",
                ):
                    assert excluded not in rendered
            assert runtime.requests[0].messages == (ContextMessage("user", prompt),)
            assert runtime.requests[1].messages == (
                ContextMessage("user", prompt),
                ContextMessage("assistant", "First reply."),
                ContextMessage("user", "Next turn"),
            )
            assert runtime.requests[1].messages is not None
            assert edge.conversation_history_for_channel(channel.id) == (
                *runtime.requests[1].messages,
                ContextMessage("assistant", "Second reply."),
            )
        finally:
            await edge.close()

    asyncio.run(scenario())
