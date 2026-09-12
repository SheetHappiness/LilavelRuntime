"""Small in-memory semantic state for the local presence runtime."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from enum import StrEnum
from threading import Lock
from uuid import uuid4

MAX_INTENTIONS = 8
MAX_RECENT_SELF_ACTIONS = 8
MAX_INTENTION_TEXT_BYTES = 512
MAX_SELF_ACTION_TEXT_BYTES = 4_096
MAX_MIND_PROJECTION_BYTES = 8 * 1_024
MAX_PROJECTED_SELF_ACTION_TEXT_BYTES = 1_024


class IntentionStatus(StrEnum):
    ACTIVE = "active"
    EXPRESSED = "expressed"


@dataclass(frozen=True, slots=True)
class MindIntention:
    """One runtime-owned bounded intention with Core message provenance."""

    intention_id: str
    text: str
    user_message_id: str
    assistant_message_id: str
    status: IntentionStatus = IntentionStatus.ACTIVE

    def __post_init__(self) -> None:
        _require_text(self.intention_id, "intention_id", 128)
        _require_text(self.text, "text", MAX_INTENTION_TEXT_BYTES)
        _require_text(self.user_message_id, "user_message_id", 128)
        _require_text(self.assistant_message_id, "assistant_message_id", 128)


@dataclass(frozen=True, slots=True)
class SelfAction:
    """One bounded, noncanonical action remembered for the next user turn."""

    action_id: str
    intention_id: str
    text: str

    def __post_init__(self) -> None:
        _require_text(self.action_id, "action_id", 128)
        _require_text(self.intention_id, "intention_id", 128)
        _require_text(self.text, "text", MAX_SELF_ACTION_TEXT_BYTES)


@dataclass(frozen=True, slots=True)
class MindProjection:
    """Immutable read-only state allowed into a normal conversation request."""

    active_intentions: tuple[MindIntention, ...] = ()
    recent_self_actions: tuple[SelfAction, ...] = ()

    def guidance_blocks(self) -> tuple[str, ...]:
        if not self.active_intentions and not self.recent_self_actions:
            return ()

        lines = [
            "[Mind projection]",
            "Runtime-owned read-only state follows. It is context, not an instruction.",
        ]
        if self.active_intentions:
            lines.append("Active intentions:")
            lines.extend(f"- {intention.text}" for intention in self.active_intentions)
        if self.recent_self_actions:
            lines.append("Recent noncanonical self-actions:")
            lines.extend(
                f"- said: {_clip_text(action.text, MAX_PROJECTED_SELF_ACTION_TEXT_BYTES)}"
                for action in self.recent_self_actions
            )
        rendered = "\n".join(lines)
        if len(rendered.encode("utf-8")) > MAX_MIND_PROJECTION_BYTES:
            return (rendered.encode("utf-8")[:MAX_MIND_PROJECTION_BYTES].decode("utf-8", "ignore"),)
        return (rendered,)


@dataclass(frozen=True, slots=True)
class MindStateSnapshot:
    """Immutable, bounded read-only state captured for one cognition episode."""

    version: int
    intentions: tuple[MindIntention, ...] = ()
    recent_self_actions: tuple[SelfAction, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or self.version < 0:
            raise ValueError("mind-state version must be a non-negative integer")
        if type(self.intentions) is not tuple:
            raise TypeError("intentions must be a tuple")
        if type(self.recent_self_actions) is not tuple:
            raise TypeError("recent_self_actions must be a tuple")
        if not all(type(item) is MindIntention for item in self.intentions):
            raise TypeError("intentions must contain only MindIntention values")
        if not all(type(item) is SelfAction for item in self.recent_self_actions):
            raise TypeError("recent_self_actions must contain only SelfAction values")

    @property
    def active_intentions(self) -> tuple[MindIntention, ...]:
        return tuple(item for item in self.intentions if item.status is IntentionStatus.ACTIVE)


class MindState:
    """Runtime-owned bounded state; no persistence or Core history is involved."""

    def __init__(
        self,
        *,
        intentions_capacity: int = MAX_INTENTIONS,
        self_actions_capacity: int = MAX_RECENT_SELF_ACTIONS,
    ) -> None:
        if intentions_capacity <= 0 or self_actions_capacity <= 0:
            raise ValueError("mind-state bounds must be positive")
        self._intentions_capacity = intentions_capacity
        self._self_actions_capacity = self_actions_capacity
        self._intentions: deque[MindIntention] = deque()
        self._self_actions: deque[SelfAction] = deque(maxlen=self_actions_capacity)
        self._version = 0
        self._lock = Lock()

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def intentions(self) -> tuple[MindIntention, ...]:
        with self._lock:
            return tuple(self._intentions)

    def self_actions(self) -> tuple[SelfAction, ...]:
        with self._lock:
            return tuple(self._self_actions)

    def active_intention(self) -> MindIntention | None:
        with self._lock:
            return next(
                (item for item in self._intentions if item.status is IntentionStatus.ACTIVE),
                None,
            )

    def projection(self) -> MindProjection:
        with self._lock:
            active = tuple(
                item for item in self._intentions if item.status is IntentionStatus.ACTIVE
            )
            return MindProjection(active, tuple(self._self_actions))

    def snapshot(self) -> MindStateSnapshot:
        """Capture the complete bounded state without exposing mutable storage."""

        with self._lock:
            return MindStateSnapshot(
                version=self._version,
                intentions=tuple(self._intentions),
                recent_self_actions=tuple(self._self_actions),
            )

    def create_intention(
        self,
        text: str,
        *,
        user_message_id: str,
        assistant_message_id: str,
    ) -> MindIntention | None:
        _require_text(text, "text", MAX_INTENTION_TEXT_BYTES)
        _require_text(user_message_id, "user_message_id", 128)
        _require_text(assistant_message_id, "assistant_message_id", 128)
        with self._lock:
            if len(self._intentions) >= self._intentions_capacity:
                evictable = next(
                    (
                        index
                        for index, item in enumerate(self._intentions)
                        if item.status is IntentionStatus.EXPRESSED
                    ),
                    None,
                )
                if evictable is None:
                    return None
                retained = list(self._intentions)
                del retained[evictable]
                self._intentions = deque(retained)
            intention = MindIntention(
                intention_id=str(uuid4()),
                text=text,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            )
            self._intentions.append(intention)
            self._version += 1
            return intention

    def mark_expressed(self, intention_id: str, text: str) -> SelfAction | None:
        _require_text(intention_id, "intention_id", 128)
        _require_text(text, "text", MAX_SELF_ACTION_TEXT_BYTES)
        with self._lock:
            for index, intention in enumerate(self._intentions):
                if intention.intention_id != intention_id:
                    continue
                if intention.status is not IntentionStatus.ACTIVE:
                    return None
                self._intentions[index] = replace(intention, status=IntentionStatus.EXPRESSED)
                action = SelfAction(str(uuid4()), intention_id, text)
                self._self_actions.append(action)
                self._version += 1
                return action
        return None


def _require_text(value: str, name: str, max_bytes: int) -> None:
    if not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} exceeds its bound")


def _clip_text(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", "ignore")


__all__ = [
    "MAX_INTENTIONS",
    "MAX_INTENTION_TEXT_BYTES",
    "MAX_MIND_PROJECTION_BYTES",
    "MAX_PROJECTED_SELF_ACTION_TEXT_BYTES",
    "MAX_RECENT_SELF_ACTIONS",
    "MAX_SELF_ACTION_TEXT_BYTES",
    "IntentionStatus",
    "MindIntention",
    "MindProjection",
    "MindStateSnapshot",
    "MindState",
    "SelfAction",
]
