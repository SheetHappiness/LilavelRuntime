# Phase MIND-1F-E — Legacy Cognition Retirement

## Status

Implemented and validated on 2026-09-12.

Implementation SHA: `ae936bfb4ed99e3132d5c40d840a085d8c2dc061`.

## Implemented truth

The canonical semantic ownership graph is exactly:

```text
USER     → SemanticActor → ConversationExecutionAdapter → ConversationCore
NON_USER → SemanticActor → CognitionEpisodeRunner → CognitionOutcome
                              → ProposalApplicationCoordinator
```

Conversation completion now queues one runtime-owned `INTERNAL` appraisal
opportunity as actor `NON_USER` work. The normal cognition runner can return a
quiet result or a `StateProposal(CREATE_INTENTION)`. Trusted runtime
composition binds accepted state deltas to the completed Core user and
assistant message IDs.

Deterministic idle opportunities now create one runtime-owned `INTERNAL`
opportunity for the selected active intention. The normal cognition runner
returns `ActionProposal(SPEAK | STAY_SILENT)`, and the existing P4 application
registry/session seam performs the trusted local action. Confirmed speech
records one bounded noncanonical self-action; silence is terminal and has no
external effect.

`PersistentPresenceRuntime` retains only local CLI input/output plumbing,
bounded evidence, and deterministic idle timing in canonical composition. It
does not own semantic admission, a model runner, or a presence-only tool
runtime. `MindAppraiser` and `AutonomousCognitionRunner` are retired from
production composition and retained only as explicitly deprecated standalone
P5-B1 compatibility fixtures for existing callers/tests.

The `INTERNAL` trigger source carries no observation payload or wake ID and
uses bounded runtime-owned references. Repeated internal opportunity identity
is fenced by the existing actor session fence and application fence. Internal
cognition never creates synthetic canonical conversation messages.

## Deterministic evidence

Focused proofs: `apps/runtime/tests/test_mind_convergence_e.py`.

- Successful appraisal enters the actor once and applies trusted state.
- Quiet appraisal has no effect and no retry.
- User work preempts active internal cognition.
- Idle `SPEAK` uses P4 application and marks the intention expressed.
- `STAY_SILENT` settles with no output and leaves the intention active.
- Application-in-progress cancellation waits for settlement and does not
  roll back confirmed output.
- Internal replay is fenced; separate opportunities remain distinct.
- Canonical Core history contains only the real user/assistant turn.
- Shutdown settles active internal work without an orphaned presence task.

## Semantic model-entrypoint audit

Repository static review classifies the remaining generation entrypoints as:

- `ConversationCore.runtime_generate()` → `ModelRuntime.generate_for_run()`:
  production `USER` conversation route.
- `LocalCognitionEngine._generate()` → `ModelRuntime.generate_for_run()`:
  production `NON_USER` cognition route, reachable only beneath
  `CognitionEpisodeRunner`/`SemanticActor` in the canonical CLI composition.
- `MindAppraiser.run()` and `AutonomousCognitionRunner.run()` in
  `presence.py`: deprecated standalone P5-B1 compatibility fixtures only;
  not constructed by the launcher or canonical runtime composition.
- `ModelRuntime`/`ModelRuntimeV3` implementation calls and sidecar/provider
  code: physical generation infrastructure, not additional semantic admission
  lanes.

No direct semantic generation call remains in canonical
`PersistentPresenceRuntime` composition. There is no third production semantic
model lane.

## Validation and remaining unknowns

- Focused E proofs: 10 passed.
- Runtime: 178 passed; Ruff, format, strict Pyright, lock, and `git diff
  --check` passed.
- Contracts: 7 passed; Ruff, format, strict Pyright, and lock passed.
- Core: 180 passed, 4 skipped; Ruff, format, strict Pyright, and lock passed.
- Discord adapter: 95 passed, 10 existing deprecation warnings; Ruff, format,
  strict Pyright, and lock passed.
- Root sync, lock, docs integrity, architecture guard, and launcher help
  passed.

The existing 256-entry `ProposalApplicationCoordinator` lifetime fence ceiling
is unchanged production debt. Live/provider/Discord behavior, restart-safe
actor replay, durable MindState/WakeIntent recovery, and Windows-specific
behavior remain `UNVERIFIED` unless separately exercised.
