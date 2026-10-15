"""Clipboard: the Win32 implementation on Windows, Qt's everywhere else."""

import sys

if sys.platform == "win32":
    from morphto.platform.win_clipboard import (
        COPY_IN_GUI_THREAD,
        ClipboardBusy,
        ClipInfo,
        has_content,
        peek,
        read,
        write_files,
    )
else:
    from morphto.platform.qt_clipboard import (
        COPY_IN_GUI_THREAD,
        ClipboardBusy,
        ClipInfo,
        has_content,
        peek,
        read,
        write_files,
    )

__all__ = ["COPY_IN_GUI_THREAD", "ClipInfo", "ClipboardBusy", "has_content", "peek", "read", "write_files"]
