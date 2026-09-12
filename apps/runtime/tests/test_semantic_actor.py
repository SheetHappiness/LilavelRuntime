"""Deterministic proofs for the MIND-1F-B semantic admission kernel."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from lilavel_runtime import (
    SemanticActor,
    SemanticActorState,
    SemanticAdmissionStatus,
    SemanticCancellationToken,
    SemanticEpisode,
    SemanticEpisodeStatus,
    SemanticMailboxFull,
    SemanticPriority,
    SemanticSourceKind,
)


async def _wait_until(predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("fixture condition did not become true")


@dataclass(slots=True)
class _FixtureExecutor:
    name: str
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event | None = None
    cancellation_seen: asyncio.Event = field(default_factory=asyncio.Event)
    calls: list[str] = field(default_factory=lambda: list[str]())
    active: int = 0
    max_active: int = 0

    async def __call__(
        self, episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        self.calls.append(episode.request_id)
        self.started.set()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.release is not None:
                await self.release.wait()
            if cancellation.is_requested:
                self.cancellation_seen.set()
            return self.name
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_single_active_episode_is_character_wide_and_settled_before_successor() -> None:
    actor = SemanticActor(scope_id="lilavel", mailbox_capacity=4)
    first_release = asyncio.Event()
    first_executor = _FixtureExecutor("first", release=first_release)
    second_executor = _FixtureExecutor("second")
    await actor.start()

    first = await actor.admit(
        actor.create_request(
            "semantic-a",
            source_kind=SemanticSourceKind.TEMPORAL,
            priority=SemanticPriority.NON_USER,
            executor=first_executor,
        )
    )
    await first_executor.started.wait()
    second = await actor.admit(
        actor.create_request(
            "semantic-b",
            source_kind=SemanticSourceKind.EXTERNAL,
            priority=SemanticPriority.NON_USER,
            executor=second_executor,
        )
    )
    await asyncio.sleep(0)

    assert actor.active_count == 1
    assert actor.queued_count == 1
    assert second_executor.calls == []
    first_release.set()
    assert (await first.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert (await second.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert first_executor.calls == ["semantic-a"]
    assert second_executor.calls == ["semantic-b"]
    assert first_executor.max_active == 1
    assert second_executor.max_active == 1
    assert actor.max_active == 1
    await actor.stop()


@pytest.mark.asyncio
async def test_user_priority_requests_cancellation_and_runs_before_older_non_user_work() -> None:
    actor = SemanticActor(scope_id="lilavel", mailbox_capacity=4)
    first_release = asyncio.Event()
    first_executor = _FixtureExecutor("first", release=first_release)
    older_executor = _FixtureExecutor("older")
    user_executor = _FixtureExecutor("user")
    await actor.start()

    first = await actor.admit(
        actor.create_request(
            "autonomous-a",
            source_kind=SemanticSourceKind.AUTONOMOUS,
            priority=SemanticPriority.NON_USER,
            executor=first_executor,
        )
    )
    await first_executor.started.wait()
    older = await actor.admit(
        actor.create_request(
            "temporal-c",
            source_kind=SemanticSourceKind.TEMPORAL,
            priority=SemanticPriority.NON_USER,
            executor=older_executor,
        )
    )
    user = await actor.admit(
        actor.create_request(
            "user-b",
            source_kind=SemanticSourceKind.USER,
            priority=SemanticPriority.USER,
            executor=user_executor,
        )
    )

    assert user.status is SemanticAdmissionStatus.ACCEPTED
    assert actor.active_episode is not None
    assert first_executor.cancellation_seen.is_set() is False
    first_release.set()
    first_settlement = await first.wait()
    assert first_settlement.status is SemanticEpisodeStatus.CANCELLED
    await user_executor.started.wait()
    assert (await user.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert (await older.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert older_executor.calls == ["temporal-c"]
    assert user_executor.calls == ["user-b"]
    evidence = actor.evidence()
    assert any(
        item.request_id == "autonomous-a"
        and item.status is SemanticEpisodeStatus.CANCELLATION_REQUESTED
        and item.preempted
        for item in evidence
    )
    await actor.stop()


@pytest.mark.asyncio
async def test_preemption_intent_wins_before_executor_handle_binds() -> None:
    actor = SemanticActor(scope_id="lilavel", mailbox_capacity=2, settlement_timeout=0.2)
    handle_bound = asyncio.Event()
    allow_bind = asyncio.Event()
    user_started = asyncio.Event()

    async def delayed_executor(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode
        await allow_bind.wait()
        handle_bound.set()
        if cancellation.is_requested:
            return "discarded-before-bind"
        return "unexpected"

    async def user_executor(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode, cancellation
        user_started.set()
        return "user"

    await actor.start()
    first = await actor.admit(
        actor.create_request(
            "late-bind",
            source_kind=SemanticSourceKind.AUTONOMOUS,
            priority=SemanticPriority.NON_USER,
            executor=delayed_executor,
        )
    )
    await _wait_until(lambda: actor.active_episode is not None)
    user = await actor.admit(
        actor.create_request(
            "racing-user",
            source_kind=SemanticSourceKind.USER,
            priority=SemanticPriority.USER,
            executor=user_executor,
        )
    )
    assert handle_bound.is_set() is False
    allow_bind.set()
    assert (await first.wait()).status is SemanticEpisodeStatus.CANCELLED
    assert handle_bound.is_set()
    assert (await user.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert user_started.is_set()
    await actor.stop()


@pytest.mark.asyncio
async def test_duplicate_request_is_fenced_while_active_and_after_settlement() -> None:
    actor = SemanticActor(scope_id="lilavel", mailbox_capacity=2)
    executor = _FixtureExecutor("once")
    await actor.start()
    request = actor.create_request(
        "stable-trigger-id",
        source_kind=SemanticSourceKind.TEMPORAL,
        priority=SemanticPriority.NON_USER,
        executor=executor,
    )

    first = await actor.admit(request)
    duplicate_active = await actor.admit(request)
    assert duplicate_active.status is SemanticAdmissionStatus.DUPLICATE
    assert (await first.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert (await duplicate_active.wait()).status is SemanticEpisodeStatus.COMPLETED
    duplicate_settled = await actor.admit(request)
    assert duplicate_settled.status is SemanticAdmissionStatus.DUPLICATE
    assert (await duplicate_settled.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert executor.calls == ["stable-trigger-id"]
    assert [item.status for item in actor.evidence()].count(SemanticEpisodeStatus.DUPLICATE) >= 2
    await actor.stop()


@pytest.mark.asyncio
async def test_mailbox_capacity_rejects_overflow_without_silent_drop() -> None:
    actor = SemanticActor(scope_id="lilavel", mailbox_capacity=2)
    release = asyncio.Event()
    first_executor = _FixtureExecutor("first", release=release)
    queued_executors = [_FixtureExecutor("second"), _FixtureExecutor("third")]
    rejected_executor = _FixtureExecutor("rejected")
    await actor.start()
    first = await actor.admit(
        actor.create_request(
            "first",
            source_kind=SemanticSourceKind.AUTONOMOUS,
            priority=SemanticPriority.NON_USER,
            executor=first_executor,
        )
    )
    await first_executor.started.wait()
    queued = [
        await actor.admit(
            actor.create_request(
                request_id,
                source_kind=SemanticSourceKind.TEMPORAL,
                priority=SemanticPriority.NON_USER,
                executor=executor,
            )
        )
        for request_id, executor in zip(("second", "third"), queued_executors, strict=True)
    ]
    with pytest.raises(SemanticMailboxFull):
        await actor.admit(
            actor.create_request(
                "overflow",
                source_kind=SemanticSourceKind.AUTONOMOUS,
                priority=SemanticPriority.NON_USER,
                executor=rejected_executor,
            )
        )
    assert actor.queued_count == 2
    release.set()
    await first.wait()
    for admission in queued:
        assert (await admission.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert rejected_executor.calls == []
    await actor.stop()


@pytest.mark.asyncio
async def test_executor_failure_settles_failed_and_allows_next_episode() -> None:
    actor = SemanticActor(scope_id="lilavel", mailbox_capacity=2)
    next_executor = _FixtureExecutor("next")

    async def failing_executor(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode, cancellation
        raise RuntimeError("private fixture failure")

    await actor.start()
    failed = await actor.admit(
        actor.create_request(
            "failed",
            source_kind=SemanticSourceKind.EXTERNAL,
            priority=SemanticPriority.NON_USER,
            executor=failing_executor,
        )
    )
    next_admission = await actor.admit(
        actor.create_request(
            "next",
            source_kind=SemanticSourceKind.USER,
            priority=SemanticPriority.USER,
            executor=next_executor,
        )
    )
    assert (await failed.wait()).status is SemanticEpisodeStatus.FAILED
    assert (await next_admission.wait()).status is SemanticEpisodeStatus.COMPLETED
    assert next_executor.calls == ["next"]
    assert "private fixture failure" not in repr(actor.evidence())
    await actor.stop()


@pytest.mark.asyncio
async def test_uncontainable_cancellation_poisoned_actor_does_not_admit_successor() -> None:
    actor = SemanticActor(scope_id="lilavel", mailbox_capacity=2, settlement_timeout=0.01)
    release = asyncio.Event()

    async def uncontainable_executor(
        episode: SemanticEpisode, cancellation: SemanticCancellationToken
    ) -> object:
        del episode, cancellation
        while not release.is_set():
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                continue
        return "late"

    successor_executor = _FixtureExecutor("must-not-run")
    await actor.start()
    active = await actor.admit(
        actor.create_request(
            "uncertain",
            source_kind=SemanticSourceKind.AUTONOMOUS,
            priority=SemanticPriority.NON_USER,
            executor=uncontainable_executor,
        )
    )
    await _wait_until(lambda: actor.active_episode is not None)
    successor = await actor.admit(
        actor.create_request(
            "blocked-successor",
            source_kind=SemanticSourceKind.USER,
            priority=SemanticPriority.USER,
            executor=successor_executor,
        )
    )
    assert (await active.wait()).status is SemanticEpisodeStatus.FAILED
    assert (await successor.wait()).status is SemanticEpisodeStatus.CANCELLED
    assert actor.state is SemanticActorState.POISONED
    assert successor_executor.calls == []
    release.set()
    await _wait_until(lambda: not actor._uncontained)  # type: ignore[attr-defined]
    await actor.stop()


@pytest.mark.asyncio
async def test_shutdown_cancels_active_and_queued_work_without_orphans() -> None:
    actor = SemanticActor(scope_id="lilavel", mailbox_capacity=2, settlement_timeout=0.2)
    active_executor = _FixtureExecutor("active", release=asyncio.Event())
    queued_executor = _FixtureExecutor("queued")
    await actor.start()
    active = await actor.admit(
        actor.create_request(
            "active",
            source_kind=SemanticSourceKind.AUTONOMOUS,
            priority=SemanticPriority.NON_USER,
            executor=active_executor,
        )
    )
    await active_executor.started.wait()
    queued = await actor.admit(
        actor.create_request(
            "queued",
            source_kind=SemanticSourceKind.TEMPORAL,
            priority=SemanticPriority.NON_USER,
            executor=queued_executor,
        )
    )

    await actor.stop()

    assert actor.state is SemanticActorState.STOPPED
    assert (await active.wait()).status is SemanticEpisodeStatus.CANCELLED
    assert (await queued.wait()).status is SemanticEpisodeStatus.CANCELLED
    assert queued_executor.calls == []
    assert actor.active_count == 0
    assert actor.queued_count == 0


@pytest.mark.asyncio
async def test_fence_session_rollover_keeps_ledger_bounded_without_eviction_reexecution() -> None:
    actor = SemanticActor(scope_id="lilavel", fence_capacity=1)
    executor = _FixtureExecutor("once")
    await actor.start()
    first_request = actor.create_request(
        "first-session-request",
        source_kind=SemanticSourceKind.TEMPORAL,
        priority=SemanticPriority.NON_USER,
        executor=executor,
    )
    first = await actor.admit(first_request)
    assert (await first.wait()).status is SemanticEpisodeStatus.COMPLETED
    old_session = first_request.actor_session_id
    second_request = actor.create_request(
        "second-session-request",
        source_kind=SemanticSourceKind.TEMPORAL,
        priority=SemanticPriority.NON_USER,
        executor=executor,
    )
    assert second_request.actor_session_id != old_session
    stale = await actor.admit(first_request)
    assert stale.status is SemanticAdmissionStatus.REJECTED
    assert stale.reason_code == "actor_session_mismatch"
    assert actor.settled_history_count == 0
    assert executor.calls == ["first-session-request"]
    await actor.stop()


@pytest.mark.asyncio
async def test_actor_evidence_is_bounded_and_privacy_preserving() -> None:
    actor = SemanticActor(scope_id="lilavel", evidence_capacity=4)

    async def executor(episode: SemanticEpisode, cancellation: SemanticCancellationToken) -> object:
        del episode, cancellation
        return {"raw_prompt": "do not persist this", "raw_result": "secret"}

    await actor.start()
    for number in range(3):
        admission = await actor.admit(
            actor.create_request(
                f"opaque-{number}",
                source_kind=SemanticSourceKind.EXTERNAL,
                priority=SemanticPriority.NON_USER,
                executor=executor,
            )
        )
        assert (await admission.wait()).status is SemanticEpisodeStatus.COMPLETED

    evidence = actor.evidence()
    assert len(evidence) <= 4
    rendered = repr(evidence)
    assert "do not persist this" not in rendered
    assert "secret" not in rendered
    assert all(hasattr(item, "request_id") and hasattr(item, "status") for item in evidence)
    await actor.stop()
