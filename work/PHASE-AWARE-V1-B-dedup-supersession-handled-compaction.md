# AWARE-V1-B — Peripheral Awareness Deduplication + Supersession + Handled State + Compaction

## Status

**PASS** for the AWARE-V1-B exit gate.

## Baseline and commits

- Expected baseline: `63df3eb29a71499d717391dcc81e507b8318aeae`
- Exact implementation parent: `63df3eb29a71499d717391dcc81e507b8318aeae`
- Implementation: `44ad1ee702fa0491626655b849fd806dbb35c0f5`
- Closeout: documentation commit follows the implementation commit
- Push: not performed

Preflight matched the expected baseline exactly and the worktree was clean.
The implementation adds no provider, Discord, persistence, memory,
ConversationStore, ObservationWindow, MindState, or production ContextFrame /
model-request integration.

## Lifecycle contract

`PeripheralAwarenessBuffer` remains the sole owner of bounded, process-local
NOTE state in `apps/runtime/src/lilavel_runtime/awareness.py`. The existing
attention contract is preserved:

```text
DROP  → nothing retained
NOTE  → one bounded awareness admission attempt, no cognition/action/wake
THINK → existing cognition path, no NOTE dual-write
```

### Deduplication

`AwarenessKey` is an immutable typed seam for validated runtime/adapter
metadata. Its `dedup_key` and optional `supersession_key` are each bounded to
128 UTF-8 bytes; plain strings are rejected at the buffer API. Without richer
adapter metadata, the safe default dedup key is a SHA-256 digest of trusted
runtime metadata only: exact scope/environment/surface, event identity, event
kind, and deterministic attention reason codes. Raw payload values are never
read for identity.

An equivalent active NOTE coalesces into the existing note ID. The first
`admitted_at` is preserved; the latest observation/event provenance pair and
reason codes replace the prior bounded pair; `last_seen_at` and the five-minute
TTL refresh; and `occurrence_count` increments with saturation at 1,000,000.
The two-source-reference and eight-reason-code bounds remain enforced.

### Supersession

Supersession requires an explicit non-empty bounded `supersession_key` and a
distinct dedup identity. A newer NOTE supersedes the active older NOTE only
when both notes have the same exact `AwarenessScope` and supersession key. The
older record enters `SUPERSEDED` state, is omitted from active snapshots, and
is removed by structural compaction before overflow enforcement. Missing
supersession metadata never causes a text- or kind-based guess, and no
cross-scope or cross-surface merge exists.

### Handled state

`mark_handled(note_id, authority=...)` and the narrow scoped
`mark_handled_by_key(...)` reconciliation seam require a buffer-bound
process-local `AwarenessHandledAuthority`. The capability is minted by the
buffer and cannot be reconstructed from model output or an untrusted adapter
value. A handled note is omitted from active snapshots and removed on the next
structural compaction. Expiry is a separate clock-driven transition and never
becomes handled.

### Compaction, ordering, and bounds

Compaction runs on admission, active snapshot, count, and explicit lifecycle
calls. It removes expired, handled, and superseded records before deterministic
oldest-effective-note overflow eviction. Active snapshots are immutable tuples
ordered oldest-to-newest by `(last_seen_at, note_id)`, so duplicate refreshes
have deterministic ordering. The inherited bounds remain 16 active notes per
scope, 64 active notes globally, two source references, and eight reason codes;
new key fields are 128 UTF-8 bytes and occurrence count is a bounded saturating
integer. No timer is created per note.

## Integration and boundaries

The lifecycle begins after `AttentionDecision.NOTE`; deduplication and
supersession cannot turn NOTE into THINK. No operation creates a
`CognitionTrigger`, model call, `ActionProposal`, tool/effect execution,
`TemporalWake`, response disposition, persistence write, canonical conversation
history entry, or memory record. `ObservationWindow` and MindState remain
separate owners. `snapshot_active(scope, now)` is a read seam reserved for
AWARE-V1-C; production `ContextFrame` and `ModelRequest` topology is unchanged.

The architecture guard now checks `awareness.py` imports, referenced authority
types, and calls against model, effect, wake, persistence, and ContextFrame
ownership. The guard is AST-based and remains independent of payload text or
natural-language source comments.

## Evaluation corpus

`apps/runtime/src/lilavel_runtime/awareness_eval.py` retains the 34-scenario
AWARE-V1-A regression corpus and adds `AWARE_V1_B_SCENARIOS` with **40/40
PASS** deterministic scenarios covering:

- exact duplicate coalescing, slot preservation, last-seen/TTL refresh, and
  bounded occurrence counts;
- typed identity, raw-payload rejection, different-key behavior, and scope
  isolation;
- explicit same-scope supersession, missing metadata, and active-snapshot
  omission;
- trusted handled reconciliation, untrusted/model rejection, expiry
  distinction, and compaction-before-overflow;
- per-scope/global bounds, deterministic ordering, provenance/reason bounds,
  immutable snapshots, and restart non-recovery; and
- no persistence/history/memory/ContextFrame/model/effect/wake side effects,
  unchanged DROP/NOTE/THINK behavior, unchanged CTX-V1 topology, and the
  awareness architecture import boundary.

## Validation

All deterministic checks below were run on the local Linux/Python 3.14 host.

| Check | Result |
| --- | --- |
| Root `uv sync --locked` and `uv lock --check` | **PASS** |
| Runtime `uv sync --locked` and `uv lock --check` | **PASS** |
| Runtime Ruff check | **PASS** |
| Runtime Ruff format check | **PASS** — 61 files already formatted |
| Runtime strict Pyright | **PASS** — 0 errors, 0 warnings |
| Focused AWARE-A+B tests | **PASS** — 27 passed (13 A + 14 B) |
| Deterministic AWARE-V1-A corpus | **PASS** — 34/34 |
| Deterministic AWARE-V1-B corpus | **PASS** — 40/40 |
| COG attention + CTX-V1 A/B/C + kernel regressions | **PASS** — 55 passed |
| Full Runtime suite | **PASS** — 357 passed in 10.78s |
| Architecture guard | **PASS** |
| Documentation integrity | **PASS** — 69 Markdown files, 104 local links |
| `git diff --check` | **PASS** |
| Live provider / ambient LIVE Discord | **UNVERIFIED** — not required for this phase |
| Restart recovery / durable awareness | **UNVERIFIED** — intentionally unsupported |

## Exit gate

**PASS.** Equivalent typed NOTE events coalesce deterministically without
wasting active slots; newer trusted state supersedes older state only through
explicit bounded keys; trusted runtime reconciliation marks awareness handled;
expiry/handled/superseded state compacts before overflow; scope isolation,
immutable snapshots, provenance bounds, and deterministic ordering remain
intact; and no model, cognition, action, wake, persistence, memory, or
production ContextFrame behavior was introduced.

## Remaining status

- FAIL: none.
- BLOCKED: none.
- UNVERIFIED: live provider/ambient LIVE Discord and restart recovery, both
  intentionally outside this phase's deterministic exit gate.
- Worktree was clean before the closeout record was added.
- Divergence and final worktree cleanliness are recorded after the closeout
  commit; push remains **not performed**.
