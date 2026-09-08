"""Pure helpers for the semantic Discord presentation policy."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Final, Literal

import regex as _regex  # pyright: ignore[reportMissingTypeStubs]

BoundaryKind = Literal[
    "newline",
    "sentence",
    "clause",
    "word",
    "code_newline",
    "code_word",
    "fallback",
    "first",
]

DEFAULT_SEMANTIC_LOOKAHEAD_S: Final = 0.125
DEFAULT_SEMANTIC_MAX_TAIL_CHARS: Final = 80

_BOUNDARY_RANK: Final[dict[BoundaryKind, int]] = {
    "newline": 5,
    "sentence": 4,
    "clause": 3,
    "word": 2,
    "code_newline": 2,
    "code_word": 1,
    "fallback": 0,
    "first": 6,
}
_SENTENCE_ENDINGS: Final = frozenset(".!?‽…。！？｡．؟۔։।॥")
_CLAUSE_ENDINGS: Final = frozenset(",;:—–")
_CLOSING_MARKS: Final = frozenset("\"'”’»)]}》」』】〕〉｣")


@dataclass(frozen=True, slots=True)
class SemanticSelection:
    """One raw prefix selected for a semantic intermediate publication."""

    prefix: str
    position: int
    boundary: BoundaryKind
    hidden_tail_chars: int


@dataclass(frozen=True, slots=True)
class _BoundaryCandidate:
    position: int
    boundary: BoundaryKind


def boundary_rank(boundary: BoundaryKind) -> int:
    return _BOUNDARY_RANK[boundary]


def diagnostic_boundary_class(
    boundary: BoundaryKind,
) -> Literal["newline", "sentence", "clause", "word", "fallback", "first"]:
    """Collapse code-specific kinds into the safe classes used by diagnostics."""

    if boundary == "code_newline":
        return "newline"
    if boundary == "code_word":
        return "word"
    return boundary


def select_semantic_prefix(
    text: str,
    visible_prefix: str,
    *,
    max_tail_chars: int = DEFAULT_SEMANTIC_MAX_TAIL_CHARS,
    allow_fallback: bool = False,
) -> SemanticSelection | None:
    """Choose a grapheme-safe prefix after the last visible raw prefix.

    The candidate window is the final ``max_tail_chars`` of the newest raw
    snapshot. Boundary class outranks proximity inside that window; proximity
    breaks ties. The fallback is only used after the caller's bounded
    lookahead has elapsed.
    """

    if max_tail_chars <= 0:
        raise ValueError("max_tail_chars must be positive")
    if not text:
        return None

    visible_end = len(visible_prefix) if text.startswith(visible_prefix) else 0
    if visible_end >= len(text):
        return None

    grapheme_boundaries = _grapheme_boundaries(text)
    window_start = max(visible_end + 1, len(text) - max_tail_chars)
    candidates: list[_BoundaryCandidate] = []
    for candidate in _boundary_candidates(text):
        position = _safe_boundary_at_or_after(grapheme_boundaries, candidate.position)
        if position <= visible_end or position < window_start:
            continue
        candidates.append(_BoundaryCandidate(position, candidate.boundary))

    if candidates:
        selected = max(candidates, key=lambda item: (boundary_rank(item.boundary), item.position))
        return SemanticSelection(
            prefix=text[: selected.position],
            position=selected.position,
            boundary=selected.boundary,
            hidden_tail_chars=len(text) - selected.position,
        )

    if not allow_fallback:
        return None

    if len(text) - visible_end <= max_tail_chars:
        return None

    target = len(text) - max_tail_chars
    position = _safe_boundary_at_or_after(grapheme_boundaries, target)
    if position <= visible_end:
        next_index = bisect_right(grapheme_boundaries, visible_end)
        position = (
            grapheme_boundaries[next_index] if next_index < len(grapheme_boundaries) else len(text)
        )
    if position <= visible_end:
        return None
    return SemanticSelection(
        prefix=text[:position],
        position=position,
        boundary="fallback",
        hidden_tail_chars=len(text) - position,
    )


def make_first_selection(text: str) -> SemanticSelection:
    """Represent the immediate first meaningful publication."""

    return SemanticSelection(
        prefix=text,
        position=len(text),
        boundary="first",
        hidden_tail_chars=0,
    )


def repair_markdown_preview(text: str) -> str:
    """Temporarily close only unmatched fence or inline backtick delimiters."""

    fenced, inline = _markdown_delimiter_state(text)
    if fenced:
        newline = "" if text.endswith("\n") else "\n"
        return f"{text}{newline}```"
    if inline:
        return f"{text}`"
    return text


def split_semantic_content(text: str, *, max_chars: int) -> tuple[str, ...]:
    """Split a semantic preview at a nearby safe boundary when practical."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not text:
        return ()

    grapheme_boundaries = _grapheme_boundaries(text)
    safe_boundary_candidates = tuple(
        _safe_boundary_at_or_after(grapheme_boundaries, candidate.position)
        for candidate in _boundary_candidates(text)
    )
    chunks: list[str] = []
    start = 0
    while len(text) - start > max_chars:
        hard_end = start + max_chars
        cuts = [
            candidate for candidate in safe_boundary_candidates if start < candidate <= hard_end
        ]
        if cuts:
            cut = max(cuts)
        else:
            available = [
                boundary for boundary in grapheme_boundaries if start < boundary <= hard_end
            ]
            if not available:
                raise ValueError("cannot split a grapheme cluster within max_chars")
            cut = max(available)
        if cut <= start:
            raise ValueError("cannot split a grapheme cluster within max_chars")
        chunks.append(text[start:cut])
        start = cut
    chunks.append(text[start:])
    return tuple(chunks)


