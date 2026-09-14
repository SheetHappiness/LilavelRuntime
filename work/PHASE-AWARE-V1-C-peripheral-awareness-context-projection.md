# AWARE-V1-C — Peripheral Awareness Context Projection

## Status

**PASS** for the AWARE-V1-C exit gate.

## Baseline and commits

- Expected baseline: `56ec2eac224496b395653938e929ec9ec0261fdf4`
- Implementation: `2329b5d` (`Implement AWARE-V1-C context projection`)
- Closeout: the commit containing this phase record and documentation updates
- Push: not performed

The preflight baseline was verified with a clean worktree. The implementation
commit contains only the runtime implementation, focused tests, and the
updated AWARE-A/B regression expectations required by the new ContextFrame
domain. Architecture and scoped README documentation are in the closeout
commit.

## Contract

The existing runtime-owned `PeripheralAwarenessBuffer` remains the sole NOTE
owner. `PeripheralAwarenessContextResolver` accepts exactly one typed
`AwarenessScope` and reads only `snapshot_active(scope)`. The scope preserves
the runtime semantic scope, environment, and source-subject/surface identity;
no other scope is searched and no cross-surface relevance is inferred.

`ContextFrame.awareness` is an immutable, bounded `AwarenessContext`:

- availability is `KNOWN`, `KNOWN_EMPTY`, `UNKNOWN`, or `UNAVAILABLE`;
- `AwarenessContextNote` contains only note ID, existing source references,
  deterministic reason codes, occurrence count, and first/last-seen times;
- raw observation payload, message text, summary, inferred meaning, confidence,
  memory/relationship state, and effect/tool authority are absent; and
- at most `MAX_AWARENESS_CONTEXT_NOTES = 8` notes are projected oldest to
  newest, reusing the AWARE two-source-reference and eight-reason-code bounds.

The optional runtime-owned `ContextBuildRequest.awareness_scope` carries only
the exact scope, never the buffer or raw adapter data. Resolver failures are
fail-soft for the context contribution. A supplied custom builder is also
bound to the existing runtime-owned buffer during Runtime construction.

## Purpose policy and projection

Awareness is resolved and rendered only for `USER_RESPONSE`. It remains
`UNKNOWN` and absent from projections for `AMBIENT_COGNITION`,
`INTERNAL_APPRAISAL`, and `TEMPORAL_WAKE`. `KNOWN_EMPTY`, `UNKNOWN`, and
`UNAVAILABLE` omit the block. The `Peripheral awareness` block is factual and
non-authoritative, includes no source content, and is omitted as a whole when
the existing guidance/request budget cannot fit it.

The direct USER router binds each Core session to the exact scope derived from
the current environment and source subject, then uses the existing
`ProductionContextComposer` path and existing USER model request. No new
semantic admission, provider lane, or composition path was introduced.

## Side-effect boundary

The context read path does not compact, mark handled, supersede, refresh TTL,
delete, or otherwise mutate awareness. It creates no cognition, action, wake,
canonical history entry, or model call. Assembly evidence stores only
availability, selected-note count, block presence, and budget omission; it
does not store source content or provider output. Contentful awareness,
retrieval, memory semantics, and source-message lookup remain separate future
work.

## Validation

All deterministic checks below ran on the local Linux/Python 3.14 host.

| Check | Result |
| --- | --- |
| Root/core/contracts/runtime/Discord lock checks | **PASS** — all five `uv lock --check` commands resolved |
| Ruff, format, and strict Pyright | **PASS** — Core, Contracts, Runtime, and Discord adapter; 0 Pyright errors |
| Focused AWARE-V1-C + AWARE-A/B + CTX-A/B/C + kernel tests | **PASS** — 77 passed |
| Core suite | **PASS** — 195 passed, 4 skipped |
| Contracts suite | **PASS** — 7 passed |
| Discord adapter suite | **PASS** — 95 passed, 10 existing dependency deprecation warnings |
| Runtime residual suite | **PASS** — 367 passed, 3 documented timing cases deselected |
| Full Runtime suite | **FAIL** — reproduced the documented inherited async-teardown stall after the same early test sequence; the command did not complete |
| Documentation integrity | **PASS** — 73 Markdown files, 104 local links, 12 anchors, 6 command paths |
| Architecture guard | **PASS** |
| Conflict-marker search | **PASS** — none found |
| `git diff --check` | **PASS** |
| Live provider / LIVE Discord / restart recovery | **UNVERIFIED** — intentionally outside this deterministic phase gate |

The full Runtime result is not attributed to AWARE-V1-C. Per
`docs/VALIDATION.md`, the residual command deselects exactly:
`test_superseded_prepared_run_cannot_start_stale_generation`,
`test_actor_cancellation_contains_planner_and_starts_no_response`, and
`test_temporal_non_user_work_waits_behind_active_user_conversation`.

## Exit gate

**PASS.** Active NOTE metadata reaches the existing USER_RESPONSE context path
only through an exact typed scope and the read-only `snapshot_active` seam;
projection is bounded, deterministic, metadata-only, fail-soft, and omitted
as a whole under pressure. Ambient/internal/temporal purposes do not consume
awareness, and AWARE/CTX ownership, canonical history, cognition, action,
wake, and model-call boundaries remain intact.
