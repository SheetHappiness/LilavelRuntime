# PROACTIVE-V0-R1 — Live Diagnostics + Cognition/Effect Isolation

## Status

**PASS** for deterministic composition, isolation, and content-free
diagnostics evidence. Live provider/Discord verification remains
**UNVERIFIED**.

## Baseline and implementation

- Expected baseline: `337148b820470ca3a46b9f512b014876febecfde`
- Preflight HEAD: `337148b820470ca3a46b9f512b014876febecfde`
- Implementation SHA: recorded at phase close
- Push: not performed

## Implemented truth

The default proactive cognition runtime is `ModelRuntimeV3()` with no
application tool session factory. The existing `DiscordToolSessionFactory` is
constructed separately for `ProposalApplicationCoordinator`, preserving
trusted target resolution, availability, one-shot authorization, scope, and
the existing `discord.send_message` schema.

`LocalCognitionEngine` retains bounded typed evidence for proactive generation
completion/failure and strict parser outcomes. The Discord adapter translates
that evidence into the existing lifecycle plus:

```text
appraisal_generation_completed / appraisal_generation_failed
appraisal_parse_no_change / appraisal_parse_create_intention / appraisal_parse_invalid
idle_generation_completed / idle_generation_failed
idle_parse_speak / idle_parse_stay_silent / idle_parse_invalid
speech_allowed
```

The existing `target_disabled_multiple_subjects`, `idle_cancelled_by_user`,
and `silence` names remain canonical. With
`LILAVEL_DISCORD_PROACTIVE_DIAGNOSTICS=1`, `run_edge.py` emits startup and
proactive evidence as bounded JSONL to stderr. The sink is default-off,
content-free, and failure-isolated; startup states the smoke flag, configured
idle interval, diagnostics flag, and `cognition_tools_exposed=false`.

## Evidence and validation

- Real `ModelRuntimeV3` + committed V3 fixture proof rejects any provider
  generation request containing tools, while a validated SPEAK proposal still
  exposes the application factory and sends exactly once: **PASS** — focused
  proactive suite.
- Existing one-shot, single-target, USER-preemption, effect-time revalidation,
  unknown-delivery, and canonical-history isolation tests: **PASS** — focused
  proactive and runtime cognition/presence suites.
- Strict Ruff, format, and Pyright for modified packages: **PASS**.
- Live provider + Discord send: **UNVERIFIED**; no credentials or live service
  were used.

## Exit gate

The deterministic R1 gate is complete. The exact live command is documented in
`docs/VALIDATION.md`; a live **PASS** requires observing
`{"kind":"send_confirmed"}` in stderr JSONL after a real provider and Discord
interaction. No live result is claimed here.
