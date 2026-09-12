# ADR-016 — MIND-1F-D1 Discord conversation admission through SemanticActor

## Status

Accepted and implemented on 2026-09-12.

## Decision

Production Discord reactive conversation enters the one character-wide
`SemanticActor` owned by `LilavelRuntime`. The explicit sequence is:

```text
Discord DM
  → WorldEvent
  → Observation admission
  → direct-message cognition gate
  → stable observation-derived request identity
  → SemanticActor (USER)
  → ConversationExecutionAdapter
  → CoreConversationRouter
  → ConversationCore
  → ModelRuntime
  → typed Discord presentation actions
```

The actor owns semantic admission, the two-class priority decision,
character-wide serialization, user preemption of non-user work, replay fencing,
and episode settlement. Discord work is `USER` priority. Generic and temporal
MIND work remains `NON_USER`. A non-user episode submitted while a conversation
is active waits in the actor FIFO lane. A second user item follows the same
deterministic user FIFO semantics; no additional priority class is introduced.

`ConversationExecutionAdapter` is a runtime-owned, provider-neutral execution
seam. It delegates one reactive episode to the existing
`CoreConversationRouter`, translates actor cancellation into router/Core
cancellation, and does not return until the route's Core and presentation
lifecycle is terminal or contained. It owns no subject map, transcript,
provider request, presentation state, or canonical history.

## Ownership preserved

`CoreConversationRouter` remains responsible for mapping each
`(environment, subject)` to its own session and for defensive subject-local
ordering. `ConversationCore` remains the sole owner of canonical user append,
assistant candidate streaming, successful assistant commit, conversation
cancellation/supersession semantics, and canonical history. Actor admission or
cancellation creates no hidden or synthetic Core messages. Discord retains
DM-only filtering, edge-local message deduplication, opaque subject mapping,
typing, streaming, chunking, pacing, and typed presentation action execution.

The actor request identity is derived from the trusted runtime trigger, which
is derived from observation identity and gate reason. It never uses raw user
text, Discord IDs, prompts, model output, or presentation payloads.

## Lifecycle and failure behavior

When a user request arrives during active non-user work, the actor requests
cooperative cancellation and waits for that executor to settle before starting
the conversation successor. If containment is uncertain, the actor becomes
`POISONED`, rejects queued successors, and never starts Core under uncertainty.
Runtime shutdown closes admission, settles the actor's active and queued work,
then closes router/Core sessions. A router cancellation waits for Core run
settlement before the actor lane is released; presentation cleanup remains
owned by the existing route/adapter lifecycle.

## Evidence and validation boundary

Actor evidence contains only bounded request/episode IDs, source, priority,
status, cancellation/preemption flags, and safe reason codes. Core and Discord
diagnostics retain their existing bounded correlation metadata. No raw user
text, Discord IDs, prompts, model output, credentials, tool arguments/results,
or exception bodies are added to actor evidence.

The D1 deterministic proof suite covers actor entry and duplicate fencing,
canonical history preservation, user preemption, non-user queueing,
same-subject continuity, cross-subject isolation, cancellation, presentation
and shutdown settlement, and the existing MIND-1F-C regressions. Live provider
and Discord behavior, restart-safe replay, durable wake recovery, and
Windows-specific behavior remain outside this evidence.

## Non-goals

CLI normal conversation, `PersistentPresenceRuntime`, `MindAppraiser`,
`AutonomousCognitionRunner`, and idle presence wake policy remain unchanged and
are deferred to MIND-1F-D2 / 1F-E. No new attention policy, salience,
relationship state, memory retrieval, social cooldown, or scheduler behavior
is introduced.
