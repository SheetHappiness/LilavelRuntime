# COG-V1 Convergence — Selective Disposition Architecture

Status: `CLOSED`

## Why convergence was required

The stale COG-V1-C branch was merged after the repository had already advanced
through the integrated COG-V1-D1/D2 USER lifecycle and the COG-V1-E1/E2 ambient
intervention path. That merge left a second Core-only selective-disposition
implementation beside the already-integrated runtime architecture. The two
implementations used different route vocabularies (`FAST | DELIBERATE` versus
`FAST | PLAN`) and different context, policy, validation, and failure
contracts.

## Canonical implementation

The pre-merge integrated stack remains canonical:

```text
SemanticActor USER
  → ConversationCore.prepare_turn()
  → UserDispositionResolver
  → DeliberationDecision.FAST or PLAN
  → immutable run-bound TurnBehavior
  → ConversationCore.start_prepared_run()
  → normal streaming generation
```

Core owns `WorkingState`, `ResponseDisposition`, `CognitionPolicyDecision`,
`DispositionCandidate`, `TurnBehavior`, `TurnBehaviorResolution`,
`TurnBehaviorSource`, `DeliberationDecision`, `DeliberationContext`,
`DeliberationPolicy`, and `default_turn_behavior()`. Runtime owns
`DispositionPlanner`, `UserDispositionResolver`, `DeliberationMode`, and the
deterministic D2 routing policy. `DEFAULT_ONLY` remains the production
default; `SELECTIVE` is explicit opt-in; `ALWAYS_PLAN` remains a validation
mode. FAST makes zero planner calls and PLAN makes exactly one existing planner
call. Direct USER turns remain `RESPOND`.

Ambient intervention remains the separate COG-V1-E1/E2 NON_USER path. It is not
folded into USER disposition routing.

## Removed duplicate architecture

The duplicate Core-only `lilavel_core.disposition` and
`lilavel_core.disposition_eval` modules were removed, along with their public
exports and duplicate test framework. This retires `DispositionRoute`,
`DispositionRoutingEvidence`, `DispositionContext`, `DispositionCase`,
`DispositionResolution`, duplicate policy protocols and implementations,
duplicate failure/validation contracts, and the `FAST | DELIBERATE` aliases.
No compatibility alias was added because the merged code is internal and the
canonical D2 route vocabulary is `DeliberationDecision.FAST | PLAN`.

## Preserved work

The five bounded reason concepts introduced by the stale branch remain in the
single Core `CognitionReasonCode` vocabulary:

- `SIMPLE_REQUEST`
- `HIGH_SOCIAL_STAKES`
- `MULTIPLE_PLAUSIBLE_MOVES`
- `INTERVENTION_UNCERTAIN`
- `REVERSIBLE_ASSUMPTION`

The useful direct-USER evaluation cases remain in the existing runtime D2
corpus: simple definition, factual and technical FAST cases; contradiction;
evidence update; material and reversible ambiguity; vulnerable/social nuance;
selective curiosity; the tailoring keyword trap; and genuine tailoring
affinity. The ambiguity-cost and tailoring-context counterfactuals are
represented by the existing D2 scenario metadata and benchmark. Cases already
covered by D2, including evidence-update and no-unnecessary-question
trajectories, were not duplicated as a second fixture framework. Ambient
direct-versus-ambient and ambient self-repair cases remain owned by E1/E2,
consistent with the separate NON_USER lane.

## Invariants and evidence

CharacterCanon remains immutable, WorkingState remains run-local,
ResponseDisposition remains per-response, and behavior is bound to exactly one
prepared run. SemanticActor remains the single semantic admission authority;
ConversationCore remains the canonical conversation owner; FAST adds no model
call; PLAN adds only the existing one planner call; and no third model lane or
shared mutable disposition was introduced. Discord and CLI composition are
unchanged, and no live feature was enabled.

## Validation

- Exact baseline and clean preflight: `PASS` — `HEAD` was
  `5c7d48505ccd723fec9addb2bdd2b474e047d243`; no conflict markers were
  present.
- Core lock, full pytest, and focused cognition/conversation checks: `PASS` —
  195 passed, 4 documented Linux platform skips; the focused post-removal
  selection passed 42 tests.
- Runtime lock, focused D2/planner checks, Ruff, format, and strict Pyright:
  `PASS` — 29 focused tests passed.
- Runtime residual suite: `PASS` — 355 passed, 3 deselected using the exact
  pre-existing async-teardown exclusions documented in `docs/VALIDATION.md`.
- Root lock, documentation integrity, architecture guard, and
  `git diff --check`: `PASS`.
- Full runtime suite: `FAIL` — the host stalled during the same three
  documented async-teardown tests; this does not establish a convergence
  failure.
- Live provider, live Discord, Windows, restart, and production planner
  integration: `UNVERIFIED`.

## Unknowns and exit gate

Live provider behavior, live ambient delivery, cross-process replay, and
platform-specific behavior remain `UNVERIFIED`. The exit gate is satisfied
for deterministic convergence: one canonical FAST/PLAN route contract remains,
the duplicate Core-only architecture and exports are retired, useful direct
USER eval coverage and bounded reason concepts remain in their canonical
infrastructure, and the D1/D2 USER and E1/E2 ambient ownership boundaries are
unchanged.

Implementation SHA: `9602b11161f39f0a5d067c90322b07e4b55d1a20`
