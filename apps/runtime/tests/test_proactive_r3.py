"""Focused PROACTIVE-V0-R3 deferred-commitment proofs."""

from __future__ import annotations

import asyncio
from queue import Queue
from typing import Any

import pytest
from lilavel_core import (
    GenerationAccepted,
    GenerationCompleted,
    GenerationEvent,
    ModelRequest,
    TextDelta,
)

from lilavel_runtime import (
    PROACTIVE_V0_R3_APPRAISAL_CASES,
    ActionProposalKind,
    CognitionCandidate,
    CognitionContext,
    CognitionEpisode,
    CognitionEvidenceStage,
    CognitionModelEvidence,
    CognitionTrigger,
    CognitionTriggerSource,
    IntentionKind,
    LocalCognitionEngine,
    MindState,
    ProactiveR3Expected,
)
from lilavel_runtime.cognition_model import (
    APPRAISAL_REASON,
    IDLE_REASON,
    CognitionParseOutcome,
    _parse_candidate_with_outcome,  # pyright: ignore[reportPrivateUsage]
)


class _Generation:
    def __init__(self, generation_id: str) -> None:
        self.generation_id = generation_id
        self.epoch = 1
        self.events_queue: Queue[GenerationEvent] = Queue()

    def emit(self, event: GenerationEvent) -> None:
        self.events_queue.put(event)

    def events(self) -> Any:
        while True:
            event = self.events_queue.get()
            yield event
            if isinstance(event, GenerationCompleted):
                return


class _Model:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.generations: list[_Generation] = []

    def generate_for_run(
        self,
        request: ModelRequest,
        *,
        scope_id: str,
        logical_run_id: str,
    ) -> _Generation:
        del scope_id, logical_run_id
        self.requests.append(request)
        generation = _Generation(f"generation:{len(self.generations) + 1}")
        self.generations.append(generation)
        return generation

    def generate(self, request: ModelRequest) -> _Generation:
        return self.generate_for_run(request, scope_id="runtime", logical_run_id="test")

    def cancel(self, generation_id: str) -> bool:
        del generation_id
        return True


def _finish(generation: _Generation, text: str) -> None:
    generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
    generation.emit(TextDelta(generation.generation_id, generation.epoch, text))
    generation.emit(GenerationCompleted(generation.generation_id, generation.epoch))


def _idle_episode(state: MindState, intention_id: str) -> CognitionEpisode:
    trigger = CognitionTrigger(
        (),
        IDLE_REASON,
        source=CognitionTriggerSource.INTERNAL,
        source_refs=("idle:due", intention_id),
    )
    return CognitionEpisode(
        "episode:r3",
        CognitionContext("episode:r3", "runtime", trigger, (), state.snapshot()),
    )


def test_appraisal_contract_has_one_bounded_semantic_class() -> None:
    deferred = _parse_candidate_with_outcome(
        '{"action":"create_intention","kind":"deferred_commitment","text":"send later"}',
        APPRAISAL_REASON,
    )
    initiative = _parse_candidate_with_outcome(
        '{"action":"create_intention","kind":"initiative","text":"consider later"}',
        APPRAISAL_REASON,
    )

    assert deferred[1] is CognitionParseOutcome.CREATE_INTENTION
    assert deferred[0].state_proposals[0].intention_kind is IntentionKind.DEFERRED_COMMITMENT
    assert initiative[0].state_proposals[0].intention_kind is IntentionKind.INITIATIVE

    # The compatibility shape can only become initiative; it cannot create a
    # deferred commitment without the explicit bounded kind field.
    legacy = _parse_candidate_with_outcome(
        '{"action":"create_intention","text":"consider later"}', APPRAISAL_REASON
    )
    assert legacy[0].state_proposals[0].intention_kind is IntentionKind.INITIATIVE
    assert (
        _parse_candidate_with_outcome(
            '{"action":"create_intention","kind":"deferred_commitment","text":""}',
            APPRAISAL_REASON,
        )[1]
        is CognitionParseOutcome.INVALID
    )


