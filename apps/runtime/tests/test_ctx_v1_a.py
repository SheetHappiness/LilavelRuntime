"""Focused CTX-V1-A contract, omission, trust, and regression proofs."""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest
from lilavel_core import (
    LILAVEL_CHARACTER_V0,
    LILAVEL_OPERATING_CANON_V1,
    AttentionDecision,
    IdentityCanon,
    OperatingCanon,
    compile_guidance,
    compile_operating_canon,
)
from lilavel_core.conversation import ConversationCore

from lilavel_runtime import (
    CTX_V1_A_SCENARIOS,
    ActivityKind,
    CapabilityId,
    CapabilityProjection,
    ContextAvailability,
    ContextProvenance,
    ContextPurpose,
    ContextSourceRef,
    ContextValue,
    IntentionContext,
    IntentionRef,
    ParticipantRef,
    ParticipantRole,
    compile_context_projection,
    evaluate_context_corpus,
)
from lilavel_runtime.context import ContextFrame, InteractionContext
from lilavel_runtime.context_eval import sample_context_frame
from lilavel_runtime.mind import IntentionStatus


def test_ctx_v1_a_corpus_has_thirty_passing_deterministic_scenarios() -> None:
    report = evaluate_context_corpus()

    assert len(CTX_V1_A_SCENARIOS) == 30
    assert report.scenario_count == 30
    assert report.passed
    assert all(result.passed for result in report.scenario_results)


def test_operating_canon_is_separate_stable_and_non_current() -> None:
    first = compile_operating_canon(LILAVEL_OPERATING_CANON_V1)
    second = compile_operating_canon(LILAVEL_OPERATING_CANON_V1)
    rendered = first[0].casefold()

    assert first == second
    assert isinstance(LILAVEL_OPERATING_CANON_V1, OperatingCanon)
    assert not isinstance(LILAVEL_CHARACTER_V0, OperatingCanon)
    assert IdentityCanon is not OperatingCanon
    assert "one persistent character" in rendered
    assert "unknown, not false" in rendered
    assert "cognition" in rendered and "effect" in rendered
    assert "reconsider" in rendered and "replay" in rendered
    assert not any(value in rendered for value in ("discord", "gpt-", "2026-", "frame:"))


def test_unknown_known_empty_and_known_false_are_distinct() -> None:
    unknown = ContextValue[bool](ContextAvailability.UNKNOWN, reason="not_observed")
    known_false = ContextValue[bool](ContextAvailability.KNOWN, False)
    known_empty = ContextValue[tuple[ParticipantRef, ...]](ContextAvailability.KNOWN_EMPTY, ())

    assert unknown.value is None
    assert known_false.value is False
    assert known_empty.value == ()
    assert len({unknown.availability, known_false.availability, known_empty.availability}) == 3


def test_purpose_projections_omit_irrelevant_domains_and_time() -> None:
    frame = sample_context_frame(ContextPurpose.AMBIENT_COGNITION)
    user = sample_context_frame(ContextPurpose.USER_RESPONSE)
    temporal = sample_context_frame(ContextPurpose.TEMPORAL_WAKE)

    user_text = compile_context_projection(user).rendered
    ambient_text = compile_context_projection(frame).rendered
    temporal_text = compile_context_projection(temporal).rendered

    assert "[Social context]" not in user_text
    assert "other_surface_activity" not in user_text
    assert "[Social context]" in ambient_text
    assert "other_surface_activity" in ambient_text
    assert "2026-09-13T12:00:00" not in user_text
    assert "[Temporal context]" in temporal_text
    assert "reconsider the situation now" in temporal_text


def test_context_is_immutable_bounded_typed_and_payloads_cannot_be_trusted() -> None:
    frame = sample_context_frame()

    with pytest.raises(FrozenInstanceError):
        frame.purpose = ContextPurpose.USER_RESPONSE  # type: ignore[misc]
    with pytest.raises(ValueError, match="provenance"):
        CapabilityProjection(
            CapabilityId.SPEAK,
            ContextAvailability.KNOWN,
            ContextProvenance.EXTERNAL_PAYLOAD,
        )
    with pytest.raises(ValueError, match="untrusted"):
        sample_context_frame(
            source_refs=(ContextSourceRef("payload", ContextProvenance.EXTERNAL_PAYLOAD),)
        )
    with pytest.raises(TypeError):
        ContextFrame(
            "frame:bad",
            ContextPurpose.USER_RESPONSE,
            "scope:bad",
            datetime(2026, 9, 13, tzinfo=UTC),
            {"kind": "chat"},  # type: ignore[arg-type]
            frame.interaction,
            frame.intentions,
            frame.capabilities,
        )


def test_context_has_no_history_memory_or_model_request_ownership() -> None:
    names = {field.name for field in fields(ContextFrame)}
    context_source = inspect.getsource(ContextFrame)
    request_builder_source = inspect.getsource(ConversationCore.build_model_request)

    assert not names.intersection({"messages", "memory", "mind_state", "observation_window"})
    assert "ContextMessage" not in context_source
    assert "MindState" not in context_source
    assert "ObservationWindow" not in context_source
    assert "ContextFrame" not in request_builder_source
    assert compile_guidance(LILAVEL_CHARACTER_V0) == compile_guidance(LILAVEL_CHARACTER_V0)
    assert AttentionDecision.DROP.value == "drop"


def test_context_subcontracts_reject_overflow_and_keep_unknown_other_surface_explicit() -> None:
    frame = sample_context_frame()

    unknown_text = compile_context_projection(frame).rendered
    assert "other_surface_activity: unknown" in unknown_text
    assert "no_active_activity" not in unknown_text

    with pytest.raises(ValueError, match="participant bound"):
        InteractionContext(
            ContextValue(ContextAvailability.KNOWN, ActivityKind.USER_TURN),
            ContextValue(
                ContextAvailability.KNOWN,
                tuple(ParticipantRef(f"p-{i}", ParticipantRole.OTHER) for i in range(9)),
            ),
            frame.interaction.other_surface_activity,
        )
    with pytest.raises(ValueError, match="intention.*bound"):
        IntentionContext(
            ContextAvailability.KNOWN,
            tuple(IntentionRef(f"i-{i}", IntentionStatus.ACTIVE, "bounded") for i in range(5)),
        )


def test_context_frame_social_view_does_not_create_effect_authority() -> None:
    frame = sample_context_frame(ContextPurpose.INTERNAL_APPRAISAL)

    assert frame.social.availability is ContextAvailability.KNOWN
    assert frame.temporal.availability is ContextAvailability.KNOWN
    assert frame.social.value is not None
    assert {field.name for field in fields(frame.social.value)} == {
        "activity",
        "floor",
        "speaking_surface",
    }
