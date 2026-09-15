"""Human-authored PROACTIVE-V0-R3 appraisal evaluation cases.

The cases are review fixtures for provider-backed appraisal.  They do not
classify text deterministically and are intentionally separate from the
runtime's bounded semantic contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .mind import IntentionKind


class ProactiveR3Expected(StrEnum):
    """Expected appraisal disposition for one bounded review case."""

    NO_CHANGE = "no_change"
    INITIATIVE = "initiative"
    DEFERRED_COMMITMENT = "deferred_commitment"


@dataclass(frozen=True, slots=True)
class ProactiveR3AppraisalCase:
    """One human-authored completed-turn appraisal case."""

    case_id: str
    user_turn: str
    assistant_turn: str
    expected: ProactiveR3Expected

    def __post_init__(self) -> None:
        for name in ("case_id", "user_turn", "assistant_turn"):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
        if type(self.expected) is not ProactiveR3Expected:
            raise TypeError("expected must be a ProactiveR3Expected")

    @property
    def expected_kind(self) -> IntentionKind | None:
        if self.expected is ProactiveR3Expected.INITIATIVE:
            return IntentionKind.INITIATIVE
        if self.expected is ProactiveR3Expected.DEFERRED_COMMITMENT:
            return IntentionKind.DEFERRED_COMMITMENT
        return None


PROACTIVE_V0_R3_APPRAISAL_CASES: tuple[ProactiveR3AppraisalCase, ...] = (
    ProactiveR3AppraisalCase(
        "explicit-reminder-after-delay",
        "If I say nothing for 20 seconds, send me a separate reminder.",
        "Understood. If you stay quiet for 20 seconds, I will send the reminder.",
        ProactiveR3Expected.DEFERRED_COMMITMENT,
    ),
    ProactiveR3AppraisalCase(
        "tell-me-later-if-x",
        "Tell me later if the export has finished.",
        "I can check and tell you when the next opportunity is due.",
        ProactiveR3Expected.DEFERRED_COMMITMENT,
    ),
    ProactiveR3AppraisalCase(
        "reminder-already-delivered",
        "Remind me to stand up.",
        "Stand up and stretch now.",
        ProactiveR3Expected.NO_CHANGE,
    ),
    ProactiveR3AppraisalCase(
        "vague-later-continuation",
        "We should continue this later.",
        "Agreed, we can continue whenever you return.",
        ProactiveR3Expected.NO_CHANGE,
    ),
    ProactiveR3AppraisalCase(
        "model-curiosity",
        "The migration is complete.",
        "That is useful context; I wonder what the next benchmark will show.",
        ProactiveR3Expected.INITIATIVE,
    ),
    ProactiveR3AppraisalCase(
        "user-cancels-commitment",
        "Do not remind me about the export anymore; I will handle it.",
        "Understood; I will not send that reminder.",
        ProactiveR3Expected.NO_CHANGE,
    ),
)


__all__ = [
    "PROACTIVE_V0_R3_APPRAISAL_CASES",
    "ProactiveR3AppraisalCase",
    "ProactiveR3Expected",
]
