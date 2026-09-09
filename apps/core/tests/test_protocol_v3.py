import json
from pathlib import Path

import pytest
from lilavel_contracts import ToolEffect, ToolResult, ToolResultStatus, ToolSpec

from lilavel_core.protocol_v3 import ProtocolV3Error, parse_v3_command, parse_v3_event
from lilavel_core.sidecar_protocol import ContextMessage, ModelRequest
from lilavel_core.sidecar_protocol_v3 import (
    GenerateToolsCommand,
    ToolResultsCommand,
    encode_v3_command,
)

CORPUS = Path(__file__).resolve().parents[3] / "testdata" / "protocol-v3" / "tool-cases.json"


@pytest.mark.parametrize("case", json.loads(CORPUS.read_text(encoding="utf-8"))["cases"])
def test_shared_v3_corpus(case: dict[str, object]) -> None:
    parser = parse_v3_command if case["surface"] == "command" else parse_v3_event
    if case["accepted"]:
        parser(str(case["frame"]))
    else:
        with pytest.raises(ProtocolV3Error):
            parser(str(case["frame"]))


def test_host_v3_generate_encodes_structured_context_and_immutable_tool_snapshot() -> None:
    request = ModelRequest(
        messages=(ContextMessage("user", "use fixture"),),
        system_prompt=("trusted fixture",),
    )
    command = GenerateToolsCommand(
        "generation-1",
        7,
        request,
        (
            ToolSpec(
                "fake.echo",
                "Return a fixture value.",
                {"type": "object", "properties": {"value": {"type": "string"}}},
            ),
        ),
    )

    assert json.loads(encode_v3_command(command)) == {
        "protocol_version": 3,
        "type": "generate",
        "generation_id": "generation-1",
        "epoch": 7,
        "messages": [{"role": "user", "text": "use fixture"}],
        "system_prompt": ["trusted fixture"],
        "tools": [
            {
                "name": "fake.echo",
                "description": "Return a fixture value.",
                "input_schema": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                },
            }
        ],
    }


def test_host_v3_tool_results_preserve_exact_provider_order_and_safe_status() -> None:
    command = ToolResultsCommand(
        "generation-1",
        7,
        2,
        (
            ToolResult("call-1", ToolResultStatus.OK, {"fixture": "one"}),
            ToolResult(
                "call-2",
                ToolResultStatus.TIMED_OUT,
                None,
                reason_code="executor_timeout",
                effect=ToolEffect.UNKNOWN,
            ),
        ),
    )

    encoded = json.loads(encode_v3_command(command))
    assert [result["call_id"] for result in encoded["results"]] == ["call-1", "call-2"]
    assert encoded["results"][1] == {
        "call_id": "call-2",
        "status": "timed_out",
        "reason_code": "executor_timeout",
        "output": None,
        "effect": "unknown",
    }
