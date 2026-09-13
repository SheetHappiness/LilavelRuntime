"""Focused CTX-V1-C production request, topology, and observability proofs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from lilavel_core import (
    ContextMessage,
    ConversationCore,
    GenerationCompleted,
    GenerationEvent,
    ModelRequest,
    TextDelta,
    default_turn_behavior,
)
from lilavel_core.production_cognition import (
    build_stable_runtime_guidance,
    build_turn_behavior_guidance,
)
from lilavel_core.sidecar_protocol import MAX_GUIDANCE_BYTES

from lilavel_runtime import (
    MAX_TOTAL_REQUEST_CONTEXT_BYTES,
    CognitionContext,
    CognitionEpisode,
    CognitionTrigger,
    CognitionTriggerSource,
    ContextFrameBuilder,
    ContextPurpose,
    DispositionPlanner,
    EventSource,
    LocalCognitionEngine,
    MindState,
    Observation,
    ProductionContextComposer,
    WorldEvent,
    production_context_request_factory,
    request_context_bytes,
)


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


@dataclass(slots=True)
class _Generation:
    generation_id: str
    response: str
    epoch: int = 1

    def events(self) -> Iterator[GenerationEvent]:
        yield TextDelta(self.generation_id, self.epoch, self.response)
        yield GenerationCompleted(self.generation_id, self.epoch)


class _Runtime:
    def __init__(self, response: str = "{}") -> None:
        self.response = response
        self.calls: list[ModelRequest] = []

    def generate_for_run(
        self,
        request: ModelRequest,
        *,
        scope_id: str,
        logical_run_id: str,
    ) -> _Generation:
        del scope_id, logical_run_id
        self.calls.append(request)
        return _Generation(f"generation:{len(self.calls)}", self.response)

    def generate(self, request: ModelRequest) -> _Generation:
        return self.generate_for_run(request, scope_id="legacy", logical_run_id="legacy")

    def cancel(self, generation_id: str) -> bool:
        del generation_id
        return True


def _episode(source: CognitionTriggerSource, reason: str) -> CognitionEpisode:
    if source is CognitionTriggerSource.EXTERNAL:
        observation = Observation(
            "observation:current",
            1,
            WorldEvent(
                "event:current",
                EventSource("fixture", "subject:current"),
                "message",
                {"text": "admitted observation text"},
            ),
        )
        trigger = CognitionTrigger((observation.observation_id,), reason)
        observations = (observation,)
    elif source is CognitionTriggerSource.TEMPORAL:
        trigger = CognitionTrigger(
            (),
            reason,
            source=source,
            wake_intent_id="wake:current",
            source_refs=(
                "wake:current",
                "episode:source",
                "trigger:source",
                "intention:old",
            ),
        )
        observations = ()
    else:
        trigger = CognitionTrigger((), reason, source=source, source_refs=("source:current",))
        observations = ()
    context = CognitionContext(
        episode_id="episode:current",
        scope_id="scope:current",
        trigger=trigger,
        observations=observations,
        mind_state=MindState().snapshot(),
    )
    return CognitionEpisode("episode:current", context)


def test_all_purposes_share_stable_canon_and_volatile_projection_order() -> None:
    composer = ProductionContextComposer(ContextFrameBuilder())
    stable = build_stable_runtime_guidance()
    for purpose in ContextPurpose:
        blocks = composer.compose_projection(
            purpose,
            scope_id="scope:current",
            existing_guidance=stable,
        )
        assert blocks[0].startswith("[Context purpose]")
        assert blocks[0].find("purpose:") >= 0
        assert stable == build_stable_runtime_guidance()
        assert all(block not in stable for block in blocks)
    evidence = composer.evidence()
    assert [item.purpose for item in evidence] == list(ContextPurpose)
    assert all(item.operating_canon_injected for item in evidence)


def test_user_core_keeps_history_separate_from_context_projection() -> None:
    composer = ProductionContextComposer(ContextFrameBuilder())
    core = ConversationCore(
        _Runtime(),
        trusted_guidance=build_stable_runtime_guidance(),
        turn_guidance=build_turn_behavior_guidance,
        context_guidance=composer,
        scope_id="scope:current",
    )
    run = core.prepare_turn("current user text")
    run.bind_behavior(default_turn_behavior())
    request = core.build_model_request(run)

    assert request.messages == (ContextMessage("user", "current user text"),)
    rendered = "\n".join(request.system_prompt)
    assert "purpose: user_response" in rendered
    assert "current user text" not in rendered
    assert "frame:" not in rendered
    assert "source:" not in rendered
    assert composer.evidence()[-1].history_bytes == len(b"current user text")


def test_ambient_and_temporal_use_one_existing_cognition_call() -> None:
    clock = _Clock(datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC))
    composer = ProductionContextComposer(ContextFrameBuilder(clock=clock))
    runtime = _Runtime()
    engine = LocalCognitionEngine(
        runtime,
        lambda trigger_id: (),
        context_composer=composer,
    )

    asyncio.run(engine.run(_episode(CognitionTriggerSource.EXTERNAL, "ambient")))
    assert len(runtime.calls) == 1
    ambient = runtime.calls[-1]
    assert "[Operating canon" in "\n".join(ambient.system_prompt)
    assert "purpose: ambient_cognition" in "\n".join(ambient.system_prompt)
    assert composer.evidence()[-1].request_input_bytes == request_context_bytes(ambient) - sum(
        len(block.encode()) for block in ambient.system_prompt
    )

    asyncio.run(engine.run(_episode(CognitionTriggerSource.TEMPORAL, "old wake reason")))
    assert len(runtime.calls) == 2
    temporal = runtime.calls[-1]
    rendered = "\n".join(temporal.system_prompt)
    assert "purpose: temporal_wake" in rendered
    assert "2030-01-02T03:04:05+00:00" in rendered
    assert "old wake reason" in (temporal.prompt or "")
    assert "not a historical world snapshot" in (temporal.prompt or "")


def test_internal_appraisal_keeps_completed_turn_evidence_in_history_layer() -> None:
    composer = ProductionContextComposer(ContextFrameBuilder())
    runtime = _Runtime()
    history = (ContextMessage("user", "completed user turn"), ContextMessage("assistant", "done"))
    engine = LocalCognitionEngine(
        runtime,
        lambda trigger_id: history,
        context_composer=composer,
    )

    asyncio.run(
        engine.run(_episode(CognitionTriggerSource.INTERNAL, "conversation_completion_appraisal"))
    )
    assert len(runtime.calls) == 1
    request = runtime.calls[0]
    assert request.messages == history
    rendered = "\n".join(request.system_prompt)
    assert "[Operating canon" in rendered
    assert "purpose: internal_appraisal" in rendered
    assert "completed user turn" not in rendered


def test_planner_context_is_advisory_and_adds_no_generation_call() -> None:
    composer = ProductionContextComposer(ContextFrameBuilder())
    runtime = _Runtime()
    planner = DispositionPlanner(runtime, context_composer=composer)

    request = planner.build_request("current user turn", scope_id="scope:current")
    assert "purpose: user_response" in "\n".join(request.system_prompt)
    asyncio.run(planner.plan_candidate("current user turn", scope_id="scope:current"))
    assert len(runtime.calls) == 1


def test_projection_failure_is_local_and_oversize_reduction_drops_whole_blocks() -> None:
    def fail(*args: object) -> object:
        raise RuntimeError("unavailable source")

    failed = ProductionContextComposer(ContextFrameBuilder(), request_factory=fail)  # type: ignore[arg-type]
    assert failed.compose_projection(ContextPurpose.USER_RESPONSE, scope_id="scope:current") == ()
    assert failed.evidence()[-1].outcome == "failed"

    base = ("x" * (MAX_GUIDANCE_BYTES - 1),)
    reduced = ProductionContextComposer(ContextFrameBuilder())
    assert (
        reduced.compose_projection(
            ContextPurpose.USER_RESPONSE,
            scope_id="scope:current",
            existing_guidance=base,
        )
        == ()
    )
    evidence = reduced.evidence()[-1]
    assert evidence.outcome == "omitted"
    assert evidence.omitted_block_count > 0
    assert evidence.truncation_count == 0
    assert evidence.total_request_context_bytes <= MAX_TOTAL_REQUEST_CONTEXT_BYTES


def test_temporal_provenance_is_typed_and_not_rendered_as_history_or_memory() -> None:
    episode = _episode(CognitionTriggerSource.TEMPORAL, "reconsider later")
    request = production_context_request_factory(
        ContextPurpose.TEMPORAL_WAKE,
        episode.scope_id,
        episode,
    )
    assert request.wake_intent_ref == "wake:current"
    assert request.relevant_intention_ids == ("intention:old",)
    assert all(item.trusted for item in request.source_refs)
    rendered = "\n".join(
        ProductionContextComposer(ContextFrameBuilder()).compose_projection(
            ContextPurpose.TEMPORAL_WAKE,
            scope_id=episode.scope_id,
            owner=episode,
            existing_guidance=build_stable_runtime_guidance(),
        )
    )
    assert "reconsider later" not in rendered
    assert "memory" not in rendered.lower()
    assert "wake:current" not in rendered
