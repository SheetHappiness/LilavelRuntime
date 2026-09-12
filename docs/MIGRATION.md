# Controlled migration

This document is the execution truth for the migration that bootstraps the
canonical `LilavelRuntime` repository. It records source selection and
destination mapping; product ownership and runtime invariants live in
[`ARCHITECTURE.md`](ARCHITECTURE.md), while package behavior lives beside the
package that implements it.

## Source snapshot

The source inspected before mutation was `C:\Lilavel-m4c-integration`.

| Item | Observed value |
| --- | --- |
| Branch | `exp/character-identity-v0` |
| HEAD | `3a5ee5d8a12e59f380cb1779a76a0184583e224b` |
| Worktree | A linked Git worktree whose `.git` points into `C:\Lilavel\.git\worktrees\...`; no Git metadata was copied. |
| Working-tree state | Five unstaged files, all in Character v0 production cognition/tests or Discord presence/tests. |
| Untracked files | None reported by Git; ignored caches and environments were present and excluded. |

The five unstaged files were inspected rather than assumed to be historical
truth:

- `apps/core/src/lilavel_core/production_cognition.py`
- `apps/core/tests/test_character.py`
- `apps/core/tests/test_cognition.py`
- `apps/discord-edge/src/lilavel_discord_edge/edge.py`
- `apps/discord-edge/tests/test_edge.py`

Their changes are narrow and internally consistent: production guidance uses
the existing Character v0 canon, the related deterministic assertions expect
that wiring, and the Discord client candidate sets an explicit online status.
The destination includes these files as **adapted migration input**, after
destination validation. This makes the result durable in the new repository;
it does not retroactively make the source worktree changes committed history.
Their historical rationale remains `UNKNOWN`.

## Selection and exclusions

The migration copied the tracked source implementation, tests, lock/config
files, current root/component documentation, and protocol testdata. It did not
copy `.git`, linked-worktree metadata, ignored or untracked state, caches,
virtual environments, `node_modules`, credentials, local databases, logs, or
diagnostic output.

The following tracked source items were deliberately omitted:

| Source item | Disposition | Reason |
| --- | --- | --- |
| `apps/discord-edge/*.jsonl` | Deferred/omitted | Recorded runtime/diagnostic output, not package source or canonical evidence storage. |
| `apps/model-sidecar/{}` | Deferred/omitted | Empty accidental artifact. |
| `work/` and its completed records | Deferred/omitted | Historical execution continuity, not required to validate the migrated foundation; this document is the new migration record. |

## Path and ownership mapping

| Source | Destination | Disposition | Migration note |
| --- | --- | --- | --- |
| `apps/core` | `apps/core` | Reused + adapted | ConversationCore, ModelRuntime, protocol/lifecycle, persistence/evidence, cognition, Character v0 assets, scripts, fixtures, tests, and Python lock remain behaviorally scoped as before. The five inspected dirty candidates are included explicitly as adapted input. |
| `apps/model-sidecar` | `apps/model-sidecar` | Reused | Bun protocol host, provider transport, lifecycle tests, package manifest, and lock remain local to the sidecar. The empty `{}` artifact is omitted. |
| `apps/discord-edge` | `apps/discord-adapter` | Adapted | The directory name now reflects an environment sensor+action adapter. The import namespace `lilavel_discord_edge` and distribution name remain unchanged to avoid an unnecessary semantic/package rename. |
| `docs/ARCHITECTURE.md` | `docs/ARCHITECTURE.md` | Adapted | Root ownership now distinguishes the persistent agent runtime boundary from the conversational subsystem and environment adapters. Provider operational detail stays local. |
| `docs/STACK.md` | `docs/STACK.md` | Adapted | Technology roles remain, with provider/sidecar detail delegated to its component README. |
| `docs/VALIDATION.md` | `docs/VALIDATION.md` | Adapted | Package paths and the small architecture guard are updated; deterministic/live/platform-specific result semantics are retained. |
| `docs/decisions/ADR-001` through `ADR-005` | same IDs under `docs/decisions/` | Preserved + scoped | Existing decisions remain valid for their stated subsystems; only obsolete ownership wording and paths are clarified. Historical rationale is not reconstructed. |
| New `docs/decisions/ADR-006...` | `docs/decisions/` | Added | Records the accepted top-level product boundary: Lilavel is a persistent agent runtime; Discord is an environment adapter, not the agent host. |
| `testdata/protocol-v2` | same path | Reused | Shared deterministic protocol cases. |
| `work/` | no destination | Deferred | Future task records can be created only when a substantial active work item needs continuity. |

## Current destination shape

```text
LilavelRuntime/
├── apps/
│   ├── runtime/
│   ├── core/
│   ├── discord-adapter/
│   └── model-sidecar/
├── docs/
│   └── decisions/
├── scripts/
├── testdata/
├── AGENTS.md
├── README.md
└── .gitignore
```

The post-migration Phase 2 kernel and Phase 3 Discord environment route now
live in `apps/runtime`. Explicit DMs cross `WorldEvent`, runtime observation
admission, an explicit reactive response step, Core routing, and typed
presentation-action boundaries. Wake/attention policy remains deferred. No speculative
scheduler, ambient wake system, attention loop, model tool runtime, world
model, or cross-environment state package has been introduced.

## Migration risks and unknowns

- The source snapshot was an experimental Character v0 worktree, not a clean
  canonical branch. The exact source branch/HEAD and five dirty files are
  recorded above so later work cannot confuse destination history with source
  history.
- Character v0 production wiring is durable only in the new repository's
  migration commit; its earlier source-worktree provenance is not a commit and
  remains `UNKNOWN`.
- The new repository has no top-level autonomous behavior yet. Scheduling,
  wake, attention/decision, tools/actions, and durable agent-state ownership
  require future decisions and are `DEFERRED`.
- Live provider and Discord checks are credential/service dependent. They are
  not required for this bootstrap and must be reported as `BLOCKED` when the
  required supported authentication or service is unavailable.

## Validation record

These results were executed against the staged destination tree after the
controlled copy and before the canonical initial commit. The final destination
commit contains the same validated tree; its exact SHA is reported by Git at
handoff.

- `git diff --cached --check`: `PASS` — staged destination tree has no whitespace errors.
- `python scripts/check_architecture.py`: `PASS` — Core has no Discord dependency and the adapter has no Core persistence ownership.
- Core package: `PASS` — `uv lock --check`, Ruff check/format, Pyright, and `171 passed` pytest results on Windows, including the real pinned `npx`/Bun platform fixtures.
- Model sidecar: `PASS` — locked Bun install, TypeScript check, and `67 passed` deterministic tests.
- Discord adapter: `PASS` — `uv lock --check`, Ruff check/format, Pyright, and `80 passed` deterministic tests.
- Live/provider/Discord probes: `UNVERIFIED` — intentionally not run; no live claim is required for this migration. If run without their supported prerequisites, the result is `BLOCKED`, not `FAIL`.
