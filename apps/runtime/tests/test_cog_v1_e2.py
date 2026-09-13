"""Deterministic COG-V1-E2 ambient intervention lifecycle proofs."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from lilavel_contracts import ToolCall, ToolEffect, ToolResult, ToolResultStatus, ToolSpec
from lilavel_core import (
    CognitionReasonCode,
    GenerationCompleted,
    GenerationEvent,
    ModelRequest,
    TextDelta,
    ToolBatchCorrelation,
)

from lilavel_runtime import (
    ActionApplicationStatus,
    ActionProposal,
    ActionProposalKind,
    ActivityState,
    AmbientSpeechRolloutMode,
    ApplicationToolRegistry,
    CognitionCandidate,
    CognitionEpisode,
    CognitionEpisodeRunner,
    CognitionOutcome,
    CognitionTrigger,
    CognitionTriggerSource,
    DeterministicInterventionPolicy,
    DeterministicToolSessionFactory,
    EventSource,
    FloorState,
    FreshnessBucket,
    FreshnessClass,
    HandlingState,
    InterventionBudgetState,
    LocalCognitionEngine,
    MindState,
    MindStateProvenance,
    Observation,
    ObservationWindow,
    ProposalApplicationCoordinator,
    ProposalApplicationStatus,
    RecentSpeechState,
    SocialPermissionContext,
    SocialSensitivity,
    SpeakingSurfaceState,
    SpeechAccounting,
    StateApplicationStatus,
    TemporalApplicationStatus,
    TemporalCoordinator,
    ToolBinding,
    WorldEvent,
    parse_ambient_intervention_candidate,
)

_DEFAULT_DISPOSITION = {
    "aim": "challenge",
    "stance": "skeptical",
    "engagement": "normal",
    "directness": "normal",
    "desired_length": "low",
    "humor_allowed": False,
    "question_policy": "avoid",
    "initiative": "high",
}


def _ambient_json(
    intervention: str = "interject",
    *,
    reason_codes: tuple[str, ...] = ("unique_information",),
    disposition: dict[str, object] | None | object = _DEFAULT_DISPOSITION,
    utterance: str | None = "A material constraint is still unresolved.",
    state_proposals: list[dict[str, object]] | None = None,
    temporal_proposals: list[dict[str, object]] | None = None,
    **extra: object,
) -> str:
    if disposition is _DEFAULT_DISPOSITION:
        disposition = None if intervention == "none" else dict(_DEFAULT_DISPOSITION)
    value: dict[str, object] = {
        "intervention": intervention,
        "reason_codes": list(reason_codes),
        "disposition": disposition,
        "utterance": utterance,
        "state_proposals": state_proposals or [],
        "temporal_proposals": temporal_proposals or [],
    }
    value.update(extra)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parsed_candidate(raw: str):
    candidate = parse_ambient_intervention_candidate(raw)
    assert candidate is not None
    return candidate


def _observation(kind: str = "ambient_event") -> Observation:
    return Observation(
        "observation-1",
        1,
        WorldEvent(
            "event-1",
            EventSource("fixture", "opaque-subject"),
            kind,
            {"text": "A runtime-admitted ambient observation."},
        ),
    )


class _CandidateEngine:
    def __init__(self, candidate: CognitionCandidate) -> None:
        self.candidate = candidate
        self.invocations = 0

    async def run(self, episode: CognitionEpisode) -> object:
        del episode
        self.invocations += 1
        return self.candidate


@dataclass(slots=True)
class _Generation:
    generation_id: str
    response: str
    epoch: int = 1

    def events(self) -> Iterator[GenerationEvent]:
        yield TextDelta(self.generation_id, self.epoch, self.response)
        yield GenerationCompleted(self.generation_id, self.epoch)


class _ModelRuntime:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[ModelRequest] = []
        self.generations: list[_Generation] = []

    def generate_for_run(
        self,
        request: ModelRequest,
        *,
        scope_id: str,
        logical_run_id: str,
    ) -> _Generation:
        del scope_id, logical_run_id
        self.calls.append(request)
        generation = _Generation(f"generation-{len(self.calls)}", self.response)
        self.generations.append(generation)
        return generation

    def generate(self, request: ModelRequest) -> _Generation:
        return self.generate_for_run(request, scope_id="legacy", logical_run_id="legacy")

    def cancel(self, generation_id: str) -> bool:
        del generation_id
        return True


async def _run_candidate(
    candidate: CognitionCandidate,
    *,
    source: CognitionTriggerSource = CognitionTriggerSource.EXTERNAL,
    state: MindState | None = None,
) -> tuple[CognitionOutcome, MindState]:
    actual_state = state or MindState()
    window = ObservationWindow(4)
    if source is CognitionTriggerSource.EXTERNAL:
        window.admit(_observation())
        trigger = CognitionTrigger(("observation-1",), "ambient_fixture")
    elif source is CognitionTriggerSource.TEMPORAL:
        trigger = CognitionTrigger(
            (),
            "temporal_fixture",
            source=source,
            wake_intent_id="wake-1",
            source_refs=("intention-1",),
        )
    else:
        trigger = CognitionTrigger((), "internal_fixture", source=source, source_refs=("fixture",))
    runner = CognitionEpisodeRunner(
        _CandidateEngine(candidate), window, actual_state, scope_id="fixture-scope"
    )
    outcome = await runner.run(trigger)
    assert outcome is not None
    return outcome, actual_state


def _permission_context(
    *,
    speaking_surface: SpeakingSurfaceState = SpeakingSurfaceState.AVAILABLE,
    freshness: FreshnessBucket = FreshnessBucket.FRESH,
    floor: FloorState = FloorState.FREE,
    recent_speech: RecentSpeechState = RecentSpeechState.CLEAR,
    intervention_budget: InterventionBudgetState = InterventionBudgetState.AVAILABLE,
    handling: HandlingState = HandlingState.UNRESOLVED,
    response_obligation: bool = True,
    continuity_current: bool = True,
) -> SocialPermissionContext:
    return SocialPermissionContext(
        speaking_surface=speaking_surface,
        freshness_class=FreshnessClass.REACTIVE,
        freshness=freshness,
        activity=ActivityState.CURRENT,
        floor=floor,
        recent_speech=recent_speech,
        intervention_budget=intervention_budget,
        handling=handling,
        sensitivity=SocialSensitivity.ORDINARY,
        response_obligation=response_obligation,
        continuity_current=continuity_current,
    )


def _resolver(
    current: list[SocialPermissionContext],
) -> Callable[[CognitionOutcome, int, ActionProposal], SocialPermissionContext]:
    def resolve(
        _outcome: CognitionOutcome, _index: int, _proposal: ActionProposal
    ) -> SocialPermissionContext:
        return current[0]

    return resolve


type Executor = Callable[[ToolBatchCorrelation, ToolCall, threading.Event], ToolResult]


def _boundary(
    state: MindState,
    *,
    mode: AmbientSpeechRolloutMode,
    current: list[SocialPermissionContext],
    executor: Executor | None = None,
    accounting: SpeechAccounting | None = None,
    temporal: TemporalCoordinator | None = None,
) -> tuple[ProposalApplicationCoordinator, list[ToolCall]]:
    calls: list[ToolCall] = []

    def default_executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        calls.append(call)
        return ToolResult(call.call_id, ToolResultStatus.OK, None, effect=ToolEffect.CONFIRMED)

    actual_executor = executor or default_executor
    spec = ToolSpec(
        "fixture.speak",
        "Emit one bounded fixture utterance.",
        {
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 4096}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    registry = ApplicationToolRegistry([ToolBinding(spec, actual_executor)])
    factory = DeterministicToolSessionFactory(
        registry,
        exposed_tool_names=(spec.name,),
        executor_deadline=0.05,
        containment_deadline=0.2,
    )
    boundary = ProposalApplicationCoordinator(
        state,
        scope_id="fixture-scope",
        state_provenance=MindStateProvenance("user-1", "assistant-1"),
        tool_registry=registry,
        tool_session_factory=factory,
        action_tool_names={ActionProposalKind.SPEAK: spec.name},
        runtime_instance_id="fixture-runtime",
        ambient_speech_mode=mode,
        speech_context_resolver=_resolver(current),
        speech_policy=DeterministicInterventionPolicy(),
        speech_accounting=accounting,
        temporal_coordinator=temporal,
    )
    return boundary, calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intervention", "reason", "has_speech"),
    [
        ("none", "no_new_value", False),
        ("respond", "response_obligation", True),
        ("interject", "unique_information", True),
    ],
)
async def test_bounded_ambient_schema_compiles_none_or_one_speak_proposal(
    intervention: str, reason: str, has_speech: bool
) -> None:
    parsed = _parsed_candidate(
        _ambient_json(
            intervention,
            reason_codes=(reason,),
            utterance=None if intervention == "none" else "Useful incremental context.",
        )
    )
    candidate = parsed.to_cognition_candidate()

    assert len(candidate.action_proposals) == (1 if has_speech else 0)
    assert candidate.ambient_intervention is not None
    assert candidate.ambient_intervention.intervention.value == intervention
    if has_speech:
        assert candidate.ambient_intervention.disposition is not None
        assert candidate.action_proposals[0].kind is ActionProposalKind.SPEAK
    else:
        assert candidate.ambient_intervention.disposition is None

    outcome, _ = await _run_candidate(candidate)
    assert len(outcome.action_proposals) == (1 if has_speech else 0)


@pytest.mark.parametrize(
    "raw",
    [
        _ambient_json("none", reason_codes=("no_new_value",), utterance="not silent"),
        _ambient_json("respond", reason_codes=("response_obligation",), utterance=None),
        _ambient_json("interject", reason_codes=("unique_information",), disposition=None),
        _ambient_json("unknown", reason_codes=("unique_information",)),
        _ambient_json("interject", reason_codes=("not_a_reason",)),
        _ambient_json("interject", reason_codes=("unique_information",), destination="#general"),
        _ambient_json(
            "interject",
            reason_codes=("unique_information",),
            utterance="x" * 4097,
        ),
    ],
)
def test_invalid_ambient_candidates_fail_closed(raw: str) -> None:
    assert parse_ambient_intervention_candidate(raw) is None


def test_duplicate_ambient_json_keys_are_rejected() -> None:
    raw = _ambient_json("none", reason_codes=("no_new_value",))
    assert parse_ambient_intervention_candidate(raw[:-1] + ',"utterance":null}') is None


@pytest.mark.asyncio
async def test_ambient_engine_uses_one_inference_and_never_invokes_disposition_planner() -> None:
    runtime = _ModelRuntime(_ambient_json("interject"))
    engine = LocalCognitionEngine(runtime, lambda trigger_id: ())
    state = MindState()
    window = ObservationWindow(4)
    window.admit(_observation())
    runner = CognitionEpisodeRunner(engine, window, state, scope_id="fixture-scope")

    outcome = await runner.run(CognitionTrigger(("observation-1",), "ambient_fixture"))

    assert outcome is not None
    assert len(runtime.calls) == 1
    assert outcome.ambient_intervention is not None
    assert outcome.action_proposals[0].kind is ActionProposalKind.SPEAK
    assert engine.ambient_evidence()[-1].candidate_valid is True
    assert engine.ambient_evidence()[-1].speak_proposed is True
    prompt = "\n".join(runtime.calls[0].system_prompt)
    assert "not a direct user turn" in prompt
    assert "destination" in prompt


@pytest.mark.asyncio
async def test_malformed_ambient_model_output_is_quiet_without_partial_trust() -> None:
    runtime = _ModelRuntime("not json")
    engine = LocalCognitionEngine(runtime, lambda trigger_id: ())
    state = MindState()
    window = ObservationWindow(4)
    window.admit(_observation())
    runner = CognitionEpisodeRunner(engine, window, state, scope_id="fixture-scope")

    outcome = await runner.run(CognitionTrigger(("observation-1",), "ambient_fixture"))

    assert outcome is not None
    assert outcome.is_quiet
    assert len(runtime.calls) == 1
    assert engine.ambient_evidence()[-1].candidate_valid is False
    assert engine.ambient_evidence()[-1].speak_proposed is False


@pytest.mark.asyncio
async def test_direct_user_observation_does_not_enter_ambient_model_path() -> None:
    runtime = _ModelRuntime(_ambient_json("interject"))
    engine = LocalCognitionEngine(runtime, lambda trigger_id: ())
    window = ObservationWindow(4)
    window.admit(_observation("direct_message"))
    runner = CognitionEpisodeRunner(engine, window, MindState(), scope_id="fixture-scope")

    outcome = await runner.run(CognitionTrigger(("observation-1",), "direct_user"))

    assert outcome is not None
    assert outcome.is_quiet
    assert runtime.calls == []
    assert engine.ambient_evidence() == ()


@pytest.mark.asyncio
async def test_off_mode_blocks_ambient_speech_before_p4() -> None:
    candidate = _parsed_candidate(_ambient_json("interject")).to_cognition_candidate()
    outcome, state = await _run_candidate(candidate)
    current = [_permission_context()]
    boundary, calls = _boundary(state, mode=AmbientSpeechRolloutMode.OFF, current=current)
    outcome = boundary.application_authority.bind_outcome(outcome)

    result = boundary.apply_sync(outcome)

    assert result.actions.status is ActionApplicationStatus.REJECTED
    assert result.actions.proposals[0].status is ToolResultStatus.DENIED
    assert result.actions.proposals[0].reason_code == "ambient_speech_off"
    assert calls == []
    assert boundary.speech_evidence()[-1].external_attempted is False


@pytest.mark.asyncio
async def test_shadow_runs_revalidation_but_suppresses_external_speech_and_budget() -> None:
    candidate = _parsed_candidate(_ambient_json("interject")).to_cognition_candidate()
    outcome, state = await _run_candidate(candidate)
    current = [_permission_context()]
    accounting = SpeechAccounting(intervention_budget=2)
    boundary, calls = _boundary(
        state,
        mode=AmbientSpeechRolloutMode.SHADOW,
        current=current,
        accounting=accounting,
    )
    outcome = boundary.application_authority.bind_outcome(outcome)

    result = boundary.apply_sync(outcome)

    assert result.actions.proposals[0].reason_code == "shadow_only"
    assert boundary.speech_evidence()[-1].shadow_would_speak is True
    assert calls == []
    assert accounting.confirmed_speech_count == 0
    assert accounting.snapshot()[0] is RecentSpeechState.CLEAR


@pytest.mark.asyncio
async def test_live_allowed_speech_uses_existing_p4_route_once_and_records_confirmation() -> None:
    candidate = _parsed_candidate(_ambient_json("interject")).to_cognition_candidate()
    outcome, state = await _run_candidate(candidate)
    current = [_permission_context()]
    accounting = SpeechAccounting(intervention_budget=2)
    boundary, calls = _boundary(
        state,
        mode=AmbientSpeechRolloutMode.LIVE,
        current=current,
        accounting=accounting,
    )
    outcome = boundary.application_authority.bind_outcome(outcome)

    result = boundary.apply_sync(outcome)

    assert result.status is ProposalApplicationStatus.APPLIED
    assert result.actions.status is ActionApplicationStatus.APPLIED
    assert len(calls) == 1
    assert calls[0].tool_name == "fixture.speak"
    assert boundary.speech_evidence()[-1].revalidation.value == "allowed"
    assert boundary.speech_evidence()[-1].external_attempted is True
    assert accounting.confirmed_speech_count == 1
    assert accounting.snapshot()[0] is RecentSpeechState.RECENT


@pytest.mark.asyncio
async def test_effect_time_revalidation_denies_candidate_that_became_stale() -> None:
    candidate = _parsed_candidate(_ambient_json("interject")).to_cognition_candidate()
    outcome, state = await _run_candidate(candidate)
    current = [_permission_context()]
    boundary, calls = _boundary(state, mode=AmbientSpeechRolloutMode.LIVE, current=current)
    outcome = boundary.application_authority.bind_outcome(outcome)
    current[0] = _permission_context(freshness=FreshnessBucket.STALE)

    result = boundary.apply_sync(outcome)

    assert result.actions.proposals[0].status is ToolResultStatus.DENIED
    assert result.actions.proposals[0].reason_code == CognitionReasonCode.STALE_CONTEXT.value
    assert calls == []
    assert boundary.speech_evidence()[-1].external_attempted is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("context", "reason"),
    [
        (
            _permission_context(speaking_surface=SpeakingSurfaceState.UNAVAILABLE),
            "no_speaking_surface",
        ),
        (_permission_context(floor=FloorState.BUSY), "floor_busy"),
        (
            _permission_context(intervention_budget=InterventionBudgetState.EXHAUSTED),
            "intervention_budget_exhausted",
        ),
        (_permission_context(recent_speech=RecentSpeechState.RECENT), "social_backoff"),
    ],
)
async def test_current_social_denials_stop_ambient_interject(
    context: SocialPermissionContext, reason: str
) -> None:
    candidate = _parsed_candidate(_ambient_json("interject")).to_cognition_candidate()
    outcome, state = await _run_candidate(candidate)
    boundary, calls = _boundary(state, mode=AmbientSpeechRolloutMode.LIVE, current=[context])
    outcome = boundary.application_authority.bind_outcome(outcome)

    result = boundary.apply_sync(outcome)

    assert result.actions.proposals[0].status is ToolResultStatus.DENIED
    assert result.actions.proposals[0].reason_code == reason
    assert calls == []


@pytest.mark.asyncio
async def test_respond_remains_distinct_from_interject_backoff() -> None:
    candidate = _parsed_candidate(
        _ambient_json(
            "respond",
            reason_codes=("response_obligation",),
            utterance="I can still answer the outstanding point.",
        )
    ).to_cognition_candidate()
    outcome, state = await _run_candidate(candidate)
    context = _permission_context(recent_speech=RecentSpeechState.RECENT)
    boundary, calls = _boundary(state, mode=AmbientSpeechRolloutMode.LIVE, current=[context])
    outcome = boundary.application_authority.bind_outcome(outcome)

    result = boundary.apply_sync(outcome)

    assert result.status is ProposalApplicationStatus.APPLIED
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_denied_speech_does_not_discard_valid_state_or_temporal_effects() -> None:
    clock_now = datetime(2030, 1, 1, tzinfo=UTC)
    candidate = _parsed_candidate(
        _ambient_json(
            "interject",
            state_proposals=[{"kind": "create_intention", "text": "retain this internal state"}],
            temporal_proposals=[
                {
                    "reason": "reconsider retained state",
                    "not_before": (clock_now + timedelta(seconds=10)).isoformat(),
                    "intention_ref": "intention-1",
                }
            ],
        )
    ).to_cognition_candidate()
    outcome, state = await _run_candidate(candidate)
    temporal = TemporalCoordinator(scope_id="fixture-scope", clock=lambda: clock_now)
    boundary, calls = _boundary(
        state,
        mode=AmbientSpeechRolloutMode.LIVE,
        current=[_permission_context(freshness=FreshnessBucket.STALE)],
        temporal=temporal,
    )
    outcome = boundary.application_authority.bind_outcome(outcome)

    result = boundary.apply_sync(outcome)

    assert result.status is ProposalApplicationStatus.PARTIAL
    assert result.state.status is StateApplicationStatus.APPLIED
    assert result.temporal.status is TemporalApplicationStatus.APPLIED
    assert result.actions.status is ActionApplicationStatus.REJECTED
    assert state.version == 1
    assert len(temporal.pending_intents()) == 1
    assert calls == []


@pytest.mark.asyncio
async def test_unknown_tool_effect_does_not_confirm_speech_or_update_accounting() -> None:
    candidate = _parsed_candidate(_ambient_json("interject")).to_cognition_candidate()
    outcome, state = await _run_candidate(candidate)
    accounting = SpeechAccounting(intervention_budget=2)
    attempts: list[ToolCall] = []

    def unknown_executor(
        correlation: ToolBatchCorrelation, call: ToolCall, cancelled: threading.Event
    ) -> ToolResult:
        del correlation, cancelled
        attempts.append(call)
        return ToolResult(
            call.call_id,
            ToolResultStatus.TIMED_OUT,
            None,
            reason_code="executor_timeout",
            effect=ToolEffect.UNKNOWN,
        )

    boundary, _ = _boundary(
        state,
        mode=AmbientSpeechRolloutMode.LIVE,
        current=[_permission_context()],
        executor=unknown_executor,
        accounting=accounting,
    )
    outcome = boundary.application_authority.bind_outcome(outcome)

    result = boundary.apply_sync(outcome)

    assert result.actions.proposals[0].effect is ToolEffect.UNKNOWN
    assert accounting.confirmed_speech_count == 0
    assert accounting.snapshot()[0] is RecentSpeechState.CLEAR
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_internal_and_temporal_speak_sources_share_the_effect_time_guard() -> None:
    candidate = _parsed_candidate(_ambient_json("interject")).to_cognition_candidate()
    for source in (CognitionTriggerSource.INTERNAL, CognitionTriggerSource.TEMPORAL):
        outcome, state = await _run_candidate(candidate, source=source)
        boundary, calls = _boundary(
            state,
            mode=AmbientSpeechRolloutMode.LIVE,
            current=[_permission_context(freshness=FreshnessBucket.STALE)],
        )
        outcome = boundary.application_authority.bind_outcome(outcome)

        result = boundary.apply_sync(outcome)

        assert result.actions.proposals[0].status is ToolResultStatus.DENIED
        assert calls == []


def test_existing_action_vocabulary_has_no_new_effect_lane() -> None:
    assert {kind.value for kind in ActionProposalKind} == {"speak", "stay_silent"}
    assert ActionProposal(ActionProposalKind.STAY_SILENT).content is None
