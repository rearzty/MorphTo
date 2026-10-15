"""Clipboard through Qt, for macOS and Linux (X11 and Wayland). Same interface as the Win32 module.

Qt's clipboard may only be used from the GUI thread, so copying results is done there
(``COPY_IN_GUI_THREAD``) instead of in the conversion worker as on Windows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

COPY_IN_GUI_THREAD = True


@dataclass
class ClipInfo:
    kind: str  # "files" or "image"
    files: list[Path] = field(default_factory=list)
    image_size: tuple[int, int] | None = None


class ClipboardBusy(Exception):
    """Never raised here; kept for the shared interface."""


def _mime():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    return app.clipboard().mimeData() if app is not None else None


def _files(mime) -> list[Path]:
    files = []
    if mime is not None and mime.hasUrls():
        files = [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]
    elif mime is not None and mime.hasText():
        text = mime.text().strip().strip('"')
        if text and "\n" not in text and len(text) < 4096:
            path = Path(text.removeprefix("file://"))
            if path.is_absolute():
                files = [path]
    return [f for f in files if f.is_file()]


def has_content() -> bool:
    mime = _mime()
    return mime is not None and (mime.hasUrls() or mime.hasImage() or mime.hasText())


def peek() -> ClipInfo | None:
    mime = _mime()
    if mime is None:
        return None
    files = _files(mime)
    if files:
        return ClipInfo("files", files)
    if mime.hasImage():
        from PySide6.QtWidgets import QApplication

        image = QApplication.clipboard().image()
        return ClipInfo("image", image_size=None if image.isNull() else (image.width(), image.height()))
    return None


def read() -> list[Path] | Image.Image | None:
    mime = _mime()
    if mime is None:
        return None
    files = _files(mime)
    if files:
        return files
    if mime.hasImage():
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication

        qimage = QApplication.clipboard().image()
        if qimage.isNull():
            return None
        qimage = qimage.convertToFormat(QImage.Format.Format_RGBA8888)
        data = bytes(qimage.constBits())[: qimage.sizeInBytes()]
        size = (qimage.width(), qimage.height())
        return Image.frombuffer("RGBA", size, data, "raw", "RGBA", qimage.bytesPerLine(), 1).copy()
    return None


def write_files(paths: list[Path], with_image: bool = True) -> bool:
    """Files as URLs (paste in a file manager or a chat); a single image also as pixels."""
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None or not paths:
        return False
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    # GNOME Files pastes copied files from this target
    uris = "\n".join(QUrl.fromLocalFile(str(p)).toString() for p in paths)
    mime.setData("x-special/gnome-copied-files", f"copy\n{uris}".encode())
    if with_image and len(paths) == 1 and paths[0].is_file():
        image = QImage(str(paths[0]))
        if not image.isNull():
            mime.setImageData(image)
    app.clipboard().setMimeData(mime)
    return True
