"""Focused CTX-V1-B builder, projection, trust, and isolation proofs."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest
from lilavel_core.conversation import ConversationCore

from lilavel_runtime import (
    CTX_V1_B_SCENARIOS,
    ActivityKind,
    CapabilityId,
    CapabilityProjection,
    ContextAvailability,
    ContextBuildRequest,
    ContextFrameBuilder,
    ContextProvenance,
    ContextProviderError,
    ContextPurpose,
    ContextSourceRef,
    ContextValue,
    DeclaredCapabilityResolver,
    EnvironmentContext,
    EnvironmentKind,
    IntentionResolution,
    InteractionContext,
    MindIntention,
    MindState,
    MindStateIntentionResolver,
    ParticipantRef,
    SocialResolution,
    TemporalContext,
    TemporalResolution,
    ValidatedAdapterCapability,
    compile_context_projection,
    evaluate_context_builder_corpus,
)
from lilavel_runtime.context import MAX_CONTEXT_LABEL_BYTES, MAX_CONTEXT_SOURCE_REFS
from lilavel_runtime.intervention import (
    ActivityState,
    FloorState,
    FreshnessBucket,
    FreshnessClass,
    HandlingState,
    InterventionBudgetState,
    RecentSpeechState,
    SocialPermissionContext,
    SocialSensitivity,
    SpeakingSurfaceState,
)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class StaticEnvironment:
    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> EnvironmentContext:
        del purpose, request
        return EnvironmentContext(
            ContextAvailability.KNOWN,
            kind=EnvironmentKind.CHAT,
            environment_ref="environment:current",
        )


class StaticInteraction:
    def __init__(self, participants: ContextValue[tuple[ParticipantRef, ...]]) -> None:
        self.participants = participants

    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> InteractionContext:
        del purpose, request
        return InteractionContext(
            ContextValue(ContextAvailability.KNOWN, ActivityKind.USER_TURN),
            self.participants,
            ContextValue(ContextAvailability.UNKNOWN, reason="other_surface_not_observed"),
        )


class StaticIntentions:
    def __init__(self, intentions: tuple[MindIntention, ...]) -> None:
        self.intentions = intentions

    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> IntentionResolution:
        del purpose
        selected = tuple(
            item
            for item in self.intentions
            if not request.relevant_intention_ids
            or item.intention_id in request.relevant_intention_ids
        )
        return (
            IntentionResolution(ContextAvailability.KNOWN, selected)
            if selected
            else IntentionResolution(ContextAvailability.KNOWN_EMPTY)
        )


class StaticTemporal:
    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> TemporalResolution:
        del purpose, request
        return TemporalResolution(
            ContextAvailability.KNOWN,
            TemporalContext(
                now=datetime(2000, 1, 1, tzinfo=UTC),
                wake_intent_ref="wake:validated",
                reconsideration_reason="recheck current state",
            ),
        )


class StaticSocial:
    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> SocialResolution:
        del purpose, request
        return SocialResolution(
            ContextAvailability.KNOWN,
            SocialPermissionContext(
                activity=ActivityState.CURRENT,
                floor=FloorState.FREE,
                speaking_surface=SpeakingSurfaceState.AVAILABLE,
                freshness_class=FreshnessClass.REACTIVE,
                freshness=FreshnessBucket.FRESH,
                recent_speech=RecentSpeechState.CLEAR,
                intervention_budget=InterventionBudgetState.AVAILABLE,
                handling=HandlingState.UNRESOLVED,
                sensitivity=SocialSensitivity.ORDINARY,
            ),
        )


def _rich_builder() -> ContextFrameBuilder:
    intention = MindIntention(
        "intention:0",
        "bounded intention",
        "user:0",
        "assistant:0",
    )
    return ContextFrameBuilder(
        clock=FixedClock(datetime(2026, 9, 13, 12, tzinfo=UTC)),
        environment_resolver=StaticEnvironment(),
        interaction_resolver=StaticInteraction(
            ContextValue(
                ContextAvailability.KNOWN,
                (ParticipantRef("user:current"),),
            )
        ),
        intention_resolver=StaticIntentions((intention,)),
        capability_resolver=DeclaredCapabilityResolver(
            runtime_declared=(
                CapabilityProjection(
                    CapabilityId.CONVERSE,
                    ContextAvailability.KNOWN,
                    ContextProvenance.RUNTIME_DECLARED,
                ),
            )
        ),
        social_resolver=StaticSocial(),
        temporal_resolver=StaticTemporal(),
    )


def _request(**overrides: object) -> ContextBuildRequest:
    values: dict[str, object] = {"scope_id": "scope:current"}
    values.update(overrides)
    return ContextBuildRequest(**values)  # type: ignore[arg-type]


def test_ctx_v1_b_corpus_has_thirty_five_passing_scenarios() -> None:
    report = evaluate_context_builder_corpus()

    assert len(CTX_V1_B_SCENARIOS) == 35
    assert report.scenario_count == 35
    assert report.passed
    assert all(result.passed for result in report.scenario_results)


def test_builder_constructs_all_purposes_and_only_temporal_renders_time() -> None:
    clock = FixedClock(datetime(2026, 9, 13, 12, tzinfo=UTC))
    builder = ContextFrameBuilder(clock=clock)
    request = _request(frame_id="frame:test")

    frames = {purpose: builder.build(purpose, request) for purpose in ContextPurpose}

    assert set(frames) == set(ContextPurpose)
    assert (
        "2026-09-13T12:00:00"
        not in compile_context_projection(frames[ContextPurpose.USER_RESPONSE]).rendered
    )
    assert (
        "2026-09-13T12:00:00"
        in compile_context_projection(frames[ContextPurpose.TEMPORAL_WAKE]).rendered
    )


def test_builder_selects_minimum_domains_and_keeps_social_advisory() -> None:
    builder = _rich_builder()
    request = _request(
        relevant_intention_ids=("intention:0",),
        source_refs=(ContextSourceRef("observation:1", ContextProvenance.RUNTIME_DECLARED),),
    )

    user = builder.build(ContextPurpose.USER_RESPONSE, request)
    ambient = builder.build(ContextPurpose.AMBIENT_COGNITION, request)
    internal = builder.build(ContextPurpose.INTERNAL_APPRAISAL, request)

    assert user.social.availability is ContextAvailability.UNKNOWN
    assert user.interaction.other_surface_activity.availability is ContextAvailability.UNKNOWN
    assert ambient.social.availability is ContextAvailability.KNOWN
    assert ambient.source_refs
    assert internal.capabilities.availability is ContextAvailability.UNKNOWN
    assert "does not authorize an effect" in compile_context_projection(ambient).rendered


def test_mind_state_resolver_requires_explicit_relevance_and_is_read_only() -> None:
    mind = MindState()
    intention = mind.create_intention(
        "keep the bounded commitment",
        user_message_id="user:1",
        assistant_message_id="assistant:1",
    )
    assert intention is not None
    before = mind.snapshot()
    builder = ContextFrameBuilder(
        clock=FixedClock(datetime(2026, 9, 13, tzinfo=UTC)),
        intention_resolver=MindStateIntentionResolver(mind),
    )

    unrelated = builder.build(
        ContextPurpose.USER_RESPONSE,
        _request(relevant_intention_ids=("intention:other",)),
    )
    related = builder.build(
        ContextPurpose.USER_RESPONSE,
        _request(relevant_intention_ids=(intention.intention_id,)),
    )

    assert unrelated.intentions.availability is ContextAvailability.KNOWN_EMPTY
    assert related.intentions.availability is ContextAvailability.KNOWN
    assert mind.snapshot() == before


def test_capabilities_require_valid_provenance_and_conflicts_fail_closed() -> None:
    runtime = CapabilityProjection(
        CapabilityId.CONVERSE,
        ContextAvailability.KNOWN,
        ContextProvenance.RUNTIME_DECLARED,
    )
    adapter_available = ValidatedAdapterCapability(
        CapabilityProjection(
            CapabilityId.SPEAK,
            ContextAvailability.KNOWN,
            ContextProvenance.VALIDATED_ADAPTER,
        ),
        "scope:current",
        "adapter:a",
    )
    adapter_unavailable = ValidatedAdapterCapability(
        CapabilityProjection(
            CapabilityId.SPEAK,
            ContextAvailability.UNAVAILABLE,
            ContextProvenance.VALIDATED_ADAPTER,
            reason="surface_disabled",
        ),
        "scope:current",
        "adapter:b",
    )
    builder = ContextFrameBuilder(
        clock=FixedClock(datetime(2026, 9, 13, tzinfo=UTC)),
        capability_resolver=DeclaredCapabilityResolver(
            runtime_declared=(runtime,),
            validated_adapter=(adapter_available, adapter_unavailable),
        ),
    )

    capabilities = builder.build(ContextPurpose.USER_RESPONSE, _request()).capabilities

    assert capabilities.availability is ContextAvailability.KNOWN
    assert [item.capability for item in capabilities.capabilities] == [
        CapabilityId.CONVERSE,
        CapabilityId.SPEAK,
    ]
    assert capabilities.capabilities[1].availability is ContextAvailability.UNAVAILABLE
    with pytest.raises(ValueError, match="provenance"):
        CapabilityProjection(
            CapabilityId.SPEAK,
            ContextAvailability.KNOWN,
            ContextProvenance.EXTERNAL_PAYLOAD,
        )


def test_provider_failures_are_unknown_and_malformed_values_are_rejected() -> None:
    class FailingEnvironment:
        def resolve(
            self, purpose: ContextPurpose, request: ContextBuildRequest
        ) -> EnvironmentContext:
            del purpose, request
            raise RuntimeError("owner unavailable")

    class MalformedInteraction:
        def resolve(
            self, purpose: ContextPurpose, request: ContextBuildRequest
        ) -> InteractionContext:
            del purpose, request
            return {"activity": "user_turn"}  # type: ignore[return-value]

    failed = ContextFrameBuilder(
        clock=FixedClock(datetime(2026, 9, 13, tzinfo=UTC)),
        environment_resolver=FailingEnvironment(),
    ).build(ContextPurpose.USER_RESPONSE, _request())
    assert failed.environment.availability is ContextAvailability.UNKNOWN

    with pytest.raises(ContextProviderError):
        ContextFrameBuilder(
            clock=FixedClock(datetime(2026, 9, 13, tzinfo=UTC)),
            interaction_resolver=MalformedInteraction(),
        ).build(ContextPurpose.USER_RESPONSE, _request())


def test_request_rejects_untrusted_refs_and_bounds_unicode_structurally() -> None:
    with pytest.raises(ValueError, match="untrusted"):
        _request(source_refs=(ContextSourceRef("payload", ContextProvenance.EXTERNAL_PAYLOAD),))
    with pytest.raises(ValueError, match="source reference bound"):
        _request(
            source_refs=tuple(
                ContextSourceRef(f"source:{index}", ContextProvenance.RUNTIME_DECLARED)
                for index in range(MAX_CONTEXT_SOURCE_REFS + 1)
            )
        )

    intention = MindIntention(
        "intention:unicode",
        "🙂" * 128,
        "user:unicode",
        "assistant:unicode",
    )
    builder = ContextFrameBuilder(
        clock=FixedClock(datetime(2026, 9, 13, tzinfo=UTC)),
        intention_resolver=StaticIntentions((intention,)),
    )
    frame = builder.build(
        ContextPurpose.USER_RESPONSE,
        _request(relevant_intention_ids=(intention.intention_id,)),
    )

    projection = frame.intentions.intentions[0].projection
    assert projection is not None
    assert len(projection.encode("utf-8")) <= MAX_CONTEXT_LABEL_BYTES
    assert len(compile_context_projection(frame).rendered.encode("utf-8")) <= 8 * 1_024


def test_context_frame_builder_is_not_a_model_or_effect_path() -> None:
    source = inspect.getsource(ContextFrameBuilder)
    request_source = inspect.getsource(ConversationCore.build_model_request)

    assert "ModelRequest" not in source
    assert "generate" not in source
    assert "execute" not in source
    assert "ContextFrame" not in request_source
    assert "ObservationWindow" not in source
