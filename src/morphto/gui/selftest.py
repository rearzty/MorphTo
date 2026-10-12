"""``MorphTo.exe --self-test <file> [format]``: drive the real GUI end to end and report, for checking builds.

Runs in a throwaway profile, so it never touches the user's settings, hotkey or Explorer menu.
Callbacks given to Qt are bound methods only (see the note on lambdas in popup.py).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer


class SelfTest(QObject):
    def __init__(self, app, file: str, target: str) -> None:
        super().__init__()
        from morphto.gui.app import Controller

        self.app = app
        self.file = file
        self.target = target
        self.results: list[str] = []
        self.started = time.perf_counter()
        self.deadline = time.monotonic() + 60
        self.controller = Controller(app)
        self.popup = self.controller.popup
        self.check("controller", True, f"{time.perf_counter() - self.started:.2f}s")
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.step)
        self.stage = 0
        self.timer.start(200)

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(f"{'PASS' if ok else 'FAIL'} {name}{f' ({detail})' if detail else ''}")
        print(self.results[-1], flush=True)

    def step(self) -> None:
        stage, self.stage = self.stage, self.stage + 1
        getattr(self, f"stage{stage}")()

    def stage0(self) -> None:
        from morphto.gui.settings_dialog import SettingsDialog
        from morphto.gui.welcome import WelcomeDialog

        welcome = WelcomeDialog("Win+Alt+C", True, True)
        welcome.show()
        settings = SettingsDialog(self.controller.settings)
        settings.show()
        self.app.processEvents()
        welcome.close()
        settings.close()
        self.check("dialogs", True)
        self.popup.summon()  # empty first: loading into a visible popup is the tricky path
        self.timer.start(300)

    def stage1(self) -> None:
        from morphto.core.targets import find_target

        self.popup.summon([self.file])
        self.check("popup loads file", bool(self.popup.items), ", ".join(i.fmt.id for i in self.popup.items))
        self.popup.summon([self.file])
        self.check("popup reloads file", bool(self.popup.items))
        from PySide6.QtWidgets import QWidget

        other = QWidget()  # another app's window taking focus must not close the popup (drag & drop)
        other.show()
        other.activateWindow()
        self.app.processEvents()
        self.check("stays open when another window is focused", self.popup.isVisible())
        other.close()
        self.popup.start(find_target(self.target))
        self.popup.hide()  # as if the user clicked elsewhere: the taskbar button should take over
        self.check("taskbar button while busy", self.controller.busy.host.isVisible())
        self.timer.start(100)

    def stage2(self) -> None:
        if self.popup.task is not None and time.monotonic() < self.deadline:
            self.stage = 2  # keep waiting
            self.timer.start(100)
            return
        self.check(
            "conversion", bool(self.popup.results), ", ".join(Path(p).name for p in self.popup.results)
        )
        self.check("attention after hidden finish", self.controller.busy.attention)
        self.popup.summon(keep=True)
        self.check("result shown again", self.popup._mode == "done" and self.popup.result_card.isVisible())
        self.timer.start(300)

    def stage3(self) -> None:
        self.controller.quit()


def run(file: str, target: str = "jpg") -> int:
    from morphto import engines
    from morphto.paths import engines_dir

    # Reuse the components already installed (FFmpeg, HEIC encoder) in the throwaway profile.
    os.environ.setdefault("MORPHTO_ENGINES", str(engines_dir()))
    ffmpeg = engines.get("ffmpeg").tool("ffmpeg")
    if ffmpeg is not None:
        os.environ["MORPHTO_FFMPEG"] = str(ffmpeg)
    home = tempfile.mkdtemp(prefix="morphto-selftest-")
    os.environ["MORPHTO_HOME"] = home
    settings = {"output_mode": "folder", "output_dir": str(Path(home) / "out")}
    (Path(home) / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    test = SelfTest(app, file, target)
    app.exec()
    failed = sum(r.startswith("FAIL") for r in test.results)
    print(f"self-test: {len(test.results) - failed} passed, {failed} failed", flush=True)
    return 1 if failed else 0
