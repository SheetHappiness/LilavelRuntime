# PHASE 1 — Capability Spikes

Status: CLOSED

## Baseline

Repository: `C:\LilavelRuntime`
Branch: `main`
Baseline SHA: `9ad6a6fcb79661be542580b4ab3c629148063028`

Production repository state was unchanged during the capability spikes.

## Goal

Verify two high-leverage external capabilities before designing the
persistent Lilavel agent runtime:

1. Native model tool calling through pinned `@oh-my-pi/pi-ai@18.1.2`.
2. Compatibility with the published Neuro SDK WebSocket protocol.

No production AgentRuntime, ToolRegistry, autonomy, MCP integration,
or Discord redesign was in scope.

## Decisions

### 1A — OMP/pi-ai

Verdict: `USE PINNED 18.1.2`

The pinned package supports:

- tool definitions and schemas;
- `Context.tools`;
- streamed `toolcall_start`, `toolcall_delta`, `toolcall_end`;
- finalized `ToolCall`;
- terminal `toolUse`;
- `validateToolCall()` / `validateToolArguments()`;
- `ToolResultMessage` for continuation.

`streamSimple()` remains one model turn.
Lilavel must own the tool-execution loop.

Do not adopt OMP `AgentSession` or `pi-agent-core`.

### 1B — Neuro SDK

Verdict: `COMPATIBLE`

The published protocol maps cleanly into the planned Lilavel runtime:

- `context` → `WorldEvent`
- `actions/register` / `actions/unregister` → ToolRegistry capability lifecycle
- incoming `action` → `ToolCall`
- `action/result` → `ToolResult`
- force priority → wake / interrupt priority

Neuro compatibility is an environment protocol boundary.
It does not own Lilavel lifecycle, identity, cognition, memory,
or ToolRegistry.

## Evidence

### OMP

Local pinned package inspection confirmed tool-call types,
stream events, validation helpers, stop reason and tool-result continuation.

Current production sidecar does not expose these capabilities:
tool-call output is currently classified as `unsupported_output`.

Deterministic probe result:

`current sidecar tool call: unsupported_output`
`provider tool execution: false`

### Neuro SDK

A deterministic compatibility probe completed:

`startup`
→ `context(silent)`
→ `actions/register`
→ `actions/force(priority=critical)`
→ `action`
→ `actions/unregister`
→ `action/result`

Result: PASS.

## Architecture Consequences

- Future AgentRuntime owns ToolRegistry.
- AgentRuntime owns authorization, permissions and external side effects.
- `pi-ai` owns provider-specific tool-call transport and normalization only.
- Tool argument validation from `pi-ai` is not an authorization boundary.
- Tool calls/results are ephemeral generation context unless explicitly promoted elsewhere.
- ConversationCore remains owner of canonical conversational history.
- ModelRuntime remains owner of physical generation lifecycle.
- Neuro is implemented, if used, as a replaceable EnvironmentAdapter.
- Environment-specific identifiers do not enter canonical identity/history merely by observation.
- `critical` Neuro priority is an interrupt hint, not general-purpose cancellation.

## Unknowns

- Live `gpt-5.6-luna` tool calling through the current subscription/provider path is UNVERIFIED.
- Provider behavior for `toolMode: "code_mode_only"` is UNVERIFIED.
- Tool calling with current `preferWebsockets: false` configuration is UNVERIFIED.
- Cancellation after a tool call has been emitted while an external side effect is pending is UNVERIFIED.
- External interoperability against Randy/Gary is UNVERIFIED.
- Production Neuro adapter is NOT IMPLEMENTED.
- Production sidecar protocol v3 is NOT IMPLEMENTED.

## Validation

- TypeScript check: PASS
- Focused lifecycle/protocol tests: 29 PASS
- OMP deterministic tool probe: PASS
- Neuro deterministic compatibility probe: PASS
- `git diff --check`: PASS
- Production repository worktree after spikes: clean
- Live provider probe: UNVERIFIED
- Live external Neuro server interoperability: UNVERIFIED

## Exit State

Production code changed: no.

Implementation SHA at phase exit:
`9ad6a6fcb79661be542580b4ab3c629148063028`