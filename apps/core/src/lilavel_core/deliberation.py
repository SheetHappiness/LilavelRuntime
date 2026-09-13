"""Provider-neutral contracts for bounded USER deliberation routing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .sidecar_protocol import ContextMessage

MAX_DELIBERATION_CONTEXT_MESSAGES = 4


class DeliberationDecision(StrEnum):
    """Whether the existing disposition planner should run for one turn."""

    FAST = "fast"
    PLAN = "plan"


@dataclass(frozen=True, slots=True)
class DeliberationContext:
    """The bounded context supplied to one synchronous routing policy."""

    current_user_turn: ContextMessage
    recent_canonical_context: tuple[ContextMessage, ...] = ()

    def __post_init__(self) -> None:
        if type(self.current_user_turn) is not ContextMessage:
            raise TypeError("current_user_turn must be a ContextMessage")
        if self.current_user_turn.role != "user":
            raise ValueError("current_user_turn must have the user role")
        context = tuple(self.recent_canonical_context)
        if len(context) > MAX_DELIBERATION_CONTEXT_MESSAGES:
            raise ValueError("deliberation context exceeds its message bound")
        if not all(type(message) is ContextMessage for message in context):
            raise TypeError("recent_canonical_context must contain ContextMessage values")
        if context and context[-1] != self.current_user_turn:
            raise ValueError("recent_canonical_context must end with current_user_turn")
        object.__setattr__(self, "recent_canonical_context", context)


class DeliberationPolicy(Protocol):
    """Synchronous, non-generative routing policy supplied by the caller."""

    def decide(self, context: DeliberationContext) -> DeliberationDecision:
        """Return only FAST or PLAN for the supplied bounded context."""
        ...


__all__ = [
    "MAX_DELIBERATION_CONTEXT_MESSAGES",
    "DeliberationContext",
    "DeliberationDecision",
    "DeliberationPolicy",
]
