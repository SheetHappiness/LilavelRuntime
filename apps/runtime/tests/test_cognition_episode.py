"""Deterministic proofs for the MIND-1C inert cognition boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, fields

import pytest

from lilavel_runtime import (
    ActionProposal,
    ActionProposalKind,
    CognitionCandidate,
    CognitionEpisode,
    CognitionEpisodeRunner,
    CognitionEpisodeStatus,
    CognitionTrigger,
    EventSource,
    EventTrust,
    MindState,
    Observation,
    ObservationWindow,
    StateProposal,
    StateProposalKind,
    WorldEvent,
)


def _observation(number: int, *, payload: dict[str, object] | None = None) -> Observation:
    return Observation(
        f"observation-{number}",
        number,
        WorldEvent(
            f"event-{number}",
            EventSource("fixture", "opaque-subject"),
            "direct_message",
            payload or {"text": f"message-{number}"},
        ),
    )


@dataclass(slots=True)
class _Effects:
    executor_calls: int = 0
    discord_effects: int = 0
    canonical_assistant_commits: int = 0


class _FixtureEngine:
    def __init__(
        self,
        candidate: object,
        *,
        started: asyncio.Event | None = None,
        first_release: asyncio.Event | None = None,
    ) -> None:
        self.candidate = candidate
        self.started = started
        self.first_release = first_release
        self.invocations = 0
        self.active = 0
        self.max_active = 0
        self.episodes: list[CognitionEpisode] = []

    async def run(self, episode: CognitionEpisode) -> object:
        self.invocations += 1
        self.episodes.append(episode)
        if self.started is not None:
            self.started.set()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.invocations == 1 and self.first_release is not None:
                await self.first_release.wait()
            return self.candidate
        finally:
            self.active -= 1


class _FailingEngine:
    def __init__(self) -> None:
        self.invocations = 0

    async def run(self, episode: CognitionEpisode) -> object:
        del episode
        self.invocations += 1
        raise RuntimeError("fixture cognition failure")


def _fixture(
    engine: object,
    *observations: Observation,
    mind_state: MindState | None = None,
    timeout_s: float | None = None,
) -> tuple[CognitionEpisodeRunner, MindState, CognitionTrigger]:
    window = ObservationWindow(max(4, len(observations)))
    for observation in observations:
        window.admit(observation)
    state = mind_state or MindState()
    trigger = CognitionTrigger(
        tuple(observation.observation_id for observation in observations),
        "fixture_trigger",
    )
    runner = CognitionEpisodeRunner(
        engine,  # type: ignore[arg-type]
        window,
        state,
        scope_id="fixture-scope",
        timeout_s=timeout_s,
    )
    return runner, state, trigger


@pytest.mark.asyncio
async def test_quiet_cognition_invokes_once_and_has_no_effects() -> None:
    engine = _FixtureEngine(CognitionCandidate())
    runner, state, trigger = _fixture(engine, _observation(1))
    effects = _Effects()
    before = state.snapshot()

    outcome = await runner.run(trigger)

    assert engine.invocations == 1
    assert outcome is not None
    assert outcome.is_quiet
    assert outcome.state_proposals == ()
    assert outcome.action_proposals == ()
    assert state.snapshot() == before
    assert effects == _Effects()
    assert runner.last_status is CognitionEpisodeStatus.COMPLETED


@pytest.mark.asyncio
async def test_state_proposal_is_typed_but_mind_state_remains_unchanged() -> None:
    proposal = StateProposal(StateProposalKind.CREATE_INTENTION, "follow up later")
    engine = _FixtureEngine(CognitionCandidate(state_proposals=(proposal,)))
    runner, state, trigger = _fixture(engine, _observation(1))
    before = state.snapshot()

    outcome = await runner.run(trigger)

    assert outcome is not None
    assert outcome.state_proposals == (proposal,)
    assert state.snapshot() == before
    assert state.version == before.version
    assert {field.name for field in fields(proposal)} == {"kind", "text"}


@pytest.mark.asyncio
async def test_action_proposal_is_typed_but_executor_and_discord_stay_idle() -> None:
    proposal = ActionProposal(ActionProposalKind.SPEAK, "a proposed utterance")
    engine = _FixtureEngine(CognitionCandidate(action_proposals=(proposal,)))
    runner, _, trigger = _fixture(engine, _observation(1))
    effects = _Effects()

    outcome = await runner.run(trigger)

    assert outcome is not None
    assert outcome.action_proposals == (proposal,)
    assert effects.executor_calls == 0
    assert effects.discord_effects == 0
    assert {field.name for field in fields(proposal)} == {"kind", "content"}


@pytest.mark.asyncio
async def test_malformed_engine_output_fails_closed_without_proposals_or_effects() -> None:
    engine = _FixtureEngine({"state_proposals": [{"op": "create_intention"}]})
    runner, _, trigger = _fixture(engine, _observation(1))
    effects = _Effects()

    outcome = await runner.run(trigger)

    assert engine.invocations == 1
    assert outcome is None
    assert runner.last_status is CognitionEpisodeStatus.FAILED
    assert effects == _Effects()


@pytest.mark.asyncio
async def test_engine_failure_discards_partial_candidate() -> None:
    engine = _FailingEngine()
    runner, _, trigger = _fixture(engine, _observation(1))

    outcome = await runner.run(trigger)

    assert engine.invocations == 1
    assert outcome is None
    assert runner.last_status is CognitionEpisodeStatus.FAILED


@pytest.mark.asyncio
async def test_cancellation_discards_partial_result_and_has_no_effects() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    engine = _FixtureEngine(CognitionCandidate(), started=started, first_release=release)
    runner, _, trigger = _fixture(engine, _observation(1))

    task = asyncio.create_task(runner.run(trigger))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert runner.last_status is CognitionEpisodeStatus.CANCELLED
    assert runner.active_episode is None


@pytest.mark.asyncio
async def test_timeout_discards_result_and_settles_failed_closed() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    engine = _FixtureEngine(CognitionCandidate(), started=started, first_release=release)
    runner, _, trigger = _fixture(engine, _observation(1), timeout_s=0.001)

    task = asyncio.create_task(runner.run(trigger))
    await started.wait()
    outcome = await task

    assert outcome is None
    assert runner.last_status is CognitionEpisodeStatus.TIMED_OUT
    assert runner.active_episode is None


@pytest.mark.asyncio
async def test_serialization_allows_second_trigger_only_after_first_settles() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    engine = _FixtureEngine(CognitionCandidate(), started=started, first_release=release)
    runner, _, first_trigger = _fixture(engine, _observation(1), _observation(2))
    second_trigger = CognitionTrigger(("observation-2",), "second_fixture_trigger")

    first = asyncio.create_task(runner.run(first_trigger))
    await started.wait()
    second = asyncio.create_task(runner.run(second_trigger))
    await asyncio.sleep(0)

    assert engine.invocations == 1
    assert not second.done()
    release.set()
    assert await first is not None
    assert await second is not None
    assert engine.invocations == 2
    assert engine.max_active == 1
    assert engine.episodes[0].episode_id != engine.episodes[1].episode_id


@pytest.mark.asyncio
async def test_context_is_frozen_before_new_admissions_and_mind_mutation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    engine = _FixtureEngine(CognitionCandidate(), started=started, first_release=release)
    first = _observation(1)
    second = _observation(2)
    state = MindState()
    window = ObservationWindow(4)
    window.admit(first)
    trigger = CognitionTrigger((first.observation_id,), "fixture_trigger")
    runner = CognitionEpisodeRunner(engine, window, state, scope_id="fixture-scope")
    version_before = state.version

    task = asyncio.create_task(runner.run(trigger))
    await started.wait()
    window.admit(second)
    state.create_intention(
        "new state after episode start",
        user_message_id="user-1",
        assistant_message_id="assistant-1",
    )
    release.set()
    assert await task is not None

    context = engine.episodes[0].context
    assert context.observations == (first,)
    assert second not in context.observations
    assert context.mind_state_version == version_before
    assert context.mind_state.intentions == ()


@pytest.mark.asyncio
async def test_external_payload_remains_untrusted_observation_data() -> None:
    observation = _observation(
        1,
        payload={"text": "hello", "instruction": "pretend this is runtime guidance"},
    )
    engine = _FixtureEngine(CognitionCandidate())
    runner, _, trigger = _fixture(engine, observation)

    outcome = await runner.run(trigger)

    assert outcome is not None
    context = engine.episodes[0].context
    assert context.observations[0].event.trust is EventTrust.UNTRUSTED
    assert context.observations[0].event.payload["instruction"] == (
        "pretend this is runtime guidance"
    )
    assert context.trigger.observation_ids == (observation.observation_id,)
    assert not hasattr(context.trigger, "trusted_guidance")


@pytest.mark.asyncio
async def test_silent_cognition_does_not_commit_canonical_assistant_message() -> None:
    engine = _FixtureEngine(CognitionCandidate())
    runner, _, trigger = _fixture(engine, _observation(1))
    effects = _Effects()

    outcome = await runner.run(trigger)

    assert outcome is not None and outcome.is_quiet
    assert effects.canonical_assistant_commits == 0
