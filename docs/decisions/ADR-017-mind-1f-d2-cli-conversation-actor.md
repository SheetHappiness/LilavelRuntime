# ADR-017 — MIND-1F-D2 CLI conversation admission through SemanticActor

- Status: Accepted
- Date: 2026-09-12
- Phase: MIND-1F-D2
- Supersedes: the P5-B1 direct CLI user admission path in the composed runtime

## Context

MIND-1F-D1 moved production Discord user conversation under the one
character-wide `SemanticActor`, while the persistent CLI still submitted user
text to `PersistentPresenceRuntime`. That left CLI user admission, cancellation,
and ordering outside the same authority and allowed a legacy local lane to
remain adjacent to the actor-owned Discord/Mind lanes.

## Decision

`LilavelRuntime.submit_user()` is the normal CLI user boundary. It creates an
opaque runtime-owned `cli:<submission-id>` request at `USER` priority on the
runtime's single `SemanticActor`. Every ordinary submission receives a new
identity; a caller that replays an explicit identity receives the actor's
fenced settlement without a second Core turn.

CLI and Discord user requests share the same priority and deterministic FIFO
ordering. The actor serializes them character-wide, while the CLI keeps its
`local-cli` `ConversationCore` scope and Discord keeps its
`(environment, subject)` scopes. No actor request contains user text and the
actor never writes canonical messages.

The existing provider-neutral `ConversationExecutionAdapter` also owns the
local Core bridge. It handles streaming callbacks, cancellation before the
Core run binds its model generation, and joined Core/model/presentation
settlement. `ConversationCore` remains the sole canonical user/assistant
history authority.

`PersistentPresenceRuntime` remains lifecycle-owned for P5-B1 idle policy,
`MindAppraiser`, `AutonomousCognitionRunner`, and noncanonical presence
actions. In the composed runtime it binds direct user submission back to
`LilavelRuntime` and exposes a temporary actor-user exclusion gate: legacy
appraisal/autonomous work is cancelled and joined before actor USER admission,
and remains excluded until the actor episode settles. Its standalone
compatibility construction is retained for existing P5-B1 callers and is not
the normal CLI composition.

## Consequences

- CLI and Discord cannot make parallel semantic user decisions in one runtime.
- A user request preempts active actor-owned non-user Mind work using the
  existing settlement and poisoning rules.
- CLI streaming remains a presentation concern of `PromptToolkitOutputSink`.
- Legacy appraisal/autonomous behavior is still present and is not yet actor-
  migrated; full retirement remains MIND-1F-E.
- Actor request fencing remains current-session only. Restart-safe replay and
  durable wake/state recovery remain unverified and deferred.

## Rejected alternatives

- A CLI-local actor would create a second character-wide admission authority.
- Keeping CLI input in the Presence queue would preserve parallel ordering and
  cancellation ownership.
- Merging CLI and Discord Core histories would violate per-surface continuity
  and subject isolation.
