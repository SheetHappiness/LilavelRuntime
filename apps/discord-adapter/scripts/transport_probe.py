"""Run the isolated Stage A Discord transport probe."""

from __future__ import annotations

import asyncio
import json

from lilavel_discord_edge import diagnostics_from_environment, run_transport_probe


def main() -> int:
    report = asyncio.run(run_transport_probe(diagnostics=diagnostics_from_environment()))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] in {"PASS", "BLOCKED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
