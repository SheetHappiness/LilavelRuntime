"""P5-B1 deterministic persistent-presence scenarios."""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from lilavel_core import ConversationCore, ModelRuntimeV3, ToolGenerationContext

from lilavel_runtime import (
    AutonomousCognitionRunner,
    AutonomousStatus,
    FixedPresenceWakePolicy,
    IdleOpportunity,
    PersistentPresenceRuntime,
    PresenceAction,
    PresenceOutput,
    PresenceToolSessionFactory,
    WakeAfterIdleOpportunitiesPolicy,
)
from lilavel_runtime.cli import PromptToolkitOutputSink

FIXTURE = Path(__file__).parent / "fixtures" / "presence_v3_sidecar.py"


@dataclass(slots=True)
class _Sink:
    outputs: list[PresenceOutput] = field(default_factory=lambda: list[PresenceOutput]())
    publish_started: threading.Event | None = None
    publish_release: threading.Event | None = None

    def publish(self, output: PresenceOutput) -> None:
        if output.kind == "autonomous" and self.publish_started is not None:
            self.publish_started.set()
            assert self.publish_release is not None
            self.publish_release.wait(1.0)
        self.outputs.append(output)


def _components(
    mode: str,
    *,
    sink: _Sink | None = None,
    wake: bool = True,
    idle_timeout_s: float = 0.02,
) -> tuple[PersistentPresenceRuntime, ModelRuntimeV3, _Sink]:
    actual_sink = sink or _Sink()
    tools = PresenceToolSessionFactory(actual_sink)
    model = ModelRuntimeV3(
        command=(sys.executable, str(FIXTURE), mode),
        sidecar_dir=FIXTURE.parent,
        tool_session_factory=tools,
        startup_timeout=2,
        shutdown_timeout=2,
        cancellation_timeout=1,
        tool_result_wait_deadline=1,
        tool_generation_deadline=2,
        tool_settlement_deadline=1,
    )
    core = ConversationCore(model, scope_id="presence-test")
    runner = AutonomousCognitionRunner(model, tools)
    presence = PersistentPresenceRuntime(
        model,
        core,
        runner,
        actual_sink,
        wake_policy=FixedPresenceWakePolicy(wake=wake),
        idle_timeout_s=idle_timeout_s,
    )
    return presence, model, actual_sink


async def _wait_for(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.002)


@pytest.mark.asyncio
async def test_cold_start_idle_no_wake_and_clean_shutdown() -> None:
    presence, model, sink = _components("say", wake=False)
    await presence.start()
    await _wait_for(lambda: any(item.kind == "wake_decision" for item in presence.evidence()))
    await presence.stop()

    assert sink.outputs == []
    assert model.health().state == "closed"
    assert [item.result for item in presence.evidence() if item.kind == "wake_decision"] == [
        "no_wake"
    ]
    assert not any(item.kind == "cognition_admitted" for item in presence.evidence())


@pytest.mark.asyncio
async def test_user_input_streams_commits_and_returns_to_idle() -> None:
    presence, _, sink = _components("say", wake=False, idle_timeout_s=0.2)
    await presence.start()
    assert await presence.submit_user("hello") == "completed"
    await presence.stop()

    assert "".join(item.text for item in sink.outputs if item.kind == "conversation_delta") == (
        "reply:hello"
    )
    assert [(item.role, item.text) for item in presence.history] == [
        ("user", "hello"),
        ("assistant", "reply:hello"),
    ]


@pytest.mark.asyncio
async def test_real_activity_resets_idle_and_one_timeout_makes_one_opportunity() -> None:
    presence, _, _ = _components("say", wake=False, idle_timeout_s=0.05)
    await presence.start()
    await asyncio.sleep(0.03)
    assert await presence.submit_user("reset") == "completed"
    await asyncio.sleep(0.03)
    assert not any(item.kind == "idle_opportunity" for item in presence.evidence())
    await _wait_for(lambda: any(item.kind == "idle_opportunity" for item in presence.evidence()))
    await asyncio.sleep(0.06)
    await presence.stop()

    opportunities = [item for item in presence.evidence() if item.kind == "idle_opportunity"]
    assert len(opportunities) == 1


@pytest.mark.asyncio
async def test_wake_stay_silent_is_successful_non_output_and_latched() -> None:
    presence, _, sink = _components("silent")
    await presence.start()
    await _wait_for(lambda: any(item.kind == "cognition_settled" for item in presence.evidence()))
    await asyncio.sleep(0.05)
    await presence.stop()

    assert sink.outputs == []
    settled = [item for item in presence.evidence() if item.kind == "cognition_settled"]
    assert [item.result for item in settled] == [AutonomousStatus.COMPLETED.value]
    assert len([item for item in presence.evidence() if item.kind == "idle_opportunity"]) == 1


@pytest.mark.asyncio
async def test_wake_say_has_exactly_one_visible_noncanonical_output() -> None:
    presence, model, sink = _components("say")
    await presence.start()
    await _wait_for(lambda: any(item.kind == "cognition_settled" for item in presence.evidence()))
    await presence.stop()

    autonomous = [item.text for item in sink.outputs if item.kind == "autonomous"]
    assert autonomous == ["hello from idle"]
    assert presence.history == ()
    assert model.tool_evidence()[-1].settlement == "settled"


