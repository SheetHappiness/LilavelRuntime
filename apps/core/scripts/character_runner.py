"""Model-backed Character v0 response recorder.

This harness runs the fixed Character v0 corpus through the real Core and
model-sidecar path.  It records only the bounded response records consumed by
``character_eval.py``; it does not change production composition or enable the
experimental character in the Discord edge.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

from character_eval import SCENARIOS, ArmId, RawResponse, Scenario, _guidance_by_arm

from lilavel_core import ConversationCore, ConversationRun, ConversationTextDelta, ModelRuntime

_ARM_ORDER: tuple[ArmId, ...] = (
    "A-neutral",
    "B-experimental-identity",
    "C-character-v0",
)
_GENERATION_TIMEOUT_SECONDS = 120.0
_CANCELLATION_SETTLE_TIMEOUT_SECONDS = 30.0


def _error_response(
    scenario: Scenario,
    arm: ArmId,
    *,
    error_type: str,
    issue: str,
) -> RawResponse:
    return RawResponse(
        scenario_id=scenario.id,
        arm=arm,
        response="",
        status="error",
        issues=(issue,),
        error_type=error_type,
        prompt=scenario.prompt,
    )


def run_one(
    runtime: ModelRuntime,
    scenario: Scenario,
    arm: ArmId,
    guidance: tuple[str, ...],
) -> RawResponse:
    """Run one isolated corpus turn through ConversationCore and ModelRuntime."""

    core = ConversationCore(runtime, trusted_guidance=guidance)
    parts: list[str] = []
    status = "error"
    issues: list[str] = []
    error_type: str | None = None
    collected: list[str | None] = [None]
    collector_error: list[BaseException] = []

    def collect_turn(turn: ConversationRun) -> None:
        try:
            for event in turn:
                if isinstance(event, ConversationTextDelta):
                    parts.append(event.delta)
            collected[0] = turn.wait(0).status
        except BaseException as error:  # pragma: no cover - live-provider guard
            collector_error.append(error)

    try:
        turn = core.start_turn(scenario.prompt)
        collector = threading.Thread(
            target=collect_turn,
            args=(turn,),
            name="lilavel-character-v0-collector",
            daemon=True,
        )
        collector.start()
        collector.join(_GENERATION_TIMEOUT_SECONDS)
        if collector.is_alive():
            issues.append("generation_timeout")
            issues.append(
                "timeout_cancel_requested" if turn.cancel() else "timeout_already_settled"
            )
            collector.join(_CANCELLATION_SETTLE_TIMEOUT_SECONDS)
            if collector.is_alive():
                issues.append("timeout_unsettled")
                raise TimeoutError("generation did not settle after cancellation")
        if collector_error:
            raise collector_error[0]
        if collected[0] is None:
            raise RuntimeError("collector ended without an outcome")
        status = collected[0]
    except Exception as error:  # noqa: BLE001 - record only the safe type name.
        error_type = type(error).__name__
        issues.append(f"runtime_error:{error_type}")

    return RawResponse(
        scenario_id=scenario.id,
        arm=arm,
        response="".join(parts),
        status=status,
        issues=tuple(issues),
        error_type=error_type,
        prompt=scenario.prompt,
    )


def _row(response: RawResponse) -> dict[str, object]:
    return {
        "scenario_id": response.scenario_id,
        "arm": response.arm,
        "prompt": response.prompt,
        "status": response.status,
        "response": response.response,
        "issues": list(response.issues),
        "error_type": response.error_type,
    }


def _write_rows(path: Path, responses: list[RawResponse]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(_row(response), ensure_ascii=False, sort_keys=True) for response in responses
        )
        + "\n",
        encoding="utf-8",
    )


def record_corpus(path: Path, runtime: ModelRuntime | None = None) -> int:
    """Record all 30 A/B/C responses and return a process-style result code."""

    active_runtime = runtime if runtime is not None else ModelRuntime()
    guidance_by_arm = _guidance_by_arm()
    responses: list[RawResponse] = []
    shutdown_error = False

    try:
        active_runtime.start()
        for scenario in SCENARIOS:
            for arm in _ARM_ORDER:
                responses.append(run_one(active_runtime, scenario, arm, guidance_by_arm[arm]))
                _write_rows(path, responses)
    except Exception as error:  # noqa: BLE001 - startup failure is safe to classify.
        error_name = type(error).__name__
        if not responses:
            responses = [
                _error_response(
                    scenario,
                    arm,
                    error_type=error_name,
                    issue="runtime_start_error",
                )
                for scenario in SCENARIOS
                for arm in _ARM_ORDER
            ]
        _write_rows(path, responses)
    finally:
        try:
            active_runtime.shutdown()
        except Exception:  # noqa: BLE001 - do not expose provider/OS error text.
            shutdown_error = True

    return (
        0
        if len(responses) == 30
        and not shutdown_error
        and all(
            response.status == "completed"
            and bool(response.response.strip())
            and not response.issues
            and response.error_type is None
            for response in responses
        )
        else 2
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("character-v0-recorded.jsonl"),
        help="JSONL path for records consumed by character_eval.py",
    )
    args = parser.parse_args(argv)
    return record_corpus(args.output)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
