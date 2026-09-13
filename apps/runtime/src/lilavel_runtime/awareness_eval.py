"""Deterministic AWARE-V1-A contract and boundary scenarios."""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, dataclass, fields
from datetime import UTC, datetime, timedelta

from lilavel_core import AttentionDecision, CognitionReasonCode

from .attention import (
    AttentionEvidence,
    AttentionEvidenceExtractor,
    AttentionNovelty,
    AttentionRelevance,
    AttentionSignalProfile,
    AttentionVerdict,
    DeterministicAttentionCognitionGate,
    DeterministicAttentionPolicy,
)
from .awareness import (
    MAX_AWARENESS_REASON_CODES,
    MAX_AWARENESS_SOURCE_REFS,
    AwarenessAdmission,
    AwarenessNote,
    AwarenessScope,
    PeripheralAwarenessBuffer,
)
from .context import ContextFrame
from .context_integration import ProductionContextComposer
from .contracts import NO_COGNITION, CognitionTrigger, EventSource, Observation, WorldEvent

__all__ = [
    "AWARE_V1_A_SCENARIOS",
    "AwarenessEvalReport",
    "AwarenessEvalResult",
    "AwarenessEvalScenario",
    "evaluate_awareness_corpus",
]


@dataclass(frozen=True, slots=True)
class AwarenessEvalScenario:
    id: str
    description: str


@dataclass(frozen=True, slots=True)
class AwarenessEvalResult:
    scenario: AwarenessEvalScenario
    passed: bool


@dataclass(frozen=True, slots=True)
class AwarenessEvalReport:
    scenario_results: tuple[AwarenessEvalResult, ...]

    @property
    def scenario_count(self) -> int:
        return len(self.scenario_results)

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.scenario_results)


@dataclass(slots=True)
class _FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


_NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)


def _observation(
    number: int,
    *,
    environment: str = "fixture",
    subject: str | None = "surface-a",
    kind: str = "ambient",
    payload: dict[str, object] | None = None,
) -> Observation:
    return Observation(
        f"observation-{number}",
        number,
        WorldEvent(
            f"event-{number}",
            EventSource(environment, subject),
            kind,
            payload,
        ),
    )


def _note_verdict(observation: Observation) -> AttentionVerdict:
    return DeterministicAttentionPolicy().evaluate(AttentionEvidence(observation.observation_id))


def _drop_verdict(observation: Observation) -> AttentionVerdict:
    return DeterministicAttentionPolicy().evaluate(
        AttentionEvidence(observation.observation_id, relevance=AttentionRelevance.LOW)
    )


def _think_verdict(observation: Observation) -> AttentionVerdict:
    return DeterministicAttentionPolicy().evaluate(
        AttentionEvidence(
            observation.observation_id,
            relevance=AttentionRelevance.HIGH,
            novelty=AttentionNovelty.HIGH,
        )
    )


def _scope(observation: Observation, scope_id: str = "runtime") -> AwarenessScope:
    return AwarenessScope.from_observation(scope_id, observation)


def _admit(
    buffer: PeripheralAwarenessBuffer,
    observation: Observation,
    *,
    scope_id: str = "runtime",
) -> AwarenessAdmission:
    return buffer.admit(observation, _note_verdict(observation), scope_id=scope_id)


