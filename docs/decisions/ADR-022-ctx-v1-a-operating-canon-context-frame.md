# ADR-022: CTX-V1-A operating canon and current-context view

## Status

Accepted for CTX-V1-A.

## Context

Lilavel needs model-facing runtime ontology and current-situation context, but
identity, canonical conversation evidence, working intentions, observations,
memory, attention, and effect permission have different owners. A single
prompt object would make current facts look like character law and would make
omitted data indistinguishable from empty or false data.

The next context track must therefore define contracts before it builds a
runtime builder or changes production request assembly.

## Decision

Keep the following ownership boundaries explicit:

```text
CharacterCanon  → who Lilavel is
OperatingCanon  → how she exists inside LilavelRuntime
ContextFrame    → the bounded current situation for one purpose
ConversationCore → canonical conversation evidence/history
MindState       → runtime-owned working commitments/intentions
Memory          → future durable learned knowledge
Attention/COG   → whether/how cognition and expression are proposed
```

`OperatingCanon` is a Core-owned immutable typed canon compiled to stable
bounded guidance. It states one persistent character across interfaces, the
runtime-presented information boundary, cognition/expression and proposal/effect
separation, valid silence, temporal reconsideration, runtime-surfaced
capabilities, and purpose-specific context. It contains neither CharacterCanon
personality nor current facts, time, capability claims, state, memory, or
provider/model names.

`ContextFrame` is a Runtime-owned immutable provider-neutral view, never a
transcript or mutable store. It has an explicit bounded purpose taxonomy:
`USER_RESPONSE`, `AMBIENT_COGNITION`, `INTERNAL_APPRAISAL`, and
`TEMPORAL_WAKE`. Typed subcontracts cover environment/surface, interaction and
participants, bounded intention references, capability availability and
provenance, advisory social state, optional temporal facts, and trusted source
references.

Every context status is explicit: `KNOWN`, `KNOWN_EMPTY`, `UNKNOWN`, or
`UNAVAILABLE`. Known-empty collections cannot be represented as unknown or as
an ordinary known value. Unknown and unavailable values carry bounded reasons.
Capability availability is accepted only from runtime declaration or a
validated adapter; external payloads cannot mint trusted context facts or
capabilities. Future contributions follow:

```text
adapter/runtime contribution → trust/provenance validation → ContextFrameBuilder
```

ContextFrame does not own or copy canonical conversation messages,
`ObservationWindow`, `MindState`, memory, or social-effect authority. Social
context is advisory and E2 effect-time revalidation remains the authority.
Purpose projections omit irrelevant domains, render unknown explicitly, and
render current time only for a semantically relevant temporal-wake view.

CTX-V1-A includes deterministic contracts, stable canon compilation, and a
human-authored architecture/truth corpus. It does not modify production
`ModelRequest` assembly, routing, tool execution, memory, retrieval,
peripheral notes, or context construction from live state.

## Consequences

- Stable operating law can be cached separately from CharacterCanon and
  volatile current context.
- Missing information cannot silently become false, empty, or unavailable.
- Purpose-specific projections can be added without creating provider-owned
  semantic history or a generic context bag.
- CTX-V1-B may implement the runtime-owned builder from authoritative state.
- CTX-V1-C may deliberately integrate selected projections into production
  requests after separate omission and freshness validation.

## Rejected alternatives

- A combined character/operating/current-state prompt was rejected because it
  collapses ownership and makes stable guidance volatile.
- A `dict[str, Any]` context bag was rejected because arbitrary payloads would
  be difficult to type, bound, provenance-check, or omit by purpose.
- Copying recent messages, observations, or memory into ContextFrame was
  rejected because those systems retain their own canonical ownership.
- Treating absent values as empty or false was rejected because it invents
  runtime truth.
- Injecting ContextFrame into production requests in A was rejected because
  the builder and integration contracts belong to later phases.
