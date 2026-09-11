# Lilavel Core conversational runtime

This package is the conversational foundation inside `LilavelRuntime`.
`ConversationCore` owns conversational semantics and canonical conversation
state; it is not the whole persistent agent runtime. `ModelRuntime` owns the
local lifecycle of the persistent model sidecar: generation IDs, epochs,
admission, cancellation, event delivery, and failure state. The sidecar
remains a provider and process transport boundary; it does not own conversation
history, memory, character behavior, tools, or other semantic session state.

## Lifecycle and admission

The runtime uses these states:

| State | Meaning |
| --- | --- |
| `new` | No sidecar process has been started. |
| `starting` | The process is running and Core is waiting for `ready`. |
| `ready` | Local sidecar/model readiness has been confirmed and one generation may be admitted. |
| `busy` | One generation is admitted. |
| `shutting_down` | Shutdown has closed admission; teardown is in progress. |
| `closed` | A started runtime shut down with acknowledgement, zero process exit, no unsettled handle, and confirmed process-containment teardown; an unstarted `new` runtime also becomes `closed` when shut down. |
| `failed` | A protocol, pipe, process, queue, cancellation, shutdown, or containment failure made the runtime unusable. |

The normal path is `new → starting → ready`, then `ready ↔ busy`. Shutdown
sets `shutting_down` while holding the same admission lock used by
`generate()`, before cancellation or teardown commands are sent. A concurrent
or later `generate()` therefore raises `RuntimeShuttingDown` synchronously.
There is no automatic restart: a `closed` or `failed` runtime is not reused.

`ready` and `health()` report local state and process information. They do not
probe the provider endpoint or establish quota, entitlement, or a successful
future model request. The default deadlines are 30 seconds for startup, 5
seconds for shutdown, 7 seconds for cancellation settlement, and 5 seconds
for each bounded command write.

## Generation settlement

Each returned `GenerationHandle` has one Core-generated `generation_id` and
monotonic `epoch`. On every completion, cancellation, failure, or shutdown
exit path, a returned handle receives exactly one terminal settlement: a
completed or cancelled `GenerationResult`, or a typed Core failure such as
`GenerationFailed`, `ProtocolViolation`, `SidecarCrashed`,
`CancellationTimeout`, `ShutdownTimeout`, or `EventQueueOverflow`. Repeated
terminal attempts do not settle the handle a second time. There is no
independent generation deadline, so an un-cancelled provider can remain active
until its caller cancels it or the runtime exits through another failure or
shutdown path.

Cancellation records caller intent and starts the bounded cancellation
deadline. `accepted` may legally arrive once after cancellation if the request
was already in flight; duplicate `accepted` frames and deltas before an
accepted frame are protocol failures while that request is active. Once a
generation has settled, duplicate or contradictory terminal frames are stale
and ignored. Deltas that arrive after Core enters `cancelling` are discarded.
A valid completion can win a completion/cancel race, so calling `cancel()` does
not guarantee a cancelled status.

If cancellation does not produce a terminal sidecar outcome before the
deadline, Core raises `CancellationTimeout`, marks the runtime failed, and
contains the owned process. The retained terminal event uses the corresponding
version-two `cancellation_timeout` code. Cleanup failures from the sidecar
(`cleanup_timeout` or `cleanup_error`) and forced protocol shutdown
(`shutdown_timeout`) also fail closed and prevent reuse. A stale generation
failure after a handle has already settled cannot create a second settlement,
but a generationless cleanup failure remains runtime-fatal.

Clean shutdown requires the version-two `shutdown` acknowledgement, a zero
child exit, no pending generation, and confirmed containment teardown. A
missing acknowledgement, abnormal child exit, forced shutdown deadline, or
unsettled handle leaves the runtime failed. A pending handle is settled before
forced containment so its failure preserves the known cause (`ShutdownTimeout`
or `SidecarCrashed`) instead of being replaced by child EOF. The stdout reader
remains strict through EOF: a non-empty unterminated line is a malformed
protocol frame, including after a shutdown request; only the sidecar's valid
acknowledgement followed by a zero exit is clean.

## Physical runtime evidence

`ModelRuntime.physical_evidence()` returns a copied, bounded, process-ephemeral
snapshot of safe physical generation evidence. It records generation creation,
accepted/streaming/terminal protocol event classes, aggregate text-delta counts
and UTF-8 byte lengths, and terminal outcomes or normalized safe failure codes.
It never stores request text, delta content, provider payloads, auth material,
or exception bodies. The trace is diagnostic evidence, not canonical history
or durable provenance.

The version-two protocol carries `generation_id` and `epoch` on every
generation-scoped command/event. On the Core-launched path,
`ModelSidecar.generate()` receives that identity and sets its sidecar-local
`requestId` to the same `generation_id`; the sidecar request ID is therefore an
alias, not a second semantic identity. Join `ConversationCore.runtime_evidence()`
from `run_id` to `generation_id`/`epoch`, then use
`ModelRuntime.physical_evidence()` for the corresponding protocol activity and
physical terminal outcome. Neither trace changes canonical persistence.

