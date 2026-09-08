"""Focused contract tests for the thin deterministic JSONL runner."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

PACKAGE_ROOT = Path(__file__).parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
RUNNER_PATH = PACKAGE_ROOT / "scripts" / "scenario_runner.py"


@pytest.fixture(scope="module")
def runner_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "lilavel_scenario_runner_test_module", RUNNER_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError("scenario runner module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_cli(scenario: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("LILAVEL_DISCORD_HTTP_DIAGNOSTICS", None)
    return subprocess.run(
        [sys.executable, str(RUNNER_PATH), scenario],
        cwd=PACKAGE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def records_from(process: subprocess.CompletedProcess[str]) -> list[dict[str, object]]:
    assert process.stdout
    records: list[dict[str, object]] = []
    for line in process.stdout.splitlines():
        assert line.strip()
        value = json.loads(line)
        assert isinstance(value, dict)
        records.append(cast(dict[str, object], value))
    return records


def evidence(records: list[dict[str, object]], surface: str) -> list[dict[str, object]]:
    return [
        record
        for record in records
        if record.get("record_type") == "evidence" and record.get("surface") == surface
    ]


def result_record(records: list[dict[str, object]]) -> dict[str, object]:
    return next(record for record in records if record.get("record_type") == "result")


def test_success_command_is_pure_jsonl_and_has_typed_result() -> None:
    process = run_cli("success")

    assert process.returncode == 0
    records = records_from(process)
    assert records[0]["record_type"] == "scenario"
    assert result_record(records)["result"] == "completed"
    assert process.stderr == ""
    assert "runner success input" not in process.stdout


def test_success_joins_semantic_run_to_physical_generation() -> None:
    records = records_from(run_cli("success"))
    core = evidence(records, "core")
    physical = evidence(records, "physical")
    bound = next(record for record in core if record["kind"] == "generation_bound")
    semantic_terminal = next(record for record in core if record["kind"] == "generation_terminal")
    physical_created = next(record for record in physical if record["kind"] == "generation_created")
    physical_terminal = next(
        record for record in physical if record["kind"] == "generation_terminal"
    )

    assert bound["run_id"]
    assert bound["generation_id"] == physical_created["generation_id"]
    assert bound["generation_id"] == physical_terminal["generation_id"]
    assert bound["epoch"] == physical_terminal["epoch"]
    assert semantic_terminal["result"] == "completed"
    assert physical_terminal["result"] == "completed"
    joined_run = cast(list[dict[str, object]], result_record(records)["runs"])[0]
    assert joined_run["run_id"] == bound["run_id"]
    assert joined_run["generation_id"] == physical_terminal["generation_id"]
    assert joined_run["assistant_committed"] is True


def test_supersession_proves_discard_and_commit_boundaries() -> None:
    process = run_cli("supersession")
    assert process.returncode == 0
    records = records_from(process)
    core = evidence(records, "core")
    accepted = [record for record in core if record["kind"] == "turn_accepted"]
    assert len(accepted) == 2
    old_run_id = accepted[0]["run_id"]
    new_run_id = accepted[1]["run_id"]
    old_records = [record for record in core if record.get("run_id") == old_run_id]
    new_records = [record for record in core if record.get("run_id") == new_run_id]

    discarded = next(record for record in old_records if record["kind"] == "delta_discarded")
    assert discarded["reason"] == "not_current"
    assert discarded["discarded_count"] == 1
    assert any(
        record["kind"] == "cancel_requested" and record["reason"] == "superseded"
        for record in old_records
    )
    assert any(
        record["kind"] == "run_terminal" and record["result"] == "superseded"
        for record in old_records
    )
    assert not any(record["kind"] == "assistant_commit" for record in old_records)
    assert any(
        record["kind"] == "assistant_commit" and record["result"] == "committed"
        for record in new_records
    )
    assert result_record(records)["late_delta_discarded_count"] == 1
    assert "STALE_LATE_DELTA" not in process.stdout


@pytest.mark.parametrize("scenario", ["sidecar-failure", "malformed-frame"])
def test_failure_scenarios_are_safe_typed_observations(scenario: str) -> None:
    process = run_cli(scenario)

    assert process.returncode == 0
    records = records_from(process)
    result = result_record(records)
    assert result["status"] == "failed"
    assert result["expected_failure"] is True
    physical_terminal = next(
        record
        for record in evidence(records, "physical")
        if record["kind"] == "generation_terminal"
    )
    assert physical_terminal["result"] == "failed"
    assert physical_terminal["failure_code"] in {"sidecar_crashed", "protocol_error"}
    assert "Traceback" not in process.stdout
    assert "Traceback" not in process.stderr


def test_discord_interruption_correlates_run_to_presentation_chain() -> None:
    process = run_cli("discord-interruption")
    assert process.returncode == 0
    records = records_from(process)
    core = evidence(records, "core")
    discord = evidence(records, "discord")
    run_ids = [record["run_id"] for record in core if record["kind"] == "turn_accepted"]
    assert len(run_ids) == 2
    for run_id in run_ids:
        correlated = [record for record in discord if record.get("core_run_id") == run_id]
        kinds = {record["kind"] for record in correlated}
        assert {
            "presenter_flush_start",
            "operation_start",
            "http_request_start",
            "http_response",
            "presentation_outcome",
        } <= kinds
        flush_ids = {
            record["flush_id"] for record in correlated if record["kind"] == "presenter_flush_start"
        }
        operation_ids = {
            record["operation_id"] for record in correlated if record["kind"] == "operation_start"
        }
        request_ids = {
            record["request_id"] for record in correlated if record["kind"] == "http_request_start"
        }
        assert all(
            record.get("presenter_flush_id") in flush_ids
            for record in correlated
            if record["kind"]
            in {"operation_start", "operation_return", "http_request_start", "http_response"}
        )
        assert all(
            record.get("operation_id") in operation_ids
            for record in correlated
            if record["kind"] in {"http_request_start", "http_response"}
        )
        assert all(
            record.get("request_id") in request_ids
            for record in correlated
            if record["kind"] == "http_response"
        )
    presentation = cast(list[dict[str, object]], result_record(records)["presentation"])
    assert [item["status"] for item in presentation] == ["interrupted", "completed"]
    assert presentation[0]["reason"] == "superseded"
    assert presentation[0]["core_run_id"] == run_ids[0]
    assert presentation[1]["core_run_id"] == run_ids[1]
    assert "runner-discord-channel" not in process.stdout
    assert "runner-old-message" not in process.stdout
    assert "https://" not in process.stdout
    assert '"at_s"' not in process.stdout
    assert "request_url" not in process.stdout
    assert "response_body" not in process.stdout


def structural_projection(records: list[dict[str, object]]) -> list[tuple[object, ...]]:
    run_ids = [
        record["run_id"]
        for record in evidence(records, "core")
        if record["kind"] == "turn_accepted"
    ]
    run_ordinals = {run_id: index for index, run_id in enumerate(run_ids, start=1)}
    generation_ids = [
        record["generation_id"]
        for record in evidence(records, "physical")
        if record["kind"] == "generation_created"
    ]
    generation_ids.extend(
        record["generation_id"]
        for record in evidence(records, "core")
        if record["kind"] == "generation_bound" and record["generation_id"] not in generation_ids
    )
    generation_ordinals = {
        generation_id: index for index, generation_id in enumerate(generation_ids, start=1)
    }
    projection: list[tuple[object, ...]] = []
    for record in records:
        if record["record_type"] == "evidence":
            projection.append(
                (
                    record["surface"],
                    record["kind"],
                    record["source_sequence"],
                    run_ordinals.get(
                        record.get("run_id"), run_ordinals.get(record.get("core_run_id"))
                    ),
                    generation_ordinals.get(record.get("generation_id")),
                    record.get("epoch"),
                    record.get("protocol_event"),
                    record.get("result"),
                    record.get("reason"),
                    record.get("failure_code"),
                    record.get("discarded_count"),
                    record.get("event_count"),
                )
            )
        elif record["record_type"] == "result":
            runs = cast(list[dict[str, object]], record["runs"])
            projection.append(
                (
                    "result",
                    record["result"],
                    tuple(
                        (
                            run_ordinals[run["run_id"]],
                            generation_ordinals.get(run.get("generation_id")),
                            run["semantic_result"],
                            run["physical_result"],
                            run["assistant_committed"],
                            run["discarded_delta_count"],
                        )
                        for run in runs
                    ),
                )
            )
    return projection


def test_repeated_success_has_stable_structural_result() -> None:
    first = records_from(run_cli("success"))
    second = records_from(run_cli("success"))

    assert structural_projection(first) == structural_projection(second)


def test_sensitive_input_is_not_emitted_and_stdout_remains_jsonl(runner_module: ModuleType) -> None:
    sentinel = "RUNNER_SENSITIVE_SENTINEL_DO_NOT_EMIT"
    capture = runner_module.run_scenario_capture("success", input_text=sentinel)
    output = runner_module.render_jsonl(capture)

    assert sentinel not in output
    assert all(isinstance(json.loads(line), dict) for line in output.splitlines())


def test_unknown_scenario_fails_without_traceback() -> None:
    process = run_cli("unknown-scenario")

    assert process.returncode != 0
    assert process.stdout == ""
    assert "invalid choice" in process.stderr
    assert "Traceback" not in process.stderr


def test_runner_does_not_change_repository_state(runner_module: ModuleType) -> None:
    before = subprocess.run(
        ["git", "status", "--short"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    runner_module.run_scenario_capture("success")
    after = subprocess.run(
        ["git", "status", "--short"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert after == before
