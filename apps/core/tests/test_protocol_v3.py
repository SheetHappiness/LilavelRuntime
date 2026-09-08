import json
from pathlib import Path

import pytest

from lilavel_core.protocol_v3 import ProtocolV3Error, parse_v3_command, parse_v3_event

CORPUS = Path(__file__).resolve().parents[3] / "testdata" / "protocol-v3" / "tool-cases.json"


@pytest.mark.parametrize("case", json.loads(CORPUS.read_text(encoding="utf-8"))["cases"])
def test_shared_v3_corpus(case: dict[str, object]) -> None:
    parser = parse_v3_command if case["surface"] == "command" else parse_v3_event
    if case["accepted"]:
        parser(str(case["frame"]))
    else:
        with pytest.raises(ProtocolV3Error):
            parser(str(case["frame"]))
