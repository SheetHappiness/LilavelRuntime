# Lilavel model sidecar

This package is the provider and process transport boundary for independent
model requests. It pins `@oh-my-pi/pi-ai@18.1.2` and
`@oh-my-pi/pi-catalog@18.1.2`, discovers supported OMP authentication through
the auth broker, selects `openai-codex/gpt-5.6-luna`, and invokes
`streamSimple` directly. It has no OMP `AgentSession`, MCP/tools, or semantic
conversation history.

## Importable API

Create the sidecar with `createModelSidecar()`, call `start()` once, and then
call `generate(prompt)` or `generate(messages)` for one request at a time.
Structured `messages` contain only ordered `{role, text}` values where
`role` is `user` or `assistant`. `start()` loads local auth
and the pinned model before emitting `ready`. That event, and the `state`
property, describe local sidecar/model readiness; they do not prove provider
endpoint availability, quota, or live entitlement.

`generate()` returns a handle with a `result` promise and request identity.
The sidecar emits incremental `text_delta` events while the promise is
pending. `cancel(requestId)` aborts provider work. Cancellation is an intent,
so a provider completion can win a completion/cancellation race. `close()`
aborts active work and closes auth; the protocol host supplies the outer
shutdown deadline when the sidecar runs as a process.

The optional `onEvent` observer receives ephemeral typed diagnostics keyed by
`requestId` and `epoch`, including request receipt, provider dispatch, safe
event classes, lengths, timing marks, and safe error codes. Text-delta
diagnostics remain direct observer data and are not a durable evidence store.
When the protocol host supplies `GenerationIdentity`, `requestId` is exactly
the Core `generation_id`; it is a sidecar transport alias and never semantic
conversation identity. The protocol host projects only the version-two
generation identity and safe protocol event shape to Core.

Normal provider completion, cancellation, or failure can release the sidecar
for another request only after provider cleanup resolves. Cleanup waits for
the async iterator's `return()` and, when present, the stream's
`result()` acknowledgement under one default 5-second `cleanupTimeoutMs`
deadline. A cleanup rejection emits `cleanup_error`; a timeout emits
`cleanup_timeout`. Either outcome rejects the request, poisons the sidecar,
sets it to `failed`, and prevents `ready` or same-process reuse. The sidecar
does not pretend that unconfirmed cleanup succeeded; a failed instance cannot
be started again.

## Persistent protocol host

Run `bun run protocol` for the long-lived JSONL host. It reads UTF-8 commands
from stdin and writes only version-two JSONL protocol events to stdout. Human
diagnostics belong on stderr. The supported commands are:

```json
{"protocol_version":2,"type":"generate","generation_id":"uuid","epoch":1,"prompt":"Reply exactly OK."}
{"protocol_version":2,"type":"generate","generation_id":"uuid","epoch":2,"messages":[{"role":"user","text":"Remember the word quartz."},{"role":"assistant","text":"ACK_1"},{"role":"user","text":"What was the word?"}]}
{"protocol_version":2,"type":"cancel","generation_id":"uuid","epoch":1}
{"protocol_version":2,"type":"health"}
{"protocol_version":2,"type":"shutdown"}
```

The host emits `ready`, accepts one active generation, forwards genuine
`text_delta` events, and emits at most one generation terminal event. The
generation events are `accepted`, `text_delta`, `completed`, `cancelled`, and
`failed`; local host events are `ready`, `health`, and `shutdown`. Every
generation-scoped frame carries `generation_id` and `epoch`.

The host's default bounded deadlines are 30 seconds for startup, 5 seconds
for terminal cleanup before reuse, 5 seconds for shutdown, and 5 seconds for
each stdout write that reports backpressure. Its protocol writer retains at
most 64 pending frames and 512 KiB of pending encoded output. A stalled
`drain` or an output-buffer limit is fatal, aborts pending input, and makes
`ProtocolServer.run()` return a nonzero exit code. Core's cancellation and
shutdown deadlines provide outer process containment. An output-limit terminal is held
until provider cancellation and cleanup finish; a cleanup failure wins and
poisons the host.

If shutdown exceeds its deadline, the host emits one generation-scoped
`shutdown_timeout` failure when needed, marks the host fatal, and invokes its
forced-exit hook with code 1. A clean `shutdown` acknowledgement is emitted
only after the sidecar closes successfully. `cleanup_timeout` and
`cleanup_error` are fatal even if an earlier terminal frame was already
observed; they never produce a second generation terminal frame.

The version-two protocol enforces a 256 KiB frame, 64 KiB prompt or aggregate
structured-context text, 16 KiB delta, 256 KiB response, and 128-byte identity
bounds. Invalid framing or fields are rejected by the parser; Core fails
closed on future or mismatched generation identities. For structured context,
the host maps user text to pi-ai `UserMessage` and completed assistant text to
a typed pi-ai `AssistantMessage` with local adapter metadata. When present,
trusted guidance is mapped to pi-ai `Context.systemPrompt`. It does not
forward provider payloads, response IDs, usage, `sessionId`, or
`providerSessionState`, and does not retain semantic history. Core owns the
generation lifecycle and semantic state; this host owns provider and process
transport state only.

## V3 transport proof and opt-in deterministic continuation

The sidecar contains a strict V3 contract parser and a transport-only
ordinary-function-tool adapter. It maps immutable Lilavel `ToolSpec` values to
collision-checked provider aliases, retains raw Responses terminal function
items only inside the active transport operation, and maps each provider call
ID to a generation-local `call-N` ID. It independently parses the raw argument
string, requires an exact match with pi-ai's finalized normalized object, and
marks malformed JSON or non-object JSON non-executable. Missing, duplicate, or
ambiguous correspondence fails the provider turn closed.

The ordinary production entry point remains V2. The explicit `protocol:v3`
entry point now runs a bounded active-generation loop for deterministic P4-B
coverage. One generation may span sequential provider turns; the sidecar keeps
only its bounded replay context, generation-local ID map, alias map, and pending
round. `ToolWaitState` accepts results only for the exact pending generation,
epoch, round, and ordered call-ID set, then consumes the batch before starting
the continuation.

Each provider turn has a fresh raw-call collector. `tool_calls` is emitted only
after that provider turn and its iterator/result cleanup settle. Cleanup failure
outranks cancellation, preserves its safe failure code, and poisons reuse. The
sidecar never authorizes or executes a tool; P4-D's single Discord capability is
exposed and bound only by the application adapter's trusted composition.

When Core launches the host on Windows, it starts the default `npx` launcher
with `CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP`, verifies Job Object
containment, and resumes it only after the containment handle is attached.
The separate console process group lets Core receive an operator Ctrl+C and
send the sidecar's graceful cancel-then-shutdown sequence instead of allowing
the child to terminate in the middle of a JSONL frame. Forced containment
therefore covers the `npx`/Node/cmd/Bun descendant tree. If containment setup
fails, Core fails closed. The POSIX fallback targets the owned process and
does not claim the same descendant guarantee.

For a local diagnostic run, use `bun run smoke -- "prompt"`. Its diagnostic
event JSON is temporary output and does not print credentials, request text,
provider errors, headers, or tokens. The temporary live probes are
`bun run probe -- cancel-recovery` and `bun run probe -- isolation`; both use
the same sidecar process for their checks.
