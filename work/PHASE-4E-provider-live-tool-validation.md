# PHASE 4E — Provider/Live Tool Validation + Adversarial Hardening

Status: `BLOCKED`
Baseline branch: `main`
Baseline SHA: `3cfed30115820e7af32198008e52a858d0586567`
Implementation branch: `main`
Implementation SHA: `5e1be43` (harness/evidence continuation; post-run audit)

## Goal

Close the provider-backed and real-Discord evidence left open by P4-D while
preserving the explicit V3 composition, application-owned authorization, one
trusted DM scope, conservative effect certainty, joined settlement, stale
fencing, and unchanged default V2/no-tool behavior.

## Scope and implementation outcome

This was a Linux validation phase. No production/default activation, provider
transport, or Discord behavior was changed. A harness-only first-turn
`toolChoice=required` launcher and its deterministic regression test were
added; the ordinary V2/V3 entry points retain their existing defaults. The
existing deterministic seams were audited against the P4-E adversarial matrix
and the authoritative validation matrix was rerun.

The configured live path is the repository-pinned model-sidecar provider
composition:

```text
openai-codex / gpt-5.6-luna / openai-codex-responses
```

The sidecar uses its supported auth broker discovery. Credentials and raw
provider payloads were not persisted or recorded.

## Preflight evidence

- `main` was clean at the canonical baseline SHA above.
- P4-D implementation `8ba0ea2e16479aced3f52653520381a0792fde43` is an
  ancestor of `main`.
- P4-D closure `3cfed30115820e7af32198008e52a858d0586567` is an ancestor of
  `main`.
- A dedicated task worktree was created from that exact baseline on
  `phase4e-provider-live-hardening`.
- In the original blocked run, `LILAVEL_DISCORD_BOT_TOKEN` was absent from
  that Work environment. The token value was never printed, copied, or
  persisted.

## Deterministic inherited and hardening evidence

`PASS` — the committed P4-A through P4-D boundaries remained intact. The
focused hardening surfaces covered:

- exact ordered result batches, missing/extra/wrong-order results, generation,
  epoch, and round fencing, second pending batches, and terminal-while-waiting
  rejection;
- cancellation and supersession joins, stale late-result discard,
  continuation fencing, sidecar/provider failure while an executor is
  outstanding, shutdown with outstanding work, and uncontainable-executor
  fail-closed poisoning;
- unknown/unexposed tools, strict non-coercive validation, denied and
  unavailable calls, destination-like model arguments, provider alias
  collisions, and one-shot Discord execution;
- raw argument correspondence, malformed/repaired/ambiguous provider evidence,
  and evidence/diagnostic secret exclusion.

No new deterministic patch was required. Focused results were:

- runtime P4-C/P4-B authorization and lifecycle tests: `44 passed`;
- Discord P4-D executor/composition tests: `12 passed`;
- sidecar V3 transport/state/loop/adversarial tests: `19 passed`.

## Provider-backed evidence

The supported sidecar smoke command was run once with a non-tool prompt:

```text
apps/model-sidecar: npx --yes bun@1.4.0 run smoke -- "Reply exactly P4E_PROVIDER_AUTH_OK."
```

Observed safe evidence:

- `PASS` — local sidecar startup/auth-discovery boundary emitted `ready` for
  the pinned provider/model/API and emitted `provider_dispatch`.
- `FAIL` — the command exited with code `1` and emitted the safe terminal code
  `cleanup_error`. No provider completion was observed.
- `BLOCKED` — provider-backed V3 tool selection and continuation could not be
  established because no successful live provider turn was available.

This result is not treated as tool-selection, continuation, Discord, or
provider-entitlement evidence. No provider-native payload, request text, or
credential value is recorded.

## Credentialed retry and cleanup-error root cause

Earlier retry date: `2026-09-09`, Linux, canonical `main` at
`653e316a83f5a2f0b9d130c1c7795d832e01adba`.

- `PASS` — repository-locked dependency preparation completed with
  `apps/model-sidecar: npx --yes bun@1.4.0 install --frozen-lockfile`.
  The local graph resolved the pinned `@oh-my-pi/pi-ai@18.1.2` and
  `@oh-my-pi/pi-catalog@18.1.2` packages and their Linux native addon.