def _checks() -> dict[str, bool]:
    buffer = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW), per_scope_capacity=4)
    note_observation = _observation(1)
    note_scope = _scope(note_observation)
    note_result = _admit(buffer, note_observation)
    note = note_result.note
    if note is None:
        raise AssertionError("fixture NOTE must be admitted")

    drop_observation = _observation(2, subject="surface-drop")
    drop_result = buffer.admit(
        drop_observation,
        _drop_verdict(drop_observation),
        scope_id="runtime",
    )
    think_observation = _observation(3, kind="critical")
    think_gate = DeterministicAttentionCognitionGate(
        extractor=AttentionEvidenceExtractor({"critical": _critical_profile()})
    )
    think_decision = think_gate.decide((think_observation,))
    note_only_gate = DeterministicAttentionCognitionGate(
        awareness_buffer=PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    )
    note_only_decision = note_only_gate.decide((note_observation,))

    overflowing = PeripheralAwarenessBuffer(
        clock=_FixedClock(_NOW), per_scope_capacity=2, total_capacity=3
    )
    overflow_observations = tuple(_observation(number) for number in range(10, 14))
    for observation in overflow_observations:
        _admit(overflowing, observation)
    overflow_snapshot = overflowing.snapshot(note_scope)

    other_surface = _observation(20, subject="surface-b")
    _admit(buffer, other_surface)
    other_scope = _scope(other_surface)

    expiry_clock = _FixedClock(_NOW)
    expiry_buffer = PeripheralAwarenessBuffer(clock=expiry_clock, ttl=timedelta(seconds=30))
    expiry_observation = _observation(30)
    _admit(expiry_buffer, expiry_observation)
    expiry_clock.value = _NOW + timedelta(seconds=30)

    source = inspect.getsource(PeripheralAwarenessBuffer).casefold()
    gate_source = inspect.getsource(DeterministicAttentionCognitionGate).casefold()
    model_request_source = inspect.getsource(ProductionContextComposer).casefold()
    frame_fields = {item.name for item in fields(ContextFrame)}
    snapshot = buffer.snapshot(note_scope)

    note_fields = {item.name for item in fields(AwarenessNote)}
    checks: dict[str, bool] = {
        "aware01_drop_retains_nothing": drop_result.note is None
        and buffer.count(_scope(drop_observation)) == 0,
        "aware02_think_keeps_existing_trigger": (
            isinstance(think_decision, CognitionTrigger)
            and think_decision.observation_ids == (think_observation.observation_id,)
            and note_only_decision is NO_COGNITION
        ),
        "aware03_note_admits_once": note_result.note is not None and buffer.count(note_scope) == 1,
        "aware04_note_no_model": all(
            token not in gate_source for token in ("modelrequest", "generate(", "provider")
        ),
        "aware05_note_no_action": all(
            token not in gate_source for token in ("actionproposal", "toolcall", "execute(")
        ),
        "aware06_note_no_wake": all(
            token not in gate_source for token in ("temporalwake", "schedule", "wakeintent")
        ),
        "aware07_provenance_is_trusted_typed": (
            note.source_refs == ("observation:observation-1", "event:event-1")
            and note.environment_id == "fixture"
            and note.surface_id == "surface-a"
        ),
        "aware08_payload_cannot_mint_directly": _payload_rejected(buffer),
        "aware09_deterministic_snapshot": _deterministic_snapshot(),
        "aware10_scope_isolation": buffer.snapshot(note_scope) == (note,)
        and buffer.snapshot(other_scope) != (),
        "aware11_per_scope_bound": len(overflow_snapshot) == 2,
        "aware12_overflow_is_fifo": tuple(item.observation_id for item in overflow_snapshot)
        == (
            "observation-12",
            "observation-13",
        ),
        "aware13_no_model_or_rng_ranking": all(
            token not in source for token in ("random", "model", "score", "rank")
        ),
        "aware14_source_refs_bounded": len(note.source_refs) <= MAX_AWARENESS_SOURCE_REFS,
        "aware15_reason_codes_bounded": len(note.reason_codes) <= MAX_AWARENESS_REASON_CODES,
        "aware16_no_text_projection_field": not any(
            name in {"text", "payload", "projection", "body"} for name in note_fields
        ),
        "aware17_snapshot_is_immutable_tuple": type(snapshot) is tuple,
        "aware18_note_is_immutable": _immutable_note(note),
        "aware19_no_mindstate_import": "mindstate" not in source,
        "aware20_not_observation_window": "observationwindow" not in source,
        "aware21_no_social_permission": "socialpermission" not in source,
        "aware22_no_contextframe_mutation": "contextframe" not in source,
        "aware23_no_model_request_awareness": "awareness" not in model_request_source,
        "aware24_non_durable_in_memory": all(
            token not in source for token in ("sqlite", "conversationstore", "persist")
        ),
        "aware25_restart_not_supported": (
            PeripheralAwarenessBuffer(clock=_FixedClock(_NOW)).total_count() == 0
        ),
        "aware26_expired_notes_omitted": expiry_buffer.snapshot(_scope(expiry_observation)) == (),
        "aware27_injected_clock_is_used": expiry_buffer.evidence()[-1].reason_code == "expired",
        "aware28_exact_reason_codes_preserved": note.reason_codes
        == (
            CognitionReasonCode.AMBIENT_CONTEXT,
            CognitionReasonCode.NOT_ADDRESSED,
        ),
        "aware29_duplicates_not_deduplicated": _duplicate_admissions(),
        "aware30_no_history_fields": not {item.name for item in fields(type(note))}.intersection(
            {"messages", "history", "conversation"}
        ),
        "aware31_no_memory_fields": not {item.name for item in fields(type(note))}.intersection(
            {"memory", "memories", "retrieval"}
        ),
        "aware32_no_raw_payload_copy": "payload" not in repr(note).casefold(),
        "aware33_attention_semantics_unchanged": (
            _drop_verdict(drop_observation).decision is AttentionDecision.DROP
            and _note_verdict(note_observation).decision is AttentionDecision.NOTE
            and _think_verdict(think_observation).decision is AttentionDecision.THINK
        ),
        "aware34_context_production_structure_unchanged": not any(
            token in model_request_source for token in ("while you were busy", "awareness")
        )
        and "awareness" not in {name.casefold() for name in frame_fields},
    }
    return checks


