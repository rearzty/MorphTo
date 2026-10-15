"""First-run welcome: what the hotkey is and two switches most people want."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from morphto.gui import theme
from morphto.gui.icon import app_icon
from morphto.gui.icon import render as render_icon
from morphto.i18n import tr
from morphto.platform import system


class WelcomeDialog(QDialog):
    def __init__(self, hotkey: str, autostart: bool, context_menu: bool) -> None:
        super().__init__(None, Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.change_hotkey = False
        self.setWindowTitle("MorphTo")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(theme.stylesheet(theme.current()))
        self.setFont(theme.ui_font(10))
        self.setFixedWidth(440)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 26, 28, 22)
        lay.setSpacing(14)

        logo = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(
            render_icon(128).scaled(
                64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )
        lay.addWidget(logo)

        title = QLabel(tr("welcome.title"), alignment=Qt.AlignmentFlag.AlignCenter)
        title.setFont(theme.ui_font(15, QFont.Weight.DemiBold))
        lay.addWidget(title)

        keys = QLabel(
            " + ".join(f"<span style='padding:2px 6px'>{k}</span>" for k in hotkey.split("+")),
            alignment=Qt.AlignmentFlag.AlignCenter,
            objectName="hint",
        )
        keys.setFont(theme.ui_font(16, QFont.Weight.Bold))
        lay.addWidget(keys)

        text = QLabel(tr("welcome.text"), wordWrap=True, alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(text)
        tray = QLabel(
            tr("welcome.tray"), wordWrap=True, alignment=Qt.AlignmentFlag.AlignCenter, objectName="muted"
        )
        lay.addWidget(tray)

        self.autostart = QCheckBox(tr("settings.autostart"), checked=autostart)
        self.context_menu = QCheckBox(tr("settings.context_menu"), checked=context_menu)
        self.context_menu.setVisible(system.CONTEXT_MENU_SUPPORTED)
        lay.addSpacing(4)
        lay.addWidget(self.autostart)
        lay.addWidget(self.context_menu)
        lay.addSpacing(6)

        buttons = QHBoxLayout()
        change = QPushButton(tr("welcome.change_hotkey"))
        change.clicked.connect(self._change)
        start = QPushButton(tr("welcome.start"), objectName="primary")
        start.setDefault(True)
        start.clicked.connect(self.accept)
        buttons.addWidget(change)
        buttons.addStretch()
        buttons.addWidget(start)
        lay.addLayout(buttons)

    def _change(self) -> None:
        self.change_hotkey = True
        self.accept()
