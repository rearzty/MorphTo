"""Tray application: owns the hotkey, the popup and single-instance IPC."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QSystemTrayIcon

from morphto import APP_NAME, engines
from morphto.core.formats import all_formats
from morphto.core.registry import default_registry
from morphto.gui import theme
from morphto.gui.busy import BusyIndicator
from morphto.gui.icon import app_icon
from morphto.gui.ipc import SERVER_NAME, decode
from morphto.gui.popup import Popup
from morphto.i18n import set_language, tr
from morphto.platform import pipe, windows
from morphto.settings import DEFAULT_HOTKEY, Settings

MERGE_MS = 250  # Explorer starts one process per selected file; merge them into one popup
APP_ID = "rearzty.MorphTo"


class Controller(QObject):
    hotkey_pressed = Signal()
    quit_requested = Signal()
    files_received = Signal(list, bool)  # from the IPC thread: files, show

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.settings = Settings.load()
        self.settings.autostart = windows.autostart_enabled()  # the installer may have turned it on
        set_language(self.settings.language)
        self.icon = app_icon()
        app.setWindowIcon(self.icon)
        theme.apply_accent(app)

        self.popup = Popup(self)
        self.hotkey: windows.HotkeyListener | None = None
        self.hotkey_pressed.connect(self.popup.toggle)
        self._settings_dialog = None

        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.activated.connect(self._tray_activated)
        self._build_tray_menu()
        self.tray.show()
        self.busy = BusyIndicator(self)
        self._quitting = False

        self._pending_files: list[str] = []
        self._merge_timer = QTimer(self)
        self._merge_timer.setSingleShot(True)
        self._merge_timer.setInterval(MERGE_MS)
        self._merge_timer.timeout.connect(self._flush_files)
        self._quit_done = threading.Event()
        self.files_received.connect(self._on_files)
        self.server = pipe.PipeServer(SERVER_NAME, lambda message: self.files_received.emit(*decode(message)))
        self.server.start()

        for engine in engines.all_engines():
            engine.prune()  # drop files older versions downloaded but no longer need (ffprobe.exe)
        self._register_hotkey()
        if self.settings.context_menu:
            self._apply_context_menu(True)  # refresh the command path in case the app moved

    # ---- tray ----

    def _build_tray_menu(self) -> None:
        menu = QMenu()
        menu.setStyleSheet(theme.stylesheet(theme.current()))
        open_action = QAction(tr("tray.open"), menu)
        open_action.triggered.connect(self._open_popup)
        settings_action = QAction(tr("tray.settings"), menu)
        settings_action.triggered.connect(self.open_settings)
        quit_action = QAction(tr("tray.quit"), menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(open_action)
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(f"{APP_NAME} — {self.settings.hotkey}")

    def _open_popup(self) -> None:
        self.popup.summon()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.busy.attention or self.popup.busy():
                self.popup.summon(keep=True)  # show the running job / the result waiting to be seen
            else:
                self.popup.toggle()

    def welcome(self) -> None:
        from morphto.gui.welcome import WelcomeDialog

        dialog = WelcomeDialog(self.settings.hotkey, autostart=True, context_menu=True)
        dialog.exec()
        self.settings.autostart = dialog.autostart.isChecked()
        self.settings.context_menu = dialog.context_menu.isChecked()
        windows.set_autostart(self.settings.autostart)
        self._apply_context_menu(self.settings.context_menu)
        self.save_settings()
        if dialog.change_hotkey:
            self.open_settings()

    def notify(self, title: str, message: str, error: bool = False) -> None:
        icon = QSystemTrayIcon.MessageIcon.Warning if error else QSystemTrayIcon.MessageIcon.Information
        self.tray.showMessage(title, message, icon, 4000)

    # ---- hotkey ----

    def _register_hotkey(self) -> None:
        if self.hotkey is not None:
            self.hotkey.stop()
            self.hotkey = None
        try:
            listener = windows.HotkeyListener(self.settings.hotkey, self.hotkey_pressed.emit)
        except ValueError:
            self.settings.hotkey = DEFAULT_HOTKEY
            listener = windows.HotkeyListener(self.settings.hotkey, self.hotkey_pressed.emit)
        if listener.start():
            self.hotkey = listener
        else:
            self.notify(APP_NAME, tr("tray.hotkey_busy", hotkey=self.settings.hotkey), error=True)

    # ---- IPC ----

    def _on_files(self, files: list[str], show: bool) -> None:
        self._pending_files.extend(files)
        if show:
            self._merge_timer.start()

    def _flush_files(self) -> None:
        files, self._pending_files = self._pending_files, []
        windows.allow_foreground()
        self.popup.summon(files or None)

    # ---- settings ----

    def save_settings(self) -> None:
        self.settings.save()

    def open_settings(self) -> None:
        from morphto.gui.settings_dialog import SettingsDialog

        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        self.popup.hide()
        dialog = SettingsDialog(self.settings)
        self._settings_dialog = dialog
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            new = dialog.result_settings()
        finally:
            self._settings_dialog = None
        self.apply_settings(new)

    def apply_settings(self, new: Settings) -> None:
        old = self.settings
        self.settings = new
        new.history = old.history
        new.save()
        if new.language != old.language or new.effects != old.effects:
            set_language(new.language)
            if not self.popup.busy():
                self.popup.deleteLater()
                self.popup = Popup(self)
                self.hotkey_pressed.disconnect()
                self.hotkey_pressed.connect(self.popup.toggle)
            self._build_tray_menu()
            if new.context_menu:
                self._apply_context_menu(True)
        if new.hotkey != old.hotkey:
            self._register_hotkey()
            self.tray.setToolTip(f"{APP_NAME} — {new.hotkey}")
        if new.autostart != old.autostart:
            windows.set_autostart(new.autostart)
        if new.context_menu != old.context_menu:
            self._apply_context_menu(new.context_menu)

    def _apply_context_menu(self, enabled: bool) -> None:
        sources = default_registry().sources()
        exts = [e for f in all_formats() if f.id in sources for e in f.extensions]
        icon = sys.executable if getattr(sys, "frozen", False) else None
        try:
            windows.set_context_menu(enabled, exts, tr("context.label"), icon)
        except OSError as exc:
            self.notify(APP_NAME, str(exc), error=True)

    def request_quit_and_wait(self) -> None:
        """Called from a non-GUI thread (console close / Ctrl+C)."""
        self.quit_requested.emit()
        self._quit_done.wait(3)

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self.hotkey is not None:
            self.hotkey.stop()
        self.popup.cancel()
        QThreadPool.globalInstance().waitForDone(5000)  # let cancelled jobs clean up their temp files
        self.busy.reset()
        self.server.stop()
        self.tray.hide()
        self._quit_done.set()
        self.app.quit()


def run(files: list[str], background: bool = False) -> int:
    """Run the tray app. Call through ``morphto.gui.launch`` so only one instance ever runs."""
    files = [str(Path(f).resolve()) for f in files]
    windows.set_app_user_model_id(APP_ID)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    first_run = not Settings.path().exists()
    controller = Controller(app)
    _install_quit_handlers(controller)
    if first_run:
        controller.welcome()
    if not background or files:
        controller.popup.summon(files or None)
    return app.exec()


def _install_quit_handlers(controller: Controller) -> None:
    """Ctrl+C or closing the console quits cleanly, so no dead icon stays in the tray.

    Windows calls the console handler on its own thread; it asks the GUI thread to quit and waits.
    """
    controller.quit_requested.connect(controller.quit)
    windows.on_console_close(controller.request_quit_and_wait)