def test_mind_state_preserves_bounded_kind_and_provenance_only() -> None:
    state = MindState()
    intention = state.create_intention(
        "send the accepted follow-up",
        user_message_id="user-message",
        assistant_message_id="assistant-message",
        kind=IntentionKind.DEFERRED_COMMITMENT,
    )

    assert intention is not None
    assert intention.kind is IntentionKind.DEFERRED_COMMITMENT
    assert intention.user_message_id == "user-message"
    assert intention.assistant_message_id == "assistant-message"
    assert not hasattr(intention, "channel_id")
    assert not hasattr(intention, "discord_id")


@pytest.mark.asyncio
async def test_initiative_idle_remains_silence_biased_and_cannot_fulfill() -> None:
    model = _Model()
    evidence: list[CognitionModelEvidence] = []
    engine = LocalCognitionEngine(model, lambda _: (), evidence_sink=evidence.append)
    state = MindState()
    intention = state.create_intention(
        "a discretionary thought",
        user_message_id="user-initiative",
        assistant_message_id="assistant-initiative",
        kind=IntentionKind.INITIATIVE,
    )
    assert intention is not None

    task = asyncio.create_task(engine.run(_idle_episode(state, intention.intention_id)))
    while not model.generations:
        await asyncio.sleep(0)
    _finish(model.generations[0], '{"action":"stay_silent"}')
    candidate = await task

    assert isinstance(candidate, CognitionCandidate)
    assert candidate.action_proposals[0].kind is ActionProposalKind.STAY_SILENT
    assert not candidate.action_proposals[0].content
    assert evidence[-1].stage is CognitionEvidenceStage.PARSE
    assert evidence[-1].result == "stay_silent"

    task = asyncio.create_task(engine.run(_idle_episode(state, intention.intention_id)))
    while len(model.generations) < 2:
        await asyncio.sleep(0)
    _finish(model.generations[1], '{"action":"fulfill","text":"wrong lane"}')
    candidate = await task
    assert isinstance(candidate, CognitionCandidate)
    assert candidate.action_proposals == ()


@pytest.mark.asyncio
async def test_due_deferred_commitment_uses_internal_fulfill_and_keeps_effect_inert() -> None:
    model = _Model()
    evidence: list[CognitionModelEvidence] = []
    engine = LocalCognitionEngine(model, lambda _: (), evidence_sink=evidence.append)
    state = MindState()
    intention = state.create_intention(
        "send the accepted reminder",
        user_message_id="user-deferred",
        assistant_message_id="assistant-deferred",
        kind=IntentionKind.DEFERRED_COMMITMENT,
    )
    assert intention is not None

    task = asyncio.create_task(engine.run(_idle_episode(state, intention.intention_id)))
    while not model.generations:
        await asyncio.sleep(0)
    assert model.requests[0].prompt is not None
    assert "due deferred commitment" in model.requests[0].prompt
    _finish(model.generations[0], '{"action":"fulfill","text":"the reminder"}')
    candidate = await task

    assert isinstance(candidate, CognitionCandidate)
    assert len(candidate.action_proposals) == 1
    proposal = candidate.action_proposals[0]
    assert proposal.kind is ActionProposalKind.SPEAK
    assert proposal.content == "the reminder"
    assert not hasattr(proposal, "destination")
    assert evidence[-1].result == "fulfill"

    # The due commitment does not accept ordinary discretionary silence or a
    # model-selected external destination.
    task = asyncio.create_task(engine.run(_idle_episode(state, intention.intention_id)))
    while len(model.generations) < 2:
        await asyncio.sleep(0)
    _finish(model.generations[1], '{"action":"stay_silent"}')
    candidate = await task
    assert isinstance(candidate, CognitionCandidate)
    assert candidate.action_proposals == ()


def test_r3_eval_cases_cover_requested_appraisal_edges() -> None:
    ids = {case.case_id for case in PROACTIVE_V0_R3_APPRAISAL_CASES}
    assert ids == {
        "explicit-reminder-after-delay",
        "tell-me-later-if-x",
        "reminder-already-delivered",
        "vague-later-continuation",
        "model-curiosity",
        "user-cancels-commitment",
    }
    assert (
        sum(
            case.expected is ProactiveR3Expected.DEFERRED_COMMITMENT
            for case in PROACTIVE_V0_R3_APPRAISAL_CASES
        )
        == 2
    )
