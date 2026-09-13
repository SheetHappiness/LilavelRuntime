# CTX-V1-B — Runtime ContextFrameBuilder + purpose-specific projections

Status: PASS

## Baseline and commits

- Expected baseline: `e5d22a34c42d1c581a05517a395741cb9c9f5223`
- Exact implementation parent: `e5d22a34c42d1c581a05517a395741cb9c9f5223`
- Implementation: `0325a3c0fb8619f825e410f89a6d412a8e13483a`
- Closeout: documentation commit follows the implementation commit
- Push: not performed
- Local divergence after implementation: `origin/main` ahead/behind = `0/1`

Preflight matched the expected CTX-V1-A closeout exactly and the worktree was
clean. The implementation preserves the CTX-V1-A ownership model and does not
change production model-request assembly.

## Ownership and implementation

`ContextFrameBuilder` lives in
`apps/runtime/src/lilavel_runtime/context_builder.py`. It is synchronous,
deterministic, read-only with respect to its source owners, and constructs a
new immutable `ContextFrame` for each build. It retains resolver dependencies,
not resolver outputs. Owner exceptions fail closed to bounded `UNKNOWN`
domains; malformed typed values and untrusted source references are rejected
with `ContextProviderError`/`ValueError`.

The typed resolver seams are:

- environment/surface: `EnvironmentResolver`;
- current interaction/activity/participants: `InteractionResolver`;
- MindState intentions: `MindStateIntentionResolver`, which requires an
  explicit runtime-owned relevant-intention relation because current
  `MindIntention` values do not carry a surface/scope field;
- capabilities: `CapabilityResolver`, with runtime declarations and
  `ValidatedAdapterCapability` contributions; runtime declarations take
  precedence, same-capability adapter conflicts become unavailable, and
  cross-scope adapter facts are omitted;
- advisory E1/E2 social state: `SocialResolver` over typed
  `SocialPermissionContext`;
- temporal wake relation: `TemporalCoordinatorResolver`, with the builder's
  injected current clock authoritative for `now`;
- trusted provenance: `SourceRefResolver` and already-validated
  `ContextSourceRef` request values.

The kernel now owns a default inert builder and exposes it through
`LilavelRuntime.context_builder`. It connects the existing `MindState` and
`TemporalCoordinator` when those components are configured. It deliberately
does not infer capabilities from registered tools, environment names, payloads,
or model output; without a trusted resolver, those domains remain unknown.

No canonical messages, `ObservationWindow` payloads, memory records,
provider/model identifiers, Discord classes, effect permissions, scheduler
mutations, model calls, tool calls, or production `ModelRequest` paths were
added to the builder.

## Purpose selection

The builder and compiler apply these minimum-state rules:

- `USER_RESPONSE`: current environment/surface, direct interaction and
  participants, explicitly linked intentions, and current capabilities.
  Other-surface activity, ambient social state, unrelated observations, and
  ordinary temporal data are omitted. A trusted temporal-continuation request
  may retain a temporal relation in the frame, but the ordinary projection does
  not render precise time.
- `AMBIENT_COGNITION`: current environment, current interaction including
  other-surface activity, explicitly relevant intentions, current capabilities,
  advisory social state, and trusted source references. Direct conversation
  history is not copied.
- `INTERNAL_APPRAISAL`: current activity and explicitly relevant intentions
  only. No environment noise, social state, capabilities, speech permission,
  canonical messages, or memory are implied.
- `TEMPORAL_WAKE`: current environment, current activity/participants,
  relevant intentions, current capabilities, trusted wake/source provenance,
  and the injected current time. The builder never reconstructs the world from
  the historical wake deadline and never converts the wake reason into a
  speech command. Social effect authority remains outside the frame and at
  E2 application time.

The deterministic projection block order is:

1. context purpose;
2. environment;
3. current interaction;
4. relevant intentions;
5. advisory social context when selected;
6. temporal context when selected;
7. current capabilities.

Empty optional domains are omitted as blocks. Explicit unknown/known-empty/
unavailable statuses are retained where they prevent a false inference. Frame
IDs, scope IDs, precise timestamps outside temporal wake, source references,
and internal provenance tokens are not rendered by default.

## Bounds and trust

The existing CTX-V1-A bounds remain enforced: 4 intentions, 8 capabilities,
8 participants, 8 trusted source references, 8 projection blocks, and 8 KiB
rendered UTF-8 projection. Provider input is typed and bounded before mapping;
MindState intention text is structurally clipped to the existing 256-byte
context-label bound. The final projection is never sliced by bytes, so Unicode
output remains valid UTF-8.

`SocialContextView` is advisory only and has no permission field. A frame may
be stale when an effect is attempted; E2's application-time social resolver and
revalidation remain authoritative.

## Deterministic evals and validation

`apps/runtime/src/lilavel_runtime/context_builder_eval.py` adds 35 authored
CTX-V1-B scenarios covering purpose selection, current temporal state/time,
unknown versus known-empty/unavailable, runtime and adapter provenance,
conflict handling, bounds, stable ordering, trusted references, immutable
ownership boundaries, volatile metadata omission, production request
separation, and COG-V1 regression. The full CTX-A+B focused suite passes
`16 passed`; the B corpus passes `35/35`.

| Check | Result |
| --- | --- |
| Root/runtime/contracts/Discord locked sync and lock checks | PASS |
| Runtime Ruff check and format | PASS |
| Runtime strict Pyright | PASS — 0 errors, 0 warnings |
| Focused CTX-V1-A + CTX-V1-B tests | PASS — 16 passed |
| Deterministic CTX-V1-B corpus | PASS — 35/35 scenarios |
| Full Core suite | PASS — 195 passed, 4 skipped |
| Full Contracts suite | PASS — 7 passed |
| Full Discord adapter suite | PASS — 95 passed, 10 existing deprecation warnings |
| COG-V1 focused regression | PASS — 82 passed, 2 documented timing tests deselected |
| Runtime full suite | FAIL — host timing stall at `test_superseded_prepared_run_cannot_start_stale_generation`; residual authoritative run passed 320 with the 3 documented timing tests deselected |
| Architecture guard | PASS |
| Documentation integrity | PASS |
| `git diff --check` | PASS |
| Live provider / ambient LIVE Discord composition | UNVERIFIED |

The runtime full-suite result is the pre-existing Linux/Python 3.14 async
teardown behavior documented in `docs/VALIDATION.md`, reproduced before any
CTX-V1-B-specific failure could occur. The residual run included the new B
tests and all other runtime tests.

## Exit gate and handoff

CTX-V1-B is PASS: the runtime-owned builder is typed, immutable, deterministic,
purpose-specific, bounded, provenance-preserving, side-effect free, current
time-aware for temporal wake, and not connected to production `ModelRequest`
assembly. COG-V1 behavior remains green in its focused regression scope.

No new ADR was added because ADR-022 already establishes the durable ownership
and stable/volatile-layer decision; the architecture document now records the
implemented builder and resolver map.

CTX-V1-C owns deliberate integration of stable `CharacterCanon` plus
`OperatingCanon` and purpose-specific volatile context into existing USER and
NON_USER model-request paths, with strict prompt-layer ordering,
caching-aware stable prefixes, freshness/observability checks, and behavior
regressions. CTX-V1-B does not perform that integration.
