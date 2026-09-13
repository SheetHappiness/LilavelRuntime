"""Small, reviewable non-generative deliberation policies."""

from __future__ import annotations

import re
from dataclasses import dataclass

from lilavel_core import DeliberationContext, DeliberationDecision


@dataclass(frozen=True, slots=True)
class _RoutingSignals:
    """Deterministic posture signals; raw text never leaves this module."""

    contradiction: bool = False
    agreement_pressure: bool = False
    material_ambiguity: bool = False
    social_nuance: bool = False
    tone_ambiguity: bool = False
    delayed_context: bool = False
    contextual_choice: bool = False


class DeterministicDeliberationPolicy:
    """Route only explicit posture-risk combinations to the planner.

    These are structural multi-token cues, not identity-interest keywords or a
    generic complexity classifier.  The policy is intentionally conservative:
    implicit social nuance and delayed context outside the small canonical tail
    remain known false-negative domains.
    """

    policy_id = "deterministic_rules_v1"

    def decide(self, context: DeliberationContext) -> DeliberationDecision:
        signals = _extract_signals(context)
        if any(
            (
                signals.contradiction,
                signals.agreement_pressure,
                signals.material_ambiguity,
                signals.social_nuance,
                signals.tone_ambiguity,
                signals.delayed_context,
                signals.contextual_choice,
            )
        ):
            return DeliberationDecision.PLAN
        return DeliberationDecision.FAST


class AlwaysFastDeliberationPolicy:
    """Offline lower-cost baseline: never invoke the disposition planner."""

    policy_id = "always_fast"

    def decide(self, context: DeliberationContext) -> DeliberationDecision:
        del context
        return DeliberationDecision.FAST


class AlwaysPlanDeliberationPolicy:
    """Offline quality upper-bound baseline: always invoke the planner."""

    policy_id = "always_plan"

    def decide(self, context: DeliberationContext) -> DeliberationDecision:
        del context
        return DeliberationDecision.PLAN


_CONTRADICTION_PATTERNS = (
    re.compile(
        r"\b(?:earlier|previously|you said)\b.{0,100}\b(?:but|however|"
        r"conflicts? with|contradicts?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:conflicts? with|contradicts?)\b.{0,80}\b(?:evidence|facts?|requirement|premise)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:new evidence|updated results?)\b.{0,100}\b(?:earlier|previous|no longer holds)\b",
        re.IGNORECASE,
    ),
)
_AGREEMENT_PRESSURE_PATTERNS = (
    re.compile(
        r"\b(?:just|simply)\s+(?:say|tell)\s+(?:me\s+)?(?:that\s+)?i(?:'m| am)\s+right\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:do not|don't)\s+(?:challenge|question|push back on)\s+(?:me|this)\b",
        re.IGNORECASE,
    ),
)
_MATERIAL_AMBIGUITY_PATTERNS = (
    re.compile(
        r"\b(?:depends on|would change|changes)\b.{0,80}\b(?:recommendation|"
        r"choice|decision|answer)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:missing|unknown)\b.{0,60}\b(?:fact|detail|constraint|requirement)\b",
        re.IGNORECASE,
    ),
)
_SOCIAL_NUANCE_PATTERNS = (
    re.compile(
        r"\b(?:i(?:'m| am) not sure how to|i need help with what to say|this is hard to handle)\b",
        re.IGNORECASE,
    ),
)
_TONE_AMBIGUITY_PATTERNS = (
    re.compile(
        r"\b(?:joking|kidding)\b.{0,80}\b(?:actually|serious|seriously)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:actually|serious|seriously)\b.{0,80}\b(?:joking|kidding)\b",
        re.IGNORECASE,
    ),
)
_CONTEXTUAL_CHOICE_PATTERN = re.compile(
    r"\b(?:which|what)\b.{0,70}\b(?:would you choose|would you pick|recommend)\b",
    re.IGNORECASE,
)
_CONTEXTUAL_CHOICE_CONTEXT_PATTERN = re.compile(
    r"\b(?:given my constraints|between these options|for this situation)\b",
    re.IGNORECASE,
)
_DELAYED_CURRENT_PATTERN = re.compile(
    r"\b(?:what should i|how should i|what do i)\b.{0,30}\b(?:say|do|next)\b",
    re.IGNORECASE,
)
_DELAYED_CONTEXT_PATTERN = re.compile(
    r"\b(?:still need to decide|not settled|unresolved|you just said|deadline change)\b",
    re.IGNORECASE,
)


def _extract_signals(context: DeliberationContext) -> _RoutingSignals:
    current = context.current_user_turn.text
    recent = "\n".join(message.text for message in context.recent_canonical_context[:-1])
    combined = f"{recent}\n{current}"
    return _RoutingSignals(
        contradiction=_matches(_CONTRADICTION_PATTERNS, current),
        agreement_pressure=_matches(_AGREEMENT_PRESSURE_PATTERNS, current),
        material_ambiguity=_matches(_MATERIAL_AMBIGUITY_PATTERNS, current),
        social_nuance=_matches(_SOCIAL_NUANCE_PATTERNS, current),
        tone_ambiguity=_matches(_TONE_AMBIGUITY_PATTERNS, current),
        delayed_context=(
            bool(recent)
            and _DELAYED_CURRENT_PATTERN.search(current) is not None
            and _DELAYED_CONTEXT_PATTERN.search(combined) is not None
        ),
        contextual_choice=(
            _CONTEXTUAL_CHOICE_PATTERN.search(current) is not None
            and _CONTEXTUAL_CHOICE_CONTEXT_PATTERN.search(combined) is not None
        ),
    )


def _matches(patterns: tuple[re.Pattern[str], ...], value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in patterns)


__all__ = [
    "AlwaysFastDeliberationPolicy",
    "AlwaysPlanDeliberationPolicy",
    "DeterministicDeliberationPolicy",
]
