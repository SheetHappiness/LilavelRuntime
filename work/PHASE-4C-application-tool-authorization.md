# PHASE 4C — Application-Owned Tool Authorization + Executor Layer

Status: `CLOSED`
Baseline SHA: `617ae6fdb309ac8967baf3a98d6eeaa3af667c22`
Implementation SHA at exit: `fb7d59998b9995f614e491576bdeda51796ea182`

## Goal

Establish the smallest trusted application registry, immutable per-run exposure
snapshot, strict argument validation, trusted-scope authorization, bound
sequential executor, and typed result boundary required before any real
external tool can be activated.

## Scope

- `apps/runtime` now owns `ApplicationToolRegistry`, `ToolBinding`, explicit
  exposure snapshots, bounded schema validation, authorization/availability
  hooks, executor binding, safe result normalization, and bounded metadata.
- `DeterministicToolSessionFactory` now consumes the registry while preserving
  the P4-B joined-settlement, cancellation, timeout, sequential-order, and
  stale-result seam.
- Deterministic pure and effect-like fixtures prove the full application path.
- Runtime architecture, validation, and component documentation record the
  current boundary.

## Non-goals

Real Discord effects, production V3 activation, provider-selected tools, live
Luna continuation, MCP, plugin discovery, parallel execution, Core history
changes, and all deferred autonomous capabilities remain out of scope.

## Decisions

- `USE` one canonical `ToolSpec`/`ToolCall`/`ToolResult` family from
  `lilavel-contracts`; no second semantic tool contract was added.
- `USE` an application-owned registry keyed only by canonical application tool
  name. Provider aliases remain transport-only.
- `USE` explicit `exposed_tool_names` composition. The default registry factory
  exposes no tools unless the trusted application names them.
- `USE` an immutable snapshot per generation. Later registry registration cannot
  expand an admitted run.
- `USE` a deliberately bounded non-coercive schema subset: object properties,
  required fields, primitive scalar types, scalar enums, `additionalProperties`,
  string length, and numeric range limits. Unsupported keywords/types reject at
  registration.
- `USE` `ToolBatchCorrelation.context` as the trusted authorization input.
  Model arguments remain untrusted and cannot provide scope, destination,
  identity, generation, or permission authority.
- `USE` typed non-effect results for invalid, denied, and unavailable calls;
  contained executor timeouts report `timed_out` with `effect=unknown` because
  effect settlement cannot be established from the timeout alone.
- `PRESERVE` P4-B joined provider/executor settlement and fail-closed
  uncontainable cleanup. No retry or rollback is claimed.

## Architecture consequences

`LilavelRuntime` owns application tool registration, exposure, authorization,
availability, executor binding, execution, and evidence-safe result creation.
Core continues to own conversation semantics and physical V3 lifecycle; the
sidecar continues to own provider transport and normalization. Tool calls and
results remain outside canonical conversation history and trusted guidance.
`conversation.presentation.*` is reserved from model exposure, and the normal
Discord composition remains V2/no-tool.

No new ADR was added: this phase makes the application-owned authorization and
execution consequence of ADR-007 and the P4-B session seam concrete without
changing their ownership decision.

## Inherited invariants

- Core owns canonical role/text history and successful assistant commit.
- ToolCall/ToolResult are executor evidence, not canonical messages or trusted
  system guidance.
- Model-originated arguments are untrusted by construction.
- Cancellation is not rollback; uncertain effects are explicit and never
  retried automatically.
- Successor admission remains gated on joined provider and executor settlement.
- Uncontainable executor cleanup poisons runtime reuse.
- Core does not import `lilavel_runtime`.
- Default/no-tool behavior and production V2 activation remain unchanged.

## Evidence

- Canonical baseline: `main == origin/main` at
  `617ae6fdb309ac8967baf3a98d6eeaa3af667c22`; Phase 3, P4-A, and P4-B are
  ancestor-contained.
- P4-C focused deterministic proof: `17 passed` in
  `apps/runtime/tests/test_tool_authorization.py`.
- Inherited P4-B lifecycle proof through the new registry path: `27 passed` in
  `apps/runtime/tests/test_tool_loop.py`.
- Full runtime proof: `66 passed`.
- Pure `test.echo` and effect-like `test.effect` fixtures execute through the
  real registry/snapshot/validation/authorization/bound-executor path.
- Internal presentation exposure rejection, model-supplied scope isolation,
  unknown/unexposed calls, dynamic liveness recheck, schema rejection, raw
  evidence exclusion, and effect certainty are directly asserted.

## Validation

- `PASS` — runtime `ruff check .`.
- `PASS` — runtime `ruff format --check .`.
- `PASS` — runtime strict `pyright`.
- `PASS` — runtime locked `pytest`: 66 passed on Linux.
- `PASS` — contracts lock check, Ruff, format, Pyright, and pytest: 7 passed.
- `PASS` — Core lock check, Ruff, format, and pytest: 179 passed, 4 Linux
  platform skips.
- `FAIL` — exact Core `pyright` command reports three pre-existing typing
  diagnostics in untouched `apps/core/src/lilavel_core/process_containment.py`
  for `ctypes.windll.get_last_error` under the host's Python 3.14 typing
  surface. The same failure remains with `--pythonversion 3.12`; no P4-C Core
  file changed.
- `PASS` — sidecar frozen Bun 1.4.0 install, TypeScript check, and 92 tests.
- `PASS` — Discord adapter lock check, Ruff, format, Pyright, and 80 tests.
- `PASS` — docs integrity, architecture guard, and `git diff --check`.

## Unknowns

- `UNVERIFIED` — Luna selecting a tool, real provider continuation, live
  multi-call behavior, provider cancellation across tool rounds, and real
  Discord external effects.
- `UNVERIFIED` — Windows-specific containment and launcher evidence; this
  phase ran on Linux and the Core Windows-marked tests were skipped.
- `UNVERIFIED` — production activation of any model-selected external tool;
  it remains intentionally inactive.

## Exit gate

P4-D may proceed: `PASS` for trusted registration, bounded immutable exposure,
strict validation, trusted-scope authorization, bound sequential execution,
typed non-effect rejection, P4-B joined settlement, fail-closed cleanup,
stale-result fencing, history/evidence separation, unchanged no-tool/Discord
behavior, explicit production activation, and all affected deterministic suites
and architecture/docs guards. Provider/live and Windows claims remain
`UNVERIFIED` and belong to later phase evidence.