@pytest.mark.asyncio
async def test_provider_continuation_after_terminal_action_is_consumed_not_rendered() -> None:
    presence, _, sink = _components("say")
    await presence.start()
    await _wait_for(lambda: any(item.kind == "cognition_settled" for item in presence.evidence()))
    await presence.stop()

    assert [item for item in sink.outputs if item.kind == "autonomous"] == [
        PresenceOutput("autonomous", "hello from idle")
    ]
    assert all("duplicate continuation" not in item.text for item in sink.outputs)


@pytest.mark.asyncio
async def test_user_preempts_autonomous_generation_then_is_admitted() -> None:
    presence, model, sink = _components("block-generation")
    await presence.start()
    await _wait_for(lambda: any(item.kind == "cognition_admitted" for item in presence.evidence()))
    assert await presence.submit_user("priority") == "completed"
    await presence.stop()

    assert [(item.role, item.text) for item in presence.history] == [
        ("user", "priority"),
        ("assistant", "reply:priority"),
    ]
    assert not any(item.kind == "autonomous" for item in sink.outputs)
    assert any(
        item.kind == "joined_settlement" and item.settlement == "cancelled"
        for item in model.tool_evidence()
    )


@pytest.mark.asyncio
async def test_user_input_supersedes_normal_generation_without_regression() -> None:
    presence, model, _ = _components("block-first-user", wake=False, idle_timeout_s=1)
    await presence.start()
    first = asyncio.create_task(presence.submit_user("first"))
    await _wait_for(lambda: model.health().state == "busy")
    second = asyncio.create_task(presence.submit_user("second"))

    assert await first == "cancelled"
    assert await second == "completed"
    await presence.stop()

    assert [(item.role, item.text) for item in presence.history] == [
        ("user", "first"),
        ("user", "second"),
        ("assistant", "reply:second"),
    ]


@pytest.mark.asyncio
async def test_user_during_presence_executor_wait_preserves_joined_settlement() -> None:
    started = threading.Event()
    release = threading.Event()
    sink = _Sink(publish_started=started, publish_release=release)
    presence, model, _ = _components("say", sink=sink)
    await presence.start()
    await _wait_for(started.is_set)
    user = asyncio.create_task(presence.submit_user("after effect"))
    await asyncio.sleep(0.01)
    assert not user.done()
    release.set()
    assert await user == "completed"
    await presence.stop()

    assert [item.text for item in sink.outputs if item.kind == "autonomous"] == ["hello from idle"]
    assert any(
        item.kind == "joined_settlement" and item.settlement == "cancelled"
        for item in model.tool_evidence()
    )
    assert [(item.role, item.text) for item in presence.history] == [
        ("user", "after effect"),
        ("assistant", "reply:after effect"),
    ]


@pytest.mark.asyncio
async def test_invalid_autonomous_completion_is_contained_without_output() -> None:
    presence, _, sink = _components("invalid")
    await presence.start()
    await _wait_for(lambda: any(item.kind == "cognition_settled" for item in presence.evidence()))
    await presence.stop()

    assert sink.outputs == []
    settled = next(item for item in presence.evidence() if item.kind == "cognition_settled")
    assert settled.result == AutonomousStatus.INVALID.value


@pytest.mark.asyncio
async def test_two_requested_terminal_actions_produce_at_most_one_effect() -> None:
    presence, _, sink = _components("two-actions")
    await presence.start()
    await _wait_for(lambda: any(item.kind == "cognition_settled" for item in presence.evidence()))
    await presence.stop()

    assert [item.text for item in sink.outputs if item.kind == "autonomous"] == ["hello from idle"]
    admissions = [item for item in presence.evidence() if item.kind == "cognition_admitted"]
    opportunities = [item for item in presence.evidence() if item.kind == "idle_opportunity"]
    assert len(admissions) == len(opportunities) == 1
    assert admissions[0].opportunity_id == opportunities[0].opportunity_id


@pytest.mark.asyncio
async def test_shutdown_during_autonomous_generation_settles_cleanly() -> None:
    presence, model, _ = _components("shutdown-block")
    await presence.start()
    await _wait_for(lambda: any(item.kind == "cognition_admitted" for item in presence.evidence()))
    await presence.stop()

    assert model.health().state == "closed"
    assert (
        next(item for item in presence.evidence() if item.kind == "cognition_settled").result
        == AutonomousStatus.CANCELLED.value
    )


@pytest.mark.asyncio
async def test_prompt_toolkit_sink_marshals_background_output_to_loop() -> None:
    sink = PromptToolkitOutputSink(asyncio.get_running_loop())

    worker = threading.Thread(
        target=sink.publish, args=(PresenceOutput("autonomous", "safe"),), daemon=True
    )
    worker.start()
    output = await asyncio.wait_for(sink.queue.get(), 1)
    worker.join(1)

    assert output == PresenceOutput("autonomous", "safe")


def test_presence_action_enum_is_explicit() -> None:
    assert {item.value for item in PresenceAction} == {"say", "stay_silent"}


def test_autonomous_prefix_alone_does_not_grant_presence_capability() -> None:
    tools = PresenceToolSessionFactory(_Sink())
    context = ToolGenerationContext("runtime", "scope", "autonomous:spoof", "generation", 1)

    assert tools.create(context).specs == ()


@pytest.mark.asyncio
async def test_wake_after_policy_is_deterministic_for_live_proof() -> None:
    policy = WakeAfterIdleOpportunitiesPolicy(2)

    assert not (await policy.decide(IdleOpportunity("one", 1, 3))).wake
    assert (await policy.decide(IdleOpportunity("two", 2, 3))).wake
