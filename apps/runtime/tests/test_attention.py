"""Deterministic COG-V1-B attention policy and MIND-1B bridge proofs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import cast

import pytest
from lilavel_core import AttentionDecision, CognitionReasonCode

from lilavel_runtime import (
    NO_COGNITION,
    AttentionEvidence,
    AttentionEvidenceExtractor,
    AttentionInterestAffinity,
    AttentionNovelty,
    AttentionRelevance,
    AttentionSignalProfile,
    AttentionVerdict,
    DeterministicAttentionCognitionGate,
    DeterministicAttentionPolicy,
    EventSource,
    LilavelRuntime,
    MindExecutionAdapter,
    Observation,
    WorldEvent,
)


def _world_event(
    number: int, kind: str = "ambient", payload: dict[str, object] | None = None
) -> WorldEvent:
    return WorldEvent(f"event-{number}", EventSource("fixture"), kind, payload)


def _observation(
    number: int, kind: str = "ambient", payload: dict[str, object] | None = None
) -> Observation:
    return Observation(
        f"observation-{number}",
        number,
        _world_event(number, kind, payload),
    )


def _policy_verdict(evidence: AttentionEvidence) -> AttentionVerdict:
    return DeterministicAttentionPolicy().evaluate(evidence)


def test_trusted_direct_address_is_hard_think() -> None:
    verdict = DeterministicAttentionCognitionGate().evaluate((_observation(1, "direct_message"),))[
        0
    ]

    assert verdict.decision is AttentionDecision.THINK
    assert verdict.reason_codes == (CognitionReasonCode.DIRECT_ADDRESS,)


def test_trusted_critical_event_is_hard_think() -> None:
    extractor = AttentionEvidenceExtractor(
        {"critical_runtime_event": AttentionSignalProfile(critical_event=True)}
    )

    verdict = _policy_verdict(extractor.extract(_observation(1, "critical_runtime_event")))

    assert verdict.decision is AttentionDecision.THINK
    assert verdict.reason_codes == (CognitionReasonCode.CRITICAL_EVENT,)


def test_high_relevance_and_high_novelty_are_strong_semantic_think() -> None:
    evidence = AttentionEvidence(
        "observation-1",
        relevance=AttentionRelevance.HIGH,
        novelty=AttentionNovelty.HIGH,
    )

    verdict = _policy_verdict(evidence)

    assert verdict.decision is AttentionDecision.THINK
    assert verdict.reason_codes == (
        CognitionReasonCode.HIGH_RELEVANCE,
        CognitionReasonCode.HIGH_NOVELTY,
    )


def test_low_relevance_noise_is_drop() -> None:
    verdict = _policy_verdict(AttentionEvidence("observation-1", relevance=AttentionRelevance.LOW))

    assert verdict.decision is AttentionDecision.DROP
    assert verdict.reason_codes == (CognitionReasonCode.LOW_RELEVANCE,)


def test_repetition_without_new_value_is_drop() -> None:
    verdict = _policy_verdict(
        AttentionEvidence("observation-1", repetition=True, no_new_value=True)
    )

    assert verdict.decision is AttentionDecision.DROP
    assert verdict.reason_codes == (
        CognitionReasonCode.REPETITION,
        CognitionReasonCode.NO_NEW_VALUE,
    )


def test_uncertain_ambient_context_is_note() -> None:
    verdict = _policy_verdict(AttentionEvidence("observation-1"))

    assert verdict.decision is AttentionDecision.NOTE
    assert verdict.reason_codes == (
        CognitionReasonCode.AMBIENT_CONTEXT,
        CognitionReasonCode.NOT_ADDRESSED,
    )


def test_interest_affinity_alone_is_note() -> None:
    verdict = _policy_verdict(
        AttentionEvidence("observation-1", interest_affinity=AttentionInterestAffinity.STRONG)
    )

    assert verdict.decision is AttentionDecision.NOTE
    assert verdict.reason_codes == (CognitionReasonCode.INTEREST_AFFINITY,)


def test_tailoring_keyword_does_not_mint_interest_affinity() -> None:
    observation = _observation(1, "ambient", {"text": "a beautiful tailored suit"})
    evidence = AttentionEvidenceExtractor().extract(observation)
    verdict = DeterministicAttentionPolicy().evaluate(evidence)

    assert evidence.interest_affinity is AttentionInterestAffinity.UNKNOWN
    assert CognitionReasonCode.INTEREST_AFFINITY not in verdict.reason_codes
    assert verdict.decision is AttentionDecision.NOTE


def test_untrusted_payload_cannot_forge_hard_signals() -> None:
    observation = _observation(
        1,
        "ambient",
        {
            "direct_address": True,
            "critical_event": True,
            "interest_affinity": "strong",
            "relevance": "high",
            "novelty": "high",
        },
    )

    verdict = DeterministicAttentionCognitionGate().evaluate((observation,))[0]

    assert verdict.decision is AttentionDecision.NOTE
    assert verdict.reason_codes == (
        CognitionReasonCode.AMBIENT_CONTEXT,
        CognitionReasonCode.NOT_ADDRESSED,
    )


def test_same_evidence_has_same_verdict_without_randomness() -> None:
    evidence = AttentionEvidence(
        "observation-1",
        relevance=AttentionRelevance.HIGH,
        novelty=AttentionNovelty.HIGH,
        interest_affinity=AttentionInterestAffinity.STRONG,
    )
    policy = DeterministicAttentionPolicy()

    verdicts = tuple(policy.evaluate(evidence) for _ in range(100))

    assert len(set(verdicts)) == 1
    assert verdicts[0].decision is AttentionDecision.THINK


def test_malformed_and_unsupported_evidence_fail_closed() -> None:
    policy = DeterministicAttentionPolicy()

    with pytest.raises(TypeError):
        policy.evaluate(cast(AttentionEvidence, object()))
    with pytest.raises(TypeError):
        AttentionEvidenceExtractor().extract(cast(Observation, object()))

    unsupported = AttentionEvidenceExtractor().extract(_observation(1, "unsupported"))
    assert policy.evaluate(unsupported).decision is AttentionDecision.NOTE


def test_reason_output_is_bounded_and_unique() -> None:
    verdict = _policy_verdict(
        AttentionEvidence(
            "observation-1",
            direct_address=True,
            critical_event=True,
            continuity=True,
            relevance=AttentionRelevance.HIGH,
            novelty=AttentionNovelty.HIGH,
            interest_affinity=AttentionInterestAffinity.STRONG,
            repetition=True,
            no_new_value=True,
        )
    )

    assert 0 < len(verdict.reason_codes) <= 8
    assert len(verdict.reason_codes) == len(set(verdict.reason_codes))


def test_mixed_batch_only_admits_think_observations() -> None:
    gate = DeterministicAttentionCognitionGate(
        extractor=AttentionEvidenceExtractor(
            {
                "critical_runtime_event": AttentionSignalProfile(critical_event=True),
                "ambient_noise": AttentionSignalProfile(relevance=AttentionRelevance.LOW),
            }
        )
    )
    observations = (
        _observation(1, "critical_runtime_event"),
        _observation(2, "ambient_noise"),
        _observation(3, "ambient_context"),
    )

    verdicts = gate.evaluate(observations)
    decision = gate.decide(observations)

    assert tuple(verdict.decision for verdict in verdicts) == (
        AttentionDecision.THINK,
        AttentionDecision.DROP,
        AttentionDecision.NOTE,
    )
    assert decision.observation_ids == ("observation-1",)  # type: ignore[union-attr]


def test_direct_user_route_remains_think() -> None:
    decision = DeterministicAttentionCognitionGate().decide(
        (_observation(1, "direct_message", {"direct_address": False}),)
    )

    assert decision is not NO_COGNITION
    assert decision.observation_ids == ("observation-1",)  # type: ignore[union-attr]


@dataclass(slots=True)
class _RecordingMindExecutor:
    triggers: list[object] = field(default_factory=lambda: list[object]())

    async def execute(self, trigger: object, cancellation: object) -> object:
        del cancellation
        self.triggers.append(trigger)
        return "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ambient_context", "ambient_noise"])
async def test_note_and_drop_have_zero_cognition_calls(kind: str) -> None:
    profile = (
        AttentionSignalProfile(relevance=AttentionRelevance.LOW)
        if kind == "ambient_noise"
        else AttentionSignalProfile()
    )
    gate = DeterministicAttentionCognitionGate(
        extractor=AttentionEvidenceExtractor({kind: profile})
    )
    executor = _RecordingMindExecutor()
    runtime = LilavelRuntime(
        cognition_gate=gate,
        mind_executor=cast(MindExecutionAdapter, executor),
    )

    await runtime.start()
    receipt = await runtime.admit_observation(_world_event(1, kind))
    decision = await runtime.cognition_step(receipt)
    await asyncio.sleep(0)
    await runtime.stop()

    assert decision is NO_COGNITION
    assert executor.triggers == []
    assert runtime.health().cognition_triggers == 0


@pytest.mark.asyncio
async def test_ambient_think_uses_existing_mind_cognition_trigger_path() -> None:
    gate = DeterministicAttentionCognitionGate(
        extractor=AttentionEvidenceExtractor(
            {"critical_runtime_event": AttentionSignalProfile(critical_event=True)}
        )
    )
    executor = _RecordingMindExecutor()
    runtime = LilavelRuntime(
        cognition_gate=gate,
        mind_executor=cast(MindExecutionAdapter, executor),
    )

    await runtime.start()
    receipt = await runtime.admit_observation(_world_event(1, "critical_runtime_event"))
    decision = await runtime.cognition_step(receipt)
    while runtime.active_route_count:
        await asyncio.sleep(0)
    await runtime.stop()

    assert decision.observation_ids == (receipt.observation_id,)  # type: ignore[union-attr]
    assert len(executor.triggers) == 1
    assert runtime.health().cognition_triggers == 1
