"""Replaceable DM-first Discord text edge for Lilavel."""

from .diagnostics import (
    DISCORD_HTTP_DIAGNOSTICS_ENV,
    DiscordDiagnostics,
    diagnostics_from_environment,
)
from .edge import (
    DISCORD_TOKEN_ENV,
    SEMANTIC_STREAMING_ENV,
    DiscordTextEdge,
    MissingDiscordToken,
    make_dm_intents,
    read_discord_token,
    read_semantic_streaming_from_environment,
)
from .presenter import (
    FAILED_MARKER,
    INTERRUPTED_MARKER,
    NO_RESPONSE_MARKER,
    ReplyPresenter,
)
from .probe import run_transport_probe
from .tool import (
    DISCORD_SEND_MESSAGE_MAX_CHARS,
    DISCORD_SEND_MESSAGE_NAME,
    DISCORD_SEND_MESSAGE_PROVIDER_ALIAS,
    DISCORD_SEND_MESSAGE_SPEC,
    DiscordSendPreflightError,
    DiscordToolSessionFactory,
)
from .transport import (
    MAX_DISCORD_MESSAGE_CHARS,
    DiscordMessageSink,
    RateLimitObservation,
    TransportMetrics,
    split_discord_content,
)

__all__ = [
    "DISCORD_TOKEN_ENV",
    "DISCORD_HTTP_DIAGNOSTICS_ENV",
    "DiscordMessageSink",
    "DiscordSendPreflightError",
    "DiscordToolSessionFactory",
    "DiscordDiagnostics",
    "DiscordTextEdge",
    "FAILED_MARKER",
    "INTERRUPTED_MARKER",
    "MAX_DISCORD_MESSAGE_CHARS",
    "DISCORD_SEND_MESSAGE_MAX_CHARS",
    "DISCORD_SEND_MESSAGE_NAME",
    "DISCORD_SEND_MESSAGE_PROVIDER_ALIAS",
    "DISCORD_SEND_MESSAGE_SPEC",
    "MissingDiscordToken",
    "NO_RESPONSE_MARKER",
    "RateLimitObservation",
    "ReplyPresenter",
    "SEMANTIC_STREAMING_ENV",
    "TransportMetrics",
    "make_dm_intents",
    "diagnostics_from_environment",
    "read_discord_token",
    "read_semantic_streaming_from_environment",
    "run_transport_probe",
    "split_discord_content",
]
