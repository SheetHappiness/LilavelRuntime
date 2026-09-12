# RUNTIME-H2 — Failure & Result Semantics Hardening

**Status:** PASS
**Date:** 2026-09-12
**Parent:** `c902cd78086143056902ee53d043d62982776363`
**Implementation:** `645eeda329b119124e17af50f42f640e02b3f8b6`

## Scope

RUNTIME-H2 closes four runtime correctness gaps after MIND-v1 convergence and
RUNTIME-H1:

1. propagate terminal `SemanticActor` poison through `LilavelRuntime`;
2. aggregate state, temporal, and action application results centrally;
3. guard the two actor-owned semantic model-entrypoint lanes; and
4. correct canonical developer documentation that still described implemented
   runtime seams as absent.

No cognition policy, autonomy, memory, relationship, provider, environment, UI,
CI, or durable-recovery scope was added.

## Poison propagation

`SemanticActor` remains generic and retains its fail-closed behavior. It now
records a bounded typed terminal failure and exposes an explicit
`wait_for_failure()` notification. The runtime supervisor watches that
notification as a member of the existing `asyncio.TaskGroup`; it does not poll
actor state and the actor does not decide runtime policy.

When poison occurs during normal operation, the supervisor preserves the typed
actor failure, transitions `LilavelRuntime` to `FAILED`, closes semantic
admission, and fails sibling runtime tasks through the task group. New `USER`
and `NON_USER` requests are rejected by the poisoned actor. Reason codes such as
`uncontainable_settlement`, `actor_failed`, and `shutdown_timeout` are bounded
and safe; semantic payload text is not used as failure evidence.

Shutdown remains joined and deterministic. If poison is observed while the
runtime is already `STOPPING`, the watcher does not create a second lifecycle
transition; shutdown settlement records the typed failure and returns a failed
runtime. Normal cancellation and successful settlement do not notify poison.
Uncontainable active work remains tracked until it settles, and no successor
semantic episode is allowed to run after containment becomes uncertain.

## Three-way application status semantics

`ProposalApplicationCoordinator._aggregate_status()` now treats state,
temporal, and action application as one explicit effect boundary. Any already
effectful `APPLIED`, temporal `DUPLICATE`, or action `APPLIED`/`PARTIAL` result
is retained when a later sub-application fails or rejects. No rollback claim or
effect order changed: state still applies before temporal, and actions still
execute through the existing trusted seam.

| State | Temporal | Actions | Top-level status |
| --- | --- | --- | --- |
| `NOT_REQUESTED` | `APPLIED` | `FAILED` | `PARTIAL` |
| `APPLIED` | `NOT_REQUESTED` | `FAILED` | `PARTIAL` |
| `NOT_REQUESTED` | `APPLIED` | `APPLIED` | `APPLIED` |
| `NOT_REQUESTED` | `NOT_REQUESTED` | `FAILED` | `FAILED` |
| `APPLIED` | `APPLIED` | `NOT_REQUESTED` | `APPLIED` |
| `APPLIED` | `APPLIED` | `FAILED` | `PARTIAL` |
| `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` | `NO_PROPOSALS` |

`STALE`, `REJECTED`, `FAILED`, `PARTIAL`, `APPLIED`, `DUPLICATE`,
`INELIGIBLE`, and `NO_PROPOSALS` retain their existing boundary meanings.
Application-fence duplicate/replay behavior remains unchanged.

## Static two-lane semantic guard

`scripts/check_architecture.py` now checks the highest-value AST regressions:

- `LocalCognitionEngine` may use only its scoped `generate_for_run()` route;
- `MindAppraiser` and `AutonomousCognitionRunner` may call generation only as
  explicitly deprecated standalone compatibility fixtures in `presence.py`;
- `PersistentPresenceRuntime` may not own a direct generation call;
- canonical `kernel.py` and `cli.py` may not construct the deprecated classes;
- canonical runtime composition must contain `CognitionEpisodeRunner` and
  `LocalCognitionEngine`; and
- a direct generation call in any other runtime production class is a guard
  failure.

The focused H2 suite includes a synthetic `PersistentPresenceRuntime` violation
and verifies that the guard rejects it.

## Documentation corrected

Updated canonical developer-facing documentation:

- `AGENTS.md` — current bounded temporal/presence and trusted application-tool
  seams, while keeping general scheduling, attention, arbitrary model-selected
  tools, and world state deferred;
- `README.md` — actor-owned USER/NON_USER graph, bounded MIND-1E temporal host,
  and deprecated compatibility-path status;
- `docs/STACK.md` — implemented runtime host and trusted executor boundaries;
- `docs/ARCHITECTURE.md` — poison supervision and semantic-entrypoint guard
  ownership; and
- `docs/VALIDATION.md` — reproducible H2 focused validation command.

No new ADR was required: H2 makes the existing actor-supervision,
application-boundary, and MIND-1F-E ownership contracts executable and
documented; it does not introduce a new durable architecture decision.

## Validation

All checks below passed against the implementation commit:

- `uv sync --locked`
- `uv lock --check`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked pyright` — 0 errors, 0 warnings, 0 informations
- focused H2 tests — **12 passed**
- full runtime pytest — **204 passed**
- `python scripts/check_docs.py` — `DOCS_INTEGRITY=PASS`
- `python scripts/check_architecture.py` — `ARCHITECTURE_GUARD=PASS`
- root `uv run --locked lilavel --help`
- `git diff --check`

The existing focused runtime regression set also remained green with **58
passed** before the H2 suite was added. Contracts, Core, Discord, and sidecar
package suites were not run because H2 touched only runtime implementation,
runtime tests, architecture checks, and canonical documentation; they are not
claimed as PASS here.

## Evidence limits

The following remain `UNVERIFIED` and are outside H2: live model/provider
behavior, live Discord behavior, Windows-specific behavior, restart-safe actor
replay or durable application idempotency, and durable state/wake recovery.

The implementation commit is one commit ahead of `origin/main` and has not
been pushed. The documentation closeout commit that contains this phase record
is intentionally separate and will leave the final worktree clean.
