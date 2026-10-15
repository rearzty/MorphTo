"""Linux integration: global hotkey, autostart, file manager menus.

The hotkey is grabbed through Xlib on X11. Wayland does not let applications grab keys, so on GNOME the
hotkey becomes a system keyboard shortcut that runs ``morphto`` (which hands over to the running app);
elsewhere on Wayland the user sets one in the system settings.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import select
import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from morphto import APP_NAME
from morphto.platform.hotkeys import split_hotkey
from morphto.platform.unix import (  # noqa: F401 (re-exported: the common platform interface)
    acquire_single_instance,
    allow_foreground,
    gui_command,
    on_console_close,
    run_detached,
    set_app_user_model_id,
)

CONTEXT_MENU_SUPPORTED = True


def _session_is_wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


def _desktop() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def _quote_cmd(args: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in args)


def _desktop_exec(args: list[str]) -> str:
    """Quote for the Exec= key of a .desktop file."""
    out = []
    for a in args:
        if any(c in a for c in " \t\n\"'\\><~|&;$*?#()`"):
            a = (
                '"'
                + a.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
                + '"'
            )
        out.append(a.replace("%", "%%"))
    return " ".join(out)


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


# ---- hotkey ----

_X_MODS = {"shift": 1 << 0, "ctrl": 1 << 2, "alt": 1 << 3, "win": 1 << 6}
_LOCK_MASK, _NUMLOCK_MASK = 1 << 1, 1 << 4
_KEYSYMS = {"space": "space", "insert": "Insert", "pause": "Pause", "`": "grave"}
_KEY_PRESS = 2


class _XKeyEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("pad", ctypes.c_long * 24)]


class HotkeyListener:
    """Global hotkey: Xlib key grab on X11; a desktop keyboard shortcut on GNOME under Wayland."""

    def __init__(self, hotkey: str, callback: Callable[[], None]) -> None:
        mods, key = split_hotkey(hotkey)
        if not mods or not key:
            raise ValueError(f"Invalid hotkey {hotkey!r}")
        self.hotkey = hotkey
        self._mods, self._key = mods, key
        self._callback = callback
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._ok = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if _session_is_wayland():
            return _set_gnome_shortcut(self._mods, self._key) if "gnome" in _desktop() else False
        if not os.environ.get("DISPLAY"):
            return False
        self._thread = threading.Thread(target=self._run_x11, name="morphto-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        return self._ok

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(2)

    def _run_x11(self) -> None:
        name = ctypes.util.find_library("X11")
        try:
            x = ctypes.CDLL(name or "libX11.so.6")
        except OSError:
            self._ready.set()
            return
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XStringToKeysym.restype = ctypes.c_ulong
        x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XGrabKey.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        x.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XPending.argtypes = [ctypes.c_void_p]
        x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        x.XConnectionNumber.argtypes = [ctypes.c_void_p]
        x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        error_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        x.XSetErrorHandler.argtypes = [error_type]
        x.XSetErrorHandler.restype = error_type

        display = x.XOpenDisplay(None)
        if not display:
            self._ready.set()
            return
        failed = []
        handler = error_type(lambda _d, _e: failed.append(1) or 0)  # BadAccess: someone else holds the key
        previous = x.XSetErrorHandler(handler)
        root = x.XDefaultRootWindow(display)
        keysym = x.XStringToKeysym(
            _KEYSYMS.get(self._key.lower(), self._key.lower() if len(self._key) == 1 else self._key).encode()
        )
        keycode = x.XKeysymToKeycode(display, keysym)
        mods = 0
        for m in self._mods:
            mods |= _X_MODS[m.lower()]
        variants = [mods | extra for extra in (0, _LOCK_MASK, _NUMLOCK_MASK, _LOCK_MASK | _NUMLOCK_MASK)]
        if keycode:
            for v in variants:
                x.XGrabKey(display, keycode, v, root, 1, 1, 1)  # owner_events, GrabModeAsync x2
            x.XSync(display, 0)
        self._ok = bool(keycode) and not failed
        self._ready.set()
        try:
            if self._ok:
                fd = x.XConnectionNumber(display)
                event = _XKeyEvent()
                while not self._stop.is_set():
                    select.select([fd], [], [], 0.5)
                    while x.XPending(display):
                        x.XNextEvent(display, ctypes.byref(event))
                        if event.type == _KEY_PRESS:
                            self._callback()
        finally:
            if keycode:
                for v in variants:
                    x.XUngrabKey(display, keycode, v, root)
            x.XSetErrorHandler(previous)
            x.XCloseDisplay(display)


_GNOME_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
_GNOME_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/morphto/"
_GNOME_KEY = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
_GNOME_MODS = {"ctrl": "<Control>", "alt": "<Alt>", "shift": "<Shift>", "win": "<Super>"}


def _set_gnome_shortcut(mods: list[str], key: str) -> bool:
    """Make the hotkey a GNOME custom shortcut that runs MorphTo (works under Wayland)."""
    gsettings = shutil.which("gsettings")
    if not gsettings:
        return False
    binding = "".join(_GNOME_MODS[m.lower()] for m in mods) + (key.lower() if len(key) == 1 else key)
    try:
        current = subprocess.run(
            [gsettings, "get", _GNOME_SCHEMA, "custom-keybindings"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        paths = (
            []
            if current.startswith("@as")
            else [p.strip(" '") for p in current.strip("[]").split(",") if p.strip()]
        )
        if _GNOME_PATH not in paths:
            paths.append(_GNOME_PATH)
            value = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
            subprocess.run([gsettings, "set", _GNOME_SCHEMA, "custom-keybindings", value], check=True)
        target = f"{_GNOME_KEY}:{_GNOME_PATH}"
        subprocess.run([gsettings, "set", target, "name", APP_NAME], check=True)
        subprocess.run([gsettings, "set", target, "command", _quote_cmd(gui_command())], check=True)
        subprocess.run([gsettings, "set", target, "binding", binding], check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


# ---- autostart ----


def _autostart_file() -> Path:
    return _config_home() / "autostart" / "morphto.desktop"


def set_autostart(enabled: bool) -> None:
    path = _autostart_file()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={APP_NAME}\nExec={_desktop_exec([*gui_command(), '--background'])}\n"
        "Icon=morphto\nTerminal=false\nX-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )


def autostart_enabled() -> bool:
    return _autostart_file().is_file()


# ---- file manager menus ----


def _menu_files() -> dict[str, Path]:
    data = _data_home()
    return {
        "nautilus": data / "nautilus" / "scripts" / APP_NAME,
        "nemo": data / "nemo" / "actions" / "morphto.nemo_action",
        "dolphin": data / "kio" / "servicemenus" / "morphto.desktop",
    }


def set_context_menu(enabled: bool, extensions: Iterable[str], label: str, icon: str | None = None) -> None:
    """ "Convert with MorphTo" in Nautilus (Scripts), Nemo and Dolphin."""
    files = _menu_files()
    if not enabled:
        for path in files.values():
            path.unlink(missing_ok=True)
        return
    command = gui_command()
    exts = sorted({e.lower() for e in extensions})
    entries = {
        "nautilus": f'#!/bin/sh\nexec {_quote_cmd(command)} "$@"\n',
        "nemo": (
            f"[Nemo Action]\nName={label}\nExec={_desktop_exec([*command, '%F'])}\n"
            f"Selection=notnone\nExtensions={';'.join(exts)};\nIcon-Name=morphto\n"
        ),
        "dolphin": (
            "[Desktop Entry]\nType=Service\nMimeType=application/octet-stream;all/allfiles;\n"
            "Actions=convert;\nX-KDE-ServiceTypes=KonqPopupMenu/Plugin\n\n"
            f"[Desktop Action convert]\nName={label}\nIcon=morphto\nExec={_desktop_exec([*command, '%F'])}\n"
        ),
    }
    for kind, path in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entries[kind], encoding="utf-8")
        path.chmod(0o755)  # Nautilus scripts and Dolphin service menus must be executable


# ---- files ----


def reveal(path: Path) -> None:
    """Open the file manager with the file selected (FileManager1 D-Bus API), or just its folder."""
    gdbus = shutil.which("gdbus")
    if gdbus:
        result = subprocess.run(
            [gdbus, "call", "--session", "--dest", "org.freedesktop.FileManager1",
             "--object-path", "/org/freedesktop/FileManager1", "--method",
             "org.freedesktop.FileManager1.ShowItems", f"['{path.resolve().as_uri()}']", ""],
            capture_output=True, check=False, timeout=5,
        )  # fmt: skip
        if result.returncode == 0:
            return
    open_file(path.parent if path.is_file() else path)


reveal_in_explorer = reveal


def open_file(path: Path) -> None:
    run_detached(["xdg-open", str(path)])


def force_foreground(hwnd: int) -> None:
    """Qt's activateWindow() is all a Linux window manager accepts; nothing more to do here."""
