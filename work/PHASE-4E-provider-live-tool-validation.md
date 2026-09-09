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
- `LILAVEL_DISCORD_BOT_TOKEN` was absent from this Work environment. The token
  value was never printed, copied, or persisted.

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

## Real Discord evidence

`BLOCKED` — `LILAVEL_DISCORD_BOT_TOKEN` was missing from the Work environment.
Consequently, the normal admitted one-to-one DM scope could not be established,
the explicit P4-D tool-enabled composition was not launched, and no live
Discord send was attempted. There is therefore no live attempt count,
destination confirmation, `ok/confirmed` effect, or live ToolResult
continuation to claim.

The deterministic P4-D proof remains `PASS`: its bound fake DM executor makes
one send attempt at most, suppresses mentions, maps confirmed success to
`ok/confirmed`, and never retries unknown delivery.

## Live lifecycle gates

- Live provider tool definition delivery and provider selection:
  `BLOCKED`/`UNVERIFIED`; no successful V3 provider turn.
- Live raw-argument/call-ID correspondence: `UNVERIFIED`; deterministic
  correspondence remains `PASS`.
- Live same-generation ToolResult continuation and final completion:
  `BLOCKED`/`UNVERIFIED`.
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

The missing Discord credential and failed provider smoke leave the required
provider-backed selection, same-generation continuation, and real Discord
effect gates open. Live cancellation/supersession and Windows evidence also
remain explicitly unverified.

`PHASE 4` cannot be declared closed from this run. `PHASE 5 — Neuro-compatible
environment` should wait until a later credentialed run records a successful
provider V3 tool selection, exact raw correspondence, one authorized scoped
Discord send with `ok/confirmed`, same-generation ToolResult continuation to a
final completion, and the required boundary evidence.
