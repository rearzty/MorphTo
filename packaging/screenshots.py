"""Render README screenshots from the real UI: docs/images/<lang>/*.png (transparent background).

Usage:  python packaging/screenshots.py
Windows are captured with QWidget.grab(), so the system acrylic backdrop is not part of the picture;
the popup is shown in its painted (non-acrylic) style, which looks the same on any background.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "docs" / "images"


def sample_photo(folder: Path) -> Path:
    from PIL import Image, ImageDraw

    w, h = 1600, 1066
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / h
        for x in range(w):
            s = x / w
            px[x, y] = (int(250 - 120 * t + 20 * s), int(150 - 90 * t + 40 * s), int(90 + 120 * t))
    draw = ImageDraw.Draw(img)
    draw.ellipse((w * 0.58, h * 0.30, w * 0.80, h * 0.63), fill=(255, 214, 140))
    draw.polygon(
        [(0, h), (w * 0.25, h * 0.55), (w * 0.5, h * 0.82), (w * 0.72, h * 0.6), (w, h)], fill=(42, 34, 74)
    )
    path = folder / "IMG_2041.jpg"
    img.save(path, quality=92)
    return path


def _stay_out_of_the_way() -> None:
    """Real Windows rendering (crisp fonts, HiDPI), but nothing visible or focus-stealing on the desktop:
    windows open far off-screen without activation; no tray icon or taskbar button."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QSystemTrayIcon, QWidget

    from morphto.gui import busy, popup
    from morphto.platform import windows

    original_show = QWidget.show

    def show_offscreen(self):
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        original_show(self)
        self.move(-20000, -20000)

    QWidget.show = show_offscreen
    QWidget.activateWindow = lambda self: None
    QWidget.raise_ = lambda self: None
    windows.force_foreground = lambda hwnd: None
    windows.allow_foreground = lambda: None
    popup.Popup._place = lambda self: self.adjustSize()
    QSystemTrayIcon.show = lambda self: None
    busy.BusyIndicator.update = lambda self, *a: None
    busy.BusyIndicator.finish = lambda self, *a, **k: None


def capture(lang: str, dark: bool) -> None:
    os.environ["MORPHTO_HOME"] = home = tempfile.mkdtemp(prefix="morphto-shots-")
    settings = {
        "effects": False,
        "language": lang,
        "output_mode": "folder",
        "output_dir": str(Path(home) / "out"),
    }
    Path(home, "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    from PySide6.QtWidgets import QApplication

    from morphto.core.targets import find_target
    from morphto.gui import theme
    from morphto.gui.app import Controller
    from morphto.gui.settings_dialog import SettingsDialog
    from morphto.gui.welcome import WelcomeDialog

    theme.current = lambda: theme.DARK if dark else theme.LIGHT  # independent of this PC's setting
    _stay_out_of_the_way()
    app = QApplication.instance() or QApplication([])
    folder = OUT / lang
    folder.mkdir(parents=True, exist_ok=True)
    suffix = "dark" if dark else "light"
    controller = Controller(app)
    popup = controller.popup
    photo = sample_photo(Path(home))

    def settle() -> None:
        for _ in range(20):
            app.processEvents()
            time.sleep(0.01)

    popup.summon([str(photo)])
    settle()
    popup.grab().save(str(folder / f"popup-{suffix}.png"))

    popup.search.setText("web")
    settle()
    popup.grab().save(str(folder / f"search-{suffix}.png"))
    popup.search.clear()

    popup.start(find_target("webp"))
    deadline = time.monotonic() + 30
    while popup.task is not None and time.monotonic() < deadline:
        settle()
    popup._hide_timer.stop()
    settle()
    popup.grab().save(str(folder / f"done-{suffix}.png"))

    welcome = WelcomeDialog(controller.settings.hotkey, True, True)
    welcome.show()
    settle()
    welcome.grab().save(str(folder / f"welcome-{suffix}.png"))
    welcome.close()

    settings = SettingsDialog(controller.settings)
    settings.show()
    settle()
    settings.grab().save(str(folder / f"settings-{suffix}.png"))
    settings.close()

    controller.quit()
    shutil.rmtree(home, ignore_errors=True)


def main() -> None:
    import subprocess

    if len(sys.argv) == 3:  # child process: one language/theme per process (fresh QApplication + i18n)
        capture(sys.argv[1], sys.argv[2] == "dark")
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # the icon is drawn, not rendered from text
    from PySide6.QtGui import QGuiApplication

    from morphto.gui.icon import render

    _app = QGuiApplication([])
    OUT.mkdir(parents=True, exist_ok=True)
    render(256).save(str(OUT / "icon.png"))
    del _app
    os.environ.pop("QT_QPA_PLATFORM")
    for lang in ("en", "ru"):
        for mode in ("dark", "light"):
            subprocess.run([sys.executable, __file__, lang, mode], check=True)
            print(f"docs/images/{lang}/*-{mode}.png")


if __name__ == "__main__":
    main()