- `FAIL` — the exact smoke command was rerun after that preparation:

  ```text
  apps/model-sidecar: npx --yes bun@1.4.0 run smoke -- "Reply exactly P4E_PROVIDER_AUTH_OK."
  ```

  It exited `1` and emitted only the safe sequence `ready`,
  `request_received`, `provider_dispatch`, `error(code=cleanup_error)`.
  No provider first-event, completion, or V3 tool turn was observed.
- `PASS` — a temporary direct stream probe using only safe metadata showed
  `iterator.next()` rejected with `MissingApiKeyError`; `iterator.return()`
  fulfilled; and `stream.result()` rejected with `MissingApiKeyError`.
  Supported auth discovery reported no configured broker, no active
  `openai-codex` credential, and no disabled `openai-codex` credential. The
  provider endpoint was therefore not reached.
- `PASS` — the `cleanup_error` was root-caused as error masking in the
  existing sidecar lifecycle: `ModelSidecar.#cleanup()` waits on both
  `iterator.return()` and pi-ai `stream.result()` and maps any rejection to
  `cleanup_error`, overwriting the original provider/auth failure. The
  `provider_dispatch` event is emitted before the lazy auth/provider stream
  produces its first event, so it is not provider-contact evidence.
- `PASS` — the retry preflight boolean check found
  `LILAVEL_DISCORD_BOT_TOKEN` present; its value was not printed, copied, or
  persisted. This is not live Discord evidence and is not the post-provider
  gate, which was not reached.

The first unrestricted launcher attempt before local installation failed
before sidecar startup because transient `npx` resolution selected
`@oh-my-pi/pi-natives@18.1.15` without its Linux native addon. That launcher
failure is separate from the reproduced P4-E `cleanup_error`; installing the
repository lockfile removed that local setup obstruction without changing
tracked files.

### Latest credentialed Gate 1 retry

Retry date: `2026-09-09`, Linux, canonical `main` at `f090258`.

- `BLOCKED` — the first exact smoke invocation in the restricted shell could
  not resolve `registry.npmjs.org` (`npx` reported `EAI_AGAIN`) before Bun or
  the sidecar started. It produced no live auth or provider evidence.
- `PASS` — the exact supported smoke command was then rerun with the required
  network access:

  ```text
  apps/model-sidecar: npx --yes bun@1.4.0 run smoke -- "Reply exactly P4E_PROVIDER_AUTH_OK."
  ```

  It exited `0` and emitted the safe lifecycle sequence `ready`,
  `request_received`, `provider_dispatch`, `provider_first_event`,
  `text_delta`, and `completed`.
- `PASS` — `ready` reported the pinned composition
  `openai-codex / gpt-5.6-luna / openai-codex-responses` after the sidecar's
  supported auth-discovery/startup boundary completed.
- `PASS` — `provider_first_event` with provider event type `start` proves the
  lazy authenticated provider stream was contacted; this is stronger than
  the pre-stream `provider_dispatch` marker.
- `PASS` — the normal live completion was received with the requested safe
  completion marker `P4E_PROVIDER_AUTH_OK` and terminal `textLength=20`.
- `PASS` — the process exited successfully after the `completed` frame, with
  no `cleanup_error` or `cleanup_timeout`; provider/sidecar settlement was
  therefore clean for this smoke generation.

This closes Gate 1. No provider payload, credential, header, or token was
persisted.

## Real Discord evidence

Prior run: `BLOCKED` — `LILAVEL_DISCORD_BOT_TOKEN` was missing from the Work
environment. Consequently, the normal admitted one-to-one DM scope could not
be established, the explicit P4-D tool-enabled composition was not launched,
and no live Discord send was attempted. There is therefore no live attempt
count, destination confirmation, `ok/confirmed` effect, or live ToolResult
continuation to claim.

The deterministic P4-D proof remains `PASS`: its bound fake DM executor makes
one send attempt at most, suppresses mentions, maps confirmed success to
`ok/confirmed`, and never retries unknown delivery.

On the earlier 2026-09-09 retry, the required provider-V3 completion and clean
settlement gate remained `BLOCKED`, so the post-provider token gate, trusted
inbound DM admission, and `discord.send_message({text})` live proof were not
run. Live Discord send attempts: `0`. No uncertain effect was created.

### Latest credentialed Gate 2 attempt

