"""Deterministic AWARE-V1-A and AWARE-V1-B contract scenarios."""

from __future__ import annotations

import ast
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
    MAX_AWARENESS_KEY_BYTES,
    MAX_AWARENESS_NOTES_PER_SCOPE,
    MAX_AWARENESS_NOTES_TOTAL,
    MAX_AWARENESS_OCCURRENCE_COUNT,
    MAX_AWARENESS_REASON_CODES,
    MAX_AWARENESS_SOURCE_REFS,
    AwarenessAdmission,
    AwarenessKey,
    AwarenessNote,
    AwarenessReconciliationStatus,
    AwarenessScope,
    PeripheralAwarenessBuffer,
)
from .context import ContextFrame
from .context_integration import ProductionContextComposer
from .contracts import NO_COGNITION, CognitionTrigger, EventSource, Observation, WorldEvent

__all__ = [
    "AWARE_V1_A_SCENARIOS",
    "AWARE_V1_B_SCENARIOS",
    "AwarenessEvalReport",
    "AwarenessEvalResult",
    "AwarenessEvalScenario",
    "evaluate_awareness_corpus",
    "evaluate_awareness_b_corpus",
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
        "aware13_no_rng_or_ranking": all(
            token not in source for token in ("random", "score", "rank")
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
        "aware23_no_model_request_awareness": all(
            token not in model_request_source for token in ("generate(", "execute(", "payload")
        ),
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
        "aware29_duplicates_coalesce": _duplicate_admissions(),
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
        "aware34_context_production_structure_unchanged": (
            "awareness" in {name.casefold() for name in frame_fields}
            and "while you were busy" not in model_request_source
        ),
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
        and first.note.note_id == second.note.note_id
        and second.note.occurrence_count == 2
        and second.coalesced
        and buffer.total_count() == 1
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
            "Production requests keep awareness projection bounded and separate.",
            "The owner is non-durable process memory.",
            "Restart recovery is unsupported by design.",
            "Expired notes are omitted.",
            "Expiry uses the injected clock.",
            "Attention reason semantics are preserved.",
            "Duplicate NOTE attempts coalesce in B.",
            "Canonical history is not stored.",
            "Memory records are not stored.",
            "Raw payload bodies are not copied.",
            "COG-V1 attention semantics remain unchanged.",
            "CTX-V1 production structure gains only the bounded awareness domain.",
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


def _b_admit(
    buffer: PeripheralAwarenessBuffer,
    observation: Observation,
    *,
    scope_id: str = "runtime",
    awareness_key: AwarenessKey | None = None,
) -> AwarenessAdmission:
    return buffer.admit(
        observation,
        _note_verdict(observation),
        scope_id=scope_id,
        awareness_key=awareness_key,
    )


def _b_checks() -> dict[str, bool]:
    """Run the deterministic B lifecycle corpus without any model dependency."""

    duplicate_clock = _FixedClock(_NOW)
    duplicate_buffer = PeripheralAwarenessBuffer(clock=duplicate_clock)
    duplicate_first = _b_admit(
        duplicate_buffer,
        _observation(101),
        awareness_key=AwarenessKey(dedup_key="duplicate"),
    )
    duplicate_clock.value += timedelta(seconds=1)
    duplicate_second = _b_admit(
        duplicate_buffer,
        _observation(102),
        awareness_key=AwarenessKey(dedup_key="duplicate"),
    )
    duplicate_note = duplicate_second.note

    scope_buffer = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    scope_a = _observation(103, subject="surface-a")
    scope_b = _observation(104, subject="surface-b")
    _b_admit(
        scope_buffer, scope_a, scope_id="scope-a", awareness_key=AwarenessKey(dedup_key="same")
    )
    _b_admit(
        scope_buffer, scope_b, scope_id="scope-b", awareness_key=AwarenessKey(dedup_key="same")
    )

    trusted_key_buffer = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    trusted_one = _b_admit(
        trusted_key_buffer,
        _observation(105),
        awareness_key=AwarenessKey(dedup_key="trusted-a"),
    )
    trusted_two = _b_admit(
        trusted_key_buffer,
        _observation(106),
        awareness_key=AwarenessKey(dedup_key="trusted-b"),
    )

    raw_key_rejected = False
    try:
        _b_admit(
            PeripheralAwarenessBuffer(clock=_FixedClock(_NOW)),
            _observation(107, payload={"awareness_key": "raw"}),
            awareness_key="raw",  # type: ignore[arg-type]
        )
    except TypeError:
        raw_key_rejected = True

    supersession_buffer = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    superseded_first = _b_admit(
        supersession_buffer,
        _observation(108),
        awareness_key=AwarenessKey(dedup_key="postgres", supersession_key="topic"),
    )
    superseded_second = _b_admit(
        supersession_buffer,
        _observation(109),
        awareness_key=AwarenessKey(dedup_key="rust", supersession_key="topic"),
    )
    supersession_scope_buffer = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    _b_admit(
        supersession_scope_buffer,
        _observation(110, subject="surface-a"),
        scope_id="scope-a",
        awareness_key=AwarenessKey(dedup_key="a", supersession_key="state"),
    )
    cross_scope_supersession = _b_admit(
        supersession_scope_buffer,
        _observation(111, subject="surface-b"),
        scope_id="scope-b",
        awareness_key=AwarenessKey(dedup_key="b", supersession_key="state"),
    )
    missing_super_buffer = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    _b_admit(missing_super_buffer, _observation(112))
    _b_admit(missing_super_buffer, _observation(113))

    handled_buffer = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    handled_admission = _b_admit(
        handled_buffer,
        _observation(114),
        awareness_key=AwarenessKey(dedup_key="handled"),
    )
    handled_note = handled_admission.note
    if handled_note is None:
        raise AssertionError("handled fixture did not admit")
    authority = handled_buffer.handled_authority()
    handled_result = handled_buffer.mark_handled(handled_note.note_id, authority=authority)
    untrusted_handled_rejected = False
    try:
        handled_buffer.mark_handled(handled_note.note_id, authority=None)  # type: ignore[arg-type]
    except TypeError:
        untrusted_handled_rejected = True

    expiry_clock = _FixedClock(_NOW)
    expiry_buffer = PeripheralAwarenessBuffer(clock=expiry_clock, ttl=timedelta(seconds=30))
    expiry_admission = _b_admit(expiry_buffer, _observation(115))
    expiry_clock.value += timedelta(seconds=30)
    expired_snapshot = expiry_buffer.snapshot(_scope(_observation(115)))
    expiry_reconciliation = expiry_buffer.mark_handled(
        expiry_admission.note.note_id if expiry_admission.note is not None else "missing",
        authority=expiry_buffer.handled_authority(),
    )

    compaction_clock = _FixedClock(_NOW)
    compaction_buffer = PeripheralAwarenessBuffer(
        clock=compaction_clock,
        ttl=timedelta(seconds=10),
        per_scope_capacity=1,
        total_capacity=1,
    )
    compacted_first = _b_admit(compaction_buffer, _observation(116))
    compaction_clock.value += timedelta(seconds=10)
    compacted_second = _b_admit(compaction_buffer, _observation(117))

    per_scope_buffer = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    for number in range(118, 118 + MAX_AWARENESS_NOTES_PER_SCOPE + 1):
        _b_admit(
            per_scope_buffer,
            _observation(number),
            awareness_key=AwarenessKey(dedup_key=f"scope-{number}"),
        )

    global_buffer = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    global_observations = tuple(
        _observation(number, subject=f"surface-{number}") for number in range(140, 140 + 65)
    )
    for number, observation in enumerate(global_observations):
        _b_admit(
            global_buffer,
            observation,
            awareness_key=AwarenessKey(dedup_key=f"global-{number}"),
        )

    overflow_clock = _FixedClock(_NOW)
    overflow_buffer = PeripheralAwarenessBuffer(
        clock=overflow_clock,
        ttl=timedelta(seconds=10),
        per_scope_capacity=2,
        total_capacity=2,
    )
    _b_admit(overflow_buffer, _observation(205), awareness_key=AwarenessKey(dedup_key="old"))
    overflow_clock.value += timedelta(seconds=10)
    overflow_admission = _b_admit(
        overflow_buffer,
        _observation(206),
        awareness_key=AwarenessKey(dedup_key="new"),
    )

    ordering_clock = _FixedClock(_NOW)
    ordering_buffer = PeripheralAwarenessBuffer(clock=ordering_clock)
    _b_admit(ordering_buffer, _observation(207), awareness_key=AwarenessKey(dedup_key="a"))
    _b_admit(ordering_buffer, _observation(208), awareness_key=AwarenessKey(dedup_key="b"))
    ordering_clock.value += timedelta(seconds=1)
    _b_admit(ordering_buffer, _observation(209), awareness_key=AwarenessKey(dedup_key="a"))
    ordering_snapshot = ordering_buffer.snapshot(_scope(_observation(207)))

    module = __import__("lilavel_runtime.awareness", fromlist=["PeripheralAwarenessBuffer"])
    module_source = inspect.getsource(module)
    module_tree = ast.parse(module_source)
    imported_modules = {
        node.module
        for node in ast.walk(module_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    lifecycle_calls = {
        node.func.attr
        for node in ast.walk(module_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    awareness_fields = {item.name for item in fields(AwarenessNote)}
    context_source = inspect.getsource(ProductionContextComposer).casefold()
    gate_source = inspect.getsource(DeterministicAttentionCognitionGate).casefold()
    checks: dict[str, bool] = {
        "awareb01_exact_duplicate_coalesces": (
            duplicate_first.note is not None
            and duplicate_note is not None
            and duplicate_first.note.note_id == duplicate_note.note_id
            and duplicate_second.coalesced
        ),
        "awareb02_duplicate_uses_one_slot": duplicate_buffer.total_count() == 1,
        "awareb03_duplicate_has_no_side_effect_lane": _note_gate_has_no_cognition_side_effects(),
        "awareb04_duplicate_refreshes_last_seen_and_ttl": (
            duplicate_note is not None
            and duplicate_note.last_seen_at == duplicate_clock.value
            and duplicate_note.expires_at == duplicate_clock.value + duplicate_buffer.ttl
        ),
        "awareb05_duplicate_occurrence_count_is_bounded": (
            duplicate_note is not None
            and duplicate_note.occurrence_count == 2
            and duplicate_note.occurrence_count <= MAX_AWARENESS_OCCURRENCE_COUNT
        ),
        "awareb06_scope_isolation_for_same_key": (
            scope_buffer.count(AwarenessScope("scope-a", "fixture", "surface-a")) == 1
            and scope_buffer.count(AwarenessScope("scope-b", "fixture", "surface-b")) == 1
        ),
        "awareb07_different_trusted_keys_do_not_coalesce": (
            trusted_one.note is not None
            and trusted_two.note is not None
            and trusted_one.note.note_id != trusted_two.note.note_id
            and trusted_key_buffer.total_count() == 2
        ),
        "awareb08_raw_payload_cannot_define_key": raw_key_rejected,
        "awareb09_explicit_supersession_replaces_old_state": (
            superseded_second.superseded_note_ids == (superseded_first.note.note_id,)
            if superseded_first.note is not None
            else False
        ),
        "awareb10_supersession_is_scope_local": (
            cross_scope_supersession.superseded_note_ids == ()
            and supersession_scope_buffer.total_count() == 2
        ),
        "awareb11_missing_supersession_metadata_does_not_guess": (
            missing_super_buffer.total_count() == 2
        ),
        "awareb12_superseded_note_absent_from_active_snapshot": (
            superseded_first.note is not None
            and all(
                item.note_id != superseded_first.note.note_id
                for item in supersession_buffer.snapshot(_scope(_observation(108)))
            )
        ),
        "awareb13_handled_note_absent_from_active_snapshot": (
            handled_result.status is AwarenessReconciliationStatus.HANDLED
            and handled_buffer.total_count() == 0
        ),
        "awareb14_untrusted_cannot_mark_handled": untrusted_handled_rejected,
        "awareb15_trusted_runtime_reconciliation_marks_handled": (
            handled_result.status is AwarenessReconciliationStatus.HANDLED
        ),
        "awareb16_expired_note_absent": expired_snapshot == (),
        "awareb17_expiry_is_not_handled": (
            expiry_reconciliation.status is AwarenessReconciliationStatus.NOT_FOUND
            and all(item.handled_count == 0 for item in expiry_buffer.evidence())
            and any(item.expired_count == 1 for item in expiry_buffer.evidence())
        ),
        "awareb18_compaction_precedes_overflow": (
            compacted_first.note is not None
            and compacted_second.evicted_note_ids == ()
            and compacted_first.note.note_id in compacted_second.compacted_note_ids
        ),
        "awareb19_per_scope_bound_remains_sixteen": (
            per_scope_buffer.count(_scope(_observation(118))) == MAX_AWARENESS_NOTES_PER_SCOPE
        ),
        "awareb20_global_bound_remains_sixty_four": global_buffer.total_count()
        == MAX_AWARENESS_NOTES_TOTAL,
        "awareb21_overflow_is_deterministic_after_compaction": (
            overflow_admission.evicted_note_ids == () and overflow_buffer.total_count() == 1
        ),
        "awareb22_source_refs_remain_bounded": (
            duplicate_note is not None
            and len(duplicate_note.source_refs) <= MAX_AWARENESS_SOURCE_REFS
        ),
        "awareb23_reason_codes_remain_bounded": (
            duplicate_note is not None
            and len(duplicate_note.reason_codes) <= MAX_AWARENESS_REASON_CODES
        ),
        "awareb24_identity_fields_are_bounded": (
            len(AwarenessKey(dedup_key="x" * MAX_AWARENESS_KEY_BYTES).dedup_key or "")
            == MAX_AWARENESS_KEY_BYTES
        ),
        "awareb25_refresh_moves_note_to_newest_order": (
            tuple(item.observation_id for item in ordering_snapshot)
            == ("observation-208", "observation-209")
        ),
        "awareb26_snapshot_is_immutable": (
            type(ordering_snapshot) is tuple and ordering_snapshot[0].__dataclass_params__.frozen  # type: ignore[attr-defined]
        ),
        "awareb27_caller_mutation_cannot_change_lifecycle": _immutable_note(duplicate_note),
        "awareb28_restart_loses_awareness_by_design": (
            PeripheralAwarenessBuffer(clock=_FixedClock(_NOW)).total_count() == 0
        ),
        "awareb29_no_persistence_writes": not imported_modules.intersection(
            {"lilavel_core.persistence", "persistence"}
        ),
        "awareb30_no_canonical_history": not awareness_fields.intersection(
            {"messages", "history", "conversation"}
        ),
        "awareb31_no_memory_records": not awareness_fields.intersection(
            {"memory", "memories", "retrieval"}
        ),
        "awareb32_no_context_frame_projection": (
            "awareness" in context_source and "generate(" not in context_source
        ),
        "awareb33_no_extra_model_calls": "generate(" not in gate_source,
        "awareb34_attention_semantics_unchanged": (
            _drop_verdict(_observation(218)).decision is AttentionDecision.DROP
            and _note_verdict(_observation(219)).decision is AttentionDecision.NOTE
            and _think_verdict(_observation(220)).decision is AttentionDecision.THINK
        ),
        "awareb35_think_does_not_dual_write": _think_does_not_admit_awareness(),
        "awareb36_context_topology_unchanged": (
            "compile_context_projection" in context_source
            and "contextframebuilder" in context_source
        ),
        "awareb37_effect_authority_unchanged": not imported_modules.intersection(
            {"lilavel_runtime.proposal_application", "lilavel_runtime.temporal"}
        ),
        "awareb38_compaction_is_deterministic": _deterministic_compaction(),
        "awareb39_no_rng_model_ranking_or_scoring": not lifecycle_calls.intersection(
            {"generate", "generate_for_run", "execute", "random", "rank", "score"}
        ),
        "awareb40_architecture_import_boundary_is_clean": imported_modules.issubset(
            {
                "__future__",
                "collections",
                "collections.abc",
                "dataclasses",
                "datetime",
                "enum",
                "hashlib",
                "threading",
                "typing",
                "lilavel_core",
                "attention",
                "contracts",
            }
        ),
    }
    return checks


def _note_gate_has_no_cognition_side_effects() -> bool:
    buffer = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    observation = _observation(221)
    decision = DeterministicAttentionCognitionGate(awareness_buffer=buffer).decide((observation,))
    return decision is NO_COGNITION and buffer.total_count() == 1


def _think_does_not_admit_awareness() -> bool:
    observation = _observation(222, kind="critical")
    buffer = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW))
    gate = DeterministicAttentionCognitionGate(
        extractor=AttentionEvidenceExtractor({"critical": _critical_profile()}),
        awareness_buffer=buffer,
    )
    decision = gate.decide((observation,))
    return isinstance(decision, CognitionTrigger) and buffer.total_count() == 0


def _deterministic_compaction() -> bool:
    first = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW), ttl=timedelta(seconds=5))
    second = PeripheralAwarenessBuffer(clock=_FixedClock(_NOW), ttl=timedelta(seconds=5))
    observations = (_observation(223), _observation(224))
    for buffer in (first, second):
        for observation in observations:
            _b_admit(
                buffer,
                observation,
                awareness_key=AwarenessKey(dedup_key=observation.event.event_id),
            )
    return first.compact(now=_NOW) == second.compact(now=_NOW)


AWARE_V1_B_SCENARIOS: tuple[AwarenessEvalScenario, ...] = tuple(
    AwarenessEvalScenario(f"awareb{i:02}", description)
    for i, description in enumerate(
        (
            "Exact duplicate NOTE events coalesce deterministically.",
            "Duplicate coalescing does not consume an additional active slot.",
            "Duplicate NOTE admission creates no cognition, action, or wake side effect.",
            "Duplicate coalescing refreshes last_seen_at and TTL.",
            "Duplicate occurrence counts are bounded and saturating.",
            "The same key in different scopes does not coalesce.",
            "Different trusted keys do not coalesce.",
            "Raw payload text cannot define a dedup key through the API.",
            "A newer trusted state supersedes an older state family member.",
            "Supersession is forbidden across exact scopes.",
            "Missing supersession metadata never causes guessed replacement.",
            "Superseded entries are absent from active snapshots.",
            "Handled entries are absent from active snapshots.",
            "Model or untrusted values cannot mark awareness handled.",
            "Trusted runtime reconciliation can mark awareness handled.",
            "Expired entries are absent from active snapshots.",
            "Expiry is distinct from handled state.",
            "Structural compaction precedes overflow eviction.",
            "The per-scope active bound remains sixteen.",
            "The global active bound remains sixty-four.",
            "Overflow remains deterministic after compaction.",
            "Source references remain bounded after coalescing.",
            "Reason codes remain bounded after coalescing.",
            "Deduplication and supersession keys obey byte bounds.",
            "Snapshot ordering remains deterministic after refresh.",
            "Snapshots are immutable tuples of immutable notes.",
            "Caller mutation cannot alter stored lifecycle state.",
            "Restart recovery remains intentionally unsupported.",
            "No persistence writes are introduced.",
            "No canonical conversation history is stored.",
            "No memory records are created.",
            "Production ContextFrame awareness remains an optional bounded domain.",
            "No additional model call is introduced.",
            "DROP/NOTE/THINK attention semantics remain unchanged.",
            "THINK still does not dual-write awareness.",
            "CTX-V1 request topology keeps one existing composition path.",
            "COG/E2 effect authority remains outside awareness.",
            "Compaction is deterministic for fixed clock and input order.",
            "No RNG/model ranking/scoring is used.",
            "The awareness module retains its architectural import boundary.",
        ),
        start=1,
    )
)


def evaluate_awareness_b_corpus() -> AwarenessEvalReport:
    checks = _b_checks()
    results = tuple(
        AwarenessEvalResult(scenario, passed)
        for scenario, passed in zip(AWARE_V1_B_SCENARIOS, checks.values(), strict=True)
    )
    return AwarenessEvalReport(results)
