"""Trusted MIND-1D application of completed cognition proposals."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import cast

from lilavel_contracts import ToolCall, ToolEffect, ToolResult, ToolResultStatus
from lilavel_core.tool_runtime import (
    ApplicationToolSessionFactory,
    ToolBatchCorrelation,
    ToolGenerationContext,
    ToolSessionUncontained,
)

from .contracts import (
    ActionProposalKind,
    CognitionOutcome,
    StateProposal,
    StateProposalKind,
)
from .mind import (
    MindState,
    MindStateCapacityExceeded,
    MindStateDelta,
    MindStateDeltaKind,
    MindStateVersionConflict,
)
from .temporal import (
    TemporalApplication,
    TemporalApplicationStatus,
    TemporalCoordinator,
    TemporalPreparation,
    TemporalProposalApplication,
)
from .tool_registry import ApplicationToolRegistry, validate_tool_arguments

MAX_APPLICATION_FENCES = 256

type StateProvenanceResolver = Callable[[CognitionOutcome], "MindStateProvenance"]


class ProposalApplicationStatus(StrEnum):
    APPLIED = "applied"
    PARTIAL = "partial"
    REJECTED = "rejected"
    STALE = "stale"
    FAILED = "failed"
    DUPLICATE = "duplicate"
    INELIGIBLE = "ineligible"
    NO_PROPOSALS = "no_proposals"


class StateApplicationStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    APPLIED = "applied"
    REJECTED = "rejected"
    STALE = "stale"
    FAILED = "failed"


class ActionApplicationStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    NOT_ATTEMPTED = "not_attempted"
    APPLIED = "applied"
    PARTIAL = "partial"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MindStateProvenance:
    """Trusted Core provenance supplied by application composition."""

    user_message_id: str
    assistant_message_id: str

    def __post_init__(self) -> None:
        _require_text(self.user_message_id, "user_message_id")
        _require_text(self.assistant_message_id, "assistant_message_id")


@dataclass(frozen=True, slots=True)
class StateProposalApplication:
    proposal_index: int
    kind: StateProposalKind
    status: StateApplicationStatus
    reason_code: str | None = None
    intention_id: str | None = None


@dataclass(frozen=True, slots=True)
class StateApplication:
    status: StateApplicationStatus
    expected_version: int | None
    version_before: int
    version_after: int
    proposals: tuple[StateProposalApplication, ...] = ()
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class ActionProposalApplication:
    """One action settlement; raw proposal content is intentionally absent."""

    proposal_index: int
    call_id: str
    tool_name: str | None
    status: ToolResultStatus | None
    effect: ToolEffect | None
    reason_code: str | None = None
    result: ToolResult | None = None
    attempted: bool = False


@dataclass(frozen=True, slots=True)
class ActionApplication:
    status: ActionApplicationStatus
    proposals: tuple[ActionProposalApplication, ...] = ()
    settlement: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class ProposalApplicationResult:
    application_id: str
    outcome_id: str
    status: ProposalApplicationStatus
    state: StateApplication
    actions: ActionApplication
    reason_code: str | None = None
    temporal: TemporalApplication = field(
        default_factory=lambda: TemporalApplication(TemporalApplicationStatus.NOT_REQUESTED)
    )


@dataclass(frozen=True, slots=True)
class _PreparedState:
    deltas: tuple[MindStateDelta, ...]
    status: StateApplicationStatus
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class _PreparedActions:
    calls: tuple[ToolCall, ...]
    results: tuple[ActionProposalApplication, ...]
    valid: bool
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class _PreparedTemporal:
    preparation: TemporalPreparation | None
    application: TemporalApplication
    valid: bool


class ProposalApplicationCoordinator:
    """Apply one completed MIND-1C outcome through trusted runtime seams.

    The coordinator validates the whole proposal set before touching state or
    creating a tool session. Local state is committed first as one atomic
    batch. External actions then run in proposal order through the existing P4
    ``ApplicationToolSessionFactory``. External effects are never rolled back.
    """

    def __init__(
        self,
        mind_state: MindState,
        *,
        scope_id: str,
        state_provenance: MindStateProvenance | StateProvenanceResolver | None = None,
        tool_registry: ApplicationToolRegistry | None = None,
        tool_session_factory: ApplicationToolSessionFactory | None = None,
        action_tool_names: Mapping[ActionProposalKind, str] | None = None,
        temporal_coordinator: TemporalCoordinator | None = None,
        runtime_instance_id: str = "runtime",
        fence_capacity: int = MAX_APPLICATION_FENCES,
    ) -> None:
        if type(mind_state) is not MindState:
            raise TypeError("mind_state must be a MindState")
        _require_text(scope_id, "scope_id")
        _require_text(runtime_instance_id, "runtime_instance_id")
        if isinstance(fence_capacity, bool) or not 0 < fence_capacity <= MAX_APPLICATION_FENCES:
            raise ValueError("fence_capacity is outside its bound")
        if (tool_registry is None) != (tool_session_factory is None):
            raise ValueError("tool registry and session factory must be supplied together")

        routes = dict(action_tool_names or {})
        for kind, name in routes.items():
            if type(kind) is not ActionProposalKind:
                raise TypeError("action tool routes must use ActionProposalKind keys")
            _require_text(name, "action tool name")
        self._mind_state = mind_state
        self._scope_id = scope_id
        self._runtime_instance_id = runtime_instance_id
        self._state_provenance = state_provenance
        self._tool_registry = tool_registry
        self._tool_session_factory = tool_session_factory
        self._temporal_coordinator = temporal_coordinator
        self._action_tool_names = cast(Mapping[ActionProposalKind, str], MappingProxyType(routes))
        self._fence_capacity = fence_capacity
        self._lock = threading.RLock()
        self._fences: dict[str, ProposalApplicationResult] = {}

    @property
    def application_count(self) -> int:
        with self._lock:
            return len(self._fences)

    def applications(self) -> tuple[ProposalApplicationResult, ...]:
        """Return bounded lifecycle results, including rejected attempts."""

        with self._lock:
            return tuple(self._fences.values())

    def application_id_for(self, outcome: CognitionOutcome) -> str:
        """Return the deterministic fence identity for one outcome."""

        if type(outcome) is not CognitionOutcome:
            raise TypeError("outcome must be a CognitionOutcome")
        return self._application_id(outcome)

    def apply_sync(self, outcome: CognitionOutcome) -> ProposalApplicationResult:
        """Apply outside an event loop, waiting for P4 settlement."""

        if type(outcome) is not CognitionOutcome:
            return self._invalid_input_result()

        application_id = self._application_id(outcome)
        with self._lock:
            previous = self._fences.get(application_id)
            if previous is not None:
                return self._duplicate_result(previous)
            if len(self._fences) >= self._fence_capacity:
                return self._rejected_result(
                    outcome,
                    application_id,
                    ProposalApplicationStatus.REJECTED,
                    "application_fence_full",
                )
            result = self._apply_locked(outcome, application_id)
            return self._record(application_id, result)

    async def apply(self, outcome: CognitionOutcome) -> ProposalApplicationResult:
        """Apply without blocking the event loop and join cancellation safely."""

        operation = asyncio.create_task(asyncio.to_thread(self.apply_sync, outcome))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            # Application cancellation is not rollback.  Wait for the fenced
            # operation to settle before propagating caller cancellation.
            await asyncio.shield(operation)
            raise

    def _apply_locked(
        self, outcome: CognitionOutcome, application_id: str
    ) -> ProposalApplicationResult:
        current_version = self._mind_state.version
        if not outcome.is_completed:
            return self._rejected_result(
                outcome,
                application_id,
                ProposalApplicationStatus.INELIGIBLE,
                "cognition_not_completed",
            )
        if not outcome.scope_id or outcome.scope_id != self._scope_id:
            return self._rejected_result(
                outcome,
                application_id,
                ProposalApplicationStatus.REJECTED,
                "scope_mismatch",
            )

        prepared_temporal = self._prepare_temporal(outcome)
        if not prepared_temporal.valid:
            return self._rejected_result(
                outcome,
                application_id,
                ProposalApplicationStatus.REJECTED,
                prepared_temporal.application.reason_code or "temporal_application_rejected",
                temporal=prepared_temporal.application,
            )

        prepared_state = self._prepare_state(outcome, current_version)
        prepared_actions = self._prepare_actions(outcome, application_id)

        if prepared_state.status is StateApplicationStatus.STALE:
            state = self._state_rejected(
                outcome,
                current_version,
                StateApplicationStatus.STALE,
                prepared_state.reason_code or "state_version_conflict",
            )
            actions = self._actions_blocked(prepared_actions, "state_application_blocked")
            return ProposalApplicationResult(
                application_id,
                outcome.episode_id,
                ProposalApplicationStatus.STALE,
                state,
                actions,
                "state_version_conflict",
                self._temporal_blocked(outcome, "state_application_blocked"),
            )

        if prepared_state.status is StateApplicationStatus.REJECTED or not prepared_actions.valid:
            reason = prepared_state.reason_code or prepared_actions.reason_code or "batch_rejected"
            if outcome.state_proposals:
                state = self._state_rejected(
                    outcome, current_version, StateApplicationStatus.REJECTED, reason
                )
            else:
                state = self._state_not_requested(current_version)
            actions = (
                ActionApplication(
                    ActionApplicationStatus.REJECTED,
                    prepared_actions.results,
                    reason_code=reason,
                )
                if outcome.action_proposals
                else self._actions_not_requested()
            )
            return ProposalApplicationResult(
                application_id,
                outcome.episode_id,
                ProposalApplicationStatus.REJECTED,
                state,
                actions,
                reason,
                self._temporal_blocked(outcome, "proposal_batch_rejected"),
            )

        if prepared_state.deltas:
            try:
                intentions = self._mind_state.apply_deltas(
                    prepared_state.deltas,
                    expected_version=cast(int, outcome.based_on_state_version),
                )
            except MindStateVersionConflict:
                state = self._state_rejected(
                    outcome,
                    self._mind_state.version,
                    StateApplicationStatus.STALE,
                    "state_version_conflict",
                )
                actions = self._actions_blocked(prepared_actions, "state_application_blocked")
                return ProposalApplicationResult(
                    application_id,
                    outcome.episode_id,
                    ProposalApplicationStatus.STALE,
                    state,
                    actions,
                    "state_version_conflict",
                    self._temporal_blocked(outcome, "state_application_blocked"),
                )
            except MindStateCapacityExceeded:
                state = self._state_rejected(
                    outcome,
                    self._mind_state.version,
                    StateApplicationStatus.REJECTED,
                    "state_capacity_exceeded",
                )
                actions = self._actions_blocked(prepared_actions, "state_application_blocked")
                return ProposalApplicationResult(
                    application_id,
                    outcome.episode_id,
                    ProposalApplicationStatus.REJECTED,
                    state,
                    actions,
                    "state_capacity_exceeded",
                    self._temporal_blocked(outcome, "state_application_blocked"),
                )
            except Exception:
                state = self._state_rejected(
                    outcome,
                    self._mind_state.version,
                    StateApplicationStatus.FAILED,
                    "state_application_failed",
                )
                actions = self._actions_blocked(prepared_actions, "state_application_blocked")
                return ProposalApplicationResult(
                    application_id,
                    outcome.episode_id,
                    ProposalApplicationStatus.FAILED,
                    state,
                    actions,
                    "state_application_failed",
                    self._temporal_blocked(outcome, "state_application_blocked"),
                )
            state = StateApplication(
                StateApplicationStatus.APPLIED,
                outcome.based_on_state_version,
                current_version,
                self._mind_state.version,
                tuple(
                    StateProposalApplication(
                        index,
                        proposal.kind,
                        StateApplicationStatus.APPLIED,
                        intention_id=intention.intention_id,
                    )
                    for index, (proposal, intention) in enumerate(
                        zip(outcome.state_proposals, intentions, strict=True)
                    )
                ),
            )
        else:
            state = self._state_not_requested(current_version)

        temporal = self._commit_temporal(prepared_temporal, outcome)
        if temporal.status is TemporalApplicationStatus.REJECTED:
            actions = (
                self._actions_blocked(prepared_actions, "temporal_application_blocked")
                if outcome.action_proposals
                else self._actions_not_requested()
            )
            status = (
                ProposalApplicationStatus.PARTIAL
                if state.status is StateApplicationStatus.APPLIED
                else ProposalApplicationStatus.REJECTED
            )
            return ProposalApplicationResult(
                application_id,
                outcome.episode_id,
                status,
                state,
                actions,
                temporal.reason_code or "temporal_application_rejected",
                temporal,
            )

        if not outcome.action_proposals:
            status = (
                ProposalApplicationStatus.APPLIED
                if (
                    state.status is StateApplicationStatus.APPLIED
                    or temporal.status
                    in {
                        TemporalApplicationStatus.APPLIED,
                        TemporalApplicationStatus.DUPLICATE,
                    }
                )
                else ProposalApplicationStatus.NO_PROPOSALS
            )
            return ProposalApplicationResult(
                application_id,
                outcome.episode_id,
                status,
                state,
                self._actions_not_requested(),
                temporal=temporal,
            )

        actions = self._execute_actions(prepared_actions, application_id)
        status = self._combined_status(state, actions)
        if (
            temporal.status is TemporalApplicationStatus.DUPLICATE
            and status is ProposalApplicationStatus.NO_PROPOSALS
        ):
            status = ProposalApplicationStatus.APPLIED
        return ProposalApplicationResult(
            application_id, outcome.episode_id, status, state, actions, temporal=temporal
        )

    def _prepare_state(self, outcome: CognitionOutcome, current_version: int) -> _PreparedState:
        if not outcome.state_proposals:
            return _PreparedState((), StateApplicationStatus.NOT_REQUESTED, None)
        expected = outcome.based_on_state_version
        if expected is None or expected != current_version:
            return _PreparedState((), StateApplicationStatus.STALE, "state_version_conflict")

        provenance = self._resolve_provenance(outcome)
        if provenance is None:
            return _PreparedState((), StateApplicationStatus.REJECTED, "missing_state_provenance")

        deltas: list[MindStateDelta] = []
        for proposal in outcome.state_proposals:
            if type(proposal) is not StateProposal:
                return _PreparedState(
                    (), StateApplicationStatus.REJECTED, "unsupported_state_proposal"
                )
            if proposal.kind is not StateProposalKind.CREATE_INTENTION:
                return _PreparedState(
                    (), StateApplicationStatus.REJECTED, "unsupported_state_proposal"
                )
            deltas.append(
                MindStateDelta(
                    MindStateDeltaKind.CREATE_INTENTION,
                    proposal.text,
                    provenance.user_message_id,
                    provenance.assistant_message_id,
                )
            )
        return _PreparedState(tuple(deltas), StateApplicationStatus.APPLIED, None)

    def _prepare_actions(self, outcome: CognitionOutcome, application_id: str) -> _PreparedActions:
        if not outcome.action_proposals:
            return _PreparedActions((), (), True, None)
        if self._tool_registry is None or self._tool_session_factory is None:
            unavailable_results = tuple(
                self._action_result(
                    index,
                    self._call_id(application_id, index),
                    None,
                    ToolResultStatus.UNAVAILABLE,
                    ToolEffect.NONE,
                    "tool_application_not_configured",
                    result=ToolResult(
                        self._call_id(application_id, index),
                        ToolResultStatus.UNAVAILABLE,
                        None,
                        reason_code="tool_unavailable",
                    ),
                )
                for index, _ in enumerate(outcome.action_proposals)
            )
            return _PreparedActions(
                (), unavailable_results, False, "tool_application_not_configured"
            )

        route_names = tuple(dict.fromkeys(self._action_tool_names.values()))
        try:
            exposure = self._tool_registry.snapshot(route_names)
        except Exception:
            return _PreparedActions((), (), False, "tool_exposure_invalid")

        calls: list[ToolCall] = []
        results: list[ActionProposalApplication] = []
        valid = True
        first_reason: str | None = None
        for index, proposal in enumerate(outcome.action_proposals):
            call_id = self._call_id(application_id, index)
            name = self._action_tool_names.get(proposal.kind)
            if name is None:
                valid = False
                first_reason = first_reason or "unsupported_action_proposal"
                results.append(
                    self._action_result(
                        index,
                        call_id,
                        None,
                        ToolResultStatus.INVALID,
                        ToolEffect.NONE,
                        first_reason,
                    )
                )
                continue
            arguments: Mapping[str, object]
            if proposal.kind is ActionProposalKind.SPEAK:
                arguments = {"text": proposal.content}
            elif proposal.kind is ActionProposalKind.STAY_SILENT:
                arguments = {}
            else:
                valid = False
                first_reason = first_reason or "unsupported_action_proposal"
                results.append(
                    self._action_result(
                        index,
                        call_id,
                        name,
                        ToolResultStatus.INVALID,
                        ToolEffect.NONE,
                        first_reason,
                    )
                )
                continue

            call = ToolCall(call_id, name, arguments)
            binding = exposure.binding_for(name)
            reason = (
                "tool_unavailable"
                if binding is None
                else validate_tool_arguments(binding.spec, call.arguments)
            )
            if reason is not None:
                valid = False
                first_reason = first_reason or reason
                status = (
                    ToolResultStatus.UNAVAILABLE if binding is None else ToolResultStatus.INVALID
                )
                result = ToolResult(call_id, status, None, reason_code=reason)
                results.append(
                    self._action_result(
                        index, call_id, name, status, ToolEffect.NONE, reason, result=result
                    )
                )
            else:
                calls.append(call)
                results.append(
                    self._action_result(index, call_id, name, None, None, None, attempted=False)
                )

        if not valid:
            return _PreparedActions((), tuple(results), False, first_reason or "batch_rejected")
        return _PreparedActions(tuple(calls), tuple(results), True, None)

    def _execute_actions(
        self, prepared: _PreparedActions, application_id: str
    ) -> ActionApplication:
        assert self._tool_session_factory is not None
        context = ToolGenerationContext(
            self._runtime_instance_id,
            self._scope_id,
            application_id,
            self._generation_id(application_id),
            1,
        )
        try:
            session = self._tool_session_factory.create(context)
        except Exception:
            return self._actions_not_attempted(prepared, "tool_session_unavailable")

        correlation = ToolBatchCorrelation(context, 1)
        try:
            raw_results = tuple(
                session.execute_batch(correlation, prepared.calls, threading.Event())
            )
        except ToolSessionUncontained:
            actions = self._settlements(prepared, (), unknown_index=0)
            return ActionApplication(
                self._action_status(actions), actions, "uncontained", "executor_uncontained"
            )
        except Exception:
            return self._actions_not_attempted(prepared, "tool_session_failed")

        actions = self._settlements(prepared, raw_results)
        settlement = getattr(session, "settlement", None)
        return ActionApplication(self._action_status(actions), actions, settlement, None)

    def _prepare_temporal(self, outcome: CognitionOutcome) -> _PreparedTemporal:
        if not outcome.temporal_proposals:
            return _PreparedTemporal(
                None,
                TemporalApplication(TemporalApplicationStatus.NOT_REQUESTED),
                True,
            )
        coordinator = self._temporal_coordinator
        if coordinator is None:
            return _PreparedTemporal(
                None,
                self._temporal_rejected(outcome, "temporal_application_not_configured"),
                False,
            )
        try:
            preparation = coordinator.prepare(
                outcome.temporal_proposals,
                source_episode_id=outcome.episode_id,
                source_trigger_id=outcome.trigger_id,
            )
        except Exception:
            return _PreparedTemporal(
                None,
                self._temporal_rejected(outcome, "temporal_proposal_invalid"),
                False,
            )
        if not preparation.valid:
            return _PreparedTemporal(
                preparation,
                preparation.rejection
                or self._temporal_rejected(outcome, preparation.reason_code or "temporal_rejected"),
                False,
            )
        return _PreparedTemporal(
            preparation,
            TemporalApplication(TemporalApplicationStatus.NOT_REQUESTED),
            True,
        )

    def _commit_temporal(
        self, prepared: _PreparedTemporal, outcome: CognitionOutcome
    ) -> TemporalApplication:
        if prepared.preparation is None:
            return prepared.application
        coordinator = self._temporal_coordinator
        if coordinator is None:
            return self._temporal_rejected(outcome, "temporal_application_not_configured")
        try:
            return coordinator.commit(
                prepared.preparation,
                source_episode_id=outcome.episode_id,
                source_trigger_id=outcome.trigger_id,
            )
        except Exception:
            return self._temporal_rejected(outcome, "temporal_commit_failed")

    @staticmethod
    def _temporal_rejected(outcome: CognitionOutcome, reason: str) -> TemporalApplication:
        return TemporalApplication(
            TemporalApplicationStatus.REJECTED,
            tuple(
                TemporalProposalApplication(
                    index,
                    TemporalApplicationStatus.REJECTED,
                    reason_code=reason,
                )
                for index, _ in enumerate(outcome.temporal_proposals)
            ),
            reason,
        )

    @staticmethod
    def _temporal_blocked(outcome: CognitionOutcome, reason: str) -> TemporalApplication:
        if not outcome.temporal_proposals:
            return TemporalApplication(TemporalApplicationStatus.NOT_REQUESTED)
        return ProposalApplicationCoordinator._temporal_rejected(outcome, reason)

    def _settlements(
        self,
        prepared: _PreparedActions,
        raw_results: Sequence[object],
        *,
        unknown_index: int | None = None,
    ) -> tuple[ActionProposalApplication, ...]:
        results: list[ActionProposalApplication] = []
        for index, call in enumerate(prepared.calls):
            if index < len(raw_results) and type(raw_results[index]) is ToolResult:
                result = cast(ToolResult, raw_results[index])
                if result.call_id == call.call_id:
                    results.append(
                        self._action_result(
                            index,
                            call.call_id,
                            call.tool_name,
                            result.status,
                            result.effect,
                            result.reason_code,
                            result=result,
                            attempted=True,
                        )
                    )
                    continue
            if unknown_index == index:
                result = ToolResult(
                    call.call_id,
                    ToolResultStatus.FAILED,
                    None,
                    reason_code="executor_uncontained",
                    effect=ToolEffect.UNKNOWN,
                )
                results.append(
                    self._action_result(
                        index,
                        call.call_id,
                        call.tool_name,
                        result.status,
                        result.effect,
                        result.reason_code,
                        result=result,
                        attempted=True,
                    )
                )
            else:
                results.append(
                    self._action_result(
                        index,
                        call.call_id,
                        call.tool_name,
                        None,
                        None,
                        "not_attempted",
                    )
                )
        return tuple(results)

    def _resolve_provenance(self, outcome: CognitionOutcome) -> MindStateProvenance | None:
        source = self._state_provenance
        try:
            provenance = source(outcome) if callable(source) else source
        except Exception:
            return None
        return provenance if type(provenance) is MindStateProvenance else None

    def _record(
        self, application_id: str, result: ProposalApplicationResult
    ) -> ProposalApplicationResult:
        self._fences[application_id] = result
        return result

    def _duplicate_result(self, previous: ProposalApplicationResult) -> ProposalApplicationResult:
        return ProposalApplicationResult(
            previous.application_id,
            previous.outcome_id,
            ProposalApplicationStatus.DUPLICATE,
            previous.state,
            previous.actions,
            "application_already_settled",
            previous.temporal,
        )

    def _rejected_result(
        self,
        outcome: CognitionOutcome,
        application_id: str,
        status: ProposalApplicationStatus,
        reason: str,
        *,
        temporal: TemporalApplication | None = None,
    ) -> ProposalApplicationResult:
        current = self._mind_state.version
        state = (
            self._state_rejected(
                outcome,
                current,
                StateApplicationStatus.STALE
                if status is ProposalApplicationStatus.STALE
                else StateApplicationStatus.REJECTED,
                reason,
            )
            if outcome.state_proposals
            else self._state_not_requested(current)
        )
        actions = (
            ActionApplication(
                ActionApplicationStatus.REJECTED,
                tuple(
                    self._action_result(
                        index,
                        self._call_id(application_id, index),
                        None,
                        ToolResultStatus.INVALID,
                        ToolEffect.NONE,
                        reason,
                    )
                    for index, _ in enumerate(outcome.action_proposals)
                ),
                reason_code=reason,
            )
            if outcome.action_proposals
            else self._actions_not_requested()
        )
        return ProposalApplicationResult(
            application_id,
            outcome.episode_id,
            status,
            state,
            actions,
            reason,
            temporal
            or (
                self._temporal_rejected(outcome, reason)
                if outcome.temporal_proposals
                else TemporalApplication(TemporalApplicationStatus.NOT_REQUESTED)
            ),
        )

    @staticmethod
    def _state_not_requested(current_version: int) -> StateApplication:
        return StateApplication(
            StateApplicationStatus.NOT_REQUESTED, None, current_version, current_version
        )

    @staticmethod
    def _state_rejected(
        outcome: CognitionOutcome,
        current_version: int,
        status: StateApplicationStatus,
        reason: str,
    ) -> StateApplication:
        return StateApplication(
            status,
            outcome.based_on_state_version,
            current_version,
            current_version,
            tuple(
                StateProposalApplication(index, proposal.kind, status, reason_code=reason)
                for index, proposal in enumerate(outcome.state_proposals)
            ),
            reason,
        )

    @staticmethod
    def _actions_not_requested() -> ActionApplication:
        return ActionApplication(ActionApplicationStatus.NOT_REQUESTED)

    @staticmethod
    def _actions_blocked(prepared: _PreparedActions, reason: str) -> ActionApplication:
        records = tuple(
            ActionProposalApplication(
                item.proposal_index,
                item.call_id,
                item.tool_name,
                None,
                None,
                reason,
                attempted=False,
            )
            for item in prepared.results
        )
        return ActionApplication(ActionApplicationStatus.NOT_ATTEMPTED, records, reason_code=reason)

    @staticmethod
    def _actions_not_attempted(prepared: _PreparedActions, reason: str) -> ActionApplication:
        records = tuple(
            ActionProposalApplication(
                item.proposal_index,
                item.call_id,
                item.tool_name,
                None,
                None,
                reason,
                attempted=False,
            )
            for item in prepared.results
        )
        return ActionApplication(ActionApplicationStatus.FAILED, records, reason_code=reason)

    @staticmethod
    def _action_status(
        results: Sequence[ActionProposalApplication],
    ) -> ActionApplicationStatus:
        if not results:
            return ActionApplicationStatus.NOT_ATTEMPTED
        if any(not item.attempted for item in results):
            return (
                ActionApplicationStatus.PARTIAL
                if any(item.status is ToolResultStatus.OK for item in results)
                else ActionApplicationStatus.FAILED
            )
        if all(item.status is ToolResultStatus.OK for item in results):
            return ActionApplicationStatus.APPLIED
        if any(
            item.status in {ToolResultStatus.FAILED, ToolResultStatus.TIMED_OUT} for item in results
        ):
            return (
                ActionApplicationStatus.PARTIAL
                if any(item.status is ToolResultStatus.OK for item in results)
                else ActionApplicationStatus.FAILED
            )
        return ActionApplicationStatus.REJECTED

    @staticmethod
    def _combined_status(
        state: StateApplication, actions: ActionApplication
    ) -> ProposalApplicationStatus:
        if state.status is StateApplicationStatus.APPLIED:
            return (
                ProposalApplicationStatus.APPLIED
                if actions.status is ActionApplicationStatus.APPLIED
                else ProposalApplicationStatus.PARTIAL
            )
        return {
            ActionApplicationStatus.APPLIED: ProposalApplicationStatus.APPLIED,
            ActionApplicationStatus.REJECTED: ProposalApplicationStatus.REJECTED,
            ActionApplicationStatus.FAILED: ProposalApplicationStatus.FAILED,
            ActionApplicationStatus.PARTIAL: ProposalApplicationStatus.PARTIAL,
        }.get(actions.status, ProposalApplicationStatus.FAILED)

    @staticmethod
    def _action_result(
        index: int,
        call_id: str,
        tool_name: str | None,
        status: ToolResultStatus | None,
        effect: ToolEffect | None,
        reason: str | None,
        *,
        result: ToolResult | None = None,
        attempted: bool = False,
    ) -> ActionProposalApplication:
        return ActionProposalApplication(
            index, call_id, tool_name, status, effect, reason, result, attempted
        )

    @staticmethod
    def _application_id(outcome: CognitionOutcome) -> str:
        material = f"{outcome.scope_id}\x1f{outcome.episode_id}".encode()
        return f"application:{sha256(material).hexdigest()[:32]}"

    @staticmethod
    def _call_id(application_id: str, index: int) -> str:
        digest = sha256(f"{application_id}\x1f{index}".encode()).hexdigest()[:32]
        return f"mind-1d:{digest}"

    @staticmethod
    def _generation_id(application_id: str) -> str:
        return f"mind-1d:{sha256(application_id.encode('utf-8')).hexdigest()[:32]}"

    @staticmethod
    def _invalid_input_result() -> ProposalApplicationResult:
        state = StateApplication(StateApplicationStatus.NOT_REQUESTED, None, 0, 0)
        actions = ActionApplication(ActionApplicationStatus.NOT_REQUESTED)
        return ProposalApplicationResult(
            "application:invalid",
            "invalid",
            ProposalApplicationStatus.INELIGIBLE,
            state,
            actions,
            "invalid_outcome",
            TemporalApplication(TemporalApplicationStatus.NOT_REQUESTED),
        )


def _require_text(value: str, name: str) -> None:
    if not value.strip() or len(value.encode("utf-8")) > 128:
        raise ValueError(f"{name} must be non-empty bounded text")


__all__ = [
    "ActionApplication",
    "ActionApplicationStatus",
    "ActionProposalApplication",
    "MAX_APPLICATION_FENCES",
    "MindStateProvenance",
    "ProposalApplicationCoordinator",
    "ProposalApplicationResult",
    "ProposalApplicationStatus",
    "StateApplication",
    "StateApplicationStatus",
    "StateProposalApplication",
    "StateProvenanceResolver",
    "TemporalApplication",
    "TemporalApplicationStatus",
    "TemporalProposalApplication",
]