- `PASS` — the adapter's supported boolean-only reader found
  `LILAVEL_DISCORD_BOT_TOKEN` present. The value was not printed, copied, or
  persisted. The restricted-shell preflight was `BLOCKED` by uv cache write
  permissions; the escalated supported check completed successfully.
- `PASS` — the existing explicit `DiscordTextEdge(tool_enabled=True)`
  composition connected to Discord and reached the one-DM waiting state.
  The default V2/no-tool launcher was not changed or globally enabled.
- `BLOCKED` — during the bounded listener window no user-authored one-to-one
  DM arrived, so the adapter admitted no trusted inbound scope and created no
  Core session or V3 generation. The listener was stopped before any external
  effect. Live Discord send attempts: `0`.

Gate 2 was stopped at its required inbound-scope boundary. No Discord send,
ambiguous effect, provider tool call, ToolResult continuation, or live
canonical-history mutation occurred.

### Current Gate 2-only retry

Date: `2026-09-09`, Linux, working tree `main`.

- `PASS` — a boolean-only shell check found
  `LILAVEL_DISCORD_BOT_TOKEN` present. The value was not printed, copied,
  persisted, or passed through diagnostic output.
- `PASS` — the existing explicit `DiscordTextEdge(tool_enabled=True)`
  composition imported and was constructed for the bounded listener. No
  global tool registry or V3 default activation was changed.
- `BLOCKED` — the bounded listener exited without admitting a user-authored
  one-to-one DM. No trusted inbound scope, Core session, or V3 generation was
  established.
- `PASS` — live Discord send attempts: `0`; no destination, model arguments,
  mention-bearing payload, or delivery outcome was created.
- `UNVERIFIED` — live provider selection of `discord.send_message`, exact
  `{text}` model arguments, P4-A raw correspondence, ToolResult return,
  same-generation continuation, final completion, and live history separation;
  these require an admitted trusted DM and were not run.

Required user action to resume Gate 2: send exactly one user-authored
one-to-one DM to the Lilavel bot, then rerun this Gate 2 proof. No Discord or
user IDs may be supplied to or hardcoded by the runner.

### Current Gate 2 retry after inbound DM

Date: `2026-09-09`, Linux, working tree `main`.

- `PASS` — the boolean-only token check found
  `LILAVEL_DISCORD_BOT_TOKEN` present; its value was not printed, copied, or
  persisted.
- `PASS` — one user-authored one-to-one DM established the trusted adapter
  scope, created exactly one Core session, and launched the explicit
  `tool_enabled=True` V3 composition. The default V2/no-tool path was not
  changed or globally enabled.
- `PASS` — the V3 lifecycle evidence contained one `execution_settled` record.
  Because the explicit Discord registry exposes only `discord.send_message`,
  this establishes that the admitted generation reached the exposed Discord
  tool execution boundary.
- `PASS` — the Core history boundary check observed two history entries with
  no ToolCall/ToolResult or Discord identifier metadata in canonical history.
- `UNVERIFIED` — the captured safe summary did not retain the adapter factory's
  typed `ToolResult` status/effect or an HTTP operation count. Therefore the
  exact `ok/confirmed` outcome, exact physical send-attempt count, raw P4-A
  correspondence fields, and final provider completion are not claimed.
  No retry was made.

This retry is not sufficient to close Gate 2. A future run must capture the
typed adapter settlement (`ok/confirmed`), one-send bound, exact raw
correspondence, same-generation continuation, and final completion before
claiming those results.

### Gate 2 evidence instrumentation and fresh live proof

Date: `2026-09-09`, Linux, continuation from commit `ac4f303`.

- PASS — boolean-only shell check found `LILAVEL_DISCORD_BOT_TOKEN` present;
  the value was not printed, copied, persisted, or passed through diagnostics.
- PASS — added bounded status-only evidence at existing seams: V3
  `tool_requested` records `raw_correspondence=pass`; application session
  evidence records authorization; the Discord executor exposes only an
  aggregate admitted-send count; and the explicit edge exposes a redacted
  proof snapshot containing generation/epoch/round, lifecycle/status/effect,
  authorization, exposed tool names, and send-attempt count. No arguments,
  message bodies, destinations, Discord IDs, provider payloads, credentials,
  or exception bodies are recorded.
- PASS — focused deterministic validation: runtime tool-loop `27 passed`, Core
  runtime `11 passed`, Discord tool/edge `35 passed`; changed runtime and
  Discord packages passed Ruff, format check, and Pyright.
