"""Provider-neutral contracts at the persistent-agent boundary."""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Protocol, cast

from lilavel_contracts import JsonValue, ToolCall, ToolResult, ToolSpec

from .mind import MAX_INTENTION_TEXT_BYTES, MindStateSnapshot

__all__ = [
    "ActionExecutor",
    "ActionProposal",
    "ActionProposalKind",
    "CognitionCandidate",
    "CognitionDecision",
    "CognitionContext",
    "CognitionEngine",
    "CognitionEpisode",
    "CognitionEpisodeStatus",
    "CognitionGate",
    "CognitionOutcome",
    "CognitionTrigger",
    "DirectMessageCognitionGate",
    "DirectMessageWakePolicy",
    "EnvironmentAdapter",
    "EventRouter",
    "EventSource",
    "EventSubmitter",
    "EventTrust",
    "JsonValue",
    "NeverWakePolicy",
    "NO_COGNITION",
    "MAX_COGNITION_TRIGGER_OBSERVATIONS",
    "MAX_COGNITION_PROPOSALS",
    "MAX_ACTION_PROPOSAL_CONTENT_BYTES",
    "MAX_COGNITION_REASON_BYTES",
    "Observation",
    "ObservationReceipt",
    "ObservationReceiptStatus",
    "ObservationWindow",
    "RuntimePresence",
    "StateProposal",
    "StateProposalKind",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "WakeDecision",
    "WakePolicy",
    "WorldEvent",
]


class EventTrust(StrEnum):
    """Trust assigned by Lilavel, never inferred from an adapter payload."""

    UNTRUSTED = "untrusted"
    TRUSTED = "trusted"


@dataclass(frozen=True, slots=True)
class EventSource:
    """Provider-neutral origin of an external world event."""

    environment: str
    subject: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.environment, "environment")
        if self.subject is not None:
            _require_text(self.subject, "subject")


@dataclass(frozen=True, slots=True, init=False)
class WorldEvent:
    """An immutable external event envelope; admission does not imply memory."""

    event_id: str
    source: EventSource
    kind: str
    payload: Mapping[str, JsonValue]
    trust: EventTrust = EventTrust.UNTRUSTED

    def __init__(
        self,
        event_id: str,
        source: EventSource,
        kind: str,
        payload: Mapping[str, object] | None = None,
        trust: EventTrust = EventTrust.UNTRUSTED,
    ) -> None:
        _require_text(event_id, "event_id")
        _require_text(kind, "kind")
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload", _freeze_mapping(payload or {}, "payload"))
        object.__setattr__(self, "trust", trust)


class ObservationReceiptStatus(StrEnum):
    """The bounded runtime admission outcome."""

    ADMITTED = "admitted"


@dataclass(frozen=True, slots=True)
class Observation:
    """A runtime-admitted, bounded view of one provider-neutral world event."""

    observation_id: str
    sequence: int
    event: WorldEvent

    def __post_init__(self) -> None:
        _require_text(self.observation_id, "observation_id")
        if isinstance(self.sequence, bool) or self.sequence <= 0:
            raise ValueError("sequence must be a positive integer")
        if type(self.event) is not WorldEvent:
            raise TypeError("event must be a WorldEvent")

    @property
    def world_event(self) -> WorldEvent:
        """Name the wrapped external event explicitly at the semantic boundary."""

        return self.event


@dataclass(frozen=True, slots=True)
class ObservationReceipt:
    """Proof that the runtime accepted one event into its observation window."""

    observation_id: str
    event_id: str
    sequence: int
    status: ObservationReceiptStatus = ObservationReceiptStatus.ADMITTED

    def __post_init__(self) -> None:
        _require_text(self.observation_id, "observation_id")
        _require_text(self.event_id, "event_id")
        if isinstance(self.sequence, bool) or self.sequence <= 0:
            raise ValueError("sequence must be a positive integer")


MAX_COGNITION_TRIGGER_OBSERVATIONS = 8
MAX_COGNITION_PROPOSALS = 8
MAX_ACTION_PROPOSAL_CONTENT_BYTES = 4_096
MAX_COGNITION_REASON_BYTES = 128


