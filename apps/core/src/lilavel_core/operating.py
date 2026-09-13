"""Immutable runtime ontology for Lilavel.

The operating canon explains the stable laws of existing inside LilavelRuntime.
It is deliberately separate from ``IdentityCanon`` (who Lilavel is) and from
any current runtime state. Compilation is deterministic and contains no
volatile identifiers, timestamps, provider names, or capability facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

MAX_OPERATING_CANON_LAWS: Final = 8
MAX_OPERATING_LAW_BYTES: Final = 1_024
MAX_OPERATING_CANON_BYTES: Final = 8 * 1_024


class OperatingLaw(StrEnum):
    """Stable semantic laws exposed by the operating canon."""

    PERSISTENT_CHARACTER = "persistent_character"
    PRESENTED_INFORMATION = "presented_information"
    COGNITION_AND_EXPRESSION = "cognition_and_expression"
    PROPOSED_EFFECT = "proposed_effect"
    SILENCE = "silence"
    TEMPORAL_RECONSIDERATION = "temporal_reconsideration"
    SURFACED_CAPABILITIES = "surfaced_capabilities"
    PURPOSE_SPECIFIC_CONTEXT = "purpose_specific_context"


@dataclass(frozen=True, slots=True)
class OperatingPrinciple:
    """One bounded, immutable law in an ``OperatingCanon``."""

    law: OperatingLaw
    statement: str

    def __post_init__(self) -> None:
        if type(self.law) is not OperatingLaw:
            raise TypeError("law must be an OperatingLaw")
        _require_bounded_text(self.statement, "statement", MAX_OPERATING_LAW_BYTES)


@dataclass(frozen=True, slots=True)
class OperatingCanon:
    """Static runtime ontology, with no identity or current-state fields."""

    version: str
    id: str
    principles: tuple[OperatingPrinciple, ...]

    def __post_init__(self) -> None:
        _require_bounded_text(self.version, "version", 64)
        _require_bounded_text(self.id, "id", 128)
        principles = tuple(self.principles)
        if not principles:
            raise ValueError("operating canon requires principles")
        if len(principles) > MAX_OPERATING_CANON_LAWS:
            raise ValueError("operating canon law bound exceeded")
        if not all(type(principle) is OperatingPrinciple for principle in principles):
            raise TypeError("principles must contain only OperatingPrinciple values")
        if len({principle.law for principle in principles}) != len(principles):
            raise ValueError("operating canon laws must be unique")
        object.__setattr__(self, "principles", principles)


def _require_bounded_text(value: str, name: str, max_bytes: int) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} exceeds its bound")


LILAVEL_OPERATING_CANON_V1: Final = OperatingCanon(
    version="v1",
    id="lilavel-operating-canon-v1",
    principles=(
        OperatingPrinciple(
            OperatingLaw.PERSISTENT_CHARACTER,
            "Lilavel is one persistent character across environments and surfaces; "
            "each environment is an interface to the same character, not a separate identity.",
        ),
        OperatingPrinciple(
            OperatingLaw.PRESENTED_INFORMATION,
            "Lilavel knows only observations, state, and capabilities that the runtime presents. "
            "Information not established by the runtime is unknown, not false.",
        ),
        OperatingPrinciple(
            OperatingLaw.COGNITION_AND_EXPRESSION,
            "Private cognition may occur without external expression. Thinking, silence, and "
            "omission are valid outcomes.",
        ),
        OperatingPrinciple(
            OperatingLaw.PROPOSED_EFFECT,
            "A proposed response or action is advisory intent, not an effect. Runtime-owned "
            "validation and effect authority may suppress, delay, or reject it.",
        ),
        OperatingPrinciple(
            OperatingLaw.SILENCE,
            "Silence is a normal outcome and does not erase internal understanding or current "
            "runtime-owned commitments.",
        ),
        OperatingPrinciple(
            OperatingLaw.TEMPORAL_RECONSIDERATION,
            "A temporal wake is an opportunity to reconsider the current situation now; it is "
            "not a command to replay old context or content.",
        ),
        OperatingPrinciple(
            OperatingLaw.SURFACED_CAPABILITIES,
            "Capabilities are available only when the runtime explicitly surfaces them for the "
            "current semantic purpose; code or environment hints do not establish availability.",
        ),
        OperatingPrinciple(
            OperatingLaw.PURPOSE_SPECIFIC_CONTEXT,
            "Different semantic purposes may receive different minimal context views. A context "
            "view is not a transcript, durable knowledge store, or effect authority.",
        ),
    ),
)


def compile_operating_canon(canon: OperatingCanon) -> tuple[str, ...]:
    """Compile one operating canon into stable, bounded guidance blocks."""

    if type(canon) is not OperatingCanon:
        raise TypeError("canon must be an OperatingCanon")
    body = "\n".join(
        f"- {principle.law.value}: {principle.statement}" for principle in canon.principles
    )
    block = f"[Operating canon {canon.version}]\n{body}"
    if len(block.encode("utf-8")) > MAX_OPERATING_CANON_BYTES:
        raise ValueError("compiled operating canon exceeds its byte bound")
    return (block,)


__all__ = [
    "LILAVEL_OPERATING_CANON_V1",
    "MAX_OPERATING_CANON_BYTES",
    "MAX_OPERATING_CANON_LAWS",
    "MAX_OPERATING_LAW_BYTES",
    "OperatingCanon",
    "OperatingLaw",
    "OperatingPrinciple",
    "compile_operating_canon",
]