- UNVERIFIED — the Core package Pyright command also reports three existing
  `process_containment.py` errors for `ctypes.get_last_error` typing on Linux;
  no changed-file error was reported.
- PASS — explicit `DiscordTextEdge(tool_enabled=True)` listener was started
  after the deterministic checks; ordinary defaults and global V3/tool
  activation were unchanged.
- BLOCKED — the bounded fresh proof window admitted no user-authored
  one-to-one DM (`session_count=0`). The required trusted scope was therefore
  not established, and the proof stopped before generation, tool selection,
  raw correspondence, authorization, ToolResult, continuation, completion,
  or any Discord operation.
- PASS — fresh live Discord send-attempt count: `0`; no delivery effect was
  created, so there was no retry and no ambiguous effect to classify.
- UNVERIFIED — live end-to-end claims remain open: provider selection of
  `discord.send_message`, raw correspondence, authorized send with
  `ok/confirmed`, same-generation ToolResult submission/continuation, final
  completion, and live canonical-history separation.

Required user action: send one new user-authored one-to-one DM to the Lilavel
bot while the bounded Gate 2 listener is running. The DM must explicitly
request `discord.send_message` and include a fresh unique marker. No Discord
or user IDs should be supplied or hardcoded.

### Gate 2 fresh attempt `NEW-2`

Date: `2026-09-09`, Linux, continuation from commit `58bf490`.

- PASS — boolean-only shell check found `LILAVEL_DISCORD_BOT_TOKEN` present;
  the value was not printed, copied, persisted, or passed through diagnostics.
- PASS — the explicit `DiscordTextEdge(tool_enabled=True)` listener started
  with the redacted proof instrumentation; defaults remained unchanged.
- BLOCKED — the user reported the bot offline and interrupted the bounded
  listener before any inbound DM was admitted (`session_count=0`). This run
  did not establish a trusted DM scope and stopped before generation/tool
  selection.
- PASS — fresh `NEW-2` Discord send-attempt count: `0`; no delivery effect was
  created and no retry was made.
- UNVERIFIED — provider selection, raw correspondence, authorization,
  ToolResult status/effect, same-generation continuation, final completion,
  and live history separation remain unverified.

### Gate 2 instrumented capture `NEW-3`

Date: `2026-09-09`, Linux, continuation from commit `82e64a1`.

- PASS — boolean-only shell check found `LILAVEL_DISCORD_BOT_TOKEN` present;
  the value was not printed, copied, persisted, or passed through diagnostics.
- PASS — focused deterministic validation remained satisfied from the
  instrumentation commit: runtime tool-loop `27 passed`, Core runtime
  `11 passed`, Discord tool/edge `35 passed`; changed runtime and Discord
  packages passed Ruff, format check, and Pyright.
- PASS — exactly one new instrumented listener used explicit
  `DiscordTextEdge(tool_enabled=True)` composition; activation defaults and
  P4-A–D boundaries were unchanged.
- BLOCKED — the `NEW-3` bounded listener admitted no user-authored
  one-to-one DM (`session_count=0`) before timeout. Trusted inbound scope was
  not established, so the proof stopped before generation/tool selection.
- PASS — `NEW-3` Discord send-attempt count: `0`; no executor settlement,
  ToolResult, delivery effect, or retry was created.
- UNVERIFIED — raw-argument correspondence, authorization,
  generation/epoch/round correlation, ToolResult submission, provider
  continuation, final completion, and live canonical-history separation;
  these require the admitted trusted DM scope.

### Gate 2 live capture `NEW-4`

Date: `2026-09-09`, Linux, continuation from commit `e7e90f8`.

- PASS — boolean-only check found `LILAVEL_DISCORD_BOT_TOKEN` present; its
  value was not printed, copied, persisted, or passed through diagnostics.
- PASS — one explicit `DiscordTextEdge(tool_enabled=True)` listener was
  started; ordinary activation defaults and P4-A–D boundaries were unchanged.
- PASS — one inbound DM established the trusted one-to-one scope
  (`session_count=1`).
- FAIL — the live route failed at the adapter's
  `conversation.presentation.complete` action with safe error type
  `ValueError` before the listener's evidence snapshot executed. No raw
  arguments, message body, Discord ID, provider payload, or exception body was
  recorded.
