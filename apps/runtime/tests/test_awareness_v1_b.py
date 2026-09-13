"""Focused AWARE-V1-B lifecycle and boundary proofs."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, timedelta

import pytest
from lilavel_core import AttentionDecision

from lilavel_runtime import (
    AWARE_V1_B_SCENARIOS,
    MAX_AWARENESS_KEY_BYTES,
    MAX_AWARENESS_NOTES_PER_SCOPE,
    MAX_AWARENESS_NOTES_TOTAL,
    AttentionEvidence,
    AwarenessAdmissionStatus,
    AwarenessKey,
    AwarenessReconciliationStatus,
    AwarenessScope,
    DeterministicAttentionCognitionGate,
    DeterministicAttentionPolicy,
    EventSource,
    Observation,
    PeripheralAwarenessBuffer,
    WorldEvent,
    evaluate_awareness_b_corpus,
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
    event_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> Observation:
    return Observation(
        f"observation-{number}",
        number,
        WorldEvent(
            event_id or f"event-{number}",
            EventSource(environment, subject),
            kind,
            payload,
        ),
    )


def _verdict(observation: Observation):
    return DeterministicAttentionPolicy().evaluate(AttentionEvidence(observation.observation_id))


def _admit(
    buffer: PeripheralAwarenessBuffer,
    observation: Observation,
    *,
    scope_id: str = "runtime",
    key: AwarenessKey | None = None,
):
    return buffer.admit(
        observation,
        _verdict(observation),
        scope_id=scope_id,
        awareness_key=key,
    )


def test_b_corpus_has_forty_passing_deterministic_scenarios() -> None:
    report = evaluate_awareness_b_corpus()

    assert len(AWARE_V1_B_SCENARIOS) == 40
    assert report.scenario_count == 40
    assert report.passed
    assert all(result.passed for result in report.scenario_results)


def test_awareness_key_is_typed_and_bounded() -> None:
    with pytest.raises(ValueError):
        AwarenessKey()
    with pytest.raises(ValueError):
        AwarenessKey(dedup_key="x" * (MAX_AWARENESS_KEY_BYTES + 1))

    buffer = PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    with pytest.raises(TypeError):
        _admit(buffer, _observation(32), key=cast_key("raw"))  # type: ignore[arg-type]


def test_duplicate_coalescing_refreshes_ttl_and_replaces_latest_provenance() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock, ttl=timedelta(seconds=10))
    first = _admit(buffer, _observation(1), key=AwarenessKey(dedup_key="same"))
    clock.value += timedelta(seconds=3)
    second = _admit(buffer, _observation(2), key=AwarenessKey(dedup_key="same"))

    assert first.status is AwarenessAdmissionStatus.ADMITTED
    assert second.coalesced
    assert first.note is not None and second.note is not None
    assert first.note.note_id == second.note.note_id
    assert second.note.source_refs == ("observation:observation-2", "event:event-2")
    assert second.note.last_seen_at == clock.value
    assert second.note.expires_at == clock.value + timedelta(seconds=10)
    assert second.note.occurrence_count == 2
    assert buffer.total_count() == 1


def test_duplicate_after_expiry_is_a_new_note() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock, ttl=timedelta(seconds=10))
    first = _admit(buffer, _observation(3), key=AwarenessKey(dedup_key="same"))
    clock.value += timedelta(seconds=10)
    second = _admit(buffer, _observation(4), key=AwarenessKey(dedup_key="same"))

    assert first.note is not None and second.note is not None
    assert first.note.note_id != second.note.note_id
    assert not second.coalesced
    assert second.expired_count == 1


def test_same_key_isolated_by_scope_and_different_keys_do_not_coalesce() -> None:
    buffer = PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    key = AwarenessKey(dedup_key="same")
    first = _admit(buffer, _observation(5, subject="surface-a"), scope_id="a", key=key)
    second = _admit(buffer, _observation(6, subject="surface-b"), scope_id="b", key=key)
    third = _admit(
        buffer,
        _observation(7),
        key=AwarenessKey(dedup_key="different"),
    )

    assert not second.coalesced
    assert not third.coalesced
    assert buffer.count(AwarenessScope("a", "fixture", "surface-a")) == 1
    assert buffer.count(AwarenessScope("b", "fixture", "surface-b")) == 1
    assert buffer.total_count() == 3
    assert first.note is not None and second.note is not None and third.note is not None
    assert len({first.note.note_id, second.note.note_id, third.note.note_id}) == 3


def test_default_identity_ignores_raw_payload_and_uses_event_identity() -> None:
    buffer = PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    first = _admit(
        buffer,
        _observation(8, event_id="same-event", payload={"text": "first"}),
    )
    second = _admit(
        buffer,
        _observation(9, event_id="same-event", payload={"text": "different"}),
    )

    assert first.note is not None and second.note is not None
    assert second.coalesced
    assert buffer.total_count() == 1
    assert "different" not in repr(second.note)


def test_explicit_supersession_replaces_only_same_scope_state() -> None:
    buffer = PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    old = _admit(
        buffer,
        _observation(10),
        key=AwarenessKey(dedup_key="old", supersession_key="topic"),
    )
    new = _admit(
        buffer,
        _observation(11),
        key=AwarenessKey(dedup_key="new", supersession_key="topic"),
    )
    other_scope = _admit(
        buffer,
        _observation(12, subject="surface-b"),
        scope_id="other",
        key=AwarenessKey(dedup_key="other", supersession_key="topic"),
    )

    assert old.note is not None and new.note is not None and other_scope.note is not None
    assert new.superseded_note_ids == (old.note.note_id,)
    assert tuple(item.note_id for item in buffer.snapshot(old.note.scope)) == (new.note.note_id,)
    assert other_scope.superseded_note_ids == ()


def test_missing_supersession_metadata_never_guesses_replacement() -> None:
    buffer = PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    first = _admit(buffer, _observation(13))
    second = _admit(buffer, _observation(14))

    assert first.superseded_note_ids == ()
    assert second.superseded_note_ids == ()
    assert buffer.total_count() == 2


def test_handled_reconciliation_requires_buffer_bound_runtime_authority() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock)
    other = PeripheralAwarenessBuffer(clock=clock)
    admission = _admit(buffer, _observation(15), key=AwarenessKey(dedup_key="handled"))
    assert admission.note is not None

    with pytest.raises(TypeError):
        buffer.mark_handled(admission.note.note_id, authority=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        buffer.mark_handled(admission.note.note_id, authority=other.handled_authority())

    result = buffer.mark_handled(
        admission.note.note_id,
        authority=buffer.handled_authority(),
    )
    assert result.status is AwarenessReconciliationStatus.HANDLED
    compacted = buffer.compact()
    assert admission.note.note_id in compacted.handled_note_ids
    assert buffer.snapshot(admission.note.scope) == ()


def test_expiry_is_not_handled() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock, ttl=timedelta(seconds=5))
    admission = _admit(buffer, _observation(16))
    assert admission.note is not None
    clock.value += timedelta(seconds=5)

    result = buffer.mark_handled(admission.note.note_id, authority=buffer.handled_authority())

    assert result.status is AwarenessReconciliationStatus.NOT_FOUND
    assert any(item.expired_count == 1 for item in buffer.evidence())
    assert all(item.handled_count == 0 for item in buffer.evidence())


def test_compaction_happens_before_overflow_eviction() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(
        clock=clock,
        ttl=timedelta(seconds=5),
        per_scope_capacity=1,
        total_capacity=1,
    )
    old = _admit(buffer, _observation(17), key=AwarenessKey(dedup_key="old"))
    clock.value += timedelta(seconds=5)
    new = _admit(buffer, _observation(18), key=AwarenessKey(dedup_key="new"))

    assert old.note is not None and new.note is not None
    assert new.evicted_note_ids == ()
    assert old.note.note_id in new.compacted_note_ids
    assert buffer.total_count() == 1


def test_bounds_and_ordering_remain_deterministic_after_refresh() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock, per_scope_capacity=2, total_capacity=2)
    _admit(buffer, _observation(19), key=AwarenessKey(dedup_key="a"))
    _admit(buffer, _observation(20), key=AwarenessKey(dedup_key="b"))
    clock.value += timedelta(seconds=1)
    _admit(buffer, _observation(21), key=AwarenessKey(dedup_key="a"))
    scope = AwarenessScope("runtime", "fixture", "surface-a")

    snapshot = buffer.snapshot(scope)
    assert len(snapshot) == 2
    assert tuple(item.observation_id for item in snapshot) == (
        "observation-20",
        "observation-21",
    )
    per_scope_buffer = PeripheralAwarenessBuffer(clock=clock)
    for number in range(22, 22 + MAX_AWARENESS_NOTES_PER_SCOPE + 1):
        _admit(
            per_scope_buffer,
            _observation(number),
            key=AwarenessKey(dedup_key=f"key-{number}"),
        )
    assert per_scope_buffer.count(scope) == MAX_AWARENESS_NOTES_PER_SCOPE
    assert buffer.total_count() <= MAX_AWARENESS_NOTES_TOTAL


def test_snapshot_and_lifecycle_values_are_immutable() -> None:
    buffer = PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    admission = _admit(buffer, _observation(30), key=AwarenessKey(dedup_key="immutable"))
    assert admission.note is not None
    snapshot = buffer.snapshot(admission.note.scope)

    assert type(snapshot) is tuple
    with pytest.raises(FrozenInstanceError):
        snapshot[0].occurrence_count = 3  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snapshot.append(admission.note)  # type: ignore[attr-defined]


def test_note_lifecycle_has_no_cognition_path_or_production_context_projection() -> None:
    buffer = PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    observation = _observation(31)
    decision = DeterministicAttentionCognitionGate(awareness_buffer=buffer).decide((observation,))
    assert decision is not AttentionDecision.THINK
    assert buffer.total_count() == 1

    from lilavel_runtime import ContextFrame, ProductionContextComposer

    assert "awareness" not in __import__("inspect").getsource(ProductionContextComposer).casefold()
    assert "awareness" not in {
        field.name.casefold() for field in ContextFrame.__dataclass_fields__.values()
    }


def cast_key(value: str) -> object:
    """Return an untyped value for the raw-key rejection proof."""

    return value
