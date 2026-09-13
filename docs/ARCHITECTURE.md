# Architecture

This document records the current ownership boundaries of the canonical
`LilavelRuntime` repository. Lilavel has a minimal persistent-agent kernel plus
the proven conversational foundation, a replaceable Discord environment
adapter, the bounded P5-B1/MIND-0 local presence slice, and the MIND-1A/MIND-1B
observation-admission and observation-to-cognition boundaries. COG-V1-B adds
the deterministic attention evidence/policy layer behind MIND-1B. MIND-1C adds a
bounded, effect-free cognition episode that ends at inert proposals; MIND-1D
adds the separate trusted application boundary; MIND-1E adds one-shot temporal
intention admission and time-to-cognition dispatch; MIND-1F-B adds one
runtime-owned, character-wide semantic admission actor; MIND-1F-C composes that
actor with the new generic/temporal MIND path; MIND-1F-D1 moves production
Discord reactive conversation admission under that actor; MIND-1F-D2 moves
normal local CLI conversation admission under the same actor; MIND-1F-E moves
conversation-completion appraisal and deterministic idle opportunities into the
same actor as `INTERNAL` `NON_USER` cognition. COG-V1-E2 completes the
cognition track by wiring model-backed ambient intervention candidates through
the existing inert outcome and application boundaries, with current-state
social revalidation immediately before every external `SPEAK` effect. Broader
autonomous capabilities are not implied by these slices. CTX-V1-A adds a
stable Core-owned `OperatingCanon` and inert, runtime-owned `ContextFrame`
contracts/projections. CTX-V1-B adds the read-only runtime builder and typed
purpose selection; neither phase injects a context frame into a production
`ModelRequest`.

## Top-level product boundary

The `LilavelRuntime` kernel owns the top-level process lifecycle, one
character-wide `SemanticActor`, bounded world event admission, a recent
in-memory observation window, a deterministic attention/cognition gate,
registered environment tasks, actor-owned CLI and reactive conversation admission, the
optional local presence component, Core/ModelRuntime session lifecycle,
selection of the source environment for typed presentation actions, and the
MIND-1D proposal application coordinator. MIND-1F-C adds an optional
runtime-owned MIND execution adapter and deadline-driven temporal host: all
generic and temporal non-conversational cognition enters the actor before the
runner, and completed outcomes settle through the trusted application
coordinator. MIND-1F-D1 adds the runtime-owned conversation episode adapter:
positive Discord DM gates submit `USER` work to the same actor, while
`CoreConversationRouter` and `ConversationCore` retain session and canonical
conversation semantics. MIND-1F-D2 submits normal local CLI input as `USER`
work to that same actor and uses the shared conversation execution seam for the
CLI's `local-cli` Core session. MIND-1F-E keeps only narrow local-surface
presence plumbing and deterministic idle-opportunity production; internal
appraisal and idle cognition enter the actor as `INTERNAL` `NON_USER` work.
MIND-0 adds bounded in-memory intentions
and recent self-actions to the local CLI only; it adds no general scheduler,
attention loop, world model, durable memory, or arbitrary model-selected tool
authority. MIND-1C adds an effect-free, runtime-owned bounded cognition
episode seam whose successful result is an inert proposal set; MIND-1D owns
proposal application and authorization. MIND-1E adds only a bounded in-memory
temporal coordinator; it does not add recurring schedules, general attention,
or durable agent state.

The canonical semantic graph after MIND-1F-E is:

```text
USER     → SemanticActor → ConversationExecutionAdapter → ConversationCore
NON_USER → SemanticActor → CognitionEpisodeRunner → CognitionOutcome
                              → ProposalApplicationCoordinator
```

There is no third semantic model lane. `PersistentPresenceRuntime` produces
only runtime-owned internal opportunities and local presentation plumbing in
the canonical composition.

The intended direction is:

```text
world observations
        │
        ▼
Discord adapter ──► WorldEvent ──► LilavelRuntime
                                      │
                                      ├── admission ──► ObservationWindow
                                      │                  (Observation)
                                      │
                                      └── explicit cognition gate
                                                   │
                                      ┌────────────┴────────────┐
                                      │                         │
                               NO_COGNITION              CognitionTrigger
                                      │                         │
                                      ▼                         ▼
                                    STOP                explicit reactive step
                                                   │
                                                   ▼
                              SemanticActor (USER)
                                                   │
                                                   ▼
                         ConversationExecutionAdapter
                                                   │
                                                   ▼
                           CoreConversationRouter
                                                   │
                                                   ▼
                               ConversationCore
                         │
                         ▼
                   ModelRuntime
                         │
                         ▼
                  model-sidecar
                         │
                         ▼
             typed presentation actions
                         │
                         ▼
                 Discord adapter
```

The existing positive trigger also has a separate MIND-1C seam:

```text
CognitionTrigger
  → frozen CognitionContext
  → serialized CognitionEpisode
  → validated inert CognitionOutcome
  → STOP
```

This episode path is intentionally effect-free and does not apply proposals.
The positive Discord DM route now enters the same actor through a separate
conversation execution adapter; the actor owns admission/serialization and
the router/Core path retains conversation semantics.

## COG-V1-B deterministic attention

Attention is evidence selection, not social permission. The runtime keeps
attention, intervention, and disposition separate:

```text
admitted Observation
  → AttentionEvidenceExtractor
  → AttentionEvidence
  → DeterministicAttentionPolicy
  → DROP | NOTE | THINK
```

The extractor uses exact runtime-owned event-route profiles and ignores
external payload text/fields and `EventTrust` when minting hard signals. The
policy is ordered and boolean: direct address, critical events, and trusted
continuity think first; high relevance plus high novelty thinks next; low
relevance or repetition with no new value drops; everything else is noted.
There is no weighted salience score, hidden threshold, RNG, model call,
cooldown, quiet-hours, conversation-floor, or interruption rule. Interest
affinity is an optional evidence seam, never a keyword rule, and never thinks
by itself.

`DeterministicAttentionCognitionGate` preserves the MIND-1B result shape. Only
observations with their own `THINK` verdict become bounded
`CognitionTrigger` evidence; `NOTE` and `DROP` become `NO_COGNITION`. Direct
messages retain the existing USER/Core route. Ambient `THINK` uses the
existing NON_USER/MIND route, and no new semantic/model or effect lane is
introduced. `NOTE` is transient awareness, not memory or deferred work.

## COG-V1-C model-backed disposition planning

