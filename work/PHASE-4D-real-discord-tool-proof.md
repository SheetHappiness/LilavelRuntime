# PHASE 4D — Real Discord Tool Proof

Status: `CLOSED`
Baseline branch: `main`
Baseline SHA: `711aba331ba1fe56e03189947add3944261125c3`
Implementation branch: `phase4d-discord-tool-proof`
Implementation SHA: `8ba0ea2e16479aced3f52653520381a0792fde43`

## Goal

Prove one application-owned, model-selectable Discord capability through the
existing V3 tool boundary while keeping the destination authority in trusted
application state:

```text
discord.send_message({"text": string})
```

The proof keeps ConversationCore responsible for canonical conversational
semantics, keeps the model sidecar responsible for provider transport and
normalization, and keeps the Discord adapter responsible for Discord effects.

## Scope

- Added one canonical Discord `ToolSpec` with only bounded `text` and no
  recipient, channel, user, or session fields.
- Added an adapter-owned `DiscordToolSessionFactory` backed by the real P4-C
  registry, immutable exposure snapshot, strict validation, trusted scope
  authorization, and existing P4-B session containment.
- Added explicit route-specific composition to `CoreConversationRouter` and
  `DiscordTextEdge(tool_enabled=True)`. The default edge remains V2/no-tool.
- Bound each explicit tool session to the admitted DM's adapter-local channel
  reference and the newly created Core scope before model execution.
- Added a one-shot Discord executor using `AllowedMentions.none()` and safe
  typed status/effect mapping. It makes no automatic retry and never returns
  Discord IDs, HTTP details, or exception text.
- Added deterministic fake Discord transport and V3 sidecar fixture coverage
  for the real registry/authorization/executor/continuation composition.
- Added a narrow explicit blocking bridge and ingress-task shutdown fix so
  Linux Python 3.14 shutdown does not depend on the event loop default
  executor. This preserves the bounded existing lifecycle semantics and fixes
  the pre-existing local test shutdown hang.

## Tool and authority decisions

- `DISCORD_SEND_MESSAGE_SPEC.name` is `discord.send_message`.
- The input schema is an object with required non-empty string `text`,
  `minLength=1`, `maxLength=2000`, and `additionalProperties=false`.
- `discord_send_message` remains a provider/sidecar transport alias only; it
  is not an authorization or registry key.
- The model selects the action and supplies message content. Trusted
  application composition selects the current one-to-one DM destination.
- The adapter retains the subject-to-local-channel binding outside Core and
  model history. A model-supplied destination field fails strict validation
  and cannot alter the bound channel.
- The exposure snapshot contains exactly the one Discord tool. Unexposed,
  wrong-scope, unavailable, and invalid calls dispatch zero Discord sends.

## Executor and effect semantics

The executor schedules exactly one `channel.send()` coroutine after its
pre-send cancellation fence. It always passes `discord.AllowedMentions.none()`.
It waits for an already submitted coroutine to settle so the P4-B session
barrier can observe the actual outcome; cancellation does not claim rollback.

| Condition | Tool result |
| --- | --- |
| Strict validation or authorization failure | `invalid`/`denied`/`unavailable`, `effect=none` |
| Confirmed Discord client success | `ok`, `effect=confirmed` |
| Known contained pre-send failure | `failed`, `effect=none` |
| HTTP rejection with known 4xx status | `failed`, `effect=none` |
| Timeout, cancellation during an in-flight await, 5xx, connection, or opaque post-submit failure | `failed`, `effect=unknown` |

An `effect=unknown` result is never retried. A cancellation or supersession
waits for the joined P4-B provider/executor settlement; an executor that does
not settle within containment poisons the runtime. Late results are fenced by
generation, epoch, round, and ordered call IDs.

## History and evidence

Tool calls/results remain transport/runtime evidence only. Successful Discord
tool execution does not create a canonical assistant message. Only the final
assistant completion is committed to Core history; cancelled, superseded, or
failed candidates remain noncanonical. Evidence records only bounded
canonical-tool/lifecycle/status/effect metadata and excludes raw text,
Discord IDs, provider payloads, HTTP details, credentials, and exception text.

## Deterministic D1 evidence

- `apps/discord-adapter/tests/test_tool.py`: `12 passed`.
- D1 covers content-only schema and transport alias, allowed scoped send with
  exactly one attempt, unexposed and wrong-scope zero-attempt fences,
  injected destination rejection, oversized text rejection, mention
  suppression, known pre-send failure, ambiguous post-submit failure,
  cancellation before send, in-flight cancellation with unknown effect, no
  retry, Core history separation, and final V3 continuation.
- `apps/runtime/tests/test_tool_loop.py`: `27 passed` inherited P4-B
  lifecycle proof, including joined settlement, ignored-cancellation
  containment failure, supersession, stale late-result fencing, and one
  terminal containment outcome.
- The full Discord adapter suite passed `92 tests`; the full runtime suite
  passed `66 tests`. Ordinary no-tool Discord edge tests remained green.

## Validation

- `PASS` — contracts lock check, Ruff, format, strict Pyright, and pytest:
  `7 passed`.
- `PASS` — Core Ruff, format, and pytest: `179 passed, 4 skipped` on Linux.
- `FAIL (pre-existing)` — Core strict Pyright reports the same three
  diagnostics in untouched `apps/core/src/lilavel_core/process_containment.py`
  for Python 3.14 `ctypes.windll.get_last_error` typing. No Core file changed.
- `PASS` — runtime lock check and full locked pytest: `66 passed`.
- `PASS` — Discord adapter lock check and full locked pytest: `92 passed`
  with 10 upstream discord.py deprecation warnings.
- `PASS` — sidecar Bun 1.4.0 frozen install, TypeScript check, and test suite:
  `92 pass`.
- `PASS` — repository docs integrity, architecture guard, and `git diff
  --check`.
- `UNVERIFIED` — Windows-only containment/launcher behavior; this task ran on
  Linux and did not claim a Windows proof.

## Provider-backed/live D2 evidence

`BLOCKED` — no `LILAVEL_DISCORD_BOT_TOKEN` was present in the task
environment, so no provider-backed model selection or live test-DM message
was attempted. No live evidence is fabricated. D2 remains for a later phase
with an explicitly admitted test DM, supported provider authentication, and a
reviewed one-send scope.

## ADR and production state

No new ADR was added. Existing tool-ownership and Discord-adapter decisions
already cover this consequence. The P4-D implementation is an explicit
composition seam only; ordinary Discord traffic and the default production
path remain V2/no-tool. No guild listening, arbitrary-recipient messaging,
ambient autonomy, or other external tool capability was added.

## Exit gate

P4-D deterministic exit gate is `PASS`: trusted registration and exposure,
content-only schema, trusted destination binding, zero-attempt rejection,
one-attempt allowed execution, mention suppression and bounds, typed
conservative effect certainty, no unknown-effect retry, joined settlement,
supersession/stale-result fencing, history/evidence separation, unchanged
ordinary DM behavior, explicit/off-by-default tool mode, and all affected
deterministic suites/guards pass.

P4-E may proceed for provider-backed/live and platform-specific evidence. It
must not treat this deterministic closure as proof of Luna tool selection,
live Discord delivery, restart-safe channel continuity, or Windows containment.