- UNVERIFIED — exact Discord send-attempt count, executor settlement,
  ToolResult status/effect, raw correspondence, authorization,
  generation/epoch/round correlation, ToolResult submission, provider
  continuation, final completion, and live canonical-history separation;
  the failed capture did not establish these fields.

### Gate 2 live capture `NEW-5`

Date: `2026-09-09`, Linux, continuation from commit `e7e90f8`.

- PASS — boolean-only check found `LILAVEL_DISCORD_BOT_TOKEN` present; its
  value was not printed, copied, persisted, or passed through diagnostics.
- PASS — one bounded listener used the supported explicit
  `DiscordTextEdge(tool_enabled=True)` composition. The exposed tool set was
  exactly `discord.send_message`; defaults and P4-A–D invariants were
  unchanged.
- PASS — one new inbound DM established trusted one-to-one scope
  (`session_count=1`).
- PASS — one V3 generation was created and accepted with generation ID
  `a2cd72d7-875b-4c83-95d0-6f93ce171d54`, epoch `1`; the physical trace then
  reached `completed` and terminal `completed`.
- PASS — provider continuation was not needed because no tool call was
  selected; the ordinary non-tool generation reached final completion.
- PASS — canonical history entry types were only `ContextMessage`, with no
  `ToolCall` or `ToolResult` metadata.
- PASS — exact Discord send-attempt count was `0`; no executor settlement,
  ToolResult, delivery effect, or retry occurred.
- UNVERIFIED — provider selection of `discord.send_message`, raw-argument
  correspondence, authorization, ToolResult status/effect, same-generation
  ToolResult submission/continuation, and live tool-path final completion.
  The unique live DM did not exercise the requested tool path.

### Provider tool-selection root-cause probe

Date: `2026-09-09`, Linux, read-only provider investigation after `NEW-5`.
No Discord adapter, trusted DM scope, application executor, or Discord send was
started.

- PASS — the repository-pinned `@oh-my-pi/pi-ai@18.1.2` and
  `@oh-my-pi/pi-catalog@18.1.2` source was inspected at the provider boundary.
  `ToolLoopSidecar` maps the one application spec to one provider alias and
  assigns it to `Context.tools`; the Codex Responses builder consumes that
  field. The safe derived request summary contained one application tool, one
  provider alias (`discord_send_message`), and one effective provider tool in
  the Responses-Lite `additional_tools` item.
- PASS — the exact effective pinned model/provider metadata was
  `openai-codex / gpt-5.6-luna / openai-codex-responses`, with
  `toolMode=code_mode_only`, `useResponsesLite=true`, and native tool-choice
  support flags `supportsToolChoice=true`, `supportsForcedToolChoice=true`,
  and `supportsNamedToolChoice=true`. The catalog's `supportsTools` value was
  absent, which is the pinned catalog's positive/unspecified native-tool
  signal, not an unsupported value.
- PASS — the derived default request had effective `tool_choice=auto` and one
  effective tool. The forced variant had effective `tool_choice=required` and
  the same one effective tool. Responses-Lite moves the tool into
  `additional_tools`; it does not drop the definition.
- PASS — source inspection found no `toolMode` branch in the pinned pi-ai
  Codex request builder. The observed provider-specific mode is catalog
  metadata; the active pi-ai request behavior is governed by
  `useResponsesLite`, `Context.tools`, and the optional `toolChoice` option.
- PASS — one direct authenticated live selection probe used the supported auth
  broker and `streamSimple` with exactly one ordinary function tool and
  `toolChoice=required`. It did not instantiate the Discord adapter or call an
  executor. Safe observed event classes were `start`, `toolcall_start`, nine
  `toolcall_delta` events, `toolcall_end`, and `done`.
- PASS — the live stream finalized exactly one ToolCall with provider alias
  `discord_send_message`, ended with `done.reason=toolUse` and
  `message.stopReason=toolUse`, and its provider result settled. This proves
  native tool-call selection through the current subscription path and pinned
  pi-ai API without creating an external effect.
- PASS — the prior `NEW-5` no-call completion is mechanically explained by
  the V3 provider adapter omitting `toolChoice`: its effective mode was
  `auto`, so a normal text completion was valid even though the single tool
  definition was delivered. No transport-loss or `code_mode_only` block was
  observed.
- PASS — no Discord tool execution occurred during this investigation; live
  Discord send-attempt count remains `0` and no delivery effect was created.
