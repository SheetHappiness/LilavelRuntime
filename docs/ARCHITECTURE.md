# Architecture

This document records the current ownership boundaries of the canonical
`LilavelRuntime` repository. Lilavel has a minimal persistent-agent kernel plus
the proven conversational foundation, a replaceable Discord environment
adapter, the bounded P5-B1/MIND-0 local presence slice, and the MIND-1A/MIND-1B
observation-admission and observation-to-cognition boundaries. Broader
autonomous capabilities are not implied by these slices.

## Top-level product boundary

The `LilavelRuntime` kernel owns the top-level process lifecycle, bounded world
event admission, a recent in-memory observation window, a deterministic
observation-to-cognition gate, registered environment tasks, explicit reactive
conversation routing, the optional local presence component, Core/ModelRuntime
session lifecycle, and selection of the source environment for typed
presentation actions. P5-B1 adds one monotonic idle opportunity and
transient autonomous cognition lane. MIND-0 adds bounded in-memory intentions
and recent self-actions to the local CLI only; it adds no general scheduler,
attention loop, world model, durable memory, or arbitrary model-selected tool
authority.

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

## Ownership map

| Boundary | Owns | Does not own |
| --- | --- | --- |
| `LilavelRuntime` in `apps/runtime` | Persistent process lifecycle, bounded `WorldEvent` admission, recent in-memory `ObservationWindow`, deterministic cognition gate, environment task ownership, explicit reactive response routing, optional local presence lifecycle, local-CLI-only bounded MIND-0 intentions/self-actions, Core session lifecycle, action destination selection, and the explicit application-owned tool registration/exposure/authorization/executor seam | Canon, canonical history semantics, Discord transport identity, model-based attention, general scheduling, durable memory, provider sessions, or arbitrary model-selected tools |
| `ConversationCore` | Canonical conversation history, context composition, turn admission, conversation runs, assistant commit semantics, and conversation-level cancellation/supersession | Whole-agent scheduling, world state, provider continuation, Discord identity, or tools |
| `ModelRuntime` in Core | Local physical generation admission, generation IDs/epochs, event delivery, cancellation, shutdown, fail-closed runtime state, and the explicit opt-in V3 tool-wait/continuation lifecycle | Canonical agent memory, application tool authorization/execution, provider authentication, or Discord behavior |
| `apps/model-sidecar` | Provider/process transport, supported auth discovery, provider mapping, streaming, cleanup, default version-two JSONL behavior, and bounded active-generation V3 replay/correlation state | Semantic conversation history, agent identity, application tool execution/policy, MCP, or the top-level runtime |
| `apps/discord-adapter` | Discord observations/actions, DM admission, edge-local mapping, typing, sends, edits, continuations, and presentation diagnostics | Lilavel identity, canonical history, memory, provider state, scheduling, or agent lifecycle |
| Core persistence substrate | Canonical conversation messages and separate raw provenance evidence | Interpreted memory, retrieval, reflection, confidence, or durable cross-environment agent state |

## Persistent CLI presence

The root `uv run lilavel` launcher composes one `LilavelRuntime`, one local
presence component, one `ConversationCore`, and one `ModelRuntimeV3`. Normal
typed input enters `ConversationCore.start_turn()` and retains canonical
conversation semantics. Background rendering is isolated behind a bounded
`prompt_toolkit` output bridge, so terminal mechanics do not become runtime
semantics.

The presence component waits for either real local input or a runtime-monotonic
idle deadline. Real activity resets the deadline and one-shot idle latch. One
`IdleOpportunity` receives a deterministic `NO_WAKE` or `WAKE`; an opportunity
can admit at most one cognition run, and no second opportunity is created until
new user activity resets the latch. The safe production default is `NO_WAKE`
with a 300-second idle interval; explicit CLI configuration can enable the
deterministic wake path.

After a successful normal Core turn, the runtime runs one transient,
tool-free `MindAppraiser`. Its strict bounded result can create one
runtime-owned intention, binding the actual completed Core user and assistant
message IDs. Invalid or missing appraisal output is `no_change`; it is not
retried. Idle cognition is admitted only for a selected active intention and
receives that intention directly. A successful `presence.say` marks it
`expressed` and records one bounded noncanonical `SelfAction`; silence leaves
the intention active.

`AutonomousCognitionRunner` is a thin admission client of the existing V3 model
host. It receives only the selected runtime-owned intention and immutable
Character v0 guidance plus autonomous controls. It creates no user message,
transcript, assistant commit, or durable memory. Only an explicitly
application-permitted autonomous logical run identity receives the two
presence tool specs; a matching string prefix alone grants no capability, and
ordinary Core/appraisal runs receive an empty exposure snapshot.

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
uses the shared model runtime.

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
cannot be restarted. It owns registered adapter coroutines through an
`asyncio.TaskGroup`. An unexpected owned-task failure fails the kernel closed.

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
`CognitionTrigger`; only the positive path schedules the existing reactive/Core
router. `reactive_step()` is the compatibility alias used by the current DM
adapter. The Core router converts semantic run events into trusted
runtime-generated presentation `ToolCall`s and checks `ToolResult`s; this
grants no authority to model-selected tools.

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
`CognitionTrigger`. Runtime routing owns the Core conversation and returns typed
open/bind/delta/terminal presentation actions. Discord channel/message/author
IDs never enter Core history or a model request.

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
- The legacy DM response path is explicit and ordered: admission receipt first,
  cognition gate second, reactive step third, Core conversation fourth. A
  `NO_COGNITION` result is a valid completed path; only a bounded
  `CognitionTrigger` reaches the reactive/Core route.
- A `CognitionTrigger` permits a cognition episode but does not select or
  authorize an action; MIND-1C will establish `cognition != action`.
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
cannot import Discord or Neuro implementations, and the Discord environment
cannot import Core persistence or perform Core lifecycle operations.

## Deferred boundaries

The following are intentionally `DEFERRED` rather than implied: a durable
agent-state model, general scheduler, probabilistic or LLM attention policy,
arbitrary autonomous tool activation, retrieval or memory semantics, and
additional production environment adapters. Each needs an explicit decision
and proportionate validation before code is added.
