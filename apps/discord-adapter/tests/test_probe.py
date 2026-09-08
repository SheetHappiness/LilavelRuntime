"""Deterministic Stage A availability reporting."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from lilavel_discord_edge import probe as probe_module
from lilavel_discord_edge import run_transport_probe


class FakeWebSocket:
    def __init__(self, close_calls: list[str]) -> None:
        self._close_calls = close_calls
        self.open = True

    async def close(self, code: int = 4000) -> None:
        del code
        self._close_calls.append("websocket")
        self.open = False


def patch_offline_client(client: discord.Client, close_calls: list[str]) -> FakeWebSocket:
    async def connection_close() -> None:
        close_calls.append("connection")

    async def http_close() -> None:
        close_calls.append("http")

    websocket = FakeWebSocket(close_calls)
    client_any = cast(Any, client)
    client_any._connection.close = connection_close
    client_any.http.close = http_close
    client_any.ws = websocket
    return websocket


def install_offline_start(client: discord.Client, connect: Any) -> None:
    async def login(_token: str) -> None:
        await cast(Any, client)._async_setup_hook()

    client_any = cast(Any, client)
    client_any.login = login
    client_any.connect = connect


def offline_dm(client: discord.Client) -> Any:
    client_any = cast(Any, client)
    channel = discord.DMChannel(
        me=cast(Any, None),
        state=client_any._connection,
        data=cast(
            Any,
            {
                "id": "1",
                "type": 1,
                "name": "dm",
                "recipients": [],
                "last_message_id": None,
            },
        ),
    )
    return SimpleNamespace(
        channel=channel,
        author=SimpleNamespace(bot=False, id="human"),
    )


def new_probe(client: discord.Client, shutdown_timeout_s: float) -> Any:
    probe_type_name = "_LiveTransportProbe"
    probe_type = getattr(probe_module, probe_type_name)
    return probe_type(client=client, shutdown_timeout_s=shutdown_timeout_s)


def test_transport_probe_is_explicitly_blocked_without_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pytest's monkeypatch fixture is intentionally kept at the test boundary;
    # the probe itself only reads the namespaced environment variable.
    monkeypatch.delenv("LILAVEL_DISCORD_BOT_TOKEN", raising=False)

    report = asyncio.run(run_transport_probe(timeout_s=0.1))

    assert report == {
        "status": "BLOCKED",
        "reason": "missing LILAVEL_DISCORD_BOT_TOKEN",
        "live_probe": False,
    }


def test_previous_probe_race_left_shutdown_false_before_event_callback_settled() -> None:
    async def scenario() -> dict[str, Any]:
        client = discord.Client(intents=discord.Intents.none())
        close_calls: list[str] = []
        patch_offline_client(client, close_calls)
        state: dict[str, Any] = {
            "result": {"status": "PASS", "shutdown_clean": False},
            "callback_after_close": False,
        }

        async def on_message(_payload: object) -> None:
            state["result"] = {"status": "PASS", "shutdown_clean": False}
            await client.close()
            state["callback_after_close"] = True
            state["result"]["shutdown_clean"] = client.is_closed()

        client.event(on_message)

        async def connect(*, reconnect: bool) -> None:
            del reconnect
            client.dispatch("message", object())
            while not client.is_closed():
                await asyncio.sleep(0)

        install_offline_start(client, connect)
        try:
            await client.start("placeholder", reconnect=True)
        finally:
            current = asyncio.current_task()
            state["pending_task_details"] = sorted(
                (task.get_name(), task.get_coro().__qualname__)
                for task in asyncio.all_tasks()
                if task is not current and not task.done()
            )
            if not client.is_closed():
                await client.close()
        state["close_calls"] = close_calls
        return state

    state = asyncio.run(scenario())

    assert state["result"]["shutdown_clean"] is False
    assert state["callback_after_close"] is False
    assert any(
        name == "discord.py: on_message" and coroutine == "Client._run_event"
        for name, coroutine in state["pending_task_details"]
    )
    assert any(
        coroutine == "Client.close.<locals>._close"
        for _, coroutine in state["pending_task_details"]
    )
    assert state["close_calls"] == ["connection", "websocket", "http"]


def test_probe_owner_waits_for_event_callback_before_evaluating_shutdown() -> None:
    async def scenario() -> tuple[dict[str, Any], bool, list[str], bool]:
        client = discord.Client(intents=discord.Intents.none())
        close_calls: list[str] = []
        websocket = patch_offline_client(client, close_calls)
        probe = new_probe(client, shutdown_timeout_s=0.1)
        callback_task: asyncio.Task[Any] | None = None

        async def exercise(_message: Any) -> None:
            nonlocal callback_task
            callback_task = asyncio.current_task()
            probe.result = {"status": "PASS"}

        probe._exercise = exercise

        async def connect(*, reconnect: bool) -> None:
            del reconnect
            client.dispatch("message", offline_dm(client))
            while not client.is_closed():
                await asyncio.sleep(0)

        install_offline_start(client, connect)
        report = await probe.run("placeholder")
        assert callback_task is not None
        return report, callback_task.done(), close_calls, websocket.open

    report, callback_done, close_calls, websocket_open = asyncio.run(scenario())

    assert report["status"] == "PASS"
    assert report["shutdown_clean"] is True
    assert report["shutdown_client_closed"] is True
    assert report["shutdown_client_close_completed"] is True
    assert report["shutdown_start_task_settled"] is True
    assert report["shutdown_start_task_completed_normally"] is True
    assert report["shutdown_event_callback_settled"] is True
    assert report["shutdown_websocket_closed"] is True
    assert callback_done
    assert close_calls == ["connection", "websocket", "http"]
    assert websocket_open is False


def test_probe_does_not_report_clean_shutdown_when_start_task_cannot_settle() -> None:
    async def scenario() -> tuple[dict[str, Any], bool]:
        client = discord.Client(intents=discord.Intents.none())
        close_calls: list[str] = []
        patch_offline_client(client, close_calls)
        probe = new_probe(client, shutdown_timeout_s=0.05)
        never = asyncio.Event()

        async def exercise(_message: Any) -> None:
            probe.result = {"status": "PASS"}

        probe._exercise = exercise

        async def connect(*, reconnect: bool) -> None:
            del reconnect
            client.dispatch("message", offline_dm(client))
            await never.wait()

        install_offline_start(client, connect)
        report = await probe.run("placeholder")
        start_task = probe._start_task
        assert start_task is not None
        return report, start_task.done()

    report, start_task_done = asyncio.run(scenario())

    assert report["status"] == "PASS"
    assert report["shutdown_clean"] is False
    assert report["shutdown_client_close_completed"] is True
    assert report["shutdown_start_task_settled"] is True
    assert report["shutdown_start_task_completed_normally"] is False
    assert report["shutdown_event_callback_settled"] is True
    assert start_task_done
