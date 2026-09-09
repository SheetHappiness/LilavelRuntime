# P4-B Deterministic Tool Loop and Joined Settlement

Status: `CLOSED`
Baseline SHA: `90ee8f5a6726e65972da80ad1ebb4c9b5ea5a3bc`
Implementation SHAs:

- Base: `196c5f9477807a7dced528090193aa3127ffc0be`
- Executor-settlement correction: `c20ff784c9bf6b1b7c2da6044e18ece93ba43149`

## FACT

- Production `ModelRuntime` and the ordinary sidecar entry point remain V2.
- Explicit opt-in V3 spans multiple provider turns under one generation/epoch,
  with exact ordered result batches and bounds of four calls per batch, eight
  calls total, and four rounds.
- `ConversationCore` commits only final aggregate assistant text. `ToolCall` and
  `ToolResult` never enter canonical role/text history.
- `DeterministicToolSessionFactory` is a fake executor seam only; Discord and
  real external effects remain inactive.

## DECISION

Successor admission requires joined provider and application-session settlement.
The result batch is consumed before continuation dispatch. Cancellation fences
the logical run, generation, epoch, round, and immutable call IDs; late results
are discarded. Uncontainable executor or provider-cleanup uncertainty poisons
reuse rather than claiming rollback or success. Per-call timeout cancellation
does not cancel the session, and runtime failure fences an admitted executor
before it observes settlement or can begin application work.

## RESULT

- No-tool, one/multi-round, ordered multi-call, partial text, denied/invalid/
  unavailable, executor failure/timeout, cancellation, supersession, stale
  result, shutdown, provider failure, malformed lifecycle, and uncontainable
  cleanup scenarios: PASS deterministically.
- Contracts: 7 tests PASS.
- Core: Ruff, format, Pyright, Windows containment, and 183 tests PASS.
- Runtime: Ruff, format, Pyright, and 49 tests PASS; focused P4-B 27 tests PASS.
- Sidecar: frozen install and TypeScript check PASS; 92 tests PASS.
- Discord adapter: Ruff, format, Pyright, and 80 tests PASS.
- Architecture guard, docs integrity, and `git diff --check`: PASS.

## UNVERIFIED

Luna tool selection, configured-endpoint tool-result continuation, live
multi-call behavior, `code_mode_only`, real-provider cancellation across a tool
round, and Discord external effects.

## EXIT GATE

P4-C may proceed from the correction SHA above. It must preserve explicit
production activation, application-owned policy/effects, joined settlement, and
stale-result fencing. This record does not mark Phase 4 complete.
