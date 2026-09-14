"""Runtime-owned deterministic assembly of ``ContextFrame`` values.

The builder is deliberately a read-only projection seam.  It accepts small,
typed owner resolvers, selects only the domains required by one
``ContextPurpose``, and copies their values into the immutable CTX-V1-A
contracts.  It never owns MindState, observations, social permission,
conversation history, memory, a scheduler, a model, or an effect.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol

from .awareness import (
    AwarenessNote,
    AwarenessNoteStatus,
    AwarenessScope,
    PeripheralAwarenessBuffer,
)
from .awareness_sources import AwarenessSourceResolver
from .context import (
    MAX_AWARENESS_CONTEXT_NOTES,
    MAX_AWARENESS_SOURCE_ITEMS_PER_NOTE,
    MAX_AWARENESS_SOURCE_TEXT_CHARS,
    MAX_AWARENESS_TOTAL_SOURCE_TEXT_CHARS,
    MAX_CONTEXT_CAPABILITIES,
    MAX_CONTEXT_INTENTIONS,
    MAX_CONTEXT_LABEL_BYTES,
    MAX_CONTEXT_OPAQUE_REF_BYTES,
    MAX_CONTEXT_SOURCE_REFS,
    ActivityKind,
    AwarenessContext,
    AwarenessContextNote,
    AwarenessSourceMaterial,
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
    IntentionContext,
    IntentionRef,
    InteractionContext,
    ParticipantRef,
    SocialContextView,
    TemporalContext,
)
from .intervention import (
    ActivityState,
    FloorState,
    SocialPermissionContext,
)
from .mind import MAX_INTENTIONS, IntentionStatus, MindIntention, MindState
from .temporal import TemporalCoordinator

MAX_CONTEXT_BUILD_INTENTION_IDS = MAX_INTENTIONS
MAX_CONTEXT_BUILD_CAPABILITY_INPUTS = MAX_CONTEXT_CAPABILITIES * 2


class ContextBuilderError(RuntimeError):
    """A context frame could not be assembled from the typed owner seams."""


class ContextProviderError(ContextBuilderError):
    """A resolver returned a malformed or untrusted typed value."""


class ContextClock(Protocol):
    """A runtime-owned timezone-aware clock seam."""

    def now(self) -> datetime: ...


type ContextClockSource = Callable[[], datetime] | ContextClock


@dataclass(frozen=True, slots=True)
class ContextBuildRequest:
    """Typed trigger/scope metadata supplied to one frame build.

    ``source_refs`` are already trusted runtime references.  Raw event payload
    text and raw adapter dictionaries cannot be supplied through this object.
    ``relevant_intention_ids`` is an explicit runtime-owned relation; the
    builder never ranks intentions from their prose.
    """

    scope_id: str
    frame_id: str | None = None
    relevant_intention_ids: tuple[str, ...] = ()
    wake_intent_ref: str | None = None
    source_refs: tuple[ContextSourceRef, ...] = ()
    temporal_continuation: bool = False
    awareness_scope: AwarenessScope | None = None

    def __post_init__(self) -> None:
        _require_text(self.scope_id, "scope_id", MAX_CONTEXT_OPAQUE_REF_BYTES)
        if self.frame_id is not None:
            _require_text(self.frame_id, "frame_id", MAX_CONTEXT_OPAQUE_REF_BYTES)
        if type(self.relevant_intention_ids) is not tuple:
            raise TypeError("relevant_intention_ids must be a tuple")
        if len(self.relevant_intention_ids) > MAX_CONTEXT_BUILD_INTENTION_IDS:
            raise ValueError("relevant intention reference bound exceeded")
        if len(set(self.relevant_intention_ids)) != len(self.relevant_intention_ids):
            raise ValueError("relevant intention references must be unique")
        for intention_id in self.relevant_intention_ids:
            _require_text(
                intention_id,
                "relevant intention reference",
                MAX_CONTEXT_OPAQUE_REF_BYTES,
            )
        if self.wake_intent_ref is not None:
            _require_text(self.wake_intent_ref, "wake_intent_ref", MAX_CONTEXT_OPAQUE_REF_BYTES)
        if type(self.source_refs) is not tuple:
            raise TypeError("source_refs must be a tuple")
        if len(self.source_refs) > MAX_CONTEXT_SOURCE_REFS:
            raise ValueError("source reference bound exceeded")
        for source_ref in self.source_refs:
            if type(source_ref) is not ContextSourceRef:
                raise TypeError("source_refs must contain ContextSourceRef values")
            if not source_ref.trusted:
                raise ValueError("untrusted source references cannot enter a build request")
        if type(self.temporal_continuation) is not bool:
            raise TypeError("temporal_continuation must be a bool")
        if self.awareness_scope is not None and type(self.awareness_scope) is not AwarenessScope:
            raise TypeError("awareness_scope must be an AwarenessScope")


class EnvironmentResolver(Protocol):
    """Resolve trusted provider-neutral environment/surface state."""

    def resolve(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> EnvironmentContext: ...


class InteractionResolver(Protocol):
    """Resolve trusted current activity and participant state."""

    def resolve(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> InteractionContext: ...


@dataclass(frozen=True, slots=True)
class IntentionResolution:
    """Typed read-only result from a MindState relevance resolver."""

    availability: ContextAvailability
    intentions: tuple[MindIntention, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        _validate_resolution_status(
            self.availability,
            self.intentions,
            self.reason,
            "intentions",
            MAX_INTENTIONS,
        )
        if not all(type(item) is MindIntention for item in self.intentions):
            raise TypeError("intention resolution must contain MindIntention values")


class IntentionResolver(Protocol):
    """Resolve only explicitly scope-linked/current MindState intentions."""

    def resolve(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> IntentionResolution: ...


@dataclass(frozen=True, slots=True)
class ValidatedAdapterCapability:
    """One adapter capability already validated by runtime composition."""

    projection: CapabilityProjection
    scope_id: str
    contributor_ref: str

    def __post_init__(self) -> None:
        if type(self.projection) is not CapabilityProjection:
            raise TypeError("adapter capability projection must be a CapabilityProjection")
        if self.projection.provenance is not ContextProvenance.VALIDATED_ADAPTER:
            raise ValueError("adapter capabilities require VALIDATED_ADAPTER provenance")
        _require_text(self.scope_id, "adapter capability scope_id", MAX_CONTEXT_OPAQUE_REF_BYTES)
        _require_text(
            self.contributor_ref,
            "adapter capability contributor_ref",
            MAX_CONTEXT_OPAQUE_REF_BYTES,
        )


@dataclass(frozen=True, slots=True)
class CapabilityResolution:
    """Typed runtime declarations plus validated adapter contributions."""

    availability: ContextAvailability
    runtime_declared: tuple[CapabilityProjection, ...] = ()
    validated_adapter: tuple[ValidatedAdapterCapability, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.availability) is not ContextAvailability:
            raise TypeError("capability resolution availability must be a ContextAvailability")
        if type(self.runtime_declared) is not tuple:
            raise TypeError("runtime_declared capabilities must be a tuple")
        if type(self.validated_adapter) is not tuple:
            raise TypeError("validated_adapter capabilities must be a tuple")
        if (
            len(self.runtime_declared) + len(self.validated_adapter)
            > MAX_CONTEXT_BUILD_CAPABILITY_INPUTS
        ):
            raise ValueError("capability resolution input bound exceeded")
        if not all(type(item) is CapabilityProjection for item in self.runtime_declared):
            raise TypeError("runtime_declared must contain CapabilityProjection values")
        if not all(type(item) is ValidatedAdapterCapability for item in self.validated_adapter):
            raise TypeError("validated_adapter must contain ValidatedAdapterCapability values")
        if not all(
            item.provenance is ContextProvenance.RUNTIME_DECLARED for item in self.runtime_declared
        ):
            raise ValueError("runtime declarations require RUNTIME_DECLARED provenance")
        if self.availability in {ContextAvailability.UNKNOWN, ContextAvailability.UNAVAILABLE}:
            if self.runtime_declared or self.validated_adapter:
                raise ValueError("unknown/unavailable capabilities cannot carry items")
            _require_reason(self.reason, "capabilities")
        elif self.availability is ContextAvailability.KNOWN_EMPTY:
            if self.runtime_declared or self.validated_adapter:
                raise ValueError("KNOWN_EMPTY capabilities cannot carry items")
            if self.reason is not None:
                raise ValueError("KNOWN_EMPTY capabilities cannot carry a reason")
        elif self.reason is not None:
            raise ValueError("known capabilities cannot carry a reason")
        if self.availability is ContextAvailability.KNOWN and not (
            self.runtime_declared or self.validated_adapter
        ):
            raise ValueError("known capabilities require at least one contribution")


class CapabilityResolver(Protocol):
    """Resolve only runtime-declared or runtime-validated capability facts."""

    def resolve(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> CapabilityResolution: ...


@dataclass(frozen=True, slots=True)
class SocialResolution:
    """Typed advisory E1/E2 current-state result."""

    availability: ContextAvailability
    state: SocialPermissionContext | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.availability) is not ContextAvailability:
            raise TypeError("social resolution availability must be a ContextAvailability")
        if self.availability is ContextAvailability.KNOWN:
            if type(self.state) is not SocialPermissionContext:
                raise TypeError("known social resolution requires SocialPermissionContext")
            if self.reason is not None:
                raise ValueError("known social resolution cannot carry a reason")
        elif self.state is not None:
            raise ValueError("unknown/unavailable social resolution cannot carry state")
        elif self.availability is ContextAvailability.KNOWN_EMPTY:
            raise ValueError("social state cannot be KNOWN_EMPTY")
        else:
            _require_reason(self.reason, "social")


class SocialResolver(Protocol):
    """Resolve advisory social state; it is never an effect permit."""

    def resolve(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> SocialResolution: ...


@dataclass(frozen=True, slots=True)
class TemporalResolution:
    """Typed wake/intention relation without historical world-state replay."""

    availability: ContextAvailability
    context: TemporalContext | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.availability) is not ContextAvailability:
            raise TypeError("temporal resolution availability must be a ContextAvailability")
        if self.availability is ContextAvailability.KNOWN:
            if type(self.context) is not TemporalContext:
                raise TypeError("known temporal resolution requires TemporalContext")
            if self.reason is not None:
                raise ValueError("known temporal resolution cannot carry a reason")
        elif self.context is not None:
            raise ValueError("unknown/unavailable temporal resolution cannot carry context")
        elif self.availability is ContextAvailability.KNOWN_EMPTY:
            raise ValueError("temporal state cannot be KNOWN_EMPTY")
        else:
            _require_reason(self.reason, "temporal")


class TemporalResolver(Protocol):
    """Resolve the current wake relation from runtime-owned temporal state."""

    def resolve(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> TemporalResolution: ...


class SourceRefResolver(Protocol):
    """Resolve bounded trusted provenance references, never arbitrary payloads."""

    def resolve(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> tuple[ContextSourceRef, ...]: ...


class AwarenessContextResolver(Protocol):
    """Resolve one exact runtime-owned awareness scope read-only."""

    def resolve(self, scope: AwarenessScope) -> AwarenessContext: ...


class _UnknownEnvironmentResolver:
    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> EnvironmentContext:
        del purpose, request
        return _unknown_environment("environment_not_established")


class _UnknownInteractionResolver:
    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> InteractionContext:
        del purpose, request
        return _unknown_interaction("interaction_not_established")


class _UnknownIntentionResolver:
    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> IntentionResolution:
        del purpose, request
        return IntentionResolution(
            ContextAvailability.UNKNOWN,
            reason="intentions_not_established",
        )


class _UnknownCapabilityResolver:
    def resolve(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> CapabilityResolution:
        del purpose, request
        return CapabilityResolution(
            ContextAvailability.UNKNOWN,
            reason="capabilities_not_established",
        )


class _UnknownSocialResolver:
    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> SocialResolution:
        del purpose, request
        return SocialResolution(ContextAvailability.UNKNOWN, reason="social_state_not_established")


class _UnknownTemporalResolver:
    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> TemporalResolution:
        del purpose, request
        return TemporalResolution(
            ContextAvailability.UNKNOWN,
            reason="wake_relation_not_established",
        )


class PeripheralAwarenessContextResolver:
    """Project active buffer metadata into the bounded context domain."""

    def __init__(
        self,
        buffer: PeripheralAwarenessBuffer,
        *,
        source_resolver: AwarenessSourceResolver | None = None,
    ) -> None:
        if type(buffer) is not PeripheralAwarenessBuffer:
            raise TypeError("buffer must be a PeripheralAwarenessBuffer")
        if source_resolver is not None and not callable(getattr(source_resolver, "resolve", None)):
            raise TypeError("source_resolver must provide resolve")
        self._buffer = buffer
        self._source_resolver = source_resolver

    @property
    def buffer(self) -> PeripheralAwarenessBuffer:
        """Return the existing runtime-owned buffer dependency."""

        return self._buffer

    @property
    def source_resolver(self) -> AwarenessSourceResolver | None:
        """Return the optional explicit-reference source resolver."""

        return self._source_resolver

    def resolve(self, scope: AwarenessScope) -> AwarenessContext:
        """Read exactly ``scope`` without changing awareness lifecycle state."""

        if type(scope) is not AwarenessScope:
            raise TypeError("scope must be an AwarenessScope")
        try:
            active = self._buffer.snapshot_active(scope)
        except Exception:
            return AwarenessContext(
                ContextAvailability.UNAVAILABLE,
                reason="awareness_buffer_unavailable",
            )
        if type(active) is not tuple:
            raise ContextProviderError("awareness buffer returned a non-tuple snapshot")
        if not active:
            return AwarenessContext(ContextAvailability.KNOWN_EMPTY)
        notes: list[AwarenessContextNote] = []
        remaining_chars = MAX_AWARENESS_TOTAL_SOURCE_TEXT_CHARS
        for note in active[:MAX_AWARENESS_CONTEXT_NOTES]:
            material, consumed_chars = _resolve_awareness_source_material(
                note,
                self._source_resolver,
                remaining_chars,
            )
            notes.append(_project_awareness_note(note, source_material=material))
            remaining_chars -= consumed_chars
        return AwarenessContext(ContextAvailability.KNOWN, tuple(notes))


def _resolve_awareness_source_material(
    note: AwarenessNote,
    resolver: AwarenessSourceResolver | None,
    remaining_chars: int,
) -> tuple[tuple[AwarenessSourceMaterial, ...], int]:
    if resolver is None or remaining_chars <= 0:
        return (), 0
    resolved: list[AwarenessSourceMaterial] = []
    consumed_chars = 0
    for source_ref in note.source_refs[:MAX_AWARENESS_SOURCE_ITEMS_PER_NOTE]:
        try:
            material = resolver.resolve(source_ref)
            if material is None:
                continue
            if type(material) is not AwarenessSourceMaterial:
                return (), 0
            if material.source_ref != source_ref:
                return (), 0
            text = material.text[:MAX_AWARENESS_SOURCE_TEXT_CHARS]
        except Exception:
            return (), 0
        available_chars = remaining_chars - consumed_chars
        if available_chars <= 0:
            break
        text = text[:available_chars]
        if not text:
            continue
        resolved.append(
            AwarenessSourceMaterial(
                source_ref=material.source_ref,
                source_kind=material.source_kind,
                text=text,
            )
        )
        consumed_chars += len(text)
    return tuple(resolved), consumed_chars


def _project_awareness_note(
    note: AwarenessNote,
    *,
    source_material: tuple[AwarenessSourceMaterial, ...] = (),
) -> AwarenessContextNote:
    if type(note) is not AwarenessNote:
        raise ContextProviderError("awareness buffer returned an invalid note")
    if note.status is not AwarenessNoteStatus.ACTIVE:
        raise ContextProviderError("awareness buffer returned a non-active note")
    return AwarenessContextNote(
        note_id=note.note_id,
        source_refs=note.source_refs,
        reason_codes=note.reason_codes,
        occurrence_count=note.occurrence_count,
        first_seen_at=note.admitted_at,
        last_seen_at=note.last_seen_at or note.admitted_at,
        source_material=source_material,
    )


class MindStateIntentionResolver:
    """Read-only relevance adapter over the runtime-owned ``MindState``.

    MindState does not carry a surface/scope field on each intention.  The
    resolver therefore exposes an intention only when the caller supplies an
    explicit runtime-owned relation in ``relevant_intention_ids``.  An empty
    MindState is known-empty; an active but unlinked MindState is unknown.
    """

    def __init__(self, mind_state: MindState) -> None:
        if type(mind_state) is not MindState:
            raise TypeError("mind_state must be a MindState")
        self._mind_state = mind_state

    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> IntentionResolution:
        del purpose
        snapshot = self._mind_state.snapshot()
        active = tuple(
            item for item in snapshot.intentions if item.status is IntentionStatus.ACTIVE
        )
        if not active:
            return IntentionResolution(ContextAvailability.KNOWN_EMPTY)
        if not request.relevant_intention_ids:
            return IntentionResolution(
                ContextAvailability.UNKNOWN,
                reason="intention_scope_not_established",
            )
        wanted = set(request.relevant_intention_ids)
        selected = tuple(item for item in active if item.intention_id in wanted)
        if not selected:
            return IntentionResolution(ContextAvailability.KNOWN_EMPTY)
        return IntentionResolution(ContextAvailability.KNOWN, selected)


class TemporalCoordinatorResolver:
    """Read-only adapter for current temporal wake/intention provenance."""

    def __init__(self, coordinator: TemporalCoordinator) -> None:
        if type(coordinator) is not TemporalCoordinator:
            raise TypeError("coordinator must be a TemporalCoordinator")
        self._coordinator = coordinator

    def resolve(self, purpose: ContextPurpose, request: ContextBuildRequest) -> TemporalResolution:
        del purpose
        if request.wake_intent_ref is None:
            return TemporalResolution(
                ContextAvailability.UNKNOWN,
                reason="wake_intent_not_established",
            )
        intent = self._coordinator.intent(request.wake_intent_ref)
        if intent is None:
            return TemporalResolution(
                ContextAvailability.UNKNOWN,
                reason="wake_intent_not_found",
            )
        if intent.scope_id != request.scope_id:
            return TemporalResolution(
                ContextAvailability.UNKNOWN,
                reason="wake_scope_mismatch",
            )
        return TemporalResolution(
            ContextAvailability.KNOWN,
            TemporalContext(
                wake_intent_ref=intent.wake_intent_id,
                reconsideration_reason=_clip_utf8(intent.reason, 128),
            ),
        )


class DeclaredCapabilityResolver:
    """Small immutable capability seam for runtime composition and tests.

    Runtime declarations and adapter contributions are supplied as typed
    tuples.  This is intentionally not a plugin registry and it never derives
    capabilities from installed tools, event payloads, or prompt wording.
    """

    def __init__(
        self,
        *,
        runtime_declared: tuple[CapabilityProjection, ...] = (),
        validated_adapter: tuple[ValidatedAdapterCapability, ...] = (),
    ) -> None:
        self._resolution = CapabilityResolution(
            ContextAvailability.KNOWN_EMPTY
            if not runtime_declared and not validated_adapter
            else ContextAvailability.KNOWN,
            runtime_declared=runtime_declared,
            validated_adapter=validated_adapter,
        )

    def resolve(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> CapabilityResolution:
        del purpose, request
        return self._resolution


class ContextFrameBuilder:
    """Build one immutable, purpose-specific context snapshot.

    Resolver invocation is synchronous and ordered.  Provider exceptions are
    fail-closed to an unknown domain; malformed typed values are rejected with
    ``ContextProviderError``.  All output collections are copied into tuples,
    and no resolver result is retained after ``build`` returns.
    """

    def __init__(
        self,
        *,
        clock: ContextClockSource | None = None,
        environment_resolver: EnvironmentResolver | None = None,
        interaction_resolver: InteractionResolver | None = None,
        intention_resolver: IntentionResolver | None = None,
        capability_resolver: CapabilityResolver | None = None,
        social_resolver: SocialResolver | None = None,
        temporal_resolver: TemporalResolver | None = None,
        source_ref_resolver: SourceRefResolver | None = None,
        awareness_resolver: AwarenessContextResolver | None = None,
    ) -> None:
        self._clock = clock or _utc_now
        self._environment = environment_resolver or _UnknownEnvironmentResolver()
        self._interaction = interaction_resolver or _UnknownInteractionResolver()
        self._intentions = intention_resolver or _UnknownIntentionResolver()
        self._capabilities = capability_resolver or _UnknownCapabilityResolver()
        self._social = social_resolver or _UnknownSocialResolver()
        self._temporal = temporal_resolver or _UnknownTemporalResolver()
        self._source_refs = source_ref_resolver
        self._awareness = awareness_resolver
        for name, resolver in (
            ("environment_resolver", self._environment),
            ("interaction_resolver", self._interaction),
            ("intention_resolver", self._intentions),
            ("capability_resolver", self._capabilities),
            ("social_resolver", self._social),
            ("temporal_resolver", self._temporal),
        ):
            if not callable(getattr(resolver, "resolve", None)):
                raise TypeError(f"{name} must provide resolve")
        if self._source_refs is not None and not callable(
            getattr(self._source_refs, "resolve", None)
        ):
            raise TypeError("source_ref_resolver must provide resolve")
        if self._awareness is not None and not callable(getattr(self._awareness, "resolve", None)):
            raise TypeError("awareness_resolver must provide resolve")

    def build(self, purpose: ContextPurpose, request: ContextBuildRequest) -> ContextFrame:
        """Build a bounded frame without mutating any source owner."""

        if type(purpose) is not ContextPurpose:
            raise TypeError("purpose must be a ContextPurpose")
        if type(request) is not ContextBuildRequest:
            raise TypeError("request must be a ContextBuildRequest")
        if request.temporal_continuation and purpose is not ContextPurpose.USER_RESPONSE:
            raise ValueError("temporal_continuation is only valid for USER_RESPONSE")

        captured_at = _read_clock(self._clock)
        environment = _unknown_environment("environment_not_selected")
        interaction = _unknown_interaction("interaction_not_selected")
        intentions = IntentionContext(
            ContextAvailability.UNKNOWN,
            reason="intentions_not_selected",
        )
        capabilities = CapabilityContext(
            ContextAvailability.UNKNOWN,
            reason="capabilities_not_selected",
        )
        social: ContextValue[SocialContextView] = ContextValue(
            ContextAvailability.UNKNOWN,
            reason="social_state_not_selected",
        )
        temporal: ContextValue[TemporalContext] = ContextValue(
            ContextAvailability.UNKNOWN,
            reason="temporal_state_not_selected",
        )
        awareness = AwarenessContext(
            ContextAvailability.UNKNOWN,
            reason="awareness_not_selected",
        )

        if purpose in {
            ContextPurpose.USER_RESPONSE,
            ContextPurpose.AMBIENT_COGNITION,
            ContextPurpose.TEMPORAL_WAKE,
        }:
            environment = self._resolve_environment(purpose, request)

        if purpose in {
            ContextPurpose.USER_RESPONSE,
            ContextPurpose.AMBIENT_COGNITION,
            ContextPurpose.INTERNAL_APPRAISAL,
            ContextPurpose.TEMPORAL_WAKE,
        }:
            interaction = self._select_interaction(
                purpose,
                self._resolve_interaction(purpose, request),
            )

        intentions = self._resolve_intentions(purpose, request)

        if purpose in {
            ContextPurpose.USER_RESPONSE,
            ContextPurpose.AMBIENT_COGNITION,
            ContextPurpose.TEMPORAL_WAKE,
        }:
            capabilities = self._resolve_capabilities(purpose, request)

        if purpose is ContextPurpose.AMBIENT_COGNITION:
            social = self._resolve_social(purpose, request)

        if purpose is ContextPurpose.USER_RESPONSE:
            awareness = self._resolve_awareness(request)

        if purpose is ContextPurpose.TEMPORAL_WAKE or (
            purpose is ContextPurpose.USER_RESPONSE and request.temporal_continuation
        ):
            temporal = self._resolve_temporal(purpose, request, captured_at)

        source_refs = self._resolve_source_refs(purpose, request)
        frame_id = request.frame_id or _derive_frame_id(purpose, request, captured_at)
        return ContextFrame(
            frame_id=frame_id,
            purpose=purpose,
            scope_id=request.scope_id,
            captured_at=captured_at,
            environment=environment,
            interaction=interaction,
            intentions=intentions,
            capabilities=capabilities,
            social=social,
            temporal=temporal,
            source_refs=source_refs,
            awareness=awareness,
        )

    def bind_awareness_resolver(self, resolver: AwarenessContextResolver) -> None:
        """Bind the runtime-owned awareness resolver before request execution."""

        if not callable(getattr(resolver, "resolve", None)):
            raise TypeError("awareness_resolver must provide resolve")
        if (
            type(self._awareness) is PeripheralAwarenessContextResolver
            and type(resolver) is PeripheralAwarenessContextResolver
            and self._awareness.buffer is resolver.buffer
        ):
            self._awareness = resolver
            return
        if self._awareness is not None and self._awareness is not resolver:
            raise ValueError("context builder is already bound to another awareness resolver")
        self._awareness = resolver

    def bind_awareness_source_resolver(self, resolver: AwarenessSourceResolver) -> None:
        """Attach an explicit-reference source resolver to the bound buffer view."""

        if not callable(getattr(resolver, "resolve", None)):
            raise TypeError("source_resolver must provide resolve")
        if type(self._awareness) is not PeripheralAwarenessContextResolver:
            raise ValueError("context builder has no bound peripheral awareness resolver")
        self._awareness = PeripheralAwarenessContextResolver(
            self._awareness.buffer,
            source_resolver=resolver,
        )

    def _resolve_environment(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> EnvironmentContext:
        try:
            value = self._environment.resolve(purpose, request)
        except Exception:
            return _unknown_environment("environment_resolver_failed")
        if type(value) is not EnvironmentContext:
            raise ContextProviderError("environment resolver returned an invalid value")
        return value

    def _resolve_interaction(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> InteractionContext:
        try:
            value = self._interaction.resolve(purpose, request)
        except Exception:
            return _unknown_interaction("interaction_resolver_failed")
        if type(value) is not InteractionContext:
            raise ContextProviderError("interaction resolver returned an invalid value")
        return value

    def _resolve_intentions(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> IntentionContext:
        try:
            resolution = self._intentions.resolve(purpose, request)
        except Exception:
            resolution = IntentionResolution(
                ContextAvailability.UNKNOWN,
                reason="intentions_resolver_failed",
            )
        if type(resolution) is not IntentionResolution:
            raise ContextProviderError("intention resolver returned an invalid value")
        if resolution.availability is not ContextAvailability.KNOWN:
            return IntentionContext(resolution.availability, reason=resolution.reason)
        selected = resolution.intentions[:MAX_CONTEXT_INTENTIONS]
        refs = tuple(
            IntentionRef(
                intention_id=item.intention_id,
                status=item.status,
                projection=_clip_utf8(item.text, MAX_CONTEXT_LABEL_BYTES),
            )
            for item in selected
        )
        if not refs:
            return IntentionContext(ContextAvailability.KNOWN_EMPTY)
        return IntentionContext(ContextAvailability.KNOWN, refs)

    def _resolve_capabilities(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> CapabilityContext:
        try:
            resolution = self._capabilities.resolve(purpose, request)
        except Exception:
            resolution = CapabilityResolution(
                ContextAvailability.UNKNOWN,
                reason="capabilities_resolver_failed",
            )
        if type(resolution) is not CapabilityResolution:
            raise ContextProviderError("capability resolver returned an invalid value")
        if resolution.availability is not ContextAvailability.KNOWN:
            return CapabilityContext(resolution.availability, reason=resolution.reason)

        runtime = _merge_capability_group(
            resolution.runtime_declared,
            request.scope_id,
            ContextProvenance.RUNTIME_DECLARED,
        )
        adapter = _merge_adapter_capability_group(resolution.validated_adapter, request.scope_id)
        merged: dict[CapabilityId, CapabilityProjection] = {}
        for capability, projection in runtime.items():
            merged[capability] = projection
        for capability, projection in adapter.items():
            if capability not in merged:
                merged[capability] = projection
        ordered = tuple(merged[key] for key in sorted(merged, key=lambda item: item.value))
        if not ordered:
            return CapabilityContext(ContextAvailability.KNOWN_EMPTY)
        return CapabilityContext(ContextAvailability.KNOWN, ordered[:MAX_CONTEXT_CAPABILITIES])

    def _resolve_social(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> ContextValue[SocialContextView]:
        try:
            resolution = self._social.resolve(purpose, request)
        except Exception:
            resolution = SocialResolution(
                ContextAvailability.UNKNOWN,
                reason="social_resolver_failed",
            )
        if type(resolution) is not SocialResolution:
            raise ContextProviderError("social resolver returned an invalid value")
        if resolution.availability is not ContextAvailability.KNOWN:
            return ContextValue(resolution.availability, reason=resolution.reason)
        assert resolution.state is not None
        state = resolution.state
        return ContextValue(
            ContextAvailability.KNOWN,
            SocialContextView(
                _social_activity(state.activity),
                _social_floor(state.floor),
                ContextValue(ContextAvailability.KNOWN, state.speaking_surface),
            ),
        )

    def _resolve_temporal(
        self,
        purpose: ContextPurpose,
        request: ContextBuildRequest,
        captured_at: datetime,
    ) -> ContextValue[TemporalContext]:
        try:
            resolution = self._temporal.resolve(purpose, request)
        except Exception:
            resolution = TemporalResolution(
                ContextAvailability.UNKNOWN,
                reason="temporal_resolver_failed",
            )
        if type(resolution) is not TemporalResolution:
            raise ContextProviderError("temporal resolver returned an invalid value")
        if resolution.availability is ContextAvailability.KNOWN and resolution.context is not None:
            value = resolution.context
            return ContextValue(
                ContextAvailability.KNOWN,
                TemporalContext(
                    now=captured_at,
                    wake_intent_ref=value.wake_intent_ref,
                    reconsideration_reason=value.reconsideration_reason,
                ),
            )
        # The runtime clock is authoritative for NOW even when wake provenance
        # is unavailable.  Historical ``not_before`` state is never rendered.
        return ContextValue(
            ContextAvailability.KNOWN,
            TemporalContext(
                now=captured_at,
                reconsideration_reason=resolution.reason or "wake_relation_not_established",
            ),
        )

    def _resolve_awareness(self, request: ContextBuildRequest) -> AwarenessContext:
        scope = request.awareness_scope
        if scope is None:
            return AwarenessContext(
                ContextAvailability.UNKNOWN,
                reason="awareness_scope_not_established",
            )
        if self._awareness is None:
            return AwarenessContext(
                ContextAvailability.UNAVAILABLE,
                reason="awareness_resolver_not_configured",
            )
        try:
            value = self._awareness.resolve(scope)
        except Exception:
            return AwarenessContext(
                ContextAvailability.UNAVAILABLE,
                reason="awareness_resolver_failed",
            )
        if type(value) is not AwarenessContext:
            raise ContextProviderError("awareness resolver returned an invalid value")
        if (
            value.availability is ContextAvailability.KNOWN
            and len(value.notes) > MAX_AWARENESS_CONTEXT_NOTES
        ):
            raise ContextProviderError("awareness resolver exceeded the note bound")
        return value

    def _resolve_source_refs(
        self, purpose: ContextPurpose, request: ContextBuildRequest
    ) -> tuple[ContextSourceRef, ...]:
        if purpose not in {
            ContextPurpose.AMBIENT_COGNITION,
            ContextPurpose.TEMPORAL_WAKE,
        } and not (purpose is ContextPurpose.USER_RESPONSE and request.temporal_continuation):
            return ()
        supplied = request.source_refs
        if self._source_refs is None:
            return supplied
        try:
            resolved = self._source_refs.resolve(purpose, request)
        except Exception:
            resolved = ()
        if type(resolved) is not tuple:
            raise ContextProviderError("source resolver returned a non-tuple")
        if len(resolved) > MAX_CONTEXT_SOURCE_REFS:
            raise ContextProviderError("source resolver exceeded the source reference bound")
        combined = (*supplied, *resolved)
        unique: dict[str, ContextSourceRef] = {}
        for source_ref in combined:
            if type(source_ref) is not ContextSourceRef:
                raise ContextProviderError("source resolver returned an invalid source reference")
            if not source_ref.trusted:
                raise ContextProviderError("untrusted source reference rejected")
            previous = unique.get(source_ref.reference)
            if previous is not None and previous != source_ref:
                raise ContextProviderError("conflicting source reference provenance")
            unique[source_ref.reference] = source_ref
        if len(unique) > MAX_CONTEXT_SOURCE_REFS:
            raise ContextProviderError("combined source reference bound exceeded")
        return tuple(unique.values())

    @staticmethod
    def _select_interaction(
        purpose: ContextPurpose,
        interaction: InteractionContext,
    ) -> InteractionContext:
        unknown_other: ContextValue[ActivityKind] = ContextValue(
            ContextAvailability.UNKNOWN,
            reason="other_surface_not_selected",
        )
        if purpose is ContextPurpose.AMBIENT_COGNITION:
            return interaction
        if purpose is ContextPurpose.INTERNAL_APPRAISAL:
            return InteractionContext(
                activity=interaction.activity,
                participants=ContextValue(
                    ContextAvailability.UNKNOWN,
                    reason="participants_not_selected",
                ),
                other_surface_activity=unknown_other,
            )
        return InteractionContext(
            activity=interaction.activity,
            participants=interaction.participants,
            other_surface_activity=unknown_other,
        )


def _merge_capability_group(
    values: tuple[CapabilityProjection, ...],
    scope_id: str,
    expected_provenance: ContextProvenance,
) -> dict[CapabilityId, CapabilityProjection]:
    del scope_id
    merged: dict[CapabilityId, CapabilityProjection] = {}
    for value in values:
        if type(value) is not CapabilityProjection:
            raise ContextProviderError("capability contribution is not typed")
        if value.provenance is not expected_provenance:
            raise ContextProviderError("capability contribution has invalid provenance")
        previous = merged.get(value.capability)
        if previous is None:
            merged[value.capability] = value
        elif previous != value:
            merged[value.capability] = CapabilityProjection(
                value.capability,
                ContextAvailability.UNAVAILABLE,
                expected_provenance,
                reason="conflicting_runtime_capability_declaration",
            )
    return merged


def _merge_adapter_capability_group(
    values: tuple[ValidatedAdapterCapability, ...],
    scope_id: str,
) -> dict[CapabilityId, CapabilityProjection]:
    merged: dict[CapabilityId, ValidatedAdapterCapability] = {}
    for value in values:
        if type(value) is not ValidatedAdapterCapability:
            raise ContextProviderError("adapter capability contribution is not typed")
        if value.scope_id != scope_id:
            continue
        previous = merged.get(value.projection.capability)
        if previous is None:
            merged[value.projection.capability] = value
        elif previous.projection != value.projection:
            merged[value.projection.capability] = ValidatedAdapterCapability(
                CapabilityProjection(
                    value.projection.capability,
                    ContextAvailability.UNAVAILABLE,
                    ContextProvenance.VALIDATED_ADAPTER,
                    reason="conflicting_adapter_capability_contribution",
                ),
                scope_id,
                min(previous.contributor_ref, value.contributor_ref),
            )
    return {key: value.projection for key, value in merged.items()}


def _unknown_environment(reason: str) -> EnvironmentContext:
    return EnvironmentContext(ContextAvailability.UNKNOWN, reason=reason)


def _unknown_interaction(reason: str) -> InteractionContext:
    return InteractionContext(
        activity=ContextValue[ActivityKind](ContextAvailability.UNKNOWN, reason=reason),
        participants=ContextValue[tuple[ParticipantRef, ...]](
            ContextAvailability.UNKNOWN, reason=reason
        ),
        other_surface_activity=ContextValue[ActivityKind](
            ContextAvailability.UNKNOWN, reason=reason
        ),
    )


def _social_activity(value: ActivityState) -> ContextValue[ActivityState]:
    if value is ActivityState.UNKNOWN:
        return ContextValue(ContextAvailability.UNKNOWN, reason="activity_not_established")
    return ContextValue(ContextAvailability.KNOWN, value)


def _social_floor(value: FloorState) -> ContextValue[FloorState]:
    if value is FloorState.UNKNOWN:
        return ContextValue(ContextAvailability.UNKNOWN, reason="floor_not_established")
    return ContextValue(ContextAvailability.KNOWN, value)


def _derive_frame_id(
    purpose: ContextPurpose,
    request: ContextBuildRequest,
    captured_at: datetime,
) -> str:
    material = "|".join(
        (
            purpose.value,
            request.scope_id,
            captured_at.isoformat(),
            request.wake_intent_ref or "",
            *request.relevant_intention_ids,
            *(ref.reference for ref in request.source_refs),
        )
    )
    digest = sha256(material.encode("utf-8")).hexdigest()[:48]
    return f"frame:{digest}"


def _read_clock(clock: ContextClockSource) -> datetime:
    try:
        value = clock() if callable(clock) else clock.now()
    except Exception as error:
        raise ContextBuilderError("context clock is unavailable") from error
    if type(value) is not datetime:
        raise ContextBuilderError("context clock returned a non-datetime value")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContextBuilderError("context clock returned a naive datetime")
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clip_utf8(value: str, maximum: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value
    clipped = encoded[:maximum].decode("utf-8", "ignore").rstrip()
    return clipped or "reference available"


def _require_text(value: str, name: str, maximum: int) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    if len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{name} exceeds its bound")


def _require_reason(reason: str | None, name: str) -> None:
    if reason is None:
        raise ValueError(f"{name} unknown/unavailable state requires a reason")
    _require_text(reason, f"{name} reason", 128)


def _validate_resolution_status(
    availability: ContextAvailability,
    values: tuple[object, ...],
    reason: str | None,
    name: str,
    maximum: int,
) -> None:
    if type(availability) is not ContextAvailability:
        raise TypeError(f"{name} availability must be a ContextAvailability")
    if type(values) is not tuple:
        raise TypeError(f"{name} values must be a tuple")
    if len(values) > maximum:
        raise ValueError(f"{name} resolution bound exceeded")
    if availability is ContextAvailability.KNOWN and not values:
        raise ValueError(f"known {name} resolution requires values")
    if availability is ContextAvailability.KNOWN_EMPTY and values:
        raise ValueError(f"known-empty {name} resolution cannot contain values")
    if availability in {ContextAvailability.UNKNOWN, ContextAvailability.UNAVAILABLE}:
        if values:
            raise ValueError(f"{availability.value} {name} resolution cannot contain values")
        _require_reason(reason, name)
    elif reason is not None:
        raise ValueError(f"known {name} resolution cannot carry a reason")


__all__ = [
    "CapabilityResolution",
    "CapabilityResolver",
    "AwarenessContextResolver",
    "ContextBuildRequest",
    "ContextBuilderError",
    "ContextClock",
    "ContextClockSource",
    "ContextFrameBuilder",
    "ContextProviderError",
    "DeclaredCapabilityResolver",
    "EnvironmentResolver",
    "IntentionResolution",
    "IntentionResolver",
    "InteractionResolver",
    "MindStateIntentionResolver",
    "PeripheralAwarenessContextResolver",
    "SocialResolution",
    "SocialResolver",
    "SourceRefResolver",
    "TemporalCoordinatorResolver",
    "TemporalResolution",
    "TemporalResolver",
    "ValidatedAdapterCapability",
]
