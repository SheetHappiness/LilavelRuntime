"""Focused AWARE-V1-C context projection proofs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from lilavel_core import CognitionReasonCode
from lilavel_core.sidecar_protocol import MAX_GUIDANCE_BYTES

from lilavel_runtime import (
    MAX_AWARENESS_CONTEXT_NOTES,
    AwarenessContext,
    AwarenessContextNote,
    AwarenessKey,
    AwarenessScope,
    ContextAvailability,
    ContextBuildRequest,
    ContextFrameBuilder,
    ContextPurpose,
    EventSource,
    LilavelRuntime,
    Observation,
    PeripheralAwarenessBuffer,
    PeripheralAwarenessContextResolver,
    ProductionContextComposer,
    WorldEvent,
    compile_context_projection,
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
    payload: dict[str, object] | None = None,
) -> Observation:
    return Observation(
        f"observation-{number}",
        number,
        WorldEvent(
            f"event-{number}",
            EventSource(environment, subject),
            "ambient",
            payload,
        ),
    )


def _scope(
    *,
    semantic: str = "runtime",
    environment: str = "fixture",
    surface: str | None = "surface-a",
) -> AwarenessScope:
    return AwarenessScope(semantic, environment, surface)


def _admit(
    buffer: PeripheralAwarenessBuffer,
    observation: Observation,
    *,
    scope_id: str = "runtime",
    key: AwarenessKey | None = None,
):
    from lilavel_runtime import AttentionEvidence, DeterministicAttentionPolicy

    verdict = DeterministicAttentionPolicy().evaluate(AttentionEvidence(observation.observation_id))
    return buffer.admit(observation, verdict, scope_id=scope_id, awareness_key=key)


def _request_factory(scope: AwarenessScope):
    def factory(purpose: ContextPurpose, scope_id: str, owner: object) -> ContextBuildRequest:
        del purpose, owner
        return ContextBuildRequest(scope_id=scope_id, awareness_scope=scope)

    return factory


def test_exact_scope_projects_bounded_metadata_only() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock)
    observation = _observation(1, payload={"text": "secret source content"})
    admission = _admit(buffer, observation)
    assert admission.note is not None
    builder = ContextFrameBuilder(
        awareness_resolver=PeripheralAwarenessContextResolver(buffer),
    )

    frame = builder.build(
        ContextPurpose.USER_RESPONSE,
        ContextBuildRequest(scope_id="core-session", awareness_scope=_scope()),
    )

    assert frame.awareness.availability is ContextAvailability.KNOWN
    assert len(frame.awareness.notes) == 1
    projected = frame.awareness.notes[0]
    assert projected.note_id == admission.note.note_id
    assert projected.source_refs == admission.note.source_refs
    assert projected.reason_codes == admission.note.reason_codes
    assert projected.occurrence_count == 1
    assert projected.first_seen_at == admission.note.admitted_at
    assert projected.last_seen_at == admission.note.last_seen_at
    rendered = compile_context_projection(frame).rendered
    assert "[Peripheral awareness]" in rendered
    assert "secret source content" not in rendered
    assert "memory" not in rendered
    assert "authority" in rendered
    assert not {
        field.name for field in AwarenessContextNote.__dataclass_fields__.values()
    }.intersection({"payload", "text", "body", "summary", "meaning", "memory"})


def test_production_composer_binds_existing_buffer_and_exact_scope() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock)
    _admit(buffer, _observation(1))
    composer = ProductionContextComposer(
        ContextFrameBuilder(),
        request_factory=lambda purpose, scope_id, owner: ContextBuildRequest(scope_id=scope_id),
    )
    composer.bind_awareness_buffer(buffer)
    composer.bind_awareness_scope("core-session", _scope())

    blocks = composer.compose_projection(ContextPurpose.USER_RESPONSE, scope_id="core-session")

    assert any(block.startswith("[Peripheral awareness]") for block in blocks)
    evidence = composer.evidence()[-1]
    assert evidence.awareness_availability is ContextAvailability.KNOWN
    assert evidence.awareness_note_count == 1
    assert evidence.awareness_block_present
    assert not evidence.awareness_omitted_by_budget


def test_runtime_binds_the_existing_buffer_to_a_custom_context_builder() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock)
    _admit(buffer, _observation(1))
    builder = ContextFrameBuilder()
    runtime = LilavelRuntime(context_builder=builder, awareness_buffer=buffer)

    frame = runtime.context_builder.build(
        ContextPurpose.USER_RESPONSE,
        ContextBuildRequest(scope_id="core", awareness_scope=_scope()),
    )

    assert frame.awareness.availability is ContextAvailability.KNOWN
    assert len(frame.awareness.notes) == 1


def test_semantic_environment_and_surface_scope_mismatches_do_not_project() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock)
    _admit(buffer, _observation(2))
    builder = ContextFrameBuilder(
        awareness_resolver=PeripheralAwarenessContextResolver(buffer),
    )

    for mismatched in (
        _scope(semantic="other-runtime"),
        _scope(environment="other-environment"),
        _scope(surface="surface-b"),
    ):
        frame = builder.build(
            ContextPurpose.USER_RESPONSE,
            ContextBuildRequest(scope_id="core-session", awareness_scope=mismatched),
        )
        assert frame.awareness.availability is ContextAvailability.KNOWN_EMPTY
        assert compile_context_projection(frame).rendered.count("Peripheral awareness") == 0


def test_handled_expired_and_superseded_notes_do_not_project() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock, ttl=timedelta(seconds=5))
    handled = _admit(buffer, _observation(3), key=AwarenessKey(dedup_key="handled"))
    assert handled.note is not None
    buffer.mark_handled(handled.note.note_id, authority=buffer.handled_authority())
    expired = _admit(buffer, _observation(4), key=AwarenessKey(dedup_key="expired"))
    assert expired.note is not None
    clock.value += timedelta(seconds=5)
    expired_scope = expired.note.scope
    assert buffer.snapshot_active(expired_scope) == ()

    clock.value = datetime(2030, 1, 1, tzinfo=UTC)
    superseded_old = _admit(
        buffer,
        _observation(5, subject="surface-b"),
        key=AwarenessKey(dedup_key="old", supersession_key="family"),
    )
    superseded_new = _admit(
        buffer,
        _observation(6, subject="surface-b"),
        key=AwarenessKey(dedup_key="new", supersession_key="family"),
    )
    assert superseded_old.note is not None and superseded_new.note is not None
    builder = ContextFrameBuilder(
        awareness_resolver=PeripheralAwarenessContextResolver(buffer),
    )
    frame = builder.build(
        ContextPurpose.USER_RESPONSE,
        ContextBuildRequest(scope_id="core", awareness_scope=superseded_old.note.scope),
    )
    assert frame.awareness.availability is ContextAvailability.KNOWN
    assert tuple(item.note_id for item in frame.awareness.notes) == (superseded_new.note.note_id,)


def test_dedup_occurrences_and_oldest_to_newest_order_are_preserved() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock)
    first = _admit(buffer, _observation(7), key=AwarenessKey(dedup_key="first"))
    clock.value += timedelta(seconds=1)
    second = _admit(buffer, _observation(8), key=AwarenessKey(dedup_key="second"))
    clock.value += timedelta(seconds=1)
    refreshed = _admit(buffer, _observation(9), key=AwarenessKey(dedup_key="first"))
    assert first.note is not None and second.note is not None and refreshed.note is not None
    assert refreshed.note.occurrence_count == 2

    builder = ContextFrameBuilder(
        awareness_resolver=PeripheralAwarenessContextResolver(buffer),
    )
    frame = builder.build(
        ContextPurpose.USER_RESPONSE,
        ContextBuildRequest(scope_id="core", awareness_scope=first.note.scope),
    )
    assert tuple(item.note_id for item in frame.awareness.notes) == (
        second.note.note_id,
        refreshed.note.note_id,
    )


def test_known_empty_unknown_and_unavailable_are_omitted_fail_soft() -> None:
    empty_builder = ContextFrameBuilder(
        awareness_resolver=PeripheralAwarenessContextResolver(
            PeripheralAwarenessBuffer(clock=_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
        )
    )
    empty = empty_builder.build(
        ContextPurpose.USER_RESPONSE,
        ContextBuildRequest(scope_id="core", awareness_scope=_scope()),
    )
    assert empty.awareness.availability is ContextAvailability.KNOWN_EMPTY
    assert "Peripheral awareness" not in compile_context_projection(empty).rendered

    unknown = ContextFrameBuilder().build(
        ContextPurpose.USER_RESPONSE,
        ContextBuildRequest(scope_id="core"),
    )
    unavailable = ContextFrameBuilder().build(
        ContextPurpose.USER_RESPONSE,
        ContextBuildRequest(scope_id="core", awareness_scope=_scope()),
    )
    assert unknown.awareness.availability is ContextAvailability.UNKNOWN
    assert unavailable.awareness.availability is ContextAvailability.UNAVAILABLE
    assert "Peripheral awareness" not in compile_context_projection(unknown).rendered
    assert "Peripheral awareness" not in compile_context_projection(unavailable).rendered

    class _FailingResolver:
        def resolve(self, scope: AwarenessScope) -> AwarenessContext:
            del scope
            raise RuntimeError("unavailable")

    failed = ContextFrameBuilder(awareness_resolver=_FailingResolver()).build(
        ContextPurpose.USER_RESPONSE,
        ContextBuildRequest(scope_id="core", awareness_scope=_scope()),
    )
    assert failed.awareness.availability is ContextAvailability.UNAVAILABLE


def test_awareness_is_user_response_only() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock)
    _admit(buffer, _observation(10))
    builder = ContextFrameBuilder(
        awareness_resolver=PeripheralAwarenessContextResolver(buffer),
    )
    for purpose in (
        ContextPurpose.AMBIENT_COGNITION,
        ContextPurpose.INTERNAL_APPRAISAL,
        ContextPurpose.TEMPORAL_WAKE,
    ):
        frame = builder.build(
            purpose,
            ContextBuildRequest(scope_id="core", awareness_scope=_scope()),
        )
        assert frame.awareness.availability is ContextAvailability.UNKNOWN
        assert "Peripheral awareness" not in compile_context_projection(frame).rendered


def test_projection_is_read_only_and_does_not_create_other_runtime_effects() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock)
    admission = _admit(buffer, _observation(11))
    assert admission.note is not None
    before_evidence = buffer.evidence()
    resolver = PeripheralAwarenessContextResolver(buffer)
    projected = resolver.resolve(admission.note.scope)
    assert resolver.resolve(admission.note.scope) == projected
    assert buffer.evidence() == before_evidence
    assert buffer.snapshot_active(admission.note.scope)[0] == admission.note
    assert admission.note.status.value == "active"


def test_awareness_block_is_whole_omitted_under_budget_and_evidence_is_content_free() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock)
    _admit(buffer, _observation(12))
    composer = ProductionContextComposer(
        ContextFrameBuilder(awareness_resolver=PeripheralAwarenessContextResolver(buffer)),
        request_factory=_request_factory(_scope()),
    )
    blocks = composer.compose_projection(
        ContextPurpose.USER_RESPONSE,
        scope_id="core",
        existing_guidance=("x" * (MAX_GUIDANCE_BYTES - 1),),
    )
    assert blocks == ()
    evidence = composer.evidence()[-1]
    assert evidence.awareness_availability is ContextAvailability.KNOWN
    assert evidence.awareness_note_count == 1
    assert not evidence.awareness_block_present
    assert evidence.awareness_omitted_by_budget
    assert all("observation-12" not in repr(item) for item in composer.evidence())


def test_awareness_context_bounds_are_explicit_and_reason_reference_bounds_match_buffer() -> None:
    assert MAX_AWARENESS_CONTEXT_NOTES == 8
    valid = AwarenessContextNote(
        note_id="note:1",
        source_refs=("observation:1", "event:1"),
        reason_codes=(CognitionReasonCode.AMBIENT_CONTEXT,),
        occurrence_count=2,
        first_seen_at=datetime(2030, 1, 1, tzinfo=UTC),
        last_seen_at=datetime(2030, 1, 1, 0, 0, 1, tzinfo=UTC),
    )
    context = AwarenessContext(ContextAvailability.KNOWN, (valid,))
    assert context.notes == (valid,)


def test_projection_caps_a_larger_active_buffer_without_reordering() -> None:
    clock = _Clock(datetime(2030, 1, 1, tzinfo=UTC))
    buffer = PeripheralAwarenessBuffer(clock=clock)
    for number in range(20, 20 + MAX_AWARENESS_CONTEXT_NOTES + 2):
        _admit(buffer, _observation(number), key=AwarenessKey(dedup_key=f"key-{number}"))
    scope = _scope()
    builder = ContextFrameBuilder(
        awareness_resolver=PeripheralAwarenessContextResolver(buffer),
    )

    frame = builder.build(
        ContextPurpose.USER_RESPONSE,
        ContextBuildRequest(scope_id="core", awareness_scope=scope),
    )

    assert len(frame.awareness.notes) == MAX_AWARENESS_CONTEXT_NOTES
    assert (
        tuple(item.note_id for item in frame.awareness.notes)
        == tuple(item.note_id for item in buffer.snapshot_active(scope))[
            :MAX_AWARENESS_CONTEXT_NOTES
        ]
    )
