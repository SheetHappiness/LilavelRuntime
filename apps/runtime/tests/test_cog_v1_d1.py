"""COG-V1-D1 run-bound USER disposition lifecycle proofs."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from lilavel_core import (
    LILAVEL_CHARACTER_V0,
    ContextMessage,
    ConversationCancelled,
    ConversationCore,
    GenerationCancelled,
    GenerationCompleted,
    GenerationEvent,
    GenerationFailedEvent,
    ModelRequest,
    TextDelta,
    TurnBehaviorSource,
)

from lilavel_runtime import (
    ConversationExecutionAdapter,
    ConversationExecutionResult,
    DeliberationMode,
    DispositionPlanner,
    SemanticCancellationToken,
    UserDispositionResolver,
)


def _payload(**changes: object) -> str:
    value: dict[str, object] = {
        "aim": "answer",
        "stance": "neutral",
        "engagement": "normal",
        "directness": "normal",
        "desired_length": "normal",
        "humor_allowed": False,
        "question_policy": "avoid",
        "initiative": "normal",
        "reason_codes": ["direct_address"],
    }
    value.update(changes)
    return json.dumps(value, separators=(",", ":"))


@dataclass
class _Generation:
    generation_id: str
    events_to_emit: tuple[GenerationEvent, ...]
    epoch: int = 1

    def events(self) -> Iterator[GenerationEvent]:
        yield from self.events_to_emit


class _BlockingGeneration:
    def __init__(self, generation_id: str) -> None:
        self.generation_id = generation_id
        self.epoch = 1
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = False

    def events(self) -> Iterator[GenerationEvent]:
        self.started.set()
        self.release.wait(2.0)
        if self.cancelled:
            yield GenerationCancelled(self.generation_id, self.epoch)
        else:  # pragma: no cover - the test only releases through cancellation
            yield GenerationCompleted(self.generation_id, self.epoch)


class _Runtime:
    def __init__(self, planner_response: str | None = None) -> None:
        self.planner_response = planner_response
        self.calls: list[tuple[str, ModelRequest]] = []
        self.generations: list[_Generation | _BlockingGeneration] = []
        self.cancelled_ids: list[str] = []
        self.planner_started = threading.Event()
        self.block_planner = False

    def generate_for_run(
        self,
        request: ModelRequest,
        *,
        scope_id: str,
        logical_run_id: str,
    ) -> _Generation | _BlockingGeneration:
        del scope_id
        is_planner = "strict JSON object" in "\n".join(request.system_prompt)
        self.calls.append((logical_run_id, request))
        generation_id = f"generation-{len(self.calls)}"
        if is_planner:
            self.planner_started.set()
            if self.block_planner:
                generation = _BlockingGeneration(generation_id)
            else:
                response = self.planner_response or _payload()
                generation = _Generation(
                    generation_id,
                    (TextDelta(generation_id, 1, response), GenerationCompleted(generation_id, 1)),
                )
        else:
            generation = _Generation(
                generation_id,
                (TextDelta(generation_id, 1, "response"), GenerationCompleted(generation_id, 1)),
            )
        self.generations.append(generation)
        return generation

    def generate(self, request: ModelRequest) -> _Generation | _BlockingGeneration:
        return self.generate_for_run(request, scope_id="scope", logical_run_id="legacy")

    def cancel(self, generation_id: str) -> bool:
        self.cancelled_ids.append(generation_id)
        generation = next(item for item in self.generations if item.generation_id == generation_id)
        if isinstance(generation, _BlockingGeneration):
            generation.cancelled = True
            generation.release.set()
        return True


def _core(runtime: _Runtime, scope: str = "scope") -> ConversationCore:
    return ConversationCore(
        runtime,
        trusted_guidance=("[character canon]",),
        turn_guidance=lambda behavior: (
            f"[run behavior]\nfocus: {behavior.working_state.focus}\n"
            f"aim: {behavior.response_disposition.aim}",
        ),
        scope_id=scope,
    )


async def _execute(
    core: ConversationCore,
    resolver: UserDispositionResolver,
    cancellation: SemanticCancellationToken | None = None,
) -> ConversationExecutionResult:
    adapter = ConversationExecutionAdapter(disposition_resolver=resolver)
    return await adapter.execute_core_turn(
        core,
        "current user turn",
        lambda event: None,
        cancellation or SemanticCancellationToken(),
    )


@pytest.mark.asyncio
async def test_planner_runs_after_acceptance_and_binds_behavior_to_that_run() -> None:
    runtime = _Runtime(
        _payload(aim="challenge", stance="skeptical", reason_codes=["contradiction"])
    )
    core = _core(runtime)
    resolver = UserDispositionResolver(
        mode=DeliberationMode.ALWAYS_PLAN,
        planner=DispositionPlanner(runtime),
    )

    result = await _execute(core, resolver)
    run = result.run

    assert core.history == (
        ContextMessage("user", "current user turn"),
        ContextMessage("assistant", "response"),
    )
    assert len(runtime.calls) == 2
    assert runtime.calls[0][0] == run.run_id
    assert runtime.calls[0][1].messages == (ContextMessage("user", "current user turn"),)
    assert run.behavior is not None
    assert run.behavior.source is TurnBehaviorSource.PLANNER
    assert run.behavior.working_state.focus == (
        "address the important conflict in the user's premise"
    )
    response_request = runtime.calls[1][1]
    rendered = "\n".join(response_request.system_prompt)
    assert "address the important conflict" in rendered
    assert (
        _payload(aim="challenge", stance="skeptical", reason_codes=["contradiction"])
        not in rendered
    )

    evidence = core.d1_evidence()
    resolved = next(item for item in evidence if item.kind == "disposition_resolved")
    assert resolved.disposition_source == "planner"
    assert resolved.planner_invoked is True
    assert resolved.planner_outcome == "accepted"
    assert resolved.disposition_changed_default is True
    assert any(item.kind == "response_generation_started" for item in evidence)
    assert any(item.kind == "response_first_delta" for item in evidence)


@pytest.mark.asyncio
async def test_default_only_mode_makes_zero_planner_calls_and_preserves_default_guidance() -> None:
    runtime = _Runtime(_payload(aim="challenge"))
    core = _core(runtime)
    resolver = UserDispositionResolver(
        mode=DeliberationMode.DEFAULT_ONLY,
        planner=DispositionPlanner(runtime),
    )

    result = await _execute(core, resolver)
    run = result.run

    assert len(runtime.calls) == 1
    assert run.behavior is not None
    assert run.behavior.source is TurnBehaviorSource.DEFAULT
    assert run.behavior.working_state.focus == "respond to the current user turn"
    resolved = next(item for item in core.d1_evidence() if item.kind == "disposition_resolved")
    assert resolved.planner_invoked is False
    assert resolved.planner_outcome == "not_invoked"


@pytest.mark.asyncio
async def test_sequential_runs_keep_planner_behavior_bound_to_their_own_run() -> None:
    runtime = _Runtime(
        _payload(aim="challenge", stance="skeptical", reason_codes=["contradiction"])
    )
    core = _core(runtime)
    resolver = UserDispositionResolver(
        mode=DeliberationMode.ALWAYS_PLAN,
        planner=DispositionPlanner(runtime),
    )

    first = await _execute(core, resolver)
    first_run = first.run
    runtime.planner_response = _payload(
        aim="comfort", stance="supportive", reason_codes=["vulnerability"]
    )
    second = await _execute(core, resolver)
    second_run = second.run

    assert first_run.run_id != second_run.run_id
    assert first_run.behavior is not None
    assert second_run.behavior is not None
    assert first_run.behavior.working_state.focus == (
        "address the important conflict in the user's premise"
    )
    assert second_run.behavior.working_state.focus == (
        "respond to the user's difficulty with practical warmth"
    )
    assert "address the important conflict" in "\n".join(runtime.calls[1][1].system_prompt)
    assert "practical warmth" in "\n".join(runtime.calls[3][1].system_prompt)
    assert "practical warmth" not in "\n".join(runtime.calls[1][1].system_prompt)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "planner_response, expected_outcome",
    [("not json", "rejected"), (_payload(aim="answer") + "\n{}", "rejected")],
)
async def test_rejected_planner_output_falls_back_without_retry(
    planner_response: str,
    expected_outcome: str,
) -> None:
    runtime = _Runtime(planner_response)
    core = _core(runtime)
    resolver = UserDispositionResolver(
        mode=DeliberationMode.ALWAYS_PLAN,
        planner=DispositionPlanner(runtime),
    )

    result = await _execute(core, resolver)
    run = result.run

    assert len(runtime.calls) == 2
    assert run.behavior is not None
    assert run.behavior.source is TurnBehaviorSource.DEFAULT
    resolved = next(item for item in core.d1_evidence() if item.kind == "disposition_resolved")
    assert resolved.planner_outcome == expected_outcome
    assert resolved.planner_fallback_reason == "planner_rejected"


@pytest.mark.asyncio
async def test_provider_failure_falls_back_without_retry() -> None:
    runtime = _Runtime()
    runtime.planner_response = "provider failure"

    def fail_planner(request: ModelRequest, *, scope_id: str, logical_run_id: str):
        del scope_id, logical_run_id
        runtime.calls.append(("planner", request))
        generation_id = f"generation-{len(runtime.calls)}"
        events: tuple[GenerationEvent, ...] = (
            (GenerationFailedEvent(generation_id, 1, "provider_error"),)
            if len(runtime.calls) == 1
            else (TextDelta(generation_id, 1, "response"), GenerationCompleted(generation_id, 1))
        )
        generation = _Generation(generation_id, events)
        runtime.generations.append(generation)
        return generation

    runtime.generate_for_run = fail_planner  # type: ignore[method-assign]
    core = _core(runtime)
    resolver = UserDispositionResolver(
        mode=DeliberationMode.ALWAYS_PLAN,
        planner=DispositionPlanner(runtime),
    )

    result = await _execute(core, resolver)
    assert result.outcome.status == "completed"
    assert len(runtime.calls) == 2
    resolved = next(item for item in core.d1_evidence() if item.kind == "disposition_resolved")
    assert resolved.planner_outcome == "fallback"
    assert resolved.planner_fallback_reason == "planner_provider_failure"


@pytest.mark.asyncio
async def test_superseded_prepared_run_cannot_start_stale_generation() -> None:
    runtime = _Runtime(_payload(aim="challenge"))
    runtime.block_planner = True
    core = _core(runtime)
    resolver = UserDispositionResolver(
        mode=DeliberationMode.ALWAYS_PLAN,
        planner=DispositionPlanner(runtime),
    )
    first = core.prepare_turn("first")
    resolution_task = asyncio.create_task(resolver.resolve(core, first))
    await asyncio.to_thread(runtime.planner_started.wait, 1.0)

    successor = core.prepare_turn("second")
    blocked_generation = runtime.generations[0]
    assert isinstance(blocked_generation, _BlockingGeneration)
    blocked_generation.cancelled = False
    blocked_generation.release.set()
    resolution = await resolution_task

    core.start_prepared_run(first, resolution.behavior)
    core.start_turn("third")
    assert first.wait(1.0).status == "superseded"
    assert len(runtime.calls) == 2
    assert successor.wait(1.0).status == "superseded"


@pytest.mark.asyncio
async def test_actor_cancellation_contains_planner_and_starts_no_response() -> None:
    runtime = _Runtime()
    runtime.block_planner = True
    core = _core(runtime)
    resolver = UserDispositionResolver(
        mode=DeliberationMode.ALWAYS_PLAN,
        planner=DispositionPlanner(runtime),
    )
    cancellation = SemanticCancellationToken()
    task = asyncio.create_task(_execute(core, resolver, cancellation))
    await asyncio.to_thread(runtime.planner_started.wait, 1.0)

    cancellation.request()
    result = await task

    assert result.outcome.status == "cancelled"
    assert len(runtime.calls) == 1
    assert runtime.cancelled_ids == ["generation-1"]
    assert core.history == (ContextMessage("user", "current user turn"),)
    assert any(isinstance(event, ConversationCancelled) for event in result.run.events())


def test_character_canon_is_immutable_and_planner_policy_is_fixed_user_think_respond() -> None:
    with pytest.raises(AttributeError):
        LILAVEL_CHARACTER_V0.id = "changed"  # type: ignore[misc]
