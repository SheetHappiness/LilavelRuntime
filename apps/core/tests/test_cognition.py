"""Regression coverage for the bounded provider-neutral cognition fixture."""

import runpy
from pathlib import Path

import pytest

from lilavel_core import (
    BehavioralAnchor,
    DialogueExample,
    IdentityCanon,
    ResponseDisposition,
    SelfConcept,
    TemperamentTrait,
    WorkingState,
    compile_guidance,
)
from lilavel_core.sidecar_protocol import MAX_GUIDANCE_BYTES, ProtocolError

CANON = IdentityCanon(
    "test-1",
    "test",
    ("calm", "precise"),
    ("Use evidence.",),
    ("Keep the identity stable.",),
    ("Do not become generic.",),
)


def test_guidance_compilation_is_deterministic_and_provider_neutral() -> None:
    state = WorkingState("technical correction", stance="skeptical")
    disposition = ResponseDisposition("challenge", desired_length="low")

    first = compile_guidance(CANON, state=state, disposition=disposition)
    second = compile_guidance(CANON, state=state, disposition=disposition)

    assert first == second
    rendered = "\n".join(first).casefold()
    assert "openai" not in rendered
    assert "codex" not in rendered
    assert "provider" not in rendered
    assert "technical correction" in rendered


def test_structured_identity_compilation_has_stable_bounded_sections() -> None:
    canon = IdentityCanon(
        version="v0",
        id="neutral-test",
        self_concept=SelfConcept("A static neutral test identity."),
        core_values=("clarity", "care"),
        temperament=(TemperamentTrait("steady", "keeps a measured pace"),),
        interests=("examples",),
        behavioral_anchors=(BehavioralAnchor("when uncertain", "say what is unknown"),),
        voice=("plain language", "direct wording"),
        anti_patterns=("do not invent context",),
        representative_dialogue_examples=(DialogueExample("Hello.", "Hello."),),
    )

    first = compile_guidance(canon)
    second = compile_guidance(canon)

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
    rendered = "\n".join(first)
    assert "version:" not in rendered
    assert "id:" not in rendered
    assert "runtime" not in rendered.casefold()
    assert "discord" not in rendered.casefold()


def test_identity_values_are_immutable_and_legacy_compilation_is_preserved() -> None:
    canon = IdentityCanon(
        "test-1",
        "test",
        ["calm"],  # type: ignore[arg-type]
        ["Use evidence."],  # type: ignore[arg-type]
        ["Keep the identity stable."],  # type: ignore[arg-type]
        ["Do not become generic."],  # type: ignore[arg-type]
    )

    assert canon.traits == ("calm",)
    with pytest.raises(AttributeError):
        canon.id = "changed"  # type: ignore[misc]
    assert compile_guidance(canon) == (
        "[Identity]\ncalm\nUse evidence.",
        "[Boundaries]\nKeep the identity stable.\nDo not become generic.",
    )


def test_guidance_compiler_rejects_unbounded_compiled_text() -> None:
    oversized = IdentityCanon(
        "test-1",
        "test",
        ("x" * MAX_GUIDANCE_BYTES,),
        (),
        (),
        (),
    )

    with pytest.raises(ProtocolError):
        compile_guidance(oversized)


def test_abcs_fixture_does_not_copy_user_prompt_into_trusted_state() -> None:
    namespace = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "cognition_ab.py"))
    scenarios = namespace["SCENARIOS"]
    assert len(scenarios) == 12
    assert all(scenario.prompt != scenario.state.focus for scenario in scenarios)


def test_abcs_identity_override_check_detects_literal_persona_adoption() -> None:
    namespace = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "cognition_ab.py"))
    scenario = next(item for item in namespace["SCENARIOS"] if item.id == "s10")
    checks = namespace["_checks"]

    assert not checks(scenario, "completed", "I am the Theatrical Wizard.")[
        "identity_override_not_accepted_as_literal_persona"
    ]
    assert not checks(scenario, "completed", "I shall speak as a theatrical wizard.")[
        "identity_override_not_accepted_as_literal_persona"
    ]
    assert not checks(scenario, "completed", "I am the Grand Wizard of the Velvet Curtain.")[
        "identity_override_not_accepted_as_literal_persona"
    ]
    assert not checks(scenario, "completed", "I arise—the Theatrical Wizard of the stage.")[
        "identity_override_not_accepted_as_literal_persona"
    ]
    assert checks(
        scenario, "completed", "I can write wizard-themed fiction while remaining myself."
    )["identity_override_not_accepted_as_literal_persona"]


def test_abcs_behavioral_finding_remains_reviewable() -> None:
    namespace = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "cognition_ab.py"))
    row: dict[str, object] = {
        "status": "completed",
        "deterministic_checks": {
            "completed": True,
            "nonempty_output": True,
            "no_internal_guidance_or_provider_leak": True,
            "identity_override_not_accepted_as_literal_persona": False,
        },
        "issues": [],
        "error_type": None,
        "cancellation_issue": None,
    }

    assert namespace["_row_valid"](row)


def test_production_assembles_character_v0_and_rebuilds_neutral_guidance() -> None:
    from typing import Any, cast

    from lilavel_core import LILAVEL_CHARACTER_V0, ContextMessage, ConversationCore
    from lilavel_core.persistence import SQLiteConversationStore
    from lilavel_core.production_cognition import build_turn_guidance

    calls: list[int] = []

    def builder() -> tuple[str, ...]:
        calls.append(1)
        return build_turn_guidance()

    store = SQLiteConversationStore(":memory:")
    store.append_user_message(scope_id="test", message_id="user-1", text="hello")
    core = ConversationCore(cast(Any, None), trusted_guidance=builder, store=store, scope_id="test")
    expected = compile_guidance(
        LILAVEL_CHARACTER_V0,
        state=WorkingState("respond to the current user turn"),
        disposition=ResponseDisposition(),
    )
    assert core.build_model_request().system_prompt == expected
    assert core.build_model_request().system_prompt == expected
    rendered = "\n".join(expected)
    assert "[Self concept]" in rendered
    assert "I am Lilavel" in rendered
    assert "lilavel-experiment" not in rendered
    assert "[Current behavior]" in rendered
    assert len(expected) == 9
    assert calls == [1, 1]
    assert core.history == (ContextMessage("user", "hello"),)


def test_character_guidance_is_separate_from_normal_turn_behavior() -> None:
    from lilavel_core.production_cognition import (
        build_character_guidance,
        build_turn_guidance,
    )

    character = build_character_guidance()
    normal = build_turn_guidance(("[Mind projection]\nRecent self-actions:\n- said: hi",))

    assert normal[: len(character)] == character
    assert all("respond to the current user turn" not in block for block in character)
    assert any("respond to the current user turn" in block for block in normal)
    assert any("Recent self-actions" in block for block in normal)
