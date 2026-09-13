# ADR-020: Deterministic attention evidence policy

## Status

Accepted for COG-V1-B.

## Context

Lilavel needs an ambient observation gate without conflating cognition-worthiness
with speaking permission or response style. External event payloads are
untrusted and cannot be allowed to claim direct address, criticality, novelty,
relevance, or character interest.

## Decision

Use two runtime-owned stages behind the existing MIND-1B `CognitionGate`:

```text
admitted Observation
  → AttentionEvidenceExtractor
  → AttentionEvidence
  → DeterministicAttentionPolicy
  → AttentionVerdict (DROP | NOTE | THINK)
```

The extractor mints evidence only from exact, trusted event-route profiles and
does not inspect payload text or payload fields for authority. The policy uses
ordered boolean rules with bounded reason codes. It has no weighted salience
score, threshold, RNG, model call, cooldown, quiet-hours, floor-ownership, or
interruption rule.

Only `THINK` is bridged to the existing `CognitionTrigger` contract. Direct
messages retain the existing USER/Core route. Ambient `THINK` uses the existing
NON_USER/MIND route when that composition is configured. `NOTE` and `DROP`
return `NO_COGNITION` and do not invoke cognition. Interest affinity remains an
optional bounded signal; alone it produces `NOTE` and is never inferred from
keywords.

## Consequences

- Attention, intervention, and disposition remain distinct decisions.
- The runtime can explain bounded attention outcomes without storing raw
  payload content or adding a second semantic lane.
- Unknown event semantics conservatively remain peripheral (`NOTE`) until a
  runtime-owned route can establish stronger evidence.
- A later intervention policy may suppress speaking after `THINK`; that does
  not change the attention result.

## Rejected alternatives

- A single salience score was rejected because thresholds and weights would
  hide policy ordering and make review harder.
- Payload keyword/field scraping was rejected because external data is not
  authority and would create brittle personality behavior.
- Pulling all batch notes into a cognition trigger was rejected because each
  observation must earn cognition independently.
