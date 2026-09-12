"""Focused RUNTIME-H2 failure and result-semantics proofs."""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from lilavel_contracts import ToolResult, ToolResultStatus, ToolSpec
from lilavel_core import ToolBatchCorrelation

from lilavel_runtime import (
    ActionApplication,
    ActionApplicationStatus,
    ActionProposal,
    ActionProposalKind,
    CognitionCandidate,
    CognitionEpisode,
    CognitionEpisodeRunner,
    CognitionTrigger,
    DeterministicToolSessionFactory,
    LilavelRuntime,
    MindState,
    ProposalApplicationCoordinator,
    ProposalApplicationStatus,
    RuntimeFailed,
    RuntimeState,
    SemanticActor,
    SemanticActorPoisoned,
    SemanticActorShutdownTimeout,
    SemanticActorState,
    SemanticCancellationToken,
    SemanticEpisode,
    SemanticEpisodeStatus,
    SemanticPriority,
    SemanticSourceKind,
    StateApplication,
    StateApplicationStatus,
    StateProposal,
    StateProposalKind,
    TemporalApplication,
    TemporalApplicationStatus,
    TemporalCoordinator,
    TemporalProposal,
)
from lilavel_runtime.proposal_application import MindStateProvenance
from lilavel_runtime.tool_registry import ApplicationToolRegistry, ToolBinding


async def _wait_until(predicate: Any) -> None:
    async with asyncio.timeout(2.0):
        while not predicate():
            await asyncio.sleep(0)


async def _uncontainable(release: asyncio.Event) -> object:
    while not release.is_set():
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            continue
    return "released"


async def _noop_executor(
    episode: SemanticEpisode, cancellation: SemanticCancellationToken
) -> object:
    del episode, cancellation
    return None


