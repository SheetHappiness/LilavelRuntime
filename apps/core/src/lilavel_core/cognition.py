"""Provider-neutral identity fixtures and deterministic guidance compilation."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from .sidecar_protocol import MAX_GUIDANCE_BLOCKS, MAX_GUIDANCE_BYTES, ProtocolError

Aim = Literal[
    "answer",
    "acknowledge",
    "clarify",
    "challenge",
    "tease",
    "comfort",
    "disagree",
    "explore",
    "close",
]
Level = Literal["low", "normal", "high"]
Stance = Literal["neutral", "curious", "skeptical", "playful", "supportive"]
QuestionPolicy = Literal["avoid", "required", "invite"]


class AttentionDecision(StrEnum):
    """Whether an observation merits no, lightweight, or full cognition."""

    DROP = "drop"
    NOTE = "note"
    THINK = "think"


class InterventionDecision(StrEnum):
    """Whether cognition should remain silent, answer, or interrupt."""

    NONE = "none"
    RESPOND = "respond"
    INTERJECT = "interject"


class CognitionReasonCode(StrEnum):
    """The bounded, provider-neutral reasons a policy may expose."""

    DIRECT_ADDRESS = "direct_address"
    EXPLICIT_QUESTION = "explicit_question"
    CONTRADICTION = "contradiction"
    EVIDENCE_UPDATE = "evidence_update"
    AMBIGUITY_MATERIAL = "ambiguity_material"
    LOW_INFORMATION_GAP = "low_information_gap"
    SOCIAL_FOLLOWUP = "social_followup"
    CRITICAL_EVENT = "critical_event"
    HIGH_RELEVANCE = "high_relevance"
    HIGH_NOVELTY = "high_novelty"
    INTEREST_AFFINITY = "interest_affinity"
    INTERRUPTION_COST_HIGH = "interruption_cost_high"
    REPETITION = "repetition"
    NO_NEW_VALUE = "no_new_value"
    VULNERABILITY = "vulnerability"
    PLAYFUL_CONTEXT = "playful_context"
    AMBIENT_CONTEXT = "ambient_context"
    NOT_ADDRESSED = "not_addressed"
    LOW_RELEVANCE = "low_relevance"


MAX_COGNITION_REASON_CODES = 8


@dataclass(frozen=True, slots=True)
class SelfConcept:
    """A static, concise statement of how a character describes itself."""

    statement: str


@dataclass(frozen=True, slots=True)
class TemperamentTrait:
    """A named temperament trait and its bounded explanatory note."""

    name: str
    description: str


@dataclass(frozen=True, slots=True)
class BehavioralAnchor:
    """A static situation-to-guidance pair for consistent behavior."""

    context: str
    guidance: str


@dataclass(frozen=True, slots=True)
class DialogueExample:
    """A representative static user/assistant exchange."""

    user: str
    assistant: str


@dataclass(frozen=True, slots=True)
class IdentityCanon:
    """Immutable static identity content owned by Core.

    ``traits``, ``principles``, and ``boundaries`` are retained as compatibility
    fields for the existing cognition experiment. New identity definitions
    should use the structured v0 fields below. None of these fields carry user,
    runtime, relationship, memory, transport, or provider metadata.
    """

    version: str
    id: str
    traits: tuple[str, ...] = ()
    principles: tuple[str, ...] = ()
    boundaries: tuple[str, ...] = ()
    anti_patterns: tuple[str, ...] = ()
    self_concept: SelfConcept | None = None
    core_values: tuple[str, ...] = ()
    temperament: tuple[TemperamentTrait, ...] = ()
    interests: tuple[str, ...] = ()
    behavioral_anchors: tuple[BehavioralAnchor, ...] = ()
    voice: tuple[str, ...] = ()
    representative_dialogue_examples: tuple[DialogueExample, ...] = ()

    def __post_init__(self) -> None:
        """Normalize collection inputs so the frozen value stays immutable."""

        for field_name in (
            "traits",
            "principles",
            "boundaries",
            "anti_patterns",
            "core_values",
            "temperament",
            "interests",
            "behavioral_anchors",
            "voice",
            "representative_dialogue_examples",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class WorkingState:
    focus: str
    engagement: Level = "normal"
    stance: Stance = "neutral"


@dataclass(frozen=True, slots=True)
class ResponseDisposition:
    aim: Aim = "answer"
    directness: Level = "normal"
    desired_length: Level = "normal"
    humor_allowed: bool = False
    question_policy: QuestionPolicy = "avoid"
    initiative: Level = "normal"


@dataclass(frozen=True, slots=True)
class CognitionPolicyDecision:
    """A fail-closed, provider-neutral cognition policy result.

    Attention, intervention, and response disposition are deliberately
    separate decisions.  The policy may think without speaking, but speaking
    always requires a disposition.  This contract carries no identity fields
    and cannot mutate ``IdentityCanon``.
    """

    attention: AttentionDecision
    intervention: InterventionDecision
    working_state: WorkingState | None = None
    response_disposition: ResponseDisposition | None = None
    reason_codes: tuple[CognitionReasonCode, ...] = ()

    def __post_init__(self) -> None:
        if type(self.attention) is not AttentionDecision:
            raise TypeError("attention must be an AttentionDecision")
        if type(self.intervention) is not InterventionDecision:
            raise TypeError("intervention must be an InterventionDecision")
        if type(self.reason_codes) is not tuple:
            raise TypeError("reason_codes must be a tuple")
        if not self.reason_codes:
            raise ValueError("cognition policy decisions require a reason code")
        if len(self.reason_codes) > MAX_COGNITION_REASON_CODES:
            raise ValueError("cognition policy reason-code bound exceeded")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("cognition policy reason codes must be unique")
        if not all(type(code) is CognitionReasonCode for code in self.reason_codes):
            raise TypeError("reason_codes must contain only CognitionReasonCode values")
        if self.working_state is not None and type(self.working_state) is not WorkingState:
            raise TypeError("working_state must be a WorkingState")
        if (
            self.response_disposition is not None
            and type(self.response_disposition) is not ResponseDisposition
        ):
            raise TypeError("response_disposition must be a ResponseDisposition")

        if self.attention is not AttentionDecision.THINK:
            if self.intervention is not InterventionDecision.NONE:
                raise ValueError("DROP/NOTE decisions must have NONE intervention")
            if self.working_state is not None or self.response_disposition is not None:
                raise ValueError("DROP/NOTE decisions cannot carry dynamic behavior")
            return

        if self.intervention is InterventionDecision.NONE:
            if self.response_disposition is not None:
                raise ValueError("NONE intervention cannot carry a response disposition")
        elif self.response_disposition is None:
            raise ValueError("speaking interventions require a response disposition")


__all__ = [
    "Aim",
    "AttentionDecision",
    "BehavioralAnchor",
    "CognitionPolicyDecision",
    "CognitionReasonCode",
    "DialogueExample",
    "IdentityCanon",
    "InterventionDecision",
    "Level",
    "MAX_COGNITION_REASON_CODES",
    "QuestionPolicy",
    "ResponseDisposition",
    "SelfConcept",
    "Stance",
    "TemperamentTrait",
    "WorkingState",
    "compile_guidance",
]


def compile_guidance(
    identity: IdentityCanon,
    *,
    state: WorkingState | None = None,
    disposition: ResponseDisposition | None = None,
) -> tuple[str, ...]:
    blocks = _compile_identity_blocks(identity)
    if state is not None or disposition is not None:
        s = state or WorkingState("")
        d = disposition or ResponseDisposition()
        blocks.append(
            "[Current behavior]\n"
            + "\n".join(
                (
                    f"focus: {s.focus}",
                    f"engagement: {s.engagement}",
                    f"stance: {s.stance}",
                    f"aim: {d.aim}",
                    f"directness: {d.directness}",
                    f"desired density: {d.desired_length}",
                    f"humor: {'allowed' if d.humor_allowed else 'disallowed'}",
                    f"question policy: {d.question_policy}",
                    f"initiative: {d.initiative}",
                )
            )
        )
    result = tuple(blocks)
    if len(result) > MAX_GUIDANCE_BLOCKS or any(not block.strip() for block in result):
        raise ProtocolError("invalid_field")
    if len(set(result)) != len(result):
        raise ProtocolError("invalid_field")
    if sum(len(block.encode("utf-8")) for block in result) > MAX_GUIDANCE_BYTES:
        raise ProtocolError("invalid_field")
    return result


def _compile_identity_blocks(identity: IdentityCanon) -> list[str]:
    """Serialize identity fields in a fixed order without dynamic metadata."""

    if not _has_structured_identity(identity):
        return [
            "[Identity]\n" + "\n".join((*identity.traits, *identity.principles)),
            "[Boundaries]\n" + "\n".join((*identity.boundaries, *identity.anti_patterns)),
        ]

    blocks: list[str] = []
    if identity.self_concept is not None:
        blocks.append(
            "[Self concept]\n" + _format_pairs((("statement", identity.self_concept.statement),))
        )

    core_values = (*identity.core_values, *identity.principles)
    if core_values:
        blocks.append("[Core values]\n" + _format_items(core_values))

    temperament = tuple((trait.name, trait.description) for trait in identity.temperament) + tuple(
        (trait, "") for trait in identity.traits
    )
    if temperament:
        blocks.append("[Temperament]\n" + _format_pairs(temperament))

    if identity.interests:
        blocks.append("[Interests]\n" + _format_items(identity.interests))

    anchors = tuple(
        (anchor.context, anchor.guidance) for anchor in identity.behavioral_anchors
    ) + tuple(("legacy boundary", boundary) for boundary in identity.boundaries)
    if anchors:
        blocks.append("[Behavioral anchors]\n" + _format_pairs(anchors))

    if identity.voice:
        blocks.append("[Voice]\n" + _format_items(identity.voice))

    if identity.anti_patterns:
        blocks.append("[Anti-patterns]\n" + _format_items(identity.anti_patterns))

    if identity.representative_dialogue_examples:
        examples = tuple(
            pair
            for index, example in enumerate(identity.representative_dialogue_examples, start=1)
            for pair in (
                (f"example {index} user", example.user),
                (f"example {index} assistant", example.assistant),
            )
        )
        blocks.append("[Representative dialogue examples]\n" + _format_pairs(examples))

    return blocks


def _has_structured_identity(identity: IdentityCanon) -> bool:
    return any(
        (
            identity.self_concept is not None,
            identity.core_values,
            identity.temperament,
            identity.interests,
            identity.behavioral_anchors,
            identity.voice,
            identity.representative_dialogue_examples,
        )
    )


def _format_items(items: Iterable[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _format_pairs(pairs: Iterable[tuple[str, str]]) -> str:
    return "\n".join(f"{label}: {value}" for label, value in pairs)
