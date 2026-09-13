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


class TurnBehaviorSource(StrEnum):
    """The bounded authority that produced one run's behavior."""

    DEFAULT = "default"
    PLANNER = "planner"


class TurnBehaviorResolutionOutcome(StrEnum):
    """The safe, bounded result of resolving one run's behavior."""

    NOT_INVOKED = "not_invoked"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class TurnBehavior:
    """Immutable behavior attached to exactly one prepared conversation run.

    This contract contains only validated response behavior. It has no model
    text, provider handle, memory authority, tool authority, or action data.
    """

    working_state: WorkingState
    response_disposition: ResponseDisposition
    reason_codes: tuple[CognitionReasonCode, ...] = ()
    source: TurnBehaviorSource = TurnBehaviorSource.DEFAULT

    def __post_init__(self) -> None:
        if type(self.working_state) is not WorkingState:
            raise TypeError("working_state must be a WorkingState")
        if type(self.response_disposition) is not ResponseDisposition:
            raise TypeError("response_disposition must be a ResponseDisposition")
        if type(self.reason_codes) is not tuple:
            raise TypeError("reason_codes must be a tuple")
        if len(self.reason_codes) > MAX_COGNITION_REASON_CODES:
            raise ValueError("turn behavior reason-code bound exceeded")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("turn behavior reason codes must be unique")
        if not all(type(code) is CognitionReasonCode for code in self.reason_codes):
            raise TypeError("turn behavior reason_codes must contain CognitionReasonCode")
        if type(self.source) is not TurnBehaviorSource:
            raise TypeError("source must be a TurnBehaviorSource")

    def materially_differs_from(self, other: "TurnBehavior") -> bool:
        """Compare only behavior fields that can change response guidance."""

        if type(other) is not TurnBehavior:
            raise TypeError("other must be a TurnBehavior")
        return (
            self.working_state != other.working_state
            or self.response_disposition != other.response_disposition
        )


@dataclass(frozen=True, slots=True)
class TurnBehaviorResolution:
    """Bounded resolution evidence produced before a run starts generation."""

    behavior: TurnBehavior
    planner_invoked: bool
    outcome: TurnBehaviorResolutionOutcome
    fallback_reason: str | None = None
    planner_duration_ms: int | None = None
    materially_differs_from_default: bool = False

    def __post_init__(self) -> None:
        if type(self.behavior) is not TurnBehavior:
            raise TypeError("behavior must be a TurnBehavior")
        if type(self.planner_invoked) is not bool:
            raise TypeError("planner_invoked must be a bool")
        if type(self.outcome) is not TurnBehaviorResolutionOutcome:
            raise TypeError("outcome must be a TurnBehaviorResolutionOutcome")
        if self.fallback_reason is not None:
            if type(self.fallback_reason) is not str or not self.fallback_reason.strip():
                raise ValueError("fallback_reason must be non-empty when supplied")
            if len(self.fallback_reason.encode("utf-8")) > 128:
                raise ValueError("fallback_reason is too long")
        if self.planner_duration_ms is not None and (
            isinstance(self.planner_duration_ms, bool) or self.planner_duration_ms < 0
        ):
            raise ValueError("planner_duration_ms must be non-negative")
        if type(self.materially_differs_from_default) is not bool:
            raise TypeError("materially_differs_from_default must be a bool")
        if (
            not self.planner_invoked
            and self.outcome is not TurnBehaviorResolutionOutcome.NOT_INVOKED
        ):
            raise ValueError("a non-invoked planner must use NOT_INVOKED outcome")
        if self.outcome is TurnBehaviorResolutionOutcome.ACCEPTED:
            if self.behavior.source is not TurnBehaviorSource.PLANNER:
                raise ValueError("accepted planner behavior must be planner-sourced")
            if self.fallback_reason is not None:
                raise ValueError("accepted planner behavior cannot have fallback reason")
        elif self.outcome in {
            TurnBehaviorResolutionOutcome.NOT_INVOKED,
            TurnBehaviorResolutionOutcome.REJECTED,
            TurnBehaviorResolutionOutcome.FALLBACK,
        }:
            if self.behavior.source is not TurnBehaviorSource.DEFAULT:
                raise ValueError("non-accepted behavior must be default-sourced")
        if (
            self.outcome is TurnBehaviorResolutionOutcome.NOT_INVOKED
            and self.fallback_reason is not None
        ):
            raise ValueError("not-invoked resolution cannot have fallback reason")


