# CTX-V1-A — OperatingCanon + Context Contracts + Evals

Status: PASS

## Baseline and commits

- Expected baseline: `4e93279ac93d8504694cd9af8f5545225090773d`
- Exact implementation parent: `4e93279ac93d8504694cd9af8f5545225090773d`
- Implementation: `527321b` (`feat: add CTX-V1-A operating and context contracts`)
- Closeout: documentation commit follows the implementation commit
- Previous COG-V1-E2 implementation: `0823623dbed63c68382a7cf9e5ab3ca197281469`
- Push: not performed

Preflight matched the expected baseline exactly and the worktree was clean.
The implementation commit preserves that history and adds no provider or
environment integration.

## Ownership and implementation

`OperatingCanon` lives in Core at
`apps/core/src/lilavel_core/operating.py`. It is an immutable, bounded typed
canon separate from `IdentityCanon`/CharacterCanon and current runtime state.
`LILAVEL_OPERATING_CANON_V1` contains eight stable laws covering one persistent
character across environments, runtime-presented information, cognition versus
expression, proposal versus effect, valid silence, temporal reconsideration,
runtime-surfaced capabilities, and purpose-specific views. It contains no
personality, current capability, surface, time, intention, memory, relationship,
provider, or model facts.

`ContextFrame` and its subcontracts live in Runtime at
`apps/runtime/src/lilavel_runtime/context.py`. The frame is frozen, bounded,
provider-neutral, and a view for one semantic purpose; it is not a store,
transcript, canonical history, ObservationWindow, MindState, memory record, or
effect-permission authority. Its typed domains are:

- environment and surface context;
- current interaction, participants, and other-surface activity;
- bounded relevant intention references and short projections;
- runtime-declared capability availability and provenance;
- advisory E1 social state;
- optional temporal facts and wake reference; and
- bounded trusted source references.

The exact `ContextPurpose` values are `USER_RESPONSE`, `AMBIENT_COGNITION`,
`INTERNAL_APPRAISAL`, and `TEMPORAL_WAKE`.

`ContextAvailability` distinguishes `KNOWN`, `KNOWN_EMPTY`, `UNKNOWN`, and
`UNAVAILABLE`. Known-empty collections require `KNOWN_EMPTY`; unknown and
unavailable values carry bounded reasons; absent information is rendered as
unknown rather than false or empty. Capability items accept only
`RUNTIME_DECLARED` or `VALIDATED_ADAPTER` provenance. External payload and
unknown provenance are rejected for trusted capability/frame entry. Social
context is explicitly advisory and never grants effect permission.

Structural bounds are explicit: 4 intentions, 8 capabilities, 8 participants,
8 source references, 128-byte opaque/reason references, 256-byte labels, 8
projection blocks, and an 8 KiB rendered context projection. OperatingCanon is
bounded to 8 laws, 1 KiB per law, and 8 KiB compiled output.

`compile_operating_canon()` produces stable text and
`compile_context_projection()` produces deterministic bounded blocks. Frame and
scope IDs, capture timestamps, and source references are excluded from stable
OperatingCanon output. Current time is rendered only in a `TEMPORAL_WAKE`
projection. Purpose-specific projections omit irrelevant domains, including
ambient-only social and other-surface state from `USER_RESPONSE`.

## Deterministic evals

`apps/runtime/src/lilavel_runtime/context_eval.py` contains 30 human-authored
deterministic scenarios. They cover ownership, ontology, unknown/known-empty/
unavailable truth status, runtime capability declaration, external-payload
trust rejection, purpose-specific omission, temporal reconsideration, stable /
volatile layering, conversation and memory boundaries, immutability, bounded
collections, arbitrary dictionary rejection, social/effect separation, E2
revalidation separation, CharacterCanon and COG-V1 regressions, and proof that
production ModelRequest assembly does not contain ContextFrame.

The focused tests are in `apps/runtime/tests/test_ctx_v1_a.py`. No provider,
LLM judge, live inference, production request mutation, routing change, tool
execution, memory, retrieval, peripheral NOTE buffer, or ContextFrameBuilder
was added in A.

## Validation

All checks below were run on the local Python 3.14.7 host after implementation:

| Check | Result |
| --- | --- |
| Root/package locked sync and lock checks | PASS |
| Core Ruff / format / strict Pyright | PASS |
| Runtime Ruff / format / strict Pyright | PASS |
| Contracts Ruff / format / strict Pyright | PASS |
| Discord adapter Ruff / format / strict Pyright | PASS |
| Focused CTX-V1-A tests | PASS — 8 passed |
| Deterministic CTX-V1-A corpus | PASS — 30/30 scenarios |
| Full Core suite | PASS — 195 passed, 4 skipped |
| Full Runtime suite | PASS — 315 passed |
| Full Contracts suite | PASS — 7 passed |
| Full Discord adapter suite | PASS — 95 passed, 10 existing deprecation warnings |
| Root launcher help | PASS |
| Architecture guard | PASS |
| Documentation integrity | PASS |
| `git diff --check` | PASS |
| Live provider / ambient LIVE Discord composition | UNVERIFIED |

## Exit gate

PASS. OperatingCanon and ContextFrame have separate ownership and stable versus
volatile layers; context is immutable, bounded, purpose-specific, and explicit
about unknown/empty/unavailable status; external payloads cannot enter trusted
context provenance; canonical history, memory, MindState, ObservationWindow,
social-effect authority, and COG-V1 semantics remain separate; and no
production ModelRequest consumes ContextFrame. CTX-V1-B is the next seam for a
runtime-owned builder, followed by deliberate CTX-V1-C integration.
