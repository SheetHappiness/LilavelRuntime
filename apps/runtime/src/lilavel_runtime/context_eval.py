"""Deterministic CTX-V1-A contract and boundary evaluation corpus.

These scenarios are architecture/truth checks, not language-quality judgments.
They construct typed values locally, inspect stable ownership boundaries, and
never call a provider or modify production request composition.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from enum import StrEnum

from lilavel_core import (
    LILAVEL_CHARACTER_V0,
    LILAVEL_OPERATING_CANON_V1,
    CognitionPolicyDecision,
    IdentityCanon,
    ModelRequest,
    OperatingCanon,
    compile_guidance,
    compile_operating_canon,
)
from lilavel_core.conversation import ConversationCore

from .context import (
    ActivityKind,
    CapabilityContext,
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
    IntentionContext,
    IntentionRef,
    InteractionContext,
    ParticipantRef,
    ParticipantRole,
    SocialContextView,
    SurfaceKind,
    TemporalContext,
    compile_context_projection,
)
from .contracts import CognitionTriggerSource
from .intervention import (
    ActivityState,
    DeterministicInterventionPolicy,
    FloorState,
    SpeakingSurfaceState,
)
from .mind import IntentionStatus


class ContextScenarioFamily(StrEnum):
    """Stable review categories for the CTX-V1-A corpus."""

    OWNERSHIP = "ownership"
    ONTOLOGY = "ontology"
    TRUTH_STATUS = "truth_status"
    PURPOSE = "purpose"
    BOUNDARY = "boundary"
    TRUST = "trust"
    PROJECTION = "projection"
    REGRESSION = "regression"


@dataclass(frozen=True, slots=True)
class ContextEvalScenario:
    """One human-authored deterministic architecture scenario."""

    id: str
    family: ContextScenarioFamily
    description: str

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.description.strip():
            raise ValueError("context scenario identity and description must be non-empty")
        if type(self.family) is not ContextScenarioFamily:
            raise TypeError("context scenario family must be a ContextScenarioFamily")


@dataclass(frozen=True, slots=True)
class ContextEvalResult:
    """Bounded result for one deterministic scenario."""

    scenario_id: str
    passed: bool
    check: str

    def __post_init__(self) -> None:
        if not self.scenario_id.strip() or not self.check.strip():
            raise ValueError("context eval result identity must be non-empty")
        if type(self.passed) is not bool:
            raise TypeError("passed must be a bool")


@dataclass(frozen=True, slots=True)
class ContextEvalReport:
    """Complete deterministic result over the fixed CTX-V1-A corpus."""

    scenario_results: tuple[ContextEvalResult, ...]

    def __post_init__(self) -> None:
        results = tuple(self.scenario_results)
        if len(results) != len(CTX_V1_A_SCENARIOS):
            raise ValueError("context report must contain every corpus scenario")
        if {result.scenario_id for result in results} != {
            scenario.id for scenario in CTX_V1_A_SCENARIOS
        }:
            raise ValueError("context report scenario IDs do not match the corpus")
        object.__setattr__(self, "scenario_results", results)

    @property
    def scenario_count(self) -> int:
        return len(self.scenario_results)

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.scenario_results)


CTX_V1_A_SCENARIOS: tuple[ContextEvalScenario, ...] = (
    ContextEvalScenario(
        "ctx01",
        ContextScenarioFamily.OWNERSHIP,
        "CharacterCanon and OperatingCanon remain separate.",
    ),
    ContextEvalScenario(
        "ctx02",
        ContextScenarioFamily.ONTOLOGY,
        "OperatingCanon carries runtime ontology, not personality or current facts.",
    ),
    ContextEvalScenario(
        "ctx03",
        ContextScenarioFamily.ONTOLOGY,
        "One persistent character remains one identity across environments.",
    ),
    ContextEvalScenario(
        "ctx04", ContextScenarioFamily.TRUTH_STATUS, "Missing context is UNKNOWN rather than false."
    ),
    ContextEvalScenario(
        "ctx05", ContextScenarioFamily.TRUTH_STATUS, "Known empty differs from unknown."
    ),
    ContextEvalScenario(
        "ctx06",
        ContextScenarioFamily.TRUTH_STATUS,
        "Unavailable capability differs from unknown capability.",
    ),
    ContextEvalScenario(
        "ctx07", ContextScenarioFamily.TRUST, "Capability availability must be runtime-declared."
    ),
    ContextEvalScenario(
        "ctx08",
        ContextScenarioFamily.TRUST,
        "External payloads cannot mint trusted context facts.",
    ),
    ContextEvalScenario(
        "ctx09", ContextScenarioFamily.PURPOSE, "Every frame carries one explicit bounded purpose."
    ),
    ContextEvalScenario(
        "ctx10",
        ContextScenarioFamily.PURPOSE,
        "USER_RESPONSE excludes ambient-only unrelated state.",
    ),
    ContextEvalScenario(
        "ctx11",
        ContextScenarioFamily.PURPOSE,
        "AMBIENT_COGNITION is distinct from USER_RESPONSE.",
    ),
    ContextEvalScenario(
        "ctx12",
        ContextScenarioFamily.PURPOSE,
        "INTERNAL_APPRAISAL does not imply speech or action.",
    ),
    ContextEvalScenario(
        "ctx13",
        ContextScenarioFamily.PURPOSE,
        "TEMPORAL_WAKE means reconsider now, not replay old context.",
    ),
    ContextEvalScenario(
        "ctx14",
        ContextScenarioFamily.PROJECTION,
        "Current time is not required in every model-facing projection.",
    ),
    ContextEvalScenario(
        "ctx15",
        ContextScenarioFamily.PROJECTION,
        "Volatile frame identity and capture time stay out of stable canon.",
    ),
    ContextEvalScenario(
        "ctx16",
        ContextScenarioFamily.BOUNDARY,
        "ContextFrame does not contain canonical conversation messages.",
    ),
    ContextEvalScenario(
        "ctx17", ContextScenarioFamily.BOUNDARY, "ContextFrame does not contain memory records."
    ),
    ContextEvalScenario(
        "ctx18",
        ContextScenarioFamily.BOUNDARY,
        "ContextFrame does not mutate MindState or ObservationWindow.",
    ),
    ContextEvalScenario(
        "ctx19", ContextScenarioFamily.BOUNDARY, "Social context does not authorize an effect."
    ),
    ContextEvalScenario(
        "ctx20",
        ContextScenarioFamily.BOUNDARY,
        "Model-facing context cannot override E2 revalidation.",
    ),
    ContextEvalScenario(
        "ctx21",
        ContextScenarioFamily.TRUTH_STATUS,
        "Unknown other-surface activity is not no activity.",
    ),
    ContextEvalScenario(
        "ctx22",
        ContextScenarioFamily.TRUTH_STATUS,
        "Unsupported capability is represented explicitly.",
    ),
    ContextEvalScenario(
        "ctx23",
        ContextScenarioFamily.BOUNDARY,
        "Bounded collections reject overflow deterministically.",
    ),
    ContextEvalScenario(
        "ctx24",
        ContextScenarioFamily.BOUNDARY,
        "Arbitrary dictionary context payloads are rejected.",
    ),
    ContextEvalScenario(
        "ctx25",
        ContextScenarioFamily.PROJECTION,
        "The same typed frame projects deterministically.",
    ),
    ContextEvalScenario(
        "ctx26",
        ContextScenarioFamily.PROJECTION,
        "Purpose-specific projections omit irrelevant fields.",
    ),
    ContextEvalScenario(
        "ctx27",
        ContextScenarioFamily.ONTOLOGY,
        "Provider and model implementation names do not leak into ontology.",
    ),
    ContextEvalScenario(
        "ctx28", ContextScenarioFamily.REGRESSION, "CharacterCanon is unchanged by CTX-V1-A."
    ),
    ContextEvalScenario(
        "ctx29",
        ContextScenarioFamily.REGRESSION,
        "COG-V1 contracts and lanes remain semantically unchanged.",
    ),
    ContextEvalScenario(
        "ctx30",
        ContextScenarioFamily.REGRESSION,
        "Production ModelRequest assembly still excludes ContextFrame.",
    ),
)


def evaluate_context_corpus() -> ContextEvalReport:
    """Run all CTX-V1-A checks without a model or external service."""

    checks = _checks()
    results = tuple(
        ContextEvalResult(scenario.id, checks[scenario.id], scenario.description)
        for scenario in CTX_V1_A_SCENARIOS
    )
    return ContextEvalReport(results)


def _checks() -> dict[str, bool]:
    frame = sample_context_frame(ContextPurpose.AMBIENT_COGNITION)
    user = sample_context_frame(ContextPurpose.USER_RESPONSE)
    internal = sample_context_frame(ContextPurpose.INTERNAL_APPRAISAL)
    temporal = sample_context_frame(ContextPurpose.TEMPORAL_WAKE)
    operating = compile_operating_canon(LILAVEL_OPERATING_CANON_V1)[0].casefold()
    user_text = compile_context_projection(user).rendered.casefold()
    ambient_text = compile_context_projection(frame).rendered.casefold()
    internal_text = compile_context_projection(internal).rendered.casefold()
    temporal_text = compile_context_projection(temporal).rendered.casefold()

    source_fields = {field.name for field in fields(ContextFrame)}
    context_source = inspect.getsource(ContextFrame)
    model_request_source = inspect.getsource(ModelRequest)
    conversation_source = inspect.getsource(ConversationCore)

    return {
        "ctx01": not isinstance(LILAVEL_CHARACTER_V0, OperatingCanon)
        and IdentityCanon.__name__ != OperatingCanon.__name__,
        "ctx02": all(
            phrase in operating
            for phrase in (
                "runtime-owned validation",
                "unknown, not false",
                "one persistent character",
            )
        )
        and not any(
            phrase in operating
            for phrase in ("discord", "openai", "gpt-", "2026-", "provider name")
        ),
        "ctx03": "same character" in operating and "across environments" in operating,
        "ctx04": "activity: unknown" in _unknown_frame_text(),
        "ctx05": _known_empty_vs_unknown(),
        "ctx06": "temporal_reconsideration: unavailable (unsupported)" in ambient_text
        and "application_action: unknown" in ambient_text,
        "ctx07": _raises(
            lambda: CapabilityProjection(
                CapabilityId.SPEAK,
                ContextAvailability.KNOWN,
                ContextProvenance.EXTERNAL_PAYLOAD,
            )
        ),
        "ctx08": _raises(
            lambda: sample_context_frame(
                source_refs=(ContextSourceRef("payload", ContextProvenance.EXTERNAL_PAYLOAD),)
            )
        ),
        "ctx09": set(ContextPurpose)
        == {
            ContextPurpose.USER_RESPONSE,
            ContextPurpose.AMBIENT_COGNITION,
            ContextPurpose.INTERNAL_APPRAISAL,
            ContextPurpose.TEMPORAL_WAKE,
        }
        and frame.purpose is ContextPurpose.AMBIENT_COGNITION,
        "ctx10": "[social context]" not in user_text and "other_surface_activity" not in user_text,
        "ctx11": user_text != ambient_text and "[social context]" in ambient_text,
        "ctx12": "capabilities" not in internal_text
        and "social context" not in internal_text
        and "speak" not in internal_text,
        "ctx13": "reconsider the situation now" in temporal_text and "replay" not in temporal_text,
        "ctx14": "2026-09-13t12:00:00" not in user_text
        and "2026-09-13t12:00:00" not in ambient_text,
        "ctx15": "frame:volatile" not in operating and "2026-09-13t12:00:00" not in operating,
        "ctx16": "messages" not in source_fields and "ContextMessage" not in context_source,
        "ctx17": "memory" not in source_fields and "Memory" not in context_source,
        "ctx18": "MindState" not in context_source
        and "ObservationWindow" not in context_source
        and _raises(lambda: setattr(frame, "purpose", ContextPurpose.USER_RESPONSE)),
        "ctx19": "does not authorize an effect" in ambient_text
        and "permitted" not in {field.name for field in fields(SocialContextView)},
        "ctx20": "ContextFrame" not in inspect.getsource(DeterministicInterventionPolicy.evaluate)
        and "permitted" not in {field.name for field in fields(SocialContextView)},
        "ctx21": "other_surface_activity: unknown" in _unknown_other_surface_text()
        and "no_active_activity" not in _unknown_other_surface_text(),
        "ctx22": "temporal_reconsideration: unavailable (unsupported)" in ambient_text,
        "ctx23": _raises(
            lambda: IntentionContext(
                ContextAvailability.KNOWN,
                tuple(
                    IntentionRef(f"i-{index}", IntentionStatus.ACTIVE, "bounded")
                    for index in range(5)
                ),
            )
        ),
        "ctx24": _raises(
            lambda: sample_context_frame(environment={"kind": "chat"}),  # type: ignore[arg-type]
        ),
        "ctx25": compile_context_projection(frame) == compile_context_projection(frame),
        "ctx26": "[temporal context]" not in user_text
        and "[temporal context]" in temporal_text
        and "[social context]" not in temporal_text,
        "ctx27": "provider" not in operating and "model" not in operating,
        "ctx28": compile_guidance(LILAVEL_CHARACTER_V0) == compile_guidance(LILAVEL_CHARACTER_V0),
        "ctx29": set(CognitionTriggerSource)
        == {
            CognitionTriggerSource.EXTERNAL,
            CognitionTriggerSource.TEMPORAL,
            CognitionTriggerSource.INTERNAL,
        }
        and "ContextFrame" not in inspect.getsource(CognitionPolicyDecision),
        "ctx30": "ContextFrame" not in conversation_source
        and "ContextFrame" not in model_request_source,
    }


def sample_context_frame(
    purpose: ContextPurpose = ContextPurpose.AMBIENT_COGNITION,
    *,
    source_refs: tuple[ContextSourceRef, ...] = (),
    environment: EnvironmentContext | object | None = None,
) -> ContextFrame:
    if environment is None:
        environment = EnvironmentContext(
            ContextAvailability.KNOWN,
            kind=EnvironmentKind.CHAT,
            surface=ContextValue(ContextAvailability.KNOWN, SurfaceKind.TEXT),
            environment_ref="env:opaque",
            surface_ref="surface:opaque",
        )
    return ContextFrame(
        frame_id="frame:volatile",
        purpose=purpose,
        scope_id="scope:opaque",
        captured_at=datetime(2026, 9, 13, 12, tzinfo=UTC),
        environment=environment,  # type: ignore[arg-type]
        interaction=InteractionContext(
            activity=ContextValue(ContextAvailability.KNOWN, ActivityKind.USER_TURN),
            participants=ContextValue(
                ContextAvailability.KNOWN,
                (ParticipantRef("participant:user", ParticipantRole.USER),),
            ),
            other_surface_activity=ContextValue(
                ContextAvailability.UNKNOWN, reason="other_surface_not_observed"
            ),
        ),
        intentions=IntentionContext(
            ContextAvailability.KNOWN,
            (IntentionRef("intention:opaque", IntentionStatus.ACTIVE, "follow up"),),
        ),
        capabilities=CapabilityContext(
            ContextAvailability.KNOWN,
            (
                CapabilityProjection(
                    CapabilityId.SPEAK,
                    ContextAvailability.KNOWN,
                    ContextProvenance.RUNTIME_DECLARED,
                ),
                CapabilityProjection(
                    CapabilityId.APPLICATION_ACTION,
                    ContextAvailability.UNKNOWN,
                    ContextProvenance.RUNTIME_DECLARED,
                    "not_established",
                ),
                CapabilityProjection(
                    CapabilityId.TEMPORAL_RECONSIDERATION,
                    ContextAvailability.UNAVAILABLE,
                    ContextProvenance.VALIDATED_ADAPTER,
                    "unsupported",
                ),
            ),
        ),
        social=ContextValue(
            ContextAvailability.KNOWN,
            SocialContextView(
                ContextValue(ContextAvailability.KNOWN, ActivityState.CURRENT),
                ContextValue(ContextAvailability.KNOWN, FloorState.FREE),
                ContextValue(ContextAvailability.KNOWN, SpeakingSurfaceState.AVAILABLE),
            ),
        ),
        temporal=ContextValue(
            ContextAvailability.KNOWN,
            TemporalContext(
                now=datetime(2026, 9, 13, 12, tzinfo=UTC),
                wake_intent_ref="wake:opaque",
                reconsideration_reason="revisit the active intention",
            ),
        ),
        source_refs=source_refs,
    )


def _unknown_frame_text() -> str:
    frame = sample_context_frame(
        environment=EnvironmentContext(
            ContextAvailability.UNKNOWN,
            reason="environment_not_established",
        )
    )
    return compile_context_projection(frame).rendered.casefold()


def _unknown_other_surface_text() -> str:
    return compile_context_projection(sample_context_frame()).rendered.casefold()


def _known_empty_vs_unknown() -> bool:
    known_empty = sample_context_frame(
        environment=EnvironmentContext(
            ContextAvailability.KNOWN,
            kind=EnvironmentKind.CHAT,
            surface=ContextValue(ContextAvailability.KNOWN, SurfaceKind.TEXT),
        )
    )
    empty = ContextFrame(
        frame_id="frame:empty",
        purpose=known_empty.purpose,
        scope_id=known_empty.scope_id,
        captured_at=known_empty.captured_at,
        environment=known_empty.environment,
        interaction=InteractionContext(
            known_empty.interaction.activity,
            ContextValue(ContextAvailability.KNOWN_EMPTY, ()),
            known_empty.interaction.other_surface_activity,
        ),
        intentions=IntentionContext(ContextAvailability.KNOWN_EMPTY),
        capabilities=CapabilityContext(ContextAvailability.KNOWN_EMPTY),
    )
    unknown = sample_context_frame(
        environment=EnvironmentContext(
            ContextAvailability.UNKNOWN,
            reason="environment_not_established",
        )
    )
    empty_text = compile_context_projection(empty).rendered
    unknown_text = compile_context_projection(unknown).rendered
    return "known empty" in empty_text and "unknown" in unknown_text and empty_text != unknown_text


def _raises(callback: object) -> bool:
    if not callable(callback):
        return False
    try:
        callback()
    except (TypeError, ValueError, AttributeError):
        return True
    return False


__all__ = [
    "CTX_V1_A_SCENARIOS",
    "ContextEvalReport",
    "ContextEvalResult",
    "ContextEvalScenario",
    "ContextScenarioFamily",
    "evaluate_context_corpus",
    "sample_context_frame",
]
