# CTX-V1-C — Production Context Integration + Prompt Topology + Context Observability

## Status

**PASS** for the CTX-V1-C exit gate.

CTX-V1 is architecturally complete. The full Runtime suite retains an
explicitly inherited host timing failure; that result is recorded separately
below and is not attributed to CTX-V1-C.

## Baseline and commits

- Expected baseline: `5f7603d44729ee31b605ae308bc035cc7644885c`
- Implementation commit: `16fa34167c77cd10ed91869bd0e36cb89fe503be`
- Closeout: the commit containing this phase record
- Push status: not pushed

## Delivered architecture

```text
Runtime-owned state
        ↓
ContextFrameBuilder
        ↓
purpose-specific ContextFrame
        ↓
deterministic compile_context_projection
        ↓
ProductionContextComposer
        ↓
production ModelRequest
```

`ProductionContextComposer` is the single Runtime-owned seam for turning a
trusted `ContextFrame` into model-facing guidance. It uses the existing
deterministic builder and projection compiler. It does not call a model,
mutate runtime state, execute tools, infer capabilities from package or
environment names, or persist snapshots as memory.

`OperatingCanon` remains separate from `CharacterCanon`. Production guidance
uses the one canonical `build_operating_guidance()` compiler seam, which
compiles `LILAVEL_OPERATING_CANON_V1`. Current time, environment, capabilities,
frame IDs, UUIDs, source references, counters, and other volatile values do not
enter that stable projection.

## Production injection points

| Purpose | Existing path | Injection behavior |
| --- | --- | --- |
| `USER_RESPONSE` | `ConversationCore.build_model_request` | Stable Character/Operating/turn guidance remains on the request; the Core-owned canonical messages remain in `ModelRequest.messages`; the Composer appends the bounded USER_RESPONSE projection. |
| `USER_RESPONSE` planner | `DispositionPlanner.build_request` during existing D2 planning | The planner receives the same bounded purpose projection for semantic coherence. It remains advisory and does not alter fixed USER attention/intervention admission. It adds no generation call. |
| `AMBIENT_COGNITION` | `LocalCognitionEngine._ambient_request` | Existing one-inference ambient E2 request receives the AMBIENT_COGNITION projection alongside admitted observation text. Ambient rollout remains `OFF` by default. |
| `INTERNAL_APPRAISAL` | `LocalCognitionEngine._appraisal_request` | Existing completion-appraisal inference receives the INTERNAL_APPRAISAL projection while completed canonical user/assistant evidence remains in `ModelRequest.messages`. |
| `TEMPORAL_WAKE` | `LocalCognitionEngine._temporal_request` | Existing cognition generation receives the TEMPORAL_WAKE projection and current clock/state. Wake reason and intention relation are past provenance, not reconstructed historical world state. |

The temporal path uses the existing scoped cognition generation seam; CTX-C
does not add a context-generation model call or a new semantic lane.

## Prompt topology and budgets

Provider-neutral requests preserve this stable-to-volatile organization:

```text
stable system guidance:
  CharacterCanon guidance
  OperatingCanon
  stable task/control policy
separate canonical evidence:
  ConversationCore messages where applicable
volatile runtime guidance:
  purpose-specific ContextFrame projection
volatile request input:
  current user text, admitted observation, or wake reason
```

The stable system blocks precede the volatile ContextFrame blocks. Canonical
history is supplied by ConversationCore and is never copied into a
ContextFrame. No memory or retrieval layer was introduced.

The existing provider-neutral bounds remain authoritative:

- `MAX_CONTEXT_BYTES`: 64 KiB for prompt/messages.
- `MAX_GUIDANCE_BYTES`: 16 KiB for system guidance.
- `MAX_GUIDANCE_BLOCKS`: 32 guidance blocks.
- Explicit combined accounting bound: 80 KiB for request input plus guidance.

When optional projection content cannot fit, the Composer drops whole blocks
from the end of the deterministic projection order. Stable canon, existing
canonical evidence, and current required input are not silently byte-sliced or
discarded to make room. UTF-8 is never truncated at an arbitrary byte offset.
If the builder or projection fails, only the context contribution fails closed;
the safe request proceeds with stable policy and existing canonical evidence.

## Semantics and authority

- `UNKNOWN`, known-empty, and unavailable remain distinct in model-facing
  wording; missing resolver data is not rendered as known absence.
- Capability blocks describe only CTX-contract-admitted capabilities
  (`RUNTIME_DECLARED` or `VALIDATED_ADAPTER`) and explicitly state that semantic
  awareness does not grant execution authority.
- Social context is advisory and may become stale. E2 effect-time SPEAK
  revalidation and ProposalApplicationCoordinator/P4 remain authoritative.
- Temporal wake always evaluates old provenance against the current frame and
  current time; it never replays the old world.
- Model output remains an untrusted inert proposal and cannot write trusted
  ContextFrame state, create effect authority, grant capabilities, choose a
  destination, or send directly to an environment.

## Content-free observability

The bounded `ContextAssemblyEvidence` trace records, per request composition:

- purpose;
- OperatingCanon injected yes/no;
- ContextFrame injected yes/no;
- projection block kinds;
- projection, history, request-input, and total context byte counts;
- whole-block omission count;
- truncation count (kept at zero because arbitrary byte slicing is forbidden);
- outcome: injected, omitted, or failed.

No raw user text, observation payload, intention text, projection text, source
or frame identifiers, model JSON, or chain-of-thought is recorded.

## Validation

| Check | Result |
| --- | --- |
| Lock/sync checks | **PASS** — all package lock checks resolved |
| Ruff and format | **PASS** — Core, Runtime, Contracts, and Discord adapter |
| Strict Pyright | **PASS** — Core, Runtime, Contracts, and Discord adapter; 0 errors |
| CTX-A/B/C + E2 focused tests | **PASS** — 50 passed |
| COG D1/D2/E2 regression slice | **PASS** — 43 passed, 2 inherited timing tests deselected |
| Core suite | **PASS** — 195 passed, 4 skipped |
| Contracts suite | **PASS** — 7 passed |
| Discord edge suite | **PASS** — 23 passed |
| Runtime residual split | **PASS** — 327 passed, 3 documented timing cases deselected |
| Architecture guard | **PASS** |
| Documentation integrity | **PASS** |
| `git diff --check` | **PASS** |
| Full Runtime suite | **FAIL** — required 330-test run timed out at the early COG-D1 timing stall; the inherited documented stall is `test_superseded_prepared_run_cannot_start_stale_generation` (with the two related timing cases retained in the residual exclusion set) |
| Live provider / ambient LIVE Discord | **UNVERIFIED** — not required for deterministic phase PASS |

The focused CTX-C tests also prove stable ordering, history separation,
current-time temporal semantics, structural whole-block omission, fail-soft
assembly, typed temporal provenance, advisory planner behavior, and one
existing cognition generation per ambient/temporal request.

## Completion and next phase

CTX-V1 now has the intended ownership chain: CharacterCanon defines identity,
OperatingCanon defines runtime law, ContextFrameBuilder selects trustworthy
current state, deterministic projections orient the existing model calls, and
ConversationCore retains canonical dialogue evidence. Memory remains a future
owner, and effects remain external runtime authority.

The next phase is `AWARE-V1`: real NOTE/peripheral-awareness buffering and
selective projection into ContextFrame, rather than another context
abstraction.
