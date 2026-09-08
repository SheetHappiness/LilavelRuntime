"""Run the live DM-only Discord edge against the existing Core/runtime path."""

from __future__ import annotations

import asyncio

from lilavel_discord_edge import (
    DiscordTextEdge,
    MissingDiscordToken,
    diagnostics_from_environment,
    read_semantic_streaming_from_environment,
)
from lilavel_discord_edge.edge import read_edit_interval_from_environment


async def main() -> int:
    edge = DiscordTextEdge(
        edit_interval_s=read_edit_interval_from_environment(),
        semantic_streaming=read_semantic_streaming_from_environment(),
        diagnostics=diagnostics_from_environment(),
    )
    try:
        await edge.start()
    except MissingDiscordToken:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
