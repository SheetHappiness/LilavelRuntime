# AWARE-V1-A — Peripheral Awareness NOTE Buffer Contracts + Admission + Bounds

## Status

**PASS** for the AWARE-V1-A exit gate.

## Baseline and commits

- Expected baseline: `9481e3ff9a39f6805d9c3bbdd361eea5d1e8a9ae`
- Exact implementation parent: `9481e3ff9a39f6805d9c3bbdd361eea5d1e8a9ae`
- Implementation: `718616c48015cb9949b2c6c9cf9de41086bb13c9`
- Closeout: documentation commit follows the implementation commit
- Push: not performed

Preflight matched the expected baseline exactly and the worktree was clean.
The implementation commit adds no provider, Discord, persistence, memory,
ContextFrame, or model-request integration.

## Ownership and contract

`PeripheralAwarenessBuffer` lives in
`apps/runtime/src/lilavel_runtime/awareness.py` and is the sole owner of
short-lived, process-local peripheral awareness. `AwarenessNote` is immutable
and contains only:

- runtime-local `note_id`;
- exact `AwarenessScope` of semantic `scope_id`, source `environment_id`, and
  source `surface_id` (`EventSource.subject`);
- two bounded opaque provenance references: the admitted observation and its
  WorldEvent;
- timezone-aware `observed_at`, `admitted_at`, and `expires_at` timestamps; and
- the exact deterministic `CognitionReasonCode` tuple from the NOTE verdict.

No arbitrary payload, message text, transcript, model confidence, memory
record, handled status, or effect authority is stored. The trusted path is:

```text
WorldEvent → runtime-admitted Observation
           → deterministic AttentionVerdict(NOTE)
           → PeripheralAwarenessBuffer.admit()
```

The buffer is synchronous and deterministic. `DROP` performs no admission;
`THINK` remains on the existing CognitionTrigger path and does not dual-write;
`NOTE` performs exactly one admission attempt and returns `NO_COGNITION`.
Buffer failure is fail-closed to no retained awareness and cannot escalate to
cognition. No NOTE path creates a model call, action/tool call, response
disposition, or TemporalWake.

## Scope, bounds, and retention

- 16 notes maximum per exact `(semantic scope, environment, surface)` key.
- 64 notes maximum across one buffer owner.
- 2 source references per note, at 128 UTF-8 bytes each.
- 8 deterministic reason codes per note.
- No textual projection is stored in A, so no text body is duplicated or
  bounded as a hidden transcript.
- Five-minute TTL by default, with an injected timezone-aware clock for tests.
- Expiry is checked on admission and bounded reads; no timer is scheduled per
  note.
- Snapshots are immutable tuples ordered oldest-to-newest.
- Per-scope overflow evicts the oldest note in that exact scope. Global
  overflow evicts the oldest note across all scopes. No ranking, model score,
  RNG, deduplication, or supersession is used.

Identical NOTE attempts are both admitted in A with distinct runtime-local
sequence identities. Deduplication, supersession, compaction, and handled
state are deferred to AWARE-V1-B. Awareness is in-memory and intentionally
non-durable: it may disappear on process restart and is not written to
ConversationStore or evidence persistence. Restart recovery is unsupported and
remains `UNVERIFIED`.

`ObservationWindow` remains the transient perception window used by cognition
episode assembly. Awareness does not mutate it, MindState, social permission,
effect authority, or ContextFrame. No awareness block is present in production
`ModelRequest` assembly; model-facing `WHILE YOU WERE BUSY` projection belongs
to AWARE-V1-C.

## Integration and evals

`DeterministicAttentionCognitionGate.decide()` is the exact NOTE admission
point. `DeterministicAttentionPolicy` remains pure, and `evaluate()` remains
side-effect free. `LilavelRuntime` owns the default buffer and binds it to the
deterministic gate using the SemanticActor scope. Other cognition gate types
retain their existing behavior and do not gain a NOTE lane.

`apps/runtime/src/lilavel_runtime/awareness_eval.py` contains 34 deterministic
scenarios covering DROP/THINK/NOTE semantics, zero cognition/model/action/wake
side effects, trusted provenance, payload rejection, deterministic ordering and
overflow, scope isolation, bounds, immutability, TTL and injected clocks,
non-durable ownership, the ObservationWindow/MindState/ContextFrame/memory
boundaries, and COG/CTX regressions.

## Validation

All checks below were run on the local Linux/Python 3.14.7 host:

| Check | Result |
| --- | --- |
| Root locked sync and lock check | PASS |
| Runtime locked sync and lock check | PASS |
| Runtime Ruff check | PASS |
| Runtime Ruff format check | PASS |
| Runtime strict Pyright | PASS — 0 errors, 0 warnings |
| Focused AWARE-V1-A tests | PASS — 13 passed |
| Deterministic AWARE-V1-A corpus | PASS — 34/34 scenarios |
| Focused COG-V1-B attention + CTX-V1-A/B/C + kernel regression | PASS — 68 passed |
| Full Runtime suite | PASS — 343 passed |
| Architecture guard | PASS |
| Documentation integrity | PASS |
| `git diff --check` | PASS |
| Live provider / ambient LIVE Discord | UNVERIFIED |
| Restart recovery / durable awareness | UNVERIFIED — intentionally unsupported |

## Exit gate

PASS. `AttentionDecision.NOTE` now produces a real bounded runtime-owned
peripheral-awareness entry with trusted typed provenance, exact scope
isolation, deterministic FIFO overflow, injected-clock TTL expiry, immutable
snapshots, and no durable persistence. DROP and THINK semantics are preserved;
NOTE creates no cognition, model, action, effect, response-disposition, or wake
side effects; and production ContextFrame/model-request topology is unchanged.
