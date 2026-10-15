"""Qt (PySide6) tray app and popup."""

from __future__ import annotations

import sys
import time
from pathlib import Path


def main() -> None:
    """Entry point of the windowed app (``morphtow`` / ``python -m morphto.gui``)."""
    args = sys.argv[1:]
    background = "--background" in args
    files = [str(Path(a).resolve()) for a in args if not a.startswith("--")]
    sys.exit(launch(files, background))


def launch(files: list[str], background: bool = False) -> int:
    """Hand the files to the running instance, or become the instance. Never runs two copies."""
    from morphto.gui.ipc import SERVER_NAME, forward
    from morphto.platform import system

    show = bool(files) or not background
    if forward(files, show):
        return 0
    if not system.acquire_single_instance(SERVER_NAME):
        # Another instance is starting up or busy: keep trying to reach it for a few seconds.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if forward(files, show):
                return 0
            time.sleep(0.2)
        return 1
    _preload()
    from morphto.gui.app import run

    return run(files, background)


def _preload() -> None:
    """Import every converter and Pillow plugin now, before Qt and our threads start.

    The popup then opens files without a first-use delay, and native extension modules are never
    loaded for the first time while the GUI is running (that crashed the compiled build).
    """
    from PIL import Image

    from morphto.core.registry import default_registry

    default_registry()
    Image.init()
