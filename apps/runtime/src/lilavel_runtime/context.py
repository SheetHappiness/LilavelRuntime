"""Immutable, bounded, provider-neutral current-context contracts.

``ContextFrame`` is a runtime-owned view for one semantic purpose. It is not
canonical conversation history, an observation window, MindState, memory, a
permission object, or a mutable store. CTX-V1-B adds the runtime-owned builder
in a separate module; production model request assembly remains unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, cast

from .intervention import ActivityState, FloorState, SpeakingSurfaceState
from .mind import IntentionStatus

MAX_CONTEXT_INTENTIONS: Final = 4
MAX_CONTEXT_CAPABILITIES: Final = 8
MAX_CONTEXT_PARTICIPANTS: Final = 8
MAX_CONTEXT_SOURCE_REFS: Final = 8
MAX_CONTEXT_OPAQUE_REF_BYTES: Final = 128
MAX_CONTEXT_LABEL_BYTES: Final = 256
MAX_CONTEXT_REASON_BYTES: Final = 128
MAX_CONTEXT_PROJECTION_BLOCKS: Final = 8
MAX_CONTEXT_PROJECTION_BYTES: Final = 8 * 1_024


class ContextPurpose(StrEnum):
    """The small semantic taxonomy for current-context views."""

    USER_RESPONSE = "user_response"
    AMBIENT_COGNITION = "ambient_cognition"
    INTERNAL_APPRAISAL = "internal_appraisal"
    TEMPORAL_WAKE = "temporal_wake"


class ContextAvailability(StrEnum):
    """Explicit truth status for a context value or collection."""

    KNOWN = "known"
    KNOWN_EMPTY = "known_empty"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class ContextProvenance(StrEnum):
    """Provenance classes admitted into a trusted context frame."""

    RUNTIME_DECLARED = "runtime_declared"
    VALIDATED_ADAPTER = "validated_adapter"
    EXTERNAL_PAYLOAD = "external_payload"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ContextValue[T]:
    """A typed value that distinguishes empty, unknown, and unavailable."""

    availability: ContextAvailability
    value: T | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.availability) is not ContextAvailability:
            raise TypeError("availability must be a ContextAvailability")
        if self.availability is ContextAvailability.KNOWN:
            if self.value is None:
                raise ValueError("known context values require a value")
            value = self.value
            if type(value) is tuple and not cast(tuple[object, ...], value):
                raise ValueError("known context values cannot use an empty tuple")
            if self.reason is not None:
                raise ValueError("known context values cannot carry a reason")
        elif self.availability is ContextAvailability.KNOWN_EMPTY:
            value = self.value
            if type(value) is not tuple or cast(tuple[object, ...], value):
                raise ValueError("known-empty context values require an empty tuple")
            if self.reason is not None:
                raise ValueError("known-empty context values cannot carry a reason")
        else:
            if self.value is not None:
                raise ValueError("unknown or unavailable values cannot carry a value")
            _require_bounded_text(self.reason or "", "reason", MAX_CONTEXT_REASON_BYTES)


class EnvironmentKind(StrEnum):
    """Provider-neutral environment classes."""

    CHAT = "chat"
    VOICE = "voice"
    GAME = "game"
    DESKTOP = "desktop"
    OTHER = "other"


class SurfaceKind(StrEnum):
    """Provider-neutral surface/modal classes."""

    TEXT = "text"
    VOICE = "voice"
    GAME = "game"
    DESKTOP = "desktop"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class EnvironmentContext:
    """Current environment facts without provider-specific fields."""

    availability: ContextAvailability
    kind: EnvironmentKind | None = None
    surface: ContextValue[SurfaceKind] = field(
        default_factory=lambda: ContextValue(
            ContextAvailability.UNKNOWN, reason="surface_not_established"
        )
    )
    environment_ref: str | None = None
    surface_ref: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.availability) is not ContextAvailability:
            raise TypeError("environment availability must be a ContextAvailability")
        _validate_context_value(self.surface, SurfaceKind, "surface")
        if self.availability is ContextAvailability.KNOWN_EMPTY:
            raise ValueError("an environment cannot be KNOWN_EMPTY")
        if self.availability is ContextAvailability.KNOWN:
            if type(self.kind) is not EnvironmentKind:
                raise TypeError("known environments require an EnvironmentKind")
            if self.reason is not None:
                raise ValueError("known environments cannot carry a reason")
        else:
            if self.kind is not None or self.environment_ref is not None:
                raise ValueError("unknown or unavailable environments cannot carry facts")
            _require_bounded_text(self.reason or "", "reason", MAX_CONTEXT_REASON_BYTES)
        if self.environment_ref is not None:
            _require_bounded_text(
                self.environment_ref, "environment_ref", MAX_CONTEXT_OPAQUE_REF_BYTES
            )
        if self.surface_ref is not None:
            _require_bounded_text(self.surface_ref, "surface_ref", MAX_CONTEXT_OPAQUE_REF_BYTES)
            if self.surface.availability is not ContextAvailability.KNOWN:
                raise ValueError("surface_ref requires a known surface")


class ActivityKind(StrEnum):
    """Bounded current activity classes."""

    USER_TURN = "user_turn"
    EXTERNAL_ACTIVITY = "external_activity"
    IDLE = "idle"
    NO_ACTIVE_ACTIVITY = "no_active_activity"


class ParticipantRole(StrEnum):
    """Coarse provider-neutral participant roles."""

    USER = "user"
    OTHER = "other"
    SYSTEM = "system"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ParticipantRef:
    """An opaque, bounded participant reference without provider identity."""

    reference: str
    role: ParticipantRole = ParticipantRole.UNKNOWN

    def __post_init__(self) -> None:
        _require_bounded_text(self.reference, "participant reference", MAX_CONTEXT_OPAQUE_REF_BYTES)
        if type(self.role) is not ParticipantRole:
            raise TypeError("participant role must be a ParticipantRole")


@dataclass(frozen=True, slots=True)
class InteractionContext:
    """Current activity and bounded presence view."""

    activity: ContextValue[ActivityKind]
    participants: ContextValue[tuple[ParticipantRef, ...]]
    other_surface_activity: ContextValue[ActivityKind]

    def __post_init__(self) -> None:
        _validate_context_value(self.activity, ActivityKind, "activity")
        _validate_context_value(self.other_surface_activity, ActivityKind, "other_surface_activity")
        _validate_context_value(self.participants, ParticipantRef, "participants", collection=True)
        if self.participants.value is not None:
            participants = self.participants.value
            assert isinstance(participants, tuple)
            if len(participants) > MAX_CONTEXT_PARTICIPANTS:
                raise ValueError("participant bound exceeded")
            if len({item.reference for item in participants}) != len(participants):
                raise ValueError("participant references must be unique")


@dataclass(frozen=True, slots=True)
class IntentionRef:
    """A narrow relevant-intention projection, not a MindState copy."""

    intention_id: str
    status: IntentionStatus
    projection: str | None = None

    def __post_init__(self) -> None:
        _require_bounded_text(self.intention_id, "intention_id", MAX_CONTEXT_OPAQUE_REF_BYTES)
        if type(self.status) is not IntentionStatus:
            raise TypeError("status must be an IntentionStatus")
        if self.projection is not None:
            _require_bounded_text(self.projection, "intention projection", MAX_CONTEXT_LABEL_BYTES)


@dataclass(frozen=True, slots=True)
class IntentionContext:
    """Bounded relevant intention references owned by a later builder."""

    availability: ContextAvailability
    intentions: tuple[IntentionRef, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        _validate_collection_status(
            self.availability,
            self.intentions,
            self.reason,
            "intentions",
            MAX_CONTEXT_INTENTIONS,
        )
        if not all(type(item) is IntentionRef for item in self.intentions):
            raise TypeError("intentions must contain only IntentionRef values")
        if len({item.intention_id for item in self.intentions}) != len(self.intentions):
            raise ValueError("intention IDs must be unique")


class CapabilityId(StrEnum):
    """Small provider-neutral capability vocabulary, not a capability claim."""

    OBSERVE = "observe"
    CONVERSE = "converse"
    SPEAK = "speak"
    TEMPORAL_RECONSIDERATION = "temporal_reconsideration"
    APPLICATION_ACTION = "application_action"


@dataclass(frozen=True, slots=True)
class CapabilityProjection:
    """One runtime-declared capability status for a semantic purpose."""

    capability: CapabilityId
    availability: ContextAvailability
    provenance: ContextProvenance
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.capability) is not CapabilityId:
            raise TypeError("capability must be a CapabilityId")
        if type(self.availability) is not ContextAvailability:
            raise TypeError("availability must be a ContextAvailability")
        if self.availability is ContextAvailability.KNOWN_EMPTY:
            raise ValueError("a capability item cannot be KNOWN_EMPTY")
        if type(self.provenance) is not ContextProvenance:
            raise TypeError("provenance must be a ContextProvenance")
        if self.provenance not in {
            ContextProvenance.RUNTIME_DECLARED,
            ContextProvenance.VALIDATED_ADAPTER,
        }:
            raise ValueError("capability provenance must be runtime-declared or adapter-validated")
        if self.availability in {
            ContextAvailability.UNKNOWN,
            ContextAvailability.UNAVAILABLE,
        }:
            _require_bounded_text(self.reason or "", "capability reason", MAX_CONTEXT_REASON_BYTES)
        elif self.reason is not None:
            raise ValueError("available capabilities cannot carry a reason")


@dataclass(frozen=True, slots=True)
class CapabilityContext:
    """Bounded capability availability explicitly surfaced by runtime code."""

    availability: ContextAvailability
    capabilities: tuple[CapabilityProjection, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        _validate_collection_status(
            self.availability,
            self.capabilities,
            self.reason,
            "capabilities",
            MAX_CONTEXT_CAPABILITIES,
        )
        if not all(type(item) is CapabilityProjection for item in self.capabilities):
            raise TypeError("capabilities must contain only CapabilityProjection values")
        if len({item.capability for item in self.capabilities}) != len(self.capabilities):
            raise ValueError("capability IDs must be unique")


@dataclass(frozen=True, slots=True)
class SocialContextView:
    """Advisory social state projection; it never grants effect permission."""

    activity: ContextValue[ActivityState]
    floor: ContextValue[FloorState]
    speaking_surface: ContextValue[SpeakingSurfaceState]

    def __post_init__(self) -> None:
        _validate_context_value(self.activity, ActivityState, "social activity")
        _validate_context_value(self.floor, FloorState, "social floor")
        _validate_context_value(
            self.speaking_surface, SpeakingSurfaceState, "social speaking surface"
        )


@dataclass(frozen=True, slots=True)
class TemporalContext:
    """Optional temporal facts included only for semantically relevant views."""

    now: datetime | None = None
    wake_intent_ref: str | None = None
    reconsideration_reason: str | None = None

    def __post_init__(self) -> None:
        if self.now is not None:
            if type(self.now) is not datetime:
                raise TypeError("temporal now must be a datetime")
            if self.now.tzinfo is None or self.now.utcoffset() is None:
                raise ValueError("temporal now must be timezone-aware")
        if self.wake_intent_ref is not None:
            _require_bounded_text(
                self.wake_intent_ref, "wake_intent_ref", MAX_CONTEXT_OPAQUE_REF_BYTES
            )
        if self.reconsideration_reason is not None:
            _require_bounded_text(
                self.reconsideration_reason,
                "reconsideration_reason",
                MAX_CONTEXT_REASON_BYTES,
            )


def _unknown_social() -> ContextValue[SocialContextView]:
    return ContextValue(ContextAvailability.UNKNOWN, reason="social_state_not_established")


def _unknown_temporal() -> ContextValue[TemporalContext]:
    return ContextValue(ContextAvailability.UNKNOWN, reason="temporal_state_not_requested")


@dataclass(frozen=True, slots=True)
class ContextSourceRef:
    """Bounded provenance reference; raw external payloads are not authority."""

    reference: str
    provenance: ContextProvenance

    def __post_init__(self) -> None:
        _require_bounded_text(
            self.reference, "context source reference", MAX_CONTEXT_OPAQUE_REF_BYTES
        )
        if type(self.provenance) is not ContextProvenance:
            raise TypeError("provenance must be a ContextProvenance")

    @property
    def trusted(self) -> bool:
        return self.provenance in {
            ContextProvenance.RUNTIME_DECLARED,
            ContextProvenance.VALIDATED_ADAPTER,
        }


@dataclass(frozen=True, slots=True)
class ContextFrame:
    """Immutable current-situation view for exactly one semantic purpose."""

    frame_id: str
    purpose: ContextPurpose
    scope_id: str
    captured_at: datetime
    environment: EnvironmentContext
    interaction: InteractionContext
    intentions: IntentionContext
    capabilities: CapabilityContext
    social: ContextValue[SocialContextView] = field(default_factory=_unknown_social)
    temporal: ContextValue[TemporalContext] = field(default_factory=_unknown_temporal)
    source_refs: tuple[ContextSourceRef, ...] = ()

    def __post_init__(self) -> None:
        _require_bounded_text(self.frame_id, "frame_id", MAX_CONTEXT_OPAQUE_REF_BYTES)
        _require_bounded_text(self.scope_id, "scope_id", MAX_CONTEXT_OPAQUE_REF_BYTES)
        if type(self.purpose) is not ContextPurpose:
            raise TypeError("purpose must be a ContextPurpose")
        if type(self.captured_at) is not datetime:
            raise TypeError("captured_at must be a datetime")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        for field_name, expected_type in (
            ("environment", EnvironmentContext),
            ("interaction", InteractionContext),
            ("intentions", IntentionContext),
            ("capabilities", CapabilityContext),
        ):
            if type(getattr(self, field_name)) is not expected_type:
                raise TypeError(f"{field_name} must be a {expected_type.__name__}")
        _validate_context_value(self.social, SocialContextView, "social")
        _validate_context_value(self.temporal, TemporalContext, "temporal")
        source_refs = tuple(self.source_refs)
        if len(source_refs) > MAX_CONTEXT_SOURCE_REFS:
            raise ValueError("context source reference bound exceeded")
        if not all(type(item) is ContextSourceRef for item in source_refs):
            raise TypeError("source_refs must contain only ContextSourceRef values")
        if len({item.reference for item in source_refs}) != len(source_refs):
            raise ValueError("context source references must be unique")
        if not all(item.trusted for item in source_refs):
            raise ValueError("untrusted context provenance cannot enter a ContextFrame")
        object.__setattr__(self, "captured_at", self.captured_at.astimezone(UTC))
        object.__setattr__(self, "source_refs", source_refs)


@dataclass(frozen=True, slots=True)
class ContextProjection:
    """Deterministic model-facing blocks derived from one context frame."""

    purpose: ContextPurpose
    blocks: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.purpose) is not ContextPurpose:
            raise TypeError("purpose must be a ContextPurpose")
        blocks = tuple(self.blocks)
        if not blocks or len(blocks) > MAX_CONTEXT_PROJECTION_BLOCKS:
            raise ValueError("context projection block count is outside its bound")
        if not all(type(block) is str and block.strip() for block in blocks):
            raise ValueError("context projection blocks must be non-empty text")
        if len(set(blocks)) != len(blocks):
            raise ValueError("context projection blocks must be unique")
        rendered = "\n\n".join(blocks)
        if len(rendered.encode("utf-8")) > MAX_CONTEXT_PROJECTION_BYTES:
            raise ValueError("context projection exceeds its byte bound")
        object.__setattr__(self, "blocks", blocks)

    @property
    def rendered(self) -> str:
        return "\n\n".join(self.blocks)


def compile_context_projection(frame: ContextFrame) -> ContextProjection:
    """Compile only the fields relevant to ``frame.purpose``.

    Frame IDs, scope IDs, capture timestamps, source references, and unrelated
    domains never appear in the rendered blocks. Temporal time appears only in
    a ``TEMPORAL_WAKE`` projection.
    """

    if type(frame) is not ContextFrame:
        raise TypeError("frame must be a ContextFrame")
    blocks = [f"[Context purpose]\npurpose: {frame.purpose.value}"]
    if frame.purpose in {
        ContextPurpose.USER_RESPONSE,
        ContextPurpose.AMBIENT_COGNITION,
        ContextPurpose.TEMPORAL_WAKE,
    }:
        blocks.append(_render_environment(frame.environment))
    if frame.purpose is ContextPurpose.USER_RESPONSE:
        blocks.append(_render_interaction(frame.interaction, include_other_surface=False))
        blocks.append(_render_intentions(frame.intentions))
        blocks.append(_render_capabilities(frame.capabilities))
    elif frame.purpose is ContextPurpose.AMBIENT_COGNITION:
        blocks.append(_render_interaction(frame.interaction, include_other_surface=True))
        blocks.append(_render_intentions(frame.intentions))
        blocks.append(_render_social(frame.social))
        blocks.append(_render_capabilities(frame.capabilities))
    elif frame.purpose is ContextPurpose.INTERNAL_APPRAISAL:
        blocks.append(
            _render_interaction(
                frame.interaction,
                include_other_surface=False,
                include_participants=False,
            )
        )
        blocks.append(_render_intentions(frame.intentions))
    else:
        blocks.append(
            _render_interaction(
                frame.interaction,
                include_other_surface=False,
            )
        )
        blocks.append(_render_intentions(frame.intentions))
        blocks.append(_render_temporal(frame.temporal))
        blocks.append(_render_capabilities(frame.capabilities))
    return ContextProjection(frame.purpose, tuple(blocks))


def _render_environment(environment: EnvironmentContext) -> str:
    if environment.availability is ContextAvailability.KNOWN:
        assert environment.kind is not None
        return "[Environment]\n" + "\n".join(
            (
                f"kind: {environment.kind.value}",
                _render_context_value("surface", environment.surface, lambda value: value.value),
            )
        )
    return f"[Environment]\n{_status_text(environment.availability, environment.reason)}"


def _render_interaction(
    interaction: InteractionContext,
    *,
    include_other_surface: bool,
    include_participants: bool = True,
) -> str:
    lines = [
        _render_context_value("activity", interaction.activity, lambda value: value.value),
    ]
    if include_participants:
        lines.append(
            _render_context_value(
                "participants",
                interaction.participants,
                lambda values: ", ".join(item.role.value for item in values),
            )
        )
    if include_other_surface:
        lines.append(
            _render_context_value(
                "other_surface_activity",
                interaction.other_surface_activity,
                lambda value: value.value,
            )
        )
    return "[Interaction]\n" + "\n".join(lines)


def _render_intentions(intentions: IntentionContext) -> str:
    if intentions.availability is not ContextAvailability.KNOWN:
        return f"[Relevant intentions]\n{_status_text(intentions.availability, intentions.reason)}"
    lines = ["[Relevant intentions]"]
    for item in intentions.intentions:
        projection = item.projection or "reference available"
        lines.append(f"- {item.status.value}: {projection}")
    return "\n".join(lines)


def _render_capabilities(capabilities: CapabilityContext) -> str:
    if capabilities.availability is not ContextAvailability.KNOWN:
        return f"[Capabilities]\n{_status_text(capabilities.availability, capabilities.reason)}"
    lines = ["[Capabilities]"]
    for item in capabilities.capabilities:
        if item.availability is ContextAvailability.KNOWN:
            status = "available"
        else:
            status = _status_text(item.availability, item.reason)
        lines.append(f"- {item.capability.value}: {status} ({item.provenance.value})")
    return "\n".join(lines)


def _render_social(social: ContextValue[SocialContextView]) -> str:
    if social.availability is not ContextAvailability.KNOWN:
        return "[Social context]\nadvisory only; does not authorize an effect\n" + _status_text(
            social.availability, social.reason
        )
    assert social.value is not None
    value = social.value
    return "[Social context]\n" + "\n".join(
        (
            "advisory only; does not authorize an effect",
            _render_context_value("activity", value.activity, lambda item: item.value),
            _render_context_value("floor", value.floor, lambda item: item.value),
            _render_context_value(
                "speaking_surface", value.speaking_surface, lambda item: item.value
            ),
        )
    )


def _render_temporal(temporal: ContextValue[TemporalContext]) -> str:
    if temporal.availability is not ContextAvailability.KNOWN:
        return f"[Temporal context]\n{_status_text(temporal.availability, temporal.reason)}"
    assert temporal.value is not None
    lines = ["[Temporal context]", "semantics: reconsider the situation now"]
    if temporal.value.now is not None:
        lines.append(f"now: {temporal.value.now.astimezone(UTC).isoformat()}")
    if temporal.value.reconsideration_reason is not None:
        lines.append(f"reason: {temporal.value.reconsideration_reason}")
    return "\n".join(lines)


def _render_context_value[T](
    label: str,
    value: ContextValue[T],
    formatter: Callable[[T], str],
) -> str:
    if value.availability is ContextAvailability.KNOWN:
        assert value.value is not None
        return f"{label}: {formatter(value.value)}"
    return f"{label}: {_status_text(value.availability, value.reason)}"


def _status_text(availability: ContextAvailability, reason: str | None) -> str:
    label = availability.value.replace("_", " ")
    return f"{label} ({reason})" if reason else label


def _validate_context_value[T](
    value: ContextValue[T],
    expected_type: type[object],
    name: str,
    *,
    collection: bool = False,
) -> None:
    if type(value) is not ContextValue:
        raise TypeError(f"{name} must be a ContextValue")
    if value.value is None:
        return
    if collection:
        contents = value.value
        if type(contents) is not tuple:
            raise TypeError(f"{name} must contain a tuple")
        items = cast(tuple[object, ...], contents)
        if not all(type(item) is expected_type for item in items):
            raise TypeError(f"{name} contains an invalid value")
    elif type(value.value) is not expected_type:
        raise TypeError(f"{name} contains an invalid value")


def _validate_collection_status(
    availability: ContextAvailability,
    values: tuple[object, ...],
    reason: str | None,
    name: str,
    maximum: int,
) -> None:
    if type(availability) is not ContextAvailability:
        raise TypeError(f"{name} availability must be a ContextAvailability")
    if type(values) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    if len(values) > maximum:
        raise ValueError(f"{name} bound exceeded")
    if availability is ContextAvailability.KNOWN and not values:
        raise ValueError(f"empty {name} must use KNOWN_EMPTY")
    if availability is ContextAvailability.KNOWN_EMPTY and values:
        raise ValueError(f"KNOWN_EMPTY {name} cannot contain values")
    if availability in {ContextAvailability.UNKNOWN, ContextAvailability.UNAVAILABLE}:
        if values:
            raise ValueError(f"{availability.value} {name} cannot contain values")
        _require_bounded_text(reason or "", f"{name} reason", MAX_CONTEXT_REASON_BYTES)
    elif reason is not None:
        raise ValueError(f"known {name} cannot carry a reason")


def _require_bounded_text(value: str, name: str, max_bytes: int) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} exceeds its bound")


__all__ = [
    "ActivityKind",
    "CapabilityContext",
    "CapabilityId",
    "CapabilityProjection",
    "ContextAvailability",
    "ContextFrame",
    "ContextProjection",
    "ContextProvenance",
    "ContextPurpose",
    "ContextSourceRef",
    "ContextValue",
    "EnvironmentContext",
    "EnvironmentKind",
    "IntentionContext",
    "IntentionRef",
    "InteractionContext",
    "MAX_CONTEXT_CAPABILITIES",
    "MAX_CONTEXT_INTENTIONS",
    "MAX_CONTEXT_LABEL_BYTES",
    "MAX_CONTEXT_OPAQUE_REF_BYTES",
    "MAX_CONTEXT_PARTICIPANTS",
    "MAX_CONTEXT_PROJECTION_BLOCKS",
    "MAX_CONTEXT_PROJECTION_BYTES",
    "MAX_CONTEXT_REASON_BYTES",
    "MAX_CONTEXT_SOURCE_REFS",
    "ParticipantRef",
    "ParticipantRole",
    "SocialContextView",
    "SurfaceKind",
    "TemporalContext",
    "compile_context_projection",
]