- UNVERIFIED — live raw-argument correspondence, application authorization,
  ToolResult status/effect, same-generation continuation, final completion
  after a ToolResult, and live canonical-history separation remain unverified
  because this probe stopped immediately after provider ToolCall selection.

### Final Gate 2 live-proof harness attempt (stopped)

Date: `2026-09-09`, Linux, implementation commit `7d09c17`. This was the one
and only post-harness live interaction; no retry was made.

- PASS — the boolean-only token preflight reported
  `LILAVEL_DISCORD_BOT_TOKEN` present. The token value was not printed,
  copied, persisted, or included in diagnostics.
- PASS — the explicit `DiscordTextEdge(tool_enabled=True)` composition was
  started with one bound V3 tool session and the harness-only
  `protocol:v3:live-proof` sidecar. `toolChoice=required` was configured only
  for the first provider turn; the ordinary V3 launcher remains unchanged.
- PASS — one user-authored one-to-one DM was admitted and established one
  trusted adapter scope. The harness then terminated without a safe capture
  record after its bounded completion wait raised `RuntimeError`.
- FAIL — the final harness did not produce authoritative end-to-end evidence
  for finalized `discord.send_message`, raw correspondence, authorization,
  ToolResult, continuation, or final completion. The failure was not retried.
- UNKNOWN effect — because the harness ended before emitting the redacted
  executor snapshot, the live Discord send-attempt count and delivery outcome
  cannot be established from this run. The interaction is conservatively
  recorded as `effect=unknown`; no second send or DM was attempted.
- UNVERIFIED — exact live send-attempt count, `ok/confirmed` ToolResult,
  same-generation/epoch/round ToolResult submission, provider continuation,
  final provider completion, and live canonical-history separation. The
  deterministic equivalents remain `PASS`.

### Second final Gate 2 live-proof action

Date: `2026-09-09`, Linux. This was a new controlled DM run after the explicit
user request to continue; it is not a retry after an ambiguous delivery. The
run-specific marker was generated in trusted guidance and was not emitted in
diagnostics.

The harness wrapper returned `FAIL` with `RuntimeError` after the provider
generation had completed. Its safe snapshot nevertheless captured the
following authoritative lifecycle evidence (no provider payload, arguments,
message body, credential, or Discord ID was logged):

- PASS — boolean-only token preflight: present.
- PASS — explicit composition: one exposed tool, exactly
  `discord.send_message`; tool choice was `required` on the first harness
  turn only. Production V2/no-tool and default V3 `auto`/unset behavior were
  unchanged.
- PASS — exactly one finalized tool-call admission: `call_count=1`,
  generation `a2ad59a3-a21c-4dfb-8864-2b989ef00dd6`, `epoch=1`, `round=1`.
  The exposed application name was `discord.send_message`; the provider
  alias remains transport-local.
- PASS — P4-A raw-argument correspondence: `pass`.
- PASS — application authorization: `allowed`; destination remained the
  trusted application-bound channel and was not a model argument.
- PASS — exact Discord tool send-attempt count: `1`; executor settlement:
  `settled`; no retry occurred.
- PASS — ToolResult status/effect: `ok` / `confirmed`.
- PASS — ToolResult submission: one `result_consumed` record for the same
  generation `a2ad59a3-a21c-4dfb-8864-2b989ef00dd6`, `epoch=1`, `round=1`.
- PASS — provider continuation and final provider completion: the same
  generation emitted post-tool `text_delta` and terminal `completed`.
- UNVERIFIED — live canonical-history snapshot from this run: the wrapper
  failed before its final history assertion emitted. The deterministic
  explicit composition and prior live non-tool history evidence remain
  `PASS`; no claim is made that the missing snapshot was captured here.
- FAIL — wrapper-level final capture: `RuntimeError` after the above safe
  lifecycle records. This did not create an additional Discord attempt and
  did not induce an ambiguous delivery.

### Read-only post-run canonical-history audit

Date: `2026-09-09`, Linux. No provider process, Discord client, external
effect, or persistence write was started for this audit.

- PASS — the existing live-proof harness constructs `ConversationCore` without
  a `store` argument. The Core constructor therefore selects the repository
  default `SQLiteConversationStore(":memory:")`; the harness does not provide
  a filesystem path or export the store after the run.
