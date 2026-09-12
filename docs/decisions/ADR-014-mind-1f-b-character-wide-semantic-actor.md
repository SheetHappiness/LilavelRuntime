# ADR-014 — MIND-1F-B character-wide semantic admission actor

## Status

Accepted and implemented on 2026-09-12.

## Decision

One `LilavelRuntime` instance owns one provider-neutral `SemanticActor` for
the Lilavel character scope. The actor is not partitioned by environment,
conversation subject, Discord identity, or CLI session. Conversation state
remains per conversation and remains owned by `ConversationCore`; the actor
owns only semantic episode admission, ordering, cancellation/preemption,
replay fencing, lifecycle, and safe evidence.

The actor admits a typed `SemanticEpisodeRequest` containing only a stable
request identity, source kind, one of two priority classes (`USER` or
`NON_USER`), runtime scope, current actor-session identity, and a specialized
provider-neutral async executor. The resulting `SemanticEpisode` contains
bounded identity metadata only. Conversation and cognition remain separate
executors beneath this boundary; provider requests, raw content, model state,
tool authority, and canonical history are not actor contract fields.

The mailbox is bounded and has deterministic priority ordering: user-priority
work precedes older non-user work, while FIFO order is preserved within each
class. One actor-owned worker executes at most one episode. When a user request
arrives during active non-user work, the actor requests cancellation through an
actor-owned cooperative token and waits for executor settlement before running
the user successor. A cancellation request made before an executor binds its
own handle remains visible through that token. If the executor cannot be
settled or contained within the actor policy, the actor becomes poisoned and
rejects successors; no rollback of external effects is claimed.

Replay fencing is scoped to the actor session. Active and queued identities are
deduplicated directly. Settled identities are retained in bounded history
without unsafe in-session eviction. The settled-plus-in-flight bound rejects
new unique work until the actor is idle; then the actor retires the session,
clears that session's bounded history, and issues a new session identity.
Descriptors from the retired session are rejected, so an evicted identity is
never silently re-executed in the same session. This is intentionally not
restart-safe idempotency: a new runtime actor session that receives a rebuilt
raw identity requires a durable effect fence in a later phase.

## Runtime composition and non-goals

`LilavelRuntime` starts and stops the actor even when no semantic request is
admitted. The default composition is inert and does not migrate any existing
production path. Discord reactive/Core routing, normal CLI conversation,
`MindAppraiser`, `AutonomousCognitionRunner`, temporal polling, and the
MIND-1C/1D production seams remain unchanged for later 1F-C/D/E work.

`ConversationCore` retains canonical conversation/history semantics and
successful assistant commit rules. `CognitionEpisodeRunner` remains
effect-free and proposal-producing. `ProposalApplicationCoordinator` remains
the trusted state/action/temporal effect boundary. P4 tool/runtime authority
is unchanged.

## Evidence and validation boundary

Actor evidence is bounded correlation metadata: request and episode sequence,
request ID, source kind, priority, lifecycle status, cancellation/preemption
flags, and safe reason codes. It does not store prompts, user content, model
outputs, tool arguments/results, credentials, or exception bodies. Deterministic
actor fixtures prove single-active serialization, priority preemption,
pre-handle cancellation, duplicate fencing, mailbox bounds, failure
containment, uncertain settlement fail-closed behavior, shutdown, bounded
evidence privacy, and session rollover. They do not prove live providers,
Discord behavior, restart-safe replay, or external-effect rollback.
