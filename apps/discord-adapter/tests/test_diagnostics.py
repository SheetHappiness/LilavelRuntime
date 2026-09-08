"""Deterministic coverage for opt-in Discord HTTP diagnostics."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from aiohttp.tracing import (
    TraceRequestEndParams,
    TraceRequestExceptionParams,
    TraceRequestStartParams,
)
from multidict import CIMultiDict
from yarl import URL

from lilavel_discord_edge import (
    DISCORD_HTTP_DIAGNOSTICS_ENV,
    DiscordDiagnostics,
    DiscordMessageSink,
    DiscordTextEdge,
    RateLimitObservation,
    ReplyPresenter,
    TransportMetrics,
    diagnostics_from_environment,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.edits: list[str] = []
        self.deleted = False

    async def edit(self, **kwargs: Any) -> FakeMessage:
        self.content = cast(str, kwargs["content"])
        self.edits.append(self.content)
        return self

    async def delete(self) -> None:
        self.deleted = True


class FakeChannel:
    def __init__(self) -> None:
        self.messages: list[FakeMessage] = []

    async def send(self, **kwargs: Any) -> FakeMessage:
        message = FakeMessage(cast(str, kwargs["content"]))
        self.messages.append(message)
        return message


_ALLOWED_DIAGNOSTIC_FIELDS: dict[str, frozenset[str]] = {
    "operation_start": frozenset(
        {"kind", "at_s", "operation", "operation_id", "presenter_flush_id", "core_run_id"}
    ),
    "operation_return": frozenset(
        {
            "kind",
            "at_s",
            "operation",
            "operation_id",
            "presenter_flush_id",
            "core_run_id",
            "outcome",
            "await_duration_s",
            "http_response_count",
            "last_http_response_at_s",
            "post_response_gap_s",
            "error_type",
        }
    ),
    "http_request_start": frozenset(
        {
            "kind",
            "at_s",
            "request_id",
            "operation",
            "operation_id",
            "presenter_flush_id",
            "core_run_id",
        }
    ),
    "http_response": frozenset(
        {
            "kind",
            "at_s",
            "request_id",
            "operation",
            "operation_id",
            "presenter_flush_id",
            "core_run_id",
            "response_duration_s",
            "response_status",
            "rate_limited",
            "rate_limit_limit",
            "rate_limit_remaining",
            "rate_limit_reset_after_s",
            "rate_limit_bucket_hash",
            "retry_after_s",
        }
    ),
    "http_request_exception": frozenset(
        {
            "kind",
            "at_s",
            "request_id",
            "operation",
            "operation_id",
            "presenter_flush_id",
            "core_run_id",
            "duration_s",
            "error_type",
        }
    ),
    "presenter_flush_start": frozenset(
        {"kind", "at_s", "flush_id", "chunk_count", "force", "core_run_id"}
    ),
    "presenter_flush_return": frozenset(
        {"kind", "at_s", "flush_id", "duration_s", "outcome", "error_type", "core_run_id"}
    ),
    "presentation_outcome": frozenset(
        {
            "kind",
            "at_s",
            "core_run_id",
            "status",
            "reason",
            "flush_count",
            "terminal_flush_count",
        }
    ),
    "semantic_selection": frozenset(
        {
            "kind",
            "at_s",
            "raw_chars",
            "visible_chars",
            "hidden_tail_chars",
            "hidden_tail_age_s",
            "lookahead_s",
            "max_lookahead_s",
            "boundary",
            "preview_repaired",
            "published",
        }
    ),
}

_AUTH_SENTINEL = "m3c3-auth-sentinel-do-not-emit"
_MESSAGE_SENTINEL = "m3c3-message-sentinel-do-not-emit"
_URL_SENTINEL = "m3c3-url-identifier-sentinel-do-not-emit"
_MESSAGE_ID_SENTINEL = "m3c3-message-id-sentinel-do-not-emit"
_PROVIDER_SENTINEL = "m3c3-provider-value-sentinel-do-not-emit"
_BUCKET_SENTINEL = "m3c3-bucket-sentinel-do-not-emit"
_RESPONSE_BODY_SENTINEL = "m3c3-response-body-sentinel-do-not-emit"
_EXCEPTION_SENTINEL = "m3c3-exception-message-sentinel-do-not-emit"
_SENSITIVE_SENTINELS = (
    _AUTH_SENTINEL,
    _MESSAGE_SENTINEL,
    _URL_SENTINEL,
    _MESSAGE_ID_SENTINEL,
    _PROVIDER_SENTINEL,
    _BUCKET_SENTINEL,
    _RESPONSE_BODY_SENTINEL,
    _EXCEPTION_SENTINEL,
)


def assert_only_allowlisted_fields(events: list[dict[str, object]]) -> None:
    assert events
    for event in events:
        kind = event.get("kind")
        assert isinstance(kind, str)
        allowed = _ALLOWED_DIAGNOSTIC_FIELDS.get(kind)
        assert allowed is not None, f"unexpected diagnostics event kind: {kind}"
        assert set(event).issubset(allowed), f"unexpected fields for {kind}: {set(event) - allowed}"


def assert_no_sensitive_sentinels(events: object) -> None:
    serialized = json.dumps(events, ensure_ascii=False, sort_keys=True)
    for sentinel in _SENSITIVE_SENTINELS:
        assert sentinel not in serialized


async def emit_http_response(
    diagnostics: DiscordDiagnostics,
    clock: FakeClock,
    *,
    method: str,
    url: str,
    response_at: float,
    status: int,
    response_headers: dict[str, str],
    request_headers: dict[str, str] | None = None,
) -> None:
    trace = diagnostics.trace_config
    context = trace.trace_config_ctx()
    await trace.on_request_start.send(
        cast(Any, None),
        context,
        TraceRequestStartParams(
            method,
            URL(url),
            CIMultiDict(request_headers or {}),
        ),
    )
    clock.value = response_at
    response = SimpleNamespace(status=status, headers=CIMultiDict(response_headers))
    await trace.on_request_end.send(
        cast(Any, None),
        context,
        TraceRequestEndParams(method, URL(url), CIMultiDict(), cast(Any, response)),
    )


async def emit_http_exception(
    diagnostics: DiscordDiagnostics,
    clock: FakeClock,
    *,
    method: str,
    url: str,
    exception: BaseException,
    response_at: float,
    request_headers: dict[str, str] | None = None,
) -> None:
    trace = diagnostics.trace_config
    context = trace.trace_config_ctx()
    await trace.on_request_start.send(
        cast(Any, None),
        context,
        TraceRequestStartParams(
            method,
            URL(url),
            CIMultiDict(request_headers or {}),
        ),
    )
    clock.value = response_at
    await trace.on_request_exception.send(
        cast(Any, None),
        context,
        TraceRequestExceptionParams(method, URL(url), CIMultiDict(), exception),
    )


def test_disabled_instrumentation_preserves_existing_sink_behavior() -> None:
    async def scenario() -> None:
        channel = FakeChannel()
        metrics = TransportMetrics()
        sink = DiscordMessageSink(cast(Any, channel), metrics=metrics)

        message = await sink.create("first")
        await sink.edit(message, "second")

        assert channel.messages[0].content == "second"
        assert metrics.send_latencies_s and metrics.edit_latencies_s

    asyncio.run(scenario())


def test_enabled_diagnostics_use_discord_pys_supported_http_trace_constructor_hook() -> None:
    diagnostics = DiscordDiagnostics()
    edge = DiscordTextEdge(diagnostics=diagnostics)
    assert edge.client.http.http_trace is diagnostics.trace_config


def test_enabled_trace_records_safe_post_patch_headers_and_post_response_gap() -> None:
    async def scenario() -> list[dict[str, object]]:
        clock = FakeClock()
        emitted: list[dict[str, object]] = []
        diagnostics = DiscordDiagnostics(
            clock=clock,
            emit=lambda event: emitted.append(dict(event)),
        )
        diagnostics.trace_config.freeze()

        with diagnostics.operation("POST message"):
            clock.value = 0.10
            await emit_http_response(
                diagnostics,
                clock,
                method="POST",
                url="https://discord.com/api/v10/channels/channel-123/messages",
                response_at=0.35,
                status=200,
                response_headers={
                    "X-RateLimit-Limit": "5",
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset-After": "1.25",
                    "X-RateLimit-Bucket": "bucket-secret-value",
                    "Retry-After": "0.75",
                },
                request_headers={
                    "Authorization": "Bot token-secret-value",
                    "X-Request-Body": "message-content-secret-value",
                },
            )
            clock.value = 0.90

        with diagnostics.operation("PATCH message"):
            clock.value = 1.00
            await emit_http_response(
                diagnostics,
                clock,
                method="PATCH",
                url="https://discord.com/api/v10/channels/channel-123/messages/message-456",
                response_at=1.20,
                status=429,
                response_headers={"Retry-After": "2.5"},
            )
            clock.value = 1.25

        assert emitted == diagnostics.events
        return diagnostics.events

    events = asyncio.run(scenario())
    assert_only_allowlisted_fields(events)
    responses = [event for event in events if event["kind"] == "http_response"]
    assert [event["operation"] for event in responses] == ["POST message", "PATCH message"]
    assert responses[0]["response_status"] == 200
    assert responses[0]["rate_limit_limit"] == 5
    assert responses[0]["rate_limit_remaining"] == 0
    assert responses[0]["rate_limit_reset_after_s"] == 1.25
    assert responses[0]["retry_after_s"] == 0.75
    assert isinstance(responses[0]["rate_limit_bucket_hash"], str)
    assert responses[1]["response_status"] == 429
    assert responses[1]["rate_limited"] is True
    assert responses[1]["retry_after_s"] == 2.5

    operation_returns = [event for event in events if event["kind"] == "operation_return"]
    assert operation_returns[0]["await_duration_s"] == pytest.approx(0.9)
    assert operation_returns[0]["post_response_gap_s"] == pytest.approx(0.55)
    assert operation_returns[1]["post_response_gap_s"] == pytest.approx(0.05)

    serialized = json.dumps(events, sort_keys=True)
    assert "token-secret-value" not in serialized
    assert "message-content-secret-value" not in serialized
    assert "https://discord.com" not in serialized
    assert "bucket-secret-value" not in serialized


def test_all_diagnostic_event_paths_use_allowlists_and_drop_sensitive_sentinels() -> None:
    async def scenario() -> tuple[list[dict[str, object]], list[dict[str, object]], bool]:
        clock = FakeClock()
        emitted: list[dict[str, object]] = []
        diagnostics = DiscordDiagnostics(
            clock=clock,
            emit=lambda event: emitted.append(dict(event)),
        )
        diagnostics.trace_config.freeze()

        with diagnostics.operation("POST message"):
            clock.value = 0.1
            await emit_http_response(
                diagnostics,
                clock,
                method="POST",
                url=(f"https://discord.com/api/v10/channels/{_URL_SENTINEL}/messages"),
                response_at=0.3,
                status=200,
                response_headers={
                    "X-RateLimit-Limit": "5",
                    "X-RateLimit-Remaining": "4",
                    "X-RateLimit-Reset-After": "1.25",
                    "X-RateLimit-Bucket": _BUCKET_SENTINEL,
                    "Retry-After": "0.75",
                    "X-Response-Body": _RESPONSE_BODY_SENTINEL,
                },
                request_headers={
                    "Authorization": f"Bot {_AUTH_SENTINEL}",
                    "X-Request-Body": _MESSAGE_SENTINEL,
                    "X-Provider-Value": _PROVIDER_SENTINEL,
                },
            )
            clock.value = 0.4

        with diagnostics.operation("DELETE message"):
            clock.value = 0.5
            await emit_http_exception(
                diagnostics,
                clock,
                method="DELETE",
                url=(
                    "https://discord.com/api/v10/channels/"
                    f"{_URL_SENTINEL}/messages/{_MESSAGE_ID_SENTINEL}"
                ),
                response_at=0.6,
                exception=RuntimeError(_EXCEPTION_SENTINEL),
                request_headers={"Authorization": f"Bot {_AUTH_SENTINEL}"},
            )
            clock.value = 0.7

        message = FakeMessage(_MESSAGE_SENTINEL)
        sink = DiscordMessageSink(cast(Any, FakeChannel()), diagnostics=diagnostics, clock=clock)
        await sink.delete(message)

        with diagnostics.presenter_flush(chunk_count=1, force=True):
            clock.value = 0.8
        with (
            pytest.raises(RuntimeError, match=_EXCEPTION_SENTINEL),
            diagnostics.presenter_flush(chunk_count=2, force=False),
        ):
            raise RuntimeError(_EXCEPTION_SENTINEL)
        diagnostics.semantic_selection(
            boundary="newline",
            raw_chars=80,
            visible_chars=48,
            hidden_tail_chars=32,
            hidden_tail_age_s=0.1,
            lookahead_s=0.02,
            max_lookahead_s=0.125,
            preview_repaired=True,
            published=False,
        )
        with diagnostics.presentation_context("allowlisted-core-run"):
            diagnostics.presentation_outcome(
                status="failed",
                reason="presenter_error",
                flush_count=2,
                terminal_flush_count=1,
            )
        return diagnostics.events, emitted, message.deleted

    events, emitted, deleted = asyncio.run(scenario())
    assert deleted
    assert emitted == events
    assert {event["kind"] for event in events} == set(_ALLOWED_DIAGNOSTIC_FIELDS)
    assert_only_allowlisted_fields(events)
    assert_no_sensitive_sentinels(events)


def test_presenter_diagnostics_drop_response_text_through_the_real_sink_path() -> None:
    async def scenario() -> tuple[list[dict[str, object]], FakeChannel]:
        clock = FakeClock()
        diagnostics = DiscordDiagnostics(clock=clock)
        channel = FakeChannel()
        presenter = ReplyPresenter(
            DiscordMessageSink(
                cast(Any, channel),
                diagnostics=diagnostics,
                clock=clock,
            ),
            edit_interval_s=0.0,
            clock=clock,
            diagnostics=diagnostics,
            semantic_streaming=True,
            semantic_lookahead_s=0.0,
        )
        presenter.append_delta(_MESSAGE_SENTINEL)
        assert await presenter.flush()
        await presenter.shutdown()
        return diagnostics.events, channel

    events, channel = asyncio.run(scenario())
    assert channel.messages[0].content == _MESSAGE_SENTINEL
    assert_only_allowlisted_fields(events)
    assert_no_sensitive_sentinels(events)


def test_environment_diagnostics_emit_allowlisted_jsonl_to_stderr_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(DISCORD_HTTP_DIAGNOSTICS_ENV, "1")
    clock = FakeClock()
    diagnostics = diagnostics_from_environment(clock=clock)
    assert diagnostics is not None
    diagnostics.trace_config.freeze()

    with diagnostics.operation("POST message"):
        clock.value = 0.1
        asyncio.run(
            emit_http_response(
                diagnostics,
                clock,
                method="POST",
                url=(f"https://discord.com/api/v10/channels/{_URL_SENTINEL}/messages"),
                response_at=0.2,
                status=200,
                response_headers={
                    "X-RateLimit-Bucket": _BUCKET_SENTINEL,
                    "X-Response-Body": _RESPONSE_BODY_SENTINEL,
                },
                request_headers={
                    "Authorization": f"Bot {_AUTH_SENTINEL}",
                    "X-Request-Body": _MESSAGE_SENTINEL,
                    "X-Provider-Value": _PROVIDER_SENTINEL,
                },
            )
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    emitted = [cast(dict[str, object], json.loads(line)) for line in captured.err.splitlines()]
    assert emitted
    assert diagnostics.events == []
    assert_only_allowlisted_fields(emitted)
    assert_no_sensitive_sentinels(emitted)


def test_absent_and_invalid_rate_limit_headers_are_safe() -> None:
    async def scenario() -> list[dict[str, object]]:
        clock = FakeClock()
        diagnostics = DiscordDiagnostics(clock=clock)
        diagnostics.trace_config.freeze()
        await emit_http_response(
            diagnostics,
            clock,
            method="PATCH",
            url="https://discord.com/api/v10/channels/c/messages/m",
            response_at=0.1,
            status=204,
            response_headers={
                "X-RateLimit-Limit": "not-an-int",
                "X-RateLimit-Remaining": "",
                "X-RateLimit-Reset-After": "nan",
                "Retry-After": "not-a-number",
            },
        )
        return diagnostics.events

    response = next(event for event in asyncio.run(scenario()) if event["kind"] == "http_response")
    assert response["rate_limit_limit"] is None
    assert response["rate_limit_remaining"] is None
    assert response["rate_limit_reset_after_s"] is None
    assert response["rate_limit_bucket_hash"] is None
    assert response["retry_after_s"] is None


def test_presenter_flushes_correlate_without_duplicate_edits_or_concurrency_changes() -> None:
    async def scenario() -> tuple[list[dict[str, object]], FakeChannel]:
        clock = FakeClock()
        diagnostics = DiscordDiagnostics(clock=clock)
        channel = FakeChannel()
        presenter = ReplyPresenter(
            DiscordMessageSink(
                cast(Any, channel),
                diagnostics=diagnostics,
                clock=clock,
            ),
            edit_interval_s=0.2,
            clock=clock,
            diagnostics=diagnostics,
        )

        presenter.append_delta("a")
        assert await presenter.flush(force=True)
        presenter.append_delta("b")
        assert await presenter.flush(force=True)
        presenter.append_delta("c")
        await presenter.complete(presenter.text)
        await presenter.shutdown()
        return diagnostics.events, channel

    events, channel = asyncio.run(scenario())
    assert len(channel.messages) == 1
    assert channel.messages[0].edits == ["ab", "abc"]

    flush_starts = [event for event in events if event["kind"] == "presenter_flush_start"]
    flush_returns = [event for event in events if event["kind"] == "presenter_flush_return"]
    operation_starts = [event for event in events if event["kind"] == "operation_start"]
    operation_returns = [event for event in events if event["kind"] == "operation_return"]
    assert len(flush_starts) == len(flush_returns) == 3
    assert [event["operation"] for event in operation_starts] == [
        "POST message",
        "PATCH message",
        "PATCH message",
    ]
    assert [event["presenter_flush_id"] for event in operation_starts] == [
        flush_starts[0]["flush_id"],
        flush_starts[1]["flush_id"],
        flush_starts[2]["flush_id"],
    ]
    assert all(event["outcome"] == "returned" for event in operation_returns)
    assert all(event["http_response_count"] == 0 for event in operation_returns)


def test_core_run_context_joins_flush_operation_and_http_request_without_leaking() -> None:
    async def scenario() -> list[dict[str, object]]:
        clock = FakeClock()
        diagnostics = DiscordDiagnostics(clock=clock)
        diagnostics.trace_config.freeze()

        with (
            diagnostics.presentation_context("core-run-1"),
            diagnostics.presenter_flush(chunk_count=1, force=True) as flush_id,
            diagnostics.operation("POST message") as operation_id,
        ):
            clock.value = 0.1
            await emit_http_response(
                diagnostics,
                clock,
                method="POST",
                url=(f"https://discord.com/api/v10/channels/{_MESSAGE_ID_SENTINEL}/messages"),
                response_at=0.2,
                status=200,
                response_headers={},
            )
            diagnostics.presentation_outcome(
                status="completed",
                reason=None,
                flush_count=1,
                terminal_flush_count=1,
            )

        events = diagnostics.events
        flush_start = next(event for event in events if event["kind"] == "presenter_flush_start")
        operation_start = next(event for event in events if event["kind"] == "operation_start")
        request_start = next(event for event in events if event["kind"] == "http_request_start")
        response = next(event for event in events if event["kind"] == "http_response")
        outcome = next(event for event in events if event["kind"] == "presentation_outcome")

        assert flush_start["flush_id"] == flush_id
        assert operation_start["operation_id"] == operation_id
        assert operation_start["presenter_flush_id"] == flush_id
        assert request_start["operation_id"] == operation_id
        assert request_start["presenter_flush_id"] == flush_id
        assert response["request_id"] == request_start["request_id"]
        assert response["operation_id"] == operation_id
        assert response["presenter_flush_id"] == flush_id
        assert outcome["status"] == "completed"
        assert all(event.get("core_run_id") == "core-run-1" for event in events)
        assert_only_allowlisted_fields(events)
        assert_no_sensitive_sentinels(events)
        return events

    asyncio.run(scenario())


def test_core_run_context_isolated_between_sequential_presentations() -> None:
    diagnostics = DiscordDiagnostics()

    with diagnostics.presentation_context("core-run-a"):
        with diagnostics.presenter_flush(chunk_count=1, force=True):
            pass
        diagnostics.presentation_outcome(
            status="interrupted",
            reason="superseded",
            flush_count=1,
            terminal_flush_count=1,
        )
    with diagnostics.presentation_context("core-run-b"):
        with diagnostics.presenter_flush(chunk_count=1, force=True):
            pass
        diagnostics.presentation_outcome(
            status="completed",
            reason=None,
            flush_count=1,
            terminal_flush_count=1,
        )

    correlated = [
        event
        for event in diagnostics.events
        if event["kind"] in {"presenter_flush_start", "presenter_flush_return"}
    ]
    assert [event["core_run_id"] for event in correlated] == [
        "core-run-a",
        "core-run-a",
        "core-run-b",
        "core-run-b",
    ]
    outcomes = [event for event in diagnostics.events if event["kind"] == "presentation_outcome"]
    assert [(event["core_run_id"], event["status"], event["reason"]) for event in outcomes] == [
        ("core-run-a", "interrupted", "superseded"),
        ("core-run-b", "completed", None),
    ]
    assert_only_allowlisted_fields(diagnostics.events)


def test_429_is_observed_without_swallowing_existing_sink_exception() -> None:
    async def scenario() -> tuple[list[dict[str, object]], list[RateLimitObservation]]:
        clock = FakeClock()
        diagnostics = DiscordDiagnostics(clock=clock)
        metrics = TransportMetrics()
        response = SimpleNamespace(
            status=429,
            reason="Too Many Requests",
            headers={"Retry-After": "1.5"},
        )
        error = discord.HTTPException(cast(Any, response), {"retry_after": 1.5})

        class FailingMessage:
            async def edit(self, **kwargs: Any) -> Any:
                del kwargs
                raise error

        sink = DiscordMessageSink(
            cast(Any, SimpleNamespace()),
            metrics=metrics,
            diagnostics=diagnostics,
            clock=clock,
        )
        try:
            await sink.edit(FailingMessage(), "not recorded")
        except discord.HTTPException as raised:
            assert raised is error
        else:
            raise AssertionError("expected discord.HTTPException")
        return diagnostics.events, list(metrics.rate_limits)

    events, rate_limits = asyncio.run(scenario())
    assert [event["kind"] for event in events] == ["operation_start", "operation_return"]
    assert events[-1]["outcome"] == "raised"
    assert len(rate_limits) == 1
    assert rate_limits[0].operation == "edit"
    assert rate_limits[0].retry_after_s == 1.5