def _payload_rejected(buffer: PeripheralAwarenessBuffer) -> bool:
    try:
        buffer.admit({"text": "external"}, _note_verdict(_observation(40)), scope_id="runtime")  # type: ignore[arg-type]
    except TypeError:
        return True
    return False


def _critical_profile() -> AttentionSignalProfile:
    return AttentionSignalProfile(critical_event=True)


def _deterministic_snapshot() -> bool:
    first = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    second = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    observation = _observation(50)
    _admit(first, observation)
    _admit(second, observation)
    scope = _scope(observation)
    return first.snapshot(scope) == second.snapshot(scope)


def _immutable_note(note: object) -> bool:
    if note is None:
        return False
    try:
        note.note_id = "mutated"  # type: ignore[attr-defined]
    except FrozenInstanceError:
        return True
    return False


def _duplicate_admissions() -> bool:
    buffer = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    observation = _observation(60)
    first = _admit(buffer, observation)
    second = _admit(buffer, observation)
    return (
        first.note is not None
        and second.note is not None
        and first.note.note_id != second.note.note_id
    )


AWARE_V1_A_SCENARIOS: tuple[AwarenessEvalScenario, ...] = tuple(
    AwarenessEvalScenario(f"aware{i:02d}", description)
    for i, description in enumerate(
        (
            "DROP retains no awareness.",
            "THINK keeps the existing cognition trigger path.",
            "NOTE admits one bounded entry.",
            "NOTE does not call a model.",
            "NOTE does not create an action or tool call.",
            "NOTE does not schedule a wake.",
            "Entries retain trusted typed provenance.",
            "Arbitrary payloads cannot mint notes.",
            "Fixed inputs produce the same snapshot.",
            "Scopes isolate environments and surfaces.",
            "Per-scope capacity is enforced.",
            "Overflow ordering is FIFO and deterministic.",
            "Overflow has no model or random ranking.",
            "Source references are bounded.",
            "Reason codes are bounded.",
            "No text projection is stored in A.",
            "Snapshots are immutable tuples.",
            "Notes are immutable values.",
            "MindState remains outside the owner.",
            "ObservationWindow remains outside the owner.",
            "Social permission remains outside the owner.",
            "ContextFrame remains outside the owner.",
            "Production requests receive no awareness block.",
            "The owner is non-durable process memory.",
            "Restart recovery is unsupported by design.",
            "Expired notes are omitted.",
            "Expiry uses the injected clock.",
            "Attention reason semantics are preserved.",
            "Duplicate NOTE attempts are admitted in A.",
            "Canonical history is not stored.",
            "Memory records are not stored.",
            "Raw payload bodies are not copied.",
            "COG-V1 attention semantics remain unchanged.",
            "CTX-V1 production structure remains unchanged.",
        ),
        start=1,
    )
)


def evaluate_awareness_corpus() -> AwarenessEvalReport:
    checks = _checks()
    results = tuple(
        AwarenessEvalResult(scenario, passed)
        for scenario, passed in zip(AWARE_V1_A_SCENARIOS, checks.values(), strict=True)
    )
    return AwarenessEvalReport(results)
