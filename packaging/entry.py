"""Entry point of the packaged MorphTo.exe.

MorphTo.exe is a windowed app. The console front-end ``morphto.com`` (see cli_stub.c) starts it as
``MorphTo.exe --cli <args>`` with the console's handles, so the same binary serves both.
"""

import sys


def _crash_log() -> None:
    """A windowed app has nowhere to print a crash: keep the last one in %LOCALAPPDATA%\\MorphTo\\crash.log."""
    import faulthandler
    import os
    import time

    from morphto.paths import data_dir

    try:
        path = data_dir() / "crash.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 256 * 1024:
            path.unlink()
        log = open(path, "a", encoding="utf-8")  # noqa: SIM115 - must stay open for the process lifetime
        log.write(f"\n== {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} {' '.join(sys.argv[1:])}\n")
        log.flush()
        faulthandler.enable(log, all_threads=True)
    except OSError:
        pass


def _attach_console() -> None:
    """MorphTo.exe is a windowed app: in a real console window (cmd, Windows Terminal) it gets no usable
    stdout/stderr. Attach to the console of morphto.com (our parent) and open it directly."""
    if sys.stdout is not None and sys.stderr is not None and sys.stdin is not None:
        return  # redirected to pipes/files: Python already set them up
    import ctypes

    ATTACH_PARENT_PROCESS = -1
    if not ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
        return
    if sys.stdout is None:
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stdin is None:
        sys.stdin = open("CONIN$", encoding="utf-8")  # noqa: SIM115


def cleanup() -> None:
    """Called by the uninstaller: remove what MorphTo put into the registry (autostart, Explorer menu)."""
    from morphto.core.formats import all_formats
    from morphto.platform import windows

    windows.set_autostart(False)
    exts = [e for f in all_formats() for e in f.extensions]
    windows.set_context_menu(False, exts, "")


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--cli"]:
        _attach_console()
        from morphto.cli import main as cli_main

        cli_main(args[1:])
    elif args[:1] == ["--cleanup"]:
        cleanup()
    elif args[:1] == ["--self-test"] and len(args) >= 2:
        _crash_log()
        from morphto.gui.selftest import run

        sys.exit(run(*args[1:3]))
    else:
        _crash_log()
        from morphto.gui import main as gui_main

        gui_main()


if __name__ == "__main__":
    main()
