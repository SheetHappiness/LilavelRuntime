"""Shared production composition for stable canon and volatile context.

This module is the only Runtime seam that turns a ``ContextFrame`` into
model-facing guidance.  It keeps canonical role/text history separate from
the purpose-specific projection, structurally omits optional projection
blocks when the trusted guidance budget is full, and records only bounded
content-free evidence.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from lilavel_core import (
    LILAVEL_OPERATING_CANON_V1,
    ContextMessage,
    ConversationContextGuidanceComposer,
    ConversationRun,
    ModelRequest,
    compile_operating_canon,
)
from lilavel_core.sidecar_protocol import (
    MAX_CONTEXT_BYTES,
    MAX_GUIDANCE_BLOCKS,
    MAX_GUIDANCE_BYTES,
)

from .awareness import (
    MAX_AWARENESS_NOTES_TOTAL,
    AwarenessScope,
    PeripheralAwarenessBuffer,
)
from .awareness_sources import AwarenessSourceResolver
from .context import (
    AwarenessContext,
    ContextAvailability,
    ContextProjection,
    ContextProvenance,
    ContextPurpose,
    ContextSourceRef,
    compile_context_projection,
)
from .context_builder import (
    ContextBuildRequest,
    ContextFrameBuilder,
    PeripheralAwarenessContextResolver,
)
from .contracts import CognitionEpisode, CognitionTriggerSource

MAX_CONTEXT_ASSEMBLY_EVIDENCE = 256
MAX_TOTAL_REQUEST_CONTEXT_BYTES = MAX_CONTEXT_BYTES + MAX_GUIDANCE_BYTES
ContextAssemblyOutcome = Literal["injected", "omitted", "failed"]


@dataclass(frozen=True, slots=True)
class ContextAssemblyEvidence:
    """Bounded, content-free evidence for one production request assembly."""

    purpose: ContextPurpose
    operating_canon_injected: bool
    context_injected: bool
    projection_block_kinds: tuple[str, ...]
    projection_bytes: int
    history_bytes: int
    request_input_bytes: int
    total_request_context_bytes: int
    omitted_block_count: int = 0
    truncation_count: int = 0
    outcome: ContextAssemblyOutcome = "omitted"
    awareness_availability: ContextAvailability = ContextAvailability.UNKNOWN
    awareness_note_count: int = 0
    awareness_block_present: bool = False
    awareness_omitted_by_budget: bool = False

    def __post_init__(self) -> None:
        if type(self.purpose) is not ContextPurpose:
            raise TypeError("purpose must be a ContextPurpose")
        if type(self.projection_block_kinds) is not tuple:
            raise TypeError("projection_block_kinds must be a tuple")
        if any(type(kind) is not str or not kind.strip() for kind in self.projection_block_kinds):
            raise ValueError("projection block kinds must be non-empty text")
        for name, value in (
            ("projection_bytes", self.projection_bytes),
            ("history_bytes", self.history_bytes),
            ("request_input_bytes", self.request_input_bytes),
            ("total_request_context_bytes", self.total_request_context_bytes),
            ("omitted_block_count", self.omitted_block_count),
            ("truncation_count", self.truncation_count),
        ):
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.outcome not in {"injected", "omitted", "failed"}:
            raise ValueError("invalid context assembly outcome")
        if type(self.awareness_availability) is not ContextAvailability:
            raise TypeError("awareness_availability must be a ContextAvailability")
        if isinstance(self.awareness_note_count, bool) or self.awareness_note_count < 0:
            raise ValueError("awareness_note_count must be non-negative")
        if type(self.awareness_block_present) is not bool:
            raise TypeError("awareness_block_present must be a bool")
        if type(self.awareness_omitted_by_budget) is not bool:
            raise TypeError("awareness_omitted_by_budget must be a bool")


ContextRequestFactory = Callable[[ContextPurpose, str, object], ContextBuildRequest]


def production_context_request_factory(
    purpose: ContextPurpose,
    scope_id: str,
    owner: object,
) -> ContextBuildRequest:
    """Translate existing typed cognition provenance into a build request.

    Only runtime-owned trigger IDs cross this seam. Observation payloads and
    model output are intentionally not accepted. A conversation ``run`` has
    no cognition trigger and therefore receives the scope-only request.
    """

    del purpose
    if not isinstance(owner, CognitionEpisode):
        return ContextBuildRequest(scope_id=scope_id)
    trigger = owner.trigger
    source_refs = (
        tuple(
            ContextSourceRef(reference=reference, provenance=ContextProvenance.RUNTIME_DECLARED)
            for reference in trigger.source_refs
        )
        if trigger.source is not CognitionTriggerSource.EXTERNAL
        else ()
    )
    relevant_intention_ids = (
        (trigger.source_refs[-1],)
        if trigger.source is CognitionTriggerSource.TEMPORAL and len(trigger.source_refs) >= 4
        else ()
    )
    awareness_scope = _episode_awareness_scope(owner)
    return ContextBuildRequest(
        scope_id=scope_id,
        relevant_intention_ids=relevant_intention_ids,
        wake_intent_ref=trigger.wake_intent_id,
        source_refs=source_refs,
        awareness_scope=awareness_scope,
    )


def _episode_awareness_scope(episode: CognitionEpisode) -> AwarenessScope | None:
    """Derive one exact awareness scope only from a single-scope episode."""

    observations = episode.context.observations
    if not observations:
        return None
    scopes = tuple(
        AwarenessScope.from_observation(episode.scope_id, observation)
        for observation in observations
    )
    if any(scope != scopes[0] for scope in scopes[1:]):
        return None
    return scopes[0]


def request_context_bytes(request: ModelRequest) -> int:
    """Return the content byte count for one provider-neutral model request."""

    if type(request) is not ModelRequest:
        raise TypeError("request must be a ModelRequest")
    message_bytes = (
        sum(len(message.text.encode("utf-8")) for message in request.messages)
        if request.messages is not None
        else len((request.prompt or "").encode("utf-8"))
    )
    return message_bytes + sum(len(block.encode("utf-8")) for block in request.system_prompt)


class ProductionContextComposer(ConversationContextGuidanceComposer):
    """Build all production context projections through one deterministic seam."""

    def __init__(
        self,
        builder: ContextFrameBuilder,
        *,
        request_factory: ContextRequestFactory | None = None,
        evidence_capacity: int = MAX_CONTEXT_ASSEMBLY_EVIDENCE,
    ) -> None:
        if type(builder) is not ContextFrameBuilder:
            raise TypeError("builder must be a ContextFrameBuilder")
        if evidence_capacity <= 0:
            raise ValueError("evidence_capacity must be positive")
        self._builder = builder
        self._request_factory = request_factory or _default_request_factory
        self._evidence: deque[ContextAssemblyEvidence] = deque(maxlen=evidence_capacity)
        self._awareness_scopes: dict[str, AwarenessScope] = {}
        self._operating_block = compile_operating_canon(LILAVEL_OPERATING_CANON_V1)[0]

    @property
    def builder(self) -> ContextFrameBuilder:
        """Return the immutable-by-contract builder dependency."""

        return self._builder

    def evidence(self) -> tuple[ContextAssemblyEvidence, ...]:
        """Return bounded content-free request assembly evidence."""

        return tuple(self._evidence)

    def bind_awareness_buffer(self, buffer: PeripheralAwarenessBuffer) -> None:
        """Bind the existing runtime buffer to this composition path once."""

        self._builder.bind_awareness_resolver(PeripheralAwarenessContextResolver(buffer))

    def bind_awareness_source_resolver(self, resolver: AwarenessSourceResolver) -> None:
        """Bind the read-only explicit-reference source seam for awareness."""

        self._builder.bind_awareness_source_resolver(resolver)

    def bind_awareness_scope(self, context_scope_id: str, scope: AwarenessScope) -> None:
        """Bind one runtime-owned exact scope to an existing Core session."""

        if type(scope) is not AwarenessScope:
            raise TypeError("scope must be an AwarenessScope")
        if type(context_scope_id) is not str or not context_scope_id.strip():
            raise ValueError("context_scope_id must be non-empty text")
        if len(context_scope_id.encode("utf-8")) > 128:
            raise ValueError("context_scope_id exceeds its bound")
        if (
            context_scope_id not in self._awareness_scopes
            and len(self._awareness_scopes) >= MAX_AWARENESS_NOTES_TOTAL
        ):
            oldest = next(iter(self._awareness_scopes))
            del self._awareness_scopes[oldest]
        self._awareness_scopes[context_scope_id] = scope

    def compose(
        self,
        scope_id: str,
        run: ConversationRun,
        messages: tuple[ContextMessage, ...],
        existing_guidance: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Compose the USER_RESPONSE projection appended after stable guidance."""

        return self.compose_projection(
            ContextPurpose.USER_RESPONSE,
            scope_id=scope_id,
            owner=run,
            messages=messages,
            existing_guidance=existing_guidance,
        )

    def compose_projection(
        self,
        purpose: ContextPurpose,
        *,
        scope_id: str,
        owner: object = None,
        messages: Sequence[ContextMessage] = (),
        existing_guidance: Sequence[str] = (),
        request_input_bytes: int | None = None,
    ) -> tuple[str, ...]:
        """Build, bound, and observe one purpose-specific projection.

        Resolver/build failures fail closed for this contribution only.  The
        caller keeps its stable guidance and existing canonical evidence.
        """

        if type(purpose) is not ContextPurpose:
            raise TypeError("purpose must be a ContextPurpose")
        message_values = tuple(messages)
        guidance = tuple(existing_guidance)
        history_bytes = sum(len(message.text.encode("utf-8")) for message in message_values)
        input_bytes = history_bytes if request_input_bytes is None else request_input_bytes
        if isinstance(input_bytes, bool) or input_bytes < 0:
            raise ValueError("request_input_bytes must be non-negative")
        operating_present = self._operating_block in guidance

        try:
            request = self._request_factory(purpose, scope_id, owner)
            if type(request) is not ContextBuildRequest:
                raise TypeError("context request factory returned an invalid request")
            bound_scope = self._awareness_scopes.get(scope_id)
            if bound_scope is not None and request.awareness_scope is None:
                request = replace(request, awareness_scope=bound_scope)
            frame = self._builder.build(purpose, request)
            projection = compile_context_projection(frame)
        except Exception:
            self._record(
                purpose=purpose,
                operating_canon_injected=operating_present,
                context_injected=False,
                blocks=(),
                history_bytes=history_bytes,
                request_input_bytes=input_bytes,
                existing_guidance=guidance,
                outcome="failed",
                awareness_availability=ContextAvailability.UNAVAILABLE,
            )
            return ()

        awareness = frame.awareness
        awareness_block = _awareness_block_in(projection)

        fitted, omitted = _fit_projection(
            projection,
            available_guidance_bytes=MAX_GUIDANCE_BYTES
            - sum(len(block.encode("utf-8")) for block in guidance),
            available_guidance_blocks=MAX_GUIDANCE_BLOCKS - len(guidance),
            available_total_bytes=MAX_TOTAL_REQUEST_CONTEXT_BYTES
            - input_bytes
            - sum(len(block.encode("utf-8")) for block in guidance),
            existing_guidance=guidance,
        )
        if not fitted:
            self._record(
                purpose=purpose,
                operating_canon_injected=operating_present,
                context_injected=False,
                blocks=(),
                history_bytes=history_bytes,
                request_input_bytes=input_bytes,
                existing_guidance=guidance,
                awareness=awareness,
                omitted_block_count=omitted or len(projection.blocks),
                outcome="omitted",
            )
            return ()

        self._record(
            purpose=purpose,
            operating_canon_injected=operating_present,
            context_injected=True,
            blocks=fitted,
            history_bytes=history_bytes,
            request_input_bytes=input_bytes,
            existing_guidance=guidance,
            awareness=awareness,
            awareness_block_present=awareness_block in fitted if awareness_block else False,
            omitted_block_count=omitted,
            outcome="injected",
        )
        return fitted

    def _record(
        self,
        *,
        purpose: ContextPurpose,
        operating_canon_injected: bool,
        context_injected: bool,
        blocks: tuple[str, ...],
        history_bytes: int,
        request_input_bytes: int,
        existing_guidance: tuple[str, ...] = (),
        awareness: AwarenessContext | None = None,
        awareness_block_present: bool = False,
        awareness_availability: ContextAvailability = ContextAvailability.UNKNOWN,
        awareness_omitted_by_budget: bool = False,
        omitted_block_count: int = 0,
        outcome: ContextAssemblyOutcome,
    ) -> None:
        if awareness is not None:
            awareness_availability = awareness.availability
            awareness_omitted_by_budget = (
                awareness.availability is ContextAvailability.KNOWN
                and bool(awareness.notes)
                and not awareness_block_present
            )
        self._evidence.append(
            ContextAssemblyEvidence(
                purpose=purpose,
                operating_canon_injected=operating_canon_injected,
                context_injected=context_injected,
                projection_block_kinds=tuple(_block_kind(block) for block in blocks),
                projection_bytes=sum(len(block.encode("utf-8")) for block in blocks),
                history_bytes=history_bytes,
                request_input_bytes=request_input_bytes,
                total_request_context_bytes=request_input_bytes
                + sum(len(block.encode("utf-8")) for block in existing_guidance)
                + sum(len(block.encode("utf-8")) for block in blocks),
                omitted_block_count=omitted_block_count,
                outcome=outcome,
                awareness_availability=awareness_availability,
                awareness_note_count=0 if awareness is None else len(awareness.notes),
                awareness_block_present=awareness_block_present,
                awareness_omitted_by_budget=awareness_omitted_by_budget,
            )
        )


