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

Authoritative commands and results are recorded at closeout after the final
diff is reviewed. Live provider/Discord behavior, restart-safe replay and
durable wake recovery, Windows behavior, and the existing 256-entry
application fence ceiling remain outside this phase or unverified.

## Exit gate

One Lilavel runtime has one actor-owned user admission authority across CLI and
Discord, with isolated Core histories and legacy Presence behavior explicitly
gated pending MIND-1F-E.

## Implementation

- Implementation SHA: `TBD`
- Closeout SHA: `TBD`
