"""Bounded process-tree ownership for the local sidecar.

Windows launches the configured sidecar through ``npx.cmd``.  A plain
``Popen.terminate`` only targets that command shim and can leave the npm,
``cmd.exe``, and Bun descendants running.  This module gives the runtime a
small ownership handle backed by a Windows Job Object.  The process must be
created suspended, so the job is assigned before the first instruction can
spawn a descendant.  It is also created as a separate console process group:
an operator's Ctrl+C must reach the Core owner so it can send the sidecar's
graceful shutdown command, rather than terminating the child in the middle
of a JSONL frame.

The module deliberately has no restart policy.  If job setup or the suspended
launch gate cannot be established, attaching containment fails and the caller
must fail closed instead of treating a parent-only process handle as safe.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from ctypes import wintypes
from typing import Any, Final, cast

_CREATE_SUSPENDED: Final = 0x00000004
_CREATE_NEW_PROCESS_GROUP: Final = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: Final = 9
_PROCESS_SET_QUOTA: Final = 0x0100
_PROCESS_TERMINATE: Final = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
_THREAD_SUSPEND_RESUME: Final = 0x0002
_TH32CS_SNAPTHREAD: Final = 0x00000004
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value or -1
_RESUME_THREAD_FAILED: Final = 0xFFFFFFFF


class ProcessContainmentError(OSError):
    """The runtime could not establish or use child-process containment."""


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


_KERNEL32: Any = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None


def _configure_kernel32(api: Any) -> None:
    """Set only the Win32 signatures used by this module."""

    api.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    api.SetInformationJobObject.restype = wintypes.BOOL
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    api.AssignProcessToJobObject.restype = wintypes.BOOL
    api.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    api.IsProcessInJob.restype = wintypes.BOOL
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    api.Thread32First.restype = wintypes.BOOL
    api.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    api.Thread32Next.restype = wintypes.BOOL
    api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenThread.restype = wintypes.HANDLE
    api.ResumeThread.argtypes = [wintypes.HANDLE]
    api.ResumeThread.restype = wintypes.DWORD
    api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateJobObject.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL


if _KERNEL32 is not None:
    _configure_kernel32(_KERNEL32)


def process_creation_flags() -> int:
    """Return suspended, Ctrl+C-isolated flags before attaching containment."""

    return (_CREATE_SUSPENDED | _CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0


def _win32_error(operation: str) -> ProcessContainmentError:
    get_last_error = cast(Callable[[], int], ctypes.__dict__["get_last_error"])
    error = get_last_error()
    return ProcessContainmentError(f"{operation} failed (Win32 error {error})")


def _require_kernel32() -> Any:
    if _KERNEL32 is None:
        raise ProcessContainmentError("Windows process containment is unavailable")
    return _KERNEL32


def _is_invalid_handle(handle: Any) -> bool:
    if handle is None:
        return True
    value = int(handle)
    return value == 0 or value == -1 or value == _INVALID_HANDLE_VALUE


def _close_handle(api: Any, handle: Any) -> bool:
    if not _is_invalid_handle(handle):
        try:
            return bool(api.CloseHandle(handle))
        except OSError:
            return False
    return True


def _terminate_after_attach_failure(process: subprocess.Popen[bytes]) -> None:
    with suppress(OSError):
        if process.poll() is None:
            process.kill()
    with suppress(OSError, subprocess.TimeoutExpired):
        process.wait(timeout=1.0)


def _resume_initial_thread(api: Any, process_id: int) -> None:
    """Resume the one primary thread created with CREATE_SUSPENDED.

    The process cannot execute user code while its initial thread is
    suspended.  Enumerating the thread snapshot before resuming it avoids the
    race inherent in assigning a running process to a Job Object.
    """

    snapshot = api.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if _is_invalid_handle(snapshot):
        raise _win32_error("CreateToolhelp32Snapshot")
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(_ThreadEntry32)
        thread_ids: list[int] = []
        has_entry = bool(api.Thread32First(snapshot, ctypes.byref(entry)))
        while has_entry:
            if int(entry.th32OwnerProcessID) == process_id:
                thread_ids.append(int(entry.th32ThreadID))
            has_entry = bool(api.Thread32Next(snapshot, ctypes.byref(entry)))
        if len(thread_ids) != 1:
            raise ProcessContainmentError(
                f"expected one suspended primary thread, found {len(thread_ids)}"
            )
        thread = api.OpenThread(_THREAD_SUSPEND_RESUME, False, thread_ids[0])
        if _is_invalid_handle(thread):
            raise _win32_error("OpenThread")
        try:
            previous_count = int(api.ResumeThread(thread))
            if previous_count in {0, _RESUME_THREAD_FAILED}:
                if previous_count == _RESUME_THREAD_FAILED:
                    raise _win32_error("ResumeThread")
                raise ProcessContainmentError("initial process thread was not suspended")
            if previous_count != 1:
                raise ProcessContainmentError(
                    f"initial process thread had unexpected suspend count {previous_count}"
                )
        finally:
            _close_handle(api, thread)
    finally:
        _close_handle(api, snapshot)


def _create_windows_job(process: subprocess.Popen[bytes], *, suspended: bool) -> int:
    if not suspended:
        raise ProcessContainmentError(
            "Windows sidecar containment requires CREATE_SUSPENDED process creation"
        )
    api = _require_kernel32()
    job = api.CreateJobObjectW(None, None)
    if _is_invalid_handle(job):
        raise _win32_error("CreateJobObjectW")
    try:
        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise _win32_error("SetInformationJobObject")

        process_handle = api.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            process.pid,
        )
        if _is_invalid_handle(process_handle):
            raise _win32_error("OpenProcess")
        try:
            if not api.AssignProcessToJobObject(job, process_handle):
                raise _win32_error("AssignProcessToJobObject")
            associated = wintypes.BOOL()
            if not api.IsProcessInJob(process_handle, job, ctypes.byref(associated)):
                raise _win32_error("IsProcessInJob")
            if not associated.value:
                raise ProcessContainmentError("process was not associated with its Job Object")
        finally:
            _close_handle(api, process_handle)

        _resume_initial_thread(api, process.pid)
        return int(job)
    except BaseException:
        _close_handle(api, job)
        _terminate_after_attach_failure(process)
        raise


class ProcessContainment:
    """Own a sidecar process and, on Windows, its descendant tree."""

    def __init__(self, process: subprocess.Popen[bytes], job_handle: int | None) -> None:
        self.process = process
        self._job_handle = job_handle
        self._closed = False
        self._lock = threading.Lock()

    @classmethod
    def attach(
        cls,
        process: subprocess.Popen[bytes],
        *,
        suspended: bool,
    ) -> ProcessContainment:
        """Attach containment and release a suspended process after assignment."""

        job_handle: int | None = None
        if os.name == "nt":
            try:
                job_handle = _create_windows_job(process, suspended=suspended)
            except ProcessContainmentError:
                _terminate_after_attach_failure(process)
                raise
            except BaseException as error:
                _terminate_after_attach_failure(process)
                raise ProcessContainmentError("could not establish process containment") from error
        return cls(process, job_handle)

    @property
    def contained(self) -> bool:
        """Whether this process has a live Windows Job Object association."""

        with self._lock:
            return self._job_handle is not None and not self._closed

    def terminate(self, timeout: float = 1.0) -> bool:
        """Terminate the owned tree and report whether the root exited in time."""

        if timeout <= 0:
            raise ValueError("termination timeout must be positive")
        with self._lock:
            if self._closed:
                return self.process.poll() is not None
            close_ok = True
            if self._job_handle is not None:
                api = _require_kernel32()
                try:
                    if not api.TerminateJobObject(self._job_handle, 1):
                        close_ok = False
                except OSError:
                    # Closing the job is still attempted below; its
                    # KILL_ON_JOB_CLOSE limit is the final containment edge.
                    close_ok = False
                finally:
                    close_ok = self._close_locked() and close_ok
            else:
                with suppress(OSError):
                    if self.process.poll() is None:
                        self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if self._job_handle is None:
                    with suppress(OSError):
                        self.process.kill()
                return False
            return close_ok

    def _close_locked(self) -> bool:
        if self._closed:
            return True
        if self._job_handle is not None:
            api = _require_kernel32()
            handle = self._job_handle
            if not _close_handle(api, handle):
                return False
            self._job_handle = None
        self._closed = True
        return True

    def close(self) -> bool:
        """Close the ownership handle; Windows closes kill the full tree."""

        with self._lock:
            return self._close_locked()


def attach_process_containment(
    process: subprocess.Popen[bytes],
    *,
    suspended: bool,
) -> ProcessContainment:
    """Public factory used by ``ModelRuntime`` after ``Popen``."""

    return ProcessContainment.attach(process, suspended=suspended)


__all__ = [
    "ProcessContainment",
    "ProcessContainmentError",
    "attach_process_containment",
    "process_creation_flags",
]