The standalone `DispositionPlanner` is a bounded model-backed evaluation seam.
COG-V1-D1 adds an explicit, opt-in integration seam for the production USER
route, while the production default remains `DEFAULT_ONLY`. The planner answers
only how an already direct user turn should be approached. The direct-user
invariants remain runtime-owned and fixed: `attention=THINK` and
`intervention=RESPOND`.

```text
bounded recent canonical context
        + deterministic Character v0 planner projection
                              │
                              ▼
                    DispositionPlanner
                              │
                              ▼
                  strict DispositionCandidate
                              │
                              ▼
             runtime-owned focus compiler + Core values
                              │
                              ▼
       CognitionPolicyDecision(THINK, RESPOND, disposition)
```

The candidate reuses Core's `ResponseDisposition`, `WorkingState`, and bounded
`CognitionReasonCode` vocabulary. Its parser rejects malformed, duplicate,
unknown, nested, oversized, or otherwise out-of-schema JSON and stores no raw
model reasoning. `WorkingState.focus` is never model-authored: runtime-owned
fixed mappings compile it from the validated aim and reason codes before any
trusted guidance projection. The planner receives at most the last four
canonical role/text messages, and its character projection is mechanically
derived from `IdentityCanon` while excluding voice and representative dialogue
examples. COG-V1-C itself does not call this planner from Core, CLI, Discord,
or `SemanticActor` production paths; D1 supplies the caller-owned integration
described below.

## COG-V1-D1 run-bound USER disposition integration

Each accepted USER turn has one immutable `TurnBehavior` owned by its logical
`ConversationRun`. The behavior contains only validated `WorkingState`,
`ResponseDisposition`, bounded reason codes, and `DEFAULT` or `PLANNER` source;
it contains no raw planner text, provider handle, memory, tool, or action
authority. Shared mutable `current_disposition`-style state is forbidden: a
superseded planner result must never be observable by a successor run.

The lifecycle is:

```text
USER SemanticEpisode
  → ConversationCore.prepare_turn()       # persist user, create run, supersede predecessor
  → UserDispositionResolver                 # DEFAULT_ONLY or explicit ALWAYS_PLAN
  → validated immutable TurnBehavior bound to that run
  → ConversationCore.start_prepared_run()
  → response generation, streaming, and normal commit semantics
```

Planner resolution occurs after canonical user acceptance and before response
generation. Invalid output, provider failure, timeout, or contained planner
cancellation records bounded fallback evidence and uses the deterministic
default behavior. If cancellation containment is uncertain, the existing
fail-closed actor/Core semantics apply and no successor response is started.
The actor's USER cancellation token settles planner work before the USER
episode returns; no USER-to-USER preemption policy is added here.

Both CLI and Discord use this prepared-run seam through their existing
`ConversationExecutionAdapter`/`CoreConversationRouter` USER spine. Production
composition uses `DEFAULT_ONLY`; `ALWAYS_PLAN` is an explicit injected seam for
D1 validation and later D2 routing work. D1 does not claim a selective policy.
`ConversationCore.d1_evidence()` exposes bounded source, invocation, accepted /
rejected / fallback reason, monotonic planner duration, structural difference
from default, response-generation start, and first-delta timing evidence. It
stores no user content or raw planner output.

## COG-V1-D2 evidence-backed selective deliberation

COG-V1-D2 adds an explicit provider-neutral `DeliberationDecision` contract:
`FAST` or `PLAN`. `DeliberationContext` contains only the current accepted user
turn and a bounded four-message suffix of canonical context. An injected
`DeliberationPolicy` decides whether the existing `DispositionPlanner` should be
called; it never selects disposition fields and it is synchronous/non-generative.

The run-bound lifecycle is therefore:

```text
prepared USER run
  → DeliberationPolicy.decide(DeliberationContext)
      ├── FAST → default TurnBehavior, zero planner calls
      └── PLAN → existing DispositionPlanner exactly once
  → D1 validation/fallback semantics
  → immutable run-bound TurnBehavior
  → normal response generation
```

`DEFAULT_ONLY`, `ALWAYS_PLAN`, and opt-in `SELECTIVE` modes remain explicit.
`SELECTIVE` uses the injected deterministic rule policy in the repository's
router composition, while the production default remains `DEFAULT_ONLY`.
The rule policy routes on a small set of reviewable multi-token posture signals
(contradiction/evidence pressure, material ambiguity, social or tone ambiguity,
bounded delayed context, and contextual choice). It is not a complexity
classifier, a keyword-only personality trigger, or an LLM router. A policy
failure fails closed to FAST/default so USER availability does not depend on an
optional classifier.

The D2 corpus derives `FAST_SAFE`, `PLAN_HELPFUL`, `PLAN_HARMFUL`, and
`UNRESOLVED` labels from human-authored disposition constraints. Structural
difference from the default is telemetry only; it is not the routing oracle.
The deterministic rule benchmark reached 100% PLAN precision and recall on the
21-case corpus with a 42.9% planner-call rate, compared with 0% recall for
`ALWAYS_FAST` and 100% planner calls for `ALWAYS_PLAN`. This is a small,
repository-local provisional result, so it justifies an explicit opt-in
`SELECTIVE` mode but not a production-default change. Implicit social nuance and
context outside the bounded suffix remain known false-negative domains.

`d1_evidence()` now also records the bounded deliberation decision, mode,
policy identity, and router fallback reason alongside planner invocation,
selected behavior source, planner outcome/duration, structural difference, and
generation timing. No user text or raw planner output is stored.

## COG-V1-E1 ambient intervention contracts

COG-V1-E1 adds a runtime-owned, effect-free boundary after ambient cognition:

```text
ambient THINK cognition
        │
        ▼
InterventionCandidate (advisory semantic value)
        │
        ▼
SocialPermissionContext (trusted current state)
        │
        ▼
DeterministicInterventionPolicy.revalidate()
        │
        ▼
allowed RESPOND/INTERJECT candidate or NONE
```

`Attention` remains only `DROP | NOTE | THINK`; it does not own social timing,
recent-speech backoff, floor state, freshness, or intervention budgets.
`Intervention` answers whether cognition may become external speech now and
reuses Core's `InterventionDecision` values `NONE | RESPOND | INTERJECT`.
`Disposition` remains the separate HOW layer. `NONE` is valid after `THINK`,
and internal state/intention/temporal proposals remain independent of that
silence decision.

The immutable `InterventionCandidate` contains no raw text, prompt, prose,
provider handle, action, or presentation authority. `SocialPermissionContext`
contains only bounded runtime-owned signals: non-user semantic priority/source,
surface availability, current activity, per-class freshness bucket, optional
trusted floor state, recent speech, intervention budget, handled state, social
sensitivity, and independently established response obligation/continuity.
Direct `USER` work is rejected from this ambient policy; the direct route keeps
its fixed `THINK + RESPOND` invariant and its D2 FAST/PLAN behavior.

