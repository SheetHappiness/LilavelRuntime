# PHASE 3 — Discord as Environment

Status: `CLOSED`
Baseline SHA: `e4a0b511e5dea89d41a271ff1be9262d84315780`
Implementation SHA at exit: this phase-closing commit

## Goal and implemented flow

Move the proven one-to-one Discord DM path behind the persistent runtime:

```text
DiscordAdapter → WorldEvent → LilavelRuntime → deterministic wake/route
  → ConversationCore → ModelRuntime → typed presentation actions → DiscordAdapter
```

## Decisions

- `USE` an opaque, process-local Discord channel subject in `WorldEvent`; raw
  Discord channel, message, and author IDs remain adapter-local.
- `USE` `DirectMessageWakePolicy`; only the typed `direct_message` event kind
  wakes and no LLM wake decision exists.
- `USE` `CoreConversationRouter` under `LilavelRuntime` ownership for session,
  generation, supersession, and route-task lifecycle.
- `USE` trusted runtime-generated `ToolCall`/`ToolResult` envelopes for
  presentation open/bind/watch/delta/terminal actions. They grant no
  model-selected tool authority.
- `REUSE` the existing Discord presenter, semantic streaming, chunking,
  interruption markers, diagnostics, and rate-limit behavior without redesign.
- `REFERENCE ADR-006`; this phase realizes its accepted boundary and makes no
  new consequential architecture decision.

## Evidence and invariants

- Existing Discord tests now exercise the real runtime-owned route while
  preserving the prior public `DiscordTextEdge` composition API.
- Duplicate Gateway delivery is claimed before `WorldEvent` submission.
- Core sees only user text and its own opaque scope; environment metadata is
  absent from canonical history and model requests.
- Each accepted DM produces one Core turn. Same-subject turns preserve
  continuity; concurrent same-subject turns retain Core cancellation and
  supersession settlement.
- Runtime action routing returns output only to the adapter registered for the
  event source. Action failure fails the owned route closed and cancels the
  live Core run.
- The architecture guard rejects Core importing runtime/Discord, runtime
  importing Discord, and the Discord environment performing Core lifecycle
  operations.

## Validation

| Check | Result | Evidence |
| --- | --- | --- |
| Runtime lock, Ruff, formatting, Pyright | `PASS` | locked package checks passed |
| Runtime pytest | `PASS` | 22 passed on Windows/Python 3.12.10 |
| Discord lock, Ruff, formatting, Pyright | `PASS` | locked package checks passed |
| Discord pytest | `PASS` | 80 passed on Windows/Python 3.12.10 |
| Core deterministic package | `PASS` | 171 passed; package checks passed |
| Model-sidecar deterministic package | `PASS` | 67 passed; TypeScript check passed |
| Documentation integrity | `PASS` | `scripts/check_docs.py` |
| Architecture guard | `PASS` | `scripts/check_architecture.py` |
| Whitespace guard | `PASS` | `git diff --check` |
| Live provider behavior | `UNVERIFIED` | no live command run |
| Live Discord behavior | `UNVERIFIED` | no token/service probe run |

## Unknowns and deferred scope

- Restart-safe environment-to-conversation continuity remains `UNKNOWN`; the
  current opaque subject map is process-local.
- Live Discord/provider behavior is `UNVERIFIED`; deterministic fixtures do
  not establish external service behavior.
- Guild ambience, autonomy, timers, attention, memory, voice, Twitch, Neuro,
  MCP, protocol-v3, and model-selected OMP tools remain deferred.

## Exit gate

PHASE 3 is closed when the single commit containing this record passes every
deterministic check above. No credential-dependent check is required.