On Windows, Core launches the default `npx` entry point with
`CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP`, verifies Job Object
containment, and resumes it only after the containment handle is attached.
The separate console process group prevents an operator Ctrl+C from killing
the child in the middle of a JSONL frame; Core can instead send the defined
cancel-then-shutdown command sequence and require the normal acknowledgement.
Forced containment still covers the `npx`/Node/cmd/Bun descendant tree. The
POSIX fallback targets the owned process and does not claim the same
descendant guarantee.

## Streaming consumer contract

`events()` is the sole streaming consumer for a handle and permits one active
iterator. Consume it while the generation runs when streaming output is
needed. It yields `accepted`, queued `text_delta`, and one terminal event in
wire order.

`wait(timeout)` waits only for terminal settlement. It does not drain events
and does not cancel the generation when its timeout expires; a timeout raises
Python's `TimeoutError` and leaves the request active. A later `events()` call
can still drain already queued events.

The default per-generation Core event queue holds 128 non-terminal events,
including `accepted` and deltas. `event_queue_size` may set another positive
bounded capacity. If a producer reaches a full queue, Core raises
`EventQueueOverflow`, fails the runtime closed, and retains the one terminal
failure out of band. The terminal event is delivered after the queued events
drain, so a full queue cannot hide the terminal outcome. Queue storage remains
bounded: a wait-only consumer succeeds when its stream fits, while a stream
that reaches the configured capacity is an explicitly fail-fast overload.

Only deltas successfully accepted into the Core queue contribute to
`GenerationHandle.text` and `GenerationResult.text`. Cancellation returns the
partial text available at that boundary. A delta already queued before
cancellation may still be yielded afterward; later stale or cancelled deltas
cannot leak into that generation or a later one.

The version-two protocol bounds a prompt at 64 KiB, each delta at 16 KiB, the
accumulated Core response at 256 KiB, each frame at 256 KiB, and each ID at
128 UTF-8 bytes. Older generation events are ignored. Future epochs,
mismatched identities, malformed frames, and invalid lifecycle transitions
fail closed.

## Structured caller-owned context

`ModelRequest` also accepts a non-empty ordered tuple of `ContextMessage`
values. Each value contains only `role` (`"user"` or `"assistant"`) and
`text`; aggregate UTF-8 text is bounded to 64 KiB. For example:

```python
ModelRequest(
    messages=(
        ContextMessage("user", "Remember the word quartz."),
        ContextMessage("assistant", "ACK_1"),
        ContextMessage("user", "What was the word?"),
    )
)
```

Core sends this as the version-two `generate.messages` array. The complete
caller-owned context is sent on every request; Core does not persist pi-ai or
provider-specific fields. The sidecar performs the provider adapter mapping
and retains no semantic conversation state. The legacy `prompt` form remains
available for single-turn callers.

## Semantic conversation layer

`ConversationCore` owns canonical conversation history above `ModelRuntime`.
`start_turn()` accepts the user message into history immediately and returns a
`ConversationRun`. The run surfaces assistant deltas incrementally as a
transient candidate; only a successful completion is appended once as a
canonical assistant message. Cancellation, supersession, runtime failure, or
context validation failure never commits partial assistant text.

There is at most one logical active run per conversation. A new turn may
supersede the current run; Core requests physical cancellation through
`ModelRuntime` and waits for that runtime generation to settle before asking
the single-generation runtime to start the new request. Late output from the
older run is discarded and cannot enter the new run's event stream.

`ConversationContextComposer` is the narrow seam between canonical history and
model input. The initial `FullHistoryContextComposer` sends the complete
provider-neutral `role`/`text` history as a version-two structured request.
Optional trusted guidance is carried separately from role/text history.
Callers must derive it only from trusted, application-owned semantic fixtures;
`ConversationCore` never promotes user text into this channel.
Provider metadata, session state, tools, and transport details remain outside
this layer.

Production composition lives in `production_cognition.py`. It exposes the
immutable Character v0 blocks separately, then builds normal-turn
`WorkingState` / `ResponseDisposition` controls through the existing compiler.
An application-owned read-only projection may be appended through
`build_turn_guidance` without entering canonical history. `trusted_guidance`
accepts either static blocks or a zero-argument builder; the builder receives no
user text or transport metadata.

## Offline Character v0 evaluation

`scripts/character_eval.py` builds a reproducible Character v0 evaluation packet
from recorded response JSONL. It does not call a model and is not connected to
production composition. Each input record contains `scenario_id`, one of
`A-neutral`, `B-experimental-identity`, or `C-character-v0`, and `response`;
`status`, `issues`, `error_type`, and the corpus `prompt` are optional.

Run it from this directory:

```powershell
uv run --locked python scripts/character_eval.py recorded-responses.jsonl `
  --output-dir character-eval-output
