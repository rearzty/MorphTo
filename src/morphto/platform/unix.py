"""What Linux and macOS share: single instance (a file lock), IPC over a Unix socket, quitting on signals."""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path


def runtime_dir() -> Path:
    """Per-user folder for sockets and locks: $XDG_RUNTIME_DIR on Linux, the per-user temp folder on a Mac."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    path = Path(base) if base and os.path.isdir(base) else Path(tempfile.gettempdir())
    return path


def socket_path(name: str) -> Path:
    return runtime_dir() / f"{name}.sock"


_lock_file = None


def acquire_single_instance(name: str) -> bool:
    """True if we are the only instance. The lock is held until the process exits."""
    global _lock_file
    import fcntl

    path = runtime_dir() / f"{name}.lock"
    try:
        f = path.open("a")
    except OSError:
        return True  # cannot tell; better to run than to refuse
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _lock_file = f
    return True


def send(name: str, data: bytes, timeout_ms: int = 300) -> bool:
    """Deliver ``data`` to the running instance; False if none is listening."""
    path = socket_path(name)
    if not path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_ms / 1000)
            s.connect(str(path))
            s.sendall(data)
            s.shutdown(socket.SHUT_WR)
            s.recv(1)  # wait until the server has read everything
        return True
    except OSError:
        return False


class PipeServer:
    """Same interface as the Windows named-pipe server: calls ``callback(message)`` from its own thread."""

    def __init__(self, name: str, callback: Callable[[bytes], None]) -> None:
        self.path = socket_path(name)
        self.callback = callback
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="morphto-ipc", daemon=True)

    def start(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()  # left over by a crash; we hold the instance lock, so it is ours
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        sock.listen(8)
        sock.settimeout(0.5)
        self._sock = sock
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(2)
        if self._sock is not None:
            self._sock.close()
        with contextlib.suppress(OSError):
            self.path.unlink()

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(2)
                chunks = []
                try:
                    while chunk := conn.recv(65536):
                        chunks.append(chunk)
                    conn.sendall(b"\0")
                except OSError:
                    continue
            if chunks:
                self.callback(b"".join(chunks))


def on_console_close(callback: Callable[[], None]) -> None:
    """Ctrl+C or a terminal hang-up quits cleanly (the callback runs on a worker thread)."""

    def handler(_signum, _frame) -> None:
        threading.Thread(target=callback, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, handler)


def gui_command() -> list[str]:
    """Command line that starts the GUI: the AppImage, the app bundle's binary, or Python from source."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return [appimage]
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "morphto.gui"]


def run_detached(args: list[str]) -> None:
    with contextlib.suppress(OSError):
        subprocess.Popen(args, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def set_app_user_model_id(app_id: str) -> None:
    """Windows-only concept; nothing to do here."""


def allow_foreground() -> None:
    pass
