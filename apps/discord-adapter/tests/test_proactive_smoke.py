"""Deterministic proofs for PROACTIVE-V0 Discord one-shot initiative."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from queue import Queue
from typing import Any, cast

import pytest
from lilavel_core import (
    ContextMessage,
    GenerationAccepted,
    GenerationCancelled,
    GenerationCompleted,
    GenerationEvent,
    GenerationFailedEvent,
    ModelRequest,
    ModelRuntimeV3,
    TextDelta,
)

from lilavel_discord_edge import (
    DEFAULT_PROACTIVE_IDLE_S,
    PROACTIVE_DIAGNOSTICS_ENV,
    PROACTIVE_IDLE_ENV,
    PROACTIVE_SMOKE_ENV,
    DiscordProactiveDiagnostics,
    DiscordTextEdge,
    read_proactive_diagnostics_from_environment,
    read_proactive_idle_from_environment,
    read_proactive_smoke_from_environment,
)
from test_edge import FakeChannel, FakeClient, FakeInboundMessage, FakeUser


class _Generation:
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
            if isinstance(event, (GenerationCompleted, GenerationCancelled)):
                return


class _UserRuntime:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.generations: list[_Generation] = []
        self.cancelled: list[str] = []
        self.started = False
        self.shutdown_called = False

    def start(self) -> None:
        self.started = True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def generate(self, request: ModelRequest) -> _Generation:
        self.requests.append(request)
        generation = _Generation(f"user-{len(self.generations) + 1}", 1)
        self.generations.append(generation)
        return generation

    def cancel(self, generation_id: str) -> bool:
        self.cancelled.append(generation_id)
        generation = next(item for item in self.generations if item.generation_id == generation_id)
        generation.emit(GenerationCancelled(generation.generation_id, generation.epoch))
        return True


class _CognitionRuntime(_UserRuntime):
    def generate_for_run(
        self,
        request: ModelRequest,
        *,
        scope_id: str,
        logical_run_id: str,
    ) -> _Generation:
        del scope_id, logical_run_id
        return self.generate(request)


def _finish(generation: _Generation, text: str) -> None:
    generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
    if text:
        generation.emit(TextDelta(generation.generation_id, generation.epoch, text))
    generation.emit(GenerationCompleted(generation.generation_id, generation.epoch))


async def _wait_until(check: Any, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.001)


def _edge(
    cognition: _CognitionRuntime,
    user: _UserRuntime,
    diagnostics: DiscordProactiveDiagnostics | None = None,
) -> DiscordTextEdge:
    client = FakeClient()
    return DiscordTextEdge(
        client=cast(Any, client),
        runtime_factory=lambda: user,
        proactive_runtime_factory=lambda: cognition,
        message_filter=lambda message: bool(message.channel.is_dm),
        edit_interval_s=0.0,
        semantic_lookahead_s=0.0,
        close_timeout_s=2.0,
        proactive_diagnostics=diagnostics,
    )


def _set_enabled(monkeypatch: pytest.MonkeyPatch, value: str = "1") -> None:
    monkeypatch.setenv(PROACTIVE_SMOKE_ENV, value)
    monkeypatch.setenv(PROACTIVE_IDLE_ENV, "1.0")


def test_proactive_configuration_is_opt_in_and_bounded() -> None:
    assert read_proactive_diagnostics_from_environment({}) is False
    assert read_proactive_diagnostics_from_environment({PROACTIVE_DIAGNOSTICS_ENV: "0"}) is False
    assert read_proactive_diagnostics_from_environment({PROACTIVE_DIAGNOSTICS_ENV: " 1 "}) is True
    assert read_proactive_smoke_from_environment({}) is False
    assert read_proactive_idle_from_environment({}) == DEFAULT_PROACTIVE_IDLE_S
    assert read_proactive_smoke_from_environment({PROACTIVE_SMOKE_ENV: " 1 "}) is True
    assert read_proactive_smoke_from_environment({PROACTIVE_SMOKE_ENV: "0"}) is False
    assert read_proactive_idle_from_environment({PROACTIVE_IDLE_ENV: "1"}) == 1.0
    assert read_proactive_idle_from_environment({PROACTIVE_IDLE_ENV: "600"}) == 600.0


def test_invalid_diagnostics_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        read_proactive_diagnostics_from_environment({PROACTIVE_DIAGNOSTICS_ENV: "true"})


def test_diagnostics_are_opt_in_content_free_and_sink_failure_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled(monkeypatch)
    emitted: list[dict[str, object]] = []

    def emit(event: Any) -> None:
        emitted.append(dict(event))

    diagnostics = DiscordProactiveDiagnostics(emit=emit)
    cognition = _CognitionRuntime()
    user = _UserRuntime()
    edge = _edge(cognition, user, diagnostics)

    async def scenario() -> None:
        channel = FakeChannel("diagnostic-target")
        await edge.handle_message(
            FakeInboundMessage(
                "diagnostic-message",
                channel,
                FakeUser("human"),
                "USER_CONTENT_SENTINEL",
            )
        )
        await _wait_until(lambda: len(user.generations) == 1)
        _finish(user.generations[0], "MODEL_CONTENT_SENTINEL")
        await _wait_until(lambda: len(cognition.generations) == 1)
        _finish(
            cognition.generations[0],
            '{"action":"create_intention","text":"INTENTION_CONTENT_SENTINEL"}',
        )
        await _wait_until(lambda: len(cognition.generations) == 2, timeout=2.5)
        _finish(cognition.generations[1], '{"action":"speak","text":"SPEECH_CONTENT_SENTINEL"}')
        await _wait_until(
            lambda: any(item.kind == "send_confirmed" for item in edge.proactive_evidence())
        )
        await edge.close()

    asyncio.run(scenario())

    assert emitted[0] == {
        "component": "proactive",
        "sequence": 1,
        "kind": "startup",
        "proactive_enabled": True,
        "configured_idle_interval_s": 1.0,
        "diagnostics_enabled": True,
        "cognition_tools_exposed": False,
    }
    assert [event["sequence"] for event in emitted] == list(range(1, len(emitted) + 1))
    rendered = json.dumps(emitted, sort_keys=True)
    assert all(
        sentinel not in rendered
        for sentinel in (
            "USER_CONTENT_SENTINEL",
            "MODEL_CONTENT_SENTINEL",
            "INTENTION_CONTENT_SENTINEL",
            "SPEECH_CONTENT_SENTINEL",
        )
    )

    def fail_emit(event: Any) -> None:
        del event
        raise RuntimeError("diagnostics sink failed")

    failing_diagnostics = DiscordProactiveDiagnostics(emit=fail_emit)
    failing_cognition = _CognitionRuntime()
    failing_user = _UserRuntime()
    failing_edge = _edge(failing_cognition, failing_user, failing_diagnostics)

    async def failure_sink_scenario() -> None:
        channel = FakeChannel("sink-failure-target")
        await failing_edge.handle_message(
            FakeInboundMessage("sink-failure", channel, FakeUser("human"), "one")
        )
        await _wait_until(lambda: len(failing_user.generations) == 1)
        _finish(failing_user.generations[0], "reply")
        await _wait_until(lambda: len(failing_cognition.generations) == 1)
        _finish(failing_cognition.generations[0], '{"action":"no_change"}')
        await failing_edge.close()

    asyncio.run(failure_sink_scenario())


def test_enabled_default_composes_run_aware_proactive_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled(monkeypatch)
    edge = DiscordTextEdge(
        client=cast(Any, FakeClient()),
        runtime_factory=_UserRuntime,
        message_filter=lambda message: bool(message.channel.is_dm),
        close_timeout_s=2.0,
    )

    async def scenario() -> None:
        assert edge.proactive_smoke_enabled
        await edge.close()

    asyncio.run(scenario())


def test_production_shape_proactive_v3_exposes_no_tools_and_keeps_effect_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled(monkeypatch)
    fixture = Path(__file__).parent / "fixtures" / "proactive_cognition_v3_sidecar.py"
    cognition = ModelRuntimeV3(
        command=(sys.executable, str(fixture)),
        sidecar_dir=fixture.parent,
        startup_timeout=2.0,
        shutdown_timeout=2.0,
        cancellation_timeout=1.0,
    )
    user = _UserRuntime()
    edge = DiscordTextEdge(
        client=cast(Any, FakeClient()),
        runtime_factory=lambda: user,
        proactive_runtime_factory=lambda: cognition,
        message_filter=lambda message: bool(message.channel.is_dm),
        edit_interval_s=0.0,
        semantic_lookahead_s=0.0,
        close_timeout_s=2.0,
    )

    async def scenario() -> None:
        channel = FakeChannel("production-shape")
        await edge.handle_message(
            FakeInboundMessage("production-shape-message", channel, FakeUser("human"), "unfinished")
        )
        await _wait_until(lambda: len(user.generations) == 1)
        _finish(user.generations[0], "reply")
        await _wait_until(
            lambda: any(
                item.kind == "appraisal_parse_create_intention"
                for item in edge.proactive_evidence()
            ),
            timeout=4.0,
        )
        await _wait_until(
            lambda: any(item.kind == "send_confirmed" for item in edge.proactive_evidence()),
            timeout=4.0,
        )
        kinds = [item.kind for item in edge.proactive_evidence()]
        assert {
            "appraisal_generation_completed",
            "appraisal_parse_create_intention",
            "idle_generation_completed",
            "idle_parse_speak",
            "cognition_submitted",
            "speech_allowed",
            "send_confirmed",
        }.issubset(kinds)
        assert [item["content"] for item in channel.sends] == ["reply", "one follow-up"]
        effect_snapshots = edge.tool_proof_evidence()
        assert len(effect_snapshots) == 1
        assert effect_snapshots[0]["exposed_tools"] == ("discord.send_message",)
        assert effect_snapshots[0]["send_attempt_count"] == 1
        await edge.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "environment",
    [
        {PROACTIVE_SMOKE_ENV: "true"},
        {PROACTIVE_SMOKE_ENV: "2"},
    ],
)
def test_invalid_smoke_configuration_fails_before_client_creation(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        read_proactive_smoke_from_environment(environment)


@pytest.mark.parametrize("value", ["0", "0.9", "600.1", "nan", "inf", "not-a-number"])
def test_invalid_idle_configuration_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        read_proactive_idle_from_environment({PROACTIVE_IDLE_ENV: value})


def test_edge_rejects_invalid_configuration_before_discord_client_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PROACTIVE_SMOKE_ENV, "1")
    monkeypatch.setenv(PROACTIVE_IDLE_ENV, "0")
    started = False

    def client_factory(**kwargs: object) -> FakeClient:
        del kwargs
        nonlocal started
        started = True
        return FakeClient()

    monkeypatch.setattr("lilavel_discord_edge.edge.discord.Client", client_factory)
    with pytest.raises(ValueError):
        DiscordTextEdge()
    assert not started


def test_smoke_off_preserves_reactive_behavior_and_creates_no_idle_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PROACTIVE_SMOKE_ENV, "0")
    user = _UserRuntime()
    edge = DiscordTextEdge(
        client=cast(Any, FakeClient()),
        runtime_factory=lambda: user,
        edit_interval_s=0.0,
        semantic_lookahead_s=0.0,
        close_timeout_s=2.0,
        message_filter=lambda message: bool(message.channel.is_dm),
    )

    async def scenario() -> None:
        assert not edge.proactive_smoke_enabled
        assert not edge.proactive_timer_active
        channel = FakeChannel("off-channel")
        await edge.handle_message(
            FakeInboundMessage("off-message", channel, FakeUser("human"), "hi")
        )
        await _wait_until(lambda: len(user.generations) == 1)
        _finish(user.generations[0], "reactive")
        await edge.wait_idle()
        await asyncio.sleep(0.02)
        assert len(user.generations) == 1
        assert channel.messages[-1].content == "reactive"
        await edge.close()

    asyncio.run(scenario())


def test_successful_turn_reuses_appraisal_idle_actor_and_trusted_dm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled(monkeypatch)
    cognition = _CognitionRuntime()
    user = _UserRuntime()
    edge = _edge(cognition, user)

    async def scenario() -> None:
        channel = FakeChannel("trusted-channel")
        await edge.handle_message(
            FakeInboundMessage("inbound-1", channel, FakeUser("human"), "unfinished matter")
        )
        await _wait_until(lambda: len(user.generations) == 1)
        _finish(user.generations[0], "acknowledged")
        await _wait_until(lambda: len(cognition.generations) == 1)
        _finish(cognition.generations[0], '{"action":"create_intention","text":"check later"}')
        await _wait_until(
            lambda: any(item.kind == "idle_armed" for item in edge.proactive_evidence())
        )
        await _wait_until(lambda: len(cognition.generations) == 2, timeout=2.5)
        assert "trusted-channel" not in str(cognition.requests[0])
        assert "trusted-channel" not in str(cognition.requests[1])
        _finish(cognition.generations[1], '{"action":"speak","text":"one useful follow-up"}')
        await _wait_until(
            lambda: any(item.kind == "send_confirmed" for item in edge.proactive_evidence())
        )
        assert [item["content"] for item in channel.sends] == [
            "acknowledged",
            "one useful follow-up",
        ]
        assert edge.conversation_history_for_channel(channel.id) == (
            ContextMessage("user", "unfinished matter"),
            ContextMessage("assistant", "acknowledged"),
        )
        assert edge.proactive_target_bound
        assert not edge.proactive_target_disabled
        assert [item.kind for item in edge.proactive_evidence()].count("idle_armed") == 1
        assert len(cognition.generations) == 2
        await edge.close()

    asyncio.run(scenario())


def test_no_intention_means_no_idle_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enabled(monkeypatch)
    cognition = _CognitionRuntime()
    user = _UserRuntime()
    edge = _edge(cognition, user)

    async def scenario() -> None:
        channel = FakeChannel("quiet-channel")
        await edge.handle_message(FakeInboundMessage("quiet", channel, FakeUser("human"), "closed"))
        await _wait_until(lambda: len(user.generations) == 1)
        _finish(user.generations[0], "done")
        await _wait_until(lambda: len(cognition.generations) == 1)
        _finish(cognition.generations[0], '{"action":"no_change"}')
        await asyncio.sleep(1.1)
        assert len(cognition.generations) == 1
        assert not edge.proactive_timer_active
        assert any(item.kind == "intention_absent" for item in edge.proactive_evidence())
        await edge.close()

    asyncio.run(scenario())


def test_second_dm_subject_disables_only_proactive_speech(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enabled(monkeypatch)
    cognition = _CognitionRuntime()
    user = _UserRuntime()
    edge = _edge(cognition, user)

    async def scenario() -> None:
        first = FakeChannel("first")
        second = FakeChannel("second")
        await edge.handle_message(
            FakeInboundMessage("first-message", first, FakeUser("human"), "one")
        )
        await _wait_until(lambda: len(user.generations) == 1)
        _finish(user.generations[0], "reply-one")
        await _wait_until(lambda: len(cognition.generations) == 1)
        await edge.handle_message(
            FakeInboundMessage("second-message", second, FakeUser("human"), "two")
        )
        await _wait_until(lambda: len(user.generations) == 2)
        _finish(user.generations[1], "reply-two")
        await _wait_until(lambda: second.sends)
        await _wait_until(
            lambda: any(
                item.kind == "target_disabled_multiple_subjects"
                for item in edge.proactive_evidence()
            )
        )
        _finish(cognition.generations[0], '{"action":"create_intention","text":"later"}')
        await asyncio.sleep(1.1)
        assert len(cognition.generations) == 1
        assert edge.proactive_target_disabled
        assert [item["content"] for item in first.sends] == ["reply-one"]
        assert [item["content"] for item in second.sends] == ["reply-two"]
        await edge.close()

    asyncio.run(scenario())


def test_user_message_cancels_one_shot_opportunity(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enabled(monkeypatch)
    cognition = _CognitionRuntime()
    user = _UserRuntime()
    edge = _edge(cognition, user)

    async def scenario() -> None:
        channel = FakeChannel("same-target")
        await edge.handle_message(FakeInboundMessage("first", channel, FakeUser("human"), "one"))
        await _wait_until(lambda: len(user.generations) == 1)
        _finish(user.generations[0], "reply-one")
        await _wait_until(lambda: len(cognition.generations) == 1)
        _finish(cognition.generations[0], '{"action":"create_intention","text":"later"}')
        await _wait_until(lambda: edge.proactive_timer_active)
        await edge.handle_message(FakeInboundMessage("second", channel, FakeUser("human"), "two"))
        await _wait_until(lambda: len(user.generations) == 2)
        _finish(user.generations[1], "reply-two")
        await _wait_until(lambda: len(channel.sends) == 2)
        await _wait_until(
            lambda: any(item.kind == "idle_cancelled_by_user" for item in edge.proactive_evidence())
        )
        await _wait_until(lambda: len(cognition.generations) == 2)
        _finish(cognition.generations[1], '{"action":"create_intention","text":"later again"}')
        await _wait_until(
            lambda: [item.kind for item in edge.proactive_evidence()].count("idle_armed") == 2
        )
        await _wait_until(lambda: len(cognition.generations) == 3, timeout=2.5)
        _finish(cognition.generations[2], '{"action":"stay_silent"}')
        await _wait_until(
            lambda: [item.kind for item in edge.proactive_evidence()].count("silence") == 1
        )
        assert len(cognition.generations) == 3
        assert [item["content"] for item in channel.sends] == ["reply-one", "reply-two"]
        await edge.close()

    asyncio.run(scenario())


def test_idle_silence_and_failed_delivery_have_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enabled(monkeypatch)
    cognition = _CognitionRuntime()
    user = _UserRuntime()
    edge = _edge(cognition, user)

    async def scenario() -> None:
        channel = FakeChannel("silent-target")
        await edge.handle_message(FakeInboundMessage("silent", channel, FakeUser("human"), "one"))
        await _wait_until(lambda: len(user.generations) == 1)
        _finish(user.generations[0], "reply")
        await _wait_until(lambda: len(cognition.generations) == 1)
        _finish(cognition.generations[0], '{"action":"create_intention","text":"later"}')
        await _wait_until(lambda: len(cognition.generations) == 2, timeout=2.5)
        _finish(cognition.generations[1], '{"action":"stay_silent"}')
        await _wait_until(lambda: any(item.kind == "silence" for item in edge.proactive_evidence()))
        assert [item["content"] for item in channel.sends] == ["reply"]
        await edge.close()

    asyncio.run(scenario())


def test_unknown_discord_delivery_is_consumed_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled(monkeypatch)
    cognition = _CognitionRuntime()
    user = _UserRuntime()

    class FailingAfterFirstSend(FakeChannel):
        async def send(self, **kwargs: Any) -> Any:
            if self.sends:
                raise ConnectionError("synthetic unknown delivery")
            return await super().send(**kwargs)

    edge = _edge(cognition, user)

    async def scenario() -> None:
        channel = FailingAfterFirstSend("unknown-target")
        await edge.handle_message(FakeInboundMessage("unknown", channel, FakeUser("human"), "one"))
        await _wait_until(lambda: len(user.generations) == 1)
        _finish(user.generations[0], "reply")
        await _wait_until(lambda: len(cognition.generations) == 1)
        _finish(cognition.generations[0], '{"action":"create_intention","text":"later"}')
        await _wait_until(lambda: len(cognition.generations) == 2, timeout=2.5)
        _finish(cognition.generations[1], '{"action":"speak","text":"attempt once"}')
        await _wait_until(
            lambda: any(item.kind == "send_unknown" for item in edge.proactive_evidence())
        )
        await asyncio.sleep(0.05)
        assert [item["content"] for item in channel.sends] == ["reply"]
        assert any(item.kind == "send_unknown" for item in edge.proactive_evidence())
        await edge.close()

    asyncio.run(scenario())


def test_failed_user_turn_does_not_start_appraisal(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enabled(monkeypatch)
    cognition = _CognitionRuntime()
    user = _UserRuntime()
    edge = _edge(cognition, user)

    async def scenario() -> None:
        channel = FakeChannel("failed-target")
        await edge.handle_message(FakeInboundMessage("failed", channel, FakeUser("human"), "one"))
        await _wait_until(lambda: len(user.generations) == 1)
        user.generations[0].emit(
            GenerationFailedEvent(user.generations[0].generation_id, 1, "provider_error")
        )
        await edge.wait_idle()
        await asyncio.sleep(1.05)
        assert cognition.generations == []
        assert not any(item.kind == "appraisal_started" for item in edge.proactive_evidence())
        await edge.close()

    asyncio.run(scenario())


def test_shutdown_prevents_late_proactive_send(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enabled(monkeypatch)
    cognition = _CognitionRuntime()
    user = _UserRuntime()
    edge = _edge(cognition, user)

    async def scenario() -> None:
        channel = FakeChannel("shutdown-target")
        await edge.handle_message(FakeInboundMessage("shutdown", channel, FakeUser("human"), "one"))
        await _wait_until(lambda: len(user.generations) == 1)
        _finish(user.generations[0], "reply")
        await _wait_until(lambda: len(cognition.generations) == 1)
        _finish(cognition.generations[0], '{"action":"create_intention","text":"later"}')
        await _wait_until(lambda: edge.proactive_timer_active)
        await edge.close()
        await asyncio.sleep(1.1)
        assert [item["content"] for item in channel.sends] == ["reply"]
        assert cognition.shutdown_called

    asyncio.run(scenario())
