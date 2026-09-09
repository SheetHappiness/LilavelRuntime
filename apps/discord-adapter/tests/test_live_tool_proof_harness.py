"""Deterministic post-run evidence checks for the opt-in live proof harness."""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from queue import Queue
from types import ModuleType

from lilavel_core import (
    ConversationCore,
    GenerationAccepted,
    GenerationCompleted,
    GenerationEvent,
    ModelRequest,
    SQLiteConversationStore,
    TextDelta,
)

SCRIPT = Path(__file__).parents[1] / "scripts" / "run_live_tool_proof.py"


def _proof_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_live_tool_proof", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("live proof harness is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Generation:
    def __init__(self) -> None:
        self.generation_id = "generation-proof"
        self.epoch = 1
        self._events: Queue[GenerationEvent] = Queue()

    def emit(self, event: GenerationEvent) -> None:
        self._events.put(event)

    def events(self) -> Iterator[GenerationEvent]:
        while True:
            event = self._events.get()
            yield event
            if isinstance(event, GenerationCompleted):
                return


class _Runtime:
    def __init__(self) -> None:
        self.generation = _Generation()

    def generate(self, request: ModelRequest) -> _Generation:
        del request
        return self.generation

    def cancel(self, generation_id: str) -> bool:
        del generation_id
        return False


class _Edge:
    def tool_proof_evidence(self) -> tuple[dict[str, object], ...]:
        return ()

    async def close(self) -> None:
        return


def _completed_core(path: Path) -> tuple[ConversationCore, SQLiteConversationStore]:
    store = SQLiteConversationStore(path)
    runtime = _Runtime()
    core = ConversationCore(
        runtime,
        scope_id="proof-scope",
        store=store,
        trusted_guidance=("Use the already-bound application action.",),
    )
    run = core.start_turn("accepted user context")
    generation = runtime.generation
    generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
    generation.emit(TextDelta(generation.generation_id, generation.epoch, "final assistant"))
    generation.emit(GenerationCompleted(generation.generation_id, generation.epoch))
    assert run.wait(2.0).status == "completed"
    return core, store


def test_clean_completion_history_is_recovered_after_store_close(tmp_path: Path) -> None:
    module = _proof_module()
    path = tmp_path / "clean.sqlite3"
    core, store = _completed_core(path)
    store.close()

    audit = module._read_history_audit(path, [core], ("Use the already-bound application action.",))

    assert audit["status"] == "PASS"
    assert audit["accepted_user_context_message"] is True
    assert audit["final_assistant_completion"] is True
    assert audit["canonical_history_roles"] == ("user", "assistant")
    assert audit["tool_call_in_canonical_history"] is False
    assert audit["tool_result_in_canonical_history"] is False
    assert audit["discord_metadata_in_canonical_history"] is False
    assert audit["discord_metadata_in_trusted_guidance"] is False


def test_post_provider_presentation_failure_keeps_history_auditable(tmp_path: Path) -> None:
    module = _proof_module()
    path = tmp_path / "presentation-failure.sqlite3"
    core, store = _completed_core(path)

    async def scenario() -> dict[str, object]:
        start_task = asyncio.create_task(asyncio.sleep(0))

        async def wait_for_completion() -> None:
            raise RuntimeError("presentation failure")

        return await module._collect_post_run(
            _Edge(),
            start_task,
            wait_for_completion=wait_for_completion,
            cores=[core],
            runtimes=[],
            stores=[store],
            store_path=path,
            trusted_guidance=("Use the already-bound application action.",),
        )

    result = asyncio.run(scenario())

    assert result["status"] == "FAIL"
    assert result["exception"] == {
        "phase": "wait_idle",
        "type": "RuntimeError",
        "cause_type": None,
        "classification": "presentation_or_router_after_core_completion",
    }
    audit = result["canonical_history_audit"]
    assert isinstance(audit, dict)
    assert audit["status"] == "PASS"
    assert audit["accepted_user_context_message"] is True
    assert audit["final_assistant_completion"] is True
    assert audit["tool_call_in_canonical_history"] is False
    assert audit["tool_result_in_canonical_history"] is False
    assert audit["discord_metadata_in_canonical_history"] is False
    assert audit["discord_metadata_in_trusted_guidance"] is False