- PASS — the supported adapter history reader is process-local:
  `DiscordTextEdge.conversation_history_for_channel()` delegates to the live
  router session and Core's `history`. The router keeps sessions in an
  in-process map; shutdown closes the session runtime and does not persist or
  export its Core store.
- PASS — read-only filesystem inspection found no SQLite database or other
  live-proof persistence artifact, and no proof process remained from which
  the process-local history could be queried. The recorded generation ID is
  lifecycle evidence only and is not a recoverable Core scope or store.
- PASS — source ordering establishes the invariant for any still-live run:
  Core appends the accepted user message before starting generation, and
  appends the assistant only in `commit_assistant()` before emitting
  `ConversationCompleted`. ToolCall and ToolResult are not Core canonical
  message types; the SQLite schema permits only `user` and `assistant` roles.
- PASS — deterministic history-isolation evidence remains authoritative:
  the explicit fake-tool round records only the accepted user and final
  assistant `ContextMessage`s, with no ToolCall/ToolResult transcript; the
  explicit Discord composition keeps the destination in the bound adapter
  and the trusted guidance separate from Core history.
- UNVERIFIED — this specific live run's accepted user ContextMessage, final
  assistant completion, absence of ToolCall/ToolResult, and absence of Discord
  destination/ID metadata cannot be recovered from persisted state because
  the only store was in-memory and the wrapper emitted no history snapshot.
  No IDs, message bodies, provider payloads, or trusted guidance were
  reconstructed or manufactured.
- PASS — the wrapper `RuntimeError` is mechanically downstream of the safe
  provider/tool records: the harness waits through `edge.wait_idle()`, whose
  only live failure branch raises when the runtime has a recorded kernel or
  route failure. The snapshot already contained provider terminal
  `completed`, executor settlement, confirmed ToolResult, and same-generation
  result consumption. No second attempt occurred.
- UNVERIFIED — the exact child route/presentation exception and whether Core's
  assistant commit had completed before that route failure were not retained.
  The wrapper exception has no code path that deletes or rolls back canonical
  messages, but the post-run in-memory state is unavailable, so its actual
  assistant-commit outcome cannot be claimed.

### Live-proof harness hardening (final proof not executed)

Date: `2026-09-09`, Linux. No provider turn or Discord send was run for this
change.

- PASS — the opt-in live proof now gives `ConversationCore` a task-owned
  temporary filesystem SQLite path. It uses the existing
  `SQLiteConversationStore` API and unchanged Core append/load semantics; the
  path and database contents are not emitted in evidence.
- PASS — evidence collection is in a `finally` path. It takes the redacted
  lifecycle snapshot before teardown, records wait/close/start/store exception
  types and cause types without exception text, closes the live store, then
  reopens the SQLite file through repository-supported load-only calls.
- PASS — an application exception remains `FAIL`; collection does not convert
  a failed `edge.wait_idle()` or presentation/teardown failure into `PASS`.
  A timeout remains `BLOCKED`.
- PASS — `edge.wait_idle()` is a wrapper over the runtime's recorded kernel or
  route failure; it is not itself a provider or Core terminal result. The
  existing deterministic presenter sink-failure regression proves that a
  presentation failure reaches this wrapper path. The harness now snapshots
  Core runtime evidence so a future live run can distinguish completed Core
  semantics from a post-provider presentation/router failure.
- UNVERIFIED — the exact child failure for the already completed live run
  remains unavailable: its earlier safe snapshot did not include Core runtime
  evidence and its store was in-memory. The recorded provider completion rules
  out neither a specific presentation child exception nor a Core assistant
  commit for that historical process.
- PASS — deterministic clean-completion and post-provider presentation-failure
  harness tests both recover the closed SQLite history. They prove the
  accepted user and required assistant are canonical, roles are limited to
  `user`/`assistant`, ToolCall/ToolResult and Discord metadata are outside the
  canonical schema, trusted guidance is separate, and the failure case remains
  `FAIL` while still returning the history audit.
- PASS — focused validation: harness Ruff, strict Pyright, two new harness
  tests, the existing presenter-failure `wait_idle()` regression, the explicit
  tool composition regression, and `git diff --check`.

## Live lifecycle gates

- Live non-tool provider auth, contact, completion, and clean settlement:
  `PASS`; see the latest credentialed Gate 1 retry above.