def _default_request_factory(
    purpose: ContextPurpose,
    scope_id: str,
    owner: object,
) -> ContextBuildRequest:
    del purpose, owner
    return ContextBuildRequest(scope_id=scope_id)


def _fit_projection(
    projection: ContextProjection,
    *,
    available_guidance_bytes: int,
    available_guidance_blocks: int,
    available_total_bytes: int,
    existing_guidance: tuple[str, ...],
) -> tuple[tuple[str, ...], int]:
    """Drop whole optional projection blocks until transport guidance fits."""

    blocks = [block for block in projection.blocks if block not in existing_guidance]
    omitted = len(projection.blocks) - len(blocks)
    available_bytes = min(available_guidance_bytes, available_total_bytes)
    if available_bytes <= 0 or available_guidance_blocks <= 0:
        return (), len(projection.blocks)

    while blocks and (
        len(blocks) > available_guidance_blocks
        or sum(len(block.encode("utf-8")) for block in blocks) > available_bytes
    ):
        blocks.pop()
        omitted += 1
    return tuple(blocks), omitted


def _block_kind(block: str) -> str:
    """Return a stable non-content label for a compiled projection block."""

    first_line = block.split("\n", 1)[0]
    return first_line.strip("[]").strip().lower().replace(" ", "_")


def _awareness_block_in(projection: ContextProjection) -> str | None:
    """Return the optional awareness block without exposing its content."""

    for block in projection.blocks:
        if _block_kind(block) == "peripheral_awareness":
            return block
    return None


__all__ = [
    "ContextAssemblyEvidence",
    "ContextRequestFactory",
    "MAX_CONTEXT_ASSEMBLY_EVIDENCE",
    "MAX_TOTAL_REQUEST_CONTEXT_BYTES",
    "ProductionContextComposer",
    "production_context_request_factory",
    "request_context_bytes",
]