The policy is ordered and deterministic: hard current-state denials first,
trusted `RESPOND` obligation second, strict material-value `INTERJECT` last,
then `NONE`. `INTERJECT` requires fresh/current/unresolved context, no recent
speech backoff, and explicit supporting evidence such as contradiction,
forgotten constraint, stuck discussion, or unique information. Interest
affinity, a witty thought, and high relevance alone are insufficient. Freshness
uses a reviewable per-event-class `FRESH | AGING | STALE` mapping rather than a
universal timeout; continuity/temporal response may survive `AGING`, while
unsolicited interjection remains `FRESH`-only. E1 intentionally stopped before
effect-time revalidation; E2 supplies the production integration described below.

There is no ambient `INTERRUPT` value. Voice/VAD/barge-in timing is a separate
future domain. E1 adds no model call, generation, proposal application, or
presentation wiring, and production autonomous ambient speech remains
disabled. The human-authored E1 corpus and silence-first metrics live in
`apps/runtime/src/lilavel_runtime/intervention_eval.py`.

## COG-V1-E2 production ambient intervention integration

COG-V1-E2 wires only external ambient `THINK` episodes to one model-backed
candidate inference. The model returns a strict bounded object with top-level
`intervention`, `reason_codes`, optional `disposition`, optional `utterance`,
and the existing bounded state/temporal proposal lists. The top-level reason
codes are also the evidence used for the disposition compilation; destination,
channel, surface, tool, executor, floor, freshness, budget, handled state, and
permission fields are not part of the model schema.

```text
ambient Observation
        │
        ▼
Attention: DROP | NOTE | THINK
        │ THINK
        ▼
SemanticActor: NON_USER
        │
        ▼
CognitionEpisodeRunner
        │
        ▼
LocalCognitionEngine: one model inference
        │
        ▼
validated AmbientInterventionCandidate
        │
        ▼
inert CognitionOutcome
        │
        ▼
ProposalApplicationCoordinator
        │ state/temporal commit, then current SPEAK revalidation
        ▼
existing P4 ActionProposal.SPEAK route or denied speech
```

`NONE` compiles to no presentation action. `RESPOND` and `INTERJECT` each
require a validated disposition and bounded non-empty utterance and compile to
the existing `ActionProposal(ActionProposalKind.SPEAK, content)` shape. The
disposition remains conceptually separate HOW data even though intervention
and disposition are produced by the same inference. `CognitionEpisodeRunner`
and the model engine remain advisory and inert; `ProposalApplicationCoordinator`
is still the only effect authority.

Immediately before P4 batch execution, the coordinator rebuilds a
runtime-owned `SocialPermissionContext` through the composition resolver and
re-runs the deterministic E1 policy. A candidate that was valid during
cognition can therefore be denied for current stale/handled state, backoff,
floor, unavailable surface, or exhausted budget. Missing trusted context fails
closed. Denied speech is action-level settlement: already-valid state and
temporal proposals remain applied and are reported alongside the denied
`SPEAK`; cognition itself is not rolled back. This guard applies to every
effectful `SPEAK` source, including future internal or temporal producers.

The model never selects a destination. The trusted action-tool mapping supplied
by runtime composition selects the existing target, and the P4 registry/session
performs the only external effect. A confirmed P4 effect, and only a confirmed
effect, updates runtime-owned recent-speech and unsolicited-intervention
accounting. Shadow decisions do not consume that accounting. The existing
`STAY_SILENT` proposal remains only for narrow legacy idle compatibility; an
ordinary ambient `NONE` does not create it.

Ambient speech has explicit rollout modes:

```text
OFF    cognition may run; ambient SPEAK is denied
SHADOW full candidate and current revalidation pipeline; would-speak is recorded, no effect
LIVE   only validated and currently permitted SPEAK reaches existing P4 authority
```

The conservative production default is `OFF`. The canonical CLI explicitly
selects `OFF`, and no Discord live startup is changed to enable ambient
speech. `LIVE` remains a composition choice that requires a trusted current
social-context resolver; no unsafe Discord destination shortcut is invented.
There is no new semantic lane, no direct Discord/network send from cognition,
and voice interrupt/VAD/barge-in remains deferred.

With E2, COG-V1 is architecturally complete as:

```text
Observation → Attention → Cognition → Intervention → Disposition → Authorized Effect
```

Direct `USER` work and ambient `NON_USER` work remain distinct paths beneath
one character-wide `SemanticActor`. Direct USER disposition defaults and D2
selective behavior are unchanged.

## CTX-V1-A operating canon and context contracts

CTX-V1-A keeps four ownership domains separate:

```text
CharacterCanon  = who Lilavel is
OperatingCanon  = how existing inside LilavelRuntime works
ContextFrame    = what current situation is relevant now
ConversationCore = canonical conversational evidence and history
MindState       = runtime-owned working commitments and intentions
Memory          = future durable learned knowledge
Attention/COG   = whether and how to think or speak
```

`OperatingCanon` lives in Core at
`apps/core/src/lilavel_core/operating.py`, alongside the identity and guidance
contracts but not inside `CharacterCanon`. Its immutable, bounded laws describe
one persistent character across interfaces, presented-information limits,
cognition/expression separation, advisory proposals, valid silence, temporal
reconsideration, runtime-surfaced capabilities, and purpose-specific views.
It contains no personality, current time, current surface, current capability,
intention, memory, relationship, provider, or model facts. Its compiler emits
stable text only.

`ContextFrame` lives in Runtime at `apps/runtime/src/lilavel_runtime/context.py`.
It is an immutable, bounded, provider-neutral view for exactly one
`ContextPurpose`, not a store or transcript. Its typed subcontracts cover
environment/surface, current interaction and participants, narrow intention
references, runtime-declared capability availability, advisory social state,
optional temporal facts, and bounded trusted source references. It does not own
or copy `ConversationCore` messages, `MindState`, `ObservationWindow`, memory,
or E1/E2 effect permission.

Context values distinguish `KNOWN`, `KNOWN_EMPTY`, `UNKNOWN`, and
`UNAVAILABLE`. Collection contracts require `KNOWN_EMPTY` for a known empty
collection and require a bounded reason for unknown or unavailable data.
Capability items accept only runtime-declared or validated-adapter provenance;
external payload provenance cannot enter a frame. Future adapter contributions
must pass through runtime trust validation and a runtime-owned builder.

