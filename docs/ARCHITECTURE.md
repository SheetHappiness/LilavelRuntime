# Architecture

This document records the current ownership boundaries of the canonical
`LilavelRuntime` repository. Lilavel is modeled as a persistent agent runtime,
but this migration implements only the proven conversational foundation and a
replaceable Discord environment adapter. Future autonomous capabilities are
not implied by the product model.

## Top-level product boundary

The `LilavelRuntime` product boundary is the future owner of agent lifecycle,
world observations, scheduling and wake, attention/decision, tools/actions, and
cross-environment agent-state orchestration. No speculative scheduler, world
model, attention loop, or tool authority is introduced by this migration.

The current code has no separate top-level runtime package because there is no
implemented contract to place there yet. The repository is prepared for that
owner without pretending that the owner already exists in code.

The intended direction is:

```text
world observations
        │
        ▼
environment adapters ──► LilavelRuntime orchestration (future)
        │                              │
        │                              ├── scheduling / wake (future)
        │                              ├── attention / decision (future)
        │                              ├── tools / actions (future)
        │                              └── agent-state orchestration (future)
        │
        └──── approved conversational action
                         │
                         ▼
                  ConversationCore
                         │
                         ▼
                   ModelRuntime
                         │
                         ▼
                  model-sidecar
```

## Ownership map

| Boundary | Owns | Does not own |
| --- | --- | --- |
| `LilavelRuntime` product boundary | Future cross-environment agent lifecycle and orchestration | Current conversation history, provider sessions, or adapter-specific identity |
| `ConversationCore` | Canonical conversation history, context composition, turn admission, conversation runs, assistant commit semantics, and conversation-level cancellation/supersession | Whole-agent scheduling, world state, provider continuation, Discord identity, or tools |
| `ModelRuntime` in Core | Local physical generation admission, generation IDs/epochs, event delivery, cancellation, shutdown, and fail-closed runtime state | Canonical agent memory, provider authentication, or Discord behavior |
| `apps/model-sidecar` | Provider/process transport, supported auth discovery, provider mapping, streaming, cleanup, and version-two JSONL host behavior | Semantic conversation history, agent identity, tools/MCP, or the top-level runtime |
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

The model sidecar is a transport boundary, not a second conversational runtime.
Core owns the full context supplied to each request. The sidecar does not retain
semantic history or provider continuation state. Stdout is reserved for
machine-readable version-two JSONL frames; human diagnostics use stderr.

Provider selection, supported authentication discovery, pi-ai context mapping,
cleanup deadlines, Bun commands, and Windows launcher details are local
implementation concerns documented in
[`apps/model-sidecar/README.md`](../apps/model-sidecar/README.md), not system
ownership rules.

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
exception bodies. Evidence is not canonical history and is not a live-provider
or restart proof.

## Environment adapter boundary

The Discord implementation lives at `apps/discord-adapter` and depends on
`lilavel-core`; Core has no Discord dependency. The Python import namespace
`lilavel_discord_edge` is retained as a compatibility detail of the migrated
package, while the directory name expresses its environment-adapter role.

The current adapter admits only human-authored one-to-one DM
`MESSAGE_CREATE` events. It maps channel metadata to an opaque process-local
session key and a Core conversation, then consumes semantic run events for
typing, sends, coalesced edits, continuations, and terminal presentation. A
Discord channel/message/author ID never enters Core history or a model request.

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

The static guard in `scripts/check_architecture.py` checks the two cheapest
regressions: Core cannot import Discord, and the Discord adapter cannot import
Core's persistence ownership.

## Deferred boundaries

The following are intentionally `DEFERRED` rather than implied: a durable
agent-state model, world/event schema, scheduling and wake policy,
attention/decision policy, tool/action authority, retrieval or memory
semantics, shared runtime scheduling, and non-Discord environment adapters.
Each needs an explicit decision and proportionate validation before code is
added.
