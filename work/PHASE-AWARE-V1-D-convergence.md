# AWARE-V1-D — Source-Resolution Convergence

## Status

**PASS** for the convergence exit gate.

## Baseline and references

- Canonical base: `cc3663d791f22f0400334e6e8b62faa7ef7a1816`
- Local comparison: `backup/aware-v1-d-local` at
  `ce3b4c8fb10e99215d6c692dd5d7669fd3e5cdf4`
- Common ancestor: `bd89d9b4f7c286932c91cdb3b0b3fb0fade978f8`
- Implementation: `626b701d333f19ebc09be773667a872544c48a3b`
- Closeout: this phase record follows the implementation commit
- Push: not performed

The canonical base and local reference were compared from the common
ancestor. Their production source-resolution modules and runtime wiring were
semantically identical. The local reference contributed stronger runtime
wiring evidence and the AWARE-V1-D documentation; those unique, valid pieces
were ported into the canonical tree. No wholesale merge, rebase, or conflict
strategy was used.

## Decisions

| Area | Decision | Rationale |
| --- | --- | --- |
| `AwarenessSourceMaterial` contract | **KEEP_REMOTE** | The canonical base already has the single immutable three-field contract. |
| `AwarenessSourceResolver` contract | **KEEP_REMOTE** | One provider-neutral explicit-reference seam is sufficient. |
| `ObservationWindowAwarenessSourceResolver` | **KEEP_REMOTE** | The implementations are identical: local observation/event lookup and existing textual payload only. |
| Context attachment/rendering/wiring | **KEEP_REMOTE** | The canonical production path already has one exact-scope USER_RESPONSE route. |
| Runtime wiring evidence | **MERGE_BOTH** | Ported the local end-to-end admission and `NO_COGNITION` proof. |
| Documentation | **KEEP_LOCAL** | Ported the local README and architecture sections absent from the canonical base. |
| Partial resolver failure | **MERGE_BOTH** | Preserved successful sibling refs when one explicit lookup raises; malformed or mismatched results still fail closed for that note. |
| Duplicate APIs/paths | **DROP_DUPLICATE** | No duplicate production contracts or resolver stacks were retained. |

## Canonical path

```text
ObservationWindow
  → ObservationWindowAwarenessSourceResolver.resolve(source_ref)
  → PeripheralAwarenessContextResolver.resolve(exact AwarenessScope)
  → AwarenessContextNote.source_material
  → compile_context_projection(USER_RESPONSE)
```

Only explicit `observation:<id>` and `event:<id>` references are accepted.
Lookup is local and read-only. Source text is existing
`WorldEvent.payload["text"]`, bounded to two items per note, 512 characters per
item, and 2,048 characters across one projected awareness context. Resolved
text remains untrusted contextual evidence and is absent from trusted canon,
ambient cognition, internal appraisal, and temporal wake projections.

## Preserved evidence

- Remote implementation: one `AwarenessSourceMaterial`, one
  `AwarenessSourceResolver`, one ObservationWindow-backed resolver, one
  ContextFrame attachment path, and the existing bounds/wiring.
- Local implementation: runtime-owned ObservationWindow admission proof,
  unsupported-reference proof, README/architecture documentation, and the
  phase evidence record.
- Convergence additions: partial exception preservation and malformed or
  source-identity-mismatch non-injection tests.

## Validation

| Check | Result |
| --- | --- |
| Root/Core/Runtime/Contracts/Discord lock checks | **PASS** |
| Focused AWARE-V1-D tests | **PASS** — 16 passed |
| Inherited AWARE/CTX tests | **PASS** — 62 passed |
| Full Runtime suite | **PASS** — 386 passed |
| Full Core suite | **PASS** — 195 passed, 4 skipped |
| Full Contracts suite | **PASS** — 7 passed |
| Full Discord adapter suite | **PASS** — 95 passed, 10 existing deprecation warnings |
| Ruff check and format check | **PASS** — Core, Runtime, Contracts, Discord |
| Strict Pyright | **PASS** — Core, Runtime, Contracts, Discord; 0 errors/warnings |
| Root launcher help | **PASS** |
| Documentation integrity | **PASS** |
| Architecture guard | **PASS** |
| Conflict-marker search | **PASS** — none found |
| `git diff --check` | **PASS** |

Live provider behavior, live Discord, restart recovery, durable awareness, and
future semantic retrieval remain **UNVERIFIED** and outside this phase.

## Exit gate

**PASS.** The canonical base has one source-resolution contract and one
production resolver path, all bounded explicit-reference behavior is tested,
no lifecycle/model/effect/history side effects exist, the worktree is clean,
and the task-owned convergence changes are committed without a push.