```

The model-backed recorder runs the same fixed ten-scenario corpus across the
three arms through `ConversationCore` and the real `ModelRuntime` → Bun
sidecar path. It keeps each turn isolated and writes the exact response shape
accepted by `character_eval.py`:

```powershell
uv run --locked python scripts/character_runner.py `
  --output recorded-responses.jsonl
uv run --locked python scripts/character_eval.py recorded-responses.jsonl `
  --output-dir character-eval-output
```

The recorder requires the supported provider authentication already available
to the sidecar. Its result is live/provider-dependent evidence; a missing
authentication setup is `BLOCKED`, not a deterministic evaluation pass. The
recorder remains an evaluation harness and does not select or mutate production
composition.

The output contains `raw-responses.jsonl`, a metadata-bearing `reveal.jsonl`,
the fixed-seed `blind-review.jsonl`, and `corpus.json`. Blind records contain
the identical scenario prompts and response text but no arm metadata. The
harness emits deterministic checks for identity naming, persona-override
resistance, self-announcement, unrelated menswear references, internal/provider
leaks, and copying scenario text into trusted guidance. It does not assign a
subjective score or select a winner.

## Local canonical persistence and raw evidence

`ConversationCore` uses a narrow `ConversationStore` boundary. The default
store is a separate in-memory SQLite database for deterministic ephemeral
callers. A caller that needs restart-safe history supplies one
`SQLiteConversationStore` backed by a filesystem path and an explicit stable
provider-neutral `scope_id`:

```python
store = SQLiteConversationStore("conversation.sqlite3")
core = ConversationCore(runtime, scope_id="stable-scope", store=store)
```

The durable model is intentionally separate from `ContextMessage`. A
`CanonicalMessage` has a Core-owned `message_id`, `scope_id`, monotonic
`sequence_no`, role, text, and UTC creation timestamp. The existing model
context still contains only `ContextMessage(role, text)`, so durable identity
never changes the version-two wire shape. `core.canonical_history` exposes the
durable records; `core.history` remains the compatibility role/text view.

Schema version `1` is stored in SQLite `PRAGMA user_version`. It creates only
`conversation_messages` and `evidence_records`, with foreign-key and
uniqueness constraints. Every canonical message append and its corresponding
raw evidence record are committed in one `BEGIN IMMEDIATE` transaction.
Evidence stores provenance (`source_message_id`, source role, canonical
provenance kind, observed/created timestamps) and no copied text; the source
canonical message is the content authority. Evidence has no claim,
confidence, temporal-validity, retrieval, or reflection semantics.

`message_id` is the Core-owned idempotency key. Replaying the same message ID
with the same scope, role, and text returns the existing immutable message and
does not add another evidence record. Reusing it with different data fails
with `PersistenceConflict`. Invalid sources, roles, scope mismatches, schema
versions, unreadable databases, and SQLite write failures raise typed
`PersistenceError` subclasses. There is no silent fallback from a configured
durable store to memory, and a failed assistant persistence write settles the
run as failed without reporting a canonical assistant success.

The SQLite transaction is the canonical assistant commit point. A crash or
runtime failure before that point leaves the accepted user but no assistant;
if the transaction has already committed, the assistant is durable even if a
process crash prevents the caller from observing the later semantic terminal
event. Delivery acknowledgement is deliberately outside this foundation.

The persistence benchmark is a local directional smoke, not a universal
latency guarantee:

```powershell
uv run --locked python scripts/persistence_latency.py
```

The opt-in live Luna probe uses the existing supported authentication and
prints only semantic results and timing observations:

```powershell
uv run --locked python scripts/conversation_live.py
```

The authoritative Core checks are run from this directory:

```powershell
uv sync --locked
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```

## P4-A V3 contract proof

`protocol_v3.py` parses the shared strict V3 tool-frame corpus without making
`ModelRuntime` expose tools. The shared `lilavel-contracts` dependency hosts
immutable `ToolSpec`, model-untrusted `ToolCall`, and bounded `ToolResult`
values; it owns no tool policy, authorization, execution, or canonical history.
P4-A left the active Core/sidecar runtime protocol at V2; the P4-B continuation
slice below adds a separate explicit opt-in host.

## P4-B opt-in deterministic tool loop

`ModelRuntimeV3` now provides that deterministic continuation path without
changing the default `ModelRuntime` V2 activation. One `generation_id` and
`epoch` spans up to four tool rounds, four calls per batch, and eight calls in
total. While an immutable call batch is pending, the generation remains busy;
only the exact ordered result batch for the active round can be consumed, and it
is fenced before continuation dispatch.

`ConversationCore` supplies its local `scope_id` and logical `run_id` only to a
runtime that explicitly implements the run-aware seam. Calls and results remain
outside canonical role/text history. Pre-tool and continuation text are one
append-only transient candidate, and only the final successful generation
completion can commit the aggregate assistant text.

Provider settlement is joined with application tool-session settlement on
cancellation, supersession, failure, and shutdown. A stale result cannot resume
a newer generation; an uncontainable executor or uncertain provider cleanup
fails the runtime closed. Default bounds are 60 seconds for result wait, 120
seconds overall for a tool-enabled generation, and 10 seconds for the fake
executor; existing stricter transport/shutdown bounds still win.