The purpose taxonomy is deliberately small: `USER_RESPONSE`,
`AMBIENT_COGNITION`, `INTERNAL_APPRAISAL`, and `TEMPORAL_WAKE`. Deterministic
projections omit irrelevant domains by purpose. In particular, user-response
views omit ambient-only other-surface and social state, internal appraisal does
not imply expression, and temporal wake may include current time only when its
purpose requires it. Social context is advisory and cannot authorize an
effect; E2 effect-time revalidation remains authoritative.

Frame IDs, scope IDs, capture timestamps, and provenance references are
volatile/runtime metadata and are excluded from the stable OperatingCanon
output. ContextFrame has no canonical messages, summaries, or memory records.

### CTX-V1-B runtime builder

`ContextFrameBuilder` lives in
`apps/runtime/src/lilavel_runtime/context_builder.py`. It receives small typed
resolver seams and makes one immutable frame without retaining their results or
mutating an owner. The authoritative map is deliberately narrow:

| Frame domain | Authoritative seam | If not established |
| --- | --- | --- |
| environment/surface | runtime composition's provider-neutral environment resolver | `UNKNOWN` |
| interaction/activity/participants | runtime or adapter interaction resolver | `UNKNOWN` |
| intentions | `MindStateIntentionResolver` plus an explicit scope/current-intention relation | `KNOWN_EMPTY` only for an empty/matched-empty MindState; otherwise `UNKNOWN` |
| capabilities | runtime declarations and `VALIDATED_ADAPTER` contributions | `UNKNOWN`; conflicts are `UNAVAILABLE` |
| social | typed E1/E2 current `SocialPermissionContext` resolver | `UNKNOWN` |
| temporal | current runtime clock and optional `TemporalCoordinatorResolver` | current time remains current; wake relation is unknown |
| source refs | trusted runtime provenance references | omitted |

The builder never reads canonical messages, arbitrary observation payloads,
installed tool lists, model output, or Discord-specific classes. The kernel
exposes a default builder backed by the configured `MindState` and
`TemporalCoordinator`; environment, interaction, capability, social, and
provenance facts remain unknown until a trusted composition supplies the
corresponding resolver. No generic runtime-state bag was added.

Selection is deterministic and minimal. `USER_RESPONSE` keeps current
environment, direct interaction, explicitly linked intentions, and current
capabilities; it omits other-surface activity, social state, unrelated
observations, and ordinary temporal data. `AMBIENT_COGNITION` adds current
other-surface activity, advisory social state, and trusted source references,
but has no direct conversation history. `INTERNAL_APPRAISAL` keeps only current
activity and explicitly linked intentions; it does not imply speech or tools.
`TEMPORAL_WAKE` uses current environment/activity, linked intentions,
capabilities, trusted wake/source provenance, and the injected current clock;
it never reconstructs the scheduled world state. A USER temporal continuation
may retain a trusted temporal relation in the frame, while the ordinary user
projection still does not render precise time.

The projection compiler has a stable block order: purpose, environment,
interaction, relevant intentions, social context when selected, temporal
context when selected, then capabilities. It renders no frame IDs, timestamps,
opaque source refs, or internal provenance tokens by default. Current time is
rendered only for `TEMPORAL_WAKE`. Resolver exceptions fail closed to bounded
`UNKNOWN` views; malformed typed results and untrusted references are rejected.
Intentions are structurally capped at 4, capabilities at 8, participants at 8,
source refs at 8, projection blocks at 8, and rendered UTF-8 output at 8 KiB.
Intention prose is clipped at its field bound before compilation; the final
projection is never byte-sliced. Social context is advisory evidence only and
cannot grant an effect; E2 revalidates social permission at application time
because a captured frame may be stale.

CTX-V1-B leaves production request composition unchanged. CTX-V1-C is the
handoff for deliberate USER/NON_USER model-request integration, prompt-layer
ordering, caching-aware stable prefixes, and freshness/behavior evaluation.

## MIND-1F-B semantic admission actor

Each `LilavelRuntime` instance composes exactly one provider-neutral
`SemanticActor` for the character-wide scope. Conversation subjects remain
separate inside their specialized conversation executors; the actor is not
keyed by environment or subject. Its bounded mailbox has only two priority
classes: `USER` and `NON_USER`, with FIFO order within each class. One
actor-owned worker admits at most one semantic episode at a time. MIND-1F-D1
uses the actor for production Discord reactive conversation; MIND-1F-D2 uses
the same lane for normal local CLI conversation; temporal and generic MIND
clients also use it. MIND-1F-E adds runtime-owned internal appraisal and idle
opportunities to this same `NON_USER` lane; the actor remains unaware of their
surface meaning.

When user-priority work arrives during active non-user work, the actor requests
cooperative cancellation through an actor-owned token and waits for the active
executor task to settle before the user successor can start. A cancellation
race before the specialized executor binds its own handle is covered by the
same token. If settlement or containment is uncertain, the actor becomes
`POISONED` and rejects queued successors; it never claims rollback of effects
that may already have occurred.

Request fencing uses the stable request identity together with the current
actor session identity. Settled history is bounded without unsafe in-session
eviction: when the history reaches capacity while idle, the actor retires the
session and starts a new one. Old-session descriptors are rejected, while a
new runtime actor session does not claim restart-safe idempotency. Evidence is
bounded correlation metadata only: request and episode IDs, source, priority,
status, cancellation/preemption outcome, and safe reason codes. Raw prompts,
user content, model results, tool arguments/results, and credentials are not
stored.

The actor is composed and lifecycle-owned by `LilavelRuntime` but inert by
default. MIND-1F-C activates it for explicit generic/temporal MIND composition,
MIND-1F-D1 uses it for production Discord reactive conversations, and
MIND-1F-D2 uses it for normal local CLI conversations. The MIND path is:

```text
generic CognitionTrigger ─┐
temporal CognitionTrigger ─┼─► LilavelRuntime.submit_cognition()
internal CognitionTrigger ┘             │
                                       ▼
                              SemanticActor (NON_USER)
                                       │
                                       ▼
                              MindExecutionAdapter
                                       │
                         ┌─────────────┴─────────────┐
                         ▼                           ▼
                CognitionEpisodeRunner       ProposalApplicationCoordinator
                  (inert outcome)              (trusted application)
```

The actor fences `CognitionTrigger.trigger_id`, so duplicate submission in one
actor session admits at most one cognition/application sequence. The adapter
returns bounded `MindExecutionResult` metadata only after the runner and, when
present, application coordinator have settled. Known runner/application
failures settle as terminal actor outcomes; uncertain containment remains
fail-closed under the actor rules. `ConversationCore` keeps canonical history
and assistant commit semantics; `CognitionEpisodeRunner` remains effect-free
and proposal-producing; `ProposalApplicationCoordinator` remains the trusted
effect boundary.

