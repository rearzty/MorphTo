"""Native Windows 11 window effects: acrylic backdrop, system shadow and rounded corners.

Qt turns frameless translucent windows into "layered" windows, which DWM cannot put a backdrop behind.
So the popup is created as a normal (captioned) translucent window, the caption is removed by
answering WM_NCCALCSIZE, and DWM is asked to draw the acrylic material behind the client area.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
HTCLIENT = 1

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWCP_ROUND = 2
DWMSBT_TRANSIENTWINDOW = 3  # acrylic, as used by flyouts and context menus

_BACKDROP_MIN_BUILD = 22621  # Windows 11 22H2


class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int),
        ("right", ctypes.c_int),
        ("top", ctypes.c_int),
        ("bottom", ctypes.c_int),
    ]


def backdrop_supported() -> bool:
    return sys.platform == "win32" and sys.getwindowsversion().build >= _BACKDROP_MIN_BUILD


def _set_attr(hwnd: int, attr: int, value: int) -> bool:
    val = ctypes.c_int(value)
    hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd), wintypes.DWORD(attr), ctypes.byref(val), ctypes.sizeof(val)
    )
    return hr == 0


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
]  # fmt: skip
GWL_STYLE = -16
WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00080000, 0x00020000, 0x00010000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20


def prepare_captionless(hwnd: int) -> None:
    """Drop the system menu so DWM draws no caption buttons (close/min/max) over our own header,
    then make Windows re-query the frame size (answered in handle_native_event)."""
    style = _user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    _user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style & ~(WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX))
    flags = SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
    _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, flags)


def apply_acrylic(hwnd: int, dark: bool) -> bool:
    """Turn on the acrylic backdrop; False if the system refused (caller should paint an opaque bg)."""
    try:
        margins = _MARGINS(-1, -1, -1, -1)
        if ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins)) != 0:
            return False
        _set_attr(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, int(dark))
        _set_attr(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
        return _set_attr(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_TRANSIENTWINDOW)
    except (AttributeError, OSError):
        return False


def handle_native_event(message: int) -> tuple[bool, int] | None:
    """Make the whole window client area (no caption/borders) and non-resizable.

    Returns a (handled, result) pair for QWidget.nativeEvent, or None to let Qt handle it.
    """
    msg = wintypes.MSG.from_address(message)
    if msg.message == WM_NCCALCSIZE and msg.wParam:
        return True, 0
    if msg.message == WM_NCHITTEST:
        return True, HTCLIENT
    return None
