# ADR-021: COG-V1-E2 ambient intervention effect guard

## Status

Accepted for COG-V1-E2.

## Context

COG-V1-E1 established deterministic ambient intervention semantics but stopped
before speech externalization. Ambient cognition needs a useful model-backed
candidate while preserving the runtime's authority over current social state,
destination, tool execution, and effect settlement. A decision made against a
stale cognition snapshot must not become speech merely because the model once
selected `RESPOND` or `INTERJECT`.

## Decision

Use one model-backed ambient cognition inference with an exact bounded schema:

```text
intervention
reason_codes
disposition?     # bounded HOW fields only when speaking
utterance?       # only when speaking
state_proposals
temporal_proposals
```

`NONE` has no presentation payload and compiles to zero `SPEAK` proposals.
`RESPOND` and `INTERJECT` require validated disposition and bounded utterance
and compile to the existing inert `ActionProposal.SPEAK`. The model cannot
select a destination, channel, surface, tool, executor, permission, or social
fact. Intervention and disposition remain separate concepts even though one
inference may emit both.

`CognitionEpisodeRunner` remains the only model-backed `NON_USER` cognition
route and returns advisory inert outcomes. Immediately before P4 action
execution, `ProposalApplicationCoordinator` asks composition for a fresh
runtime-owned `SocialPermissionContext` and re-runs the E1 policy. Missing
context denies speech. State and temporal effects already committed from the
same valid outcome are not rolled back when its `SPEAK` is denied.

Ambient rollout is explicit: `OFF`, `SHADOW`, or `LIVE`. `OFF` is the
production default. `SHADOW` runs the same candidate, validation, and current
revalidation pipeline as `LIVE`, records content-free evidence, and suppresses
the external effect without consuming confirmed-speech accounting. `LIVE`
allows only the validated, currently permitted `SPEAK` to reach existing P4
authority. Every effectful `SPEAK` source uses the guard, including future
internal and temporal producers.

## Consequences

- Cognition can produce bounded ambient value without acquiring effect authority.
- Current social state, not cognition-time permission, decides whether speech escapes.
- Existing `ActionProposal`, `ProposalApplicationCoordinator`, tool registry,
  and P4 session remain the sole effect path.
- Confirmed effects, not shadow, failed, or unknown results, update recent-speech
  and unsolicited-budget accounting.
- `STAY_SILENT` remains narrow compatibility debt for legacy idle behavior;
  ordinary ambient silence has no effect proposal.
- Voice interruption, VAD, and barge-in remain deferred.

## Rejected alternatives

- A serial intervention-model → disposition-planner → response-model chain was
  rejected because it adds latency, stale-context exposure, and duplicate
  semantic authority.
- A cognition-time-only social check was rejected because queued work can race
  with user activity, backoff, floor, freshness, or surface changes.
- A new ambient speech action/presenter bus was rejected because the existing
  P4 action route already owns target selection and effect settlement.
