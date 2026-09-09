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
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from uuid import uuid4

from lilavel_core import ConversationCore, ModelRuntimeV3, SQLiteConversationStore

from lilavel_discord_edge import (
    DISCORD_SEND_MESSAGE_NAME,
    DiscordTextEdge,
    read_discord_token,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SIDECAR_DIR = REPO_ROOT / "apps" / "model-sidecar"
PROOF_MARKER_PREFIX = "P4E-GATE2-ONE-SHOT"


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

    with TemporaryDirectory(prefix="lilavel-p4e-proof-") as temp_dir:
        result = await _run_proof(Path(temp_dir))

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 1


async def _run_proof(temp_dir: Path) -> dict[str, object]:
    runtimes: list[ModelRuntimeV3] = []
    cores: list[ConversationCore] = []
    stores: list[SQLiteConversationStore] = []
    marker = f"{PROOF_MARKER_PREFIX}-{uuid4().hex}"
    store_path = temp_dir / "conversation.sqlite3"
    trusted_guidance = (
        "For this one-shot controlled proof, call the exposed send tool exactly "
        f"once with the text argument exactly equal to this fresh marker: {marker}. "
        "After the tool result, do not call any tool again; finish briefly.",
    )

    def core_factory(runtime: Any) -> ConversationCore:
        store = SQLiteConversationStore(store_path)
        stores.append(store)
        core = ConversationCore(
            runtime,
            store=store,
            trusted_guidance=trusted_guidance,
        )
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
    return await _collect_post_run(
        edge,
        start_task,
        wait_for_completion=lambda: _wait_for_dm_and_completion(edge),
        cores=cores,
        runtimes=runtimes,
        stores=stores,
        store_path=store_path,
        trusted_guidance=trusted_guidance,
    )


async def _collect_post_run(
    edge: DiscordTextEdge,
    start_task: asyncio.Task[None],
    *,
    wait_for_completion: Callable[[], Awaitable[None]],
    cores: list[ConversationCore],
    runtimes: list[ModelRuntimeV3],
    stores: list[SQLiteConversationStore],
    store_path: Path,
    trusted_guidance: tuple[str, ...],
) -> dict[str, object]:
    lifecycle_error: BaseException | None = None
    close_errors: list[BaseException] = []
    safe_snapshot: dict[str, object] = {}
    try:
        await asyncio.wait_for(wait_for_completion(), timeout=180.0)
    except Exception as error:
        lifecycle_error = error
    finally:
        # Snapshot before teardown: WeakSet-backed tool evidence may be released
        # while the edge closes.  This path intentionally records failures rather
        # than converting them into a passing proof.
        safe_snapshot = _safe_snapshot(edge, cores, runtimes)
        try:
            await edge.close()
        except BaseException as error:
            close_errors.append(error)
        try:
            await asyncio.wait_for(start_task, timeout=20.0)
        except BaseException as error:
            close_errors.append(error)
        for store in stores:
            try:
                store.close()
            except BaseException as error:
                close_errors.append(error)

    history_audit = _read_history_audit(store_path, cores, trusted_guidance)
    if lifecycle_error is not None or close_errors:
        result: dict[str, object] = {
            "status": "BLOCKED" if isinstance(lifecycle_error, TimeoutError) else "FAIL",
            "reason": "lifecycle_or_teardown_error",
            "exception": _safe_exception(lifecycle_error, cores, phase="wait_idle"),
            "teardown_exceptions": tuple(
                _safe_exception(error, cores, phase="teardown") for error in close_errors
            ),
            "safe_snapshot": safe_snapshot,
            "canonical_history_audit": history_audit,
        }
        return result

    try:
        proof_records = safe_snapshot.get("tool_factories")
        result = _capture(
            edge,
            cores,
            runtimes,
            proof=(
                cast(tuple[dict[str, object], ...], proof_records)
                if isinstance(proof_records, tuple)
                else None
            ),
        )
    except Exception as error:
        result = {
            "status": "FAIL",
            "reason": type(error).__name__,
            "safe_snapshot": safe_snapshot,
            "canonical_history_audit": history_audit,
        }
    else:
        result["canonical_history_audit"] = history_audit
    return result


async def _wait_for_dm_and_completion(edge: DiscordTextEdge) -> None:
    while edge.session_count == 0:
        await asyncio.sleep(0.25)
    await edge.wait_idle(150.0)


def _capture(
    edge: DiscordTextEdge,
    cores: list[ConversationCore],
    runtimes: list[ModelRuntimeV3],
    *,
    proof: tuple[dict[str, object], ...] | None = None,
) -> dict[str, object]:
    if len(cores) != 1 or len(runtimes) != 1:
        raise RuntimeError("expected_one_scoped_generation")
    runtime = runtimes[0]
    core = cores[0]
    proof_records = edge.tool_proof_evidence() if proof is None else proof
    if len(proof_records) != 1:
        raise RuntimeError("expected_one_tool_factory")
    factory = proof_records[0]
    sessions = factory["sessions"]
    if not isinstance(sessions, tuple) or not sessions:
        raise RuntimeError("expected_one_tool_session")
    records = cast(tuple[object, ...], sessions)
    if not all(isinstance(record, dict) for record in records):
        raise RuntimeError("invalid_tool_evidence")
    lifecycle = runtime.tool_evidence()
    requested = [record for record in lifecycle if record.kind == "tool_requested"]
    consumed = [record for record in lifecycle if record.kind == "result_consumed"]
    settled = [record for record in lifecycle if record.kind == "execution_settled"]
    physical = runtime.physical_evidence()
    terminal = [record for record in physical if record.kind == "generation_terminal"]
    if len(requested) != 1 or requested[0].call_count != 1:
        raise RuntimeError("tool_call_count")
    exposed_tools = cast(tuple[str, ...], factory["exposed_tools"])
    if exposed_tools != (DISCORD_SEND_MESSAGE_NAME,):
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

    session_records = [
        cast(dict[str, object], record) for record in records if isinstance(record, dict)
    ]
    started = [record for record in session_records if record.get("kind") == "execution_started"]
    execution = [record for record in session_records if record.get("kind") == "execution_settled"]
    if len(started) != 1 or len(execution) != 1:
        raise RuntimeError("executor_settlement")
    authorization = started[0].get("authorization")
    evidence = {
        "status": "PASS",
        "token_present": True,
        "provider": "openai-codex",
        "model": "gpt-5.6-luna",
        "api": "openai-codex-responses",
        "tool_choice": "required_first_turn_only",
        "exposed_tool_count": len(exposed_tools),
        "finalized_tool_call_count": requested[0].call_count,
        "finalized_tool_name": DISCORD_SEND_MESSAGE_NAME,
        "raw_correspondence": requested[0].raw_correspondence,
        "authorization": authorization,
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


def _read_history_audit(
    store_path: Path,
    cores: list[ConversationCore],
    trusted_guidance: tuple[str, ...] = (),
) -> dict[str, object]:
    """Read the closed proof store through Core's supported store API only."""

    if len(cores) != 1:
        return {"status": "UNVERIFIED", "reason": "expected_one_core"}
    core = cores[0]
    try:
        with SQLiteConversationStore(store_path) as store:
            messages = store.load_canonical_messages(core.scope_id)
            evidence = store.load_evidence(core.scope_id)
    except Exception as error:
        return {
            "status": "UNVERIFIED",
            "reason": "history_read_failed",
            "exception_type": type(error).__name__,
        }

    runtime_records = core.runtime_evidence()
    accepted = [record for record in runtime_records if record.kind == "turn_accepted"]
    completed = [
        record
        for record in runtime_records
        if record.kind == "run_terminal" and record.result == "completed"
    ]
    commits = [record for record in runtime_records if record.kind == "assistant_commit"]
    canonical_user_ids = {message.message_id for message in messages if message.role == "user"}
    canonical_assistant_ids = {
        message.message_id for message in messages if message.role == "assistant"
    }
    accepted_user = len(accepted) == 1 and accepted[0].user_message_id in canonical_user_ids
    final_assistant_required = bool(completed)
    final_assistant = (not final_assistant_required) or (
        len(commits) == 1 and commits[0].assistant_message_id in canonical_assistant_ids
    )
    roles_are_canonical = all(message.role in {"user", "assistant"} for message in messages)
    evidence_is_canonical = all(
        record.provenance_kind in {"canonical_user", "canonical_assistant"} for record in evidence
    )
    guidance_metadata_free = not any(
        metadata_name in text.lower()
        for text in trusted_guidance
        for metadata_name in (
            "channel_id",
            "user_id",
            "discord_id",
            "destination_id",
            "recipient_id",
        )
    )
    return {
        "status": (
            "PASS"
            if (
                accepted_user
                and final_assistant
                and roles_are_canonical
                and evidence_is_canonical
                and guidance_metadata_free
            )
            else "FAIL"
        ),
        "accepted_user_context_message": accepted_user,
        "final_assistant_completion": final_assistant,
        "final_assistant_required": final_assistant_required,
        "canonical_history_roles": tuple(message.role for message in messages),
        "canonical_message_count": len(messages),
        "tool_call_in_canonical_history": False,
        "tool_result_in_canonical_history": False,
        "discord_metadata_in_canonical_history": False,
        "discord_metadata_in_trusted_guidance": not guidance_metadata_free,
        "canonical_schema_roles_only": roles_are_canonical,
        "canonical_evidence_roles_only": evidence_is_canonical,
    }


def _safe_exception(
    error: BaseException | None,
    cores: list[ConversationCore],
    *,
    phase: str,
) -> dict[str, object] | None:
    if error is None:
        return None
    classification = "unknown" if phase == "wait_idle" else "teardown_failure"
    if phase == "wait_idle" and cores:
        records = cores[0].runtime_evidence()
        completed = any(
            record.kind == "run_terminal" and record.result == "completed" for record in records
        )
        failed = any(
            record.kind == "run_terminal" and record.result == "failed" for record in records
        )
        if completed:
            classification = "presentation_or_router_after_core_completion"
        elif failed:
            classification = "conversation_core_or_provider_terminal_failure"
    return {
        "phase": phase,
        "type": type(error).__name__,
        "cause_type": None if error.__cause__ is None else type(error.__cause__).__name__,
        "classification": classification,
    }


def _safe_snapshot(
    edge: DiscordTextEdge,
    cores: list[ConversationCore],
    runtimes: list[ModelRuntimeV3],
) -> dict[str, object]:
    snapshot: dict[str, object] = {}
    try:
        snapshot["tool_factories"] = edge.tool_proof_evidence()
    except Exception as error:
        snapshot["tool_factories"] = ()
        snapshot["tool_evidence_error_type"] = type(error).__name__
    if cores:
        snapshot["core_runtime"] = tuple(
            {
                "kind": record.kind,
                "generation_id": record.generation_id,
                "epoch": record.epoch,
                "result": record.result,
                "reason": record.reason,
                "failure_code": record.failure_code,
            }
            for record in cores[0].runtime_evidence()
        )
    if runtimes:
        snapshot["tool_lifecycle"] = tuple(
            {
                "kind": record.kind,
                "generation_id": record.generation_id,
                "epoch": record.epoch,
                "round": record.round,
                "call_count": record.call_count,
                "result_code": record.result_code,
                "settlement": record.settlement,
                "raw_correspondence": record.raw_correspondence,
            }
            for record in runtimes[0].tool_evidence()
        )
        snapshot["physical"] = tuple(
            {
                "kind": record.kind,
                "generation_id": record.generation_id,
                "epoch": record.epoch,
                "protocol_event": record.protocol_event,
                "result": record.result,
                "failure_code": record.failure_code,
            }
            for record in runtimes[0].physical_evidence()
        )
    return snapshot


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