MIND-1D begins only after a runner-produced completed outcome. The application
coordinator front-loads structural validation for the complete proposal set,
checks the outcome's runtime scope and state snapshot version, then commits
trusted state deltas atomically before executing any action. Actions are
compiled from proposal kinds into application-selected canonical tool names
and model-owned content; the P4 registry, exposure, authorization, liveness,
argument validation, executor, settlement, and effect-certainty rules remain
authoritative. The local state and external-action subpaths have separate
statuses and evidence. External effects are never claimed to be rolled back.

RUNTIME-H1 adds a coordinator-owned application permit to the completed
outcome. The permit carries a process-local authenticity capability plus a
versioned SHA-256 digest of the canonical scope, episode, trigger, state,
action, and temporal proposal encoding. The capability is not model-controlled,
not serialized as authority, and is excluded from evidence. The coordinator
retains only a bounded settled replay window for the current application epoch;
once that window rotates, every prior-epoch permit—including one that was
issued but never applied—is permanently retired. Rotation occurs only while
holding the same settlement lock used by application, so no unsettled
application can be forgotten. New permits continue in the new epoch, and a
retired permit fails closed before state, temporal, or tool effects. This is
at-most-once protection for valid runtime-issued permits during one coordinator
or runtime lifetime; replay/idempotency across process restart is UNVERIFIED.

```text
CognitionOutcome (completed, scoped, versioned)
  → coordinator-owned ApplicationPermit
  → ProposalApplicationCoordinator
  → validate the complete proposal set
      ├── StateProposal → trusted MindStateDelta → atomic MindState apply
      └── ActionProposal → trusted ToolCall → existing P4 runtime → ToolResult
      └── TemporalProposal → TemporalCoordinator → runtime-owned WakeIntent
```

## MIND-1F-D1 Discord conversation convergence

The production reactive Discord path now uses the character-wide actor:

```text
Discord DM
  → WorldEvent
  → observation admission
  → direct-message cognition gate
  → CognitionTrigger / stable observation-derived request ID
  → SemanticActor (USER)
  → ConversationExecutionAdapter
  → CoreConversationRouter
  → ConversationCore.prepare_turn()
  → run-bound disposition resolution
  → ConversationCore.start_prepared_run()
  → ModelRuntime
  → typed presentation actions
  → Discord adapter
```

`ConversationExecutionAdapter` is a narrow runtime-owned seam. It supplies the
existing router run as one actor episode, translates the actor cancellation
token into router/Core cancellation, and joins Core and presentation
settlement before the actor lane is released. It does not own a transcript,
subject map, provider request, or presentation state. A duplicate trigger ID
is fenced by the actor even when the Discord edge's process-local message
deduplicator is bypassed.

The actor is character-wide and has one active episode. Discord human work is
`USER` priority; generic and temporal MIND work is `NON_USER`. User admission
requests cooperative cancellation of active non-user work and waits for its
contained settlement. Non-user work submitted while a conversation is active
waits in the actor's FIFO lane. A user item does not introduce another priority
class and therefore follows deterministic FIFO ordering behind an earlier user
episode.

The actor owns admission, priority, serialization, preemption, replay fencing,
and lifecycle settlement. `CoreConversationRouter` retains the
`(environment, subject)` session map and defensive per-subject lock.
`ConversationCore` remains the sole owner of canonical user append, transient
assistant candidate streaming, successful assistant commit, cancellation /
supersession semantics, and canonical history. Actor cancellation adds no
synthetic conversation message. Discord typing, chunking, semantic streaming,
rate-limit pacing, and presentation action routing remain adapter behavior.

Shutdown closes runtime admission, settles the actor's active and queued work,
then closes router/Core sessions. If active non-user work cannot be contained,
the actor poisons and rejects the Discord successor rather than starting a
conversation under uncertainty.

## MIND-1F-D2 CLI conversation convergence

The normal local CLI path now enters the same character-wide actor as Discord:

```text
CLI input
  → LilavelRuntime.submit_user()
  → SemanticActor (USER)
  → ConversationExecutionAdapter
  → ConversationCore(scope_id="local-cli")
  → ModelRuntime
  → PromptToolkitOutputSink
```

`submit_user()` assigns an opaque runtime-owned `cli:<submission-id>` request
identity. A new identity is generated for every ordinary call, so repeated
text remains distinct; an explicitly replayed identity is fenced by the actor
within its current session. CLI and Discord user requests have the same
priority and deterministic actor FIFO ordering. The actor never receives user
text and does not append canonical messages.

The provider-neutral `ConversationExecutionAdapter` owns the Core bridge,
transient event streaming, cancellation-before-run-bind, and joined Core/model
settlement. The CLI's `local-cli` Core scope and each Discord
`(environment, subject)` scope remain isolated. After a successful Core turn,
Presence queues one runtime-owned internal appraisal opportunity through the
actor. Admission of that `NON_USER` successor is not awaited as hidden
post-processing, so a new user turn can preempt or outrank pending appraisal.

The canonical CLI composition gives `PersistentPresenceRuntime` only local
input/output plumbing and deterministic idle timing. It has no independent
semantic scheduler or model call. Existing standalone P5-B1 callers may still
use the explicitly deprecated compatibility runner path; it is not reachable
from the canonical CLI composition.

## MIND-1F-E legacy cognition retirement

MIND-1F-E retires the production appraisal and autonomous model lanes. The
runtime-owned local producers emit narrow `INTERNAL` triggers containing only
bounded runtime references:

```text
successful Core turn
  → INTERNAL appraisal opportunity
  → SemanticActor (NON_USER)
  → CognitionEpisodeRunner
  → StateProposal(CREATE_INTENTION) or quiet outcome
  → ProposalApplicationCoordinator

deterministic idle opportunity + active intention
  → INTERNAL idle opportunity
  → SemanticActor (NON_USER)
  → CognitionEpisodeRunner
  → ActionProposal(SPEAK | STAY_SILENT)
  → ProposalApplicationCoordinator → existing P4 application seam
```

`ConversationCore` remains the only canonical history owner. Internal
cognition creates no synthetic user or assistant messages. Appraisal state
provenance is captured by trusted runtime composition from the completed Core
run; model output cannot invent message IDs. Local presence output is emitted
only by an application-selected P4 binding, and `STAY_SILENT` is a terminal
no-effect application.

