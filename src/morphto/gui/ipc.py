"""Single-instance IPC. Kept free of heavy imports: a second launch (e.g. from the Explorer menu)
only needs to hand its files to the running app and exit as fast as possible."""

from __future__ import annotations

import getpass
import json

from morphto import APP_NAME
from morphto.platform import pipe

SERVER_NAME = f"{APP_NAME}-{getpass.getuser()}"


def forward(files: list[str], show: bool) -> bool:
    """Send files to an already running instance; False if none is running."""
    payload = json.dumps({"files": files, "show": show}).encode("utf-8")
    return pipe.send(SERVER_NAME, payload)


def decode(message: bytes) -> tuple[list[str], bool]:
    try:
        data = json.loads(message.decode("utf-8"))
        return [str(f) for f in data.get("files", [])], bool(data.get("show", True))
    except (ValueError, AttributeError):
        return [], False
