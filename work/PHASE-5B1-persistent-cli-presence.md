# Phase 5-B1 — Persistent CLI presence

## Baseline and scope

- Canonical baseline: `50ff8181823cf5e3b79781da890f89a3399bf593`.
- P5-A ancestor: `a1f29cf3746a24b22632d135d5f045be9c1abafc`.
- The canonical baseline already contains the stronger P5-B0 audit.
- Competing audit commit `95d5ca987de8124db1fb5fb00f0cfcbc13bfd7a1`
  is a sibling of the canonical audit and was not merged or cherry-picked.
- Branch: `phase5b1-persistent-cli-presence` in a task-owned worktree.
- No push was performed.

P5-B1 implements one persistent local CLI, canonical normal conversation, one
bounded idle opportunity, deterministic wake admission, and exactly one
noncanonical terminal presence action. It does not implement memory, advanced
attention, additional environments, arbitrary autonomous tools, or a general
scheduler.

## Decisions

### Autonomous cognition ownership

`ConversationCore` was not extended with a special autonomous turn. Its
existing `start_turn()` contract atomically accepts a canonical user message
and commits a successful assistant completion; bypass flags would make that API
semantically conditional.

Instead, the runtime owns a thin `AutonomousCognitionRunner` that submits one
transient request through the same `ModelRuntimeV3`. It consumes the existing
generation stream and delegates tools to the existing application-owned
registry/session path. It owns no provider loop, executor, transcript,
canonical history, or persistence. `LilavelRuntime` starts, monitors, and stops
the local presence component.

### Terminal actions

Only an application-permitted autonomous run identity receives
`presence.say(text)` and `presence.stay_silent()` specs; an
`autonomous:`-shaped string alone grants nothing. The generation-scoped
executor then independently authorizes and atomically reserves the first valid
action:

- `say` validates non-empty bounded text, enqueues it once to the local CLI,
  and returns `ToolResult(status=ok, effect=confirmed)`;
- `stay_silent` returns `ToolResult(status=ok, effect=none)` and emits nothing;
- later actions in the same generation are unavailable and cannot create a
  second effect.

The provider continuation remains mandatory after `ToolResult`. Its text is
consumed and discarded, as is any ordinary autonomous text before a tool. Only
the terminal executor is a semantic output sink, preventing duplicate speech
without weakening V3 joined settlement.

## Implemented components

- Root `pyproject.toml` and lock provide the `uv run lilavel` launcher shape.
- `prompt-toolkit==3.0.52` is pinned in the runtime dependency and lock.
- The Discord adapter lock is refreshed because it consumes the local runtime
  package and therefore inherits the new CLI dependency transitively.
- The async CLI uses `PromptSession.prompt_async()` and `patch_stdout()` with a
  bounded, backpressured cross-thread output queue.
- `PersistentPresenceRuntime` owns bounded local input, monotonic idle timing,
  a one-shot opportunity latch, deterministic wake, and the single cognition
  lane; it is lifecycle-owned by `LilavelRuntime`.
- User activity resets idle state and records cancellation intent before
  successor admission. The latch covers the race before an autonomous handle
  is bound.
- Presence evidence and generation action state are bounded and contain only
  IDs/status classes, never prompt, response, tool arguments, or private
  reasoning.
- A small Python 3.14 typing compatibility fix uses a typed lookup for the
  Windows-only `ctypes.get_last_error` symbol; runtime behavior is unchanged.

## Deterministic scenario evidence

The runtime suite independently expresses the required behavior without
copying ProjectBEA source or tests:

1. cold start, idle, and clean shutdown;
2. user input while idle, streamed response, canonical commit, and idle return;
3. real activity resets the idle deadline;
4. one timeout creates exactly one `IdleOpportunity`;
5. `NO_WAKE` performs no model call;
6. `WAKE → stay_silent` is successful and invisible;
7. `WAKE → say` creates exactly one visible output;
8. autonomous output remains absent from canonical history;
9. provider continuation text after the action is suppressed;
10. user input preempts autonomous generation across the pre-handle race;
11. user input during the executor effect waits for joined settlement;
12. the one-shot latch prevents recursive or runaway idle cognition;
13. shutdown cancels and settles autonomous generation;
14. one opportunity maps to at most one cognition admission;
15. ordinary text or no terminal tool is contained as invalid with no output;
16. the prompt-toolkit sink is isolated behind a thread-safe bounded boundary;
17. normal user-generation supersession retains prior Core semantics.

The fake V3 sidecar has no provider or external effects. ProjectBEA code was
not copied or derived, so no new upstream license notice is required.

## Validation record

Executed on Linux with Python 3.14.7:

- Contracts Ruff check and format check: PASS.
- Contracts Pyright: PASS.
- Contracts Pytest: PASS, 7 tests.
- Runtime `uv lock --check`: PASS.
- Runtime Ruff check and format check: PASS.
- Runtime Pyright: PASS.
- Runtime Pytest: PASS, 84 tests.
- Core `uv lock --check`: PASS.
- Core Ruff check and format check: PASS.
- Core Pyright: PASS.
- Core Pytest: PASS, 179 passed and 4 Windows-only skips.
- Discord adapter Ruff check and format check: PASS.
- Discord adapter Pyright: PASS.
- Discord adapter Pytest: PASS, 95 tests (10 upstream deprecation warnings).
- Root `uv sync --locked`, `uv lock --check`, and
  `uv run --locked lilavel --help`: PASS.
- Model-sidecar TypeScript check: PASS.
- Model-sidecar Pytest-equivalent Bun suite: PASS, 94 tests.
- Live local presence proof: PASS. One provider-authenticated process streamed
  `FIRST_OK`, recorded one `NO_WAKE`, streamed `SECOND_OK`, produced one bounded
  autonomous local `presence.say` output, accepted and streamed `AFTER_OK`, and
  exited with code 0. Debug evidence recorded exactly two idle opportunities,
  one cognition admission, and one completed cognition settlement.
- Repository documentation, architecture, and whitespace checks are recorded
  after the final validation pass below.

## Canonical and settlement proofs

The autonomous runner receives only a bounded snapshot of
`ConversationCore.history`; it never calls `start_turn`, store append, or an
assistant commit path. Deterministic `say` leaves `history == ()`. Normal user
conversation still commits only successful Core completion.

Preemption sets cancellation intent at autonomous admission time, applies it
when the physical handle binds, consumes the cancelled terminal, and only then
dequeues the user successor. During an executing `say`, the visible effect is
not rolled back; user admission waits until executor and provider settlement
join, and V3 evidence records `joined_settlement=cancelled`.

## Remaining unknowns and deferred work

- Live provider behavior remains timing- and model-dependent even though the
  recorded local proof selected one terminal tool successfully.
- Autonomous utterances have no durable audit or history record in V0 beyond
  bounded runtime evidence.
- The production idle interval and future wake policy need product tuning; the
  current default is conservative and deterministic.
- Durable memory, retrieval, relationship cognition, REACT/NOTE/DROP,
  probabilistic attention, other environments, full Skill API, and arbitrary
  autonomous tools remain deferred.

## Exit gate

The phase closes when all repository-required checks pass, the live local proof
is reported as `PASS`, `FAIL`, or `BLOCKED` without conflating it with fixture
evidence, and one task-owned commit is created without push.