`MindAppraiser` and `AutonomousCognitionRunner` are retired from production
composition. Their narrow direct-generation implementations remain only as
deprecated standalone P5-B1 compatibility fixtures while existing legacy tests
and callers are migrated; `PersistentPresenceRuntime` itself has no direct
semantic generation call in canonical composition. The retained compatibility
classes are not a third production semantic lane.

## MIND-1E temporal intention boundary

The temporal path is intentionally one-shot and inert until a trusted
application accepts a bounded proposal:

```text
CognitionOutcome
  → TemporalProposal (reason, intention_ref?, requested not_before)
  → validate and normalize against runtime UTC clock
  → WakeIntent (pending)
  → deadline
  → CognitionTrigger(source=temporal, wake_intent_id, source_refs)
  → TemporalHost
  → SemanticActor (NON_USER)
  → CognitionEpisodeRunner
  → ProposalApplicationCoordinator
```

The `TemporalCoordinator` owns the normalized deadline, one-second minimum
delay, seven-day maximum horizon, eight-pending bound, scope/actor ownership,
deduplication, cancellation/supersession, due ordering, and one-shot dispatch
fence. Equivalent pending proposals use scope + reason + intention reference as
their key; the first pending deadline wins. Past requests clamp to the minimum
delay, future requests beyond the horizon clamp to the horizon, and malformed
or unsupported proposals are rejected.

Temporal dispatch emits no `ActionProposal` or tool call and has no Core,
Discord, assistant-message, or canonical-history capability. A temporal
trigger has no observation payload; it carries a bounded reason as data plus
runtime-owned wake and source IDs. The reason is not trusted instruction or
authority. The runtime-owned `TemporalHost` waits on the earliest deadline or
a narrow coordinator change notification, then calls `poll_due()` and submits
each returned trigger once. It has no model, runner, application, environment,
or tool capability. The actor serializes temporal and external triggers in one
cognition lane, so an active episode cannot run in parallel with a due wake;
the runner's local serial lock is defensive containment, not character-wide
admission authority. A dispatched wake remains fenced even if its later
episode fails or is cancelled. Fake-clock tests use the host's read-only wake
seam to advance deterministic time; this is not live-clock evidence.

Accepted wakes are in-memory only in MIND-1E. Restart recovery, durable wake
records, and overdue-at-restart policy are `UNVERIFIED`/deferred because the
current runtime/MindState persistence seam is not a small durable agent-state
store.

## Ownership map

| Boundary | Owns | Does not own |
| --- | --- | --- |
| `LilavelRuntime` in `apps/runtime` | Persistent process lifecycle, one character-wide `SemanticActor`, optional `MindExecutionAdapter`, runtime-owned conversation execution adapter, deadline-driven `TemporalHost`, bounded `WorldEvent` admission, recent in-memory `ObservationWindow`, deterministic cognition gate, runtime-owned ambient intervention/social-permission contracts and E1 evaluation, inert bounded `ContextFrame` contracts, the deterministic runtime-owned `ContextFrameBuilder` and purpose projections, environment task ownership, actor-owned CLI and reactive response admission, optional local presence lifecycle, local-CLI-only bounded MIND-0 intentions/self-actions, Core session lifecycle, action destination selection, the explicit application-owned tool registration/exposure/authorization/executor seam, MIND-1D proposal application, the bounded MIND-1E temporal coordinator, and the opt-in COG-V1-C disposition-planner/evaluation seam | Canonical history semantics, Discord transport identity, model-based attention, recurring/general scheduling, durable memory or wake records, provider sessions, autonomous ambient speech, or arbitrary model-selected tools |
| `ConversationCore` | Canonical conversation history, context composition, turn admission, conversation runs, assistant commit semantics, and conversation-level cancellation/supersession; the immutable identity, disposition, and stable `OperatingCanon` value contracts remain Core-owned | Whole-agent scheduling, world state, provider continuation, Discord identity, or tools |
| `ModelRuntime` in Core | Local physical generation admission, generation IDs/epochs, event delivery, cancellation, shutdown, fail-closed runtime state, and the explicit opt-in V3 tool-wait/continuation lifecycle | Canonical agent memory, application tool authorization/execution, provider authentication, or Discord behavior |
| `apps/model-sidecar` | Provider/process transport, supported auth discovery, provider mapping, streaming, cleanup, default version-two JSONL behavior, and bounded active-generation V3 replay/correlation state | Semantic conversation history, agent identity, application tool execution/policy, MCP, or the top-level runtime |
| `apps/discord-adapter` | Discord observations/actions, DM admission, edge-local mapping, typing, sends, edits, continuations, and presentation diagnostics | Lilavel identity, canonical history, memory, provider state, scheduling, or agent lifecycle |
| Core persistence substrate | Canonical conversation messages and separate raw provenance evidence | Interpreted memory, retrieval, reflection, confidence, or durable cross-environment agent state |

## Persistent CLI presence

The root `uv run lilavel` launcher composes one `LilavelRuntime`, one local
presence component, one `ConversationCore`, and one `ModelRuntimeV3`. Normal
typed input enters `LilavelRuntime.submit_user()` as an actor-owned `USER`
episode and then reaches the `local-cli` `ConversationCore` through the shared
`ConversationExecutionAdapter`. Background rendering is isolated behind a
bounded `prompt_toolkit` output bridge, so terminal mechanics do not become
runtime semantics.

The presence component waits for either real local input or a runtime-monotonic
idle deadline. Real activity resets the deadline and one-shot idle latch. One
`IdleOpportunity` receives a deterministic `NO_WAKE` or `WAKE`; an opportunity
can admit at most one cognition run, and no second opportunity is created until
new user activity resets the latch. The safe production default is `NO_WAKE`
with a 300-second idle interval; explicit CLI configuration can enable the
deterministic wake path.

After a successful normal Core turn, the runtime queues one transient internal
appraisal opportunity. The opportunity enters the actor as `NON_USER` work and
uses `CognitionEpisodeRunner`; its strict bounded result can create one
runtime-owned intention through `ProposalApplicationCoordinator`, binding the
actual completed Core user and assistant message IDs. Invalid or missing
output is quiet and is not retried. Idle cognition follows the same path for a
selected active intention. A successful `presence.say` is applied through the
existing P4 application seam, marks the intention `expressed`, and records one
bounded noncanonical `SelfAction`; `presence.stay_silent` has no effect and
leaves the intention active.

`PersistentPresenceRuntime` owns only local input/output plumbing and the
deterministic idle timer in the canonical composition. It does not own a model
runner, semantic admission, or presence-only tool runtime. The deprecated
standalone `MindAppraiser` and `AutonomousCognitionRunner` compatibility
classes are not used by the launcher and are not production semantic lanes.

