# ADR-024 — PROACTIVE-V0-R3 deferred commitment semantics

## Status

Accepted and implemented at PROACTIVE-V0-R3.

## Decision

Runtime-owned intentions carry exactly one bounded semantic kind:

- `initiative` — discretionary proactive work; idle cognition remains
  silence-biased and may produce `speak` or `stay_silent`;
- `deferred_commitment` — a concrete future USER-requested Lilavel action that
  the assistant accepted, did not fulfill in the completed turn, and can still
  fulfill through the currently surfaced capability.

The appraisal contract carries that kind explicitly. The old untyped appraisal
shape remains accepted only as a compatibility interpretation of `initiative`;
it can never create a deferred commitment. MindState stores the bounded kind
with existing Core user/assistant provenance and no environment metadata.

An idle timer expiry is the runtime-owned due condition. A due deferred
commitment enters the existing `SemanticActor` cognition path with fulfillment
guidance. Cognition must return bounded internal `fulfill` text, which is
compiled to the existing inert `ActionProposal(SPEAK)`; no external action enum
or second semantic lane is introduced. A due deferred commitment supplies the
trusted `response_obligation`/continuity fact at effect-time policy
revalidation. The model cannot manufacture that fact, select a destination, or
bypass `ProposalApplicationCoordinator`.

## Consequences

Ordinary initiative may still remain silent, and a deferred commitment is not
created for a future topic, casual future tense, a possible later user return,
or a reminder already delivered in the current assistant turn. A due deferred
commitment receives one cognition/application opportunity. Confirmed external
delivery consumes the opportunity; denied, failed, unknown, cancelled, and
invalid outcomes do not trigger automatic retries. Internal proactive output
remains outside canonical `ConversationCore` history.

This decision does not add a scheduler, persistence, restart recovery, memory,
keyword matching, arbitrary model tools, or automatic external sending.
