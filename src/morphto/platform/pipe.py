"""Single-instance IPC: a named pipe on Windows, a Unix socket elsewhere."""

import sys

if sys.platform == "win32":
    from morphto.platform.win_pipe import PipeServer, send
else:
    from morphto.platform.unix import PipeServer, send

__all__ = ["PipeServer", "send"]
