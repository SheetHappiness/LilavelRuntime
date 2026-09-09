# Architecture

This document records the current ownership boundaries of the canonical
`LilavelRuntime` repository. Lilavel has a minimal persistent-agent kernel plus
the proven conversational foundation and a replaceable Discord environment
adapter. Future autonomous capabilities are not implied by this kernel.

## Top-level product boundary

The `LilavelRuntime` kernel owns the top-level process lifecycle, bounded world
event ingress, registered environment tasks, wake classification,
conversation routing, Core/ModelRuntime session lifecycle, and selection of
the source environment for typed presentation actions. It adds no autonomous
scheduling, attention loop, world model, memory, or production model-selected
tool authority.

The intended direction is:

```text
world observations
        │
        ▼
Discord adapter ──► WorldEvent ──► LilavelRuntime
                                      │
                                      ├── deterministic DM wake
                                      ├── conversation route/session
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
| `LilavelRuntime` in `apps/runtime` | Persistent process lifecycle, bounded event ingress, environment task ownership, explicit-DM wake/routing, Core session lifecycle, action destination selection, and the explicit application-owned tool registration/exposure/authorization/executor seam | Canon, canonical history semantics, Discord transport identity, autonomous scheduling, memory, provider sessions, or production model-selected tools |
| `ConversationCore` | Canonical conversation history, context composition, turn admission, conversation runs, assistant commit semantics, and conversation-level cancellation/supersession | Whole-agent scheduling, world state, provider continuation, Discord identity, or tools |
| `ModelRuntime` in Core | Local physical generation admission, generation IDs/epochs, event delivery, cancellation, shutdown, fail-closed runtime state, and the explicit opt-in V3 tool-wait/continuation lifecycle | Canonical agent memory, application tool authorization/execution, provider authentication, or Discord behavior |
| `apps/model-sidecar` | Provider/process transport, supported auth discovery, provider mapping, streaming, cleanup, default version-two JSONL behavior, and bounded active-generation V3 replay/correlation state | Semantic conversation history, agent identity, application tool execution/policy, MCP, or the top-level runtime |
| `apps/discord-adapter` | Discord observations/actions, DM admission, edge-local mapping, typing, sends, edits, continuations, and presentation diagnostics | Lilavel identity, canonical history, memory, provider state, scheduling, or agent lifecycle |
| Core persistence substrate | Canonical conversation messages and separate raw provenance evidence | Interpreted memory, retrieval, reflection, confidence, or durable cross-environment agent state |

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

The opt-in deterministic V3 composition in `apps/runtime` uses one trusted
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

## Persistence and evidence

The current SQLite substrate keeps canonical messages and raw provenance
evidence separate. A message append and its evidence record are committed in
one transaction. Evidence points to its canonical source and does not become
interpreted memory, a claim, or a second content authority.

The default store is SQLite `:memory:`. Restart-safe conversation history
requires an explicit file-backed store and stable provider-neutral scope. The
product-level persistent agent state described above is not implemented by
this foundation.

Runtime evidence is bounded diagnostic output. Core evidence joins conversation
run IDs to physical generation IDs/epochs and safe protocol outcomes without
storing request text, delta content, credentials, provider payloads, or
exception bodies. V3 tool evidence adds only correlation-safe lifecycle classes,
counts, normalized status/effect codes, and settlement outcomes; raw arguments,
results, and replay payloads are excluded. Evidence is not canonical history and
is not a live-provider or restart proof.

## Persistent kernel lifecycle

`LilavelRuntime` is a separate Python package that depends on provider-neutral
Core but not on Discord, Neuro, the model sidecar, or a provider. Its normal lifecycle is
`new → starting → running → stopping → stopped`; stopped and failed instances
cannot be restarted. It owns registered adapter coroutines through an
`asyncio.TaskGroup`. An unexpected owned-task failure fails the kernel closed.

Observation ingress uses a bounded `asyncio.Queue`. `submit()` applies
backpressure while the queue is full; it never silently drops or accumulates
unbounded events. Shutdown closes admission, cancels environment tasks, drains
accepted observations through the wake-policy seam, and settles the task group
within a configured deadline. The zero-environment path starts the same event
consumer, remains healthy without conversations or model activity, and follows
the same clean shutdown path.

`WorldEvent` payloads are deep-frozen JSON-compatible values and are untrusted
by default. Observation does not promote an event into memory or canonical
conversation history. The deterministic direct-message policy routes only the
typed `direct_message` kind. The Core router converts semantic run events into
trusted runtime-generated presentation `ToolCall`s and checks `ToolResult`s;
this grants no authority to model-selected tools.

## Environment adapter boundary

The Discord implementation lives at `apps/discord-adapter` and depends on the
runtime composition boundary; Core has no Discord dependency. The Python import namespace
`lilavel_discord_edge` is retained as a compatibility detail of the migrated
package, while the directory name expresses its environment-adapter role.

The adapter admits only human-authored one-to-one DM `MESSAGE_CREATE` events.
It deduplicates the Discord message, retains channel/message objects locally,
maps the channel to an opaque process-local subject, and submits a `WorldEvent`
containing only that opaque subject and user text. Runtime routing owns the
Core conversation and returns typed open/bind/delta/terminal presentation
actions. Discord channel/message/author IDs never enter Core history or a
model request.

The adapter's bounded message deduplication and session map are edge-local
delivery concerns, not durable agent identity or memory. Adapter restarts do
not establish durable Discord-to-agent continuity. Guild listening, voice,
social attention, slash commands, tools, MCP, and provider continuation remain
outside this component.

## Interaction invariants

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
agent-state model, scheduler or timer source, autonomous model wake loop,
attention/decision policy, production model tool activation,
retrieval or memory semantics, and production
non-Discord environment adapters. Each needs an explicit decision and
proportionate validation before code is added.
