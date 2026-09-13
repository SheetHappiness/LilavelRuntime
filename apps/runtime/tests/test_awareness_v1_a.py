"""Focused AWARE-V1-A peripheral NOTE buffer and boundary proofs."""

from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from lilavel_core import AttentionDecision

from lilavel_runtime import (
    AWARE_V1_A_SCENARIOS,
    NO_COGNITION,
    AttentionEvidence,
    AttentionEvidenceExtractor,
    AttentionRelevance,
    AwarenessAdmissionStatus,
    AwarenessScope,
    DeterministicAttentionCognitionGate,
    DeterministicAttentionPolicy,
    EventSource,
    LilavelRuntime,
    MindExecutionAdapter,
    Observation,
    ObservationWindow,
    PeripheralAwarenessBuffer,
    WorldEvent,
    evaluate_awareness_corpus,
)


@dataclass(slots=True)
class _Clock:
    value: datetime

    def now(self) -> datetime:
        return self.value


def _observation(
    number: int,
    *,
    environment: str = "fixture",
    subject: str | None = "surface-a",
    kind: str = "ambient",
    payload: dict[str, object] | None = None,
) -> Observation:
    return Observation(
        f"observation-{number}",
        number,
        WorldEvent(
            f"event-{number}",
            EventSource(environment, subject),
            kind,
            payload,
        ),
    )


def _note_verdict(observation: Observation):
    return DeterministicAttentionPolicy().evaluate(AttentionEvidence(observation.observation_id))


def _scope(observation: Observation, scope_id: str = "runtime") -> AwarenessScope:
    return AwarenessScope.from_observation(scope_id, observation)


def _admit(
    buffer: PeripheralAwarenessBuffer,
    observation: Observation,
    *,
    scope_id: str = "runtime",
):
    return buffer.admit(observation, _note_verdict(observation), scope_id=scope_id)


def test_awareness_corpus_has_thirty_four_passing_deterministic_scenarios() -> None:
    report = evaluate_awareness_corpus()

    assert len(AWARE_V1_A_SCENARIOS) == 34
    assert report.scenario_count == 34
    assert report.passed
    assert all(result.passed for result in report.scenario_results)


def test_constructor_bounds_and_contract_reject_untrusted_shapes() -> None:
    with pytest.raises(ValueError):
        PeripheralAwarenessBuffer(per_scope_capacity=0)
    with pytest.raises(ValueError):
        PeripheralAwarenessBuffer(total_capacity=65)
    with pytest.raises(ValueError):
        PeripheralAwarenessBuffer(ttl=timedelta(0))

    buffer = PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    observation = _observation(1, payload={"text": "do not copy"})
    with pytest.raises(TypeError):
        buffer.admit(
            cast(Observation, {"payload": "external"}),
            _note_verdict(observation),
            scope_id="runtime",
        )


def test_note_admission_is_the_only_stateful_attention_gate_effect() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock)
    gate = DeterministicAttentionCognitionGate(awareness_buffer=buffer)
    observation = _observation(1)

    assert gate.evaluate((observation,))[0].decision is AttentionDecision.NOTE
    assert buffer.total_count() == 0

    decision = gate.decide((observation,))

    assert decision is NO_COGNITION
    assert buffer.count(_scope(observation)) == 1
    evidence = buffer.evidence()
    assert evidence[-1].outcome == "admitted"
    assert evidence[-1].scope_count == 1


def test_drop_and_think_keep_existing_attention_semantics_without_note_duplication() -> None:
    buffer = PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    extractor = AttentionEvidenceExtractor({"critical": _critical_profile()})
    gate = DeterministicAttentionCognitionGate(
        extractor=extractor,
        awareness_buffer=buffer,
    )
    drop = _observation(1)
    think = _observation(2, kind="critical")

    drop_verdict = DeterministicAttentionPolicy().evaluate(
        AttentionEvidence(drop.observation_id, relevance=AttentionRelevance.LOW)
    )
    assert drop_verdict.decision is AttentionDecision.DROP
    assert (
        buffer.admit(drop, drop_verdict, scope_id="runtime").status
        is AwarenessAdmissionStatus.REJECTED
    )

    decision = gate.decide((think,))

    assert decision.observation_ids == (think.observation_id,)  # type: ignore[union-attr]
    assert buffer.total_count() == 0


def test_runtime_binds_default_attention_gate_to_peripheral_awareness() -> None:
    buffer = PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    runtime = LilavelRuntime(
        awareness_buffer=buffer,
        mind_executor=cast(MindExecutionAdapter, _UnusedMindExecutor()),
    )

    async def run() -> None:
        await runtime.start()
        receipt = await runtime.admit_observation(_observation(1).event)
        decision = await runtime.cognition_step(receipt)
        assert decision is NO_COGNITION
        await runtime.stop()

    import asyncio

    asyncio.run(run())

    assert buffer.snapshot(AwarenessScope("runtime", "fixture", "surface-a"))
    assert runtime.health().cognition_triggers == 0


@dataclass(slots=True)
class _UnusedMindExecutor:
    calls: int = 0

    async def execute(self, trigger: object, cancellation: object) -> object:
        del trigger, cancellation
        self.calls += 1
        raise AssertionError("NOTE must not enter cognition")