class CognitionDecision(StrEnum):
    """The explicit negative cognition outcome."""

    NO_COGNITION = "no_cognition"


NO_COGNITION = CognitionDecision.NO_COGNITION


@dataclass(frozen=True, slots=True)
class CognitionTrigger:
    """A bounded runtime decision to start one cognition episode.

    The trigger contains only provider-neutral IDs of already admitted
    observations. It does not copy external payloads or authorize a response
    or action.
    """

    observation_ids: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if type(self.observation_ids) is not tuple:
            raise TypeError("observation_ids must be a tuple")
        if not self.observation_ids:
            raise ValueError("cognition trigger must reference an observation")
        if len(self.observation_ids) > MAX_COGNITION_TRIGGER_OBSERVATIONS:
            raise ValueError("cognition trigger observation bound exceeded")
        if len(set(self.observation_ids)) != len(self.observation_ids):
            raise ValueError("cognition trigger observation IDs must be unique")
        for observation_id in self.observation_ids:
            _require_text(observation_id, "observation_id")
        _require_bounded_text(self.reason, "reason", MAX_COGNITION_REASON_BYTES)

    @property
    def trigger_id(self) -> str:
        """Return a stable evidence-derived identity without adding authority."""

        material = "\x1f".join((*self.observation_ids, self.reason)).encode("utf-8")
        return f"trigger:{sha256(material).hexdigest()[:32]}"


@dataclass(frozen=True, slots=True)
class CognitionContext:
    """The immutable, explicitly bounded input snapshot for one episode."""

    episode_id: str
    scope_id: str
    trigger: CognitionTrigger
    observations: tuple[Observation, ...]
    mind_state: MindStateSnapshot

    def __post_init__(self) -> None:
        _require_text(self.episode_id, "episode_id")
        _require_text(self.scope_id, "scope_id")
        if type(self.trigger) is not CognitionTrigger:
            raise TypeError("trigger must be a CognitionTrigger")
        if type(self.observations) is not tuple:
            raise TypeError("observations must be a tuple")
        if len(self.observations) != len(self.trigger.observation_ids):
            raise ValueError("context observation count must match the trigger")
        if tuple(item.observation_id for item in self.observations) != self.trigger.observation_ids:
            raise ValueError("context observations must match trigger evidence order")
        if not all(type(item) is Observation for item in self.observations):
            raise TypeError("observations must contain only Observation values")
        if type(self.mind_state) is not MindStateSnapshot:
            raise TypeError("mind_state must be a MindStateSnapshot")

    @property
    def trigger_id(self) -> str:
        return self.trigger.trigger_id

    @property
    def mind_state_version(self) -> int:
        return self.mind_state.version


@dataclass(frozen=True, slots=True)
class CognitionEpisode:
    """One serialized cognition invocation and its frozen context."""

    episode_id: str
    context: CognitionContext

    def __post_init__(self) -> None:
        _require_text(self.episode_id, "episode_id")
        if type(self.context) is not CognitionContext:
            raise TypeError("context must be a CognitionContext")
        if self.context.episode_id != self.episode_id:
            raise ValueError("episode and context IDs must match")

    @property
    def scope_id(self) -> str:
        return self.context.scope_id

    @property
    def trigger(self) -> CognitionTrigger:
        return self.context.trigger


