# COG-V1-E2 — Production Ambient Intervention Integration

Status: PASS

## Baseline and commits

- Expected baseline: `b88877a6278ebc250b71ff8d093d52579322f30f`
- Exact implementation parent: `b88877a6278ebc250b71ff8d093d52579322f30f`
- Implementation: `0823623` (feat(runtime): integrate ambient intervention effect guard)
- Closeout: documentation/ADR commit follows the implementation commit
- E1 implementation: `164224e57dfa6082a3c32c9382e8697735ca1973`
- E1 parent: `c73f95fa81fdb74b5018fa1d1437b7b67a379d54`
- Push: not performed

The preflight HEAD matched the expected baseline exactly, the E1 parent chain
was `c73f95f → 164224e → b88877a`, and the worktree was clean before E2 work.

## Implemented contract

External ambient `THINK` episodes use one `LocalCognitionEngine` model
inference. The strict top-level candidate schema is:

```text
{
  intervention: NONE | RESPOND | INTERJECT,
  reason_codes: bounded known CognitionReasonCode values,
  disposition: null or bounded HOW fields,
  utterance: null or bounded non-empty text,
  state_proposals: existing bounded create_intention list,
  temporal_proposals: existing bounded temporal list
}
```

The ambient disposition contains `aim`, `stance`, `engagement`, `directness`,
`desired_length`, `humor_allowed`, `question_policy`, and `initiative`.
Top-level `reason_codes` supplies the evidence. `NONE` requires null
disposition and utterance and compiles to no presentation action.
`RESPOND`/`INTERJECT` require a complete disposition and utterance and compile
to exactly one existing inert `ActionProposal.SPEAK`.

Unknown keys/enums/reasons, duplicate JSON keys, malformed combinations,
oversized results, and transport fields fail closed to a quiet candidate.
No destination, channel, surface, tool, executor, social-permission fact,
floor, freshness, budget, or handled-state field is model-controlled.

## Authority and lifecycle

`CognitionEpisodeRunner` remains the only model-backed `NON_USER` cognition
route and keeps the outcome inert. `ProposalApplicationCoordinator` performs
effect-time revalidation immediately before the existing P4 batch session.
Composition supplies a fresh runtime-owned `SocialPermissionContext`; missing
trusted context denies speech. The E1 policy is reused without a second
disposition-planner call.

Destination derives only from the trusted runtime
`ActionProposalKind.SPEAK → tool name` mapping and P4 registry/session. There
is no ambient action or presenter lane and no cognition-layer Discord/network
send. The guard applies to any `SPEAK` source, including internal and temporal
producers.

State and temporal proposals commit according to their existing application
rules before action execution. A stale/denied `SPEAK` is settled as a denied
action and does not discard valid state or temporal effects; the aggregate is
reported as partial when internal effects succeeded. Only a confirmed P4
speech effect updates recent-speech and interjection-budget accounting.
Unknown, failed, and shadow outcomes do not.

## Rollout

```text
OFF    ambient cognition may run; external ambient SPEAK is denied
SHADOW full candidate/validation/current-revalidation pipeline; no external effect
LIVE   validated and currently permitted SPEAK reaches existing P4 authority
```

The production default is `OFF`. The canonical CLI explicitly selects `OFF`;
Discord live startup remains unchanged and LIVE is not enabled without a
trusted composition resolver. `STAY_SILENT` remains only for narrow legacy
idle compatibility; ordinary ambient `NONE` creates no stay-silent action.
Voice interrupt/VAD/barge-in remains deferred.

## Validation

All deterministic checks below were run after implementation:

| Check | Result |
|---|---|
| Root and runtime lock checks | PASS |
| Runtime Ruff check / format check | PASS |
| Runtime strict Pyright | PASS |
| Focused E2 tests | PASS — 27 passed |
| Focused E2 + E1 + D1 + D2 regression set | PASS — 67 passed |
| Full runtime suite | PASS — 307 passed |
| Shared contracts package | PASS — 7 passed |
| Discord adapter package | PASS — 95 passed, 10 host deprecation warnings |
| Architecture guard | PASS |
| Documentation integrity | PASS |
| `git diff --check` | PASS |
| Live provider / live ambient LIVE composition | UNVERIFIED |

The previously documented Python 3.14 async-teardown hang did not reproduce in
the final full runtime run; it is not relabeled as a failure for this phase.

## Exit gate

PASS. Ambient external `THINK` now follows:

```text
Observation → Attention → Cognition → Intervention → Disposition → Authorized Effect
```

Direct `USER` and ambient `NON_USER` remain distinct routes beneath one
`SemanticActor`; attention, application authority, Character v0, and D2 user
disposition defaults remain intact. No push was performed.