def _critical_profile():
    from lilavel_runtime import AttentionSignalProfile

    return AttentionSignalProfile(critical_event=True)


def test_fixed_clock_ttl_expiry_and_ordering_are_deterministic() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock, ttl=timedelta(seconds=10))
    observations = tuple(_observation(number) for number in range(1, 4))
    for observation in observations:
        _admit(buffer, observation)

    assert tuple(note.observation_id for note in buffer.snapshot(_scope(observations[0]))) == (
        "observation-1",
        "observation-2",
        "observation-3",
    )
    clock.value += timedelta(seconds=10)
    assert buffer.snapshot(_scope(observations[0])) == ()
    assert buffer.evidence()[-1].reason_code == "expired"


def test_scope_isolation_and_deterministic_fifo_overflow() -> None:
    buffer = PeripheralAwarenessBuffer(
        clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)),
        per_scope_capacity=2,
        total_capacity=3,
    )
    first = tuple(_observation(number) for number in range(1, 4))
    other = _observation(4, subject="surface-b")
    for observation in (*first, other):
        _admit(buffer, observation)

    assert tuple(note.observation_id for note in buffer.snapshot(_scope(first[0]))) == (
        "observation-2",
        "observation-3",
    )
    assert tuple(note.observation_id for note in buffer.snapshot(_scope(other))) == (
        "observation-4",
    )
    assert buffer.total_count() == 3
    assert any(item.evicted_count == 1 for item in buffer.evidence())


def test_global_capacity_evicts_oldest_across_scopes() -> None:
    buffer = PeripheralAwarenessBuffer(
        clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)),
        per_scope_capacity=4,
        total_capacity=3,
    )
    observations = (
        _observation(1, subject="surface-a"),
        _observation(2, subject="surface-b"),
        _observation(3, subject="surface-c"),
        _observation(4, subject="surface-d"),
    )
    for observation in observations:
        _admit(buffer, observation)

    assert buffer.total_count() == 3
    assert buffer.snapshot(_scope(observations[0])) == ()
    assert any(item.evicted_count == 1 for item in buffer.evidence())


def test_duplicate_note_attempts_coalesce_and_snapshots_are_immutable() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock)
    observation = _observation(1)
    first = _admit(buffer, observation).note
    clock.value += timedelta(seconds=1)
    second = _admit(buffer, observation).note

    assert first is not None and second is not None
    assert first.note_id == second.note_id
    assert second.occurrence_count == 2
    assert second.last_seen_at == clock.value
    assert buffer.total_count() == 1
    snapshot = buffer.snapshot(_scope(observation))
    assert type(snapshot) is tuple
    with pytest.raises(FrozenInstanceError):
        first.note_id = "mutated"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snapshot.append(first)  # type: ignore[attr-defined]


def test_provenance_is_bounded_and_payload_is_not_retained() -> None:
    buffer = PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    observation = _observation(1, payload={"text": "secret external body"})
    note = _admit(buffer, observation).note

    assert note is not None
    assert note.source_refs == ("observation:observation-1", "event:event-1")
    assert len(note.source_refs) == 2
    assert "secret external body" not in repr(note)
    assert not {field.name for field in note.__dataclass_fields__.values()}.intersection(
        {"payload", "text", "body", "messages", "memory"}
    )


def test_awareness_failure_fails_closed_without_turning_note_into_cognition() -> None:
    class _FailingBuffer:
        def admit(self, observation: Observation, verdict: object, *, scope_id: str) -> object:
            del observation, verdict, scope_id
            raise RuntimeError("buffer unavailable")

    observation = _observation(1)
    gate = DeterministicAttentionCognitionGate(
        awareness_buffer=cast(PeripheralAwarenessBuffer, _FailingBuffer())
    )

    assert gate.decide((observation,)) is NO_COGNITION


def test_awareness_owner_has_no_generation_effect_persistence_or_context_authority() -> None:
    from lilavel_runtime import ContextFrame, ProductionContextComposer
    from lilavel_runtime.awareness import PeripheralAwarenessBuffer as Owner

    source = inspect.getsource(Owner).casefold()
    module_source = inspect.getsource(__import__("lilavel_runtime.awareness", fromlist=["Owner"]))
    composer_source = inspect.getsource(ProductionContextComposer).casefold()
    frame_fields = {field.name for field in ContextFrame.__dataclass_fields__.values()}

    assert all(
        token not in source + module_source.casefold()
        for token in ("modelrequest", "conversationstore", "sqlite", "discord", "toolcall")
    )
    assert "awareness" not in composer_source
    assert "awareness" not in {field.casefold() for field in frame_fields}
    imports = {
        node.module
        for node in ast.walk(ast.parse(module_source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not imports.intersection({"context", "mind", "persistence", "lilavel_core.persistence"})


def test_awareness_does_not_mutate_mind_or_observation_window() -> None:
    from lilavel_runtime import MindState

    mind = MindState()
    window = ObservationWindow(4)
    before_mind = mind.snapshot()
    before_window = window.snapshot()
    buffer = PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    observation = _observation(1)
    window.admit(observation)
    _admit(buffer, observation)

    assert mind.snapshot() == before_mind
    assert window.snapshot() == (observation,)
    assert before_window == ()
