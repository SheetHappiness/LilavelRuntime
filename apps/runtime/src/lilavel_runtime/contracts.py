"""Provider-neutral contracts at the persistent-agent boundary."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, cast

from lilavel_contracts import JsonValue, ToolCall, ToolResult, ToolSpec

__all__ = [
    "ActionExecutor",
    "DirectMessageWakePolicy",
    "EnvironmentAdapter",
    "EventRouter",
    "EventSource",
    "EventSubmitter",
    "EventTrust",
    "JsonValue",
    "NeverWakePolicy",
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
    """Provider-neutral origin of an observation."""

    environment: str
    subject: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.environment, "environment")
        if self.subject is not None:
            _require_text(self.subject, "subject")


@dataclass(frozen=True, slots=True, init=False)
class WorldEvent:
    """An immutable observation envelope; ingestion does not imply memory."""

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


type EventSubmitter = Callable[[WorldEvent], Awaitable[None]]
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
    """A wake-policy outcome. Phase 2 records it but performs no model work."""

    wake: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.reason is not None:
            _require_text(self.reason, "reason")


class WakePolicy(Protocol):
    async def decide(self, event: WorldEvent) -> WakeDecision: ...


class EventRouter(Protocol):
    """Route a positively classified event without owning its destination adapter."""

    async def route(self, event: WorldEvent, execute: ActionExecutor) -> None: ...

    async def close(self) -> None: ...


class RuntimePresence(Protocol):
    """Optional runtime-owned local presence component."""

    async def start(self) -> None: ...

    async def submit_user(self, text: str) -> str: ...

    async def wait(self) -> None: ...

    async def stop(self) -> None: ...


class NeverWakePolicy:
    """Safe default for a kernel with no autonomous behavior."""

    async def decide(self, event: WorldEvent) -> WakeDecision:
        del event
        return WakeDecision(wake=False)


class DirectMessageWakePolicy:
    """Deterministically wake only for an explicit one-to-one message."""

    async def decide(self, event: WorldEvent) -> WakeDecision:
        if event.kind == "direct_message":
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
