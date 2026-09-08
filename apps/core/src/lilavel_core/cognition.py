"""Provider-neutral identity fixtures and deterministic guidance compilation."""

from collections.abc import Iterable
from dataclasses import dataclass
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
    question_policy: Literal["avoid", "required", "invite"] = "avoid"
    initiative: Level = "normal"


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
