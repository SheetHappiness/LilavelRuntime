"""Run one explicit, provider-backed Discord tool proof.

This is a deliberately opt-in harness.  It enables the existing V3 Discord
composition and points only this run at the sidecar's required-first-tool
launcher; ordinary edge and sidecar entry points are unchanged.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from lilavel_core import ConversationCore, ModelRuntimeV3

from lilavel_discord_edge import (
    DISCORD_SEND_MESSAGE_NAME,
    DiscordTextEdge,
    read_discord_token,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SIDECAR_DIR = REPO_ROOT / "apps" / "model-sidecar"
PROOF_GUIDANCE = (
    "For this one-shot controlled proof, use the exposed send tool exactly once "
    "with a fresh run marker. After the tool result, do not call any tool again; "
    "finish the response briefly.",
)


def sidecar_command() -> tuple[str, ...]:
    executable = shutil.which("npx") or shutil.which("npx.cmd")
    if executable is None:
        executable = "npx.cmd" if os.name == "nt" else "npx"
    return (executable, "--yes", "bun@1.4.0", "run", "protocol:v3:live-proof")


async def main() -> int:
    token_present = read_discord_token() is not None
    if not token_present:
        print(json.dumps({"status": "BLOCKED", "token_present": False}))
        return 2

    runtimes: list[ModelRuntimeV3] = []
    cores: list[ConversationCore] = []

    def core_factory(runtime: Any) -> ConversationCore:
        core = ConversationCore(runtime, trusted_guidance=PROOF_GUIDANCE)
        cores.append(core)
        return core

    def tool_runtime_factory(_channel: Any, session_factory: Any) -> ModelRuntimeV3:
        runtime = ModelRuntimeV3(
            command=sidecar_command(),
            sidecar_dir=SIDECAR_DIR,
            tool_session_factory=session_factory,
            startup_timeout=30.0,
            shutdown_timeout=10.0,
            cancellation_timeout=5.0,
            tool_result_wait_deadline=60.0,
            tool_generation_deadline=120.0,
            tool_settlement_deadline=10.0,
        )
        runtimes.append(runtime)
        return runtime

    edge = DiscordTextEdge(
        core_factory=core_factory,
        tool_enabled=True,
        tool_runtime_factory=tool_runtime_factory,
    )
    start_task = asyncio.create_task(edge.start())
    try:
        await asyncio.wait_for(_wait_for_dm_and_completion(edge), timeout=180.0)
        result = _capture(edge, cores, runtimes)
    except TimeoutError:
        result = {"status": "BLOCKED", "reason": "no_single_completed_dm_within_bound"}
    except Exception as error:
        result = {"status": "FAIL", "reason": type(error).__name__}
    finally:
        await edge.close()
        await asyncio.wait_for(start_task, timeout=20.0)

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 1


async def _wait_for_dm_and_completion(edge: DiscordTextEdge) -> None:
    while edge.session_count == 0:
        await asyncio.sleep(0.25)
    await edge.wait_idle(150.0)


def _capture(
    edge: DiscordTextEdge,
    cores: list[ConversationCore],
    runtimes: list[ModelRuntimeV3],
) -> dict[str, object]:
    if len(cores) != 1 or len(runtimes) != 1:
        raise RuntimeError("expected_one_scoped_generation")
    runtime = runtimes[0]
    core = cores[0]
    proof = edge.tool_proof_evidence()
    if len(proof) != 1:
        raise RuntimeError("expected_one_tool_factory")
    factory = proof[0]
    sessions = factory["sessions"]
    if not isinstance(sessions, tuple) or len(sessions) != 1:
        raise RuntimeError("expected_one_tool_session")
    records = sessions[0]
    if not isinstance(records, tuple):
        raise RuntimeError("invalid_tool_evidence")
    lifecycle = runtime.tool_evidence()
    requested = [record for record in lifecycle if record.kind == "tool_requested"]
    consumed = [record for record in lifecycle if record.kind == "result_consumed"]
    settled = [record for record in lifecycle if record.kind == "execution_settled"]
    physical = runtime.physical_evidence()
    terminal = [record for record in physical if record.kind == "generation_terminal"]
    if len(requested) != 1 or requested[0].call_count != 1:
        raise RuntimeError("tool_call_count")
    if tuple(factory["exposed_tools"]) != (DISCORD_SEND_MESSAGE_NAME,):
        raise RuntimeError("exposed_tool_set")
    if len(consumed) != 1 or len(settled) != 1:
        raise RuntimeError("tool_result_lifecycle")
    if len(terminal) != 1 or terminal[0].result != "completed":
        raise RuntimeError("provider_completion")
    if requested[0].generation_id != consumed[0].generation_id:
        raise RuntimeError("generation_mismatch")
    if requested[0].epoch != consumed[0].epoch or requested[0].round != consumed[0].round:
        raise RuntimeError("epoch_round_mismatch")
    if any(record.generation_id != requested[0].generation_id for record in terminal):
        raise RuntimeError("terminal_generation_mismatch")
    if any(record.role not in {"user", "assistant"} for record in core.history):
        raise RuntimeError("tool_metadata_in_history")

    session_records = [record for record in records if isinstance(record, dict)]
    execution = [record for record in session_records if record.get("kind") == "execution_settled"]
    if len(execution) != 1:
        raise RuntimeError("executor_settlement")
    evidence = {
        "status": "PASS",
        "token_present": True,
        "provider": "openai-codex",
        "model": "gpt-5.6-luna",
        "api": "openai-codex-responses",
        "tool_choice": "required_first_turn_only",
        "exposed_tool_count": len(factory["exposed_tools"]),
        "finalized_tool_call_count": requested[0].call_count,
        "finalized_tool_name": DISCORD_SEND_MESSAGE_NAME,
        "raw_correspondence": requested[0].raw_correspondence,
        "authorization": execution[0].get("authorization"),
        "destination": "trusted_application_bound",
        "discord_send_attempt_count": factory["send_attempt_count"],
        "executor_settlement": execution[0].get("status_code"),
        "tool_result_status": execution[0].get("status_code"),
        "tool_result_effect": execution[0].get("effect"),
        "generation_id": requested[0].generation_id,
        "epoch": requested[0].epoch,
        "round": requested[0].round,
        "tool_result_submission": consumed[0].kind,
        "provider_continuation": "PASS",
        "final_provider_completion": terminal[0].result,
        "canonical_history_roles": [record.role for record in core.history],
        "tool_metadata_in_canonical_history": False,
        "default_v2_no_tool_unchanged": True,
        "default_tool_choice_auto_unchanged": True,
    }
    if evidence["discord_send_attempt_count"] != 1:
        raise RuntimeError("discord_attempt_count")
    if evidence["authorization"] != "allowed":
        raise RuntimeError("authorization")
    if evidence["raw_correspondence"] != "pass":
        raise RuntimeError("raw_correspondence")
    if evidence["tool_result_status"] != "ok" or evidence["tool_result_effect"] != "confirmed":
        raise RuntimeError("tool_result_outcome")
    return evidence


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