class CognitionEpisodeStatus(StrEnum):
    """Terminal state of a cognition episode; only COMPLETED yields an outcome."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class StateProposalKind(StrEnum):
    """The deliberately small state proposal vocabulary available in MIND-1C."""

    CREATE_INTENTION = "create_intention"


@dataclass(frozen=True, slots=True)
class StateProposal:
    """An inert proposal for a runtime-owned intention operation."""

    kind: StateProposalKind
    text: str

    def __post_init__(self) -> None:
        if type(self.kind) is not StateProposalKind:
            raise TypeError("kind must be a StateProposalKind")
        _require_bounded_text(self.text, "text", MAX_INTENTION_TEXT_BYTES)
        object.__setattr__(self, "text", self.text.strip())


class ActionProposalKind(StrEnum):
    """The inert action intent vocabulary; destination and authority are absent."""

    SPEAK = "speak"
    STAY_SILENT = "stay_silent"


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """An inert action intent with no destination, permission, or executor handle."""

    kind: ActionProposalKind
    content: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not ActionProposalKind:
            raise TypeError("kind must be an ActionProposalKind")
        if self.kind is ActionProposalKind.SPEAK:
            if not isinstance(self.content, str):
                raise TypeError("speak action proposals require text content")
            _require_bounded_text(self.content, "content", MAX_ACTION_PROPOSAL_CONTENT_BYTES)
            object.__setattr__(self, "content", self.content.strip())
        elif self.content is not None:
            raise ValueError("stay-silent action proposals cannot carry content")


@dataclass(frozen=True, slots=True)
class CognitionCandidate:
    """Structured engine output awaiting runtime validation and episode binding."""

    state_proposals: tuple[StateProposal, ...] = ()
    action_proposals: tuple[ActionProposal, ...] = ()

    def __post_init__(self) -> None:
        _validate_proposals(self.state_proposals, StateProposal, "state_proposals")
        _validate_proposals(self.action_proposals, ActionProposal, "action_proposals")


@dataclass(frozen=True, slots=True)
class CognitionOutcome:
    """A successful, inert cognition result; MIND-1C never applies its proposals."""

    episode_id: str
    trigger_id: str
    state_proposals: tuple[StateProposal, ...] = ()
    action_proposals: tuple[ActionProposal, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.episode_id, "episode_id")
        _require_text(self.trigger_id, "trigger_id")
        _validate_proposals(self.state_proposals, StateProposal, "state_proposals")
        _validate_proposals(self.action_proposals, ActionProposal, "action_proposals")

    @property
    def is_quiet(self) -> bool:
        return not self.state_proposals and not self.action_proposals


class CognitionEngine(Protocol):
    """Effect-free provider/model seam; the runner validates its untrusted result."""

    async def run(self, episode: CognitionEpisode) -> object: ...


class CognitionGate(Protocol):
    """Decide whether an explicit batch of admitted observations merits cognition."""

    def decide(
        self, observations: Sequence[Observation]
    ) -> CognitionDecision | CognitionTrigger: ...


class DirectMessageCognitionGate:
    """Small deterministic policy preserving the existing direct-message route.

    Only the runtime event kind already emitted by the DM adapter is eligible.
    An ordered batch is coalesced into one bounded trigger without timers,
    scheduler state, model calls, or payload interpretation.
    """

    def __init__(self, *, max_observations: int = MAX_COGNITION_TRIGGER_OBSERVATIONS) -> None:
        if (
            isinstance(max_observations, bool)
            or not 0 < max_observations <= MAX_COGNITION_TRIGGER_OBSERVATIONS
        ):
            raise ValueError("max_observations must be between 1 and the cognition trigger bound")
        self._max_observations = max_observations

    def decide(self, observations: Sequence[Observation]) -> CognitionDecision | CognitionTrigger:
        for observation in observations:
            if type(observation) is not Observation:
                raise TypeError("cognition gates accept only Observations")

        eligible_ids: list[str] = []
        seen_ids: set[str] = set()
        for observation in observations:
            if observation.observation_id in seen_ids:
                continue
            seen_ids.add(observation.observation_id)
            if observation.event.kind == "direct_message":
                eligible_ids.append(observation.observation_id)
                if len(eligible_ids) == self._max_observations:
                    break
        if not eligible_ids:
            return NO_COGNITION
        return CognitionTrigger(tuple(eligible_ids), reason="explicit_direct_message")


class ObservationWindow:
    """A finite, in-memory window of recently admitted observations.

    The oldest observation is evicted when capacity is exceeded. This is a
    transient runtime working set, not canonical history, memory, or durable
    persistence.
    """

    def __init__(self, capacity: int) -> None:
        if isinstance(capacity, bool) or capacity <= 0:
            raise ValueError("observation window capacity must be positive")
        self._capacity = capacity
        self._observations: OrderedDict[str, Observation] = OrderedDict()

    @property
    def capacity(self) -> int:
        return self._capacity

    def admit(self, observation: Observation) -> None:
        if type(observation) is not Observation:
            raise TypeError("observation must be an Observation")
        if observation.observation_id in self._observations:
            raise ValueError(f"duplicate observation_id: {observation.observation_id}")
        self._observations[observation.observation_id] = observation
        if len(self._observations) > self._capacity:
            self._observations.popitem(last=False)

    def get(self, observation_id: str) -> Observation | None:
        return self._observations.get(observation_id)

    def snapshot(self) -> tuple[Observation, ...]:
        return tuple(self._observations.values())

    def __len__(self) -> int:
        return len(self._observations)


type EventSubmitter = Callable[[WorldEvent], Awaitable[ObservationReceipt]]
type ActionExecutor = Callable[[ToolCall], Awaitable[ToolResult]]


class EnvironmentAdapter(Protocol):
    """A runtime-owned observation source and environment action executor.

    ``run`` is a long-lived coroutine. Returning before cancellation is an
    adapter failure; the runtime owns and cancels the task during shutdown.
    """

    @property
    def environment_id(self) -> str: ...

    async def run(self, submit: EventSubmitter) -> None: ...

    async def execute(self, call: ToolCall) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class WakeDecision:
    """A deferred wake-policy outcome; MIND-1A does not produce one."""

    wake: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.reason is not None:
            _require_text(self.reason, "reason")


class WakePolicy(Protocol):
    """Deferred observation-to-cognition policy, not used during admission."""

    async def decide(self, observation: Observation) -> WakeDecision: ...


class EventRouter(Protocol):
    """Run an explicit response step for an admitted observation."""

    async def route(self, observation: Observation, execute: ActionExecutor) -> None: ...

    async def close(self) -> None: ...


class RuntimePresence(Protocol):
    """Optional runtime-owned local presence component."""

    async def start(self) -> None: ...

    async def submit_user(self, text: str) -> str: ...

    async def wait(self) -> None: ...

    async def stop(self) -> None: ...


class NeverWakePolicy:
    """Safe default for a kernel with no autonomous behavior."""

    async def decide(self, observation: Observation) -> WakeDecision:
        del observation
        return WakeDecision(wake=False)


class DirectMessageWakePolicy:
    """Legacy classifier retained as a future-policy seam, not runtime wiring."""

    async def decide(self, observation: Observation) -> WakeDecision:
        if observation.event.kind == "direct_message":
            return WakeDecision(wake=True, reason="explicit_direct_message")
        return WakeDecision(wake=False, reason="not_direct_message")


def _require_text(value: str, name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _require_bounded_text(value: str, name: str, max_bytes: int) -> None:
    if type(value) is not str:
        raise TypeError(f"{name} must be text")
    _require_text(value, name)
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} exceeds its bound")


def _validate_proposals(
    proposals: tuple[object, ...], expected_type: type[object], name: str
) -> None:
    if type(proposals) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    if len(proposals) > MAX_COGNITION_PROPOSALS:
        raise ValueError(f"{name} bound exceeded")
    if not all(type(proposal) is expected_type for proposal in proposals):
        raise TypeError(f"{name} must contain only {expected_type.__name__} values")


def _freeze_mapping(value: Mapping[str, object], name: str) -> Mapping[str, JsonValue]:
    frozen: dict[str, JsonValue] = {}
    for key, item in value.items():
        frozen[key] = _freeze_json(item, f"{name}.{key}")
    return MappingProxyType(frozen)


def _freeze_json(value: object, name: str) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        if not all(isinstance(key, str) for key in mapping):
            raise TypeError(f"{name} keys must be strings")
        return _freeze_mapping(cast(Mapping[str, object], mapping), name)
    if isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        return tuple(_freeze_json(item, name) for item in sequence)
    raise TypeError(f"{name} must contain only JSON-compatible values")
