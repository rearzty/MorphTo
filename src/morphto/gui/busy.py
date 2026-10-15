"""Shows that work is going on while the popup is hidden: a progress ring on the tray icon everywhere, and on
Windows also a taskbar button with a progress bar. Clicking either brings the popup back."""

from __future__ import annotations

import ctypes
import sys
import uuid
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from morphto import APP_NAME
from morphto.gui.icon import render as render_icon
from morphto.platform.hotkeys import display_hotkey

if TYPE_CHECKING:
    from morphto.gui.app import Controller

if sys.platform == "win32":
    from ctypes import wintypes

TBPF_NOPROGRESS, TBPF_INDETERMINATE, TBPF_NORMAL, TBPF_ERROR = 0, 1, 2, 4


class _GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_byte * 16)]

    @classmethod
    def from_str(cls, text: str) -> _GUID:
        guid = cls()
        ctypes.memmove(guid.data, uuid.UUID(text).bytes_le, 16)
        return guid


class TaskbarProgress:
    """Minimal ctypes wrapper around ITaskbarList3 (progress on a taskbar button)."""

    def __init__(self) -> None:
        self._ptr = ctypes.c_void_p()
        if sys.platform != "win32":
            self._ptr = None
            return
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        clsid = _GUID.from_str("56FDF344-FD6D-11d0-958A-006097C9A090")
        iid = _GUID.from_str("EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF")
        CLSCTX_INPROC_SERVER = 1
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER, ctypes.byref(iid), ctypes.byref(self._ptr)
        )
        if hr != 0 or not self._ptr:
            self._ptr = None
            return
        vtable = ctypes.cast(self._ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        proto = ctypes.WINFUNCTYPE
        self._hr_init = proto(ctypes.c_long, ctypes.c_void_p)(vtable[3])
        self._set_value = proto(
            ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong
        )(vtable[9])
        self._set_state = proto(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_int)(vtable[10])
        self._hr_init(self._ptr)

    def set(self, hwnd: int, fraction: float | None, state: int = TBPF_NORMAL) -> None:
        if not self._ptr:
            return
        if fraction is None:
            self._set_state(self._ptr, hwnd, state if state != TBPF_NORMAL else TBPF_INDETERMINATE)
            return
        self._set_state(self._ptr, hwnd, state)
        self._set_value(self._ptr, hwnd, int(max(0.0, min(1.0, fraction)) * 1000), 1000)

    def clear(self, hwnd: int) -> None:
        if self._ptr:
            self._set_state(self._ptr, hwnd, TBPF_NOPROGRESS)


def _flash(hwnd: int) -> None:
    if sys.platform != "win32":
        return

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]

    FLASHW_TRAY, FLASHW_TIMERNOFG = 0x2, 0xC
    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, FLASHW_TRAY | FLASHW_TIMERNOFG, 0, 0)
    ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))


class _TaskbarHost(QWidget):
    """An invisible, always-minimized window whose only purpose is a taskbar button."""

    def __init__(self, on_click) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self._on_click = on_click
        self.setWindowTitle(APP_NAME)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowOpacity(0.0)
        self.setGeometry(-32000, -32000, 1, 1)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange and not self.isMinimized() and self.isVisible():
            self.setWindowState(Qt.WindowState.WindowMinimized)
            self._on_click()
        super().changeEvent(event)


def ring_icon(fraction: float | None, done: bool = False) -> QIcon:
    """The app icon with a progress ring (or a green check when ``done``)."""
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = max(2.0, size / 7)
        rect = QRectF(width / 2, width / 2, size - width, size - width)
        if done:
            p.drawPixmap(0, 0, render_icon(size))
            dot = size * 0.42
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#22C55E"))
            p.drawEllipse(QRectF(size - dot, size - dot, dot, dot))
        else:
            p.setOpacity(0.55)
            p.drawPixmap(0, 0, render_icon(size))
            p.setOpacity(1.0)
            p.setPen(QPen(QColor(255, 255, 255, 90), width))
            p.drawEllipse(rect)
            p.setPen(QPen(QColor("#FFFFFF"), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            span = 360 * (fraction if fraction is not None else 0.25)
            p.drawArc(rect, 90 * 16, int(-span * 16))
        p.end()
        icon.addPixmap(pm)
    return icon


class BusyIndicator:
    def __init__(self, controller: Controller) -> None:
        self.controller = controller
        self.taskbar = TaskbarProgress()
        # Elsewhere a hidden window would show up as a stray app window: the tray ring has to do.
        self.host = _TaskbarHost(self._activate) if sys.platform == "win32" else None
        if self.host is not None:
            self.host.setWindowIcon(controller.icon)
        self._shown_percent = -1
        self.attention = False  # a result is waiting to be seen

    def update(self, fraction: float | None, text: str) -> None:
        """Called on every progress tick while a job runs."""
        self.attention = False
        if self.host is not None:
            if not self.host.isVisible():
                self.host.showMinimized()
            self.host.setWindowTitle(f"{APP_NAME} — {text}")
            self.taskbar.set(int(self.host.winId()), fraction)
        percent = -2 if fraction is None else int(fraction * 50)  # ring steps of 2 %
        if percent != self._shown_percent:
            self._shown_percent = percent
            self.controller.tray.setIcon(ring_icon(fraction))
        self.controller.tray.setToolTip(f"{APP_NAME} — {text}")

    def finish(self, popup_visible: bool, failed: bool = False) -> None:
        """Work is over. If the popup is hidden, keep the taskbar button blinking until it is opened."""
        self._shown_percent = -1
        hwnd = int(self.host.winId()) if self.host is not None else 0
        if hwnd:
            self.taskbar.clear(hwnd)
        self.controller.tray.setToolTip(f"{APP_NAME} — {display_hotkey(self.controller.settings.hotkey)}")
        if popup_visible:
            self.reset()
            return
        self.attention = True
        if failed and hwnd:
            self.taskbar.set(hwnd, 1.0, TBPF_ERROR)
        self.controller.tray.setIcon(ring_icon(None, done=True) if not failed else self.controller.icon)
        if hwnd:
            _flash(hwnd)

    def reset(self) -> None:
        self.attention = False
        if self.host is not None:
            self.host.hide()
        self.controller.tray.setIcon(self.controller.icon)

    def _activate(self) -> None:
        self.controller.popup.summon(keep=True)
