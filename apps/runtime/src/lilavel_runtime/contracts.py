"""Provider-neutral contracts at the persistent-agent boundary."""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, cast

from lilavel_contracts import JsonValue, ToolCall, ToolResult, ToolSpec

__all__ = [
    "ActionExecutor",
    "CognitionDecision",
    "CognitionGate",
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
    "Observation",
    "ObservationReceipt",
    "ObservationReceiptStatus",
    "ObservationWindow",
    "RuntimePresence",
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
        _require_text(self.reason, "reason")


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
