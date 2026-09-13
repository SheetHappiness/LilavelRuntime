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
same actor as `INTERNAL` `NON_USER` cognition. Broader autonomous capabilities
are not implied by these slices.

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

The standalone `DispositionPlanner` is an opt-in model-backed evaluation seam,
not a production USER-route integration. It answers only how an already direct
user turn should be approached. The direct-user invariants remain runtime
owned and fixed: `attention=THINK` and `intervention=RESPOND`.

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
examples. The existing `ConversationCore`, CLI, Discord, and SemanticActor
production USER paths do not call this planner in COG-V1-C.

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
  → ConversationCore.start_turn()
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
| `LilavelRuntime` in `apps/runtime` | Persistent process lifecycle, one character-wide `SemanticActor`, optional `MindExecutionAdapter`, runtime-owned conversation execution adapter, deadline-driven `TemporalHost`, bounded `WorldEvent` admission, recent in-memory `ObservationWindow`, deterministic cognition gate, environment task ownership, actor-owned CLI and reactive response admission, optional local presence lifecycle, local-CLI-only bounded MIND-0 intentions/self-actions, Core session lifecycle, action destination selection, the explicit application-owned tool registration/exposure/authorization/executor seam, MIND-1D proposal application, the bounded MIND-1E temporal coordinator, and the opt-in COG-V1-C disposition-planner/evaluation seam | Canonical history semantics, Discord transport identity, model-based attention, recurring/general scheduling, durable memory or wake records, provider sessions, or arbitrary model-selected tools |
| `ConversationCore` | Canonical conversation history, context composition, turn admission, conversation runs, assistant commit semantics, and conversation-level cancellation/supersession; the immutable identity and disposition value contracts remain Core-owned | Whole-agent scheduling, world state, provider continuation, Discord identity, or tools |
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
