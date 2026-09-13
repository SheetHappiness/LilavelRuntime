# PHASE COG-V1-D1 — Run-Bound USER Disposition Integration

Status: `CLOSED`
Baseline SHA: `9f3944ae2034dd1e79305368758c17f80f261761`
Implementation SHA at exit: `d39081916cb3abecd3bb8bb54e6d6a97a3790275`

## Goal

Integrate the existing COG-V1-C `DispositionPlanner` into the direct USER
lifecycle without shared mutable guidance, semantic-actor bypass, a third
semantic lane, or the final D2 selective-routing policy.

## Decisions

- `ConversationCore.prepare_turn()` persists the canonical user message,
  creates the logical `ConversationRun`, makes it active, and supersedes the
  predecessor before any planner or response model call.
- `TurnBehavior` is a frozen Core contract containing only validated
  `WorkingState`, `ResponseDisposition`, bounded reason codes, and
  `DEFAULT`/`PLANNER` source. `ConversationCore.start_prepared_run()` binds it
  once to exactly one run before generation.
- Shared mutable `current_disposition` state is forbidden. A planner result is
  never stored on the Core or conversation scope as ambient current state.
- `ConversationCore.start_turn()` remains the compatibility DEFAULT_ONLY path.
  The runtime-owned `UserDispositionResolver` provides an explicit
  `DEFAULT_ONLY` / `ALWAYS_PLAN` seam; production CLI and Discord composition
  remain `DEFAULT_ONLY` pending D2 routing.
- Planner resolution occurs after user acceptance and before response
  generation. It reuses COG-V1-C parsing, bounded recent canonical context,
  runtime-owned focus compilation, and fixed direct-user `THINK` + `RESPOND`.
- Malformed/rejected output, provider failure, timeout, and contained planner
  cancellation use the deterministic default with no planner retry. An
  uncontained cancellation preserves fail-closed actor/Core behavior.
- No USER-to-USER preemption or selective heuristic router is introduced.

## Lifecycle

```text
USER SemanticEpisode
  → SemanticActor USER lane
  → ConversationCore.prepare_turn()
  → UserDispositionResolver
  → immutable run-bound TurnBehavior
  → ConversationCore.start_prepared_run()
  → streaming / stale-output filtering / assistant commit
```

The CLI and Discord paths share the existing USER semantic spine. The planner
is not routed through `LocalCognitionEngine`; NON_USER/internal cognition stays
on its existing actor lane.

## Evidence

`ConversationCore.d1_evidence()` is a bounded, content-free trace containing
disposition source, planner invocation/outcome, bounded fallback reason,
monotonic planner duration, structural difference from default, response
generation start, and first-delta timing. It stores neither user text nor raw
planner output. Existing Core lifecycle evidence remains unchanged.

## Validation

- Preflight exact baseline and clean worktree: `PASS`.
- Core lock check: `PASS`.
- Core Ruff check and format check: `PASS`.
- Core strict Pyright: `PASS`.
- Core focused conversation/cognition tests: `PASS` — 26 passed.
- Core full pytest: `PASS` — 195 passed, 4 Linux platform skips.
- Runtime lock check: `PASS`.
- Runtime Ruff check and format check: `PASS`.
- Runtime strict Pyright: `PASS`.
- Focused D1 + inherited COG-V1-C tests: `PASS` — 27 passed.
- Focused canonical USER actor/CLI composition tests: `PASS` — 23 passed.
- Runtime full pytest including the documented timing-sensitive test:
  `PASS` — 249 passed.
- Runtime full pytest with that test deselected: `PASS` — 248 passed, 1
  deselected.
- Architecture guard: `PASS`.
- `git diff --check`: `PASS`.
- Live provider, live Discord, Windows, restart, and production ALWAYS_PLAN
  validation: `UNVERIFIED`.

## Exit gate

The D1 exit gate is satisfied for deterministic validation: each USER run has
immutable behavior, acceptance precedes planning, planner failure falls back
when contained, cancellation/supersession cannot start stale prepared work,
DEFAULT_ONLY makes zero planner calls, explicit ALWAYS_PLAN makes one validated
planner call, and SemanticActor/Core ownership remains intact. D2 selective
deliberation remains future work.
