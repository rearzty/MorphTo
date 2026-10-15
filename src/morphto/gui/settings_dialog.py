"""Settings window."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from morphto import __version__, engines
from morphto.gui import effects, theme
from morphto.gui.icon import app_icon
from morphto.gui.workers import InstallTask
from morphto.i18n import LANGUAGES, engine_name, tr
from morphto.platform import system
from morphto.platform.hotkeys import HOTKEY_KEYS, MOD_LABELS, format_hotkey, split_hotkey
from morphto.settings import Settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.original = settings
        self.setWindowTitle(tr("settings.title"))
        self.setWindowIcon(app_icon())
        self.setStyleSheet(theme.stylesheet(theme.current()))
        self.setFont(theme.ui_font(10))
        self.setMinimumWidth(500)
        self._install: InstallTask | None = None
        self._installing: engines.Engine | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        # language + hotkey
        general = QGroupBox()
        gl = QVBoxLayout(general)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("settings.language")), 1)
        self.language = QComboBox()
        self.language.addItem(tr("settings.language_system"), "")
        for code, name in LANGUAGES.items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(settings.language)))
        row.addWidget(self.language)
        gl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("settings.hotkey")), 1)
        mods, key = split_hotkey(settings.hotkey)
        self.mod_buttons: dict[str, QPushButton] = {}
        for mod in ("Ctrl", "Alt", "Shift", "Win"):
            b = QPushButton(MOD_LABELS[mod], checkable=True, checked=mod in mods)
            b.setProperty("mod", "true")
            b.setMinimumWidth(52)
            b.toggled.connect(self._validate)
            self.mod_buttons[mod] = b
            row.addWidget(b)
        row.addWidget(QLabel("+"))
        self.key = QComboBox()
        self.key.addItems(HOTKEY_KEYS)
        self.key.setCurrentText(key if key in HOTKEY_KEYS else "C")
        row.addWidget(self.key)
        gl.addLayout(row)
        lay.addWidget(general)

        # output
        out = QGroupBox(tr("settings.output"))
        ol = QVBoxLayout(out)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for mode, key_ in (("next", "settings.output_next"), ("folder", "settings.output_folder"),
                           ("ask", "settings.output_ask")):  # fmt: skip
            rb = QRadioButton(tr(key_))
            rb.setChecked(settings.output_mode == mode)
            self.mode_group.addButton(rb)
            self.mode_buttons[mode] = rb
            ol.addWidget(rb)
        row = QHBoxLayout()
        self.folder = QLineEdit(settings.output_dir)
        browse = QPushButton(tr("settings.browse"))
        browse.clicked.connect(self._pick_folder)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        ol.addLayout(row)
        note = QLabel(tr("settings.output_note"), objectName="muted", wordWrap=True)
        ol.addWidget(note)
        lay.addWidget(out)

        # behaviour
        behaviour = QGroupBox()
        bl = QVBoxLayout(behaviour)
        self.copy = QCheckBox(tr("settings.copy"), checked=settings.copy_to_clipboard)
        self.autostart = QCheckBox(tr("settings.autostart"), checked=settings.autostart)
        self.context_menu = QCheckBox(tr("settings.context_menu"), checked=settings.context_menu)
        self.context_menu.setVisible(system.CONTEXT_MENU_SUPPORTED)
        self.effects = QCheckBox(tr("settings.effects"), checked=settings.effects)
        self.effects.setVisible(effects.backdrop_supported())
        self.check_updates = QCheckBox(tr("settings.check_updates"), checked=settings.check_updates)
        for cb in (self.copy, self.autostart, self.context_menu, self.effects, self.check_updates):
            bl.addWidget(cb)
        lay.addWidget(behaviour)

        # components
        comps = QGroupBox(tr("settings.components"))
        cl = QVBoxLayout(comps)
        self.engine_rows: dict[str, tuple[QLabel, QPushButton, QProgressBar]] = {}
        for engine in engines.all_engines():
            row = QHBoxLayout()
            label = QLabel()
            button = QPushButton()
            bar = QProgressBar(textVisible=False, maximum=1000)
            bar.setFixedWidth(120)
            bar.hide()
            button.setProperty("engine", engine.id)
            button.clicked.connect(self._on_engine_button)
            row.addWidget(label, 1)
            row.addWidget(bar)
            row.addWidget(button)
            cl.addLayout(row)
            self.engine_rows[engine.id] = (label, button, bar)
        self._refresh_engines()
        lay.addWidget(comps)

        # footer
        footer = QHBoxLayout()
        footer.addWidget(QLabel(tr("settings.about", version=__version__), objectName="muted"), 1)
        cancel = QPushButton(tr("settings.cancel"))
        cancel.clicked.connect(self.reject)
        self.save_btn = QPushButton(tr("settings.save"), objectName="primary")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self.accept)
        footer.addWidget(cancel)
        footer.addWidget(self.save_btn)
        lay.addLayout(footer)
        self._validate()

    def _validate(self) -> None:
        self.save_btn.setEnabled(any(b.isChecked() for b in self.mod_buttons.values()))

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("popup.choose_folder"), self.folder.text())
        if folder:
            self.folder.setText(folder)
            self.mode_buttons["folder"].setChecked(True)

    def _refresh_engines(self) -> None:
        for engine in engines.all_engines():
            label, button, _bar = self.engine_rows[engine.id]
            source = engine.source()
            status = {"managed": tr("settings.installed"), "system": tr("settings.system")}.get(
                source or "", tr("settings.not_installed")
            )
            label.setText(f"<b>{engine_name(engine)}</b> — {status}")
            button.setText(tr("settings.remove") if source == "managed" else tr("settings.install"))
            button.setVisible(source != "system")
            button.setEnabled(self._install is None)

    def _engine_action(self, engine: engines.Engine) -> None:
        if engine.source() == "managed":
            engine.remove()
            self._refresh_engines()
            return
        _label, button, bar = self.engine_rows[engine.id]
        self._install = InstallTask([engine.id])
        self._installing = engine
        self._install.signals.progress.connect(self._on_install_progress)
        self._install.signals.finished.connect(self._on_install_finished)
        bar.setValue(0)
        bar.show()
        button.setEnabled(False)
        QThreadPool.globalInstance().start(self._install)

    def _on_engine_button(self) -> None:
        self._engine_action(engines.get(self.sender().property("engine")))

    def _on_install_progress(self, fraction: float) -> None:
        if self._installing is not None:
            self.engine_rows[self._installing.id][2].setValue(int(fraction * 1000))

    def _on_install_finished(self, error: str) -> None:
        engine, self._installing = self._installing, None
        if engine is not None:
            self._installed(engine, error)

    def _installed(self, engine: engines.Engine, error: str) -> None:
        self._install = None
        self.engine_rows[engine.id][2].hide()
        self._refresh_engines()
        if error:
            self.engine_rows[engine.id][0].setText(
                tr("engine.failed", engine=engine_name(engine), error=error)
            )

    def reject(self) -> None:
        if self._install is not None:
            self._install.cancel.set()
        super().reject()

    def result_settings(self) -> Settings:
        mods = [m for m, b in self.mod_buttons.items() if b.isChecked()]
        mode = (
            next(m for m, b in self.mode_buttons.items() if b.isChecked())
            if self.mode_group.checkedButton()
            else "next"
        )
        return replace(
            self.original,
            language=self.language.currentData(),
            hotkey=format_hotkey(mods, self.key.currentText()),
            output_mode=mode,
            output_dir=self.folder.text().strip() or self.original.output_dir,
            copy_to_clipboard=self.copy.isChecked(),
            autostart=self.autostart.isChecked(),
            context_menu=self.context_menu.isChecked(),
            effects=self.effects.isChecked(),
            check_updates=self.check_updates.isChecked(),
        )
