"""macOS integration: global hotkey (Carbon, needs no accessibility permission), login item, Finder.

The hotkey is registered with RegisterEventHotKey; its events arrive through the app's main event loop,
which Qt runs, so the callback is called on the GUI thread and start() must be called from it too.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import plistlib
from collections.abc import Callable, Iterable
from pathlib import Path

from morphto.platform.hotkeys import split_hotkey
from morphto.platform.unix import (  # noqa: F401 (re-exported: the common platform interface)
    acquire_single_instance,
    allow_foreground,
    gui_command,
    on_console_close,
    run_detached,
    set_app_user_model_id,
)

CONTEXT_MENU_SUPPORTED = False  # a Finder Quick Action is planned

# Carbon modifier flags and ANSI virtual key codes (the physical key, whatever the layout).
_MODS = {"win": 1 << 8, "shift": 1 << 9, "alt": 1 << 11, "ctrl": 1 << 12}
_KEYCODES = {
    "A": 0, "S": 1, "D": 2, "F": 3, "H": 4, "G": 5, "Z": 6, "X": 7, "C": 8, "V": 9, "B": 11, "Q": 12,
    "W": 13, "E": 14, "R": 15, "Y": 16, "T": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
    "9": 25, "7": 26, "8": 28, "0": 29, "O": 31, "U": 32, "I": 34, "P": 35, "L": 37, "J": 38, "K": 40,
    "N": 45, "M": 46, "SPACE": 49, "`": 50, "INSERT": 114, "PAUSE": 113,
    "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97, "F7": 98, "F8": 100, "F9": 101,
    "F10": 109, "F11": 103, "F12": 111,
}  # fmt: skip
_K_EVENT_CLASS_KEYBOARD = int.from_bytes(b"keyb", "big")
_K_EVENT_HOTKEY_PRESSED = 5


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_carbon = None


def _load_carbon():
    global _carbon
    if _carbon is None:
        c = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        c.GetApplicationEventTarget.restype = ctypes.c_void_p
        c.InstallEventHandler.argtypes = [
            ctypes.c_void_p, _HANDLER, ctypes.c_uint32, ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]  # fmt: skip
        c.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]  # fmt: skip
        c.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        c.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        _carbon = c
    return _carbon


class HotkeyListener:
    def __init__(self, hotkey: str, callback: Callable[[], None]) -> None:
        mods, key = split_hotkey(hotkey)
        if not mods or key.upper() not in _KEYCODES:
            raise ValueError(f"Invalid hotkey {hotkey!r}")
        self.hotkey = hotkey
        self._modifiers = sum(_MODS[m.lower()] for m in mods)
        self._keycode = _KEYCODES[key.upper()]
        self._callback = callback
        self._handler = _HANDLER(self._on_event)  # keep a reference: Carbon holds the raw pointer
        self._handler_ref = ctypes.c_void_p()
        self._hotkey_ref = ctypes.c_void_p()

    def start(self) -> bool:
        try:
            carbon = _load_carbon()
        except OSError:
            return False
        target = carbon.GetApplicationEventTarget()
        spec = _EventTypeSpec(_K_EVENT_CLASS_KEYBOARD, _K_EVENT_HOTKEY_PRESSED)
        if carbon.InstallEventHandler(
            target, self._handler, 1, ctypes.byref(spec), None, ctypes.byref(self._handler_ref)
        ):
            return False
        hotkey_id = _EventHotKeyID(int.from_bytes(b"Mrph", "big"), 1)
        status = carbon.RegisterEventHotKey(
            self._keycode, self._modifiers, hotkey_id, target, 0, ctypes.byref(self._hotkey_ref)
        )
        if status:
            carbon.RemoveEventHandler(self._handler_ref)
            self._handler_ref = ctypes.c_void_p()
            return False  # e.g. eventHotKeyExistsErr: another app owns it
        return True

    def stop(self) -> None:
        if _carbon is None:
            return
        if self._hotkey_ref:
            _carbon.UnregisterEventHotKey(self._hotkey_ref)
            self._hotkey_ref = ctypes.c_void_p()
        if self._handler_ref:
            _carbon.RemoveEventHandler(self._handler_ref)
            self._handler_ref = ctypes.c_void_p()

    def _on_event(self, _call_ref, _event, _user_data) -> int:
        self._callback()
        return 0


# ---- login item ----

_AGENT_LABEL = "com.rearzty.morphto"


def _agent_file() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_AGENT_LABEL}.plist"


def set_autostart(enabled: bool) -> None:
    path = _agent_file()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(
            {"Label": _AGENT_LABEL, "ProgramArguments": [*gui_command(), "--background"], "RunAtLoad": True},
            f,
        )


def autostart_enabled() -> bool:
    return _agent_file().is_file()


def set_context_menu(enabled: bool, extensions: Iterable[str], label: str, icon: str | None = None) -> None:
    """Not available on macOS yet."""


# ---- files and windows ----


def reveal(path: Path) -> None:
    run_detached(["open", "-R", str(path)])


reveal_in_explorer = reveal


def open_file(path: Path) -> None:
    run_detached(["open", str(path)])


def force_foreground(hwnd: int) -> None:
    """[NSApp activateIgnoringOtherApps:YES]: a menu-bar app must bring itself to the front."""
    with contextlib.suppress(OSError, AttributeError):
        objc = ctypes.CDLL(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(address)
        send_bool = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)(address)
        app = send_id(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
        if app:
            send_bool(app, objc.sel_registerName(b"activateIgnoringOtherApps:"), True)


def hide_dock_icon() -> None:
    """Run as a menu-bar app (no Dock icon) when started from source; the .app sets LSUIElement itself."""
    with contextlib.suppress(Exception):
        objc = ctypes.CDLL(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(address)
        send_long = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(address)
        app = send_id(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
        if app:
            send_long(app, objc.sel_registerName(b"setActivationPolicy:"), 1)  # accessory
