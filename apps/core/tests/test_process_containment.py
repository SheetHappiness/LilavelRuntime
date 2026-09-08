"""Windows process-tree containment tests for the real npx -> Bun launcher."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import pytest

from lilavel_core import (
    CancellationTimeout,
    GenerationAccepted,
    ModelRequest,
    ModelRuntime,
    TextDelta,
)
from lilavel_core.process_containment import (
    ProcessContainment,
    ProcessContainmentError,
    attach_process_containment,
    process_creation_flags,
)

FIXTURE = Path(__file__).parent / "fixtures" / "windows_npx_sidecar.ts"
REPO_ROOT = FIXTURE.parents[4]


def test_process_creation_flags_preserve_containment_and_isolate_console_interrupts() -> None:
    flags = process_creation_flags()
    if os.name == "nt":
        assert flags & 0x00000004  # CREATE_SUSPENDED
        assert flags & 0x00000200  # CREATE_NEW_PROCESS_GROUP
    else:
        assert flags == 0


def _processes_with_marker(marker: str) -> list[dict[str, Any]]:
    """Return only launcher descendants; exclude this query's PowerShell shim."""

    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is required for the Windows process-tree assertion")
    script = (
        "$rows = @(Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine); "
        "$rows | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    output = completed.stdout.strip()
    if not output:
        return []
    parsed: Any = json.loads(output)
    rows: list[Any] = cast(list[Any], parsed) if isinstance(parsed, list) else [parsed]
    selected: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        typed_row = cast(dict[str, Any], row)
        if typed_row.get("Name") in {"cmd.exe", "node.exe", "bun.exe"}:
            selected.append(typed_row)
    return selected


def _wait_for_processes(marker: str, minimum: int, timeout: float = 10.0) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    rows: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        rows = _processes_with_marker(marker)
        if (minimum == 0 and not rows) or (minimum > 0 and len(rows) >= minimum):
            return rows
        time.sleep(0.1)
    return rows


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are Windows-only")
def test_real_npx_bun_tree_is_contained_when_root_exits_first() -> None:
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if npx is None:
        pytest.skip("npx is required for the real launcher containment probe")
    marker = f"LilavelWindowsContainment_{uuid.uuid4().hex}"
    command = [
        npx,
        "--yes",
        "bun@1.4.0",
        "-e",
        f"console.error('{marker}'); setInterval(()=>{{}}, 60000)",
    ]
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT / "apps" / "model-sidecar",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=process_creation_flags(),
    )
    containment: ProcessContainment | None = None
    try:
        containment = attach_process_containment(process, suspended=True)
        assert containment.contained
        tree = _wait_for_processes(marker, minimum=4)
        assert {row["Name"] for row in tree} >= {"cmd.exe", "node.exe", "bun.exe"}

        # Popen.terminate targets the root cmd.exe only.  The Job Object stays
        # open, so the npx/node/cmd/Bun descendants should remain observable
        # until the owner explicitly closes containment.
        process.terminate()
        process.wait(timeout=3.0)
        time.sleep(0.3)
        descendants = _processes_with_marker(marker)
        assert {row["Name"] for row in descendants} >= {"cmd.exe", "node.exe", "bun.exe"}

        assert containment.close()
        assert _wait_for_processes(marker, minimum=0, timeout=5.0) == []
    finally:
        if containment is not None:
            containment.terminate(timeout=3.0)
        elif process.poll() is None:
            process.kill()
        if process.poll() is None:
            process.wait(timeout=3.0)
        assert process.poll() is not None


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are Windows-only")
def test_containment_rejects_a_running_process_and_fails_closed() -> None:
    process = subprocess.Popen(
        [shutil.which("python") or "python", "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with pytest.raises(ProcessContainmentError):
            attach_process_containment(process, suspended=False)
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2.0)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are Windows-only")
def test_model_runtime_cancellation_deadline_contains_real_npx_bun_tree() -> None:
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if npx is None:
        pytest.skip("npx is required for the real launcher containment probe")
    model = ModelRuntime(
        command=(npx, "--yes", "bun@1.4.0", "run", str(FIXTURE)),
        sidecar_dir=FIXTURE.parent,
        startup_timeout=20.0,
        shutdown_timeout=3.0,
        cancellation_timeout=0.5,
        write_timeout=3.0,
    )
    try:
        model.start()
        tree = _wait_for_processes(FIXTURE.name, minimum=4, timeout=15.0)
        assert {row["Name"] for row in tree} >= {"cmd.exe", "node.exe", "bun.exe"}

        handle = model.generate(ModelRequest("hang"))
        stream = iter(handle)
        accepted = next(stream)
        delta = next(stream)
        assert isinstance(accepted, GenerationAccepted)
        assert accepted.generation_id == handle.generation_id
        assert isinstance(delta, TextDelta)
        assert delta.generation_id == handle.generation_id
        assert model.cancel(handle.generation_id)
        with pytest.raises(CancellationTimeout):
            handle.wait(timeout=3.0)
        assert model.health().state == "failed"

        # The cancellation watchdog must terminate the owned launcher tree,
        # including npx/node/cmd/Bun descendants, within the bounded deadline.
        assert _wait_for_processes(FIXTURE.name, minimum=0, timeout=5.0) == []
    finally:
        with suppress(Exception):
            model.shutdown()
        assert _wait_for_processes(FIXTURE.name, minimum=0, timeout=5.0) == []
