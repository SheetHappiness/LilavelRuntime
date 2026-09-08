"""Focused checks for the canonical Lilavel character canon."""

from lilavel_core import LILAVEL_CHARACTER_V0, compile_guidance


def test_lilavel_character_v0_compiles_deterministically() -> None:
    first = compile_guidance(LILAVEL_CHARACTER_V0)
    second = compile_guidance(LILAVEL_CHARACTER_V0)

    assert first == second
    assert [block.splitlines()[0] for block in first] == [
        "[Self concept]",
        "[Core values]",
        "[Temperament]",
        "[Interests]",
        "[Behavioral anchors]",
        "[Voice]",
        "[Anti-patterns]",
        "[Representative dialogue examples]",
    ]
    rendered = "\n".join(first).casefold()
    assert "workingstate" not in rendered
    assert "responsedisposition" not in rendered
    assert "provider" in rendered


def test_lilavel_character_v0_contains_the_requested_identity_contract() -> None:
    canon = LILAVEL_CHARACTER_V0

    assert canon.version == "v0"
    assert canon.id == "lilavel-character-v0"
    assert canon.self_concept is not None
    self_concept = canon.self_concept.statement.casefold()
    assert "lilavel" in self_concept
    assert "application" in self_concept
    assert "ai" in self_concept
    assert "not a human" in self_concept
    assert "fabricated human biography" in self_concept
    assert "underlying model" in self_concept
    assert "provider" in self_concept

    assert len(canon.core_values) >= 4
    assert len(canon.temperament) >= 6
    assert len(canon.behavioral_anchors) >= 7
    assert len(canon.representative_dialogue_examples) == 7

    all_text = "\n".join(
        (
            *canon.core_values,
            *(trait.description for trait in canon.temperament),
            *canon.interests,
            *(anchor.guidance for anchor in canon.behavioral_anchors),
            *canon.voice,
            *canon.anti_patterns,
            *(example.user for example in canon.representative_dialogue_examples),
            *(example.assistant for example in canon.representative_dialogue_examples),
        )
    ).casefold()
    assert "contradiction" in all_text
    assert "i don't know" in all_text
    assert "change" in all_text
    assert "menswear" in all_text
    assert "tailor" in all_text
    assert "sarcasm" in all_text
    assert "catchphrase" in all_text
    assert "emotionally cold" in all_text
