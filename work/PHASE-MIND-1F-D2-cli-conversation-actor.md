# Phase MIND-1F-D2 — CLI conversation through the SemanticActor

## Baseline and scope

- Canonical repository: `LilavelRuntime`.
- Parent: MIND-1F-D1 closeout `d6dd8db32a634b4059b94474e838e5803a59d57a`.
- Scope: normal local CLI user conversation only.
- No history rewrite or push.

## Implemented truth

- `LilavelRuntime.submit_user()` admits normal CLI input as `USER` work on the
  one character-wide `SemanticActor`.
- CLI and Discord use the shared `ConversationExecutionAdapter`; CLI streams
  Core events to the existing presentation sink and keeps `local-cli` history
  separate from Discord subject sessions.
- CLI submission identities are runtime-owned and opaque. Ordinary repeated
  text receives distinct identities; explicit replay is fenced in-session.
- Actor cancellation and shutdown settle Core/model/presentation before lane
  release. Partial streamed assistant output is not claimed to roll back, and
  cancelled Core turns do not commit an assistant message.
- Presence remains lifecycle-owned for legacy idle/appraisal/autonomous work.
  The temporary actor-user exclusion cancels and joins that work around actor
  USER episodes. Standalone Presence callers retain a compatibility path; the
  composed CLI does not use it.

## Deterministic evidence

The focused D2 suite proves actor-owned CLI admission, canonical Core history,
CLI↔Discord serialization in both directions, isolated histories, distinct
identical-text submissions, replay fencing, interrupted assistant semantics,
legacy-lane exclusion, and clean shutdown.

## Validation

Executed on Linux with Python 3.14.7:

- Focused D2 proofs: PASS, 9 passed.
- Runtime full suite: PASS, 168 passed.
- Runtime Ruff, formatting, strict Pyright, and lock: PASS.
- Contracts suite: PASS, 7 passed; Ruff, formatting, Pyright, and lock: PASS.
- Core suite: PASS, 180 passed and 4 skipped; Ruff, formatting, Pyright, and
  lock: PASS.
- Discord adapter suite: PASS, 95 passed and 10 existing deprecation
  warnings; Ruff, formatting, Pyright, and lock: PASS.
- Root sync, lock, and launcher help: PASS.
- Documentation integrity, architecture guard, and `git diff --check`: PASS.

Live provider/Discord behavior remains `UNVERIFIED`. Restart-safe replay,
durable MindState/WakeIntent recovery, and Windows-specific behavior remain
`UNVERIFIED`. The existing 256-entry application fence ceiling remains
unchanged debt. Deterministic fixtures do not establish those claims.

## Exit gate

One Lilavel runtime has one actor-owned user admission authority across CLI and
Discord, with isolated Core histories and legacy Presence behavior explicitly
gated pending MIND-1F-E.

## Implementation

- Implementation SHA: `0fe9c1e08d3594c2b3b8e2dfb4a130fca48fba03`
- Closeout SHA: `TBD`