def default_turn_behavior() -> TurnBehavior:
    """Return the deterministic behavior used by the existing USER path."""

    return TurnBehavior(
        working_state=WorkingState("respond to the current user turn"),
        response_disposition=ResponseDisposition(),
    )


@dataclass(frozen=True, slots=True)
class DispositionCandidate:
    """A bounded model result containing only response-behavior selections.

    This is deliberately narrower than ``CognitionPolicyDecision``.  A model
    can choose how a direct user turn should be approached, but it cannot
    choose attention, intervention, guidance text, or any runtime action.
    """

    aim: Aim
    stance: Stance
    engagement: Level
    directness: Level
    desired_length: Level
    humor_allowed: bool
    question_policy: QuestionPolicy
    initiative: Level
    reason_codes: tuple[CognitionReasonCode, ...]

    def __post_init__(self) -> None:
        allowed_values = {
            "aim": {
                "answer",
                "acknowledge",
                "clarify",
                "challenge",
                "tease",
                "comfort",
                "disagree",
                "explore",
                "close",
            },
            "stance": {"neutral", "curious", "skeptical", "playful", "supportive"},
            "engagement": {"low", "normal", "high"},
            "directness": {"low", "normal", "high"},
            "desired_length": {"low", "normal", "high"},
            "question_policy": {"avoid", "required", "invite"},
            "initiative": {"low", "normal", "high"},
        }
        for field_name, values in allowed_values.items():
            value = getattr(self, field_name)
            if type(value) is not str or value not in values:
                raise ValueError(f"disposition candidate {field_name} is invalid")
        if type(self.humor_allowed) is not bool:
            raise TypeError("disposition candidate humor_allowed must be a bool")
        if type(self.reason_codes) is not tuple:
            raise TypeError("disposition candidate reason_codes must be a tuple")
        if not self.reason_codes:
            raise ValueError("disposition candidates require a reason code")
        if len(self.reason_codes) > MAX_COGNITION_REASON_CODES:
            raise ValueError("disposition candidate reason-code bound exceeded")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("disposition candidate reason codes must be unique")
        if not all(type(code) is CognitionReasonCode for code in self.reason_codes):
            raise TypeError("disposition candidate reason_codes must contain CognitionReasonCode")


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
    "DispositionCandidate",
    "IdentityCanon",
    "InterventionDecision",
    "Level",
    "MAX_COGNITION_REASON_CODES",
    "QuestionPolicy",
    "ResponseDisposition",
    "SelfConcept",
    "Stance",
    "TurnBehavior",
    "TurnBehaviorResolution",
    "TurnBehaviorResolutionOutcome",
    "TurnBehaviorSource",
    "TemperamentTrait",
    "WorkingState",
    "compile_guidance",
    "compile_planner_guidance",
    "default_turn_behavior",
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


def compile_planner_guidance(identity: IdentityCanon) -> tuple[str, ...]:
    """Compile the decision-relevant projection of one immutable identity.

    The planner receives the same canonical identity as normal conversation
    guidance, but not generation-oriented voice examples. Keeping this field
    selection here prevents a second hand-maintained persona definition from
    appearing in runtime code.
    """

    if type(identity) is not IdentityCanon:
        raise TypeError("identity must be an IdentityCanon")
    blocks = _compile_planner_identity_blocks(identity)
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


def _compile_planner_identity_blocks(identity: IdentityCanon) -> list[str]:
    """Serialize only canonical fields that can affect disposition."""

    if not _has_structured_identity(identity):
        return [
            "[Planner values]\n" + _format_items((*identity.traits, *identity.principles)),
            "[Planner boundaries]\n"
            + _format_items((*identity.boundaries, *identity.anti_patterns)),
        ]

    blocks: list[str] = []
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

    if identity.anti_patterns:
        blocks.append("[Anti-patterns]\n" + _format_items(identity.anti_patterns))
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
