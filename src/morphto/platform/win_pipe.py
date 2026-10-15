"""Tiny named-pipe channel for single-instance IPC (a second launch hands its files to the running app).

Pure ctypes, no Qt: the sending side must start and exit as fast as possible.
"""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable
from ctypes import wintypes

GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
PIPE_ACCESS_INBOUND = 0x1
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_TYPE_BYTE, PIPE_WAIT, PIPE_REJECT_REMOTE_CLIENTS = 0x0, 0x0, 0x8
PIPE_UNLIMITED_INSTANCES = 255
ERROR_PIPE_BUSY, ERROR_PIPE_CONNECTED, ERROR_BROKEN_PIPE = 231, 535, 109
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateNamedPipeW.restype = wintypes.HANDLE
_k32.CreateNamedPipeW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
]  # fmt: skip
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]  # fmt: skip
_k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_k32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
_k32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
_k32.ReadFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
_k32.WriteFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]


def pipe_path(name: str) -> str:
    return rf"\\.\pipe\{name}"


def send(name: str, data: bytes, timeout_ms: int = 300) -> bool:
    """Deliver ``data`` to the server; False if nobody is listening."""
    path = pipe_path(name)
    for _ in range(2):
        handle = _k32.CreateFileW(path, GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
        if handle != INVALID_HANDLE_VALUE:
            break
        if ctypes.get_last_error() != ERROR_PIPE_BUSY or not _k32.WaitNamedPipeW(path, timeout_ms):
            return False
    else:
        return False
    try:
        written = wintypes.DWORD()
        view = ctypes.create_string_buffer(data, len(data))
        return bool(_k32.WriteFile(handle, view, len(data), ctypes.byref(written), None))
    finally:
        _k32.CloseHandle(handle)


class PipeServer:
    """Accepts one message per connection on a background thread and hands it to ``on_message``."""

    def __init__(self, name: str, on_message: Callable[[bytes], None]) -> None:
        self.name = name
        self.on_message = on_message
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="morphto-ipc", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        send(self.name, b"", 100)  # unblock ConnectNamedPipe
        self._thread.join(1)

    def _run(self) -> None:
        path = pipe_path(self.name)
        first = True
        while not self._stop.is_set():
            flags = PIPE_ACCESS_INBOUND | (FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0)
            handle = _k32.CreateNamedPipeW(
                path, flags, PIPE_TYPE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                PIPE_UNLIMITED_INSTANCES, 0, 1 << 16, 0, None,
            )  # fmt: skip
            if handle == INVALID_HANDLE_VALUE:
                return
            first = False
            try:
                connected = (
                    _k32.ConnectNamedPipe(handle, None) or ctypes.get_last_error() == ERROR_PIPE_CONNECTED
                )
                if connected and not self._stop.is_set():
                    message = _read_all(handle)
                    if message:
                        self.on_message(message)
            finally:
                _k32.DisconnectNamedPipe(handle)
                _k32.CloseHandle(handle)


def _read_all(handle: int) -> bytes:
    chunks = []
    buf = ctypes.create_string_buffer(1 << 16)
    read = wintypes.DWORD()
    while _k32.ReadFile(handle, buf, len(buf), ctypes.byref(read), None) and read.value:
        chunks.append(buf.raw[: read.value])
        if sum(len(c) for c in chunks) > (1 << 20):  # 1 MB is far more than any list of paths
            break
    return b"".join(chunks)
