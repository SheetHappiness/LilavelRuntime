"""Core integration coverage for the provider-neutral structured request."""

import os
import shutil
import sys
from pathlib import Path

import pytest

from lilavel_core import (
    ContextMessage,
    GenerationAccepted,
    GenerationCancelled,
    GenerationCompleted,
    ModelRequest,
    ModelRuntime,
    TextDelta,
)

FIXTURE = Path(__file__).parent / "fixtures" / "structured_sidecar.py"
BUN_FIXTURE = Path(__file__).parent / "fixtures" / "guided_sidecar.ts"


def runtime() -> ModelRuntime:
    return ModelRuntime(
        command=(sys.executable, str(FIXTURE)),
        sidecar_dir=Path(__file__).parents[1],
    )


def test_core_sends_structured_context_and_preserves_order() -> None:
    model = runtime()
    model.start()
    try:
        handle = model.generate(
            ModelRequest(
                messages=(
                    ContextMessage("user", "Remember the word quartz."),
                    ContextMessage("assistant", "ACK_1"),
                    ContextMessage("user", "What was the word?"),
                )
            )
        )
        assert handle.wait(2.0).text == "structured-continuity"
        assert [type(event) for event in handle] == [
            GenerationAccepted,
            TextDelta,
            GenerationCompleted,
        ]
    finally:
        model.shutdown()


def test_core_protocol_round_trip_preserves_trusted_guidance() -> None:
    model = runtime()
    model.start()
    try:
        handle = model.generate(
            ModelRequest(
                messages=(ContextMessage("user", "hello"),),
                system_prompt=("trusted identity",),
            )
        )
        assert handle.wait(2.0).text == "structured-guided"
    finally:
        model.shutdown()


@pytest.mark.skipif(os.name != "nt", reason="the pinned Windows launcher is not exercised here")
def test_core_to_bun_protocol_round_trip_preserves_trusted_guidance() -> None:
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if npx is None:
        pytest.skip("npx is required for the Core-to-Bun protocol probe")
    model = ModelRuntime(
        command=(npx, "--yes", "bun@1.4.0", "run", str(BUN_FIXTURE)),
        sidecar_dir=BUN_FIXTURE.parent,
        startup_timeout=20.0,
    )
    model.start()
    try:
        handle = model.generate(
            ModelRequest(
                messages=(ContextMessage("user", "hello"),),
                system_prompt=("trusted identity",),
            )
        )
        assert handle.wait(5.0).text == "GUIDANCE_WIRE_OK"
    finally:
        model.shutdown()


def test_structured_context_keeps_cancellation_recovery_and_stale_filtering() -> None:
    model = runtime()
    model.start()
    try:
        cancelled = model.generate(ModelRequest(messages=(ContextMessage("user", "cancel"),)))
        stream = iter(cancelled)
        assert isinstance(next(stream), GenerationAccepted)
        assert isinstance(next(stream), TextDelta)
        assert model.cancel(cancelled.generation_id)
        assert cancelled.wait(2.0).status == "cancelled"
        remaining = list(stream)
        assert any(isinstance(event, GenerationCancelled) for event in remaining)
        assert not any(
            isinstance(event, TextDelta) and event.delta == "STALE_LATE_DELTA"
            for event in remaining
        )

        recovery = model.generate(ModelRequest(messages=(ContextMessage("user", "recovery"),)))
        assert recovery.wait(2.0).text == "structured-recovered"
    finally:
        model.shutdown()