def _make_uncontainable_executor(
    release: asyncio.Event,
) -> Callable[[SemanticEpisode, SemanticCancellationToken], Awaitable[object]]:
    async def execute(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode, cancellation
        return await _uncontainable(release)

    return execute


def _request(
    actor: SemanticActor,
    request_id: str,
    priority: SemanticPriority,
    executor: Any,
) -> Any:
    return actor.create_request(
        request_id,
        source_kind=(
            SemanticSourceKind.USER
            if priority is SemanticPriority.USER
            else SemanticSourceKind.INTERNAL
        ),
        priority=priority,
        executor=executor,
    )


@pytest.mark.asyncio
async def test_poison_propagates_to_runtime_and_closes_both_semantic_lanes() -> None:
    release = asyncio.Event()
    actor = SemanticActor(scope_id="runtime", settlement_timeout=0.01)
    runtime = LilavelRuntime(semantic_actor=actor, shutdown_timeout=1.0)
    await runtime.start()

    active = await actor.admit(
        _request(
            actor,
            "uncertain",
            SemanticPriority.NON_USER,
            _make_uncontainable_executor(release),
        )
    )
    await _wait_until(lambda: actor.active_count == 1)
    successor = await actor.admit(
        _request(actor, "successor", SemanticPriority.USER, _noop_executor)
    )

    assert (await active.wait()).status in {
        SemanticEpisodeStatus.CANCELLED,
        SemanticEpisodeStatus.FAILED,
    }
    assert (await successor.wait()).status is SemanticEpisodeStatus.CANCELLED
    await _wait_until(lambda: runtime.state is RuntimeState.FAILED)

    health = runtime.health()
    assert health.state is RuntimeState.FAILED
    assert health.healthy is False
    assert health.semantic_actor_state is SemanticActorState.POISONED
    assert health.failure_code == "SemanticActorPoisoned"
    assert isinstance(actor.failure, SemanticActorPoisoned)
    assert actor.failure.reason_code == "uncontainable_settlement"

    rejected_user = await actor.admit(
        _request(actor, "new-user", SemanticPriority.USER, _noop_executor)
    )
    rejected_non_user = await actor.admit(
        _request(
            actor, "new-internal", SemanticPriority.NON_USER, _noop_executor
        )
    )
    assert rejected_user.status.value == "rejected"
    assert rejected_non_user.status.value == "rejected"
    assert (await rejected_user.wait()).reason_code == "actor_poisoned"
    assert (await rejected_non_user.wait()).reason_code == "actor_poisoned"

    release.set()
    await _wait_until(lambda: not actor._uncontained)  # type: ignore[attr-defined]
    with pytest.raises((RuntimeFailed, SemanticActorShutdownTimeout)):
        await runtime.stop()
    assert runtime.state is RuntimeState.FAILED


@pytest.mark.asyncio
async def test_poison_during_shutdown_is_bounded_and_tracked() -> None:
    release = asyncio.Event()
    actor = SemanticActor(scope_id="runtime", settlement_timeout=0.01)
    runtime = LilavelRuntime(semantic_actor=actor, shutdown_timeout=1.0)
    await runtime.start()
    active = await actor.admit(
        _request(
            actor,
            "shutdown-uncertain",
            SemanticPriority.NON_USER,
            _make_uncontainable_executor(release),
        )
    )
    await _wait_until(lambda: actor.active_count == 1)

    try:
        with pytest.raises((RuntimeFailed, SemanticActorShutdownTimeout)):
            await runtime.stop()
    finally:
        release.set()
        await _wait_until(lambda: not actor._uncontained)  # type: ignore[attr-defined]

    assert (await active.wait()).status in {
        SemanticEpisodeStatus.CANCELLED,
        SemanticEpisodeStatus.FAILED,
    }
    assert runtime.state is RuntimeState.FAILED
    assert actor.state is SemanticActorState.POISONED


@pytest.mark.asyncio
async def test_normal_cancellation_settlement_does_not_poison_runtime() -> None:
    started = asyncio.Event()
    user_started = asyncio.Event()
    actor = SemanticActor(scope_id="runtime", settlement_timeout=0.1)
    runtime = LilavelRuntime(semantic_actor=actor)
    await runtime.start()

    async def cancellable(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode
        started.set()
        await cancellation.wait()
        return "cancelled"

    async def user(episode: SemanticEpisode, cancellation: SemanticCancellationToken) -> object:
        del episode, cancellation
        user_started.set()
        return "user"

    active = await actor.admit(
        _request(actor, "cancellable", SemanticPriority.NON_USER, cancellable)
    )
    await started.wait()
    successor = await actor.admit(_request(actor, "user", SemanticPriority.USER, user))

    assert (await active.wait()).status is SemanticEpisodeStatus.CANCELLED
    assert (await successor.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert user_started.is_set()
    assert actor.state is SemanticActorState.RUNNING
    assert actor.failure is None
    assert runtime.state is RuntimeState.RUNNING
    await runtime.stop()


@pytest.mark.parametrize(
    ("state", "temporal", "actions", "expected"),
    (
        (
            StateApplicationStatus.NOT_REQUESTED,
            TemporalApplicationStatus.APPLIED,
            ActionApplicationStatus.FAILED,
            ProposalApplicationStatus.PARTIAL,
        ),
        (
            StateApplicationStatus.APPLIED,
            TemporalApplicationStatus.NOT_REQUESTED,
            ActionApplicationStatus.FAILED,
            ProposalApplicationStatus.PARTIAL,
        ),
        (
            StateApplicationStatus.NOT_REQUESTED,
            TemporalApplicationStatus.APPLIED,
            ActionApplicationStatus.APPLIED,
            ProposalApplicationStatus.APPLIED,
        ),
        (
            StateApplicationStatus.NOT_REQUESTED,
            TemporalApplicationStatus.NOT_REQUESTED,
            ActionApplicationStatus.FAILED,
            ProposalApplicationStatus.FAILED,
        ),
        (
            StateApplicationStatus.APPLIED,
            TemporalApplicationStatus.APPLIED,
            ActionApplicationStatus.NOT_REQUESTED,
            ProposalApplicationStatus.APPLIED,
        ),
        (
            StateApplicationStatus.APPLIED,
            TemporalApplicationStatus.APPLIED,
            ActionApplicationStatus.FAILED,
            ProposalApplicationStatus.PARTIAL,
        ),
        (
            StateApplicationStatus.NOT_REQUESTED,
            TemporalApplicationStatus.NOT_REQUESTED,
            ActionApplicationStatus.NOT_REQUESTED,
            ProposalApplicationStatus.NO_PROPOSALS,
        ),
    ),
)
def test_three_way_application_status_truth_table(
    state: StateApplicationStatus,
    temporal: TemporalApplicationStatus,
    actions: ActionApplicationStatus,
    expected: ProposalApplicationStatus,
) -> None:
    result = ProposalApplicationCoordinator._aggregate_status(  # pyright: ignore[reportPrivateUsage]
        StateApplication(state, None, 0, 0),
        TemporalApplication(temporal),
        ActionApplication(actions),
    )
    assert result is expected


@pytest.mark.asyncio
async def test_temporal_success_then_action_failure_is_partial_and_replay_is_unchanged() -> None:
    calls: list[str] = []

    def execute(correlation: ToolBatchCorrelation, call: Any, cancelled: Any) -> ToolResult:
        del correlation, cancelled
        calls.append(call.call_id)
        return ToolResult(
            call.call_id,
            ToolResultStatus.FAILED,
            None,
            reason_code="executor_failed",
        )

    spec = {
        "type": "object",
        "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 64}},
        "required": ["text"],
        "additionalProperties": False,
    }
    tool_spec = ToolSpec("fixture.emit", "Emit", spec)
    registry = ApplicationToolRegistry([ToolBinding(tool_spec, execute)])
    factory = DeterministicToolSessionFactory(
        registry, exposed_tool_names=(tool_spec.name,), executor_deadline=0.05
    )
    temporal = TemporalCoordinator(scope_id="fixture")
    state = MindState()
    boundary = ProposalApplicationCoordinator(
        state,
        scope_id="fixture",
        temporal_coordinator=temporal,
        tool_registry=registry,
        tool_session_factory=factory,
        action_tool_names={ActionProposalKind.SPEAK: tool_spec.name},
        state_provenance=MindStateProvenance("user", "assistant"),
    )
    candidate = CognitionCandidate(
        state_proposals=(StateProposal(StateProposalKind.CREATE_INTENTION, "remember later"),),
        action_proposals=(ActionProposal(ActionProposalKind.SPEAK, "later"),),
        temporal_proposals=(
            TemporalProposal("wake later", temporal.now() + timedelta(seconds=10), "intention"),
        ),
    )

    class Engine:
        async def run(self, episode: CognitionEpisode) -> object:
            del episode
            return candidate

    runner = CognitionEpisodeRunner(
        Engine(),
        _observation_window(),
        state,
        scope_id="fixture",
        application_authority=boundary.application_authority,
    )
    outcome = await runner.run(CognitionTrigger(("observation",), "fixture"))
    assert outcome is not None

    result = boundary.apply_sync(outcome)
    assert result.state.status is StateApplicationStatus.APPLIED
    assert result.temporal.status is TemporalApplicationStatus.APPLIED
    assert result.actions.status is ActionApplicationStatus.FAILED
    assert result.status is ProposalApplicationStatus.PARTIAL
    assert len(calls) == 1
    assert boundary.apply_sync(outcome).status is ProposalApplicationStatus.DUPLICATE


def _observation_window() -> Any:
    from lilavel_runtime import EventSource, Observation, ObservationWindow, WorldEvent

    window = ObservationWindow(2)
    window.admit(
        Observation(
            "observation",
            1,
            WorldEvent("event", EventSource("fixture"), "observation", {}),
        )
    )
    return window


def _load_architecture_guard() -> ModuleType:
    path = Path(__file__).parents[3] / "scripts" / "check_architecture.py"
    spec = importlib.util.spec_from_file_location("h2_architecture_guard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_architecture_guard_rejects_synthetic_presence_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guard = _load_architecture_guard()
    package = tmp_path / "lilavel_runtime"
    package.mkdir()
    (package / "bad.py").write_text(
        "class PersistentPresenceRuntime:\n"
        "    def run(self):\n"
        "        return self._model.generate_for_run(request)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "RUNTIME_SOURCE", tmp_path)

    violations = guard._semantic_entrypoint_violations()

    assert any(
        "PersistentPresenceRuntime owns direct semantic generation" in item for item in violations
    )
