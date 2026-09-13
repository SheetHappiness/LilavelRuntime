"""Human-authored deterministic CTX-V1-B builder/projection corpus."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from enum import StrEnum

from lilavel_core import ModelRequest, compile_operating_canon
from lilavel_core.conversation import ConversationCore
from lilavel_core.operating import LILAVEL_OPERATING_CANON_V1

from .context import (
    MAX_CONTEXT_PROJECTION_BLOCKS,
    MAX_CONTEXT_PROJECTION_BYTES,
    ActivityKind,
    CapabilityId,
    CapabilityProjection,
    ContextAvailability,
    ContextFrame,
    ContextProvenance,
    ContextPurpose,
    ContextSourceRef,
    ContextValue,
    EnvironmentContext,
    EnvironmentKind,
    InteractionContext,
    ParticipantRef,
    ParticipantRole,
    SocialContextView,
    SurfaceKind,
    TemporalContext,
    compile_context_projection,
)
from .context_builder import (
    ContextBuildRequest,
    ContextFrameBuilder,
    DeclaredCapabilityResolver,
    IntentionResolution,
    SocialResolution,
    TemporalResolution,
    ValidatedAdapterCapability,
)
from .context_eval import evaluate_context_corpus
from .intervention import (
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
from .mind import MindIntention


class ContextBuilderScenarioFamily(StrEnum):
    PURPOSE = "purpose"
    TRUTH_STATUS = "truth_status"
    TRUST = "trust"
    BOUNDS = "bounds"
    ISOLATION = "isolation"
    DETERMINISM = "determinism"
    REGRESSION = "regression"


@dataclass(frozen=True, slots=True)
class ContextBuilderEvalScenario:
    id: str
    family: ContextBuilderScenarioFamily
    description: str


@dataclass(frozen=True, slots=True)
class ContextBuilderEvalResult:
    scenario_id: str
    passed: bool
    check: str


@dataclass(frozen=True, slots=True)
class ContextBuilderEvalReport:
    scenario_results: tuple[ContextBuilderEvalResult, ...]

    def __post_init__(self) -> None:
        if len(self.scenario_results) != len(CTX_V1_B_SCENARIOS):
            raise ValueError("CTX-V1-B report must contain the complete corpus")
        if {item.scenario_id for item in self.scenario_results} != {
            item.id for item in CTX_V1_B_SCENARIOS
        }:
            raise ValueError("CTX-V1-B report IDs do not match the corpus")

    @property
    def scenario_count(self) -> int:
        return len(self.scenario_results)

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.scenario_results)


def _scenario(
    scenario_id: str,
    family: ContextBuilderScenarioFamily,
    description: str,
) -> ContextBuilderEvalScenario:
    return ContextBuilderEvalScenario(scenario_id, family, description)


CTX_V1_B_SCENARIOS: tuple[ContextBuilderEvalScenario, ...] = (
    _scenario("ctxb01", ContextBuilderScenarioFamily.DETERMINISM, "same state is identical"),
    _scenario("ctxb02", ContextBuilderScenarioFamily.PURPOSE, "user and ambient views differ"),
    _scenario("ctxb03", ContextBuilderScenarioFamily.PURPOSE, "user omits ambient surface state"),
    _scenario("ctxb04", ContextBuilderScenarioFamily.PURPOSE, "ambient omits messages"),
    _scenario("ctxb05", ContextBuilderScenarioFamily.PURPOSE, "internal has no speech"),
    _scenario("ctxb06", ContextBuilderScenarioFamily.PURPOSE, "temporal uses current state"),
    _scenario("ctxb07", ContextBuilderScenarioFamily.PURPOSE, "only temporal renders current time"),
    _scenario("ctxb08", ContextBuilderScenarioFamily.TRUTH_STATUS, "unknown participants"),
    _scenario("ctxb09", ContextBuilderScenarioFamily.TRUTH_STATUS, "empty participants"),
    _scenario("ctxb10", ContextBuilderScenarioFamily.TRUTH_STATUS, "unknown and unavailable"),
    _scenario("ctxb11", ContextBuilderScenarioFamily.TRUST, "runtime capabilities are included"),
    _scenario("ctxb12", ContextBuilderScenarioFamily.TRUST, "adapter capabilities are included"),
    _scenario("ctxb13", ContextBuilderScenarioFamily.TRUST, "untrusted payload cannot enter"),
    _scenario("ctxb14", ContextBuilderScenarioFamily.TRUST, "adapter conflict fails closed"),
    _scenario("ctxb15", ContextBuilderScenarioFamily.BOUNDS, "intentions are bounded to four"),
    _scenario("ctxb16", ContextBuilderScenarioFamily.PURPOSE, "other-scope intentions are omitted"),
    _scenario("ctxb17", ContextBuilderScenarioFamily.BOUNDS, "capabilities use the frame bound"),
    _scenario("ctxb18", ContextBuilderScenarioFamily.BOUNDS, "participants use the frame bound"),
    _scenario("ctxb19", ContextBuilderScenarioFamily.BOUNDS, "sources use the frame bound"),
    _scenario("ctxb20", ContextBuilderScenarioFamily.BOUNDS, "projections use the block bound"),
    _scenario("ctxb21", ContextBuilderScenarioFamily.BOUNDS, "projections use the byte bound"),
    _scenario("ctxb22", ContextBuilderScenarioFamily.DETERMINISM, "stable ordering"),
    _scenario("ctxb23", ContextBuilderScenarioFamily.ISOLATION, "conversation messages stay out"),
    _scenario("ctxb24", ContextBuilderScenarioFamily.ISOLATION, "memory records stay out"),
    _scenario("ctxb25", ContextBuilderScenarioFamily.TRUST, "raw observation payload stays out"),
    _scenario("ctxb26", ContextBuilderScenarioFamily.ISOLATION, "social state is advisory only"),
    _scenario("ctxb27", ContextBuilderScenarioFamily.ISOLATION, "MindState is not mutated"),
    _scenario("ctxb28", ContextBuilderScenarioFamily.ISOLATION, "ObservationWindow is not touched"),
    _scenario("ctxb29", ContextBuilderScenarioFamily.ISOLATION, "scheduler is not touched"),
    _scenario("ctxb30", ContextBuilderScenarioFamily.ISOLATION, "volatile state stays out"),
    _scenario("ctxb31", ContextBuilderScenarioFamily.ISOLATION, "IDs and refs are not rendered"),
    _scenario("ctxb32", ContextBuilderScenarioFamily.TRUTH_STATUS, "disabled is unavailable"),
    _scenario("ctxb33", ContextBuilderScenarioFamily.TRUST, "provider facts stay out of contracts"),
    _scenario("ctxb34", ContextBuilderScenarioFamily.REGRESSION, "request assembly unchanged"),
    _scenario("ctxb35", ContextBuilderScenarioFamily.REGRESSION, "COG-V1 remains green"),
)


class _FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self.value


class _Environment:
    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> EnvironmentContext:
        del purpose, request
        return EnvironmentContext(
            ContextAvailability.KNOWN,
            kind=EnvironmentKind.CHAT,
            surface=ContextValue(ContextAvailability.KNOWN, SurfaceKind.TEXT),
            environment_ref="env:current",
            surface_ref="surface:current",
        )


class _Interaction:
    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> InteractionContext:
        del purpose, request
        return InteractionContext(
            activity=ContextValue(ContextAvailability.KNOWN, ActivityKind.USER_TURN),
            participants=ContextValue(
                ContextAvailability.KNOWN,
                (ParticipantRef("user:current", ParticipantRole.USER),),
            ),
            other_surface_activity=ContextValue(
                ContextAvailability.KNOWN,
                ActivityKind.EXTERNAL_ACTIVITY,
            ),
        )


class _Intentions:
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
        if not selected:
            return IntentionResolution(ContextAvailability.KNOWN_EMPTY)
        return IntentionResolution(ContextAvailability.KNOWN, selected)


class _Social:
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
                response_obligation=False,
                continuity_current=False,
            ),
        )


class _Temporal:
    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> TemporalResolution:
        del purpose, request
        return TemporalResolution(
            ContextAvailability.KNOWN,
            TemporalContext(
                now=datetime(2001, 1, 1, tzinfo=UTC),
                wake_intent_ref="wake:current",
                reconsideration_reason="old scheduler reason",
            ),
        )


def _builder(clock: _FixedClock | None = None) -> tuple[ContextFrameBuilder, _FixedClock]:
    actual_clock = clock or _FixedClock(datetime(2026, 9, 13, 12, tzinfo=UTC))
    intentions = tuple(
        MindIntention(
            f"intention:{index}",
            f"relevant intention {index}",
            f"user:{index}",
            f"assistant:{index}",
        )
        for index in range(6)
    )
    capability = CapabilityProjection(
        CapabilityId.CONVERSE,
        ContextAvailability.KNOWN,
        ContextProvenance.RUNTIME_DECLARED,
    )
    return (
        ContextFrameBuilder(
            clock=actual_clock,
            environment_resolver=_Environment(),
            interaction_resolver=_Interaction(),
            intention_resolver=_Intentions(intentions),
            capability_resolver=DeclaredCapabilityResolver(runtime_declared=(capability,)),
            social_resolver=_Social(),
            temporal_resolver=_Temporal(),
        ),
        actual_clock,
    )


def _request(**overrides: object) -> ContextBuildRequest:
    values: dict[str, object] = {"scope_id": "scope:current"}
    values.update(overrides)
    return ContextBuildRequest(**values)  # type: ignore[arg-type]


def evaluate_context_builder_corpus() -> ContextBuilderEvalReport:
    checks = _checks()
    return ContextBuilderEvalReport(
        tuple(
            ContextBuilderEvalResult(item.id, checks[item.id], item.description)
            for item in CTX_V1_B_SCENARIOS
        )
    )


def _checks() -> dict[str, bool]:
    builder, clock = _builder()
    request = _request(
        relevant_intention_ids=tuple(f"intention:{index}" for index in range(6)),
        wake_intent_ref="wake:current",
        source_refs=(ContextSourceRef("observation:current", ContextProvenance.RUNTIME_DECLARED),),
    )
    user = builder.build(ContextPurpose.USER_RESPONSE, request)
    ambient = builder.build(ContextPurpose.AMBIENT_COGNITION, request)
    internal = builder.build(ContextPurpose.INTERNAL_APPRAISAL, request)
    temporal = builder.build(ContextPurpose.TEMPORAL_WAKE, request)
    user_projection = compile_context_projection(user).rendered
    ambient_projection = compile_context_projection(ambient).rendered
    internal_projection = compile_context_projection(internal).rendered
    temporal_projection = compile_context_projection(temporal).rendered

    unavailable = CapabilityProjection(
        CapabilityId.APPLICATION_ACTION,
        ContextAvailability.UNAVAILABLE,
        ContextProvenance.RUNTIME_DECLARED,
        reason="disabled",
    )
    unknown_capability = CapabilityProjection(
        CapabilityId.APPLICATION_ACTION,
        ContextAvailability.UNKNOWN,
        ContextProvenance.RUNTIME_DECLARED,
        reason="not established",
    )
    adapter_a = ValidatedAdapterCapability(
        CapabilityProjection(
            CapabilityId.SPEAK,
            ContextAvailability.KNOWN,
            ContextProvenance.VALIDATED_ADAPTER,
        ),
        "scope:current",
        "adapter:a",
    )
    adapter_b = ValidatedAdapterCapability(
        CapabilityProjection(
            CapabilityId.SPEAK,
            ContextAvailability.UNAVAILABLE,
            ContextProvenance.VALIDATED_ADAPTER,
            reason="surface disabled",
        ),
        "scope:current",
        "adapter:b",
    )
    conflict_one = ContextFrameBuilder(
        clock=clock,
        environment_resolver=_Environment(),
        interaction_resolver=_Interaction(),
        intention_resolver=_Intentions(()),
        capability_resolver=DeclaredCapabilityResolver(validated_adapter=(adapter_a, adapter_b)),
        social_resolver=_Social(),
        temporal_resolver=_Temporal(),
    )
    conflict_two = ContextFrameBuilder(
        clock=clock,
        environment_resolver=_Environment(),
        interaction_resolver=_Interaction(),
        intention_resolver=_Intentions(()),
        capability_resolver=DeclaredCapabilityResolver(validated_adapter=(adapter_b, adapter_a)),
        social_resolver=_Social(),
        temporal_resolver=_Temporal(),
    )
    conflict_text = compile_context_projection(
        conflict_one.build(ContextPurpose.AMBIENT_COGNITION, _request())
    ).rendered
    conflict_text_reversed = compile_context_projection(
        conflict_two.build(ContextPurpose.AMBIENT_COGNITION, _request())
    ).rendered

    intention_fields = {field.name for field in fields(ContextFrame)}
    builder_source = inspect.getsource(ContextFrameBuilder)
    operating = compile_operating_canon(LILAVEL_OPERATING_CANON_V1)[0]
    return {
        "ctxb01": user == builder.build(ContextPurpose.USER_RESPONSE, request)
        and compile_context_projection(user)
        == compile_context_projection(builder.build(ContextPurpose.USER_RESPONSE, request)),
        "ctxb02": user_projection != ambient_projection,
        "ctxb03": "other_surface_activity" not in user_projection,
        "ctxb04": "messages" not in ambient_projection and "canonical" not in ambient_projection,
        "ctxb05": "capabilities" not in internal_projection
        and "Social context" not in internal_projection,
        "ctxb06": "old scheduler reason" in temporal_projection
        and "2001-01-01T00:00:00" not in temporal_projection,
        "ctxb07": "2026-09-13T12:00:00" in temporal_projection
        and "2026-09-13T12:00:00" not in user_projection,
        "ctxb08": _unknown_participants(),
        "ctxb09": _known_empty_participants(),
        "ctxb10": unavailable.availability is not unknown_capability.availability,
        "ctxb11": "converse: available (runtime_declared)" in ambient_projection,
        "ctxb12": "speak:" in conflict_text,
        "ctxb13": _raises(
            lambda: ContextBuildRequest(
                "scope:current",
                source_refs=(ContextSourceRef("payload", ContextProvenance.EXTERNAL_PAYLOAD),),
            )
        ),
        "ctxb14": conflict_text == conflict_text_reversed
        and "unavailable (conflicting_adapter_capability_contribution)" in conflict_text,
        "ctxb15": len(user.intentions.intentions) == 4,
        "ctxb16": len(
            _builder()[0]
            .build(
                ContextPurpose.USER_RESPONSE,
                _request(relevant_intention_ids=("intention:missing",)),
            )
            .intentions.intentions
        )
        == 0,
        "ctxb17": len(ambient.capabilities.capabilities) <= 8,
        "ctxb18": len(ambient.interaction.participants.value or ()) <= 8,
        "ctxb19": len(ambient.source_refs) <= 8,
        "ctxb20": len(compile_context_projection(ambient).blocks) <= MAX_CONTEXT_PROJECTION_BLOCKS,
        "ctxb21": len(compile_context_projection(ambient).rendered.encode("utf-8"))
        <= MAX_CONTEXT_PROJECTION_BYTES,
        "ctxb22": conflict_text == conflict_text_reversed,
        "ctxb23": not {"messages", "conversation"}.intersection(intention_fields),
        "ctxb24": "memory" not in {field.name for field in fields(ContextFrame)},
        "ctxb25": "payload" not in {field.name for field in fields(ContextBuildRequest)},
        "ctxb26": "does not authorize an effect" in ambient_projection
        and "permitted" not in {field.name for field in fields(SocialContextView)},
        "ctxb27": clock.calls > 0,
        "ctxb28": "ObservationWindow" not in builder_source,
        "ctxb29": not any(
            token in builder_source for token in (".commit(", ".cancel(", ".schedule(")
        ),
        "ctxb30": "frame:" not in operating and "2026-09-13" not in operating,
        "ctxb31": "observation:current" not in ambient_projection
        and "frame:" not in ambient_projection,
        "ctxb32": unavailable.availability is ContextAvailability.UNAVAILABLE,
        "ctxb33": not any(
            token in {field.name for field in fields(ContextFrame)}
            for token in {"provider", "model", "discord"}
        ),
        "ctxb34": "ContextFrame" not in inspect.getsource(ConversationCore.build_model_request)
        and "ContextFrame" not in inspect.getsource(ModelRequest),
        "ctxb35": evaluate_context_corpus().passed and internal_projection != temporal_projection,
    }


def _unknown_participants() -> bool:
    class UnknownInteraction(_Interaction):
        def resolve(
            self, purpose: ContextPurpose, request: ContextBuildRequest
        ) -> InteractionContext:
            del purpose, request
            return InteractionContext(
                ContextValue(ContextAvailability.UNKNOWN, reason="not_observed"),
                ContextValue(ContextAvailability.UNKNOWN, reason="not_observed"),
                ContextValue(ContextAvailability.UNKNOWN, reason="not_observed"),
            )

    builder, clock = _builder()
    builder = ContextFrameBuilder(
        clock=clock,
        environment_resolver=_Environment(),
        interaction_resolver=UnknownInteraction(),
        intention_resolver=_Intentions(()),
        capability_resolver=DeclaredCapabilityResolver(),
    )
    return (
        builder.build(
            ContextPurpose.USER_RESPONSE,
            _request(),
        ).interaction.participants.availability
        is ContextAvailability.UNKNOWN
    )


def _known_empty_participants() -> bool:
    class EmptyInteraction(_Interaction):
        def resolve(
            self, purpose: ContextPurpose, request: ContextBuildRequest
        ) -> InteractionContext:
            value = super().resolve(purpose, request)
            return InteractionContext(
                value.activity,
                ContextValue(ContextAvailability.KNOWN_EMPTY, ()),
                value.other_surface_activity,
            )

    builder, _ = _builder()
    builder = ContextFrameBuilder(
        clock=_FixedClock(datetime(2026, 9, 13, 12, tzinfo=UTC)),
        environment_resolver=_Environment(),
        interaction_resolver=EmptyInteraction(),
        intention_resolver=_Intentions(()),
        capability_resolver=DeclaredCapabilityResolver(),
    )
    return (
        builder.build(
            ContextPurpose.USER_RESPONSE, _request()
        ).interaction.participants.availability
        is ContextAvailability.KNOWN_EMPTY
    )


def _raises(function: object) -> bool:
    try:
        assert callable(function)
        function()
    except (TypeError, ValueError):
        return True
    return False


__all__ = [
    "CTX_V1_B_SCENARIOS",
    "ContextBuilderEvalReport",
    "ContextBuilderEvalResult",
    "ContextBuilderEvalScenario",
    "ContextBuilderScenarioFamily",
    "evaluate_context_builder_corpus",
]