Normal CLI conversation receives a bounded read-only `MindProjection` through
the trusted-guidance composition seam. It includes active intentions and
recent noncanonical self-action context; autonomous speech is never appended to
Core history. Character v0 blocks remain immutable and are separate from
run-specific normal-turn, appraisal, and autonomous behavior controls.

The generation-scoped presence executor permits exactly one action. A
successful `presence.say` enqueues one bounded local utterance and returns
`effect=confirmed`; `presence.stay_silent` returns success with `effect=none`
and emits nothing. Provider continuation remains mandatory after the result
batch, but its semantic text is discarded. User input records priority
cancellation before successor admission, including the pre-handle race, and
waits for the existing provider/tool joined settlement before the user turn
uses the shared model runtime. In the canonical composition, Presence binds direct
user submission back to `LilavelRuntime`; it retains no active user authority
outside that actor path. Internal appraisal and idle work are actor-owned
`NON_USER` episodes. Standalone Presence construction retains only the
explicitly deprecated P5-B1 compatibility path.

## Conversational foundation

`ConversationCore` is the provider-neutral semantic conversation layer. It
accepts a user message only after an atomic append to its Core-owned store. A
`ConversationRun` exposes assistant deltas as transient candidate output; only
a successful completion is committed as the canonical assistant message.
Partial, cancelled, superseded, failed, and stale assistant output has no
canonical persistence path.

There is one logical active run per conversation. A new turn can supersede the
current run; Core requests physical cancellation through `ModelRuntime`, waits
for the runtime generation to settle, and then starts the next request. Late
output from an older run cannot enter the new run's stream.

`ConversationContextComposer` is the seam between canonical conversation state
and model input. The current composer sends complete provider-neutral ordered
`role`/`text` context on each request. Optional trusted guidance is separate
from user text and must come from application-owned compiler output.

## Generation and model boundary

`ModelRuntime` owns the application-visible local generation lifecycle. It
creates generation IDs and epochs, admits one physical generation at a time,
consumes strict version-two JSONL events, and settles each handle exactly once.
Cancellation is an intent; a valid completion may win a completion/cancel
race. Protocol, pipe, queue, cancellation-deadline, shutdown, or process
containment uncertainty fails closed and does not trigger automatic reuse.

`ModelRuntimeV3` is an explicit opt-in host; the default production path remains
V2. One V3 generation identity may span bounded provider turns separated by
`tool_wait`. An exact ordered result batch is consumed and fenced before the
continuation write. Cancellation, supersession, shutdown, and provider failure
join provider settlement with the application tool session before successor
admission. An executor that cannot be confirmed settled poisons the runtime.

The model sidecar is a transport boundary, not a second conversational runtime.
Core owns the full context supplied to each request. The default V2 host retains
no semantic history or provider continuation state. The opt-in V3 host retains
only bounded active-generation replay context and local/provider call mapping;
it discards them at the generation terminal. Stdout remains machine-readable
JSONL only; human diagnostics use stderr.

Provider selection, supported authentication discovery, pi-ai context mapping,
cleanup deadlines, Bun commands, and Windows launcher details are local
implementation concerns documented in
[`apps/model-sidecar/README.md`](../apps/model-sidecar/README.md), not system
ownership rules.

## Application tool authorization boundary

The opt-in deterministic V3 composition uses one trusted
`ApplicationToolRegistry` of canonical `ToolSpec` values plus bound executors.
The registry rejects duplicate names and unsupported schema features, and it
never uses provider aliases as authority keys. Each generation receives a
bounded immutable exposure snapshot selected by trusted application
composition.

Exposure is not a capability grant. A model-originated `ToolCall` must remain
in the snapshot, pass strict non-coercive application validation, pass the
trusted-scope authorization hook, and remain live immediately before its
sequential bound executor starts. Internal presentation actions are not
model-exposable bindings. Validation, denial, unavailability, executor
failure, timeout, and effect certainty map to the existing typed `ToolResult`
contract; raw arguments, results, provider payloads, and exception bodies
remain outside runtime evidence and canonical history.

P4-D adds exactly one explicit Discord adapter composition: the canonical
`discord.send_message` spec contains only bounded `text`. `DiscordTextEdge`
resolves the admitted DM's local channel reference from the opaque subject,
binds that executor to the Core scope before generation, and exposes only that
snapshot to the explicit V3 host. The model cannot provide or alter the
destination. The executor sends once at most, suppresses mentions, maps
confirmed/pre-send/ambiguous outcomes to typed status/effect values, and never
retries `effect=unknown`. This is a deterministic proof seam, not a global
production activation; ordinary Discord traffic remains V2/no-tool.

## Persistence and evidence

The current SQLite substrate keeps canonical messages and raw provenance
evidence separate. A message append and its evidence record are committed in
one transaction. Evidence points to its canonical source and does not become
interpreted memory, a claim, or a second content authority.

The default store is SQLite `:memory:`. Restart-safe conversation history
requires an explicit file-backed store and stable provider-neutral scope. The
MIND-0 state is intentionally in-memory and local-CLI-only, so it is lost on
restart; durable product-level agent state remains outside this foundation.

Runtime evidence is bounded diagnostic output. Core evidence joins conversation
run IDs to physical generation IDs/epochs and safe protocol outcomes without
storing request text, delta content, credentials, provider payloads, or
exception bodies. V3 tool evidence adds only correlation-safe lifecycle classes,
counts, normalized status/effect codes, and settlement outcomes; raw arguments,
results, and replay payloads are excluded. Evidence is not canonical history and
is not a live-provider or restart proof.

## Persistent kernel lifecycle

`LilavelRuntime` is a separate Python package that depends on provider-neutral
Core and the CLI-local `prompt-toolkit` renderer but not on Discord, Neuro, the
model sidecar package, or a provider. Its normal lifecycle is
`new → starting → running → stopping → stopped`; stopped and failed instances
cannot be restarted. It owns the `SemanticActor` lifecycle and registered
adapter coroutines through an `asyncio.TaskGroup`; the actor owns one bounded
worker task and settles or contains its semantic work before runtime shutdown
completes. An unexpected owned-task failure fails the kernel closed. The
runtime also supervises the actor's explicit terminal failure notification:
`SemanticActorState.POISONED` transitions a running runtime to `FAILED`, closes
semantic admission, and preserves a bounded typed failure reason. Poison during
`STOPPING` is handled as shutdown failure without reviving or double-transitioning
the lifecycle.

