"""P5-B1 deterministic persistent-presence scenarios."""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from lilavel_core import (
    ContextMessage,
    ConversationCore,
    ModelRequest,
    ModelRuntimeV3,
    ToolGenerationContext,
)
from lilavel_core.production_cognition import build_character_guidance, build_turn_guidance

from lilavel_runtime import (
    AutonomousCognitionRunner,
    AutonomousStatus,
    FixedPresenceWakePolicy,
    IdleOpportunity,
    IntentionStatus,
    MindAppraisalAction,
    MindState,
    PersistentPresenceRuntime,
    PresenceAction,
    PresenceOutput,
    PresenceToolSessionFactory,
    WakeAfterIdleOpportunitiesPolicy,
    parse_mind_appraisal,
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
    mind_state = MindState()
    core = ConversationCore(
        model,
        trusted_guidance=lambda: build_turn_guidance(mind_state.projection().guidance_blocks()),
        scope_id="presence-test",
    )
    runner = AutonomousCognitionRunner(model, tools)
    presence = PersistentPresenceRuntime(
        model,
        core,
        runner,
        actual_sink,
        mind_state=mind_state,
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
async def test_wake_without_an_active_intention_admits_no_model_generation() -> None:
    presence, model, sink = _components("say", wake=True)
    await presence.start()
    await _wait_for(lambda: any(item.kind == "wake_decision" for item in presence.evidence()))
    await asyncio.sleep(0.05)
    await presence.stop()

    assert sink.outputs == []
    assert not any(item.kind == "cognition_admitted" for item in presence.evidence())
    assert not any(
        item.kind in {"tool_requested", "execution_started"} for item in model.tool_evidence()
    )


def test_mind_appraisal_parser_fails_closed_without_retry() -> None:
    assert parse_mind_appraisal('{"action":"no_change"}').action is MindAppraisalAction.NO_CHANGE
    assert parse_mind_appraisal('{"action":"create_intention","text":"finish later"}').action is (
        MindAppraisalAction.CREATE_INTENTION
    )
    for raw in (
        "",
        "not json",
        '{"action":"no_change","text":"unexpected"}',
        '{"action":"create_intention","text":""}',
        '{"action":"create_intention","text":"x","extra":true}',
    ):
        assert parse_mind_appraisal(raw).action is MindAppraisalAction.NO_CHANGE


def test_mind_state_bounds_records_and_selects_only_active_intentions() -> None:
    state = MindState(intentions_capacity=1, self_actions_capacity=1)
    first = state.create_intention(
        "first matter",
        user_message_id="user-1",
        assistant_message_id="assistant-1",
    )
    assert first is not None
    assert (
        state.create_intention(
            "blocked matter",
            user_message_id="user-2",
            assistant_message_id="assistant-2",
        )
        is None
    )
    action = state.mark_expressed(first.intention_id, "said once")
    assert action is not None
    assert state.active_intention() is None

    second = state.create_intention(
        "second matter",
        user_message_id="user-3",
        assistant_message_id="assistant-3",
    )
    assert second is not None
    assert second.intention_id != first.intention_id
    assert state.active_intention() == second
    assert len(state.intentions()) == len(state.self_actions()) == 1


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
async def test_mind_state_causes_noncanonical_expression_and_next_turn_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    presence, model, sink = _components("say", idle_timeout_s=0.02)
    captured: list[tuple[str, ModelRequest]] = []
    original_generate_for_run = model.generate_for_run

    def capture_request(request: ModelRequest, *, scope_id: str, logical_run_id: str):
        captured.append((logical_run_id, request))
        return original_generate_for_run(request, scope_id=scope_id, logical_run_id=logical_run_id)

    monkeypatch.setattr(model, "generate_for_run", capture_request)

    await presence.start()
    prompt = "I have an unfinished future matter to finish tomorrow."
    assert await presence.submit_user(prompt) == "completed"

    intention = presence.mind_state.intentions()[0]
    assert intention.status is IntentionStatus.ACTIVE
    assert intention.text == "Finish the concrete matter later."
    assert intention.user_message_id == presence.canonical_history[0].message_id
    assert intention.assistant_message_id == presence.canonical_history[1].message_id
    assert not [item for item in sink.outputs if item.kind == "autonomous"]

    await _wait_for(lambda: any(item.kind == "cognition_settled" for item in presence.evidence()))
    assert [item.text for item in sink.outputs if item.kind == "autonomous"] == ["hello from idle"]
    assert presence.mind_state.intentions()[0].status is IntentionStatus.EXPRESSED
    assert [item.text for item in presence.mind_state.self_actions()] == ["hello from idle"]
    assert len(presence.history) == 2

    assert await presence.submit_user("Ще ні") == "completed"
    await presence.stop()

    normal_requests = [
        request
        for run_id, request in captured
        if not run_id.startswith(("appraisal:", "autonomous:"))
    ]
    assert len(normal_requests) == 2
    follow_up = normal_requests[-1]
    assert follow_up.messages is not None
    assert follow_up.messages[-1] == ContextMessage("user", "Ще ні")
    assert any("hello from idle" in block for block in follow_up.system_prompt)
    assert all("hello from idle" not in message.text for message in follow_up.messages)


@pytest.mark.asyncio
async def test_user_preempts_appraisal_before_successor_generation() -> None:
    presence, model, _ = _components("block-first-appraisal", wake=False, idle_timeout_s=1)
    await presence.start()
    first = asyncio.create_task(presence.submit_user("An unfinished future matter."))
    await _wait_for(lambda: any(item.kind == "appraisal_admitted" for item in presence.evidence()))
    second = asyncio.create_task(presence.submit_user("Ще ні"))

    assert await first == "completed"
    assert await second == "completed"
    await presence.stop()

    appraisal_settled = [item for item in presence.evidence() if item.kind == "appraisal_settled"]
    assert appraisal_settled[0].result == AutonomousStatus.CANCELLED.value
    assert presence.mind_state.intentions() == ()
    assert [(item.role, item.text) for item in presence.history] == [
        ("user", "An unfinished future matter."),
        ("assistant", "reply:An unfinished future matter."),
        ("user", "Ще ні"),
        ("assistant", "reply:Ще ні"),
    ]
    assert not any(
        item.kind in {"tool_requested", "execution_started"} for item in model.tool_evidence()
    )


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
    assert await presence.submit_user("An unfinished future matter needs attention.") == "completed"
    await _wait_for(lambda: any(item.kind == "cognition_settled" for item in presence.evidence()))
    await asyncio.sleep(0.05)
    await presence.stop()

    assert [item for item in sink.outputs if item.kind == "autonomous"] == []
    settled = [item for item in presence.evidence() if item.kind == "cognition_settled"]
    assert [item.result for item in settled] == [AutonomousStatus.COMPLETED.value]
    assert len([item for item in presence.evidence() if item.kind == "idle_opportunity"]) == 1
    assert presence.mind_state.intentions()[0].status is IntentionStatus.ACTIVE


@pytest.mark.asyncio
async def test_wake_say_has_exactly_one_visible_noncanonical_output() -> None:
    presence, model, sink = _components("say")
    await presence.start()
    assert await presence.submit_user("An unfinished future matter needs attention.") == "completed"
    await _wait_for(lambda: any(item.kind == "cognition_settled" for item in presence.evidence()))
    await presence.stop()

    autonomous = [item.text for item in sink.outputs if item.kind == "autonomous"]
    assert autonomous == ["hello from idle"]
    assert len(presence.history) == 2
    assert model.tool_evidence()[-1].settlement == "settled"


@pytest.mark.asyncio
async def test_provider_continuation_after_terminal_action_is_consumed_not_rendered() -> None:
    presence, _, sink = _components("say")
    await presence.start()
    assert await presence.submit_user("An unfinished future matter needs attention.") == "completed"
    await _wait_for(lambda: any(item.kind == "cognition_settled" for item in presence.evidence()))
    await presence.stop()

    assert [item for item in sink.outputs if item.kind == "autonomous"] == [
        PresenceOutput("autonomous", "hello from idle")
    ]
    assert all("duplicate continuation" not in item.text for item in sink.outputs)


@pytest.mark.asyncio
async def test_p5b1_probe_captures_system_prompt_for_user_and_autonomous_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep canonical Character v0 guidance on both P5-B1 request paths."""

    presence, model, _ = _components("silent", idle_timeout_s=0.02)
    captured: list[tuple[str, ModelRequest]] = []
    original_generate_for_run = model.generate_for_run

    def capture_request(request: ModelRequest, *, scope_id: str, logical_run_id: str):
        captured.append((logical_run_id, request))
        return original_generate_for_run(request, scope_id=scope_id, logical_run_id=logical_run_id)

    monkeypatch.setattr(model, "generate_for_run", capture_request)

    await presence.start()
    assert await presence.submit_user("An unfinished future matter needs attention.") == "completed"
    await _wait_for(lambda: any(item.kind == "cognition_settled" for item in presence.evidence()))
    await presence.stop()

    assert len(captured) == 3
    user_request = next(
        request
        for run_id, request in captured
        if not run_id.startswith(("autonomous:", "appraisal:"))
    )
    appraisal_request = next(
        request for run_id, request in captured if run_id.startswith("appraisal:")
    )
    autonomous_request = next(
        request for run_id, request in captured if run_id.startswith("autonomous:")
    )
    character_guidance = build_character_guidance()
    assert user_request.system_prompt == build_turn_guidance()
    assert appraisal_request.system_prompt[: len(character_guidance)] == character_guidance
    assert all(
        "respond to the current user turn" not in block for block in appraisal_request.system_prompt
    )
    assert autonomous_request.system_prompt[: len(character_guidance)] == character_guidance
    assert all(
        "respond to the current user turn" not in block
        for block in autonomous_request.system_prompt
    )
    assert autonomous_request.prompt is not None
    assert "Finish the concrete matter later." in autonomous_request.prompt


@pytest.mark.asyncio
async def test_user_preempts_autonomous_generation_then_is_admitted() -> None:
    presence, model, sink = _components("block-generation")
    await presence.start()
    assert await presence.submit_user("An unfinished future matter needs attention.") == "completed"
    await _wait_for(lambda: any(item.kind == "cognition_admitted" for item in presence.evidence()))
    assert await presence.submit_user("priority") == "completed"
    await presence.stop()

    assert [(item.role, item.text) for item in presence.history] == [
        ("user", "An unfinished future matter needs attention."),
        ("assistant", "reply:An unfinished future matter needs attention."),
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
    assert await presence.submit_user("An unfinished future matter needs attention.") == "completed"
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
        ("user", "An unfinished future matter needs attention."),
        ("assistant", "reply:An unfinished future matter needs attention."),
        ("user", "after effect"),
        ("assistant", "reply:after effect"),
    ]


@pytest.mark.asyncio
async def test_invalid_autonomous_completion_is_contained_without_output() -> None:
    presence, _, sink = _components("invalid")
    await presence.start()
    assert await presence.submit_user("An unfinished future matter needs attention.") == "completed"
    await _wait_for(lambda: any(item.kind == "cognition_settled" for item in presence.evidence()))
    await presence.stop()

    assert [item for item in sink.outputs if item.kind == "autonomous"] == []
    settled = next(item for item in presence.evidence() if item.kind == "cognition_settled")
    assert settled.result == AutonomousStatus.INVALID.value


@pytest.mark.asyncio
async def test_two_requested_terminal_actions_produce_at_most_one_effect() -> None:
    presence, _, sink = _components("two-actions")
    await presence.start()
    assert await presence.submit_user("An unfinished future matter needs attention.") == "completed"
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
    assert await presence.submit_user("An unfinished future matter needs attention.") == "completed"
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
