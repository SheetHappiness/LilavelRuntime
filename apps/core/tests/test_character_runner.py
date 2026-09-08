"""Deterministic contract checks for the model-backed Character v0 recorder."""

import runpy
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from lilavel_core import (
    ContextMessage,
    GenerationAccepted,
    GenerationCompleted,
    GenerationEvent,
    ModelRequest,
    TextDelta,
)

SCRIPT_DIR = Path(__file__).parents[1] / "scripts"
SCRIPT = SCRIPT_DIR / "character_runner.py"


def _module() -> dict[str, Any]:
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        return runpy.run_path(str(SCRIPT))
    finally:
        sys.path.pop(0)


class _FakeGeneration:
    generation_id = "fake-generation"
    epoch = 1

    def events(self) -> Iterator[GenerationEvent]:
        yield GenerationAccepted(self.generation_id, self.epoch)
        yield TextDelta(self.generation_id, self.epoch, "recorded response")
        yield GenerationCompleted(self.generation_id, self.epoch)


class _FakeRuntime:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.started = False
        self.shutdown_called = False

    def start(self) -> None:
        self.started = True

    def generate(self, request: ModelRequest) -> _FakeGeneration:
        self.requests.append(request)
        return _FakeGeneration()

    def cancel(self, generation_id: str) -> bool:
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True


def test_one_run_uses_core_history_and_separate_trusted_guidance() -> None:
    namespace = _module()
    runtime = _FakeRuntime()
    scenario = namespace["SCENARIOS"][0]
    guidance = ("[trusted fixture]",)

    response = namespace["run_one"](runtime, scenario, "C-character-v0", guidance)

    assert response.status == "completed"
    assert response.response == "recorded response"
    assert response.prompt == scenario.prompt
    assert runtime.requests[0].messages == (ContextMessage("user", scenario.prompt),)
    assert runtime.requests[0].system_prompt == guidance


def test_record_corpus_writes_exact_thirty_character_eval_records(tmp_path: Path) -> None:
    namespace = _module()
    runtime = _FakeRuntime()
    output = tmp_path / "recorded.jsonl"

    assert namespace["record_corpus"](output, runtime) == 0

    rows = [line for line in output.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 30
    assert runtime.started
    assert runtime.shutdown_called
    assert [request.messages for request in runtime.requests] == [
        (ContextMessage("user", scenario.prompt),)
        for scenario in namespace["SCENARIOS"]
        for _arm in ("A-neutral", "B-experimental-identity", "C-character-v0")
    ]

    evaluator = runpy.run_path(str(SCRIPT_DIR / "character_eval.py"))
    parsed = evaluator["_read_responses"](output)
    packets = evaluator["build_packets"](parsed)
    assert len(parsed) == 30
    assert len(packets["raw"]) == 30
    assert len(packets["blind"]) == 30