- Live provider tool definition delivery: `PASS` for `NEW-5`; the explicit
  provider-facing tool set contained only `discord.send_message`.
- Live provider tool selection: `PASS`; an isolated authenticated live probe
  with the same pinned provider/model/API and explicit `toolChoice=required`
  finalized exactly one native `discord_send_message` ToolCall. This probe
  stopped before any application or Discord execution.
- Live raw-argument/call-ID correspondence: `PASS` for the second final action;
  the safe snapshot recorded `raw_correspondence=pass` on the one admitted
  call.
- Live same-generation ToolResult continuation: `PASS` for the second final
  action; one result was consumed at the matching generation/epoch/round.
- Live ordinary final completion: `PASS` for `NEW-5`; live tool-path final
  completion: `PASS` for the second final action.
- Live provider-backed contained error-result continuation: `UNVERIFIED`; no
  safe live V3 turn was available and no extra external effect was created.
- Real-provider cancellation/supersession: `UNVERIFIED`; existing
  deterministic P4-B/P4-D evidence remains authoritative. No ambiguous live
  Discord delivery was induced.
- Canonical history boundary: `PASS` under deterministic composition and the
  `NEW-5` live non-tool turn; final live tool metadata separation is
  `UNVERIFIED` for the historical live tool run; the hardened harness is ready
  to make this live assertion from a closed task-owned SQLite file.
- Default ordinary behavior and production activation: `PASS`; V2/no-tool
  remains the default and model-selected external tools remain explicit
  opt-in only.

## Linux and Windows

- Linux deterministic validation: `PASS` except for the known pre-existing
  Core exact-Pyright issue recorded below.
- Windows containment/launcher evidence: `UNVERIFIED`; Windows was not run.

## Validation

- `PASS` — contracts: lock check, Ruff, format, Pyright, and pytest (`7
  passed`).
- `PASS` — runtime: lock check, Ruff, format, Pyright, and full pytest (`66
  passed`).
- `PASS` — Core: lock check, Ruff, format, and pytest (`179 passed, 4
  skipped` on Linux).
- `FAIL (pre-existing, not a P4-E regression)` — exact Core Pyright reports
  the same three diagnostics in untouched
  `apps/core/src/lilavel_core/process_containment.py` for Python 3.14
  `ctypes.windll.get_last_error` typing.
- `PASS` — Discord adapter: lock check, Ruff, format, Pyright, and full pytest
  (`92 passed`, 10 upstream deprecation warnings).
- `PASS` — sidecar frozen Bun 1.4.0 install, TypeScript check, and full tests
  (`92 passed`).
- `PASS` — final harness continuation validation: sidecar TypeScript check and
  focused tool-loop/transport/provider-choice tests (`15 passed`); Discord
  harness lint/format checks passed.
- `PASS` — docs integrity, architecture guard, and `git diff --check`.

## Production state and ADR

No ADR was added. P4-E did not introduce a new ownership or activation
decision. The explicit required-first-turn option is harness-only; ordinary
production traffic remains V2/no-tool and the default V3 provider choice
remains unset/auto.

## Remaining unknowns and exit gate

Gate 1 is closed `PASS`: supported auth discovery, actual provider contact,
normal completion, and clean provider/sidecar settlement were all observed.
The provider-selection portion of Gate 2 is now `PASS`: the pinned
subscription path delivered and finalized a native ToolCall when tool choice
was explicitly required. The second final action proves the provider/tool,
  raw correspondence, authorization, one confirmed send, same-generation
  ToolResult submission, continuation, and final completion gates `PASS`. The
  read-only post-run audit could not recover the run's process-local canonical
  history, so the live history boundary remains `UNVERIFIED` for that run.
  Overall Gate 2 therefore remains `BLOCKED` under the written exit gate;
  deterministic history-isolation evidence is sufficient for the
  implementation invariant but does not silently substitute for the required
  live-history evidence. The final live proof is prepared but was not executed
  in this hardening change. Live cancellation/supersession and Windows
  evidence remain explicitly unverified.

`PHASE 4` cannot be declared closed from this run. `PHASE 5 — Neuro-compatible
environment` should wait until a later credentialed run records a successful
provider V3 tool selection, exact raw correspondence, one authorized scoped
Discord send with `ok/confirmed`, same-generation ToolResult continuation to a
final completion, and the required boundary evidence.
