"""Run a small, deterministic, offline runtime-evidence scenario.

The runner is deliberately a repo-local validation surface.  It invokes the
existing Core runtime, committed fake sidecar, and Discord edge diagnostics;
it does not replay protocol frames or reimplement lifecycle semantics.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from aiohttp.tracing import TraceRequestEndParams, TraceRequestStartParams
from lilavel_core import (
    ConversationCore,
    GenerationAccepted,
    GenerationCompleted,
    ModelRuntime,
    PhysicalGenerationEvidenceRecord,
    RuntimeEvidenceRecord,
    TextDelta,
)
from multidict import CIMultiDict
from yarl import URL

from lilavel_discord_edge import (
    INTERRUPTED_MARKER,
    DiscordDiagnostics,
    DiscordTextEdge,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE_ROOT = REPO_ROOT / "apps" / "core"
FAKE_SIDECAR = CORE_ROOT / "tests" / "fixtures" / "fake_sidecar.py"

SCENARIO_NAMES = (
    "success",
    "supersession",
    "sidecar-failure",
    "malformed-frame",
    "discord-interruption",
)


@dataclass(frozen=True, slots=True)
class ScenarioCapture:
    """Safe source-separated evidence retained until JSONL rendering."""

    scenario: str
    core: tuple[RuntimeEvidenceRecord, ...]
    physical: tuple[PhysicalGenerationEvidenceRecord, ...]
    discord: tuple[dict[str, object], ...]
    result: dict[str, object]


def _make_runtime(mode: str) -> ModelRuntime:
    if not FAKE_SIDECAR.is_file():
        raise RuntimeError("committed fake sidecar fixture is unavailable")
    return ModelRuntime(
        command=(sys.executable, str(FAKE_SIDECAR), mode),
        sidecar_dir=CORE_ROOT,
        startup_timeout=5.0,
        shutdown_timeout=5.0,
        cancellation_timeout=2.0,
        write_timeout=2.0,
    )


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise RuntimeError("scenario did not reach its deterministic checkpoint")
        time.sleep(0.001)


async def _wait_until_async(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError("scenario did not reach its deterministic checkpoint")
        await asyncio.sleep(0.001)


def _complete_run(run: Any) -> None:
    run.wait(5.0)
    tuple(run.events())


def _core_run_summaries(
    core_records: Sequence[RuntimeEvidenceRecord],
    physical_records: Sequence[PhysicalGenerationEvidenceRecord],
) -> list[dict[str, object]]:
    physical_terminals = {
        (record.generation_id, record.epoch): record
        for record in physical_records
        if record.kind == "generation_terminal"
    }
    summaries: list[dict[str, object]] = []
    for accepted in (record for record in core_records if record.kind == "turn_accepted"):
        run_records = [record for record in core_records if record.run_id == accepted.run_id]
        bound = next((record for record in run_records if record.kind == "generation_bound"), None)
        terminal = next((record for record in run_records if record.kind == "run_terminal"), None)
        generation_terminal = next(
            (record for record in run_records if record.kind == "generation_terminal"), None
        )
        physical_terminal = (
            None
            if bound is None or bound.generation_id is None or bound.epoch is None
            else physical_terminals.get((bound.generation_id, bound.epoch))
        )
        discarded = [record for record in run_records if record.kind == "delta_discarded"]
        summaries.append(
            {
                "run_id": accepted.run_id,
                "user_message_id": accepted.user_message_id,
                "generation_id": None if bound is None else bound.generation_id,
                "epoch": None if bound is None else bound.epoch,
                "semantic_generation_result": (
                    None if generation_terminal is None else generation_terminal.result
                ),
                "semantic_result": None if terminal is None else terminal.result,
                "physical_result": None if physical_terminal is None else physical_terminal.result,
                "physical_failure_code": (
                    None if physical_terminal is None else physical_terminal.failure_code
                ),
                "assistant_committed": any(
                    record.kind == "assistant_commit" for record in run_records
                ),
                "discarded_delta_count": sum(record.discarded_count for record in discarded),
                "discarded_delta_bytes": sum(record.discarded_bytes for record in discarded),
            }
        )
    return summaries


def _core_result(
    *,
    scenario: str,
    core_records: Sequence[RuntimeEvidenceRecord],
    physical_records: Sequence[PhysicalGenerationEvidenceRecord],
    scenario_result: str,
    status: str,
    evidence_surfaces: Sequence[str],
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": status,
        "result": scenario_result,
        "deterministic": True,
        "offline": True,
        "evidence_surfaces": list(evidence_surfaces),
        "runs": _core_run_summaries(core_records, physical_records),
    }
    if extra is not None:
        result.update(extra)
    return result


@lru_cache(maxsize=1)
def _load_existing_fake_runtime() -> type[Any]:
    """Load the existing semantic late-delta test double without pytest APIs."""

    path = CORE_ROOT / "tests" / "test_conversation.py"
    spec = spec_from_file_location("lilavel_existing_conversation_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("existing Core conversation test infrastructure is unavailable")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    runtime_type = getattr(module, "FakeRuntime", None)
    if not isinstance(runtime_type, type):
        raise RuntimeError("existing Core conversation fake runtime is unavailable")
    return runtime_type


def _emit_fake_generation(generation: Any, text: str, *, complete: bool) -> None:
    generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
    if text:
        generation.emit(TextDelta(generation.generation_id, generation.epoch, text))
    if complete:
        generation.emit(GenerationCompleted(generation.generation_id, generation.epoch))


def _run_core_supersession(input_text: str) -> ScenarioCapture:
    runtime = _load_existing_fake_runtime()()
    core = ConversationCore(runtime, scope_id="runner-supersession")
    old = core.start_turn(input_text)
    _wait_until(lambda: len(runtime.generations) == 1)
    _emit_fake_generation(runtime.generations[0], "partial", complete=False)
    _wait_until(lambda: old.text == "partial")
    current = core.start_turn("fresh")
    _complete_run(old)
    _wait_until(lambda: len(runtime.generations) == 2)
    _emit_fake_generation(runtime.generations[1], "fresh", complete=True)
    _complete_run(current)
    core_records = core.runtime_evidence()
    physical_records: tuple[PhysicalGenerationEvidenceRecord, ...] = ()
    summaries = _core_run_summaries(core_records, physical_records)
    if not (
        len(summaries) == 2
        and summaries[0]["semantic_result"] == "superseded"
        and summaries[0]["assistant_committed"] is False
        and summaries[1]["semantic_result"] == "completed"
        and summaries[1]["assistant_committed"] is True
        and summaries[0]["discarded_delta_count"] == 1
    ):
        raise RuntimeError("supersession scenario did not settle as expected")
    result = _core_result(
        scenario="supersession",
        core_records=core_records,
        physical_records=physical_records,
        scenario_result="superseded_then_completed",
        status="completed",
        evidence_surfaces=("core",),
        extra={
            "late_delta_discarded_count": summaries[0]["discarded_delta_count"],
            "physical_evidence": "not invoked by the semantic test double",
        },
    )
    return ScenarioCapture("supersession", core_records, physical_records, (), result)


def _run_core_scenario(
    scenario: str,
    *,
    mode: str,
    scope_id: str,
    input_text: str,
) -> ScenarioCapture:
    if scenario == "supersession":
        return _run_core_supersession(input_text)

    runtime = _make_runtime(mode)
    core = ConversationCore(runtime, scope_id=scope_id)
    runtime.start()
    try:
        run = core.start_turn(input_text)
        _complete_run(run)
        core_records = core.runtime_evidence()
        physical_records = runtime.physical_evidence()
        summaries = _core_run_summaries(core_records, physical_records)
        if scenario == "success":
            expected = (
                len(summaries) == 1
                and summaries[0]["semantic_result"] == "completed"
                and summaries[0]["physical_result"] == "completed"
                and summaries[0]["assistant_committed"] is True
            )
            scenario_result = "completed"
            status = "completed"
        else:
            expected = (
                len(summaries) == 1
                and summaries[0]["semantic_result"] == "failed"
                and summaries[0]["physical_result"] == "failed"
                and summaries[0]["assistant_committed"] is False
            )
            scenario_result = "failed"
            status = "failed"
        if not expected:
            raise RuntimeError("Core scenario did not produce the expected terminal")
        extra = None
        if scenario != "success":
            extra = {"expected_failure": True, "runtime_state": runtime.health().state}
        result = _core_result(
            scenario=scenario,
            core_records=core_records,
            physical_records=physical_records,
            scenario_result=scenario_result,
            status=status,
            evidence_surfaces=("core", "physical"),
            extra=extra,
        )
        return ScenarioCapture(scenario, core_records, physical_records, (), result)
    finally:
        runtime.shutdown()


class _RunnerClient:
    def __init__(self) -> None:
        self.user = SimpleNamespace(id="runner-bot", bot=True)
        self.closed = False

    def event(self, coroutine: Any) -> Any:
        return coroutine

    async def close(self) -> None:
        self.closed = True


class _RunnerTyping:
    def __init__(self, channel: _RunnerChannel) -> None:
        self.channel = channel

    async def __aenter__(self) -> _RunnerTyping:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _RunnerMessage:
    def __init__(self, content: str, diagnostics: DiscordDiagnostics, channel_id: str) -> None:
        self.content = content
        self._diagnostics = diagnostics
        self._channel_id = channel_id

    async def edit(self, **kwargs: Any) -> _RunnerMessage:
        await _emit_fake_http_request(
            self._diagnostics,
            "PATCH",
            f"/channels/{self._channel_id}/messages/runner-message",
        )
        self.content = cast(str, kwargs["content"])
        return self


class _RunnerChannel:
    def __init__(self, channel_id: str, diagnostics: DiscordDiagnostics) -> None:
        self.id = channel_id
        self.is_dm = True
        self._diagnostics = diagnostics
        self.messages: list[_RunnerMessage] = []

    def typing(self) -> _RunnerTyping:
        return _RunnerTyping(self)

    async def send(self, **kwargs: Any) -> _RunnerMessage:
        await _emit_fake_http_request(
            self._diagnostics,
            "POST",
            f"/channels/{self.id}/messages",
        )
        message = _RunnerMessage(cast(str, kwargs["content"]), self._diagnostics, str(self.id))
        self.messages.append(message)
        return message


@dataclass(frozen=True, slots=True)
class _RunnerInboundMessage:
    id: str
    channel: _RunnerChannel
    author: Any
    content: str


async def _emit_fake_http_request(
    diagnostics: DiscordDiagnostics,
    method: str,
    path: str,
) -> None:
    trace = diagnostics.trace_config
    context = trace.trace_config_ctx()
    url = URL(f"https://discord.com/api/v10{path}")
    await trace.on_request_start.send(
        cast(Any, None),
        context,
        TraceRequestStartParams(method, url, CIMultiDict()),
    )
    await trace.on_request_end.send(
        cast(Any, None),
        context,
        TraceRequestEndParams(
            method,
            url,
            CIMultiDict(),
            cast(Any, SimpleNamespace(status=200, headers=CIMultiDict())),
        ),
    )


async def _run_discord_interruption() -> ScenarioCapture:
    diagnostics = DiscordDiagnostics()
    diagnostics.trace_config.freeze()
    client = _RunnerClient()
    runtimes: list[Any] = []
    cores: list[ConversationCore] = []
    fake_runtime_type = _load_existing_fake_runtime()

    def runtime_factory() -> Any:
        runtime = fake_runtime_type()
        runtimes.append(runtime)
        return runtime

    def core_factory(runtime: Any) -> ConversationCore:
        core = ConversationCore(runtime, scope_id="runner-discord-interruption")
        cores.append(core)
        return core

    edge = DiscordTextEdge(
        client=cast(Any, client),
        runtime_factory=runtime_factory,
        core_factory=core_factory,
        message_filter=lambda message: bool(getattr(message.channel, "is_dm", False)),
        edit_interval_s=0.0,
        close_timeout_s=5.0,
        diagnostics=diagnostics,
        semantic_streaming=False,
    )
    channel = _RunnerChannel("runner-discord-channel", diagnostics)
    try:
        await edge.handle_message(
            _RunnerInboundMessage(
                "runner-old-message",
                channel,
                SimpleNamespace(id="runner-human", bot=False),
                "cancel",
            )
        )
        await _wait_until_async(lambda: len(runtimes) == 1)
        await _wait_until_async(lambda: len(runtimes[0].generations) == 1)
        _emit_fake_generation(runtimes[0].generations[0], "partial", complete=False)
        await _wait_until_async(
            lambda: bool(channel.messages) and channel.messages[0].content == "partial"
        )
        await edge.handle_message(
            _RunnerInboundMessage(
                "runner-new-message",
                channel,
                SimpleNamespace(id="runner-human", bot=False),
                "fresh",
            )
        )
        active_run = cores[0].active_run
        if active_run is None:
            raise RuntimeError("Discord scenario lost its active Core run")
        # The actor queues the second USER episode. Exercise the existing
        # Core supersession seam explicitly so this scenario continues to
        # cover interrupted presentation without creating a second actor lane.
        active_run.request_cancel("superseded")
        await _wait_until_async(lambda: len(runtimes[0].generations) == 2)
        _emit_fake_generation(runtimes[0].generations[1], "fresh", complete=True)
        await _wait_until_async(
            lambda: any(message.content == "fresh" for message in channel.messages)
        )
        await edge.wait_idle()
        if INTERRUPTED_MARKER not in channel.messages[0].content:
            raise RuntimeError("Discord interruption marker was not presented")
        if not cores or not runtimes:
            raise RuntimeError("Discord scenario did not create its Core/runtime session")
        core_records = cores[0].runtime_evidence()
        physical_records: tuple[PhysicalGenerationEvidenceRecord, ...] = ()
        summaries = _core_run_summaries(core_records, physical_records)
        presentation = _presentation_summaries(diagnostics.events)
        if not (
            len(summaries) == 2
            and summaries[0]["semantic_result"] == "superseded"
            and summaries[1]["semantic_result"] == "completed"
            and len(presentation) == 2
            and presentation[0]["status"] == "interrupted"
            and presentation[1]["status"] == "completed"
        ):
            raise RuntimeError("Discord interruption scenario did not correlate as expected")
        result = _core_result(
            scenario="discord-interruption",
            core_records=core_records,
            physical_records=physical_records,
            scenario_result="interrupted_then_completed",
            status="completed",
            evidence_surfaces=("core", "discord"),
            extra={
                "presentation": presentation,
                "physical_evidence": "not invoked by the semantic test double",
            },
        )
        return ScenarioCapture(
            "discord-interruption",
            core_records,
            physical_records,
            tuple(diagnostics.events),
            result,
        )
    finally:
        await edge.close()


def _presentation_summaries(events: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "core_run_id": event["core_run_id"],
            "status": event["status"],
            "reason": event.get("reason"),
            "flush_count": event["flush_count"],
            "terminal_flush_count": event["terminal_flush_count"],
        }
        for event in events
        if event.get("kind") == "presentation_outcome" and "core_run_id" in event
    ]


def run_scenario_capture(name: str, *, input_text: str | None = None) -> ScenarioCapture:
    """Run one named scenario without writing output or persistent state."""

    if name == "success":
        return _run_core_scenario(
            name,
            mode="normal",
            scope_id="runner-success",
            input_text=input_text or "runner success input",
        )
    if name == "supersession":
        return _run_core_scenario(
            name,
            mode="supersede-race",
            scope_id="runner-supersession",
            input_text=input_text or "cancel",
        )
    if name == "sidecar-failure":
        return _run_core_scenario(
            name,
            mode="crash-after-accepted",
            scope_id="runner-sidecar-failure",
            input_text=input_text or "runner failure input",
        )
    if name == "malformed-frame":
        return _run_core_scenario(
            name,
            mode="malformed-after-accepted",
            scope_id="runner-malformed-frame",
            input_text=input_text or "runner malformed input",
        )
    if name == "discord-interruption":
        return asyncio.run(_run_discord_interruption())
    raise ValueError("unknown scenario")


def _core_json_record(scenario: str, record: RuntimeEvidenceRecord) -> dict[str, object]:
    result: dict[str, object] = {
        "record_type": "evidence",
        "scenario": scenario,
        "surface": "core",
        "source_sequence": record.sequence,
        "kind": record.kind,
        "scope_id": record.scope_id,
        "run_id": record.run_id,
        "user_message_id": record.user_message_id,
    }
    for key in (
        "generation_id",
        "epoch",
        "assistant_message_id",
        "result",
        "reason",
        "failure_code",
    ):
        value = getattr(record, key)
        if value is not None:
            result[key] = value
    if record.discarded_count:
        result["discarded_count"] = record.discarded_count
        result["discarded_bytes"] = record.discarded_bytes
    return result


def _physical_json_record(
    scenario: str,
    record: PhysicalGenerationEvidenceRecord,
) -> dict[str, object]:
    result: dict[str, object] = {
        "record_type": "evidence",
        "scenario": scenario,
        "surface": "physical",
        "source_sequence": record.sequence,
        "kind": record.kind,
        "generation_id": record.generation_id,
        "epoch": record.epoch,
    }
    for key in ("protocol_event", "result", "failure_code"):
        value = getattr(record, key)
        if value is not None:
            result[key] = value
    if record.event_count:
        result["event_count"] = record.event_count
        result["event_bytes"] = record.event_bytes
    return result


_DISCORD_EVENT_FIELDS = {
    "core_run_id",
    "flush_id",
    "chunk_count",
    "force",
    "outcome",
    "presenter_flush_id",
    "operation_id",
    "operation",
    "request_id",
    "response_status",
    "rate_limited",
    "status",
    "reason",
    "flush_count",
    "terminal_flush_count",
}
_DISCORD_EVENT_KINDS = {
    "presenter_flush_start",
    "presenter_flush_return",
    "operation_start",
    "operation_return",
    "http_request_start",
    "http_response",
    "presentation_outcome",
}


def _discord_json_record(
    scenario: str,
    source_sequence: int,
    event: dict[str, object],
) -> dict[str, object] | None:
    kind = event.get("kind")
    if not isinstance(kind, str) or kind not in _DISCORD_EVENT_KINDS:
        return None
    result: dict[str, object] = {
        "record_type": "evidence",
        "scenario": scenario,
        "surface": "discord",
        "source_sequence": source_sequence,
        "kind": kind,
    }
    for key in _DISCORD_EVENT_FIELDS:
        if key in event:
            value = event[key]
            if value is None or isinstance(value, (bool, int, str)):
                result[key] = value
    return result


def render_jsonl(capture: ScenarioCapture) -> str:
    """Render only safe source records and the deterministic runner result."""

    records: list[dict[str, object]] = [
        {
            "record_type": "scenario",
            "scenario": capture.scenario,
            "surface": "runner",
            "status": "started",
            "deterministic": True,
            "offline": True,
        }
    ]
    records.extend(_core_json_record(capture.scenario, record) for record in capture.core)
    records.extend(_physical_json_record(capture.scenario, record) for record in capture.physical)
    for source_sequence, event in enumerate(capture.discord, start=1):
        record = _discord_json_record(capture.scenario, source_sequence, event)
        if record is not None:
            records.append(record)
    records.append(
        {
            "record_type": "result",
            "scenario": capture.scenario,
            "surface": "runner",
            **capture.result,
        }
    )
    return "".join(
        json.dumps(
            {"ordering": ordering, **record},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for ordering, record in enumerate(records, start=1)
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one deterministic, offline Lilavel runtime-evidence scenario."
    )
    parser.add_argument("scenario", choices=SCENARIO_NAMES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        capture = run_scenario_capture(args.scenario)
        sys.stdout.write(render_jsonl(capture))
        sys.stdout.flush()
    except Exception:
        print(
            "scenario execution failed; no evidence was emitted",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
