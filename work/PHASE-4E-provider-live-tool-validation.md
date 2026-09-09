# PHASE 4E — Provider/Live Tool Validation + Adversarial Hardening

Status: `BLOCKED`
Baseline branch: `main`
Baseline SHA: `3cfed30115820e7af32198008e52a858d0586567`
Implementation branch: `phase4e-provider-live-hardening`
Implementation SHA: `evidence-only; recorded by the phase commit`

## Goal

Close the provider-backed and real-Discord evidence left open by P4-D while
preserving the explicit V3 composition, application-owned authorization, one
trusted DM scope, conservative effect certainty, joined settlement, stale
fencing, and unchanged default V2/no-tool behavior.

## Scope and implementation outcome

This was an evidence-only phase on Linux. No production code, tool capability,
default activation, provider transport, or Discord behavior was changed. The
existing deterministic seams were audited against the P4-E adversarial matrix
and the authoritative validation matrix was rerun. No deterministic gap
requiring a code or test patch was found.

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

## Live lifecycle gates

- Live non-tool provider auth, contact, completion, and clean settlement:
  `PASS`; see the latest credentialed Gate 1 retry above.
- Live provider tool definition delivery and provider selection:
  `BLOCKED`; Gate 1 passed, but Gate 2 never admitted the required inbound DM
  scope, so no V3 generation was launched.
- Live raw-argument/call-ID correspondence: `UNVERIFIED`; deterministic
  correspondence remains `PASS`, but no live tool call reached the sidecar.
- Live same-generation ToolResult continuation and final completion:
  `BLOCKED`/`UNVERIFIED`; no live V3 generation was launched.
- Live provider-backed contained error-result continuation: `UNVERIFIED`; no
  safe live V3 turn was available and no extra external effect was created.
- Real-provider cancellation/supersession: `UNVERIFIED`; existing
  deterministic P4-B/P4-D evidence remains authoritative. No ambiguous live
  Discord delivery was induced.
- Canonical history boundary: `PASS` under the deterministic composition;
  live history behavior is `UNVERIFIED` because no live tool run occurred.
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
- `PASS` — docs integrity, architecture guard, and `git diff --check`.

## Production state and ADR

No ADR was added. P4-E did not introduce a new ownership or activation
decision. The existing explicit tool-enabled composition remains available for
a later credentialed proof, while ordinary production traffic remains
V2/no-tool.

## Remaining unknowns and exit gate

Gate 1 is closed `PASS`: supported auth discovery, actual provider contact,
normal completion, and clean provider/sidecar settlement were all observed.
Gate 2 remains `BLOCKED` solely because no user-authored one-to-one DM arrived
to establish the required trusted scope. Consequently live V3 tool selection,
raw correspondence, one authorized Discord send, same-generation ToolResult
continuation, final completion, and live history behavior remain open or
unverified. Live cancellation/supersession and Windows evidence also remain
explicitly unverified.

`PHASE 4` cannot be declared closed from this run. `PHASE 5 — Neuro-compatible
environment` should wait until a later credentialed run records a successful
provider V3 tool selection, exact raw correspondence, one authorized scoped
Discord send with `ok/confirmed`, same-generation ToolResult continuation to a
final completion, and the required boundary evidence.
