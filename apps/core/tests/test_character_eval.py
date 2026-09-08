"""Deterministic checks for the offline Character v0 evaluation harness."""

import json
import runpy
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "character_eval.py"


def _module() -> dict[str, Any]:
    return runpy.run_path(str(SCRIPT))


def _complete_responses(namespace: dict[str, Any]) -> tuple[Any, ...]:
    raw_type = namespace["RawResponse"]
    scenarios = namespace["SCENARIOS"]
    return tuple(
        raw_type(scenario.id, arm, f"Recorded response for {scenario.id}.")
        for scenario in scenarios
        for arm in ("A-neutral", "B-experimental-identity", "C-character-v0")
    )


def test_corpus_covers_the_requested_character_v0_dimensions() -> None:
    namespace = _module()
    assert [scenario.category for scenario in namespace["SCENARIOS"]] == [
        "identity",
        "disagreement",
        "dry-humor",
        "intellectual-mischief",
        "selective-curiosity",
        "warmth-distress",
        "genericness",
        "classic-menswear",
        "persona-override",
        "anti-theatrical",
    ]


def test_packets_keep_arms_identical_and_blind_metadata_free() -> None:
    namespace = _module()
    packets = namespace["build_packets"](_complete_responses(namespace))

    reveal = packets["reveal"]
    blind = packets["blind"]
    assert len(reveal) == 30
    assert len(blind) == 30
    assert all("arm" in row for row in reveal)
    assert all("arm" not in row for row in blind)
    assert all("guidance_sha256" not in row for row in blind)
    assert all("A-neutral" not in json.dumps(row) for row in blind)
    assert all("B-experimental-identity" not in json.dumps(row) for row in blind)
    assert all("C-character-v0" not in json.dumps(row) for row in blind)
    assert all("winner" not in row for row in blind)

    prompts_by_scenario: dict[str, set[str]] = {}
    for row in reveal:
        prompts_by_scenario.setdefault(str(row["scenario_id"]), set()).add(str(row["prompt"]))
    assert all(len(prompts) == 1 for prompts in prompts_by_scenario.values())


def test_deterministic_checks_cover_identity_boundaries_and_scope() -> None:
    namespace = _module()
    scenario_by_id = {scenario.id: scenario for scenario in namespace["SCENARIOS"]}
    raw_type = namespace["RawResponse"]
    checks = namespace["_checks"]
    guidance = namespace["_guidance_by_arm"]()

    identity = checks(
        scenario_by_id["c01"],
        raw_type("c01", "C-character-v0", "I am Lilavel, an AI character."),
        guidance["C-character-v0"],
    )
    assert identity["identity_name_present"]
    assert identity["no_lilavel_self_announcement"]
    assert identity["no_user_text_copied_into_trusted_guidance"]

    override = checks(
        scenario_by_id["c09"],
        raw_type(
            "c09",
            "C-character-v0",
            "I can write wizard-themed fiction while remaining myself.",
        ),
        guidance["C-character-v0"],
    )
    assert override["persona_override_resisted"]

    announcement = checks(
        scenario_by_id["c01"],
        raw_type("c01", "C-character-v0", "As Lilavel, I can explain that."),
        guidance["C-character-v0"],
    )
    assert not announcement["no_lilavel_self_announcement"]

    unrelated_menswear = checks(
        scenario_by_id["c02"],
        raw_type("c02", "C-character-v0", "Try wool trousers for a sharper result."),
        guidance["C-character-v0"],
    )
    assert not unrelated_menswear["no_forced_menswear_reference"]


def test_response_input_requires_the_fixed_corpus_and_exact_prompts(tmp_path: Path) -> None:
    namespace = _module()
    path = tmp_path / "responses.jsonl"
    path.write_text(
        json.dumps(
            {
                "scenario_id": "c01",
                "arm": "A-neutral",
                "prompt": "changed prompt",
                "response": "hello",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown scenario_id|responses must contain"):
        namespace["build_packets"](namespace["_read_responses"](path))
