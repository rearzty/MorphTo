"""Windows integration: global hotkey, single instance, autostart, Explorer context menu."""

from __future__ import annotations

import contextlib
import ctypes
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from ctypes import wintypes
from pathlib import Path

from morphto import APP_NAME
from morphto.platform.hotkeys import HOTKEY_KEYS, format_hotkey, split_hotkey  # noqa: F401 (re-exported)

CONTEXT_MENU_SUPPORTED = True

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

# All Win32 functions are bound once, here, with exact signatures. Resolving them lazily through
# ctypes.windll from several threads at once crashed the compiled (Nuitka) build.
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_u32.RegisterHotKey.restype = wintypes.BOOL
_u32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_u32.GetMessageW.restype = wintypes.BOOL
_u32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_k32.GetCurrentThreadId.restype = wintypes.DWORD
_k32.GetConsoleWindow.restype = wintypes.HWND
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_HANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
_k32.SetConsoleCtrlHandler.argtypes = [_HANDLER_ROUTINE, wintypes.BOOL]

_MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_SPECIAL_KEYS = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "pause": 0x13, "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC,
}  # fmt: skip


def parse_hotkey(text: str) -> tuple[int, int]:
    """'Win+Alt+C' -> (modifiers, virtual key code). Raises ValueError if invalid."""
    parts = [p.strip() for p in text.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError(f"Hotkey needs at least one modifier: {text!r}")
    mods = 0
    for part in parts[:-1]:
        try:
            mods |= _MODIFIERS[part.lower()]
        except KeyError:
            raise ValueError(f"Unknown modifier {part!r}") from None
    key = parts[-1]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.upper().startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key.lower() in _SPECIAL_KEYS:
        vk = _SPECIAL_KEYS[key.lower()]
    else:
        raise ValueError(f"Unknown key {key!r}")
    return mods, vk


class HotkeyListener:
    """Registers a system-wide hotkey on a dedicated thread with its own message loop."""

    def __init__(self, hotkey: str, callback: Callable[[], None]) -> None:
        self.hotkey = hotkey
        self._mods, self._vk = parse_hotkey(hotkey)
        self._callback = callback
        self._thread_id = 0
        self._ready = threading.Event()
        self._ok = False
        self._thread = threading.Thread(target=self._run, name="morphto-hotkey", daemon=True)

    def start(self) -> bool:
        """Start listening; returns False if the hotkey is taken by another program."""
        self._thread.start()
        self._ready.wait(5)
        return self._ok

    def stop(self) -> None:
        if self._thread.is_alive() and self._thread_id:
            _u32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)

    def _run(self) -> None:
        self._thread_id = _k32.GetCurrentThreadId()
        self._ok = bool(_u32.RegisterHotKey(None, 1, self._mods | MOD_NOREPEAT, self._vk))
        self._ready.set()
        if not self._ok:
            return
        try:
            msg = wintypes.MSG()
            while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self._callback()
        finally:
            _u32.UnregisterHotKey(None, 1)


_instance_mutex = None


def acquire_single_instance(name: str) -> bool:
    """True if we are the only instance. The mutex lives until the process exits."""
    global _instance_mutex
    ERROR_ALREADY_EXISTS = 183
    handle = _k32.CreateMutexW(None, False, f"Local\\{name}")
    if not handle:
        return True  # cannot tell; better to run than to refuse
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _k32.CloseHandle(handle)
        return False
    _instance_mutex = handle
    return True


def set_app_user_model_id(app_id: str) -> None:
    """Group our windows under our own taskbar identity (and icon) instead of python.exe's."""
    with contextlib.suppress(AttributeError, OSError):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))


_console_handler = None


class _ConsoleHandler:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback

    def __call__(self, _event: int) -> bool:
        self.callback()
        return True


def on_console_close(callback: Callable[[], None]) -> None:
    """Run ``callback`` (from another thread) when the console window is closed or Ctrl+C is pressed.

    Only relevant when started from a terminal; the installed app normally has no console at all.
    """
    global _console_handler
    if not _k32.GetConsoleWindow():
        return
    _console_handler = _HANDLER_ROUTINE(_ConsoleHandler(callback))
    _k32.SetConsoleCtrlHandler(_console_handler, True)


def gui_command() -> list[str]:
    """Command line that starts the GUI without a console window."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return [str(pythonw if pythonw.exists() else exe), "-m", "morphto.gui"]


def _quote(args: list[str]) -> str:
    return subprocess.list2cmdline(args)


# --- Autostart -----------------------------------------------------------------------------------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_autostart(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _quote([*gui_command(), "--background"]))
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, APP_NAME)


def autostart_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except FileNotFoundError:
        return False


# --- Explorer context menu -----------------------------------------------------------------------
# Per-extension verbs under HKCU\Software\Classes\SystemFileAssociations\.<ext>\shell\MorphTo, so the
# item only appears on files MorphTo can actually convert. No admin rights needed.

_ASSOC = r"Software\Classes\SystemFileAssociations"


def set_context_menu(enabled: bool, extensions: Iterable[str], label: str, icon: str | None = None) -> None:
    import winreg

    exts = sorted({e.lower().lstrip(".") for e in extensions})
    for ext in exts:
        base = rf"{_ASSOC}\.{ext}\shell\{APP_NAME}"
        if not enabled:
            _delete_tree(winreg.HKEY_CURRENT_USER, base)
            _delete_if_empty(winreg.HKEY_CURRENT_USER, rf"{_ASSOC}\.{ext}\shell")
            _delete_if_empty(winreg.HKEY_CURRENT_USER, rf"{_ASSOC}\.{ext}")
            continue
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
            winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, label)
            winreg.SetValueEx(key, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
            if icon:
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _quote(gui_command()) + ' "%1"')
    _notify_shell()


def _delete_tree(root, path: str) -> None:
    import winreg

    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(root, rf"{path}\{child}")
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def _delete_if_empty(root, path: str) -> None:
    """Remove a key we created only if nothing else (subkeys or values) lives in it."""
    import winreg

    try:
        with winreg.OpenKey(root, path) as key:
            subkeys, values, _ = winreg.QueryInfoKey(key)
        if subkeys == 0 and values == 0:
            winreg.DeleteKey(root, path)
    except OSError:
        pass


def _notify_shell() -> None:
    SHCNE_ASSOCCHANGED, SHCNF_IDLIST = 0x08000000, 0
    ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)


# --- Misc ----------------------------------------------------------------------------------------


def reveal_in_explorer(path: Path) -> None:
    subprocess.Popen(["explorer.exe", "/select,", str(path)])


def open_file(path: Path) -> None:
    import os

    os.startfile(path)  # type: ignore[attr-defined]


def allow_foreground() -> None:
    """Let our process take focus when the popup is summoned from the background."""
    ASFW_ANY = -1
    ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)


def force_foreground(hwnd: int) -> None:
    """Bring a window to the front even when another app currently owns the foreground."""
    user32 = ctypes.windll.user32
    foreground = user32.GetForegroundWindow()
    if foreground == hwnd:
        return
    fg_thread = user32.GetWindowThreadProcessId(foreground, None)
    our_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    if fg_thread and fg_thread != our_thread:
        user32.AttachThreadInput(our_thread, fg_thread, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(our_thread, fg_thread, False)
    else:
        user32.SetForegroundWindow(hwnd)


reveal = reveal_in_explorer