Observation admission uses a bounded `asyncio.Queue` plus a bounded in-memory
`ObservationWindow`. `admit_observation()` (and the adapter-facing `submit()`
alias) applies backpressure while pending ingress is full, adopts an immutable
`Observation`, and returns an `ObservationReceipt`. It never invokes the
cognition gate, router, Core, model, tool, or scheduler. The window retains
only recent admissions and evicts its oldest transient entry at capacity.
Shutdown closes admission, cancels environment tasks, drains accepted
observations, and settles the task group within a configured deadline. The
zero-environment path starts the same event consumer, remains healthy without
conversations or model activity, and follows the same clean shutdown path.

`WorldEvent` payloads are deep-frozen JSON-compatible values and are untrusted
by default. `Observation` preserves that trust and provenance; admission does
not promote an event into memory or canonical conversation history. Admission
does not invoke the cognition gate. An explicit `cognition_step()` resolves an
admitted receipt and returns exactly `NO_COGNITION` or a bounded
`CognitionTrigger`; only the positive path submits a `USER` episode to
`SemanticActor`. `reactive_step()` remains the compatibility alias used by the
current DM adapter. The actor invokes the runtime-owned conversation adapter,
which delegates to the existing reactive/Core router. Separately,
`CognitionEpisodeRunner` freezes one bounded context,
serializes one episode for its runtime scope, invokes an effect-free engine
seam, and returns a validated inert `CognitionOutcome` or no outcome on
failure/cancellation/timeout. A quiet outcome is cognition that completed with
zero proposals; it is not `NO_COGNITION`. The runner never mutates `MindState`,
executes tools, sends Discord output, schedules wakes, writes memory, or
commits an assistant message. `ProposalApplicationCoordinator` is the only
MIND-1D path that can turn a runner-completed outcome into a trusted state
delta or P4 tool call. The Core router's existing compatibility path
continues to convert semantic run events into trusted runtime-generated
presentation `ToolCall`s and checks `ToolResult`s; this grants no authority to
model-selected tools.

## Environment adapter boundary

The Discord implementation lives at `apps/discord-adapter` and depends on the
runtime composition boundary; Core has no Discord dependency. The Python import namespace
`lilavel_discord_edge` is retained as a compatibility detail of the migrated
package, while the directory name expresses its environment-adapter role.

The adapter admits only human-authored one-to-one DM `MESSAGE_CREATE` events.
It deduplicates the Discord message, retains channel/message objects locally,
maps the channel to an opaque process-local subject, and submits a `WorldEvent`
containing only that opaque subject and user text. After the runtime returns an
`ObservationReceipt`, the adapter explicitly requests the compatibility
reactive step; admission alone never starts the cognition gate or
ConversationCore. The current direct-message event produces one bounded
`CognitionTrigger`, and the runtime submits its stable observation-derived
identity to the character-wide actor at `USER` priority. The actor then invokes
the runtime conversation adapter. Runtime routing owns the Core conversation and
returns typed open/bind/delta/terminal presentation actions. Discord
channel/message/author IDs never enter Core history or a model request.

The adapter's bounded message deduplication and session map are edge-local
delivery concerns, not durable agent identity or memory. Adapter restarts do
not establish durable Discord-to-agent continuity. The explicit P4-D tool
composition reuses the subject-to-local-channel binding but does not expose
Discord IDs or channel authority to Core/model history. Guild listening, voice,
social attention, slash commands beyond this single proof capability, MCP, and
provider continuation remain outside this component.

## Interaction invariants

- Admitting a `WorldEvent` creates a bounded noncanonical `Observation`; it
  does not imply a response, cognition, or action. `Observation` and
  `ConversationMessage` remain distinct types and lifecycles.
- The Discord DM response path is explicit and ordered: admission receipt first,
  cognition gate second, actor `USER` admission third, conversation adapter
  and Core fourth. A `NO_COGNITION` result is a valid completed path; it
  creates no actor request, Core turn, model call, or presentation action.
- `SemanticActor` is the character-wide semantic admission authority for
  Discord reactive conversation, normal CLI conversation, generic/temporal
  MIND work, internal appraisal, and idle cognition: one active episode, user
  priority over non-user work, cooperative preemption with
  settlement-before-successor, replay fencing, and fail-closed containment.
  Discord and CLI work are `USER`; all cognition work is `NON_USER`.
- Actor cancellation/preemption never creates a synthetic Core history entry;
  only `ConversationCore` can append canonical user or successful assistant
  messages. Cancelled, failed, stale, or partial assistant candidates remain
  transient.
- A `CognitionTrigger` permits a cognition episode but does not select or
  authorize an action. MIND-1C ends with inert proposals; MIND-1D owns their
  trusted validation, authorization, application, and evidence.
- `ProposalApplicationCoordinator` validates a complete proposal set before
  effectful work, applies state first as one version-fenced local batch, and
  executes external actions in deterministic order through P4. Local state and
  external effects are not globally atomic; confirmed or unknown external
  effects are never rolled back or automatically retried.
- A `TemporalProposal` is inert until trusted application. A due
  `WakeIntent` emits only a temporal `CognitionTrigger`; time never directly
  selects an action, calls a tool, sends Discord, writes assistant history, or
  runs a second cognition actor.
- One-shot wake dispatch is fenced before trigger emission. Cancellation and
  supersession apply only to pending wakes; a dispatched wake is not silently
  retried after cognition failure or cancellation.
- User acceptance is immediate and canonical; assistant commit occurs only
  after successful completion and persistence.
- Streaming consumers receive bounded transient output and exactly one
  terminal settlement.
- Cancellation, supersession, failure, and stale generations cannot leak
  partial assistant output into canonical history or a later run.
- Provider and adapter identifiers are transport metadata, never semantic
  identity or memory.
- Cleanup uncertainty, malformed/future/mismatched protocol events, and
  containment uncertainty fail closed.
- Deterministic fixture evidence is reported separately from live provider,
  Discord, restart, and platform-specific evidence.

The static guard in `scripts/check_architecture.py` checks the current cheap
regressions: Core cannot import Discord or the top-level runtime, the runtime
cannot import Discord or Neuro implementations, the Discord environment cannot
import Core persistence or perform Core lifecycle operations, and runtime model
generation remains on the canonical `LocalCognitionEngine` route or explicitly
deprecated compatibility fixtures. `PersistentPresenceRuntime` and canonical
composition modules cannot own a direct legacy semantic generation lane.

## Deferred boundaries

The following are intentionally `DEFERRED` rather than implied: durable
agent-state and wake persistence/restart recovery, a general or recurring
scheduler, probabilistic or LLM attention policy,
arbitrary autonomous tool activation, retrieval or memory semantics, and
additional production environment adapters. Each needs an explicit decision
and proportionate validation before code is added.