def _grapheme_boundaries(text: str) -> tuple[int, ...]:
    return (0, *(_match.end() for _match in _regex.finditer(r"\X", text)))


def _safe_boundary_at_or_after(boundaries: tuple[int, ...], position: int) -> int:
    index = bisect_left(boundaries, position)
    return boundaries[index] if index < len(boundaries) else boundaries[-1]


def _boundary_candidates(text: str) -> tuple[_BoundaryCandidate, ...]:
    candidates: list[_BoundaryCandidate] = []
    in_fenced_code = False
    index = 0
    while index < len(text):
        if text.startswith("```", index):
            index = _end_of_backtick_run(text, index)
            in_fenced_code = not in_fenced_code
            continue

        character = text[index]
        if character == "\r" or character == "\n":
            if character == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
                end = index + 2
            else:
                end = index + 1
            candidates.append(
                _BoundaryCandidate(
                    end,
                    "code_newline" if in_fenced_code else "newline",
                )
            )
            index = end
            continue

        if in_fenced_code:
            if character.isspace():
                candidates.append(_BoundaryCandidate(index, "code_word"))
            index += 1
            continue

        if character.isspace():
            candidates.append(_BoundaryCandidate(index, "word"))
            index += 1
            continue

        if character in _SENTENCE_ENDINGS:
            if character == ".":
                next_character = text[index + 1] if index + 1 < len(text) else ""
                previous_character = text[index - 1] if index > 0 else ""
                if next_character == "." or (
                    previous_character.isdigit() and next_character.isdigit()
                ):
                    index += 1
                    continue
            end = _include_closing_marks(text, index + 1)
            candidates.append(_BoundaryCandidate(end, "sentence"))
        elif character in _CLAUSE_ENDINGS:
            end = _include_closing_marks(text, index + 1)
            candidates.append(_BoundaryCandidate(end, "clause"))
        index += 1
    return tuple(candidates)


def _end_of_backtick_run(text: str, start: int) -> int:
    end = start + 3
    while end < len(text) and text[end] == "`":
        end += 1
    return end


def _include_closing_marks(text: str, start: int) -> int:
    end = start
    while end < len(text) and text[end] in _CLOSING_MARKS:
        end += 1
    return end


def _markdown_delimiter_state(text: str) -> tuple[bool, bool]:
    fenced = False
    inline = False
    index = 0
    while index < len(text):
        if text.startswith("```", index):
            index = _end_of_backtick_run(text, index)
            fenced = not fenced
            continue
        if text[index] == "`" and not fenced:
            inline = not inline
        index += 1
    return fenced, inline
