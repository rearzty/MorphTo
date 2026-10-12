"""The popup: paste / drop / browse a file, type a format, press Enter."""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image
from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDrag,
    QFont,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from morphto import engines
from morphto.core import ConversionError, plan
from morphto.core.detect import detect
from morphto.core.engine import required_engines
from morphto.core.formats import Category, Format, all_formats
from morphto.core.registry import default_registry
from morphto.core.targets import Target, available_targets, rank
from morphto.gui import effects, theme
from morphto.gui.icon import render as render_icon
from morphto.gui.workers import ConvertTask, InstallTask, Job
from morphto.i18n import engine_name, tr
from morphto.platform import clipboard, windows

if TYPE_CHECKING:
    from morphto.gui.app import Controller

CARD_WIDTH = 460
SHADOW = 20  # room for the painted shadow when the system one is not available
ROW_HEIGHT = 40
MAX_ROWS = 7
AUTO_HIDE_MS = 6000  # after a finished conversion; paused while the mouse is over the popup


@dataclass(frozen=True)
class InputItem:
    path: Path
    fmt: Format
    from_clipboard: bool = False


# --- small painting helpers ----------------------------------------------------------------------


def badge_pixmap(text: str, color: str, size: QSize, radius: float = 7, ratio: float = 2.0) -> QPixmap:
    pm = QPixmap(int(size.width() * ratio), int(size.height() * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(0, 0, size.width(), size.height()), radius, radius)
    p.setPen(QColor("white"))
    font = theme.ui_font(7.5 if len(text) > 3 else 8.5, QFont.Weight.Bold)
    p.setFont(font)
    p.drawText(QRect(0, 0, size.width(), size.height()), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return pm


def image_thumbnail(path: Path, side: int = 48, ratio: float = 2.0) -> tuple[QPixmap, tuple[int, int]] | None:
    try:
        if path.stat().st_size > 80 * 1024 * 1024:
            return None
        with Image.open(path) as im:
            dims = im.size
            px = int(side * ratio)
            im.draft("RGB", (px, px))
            im.thumbnail((px, px))
            im = im.convert("RGBA")
            data = im.tobytes("raw", "RGBA")
            qimg = QImage(data, im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888).copy()
    except Exception:
        return None
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, side, side), 8, 8)
    p.setClipPath(clip)
    w, h = qimg.width() / ratio, qimg.height() / ratio
    p.drawImage(QRectF((side - w) / 2, (side - h) / 2, w, h), qimg)
    p.end()
    return pm, dims


def human_size(n: float) -> str:
    for unit in ("b", "kb", "mb", "gb"):
        if n < 1024 or unit == "gb":
            value = f"{n:.0f}" if unit == "b" else f"{n:.1f}"
            return f"{value} {tr('unit.' + unit)}"
        n /= 1024
    return ""


def size_change(before: int, after: int) -> str:
    """'2.3 MB → 450.0 KB (−80%)'."""
    text = f"{human_size(before)} → {human_size(after)}"
    if before > 0:
        pct = round((after - before) * 100 / before)
        text += f" ({'+' if pct > 0 else '−' if pct < 0 else '±'}{abs(pct)}%)"
    return text


def target_label(t: Target) -> str:
    return tr(f"preset.{t.preset}") if t.preset else t.format.name


# --- list delegate -------------------------------------------------------------------------------

# Items only carry plain values (an index into Popup.shown and the engine name); the Target objects
# themselves stay on the Python side.
INDEX_ROLE = Qt.ItemDataRole.UserRole
ENGINE_ROLE = Qt.ItemDataRole.UserRole + 1


class TargetDelegate(QStyledItemDelegate):
    def __init__(self, palette: theme.Palette, popup: Popup, parent=None) -> None:
        super().__init__(parent)
        self.pal = palette
        self.popup = popup

    def sizeHint(self, option, index) -> QSize:
        return QSize(CARD_WIDTH - 40, ROW_HEIGHT)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        target = self.popup.target_at(index.row())
        if target is None:
            return
        engine: str = index.data(ENGINE_ROLE) or ""
        r = option.rect.adjusted(2, 1, -2, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(self.pal.selection if selected else self.pal.surface_hover))
            p.drawRoundedRect(QRectF(r), 8, 8)

        color = theme.PRESET_COLOR if target.is_preset else theme.CATEGORY_COLORS[target.format.category]
        badge = badge_pixmap(target.format.ext.upper(), color, QSize(44, 24), radius=6)
        p.drawPixmap(r.left() + 8, r.top() + (r.height() - 24) // 2, badge)

        right_text = f"⬇ {engine_name(engines.get(engine))}" if engine else ("Enter ↵" if selected else "")
        right_w = 0
        if right_text:
            p.setFont(theme.ui_font(8.5))
            p.setPen(theme.qcolor(self.pal.muted))
            right_w = p.fontMetrics().horizontalAdvance(right_text) + 12
            p.drawText(
                r.adjusted(0, 0, -10, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                right_text,
            )

        text_rect = r.adjusted(62, 0, -right_w - 8, 0)
        p.setFont(theme.ui_font(10, QFont.Weight.DemiBold if selected else QFont.Weight.Normal))
        p.setPen(theme.qcolor(self.pal.text))
        title = p.fontMetrics().elidedText(
            target_label(target), Qt.TextElideMode.ElideRight, text_rect.width()
        )
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
        p.restore()


# --- input area ----------------------------------------------------------------------------------


class DropArea(QFrame):
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("drop")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def set_state(self, prop: str, value: bool) -> None:
        self.setProperty(prop, "true" if value else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class DragHandle(QWidget):
    """Header strip: drag to move the frameless popup, double-click to put it back in the center."""

    moved = Signal()
    reset_requested = Signal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                self.moved.emit()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_requested.emit()


class ResultCard(QFrame):
    """The converted file(s): shows size before → after and can be dragged into any app."""

    def __init__(self, popup: Popup) -> None:
        super().__init__()
        self.popup = popup
        self.paths: list[Path] = []
        self._press: QPoint | None = None
        self.setObjectName("result")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 12, 8)
        lay.setSpacing(10)
        self.thumb = QLabel()
        self.thumb.setFixedSize(36, 36)
        lay.addWidget(self.thumb)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.name = QLabel()
        self.name.setFont(theme.ui_font(10, QFont.Weight.DemiBold))
        self.sizes = QLabel(objectName="muted")
        texts.addWidget(self.name)
        texts.addWidget(self.sizes)
        lay.addLayout(texts, 1)
        self.hint = QLabel(objectName="muted")
        self.hint.setFont(theme.ui_font(8.5))
        lay.addWidget(self.hint)

    def set_results(self, paths: list[Path], before: int) -> None:
        self.paths = paths
        first = paths[0]
        name = first.name if len(paths) == 1 else tr("popup.files", count=len(paths))
        self.name.setText(self.name.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, 250))
        self.sizes.setText(size_change(before, sum(_file_size(p) for p in paths)))
        self.hint.setText("⇱ " + tr("popup.drag_hint"))
        thumb = image_thumbnail(first, 36) if first.suffix.lower() not in (".pdf",) else None
        if thumb:
            self.thumb.setPixmap(thumb[0])
        else:
            fmt = detect(first)
            color = theme.CATEGORY_COLORS[fmt.category] if fmt else self.popup.pal.accent
            self.thumb.setPixmap(badge_pixmap(first.suffix.lstrip(".").upper()[:4], color, QSize(36, 36), 8))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()

    def mouseMoveEvent(self, event) -> None:
        if self._press is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in self.paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = self.thumb.pixmap()
        if pixmap is not None and not pixmap.isNull():
            drag.setPixmap(pixmap)
        self.popup.modal_open = True  # don't auto-hide while the file is being dragged out
        try:
            result = drag.exec(Qt.DropAction.CopyAction)
        finally:
            self.popup.modal_open = False
        if result != Qt.DropAction.IgnoreAction:
            self.popup.hide()

    def mouseReleaseEvent(self, event) -> None:
        self._press = None

    def mouseDoubleClickEvent(self, event) -> None:
        if self.paths:
            windows.open_file(self.paths[0])
            self.popup.hide()


# --- popup ---------------------------------------------------------------------------------------


class Popup(QWidget):
    def __init__(self, controller: Controller) -> None:
        # Windows 11: a captioned window (caption removed in nativeEvent) so DWM can draw acrylic,
        # the system shadow and rounded corners. Otherwise: frameless, with a painted shadow.
        self.acrylic = controller.settings.effects and effects.backdrop_supported()
        flags = Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
        if not self.acrylic:
            flags |= Qt.WindowType.FramelessWindowHint
        super().__init__(None, flags)
        self.controller = controller
        self.pal = theme.glass(theme.current()) if self.acrylic else theme.current()
        self._margin = 0 if self.acrylic else SHADOW
        self._source_size = 0
        self.items: list[InputItem] = []
        self.targets: list[Target] = []
        self.task: ConvertTask | None = None
        self.install_task: InstallTask | None = None
        self.pending_target: Target | None = None
        self.results: list[Path] = []
        self.modal_open = False
        self._dragged = False
        self._mode = "idle"
        self._installing_name = ""
        self._tmp = tempfile.TemporaryDirectory(prefix="morphto-clip-")
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(AUTO_HIDE_MS)
        self._hide_timer.timeout.connect(self._auto_hide)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setWindowTitle("MorphTo")
        self.setStyleSheet(theme.stylesheet(self.pal, glass=self.acrylic))
        self.setFont(theme.ui_font(10))
        self._build()
        if self.acrylic:
            self._enable_acrylic()

    def _enable_acrylic(self) -> None:
        hwnd = int(self.winId())  # creates the native window
        effects.prepare_captionless(hwnd)
        if not effects.apply_acrylic(hwnd, self.pal.dark):
            # The system refused the backdrop: fall back to an opaque card (DWM still gives shadow/corners).
            self.pal = theme.current()
            self.setStyleSheet(theme.stylesheet(self.pal, glass=False, square=True))

    def nativeEvent(self, event_type, message):
        if self.acrylic and event_type == b"windows_generic_MSG":
            handled = effects.handle_native_event(int(message))
            if handled is not None:
                return handled
        return super().nativeEvent(event_type, message)

    # ---- layout ----

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        m = self._margin
        outer.setContentsMargins(m, max(0, m - 6), m, m + 6 if m else 0)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        card = QFrame(objectName="card")
        card.setFixedWidth(CARD_WIDTH)
        if not self.acrylic:
            shadow = QGraphicsDropShadowEffect(card, blurRadius=36)
            shadow.setOffset(0, 8)
            shadow.setColor(QColor(0, 0, 0, 110 if self.pal.dark else 60))
            card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 14)
        lay.setSpacing(10)

        # header (also the drag handle)
        handle = DragHandle()
        handle.moved.connect(self._on_dragged)
        handle.reset_requested.connect(self._reset_position)
        header = QHBoxLayout(handle)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        logo = QLabel()
        logo.setPixmap(
            render_icon(32).scaled(
                18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )
        header.addWidget(logo)
        title = QLabel("MorphTo", objectName="title")
        header.addWidget(title)
        header.addStretch()
        self.settings_btn = QToolButton(text="⚙")
        self.settings_btn.setFont(theme.ui_font(11))
        self.settings_btn.clicked.connect(self.controller.open_settings)
        self.close_btn = QToolButton(text="✕")
        self.close_btn.clicked.connect(self.hide)
        header.addWidget(self.settings_btn)
        header.addWidget(self.close_btn)
        lay.addWidget(handle)

        # input area
        self.drop = DropArea()
        self.drop.clicked.connect(self.browse)
        self.drop_stack = QStackedWidget(self.drop)
        drop_lay = QVBoxLayout(self.drop)
        drop_lay.setContentsMargins(12, 10, 12, 10)
        drop_lay.addWidget(self.drop_stack)

        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setContentsMargins(0, 8, 0, 8)
        el.setSpacing(4)
        self.drop_hint = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, objectName="muted", wordWrap=True)
        self.clip_hint = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, objectName="hint", wordWrap=True)
        el.addWidget(self.drop_hint)
        el.addWidget(self.clip_hint)
        self.drop_stack.addWidget(empty)

        filled = QWidget()
        fl = QHBoxLayout(filled)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(12)
        self.thumb = QLabel()
        self.thumb.setFixedSize(48, 48)
        fl.addWidget(self.thumb)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.in_name = QLabel()
        self.in_name.setFont(theme.ui_font(10.5, QFont.Weight.DemiBold))
        self.in_details = QLabel(objectName="muted")
        texts.addStretch()
        texts.addWidget(self.in_name)
        texts.addWidget(self.in_details)
        texts.addStretch()
        fl.addLayout(texts, 1)
        self.clear_btn = QToolButton(text="✕")
        self.clear_btn.clicked.connect(self.reset)
        fl.addWidget(self.clear_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.drop_stack.addWidget(filled)
        lay.addWidget(self.drop)

        # search + list
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(False)
        self.search.textChanged.connect(self._refilter)
        self.search.installEventFilter(self)
        lay.addWidget(self.search)

        self.list = QListWidget()
        self.shown: list[Target] = []
        self.list.setItemDelegate(TargetDelegate(self.pal, self, self.list))
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self.list)

        self.empty_label = QLabel(objectName="muted", alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty_label)

        # status area
        self.status = QWidget()
        sl = QVBoxLayout(self.status)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)
        self.progress = QProgressBar(textVisible=False, maximum=1000)
        self.progress.setFixedHeight(6)
        sl.addWidget(self.progress)
        self.result_card = ResultCard(self)
        sl.addWidget(self.result_card)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.status_label = QLabel(wordWrap=True)
        row.addWidget(self.status_label, 1)
        self.btn_primary = QPushButton(objectName="primary")
        self.btn_secondary = QPushButton()
        self.btn_cancel = QPushButton()
        for b in (self.btn_secondary, self.btn_primary, self.btn_cancel):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(b)
        sl.addLayout(row)
        lay.addWidget(self.status)

        self.btn_cancel.clicked.connect(self.cancel)
        self._primary_action = self._secondary_action = None
        # Signals are connected to methods, never lambdas: PySide6 inspects lambda closures, which
        # crashes with Nuitka-compiled code.
        self.btn_primary.clicked.connect(self._on_primary)
        self.btn_secondary.clicked.connect(self._on_secondary)
        self.retranslate()
        self.reset()

    def target_at(self, row: int) -> Target | None:
        return self.shown[row] if 0 <= row < len(self.shown) else None

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        target = self.target_at(self.list.row(item))
        if target is not None:
            self.start(target)

    def _on_primary(self) -> None:
        if self._primary_action:
            self._primary_action()

    def _on_secondary(self) -> None:
        if self._secondary_action:
            self._secondary_action()

    def retranslate(self) -> None:
        self.drop_hint.setText(tr("popup.drop_hint"))
        self.search.setPlaceholderText(tr("popup.search"))
        self.settings_btn.setToolTip(tr("popup.settings"))
        self.close_btn.setToolTip(tr("popup.close"))
        self.btn_cancel.setText(tr("popup.cancel"))
        self.empty_label.setText(tr("popup.no_targets"))

    # ---- showing / hiding ----

    def busy(self) -> bool:
        return self.task is not None or self.install_task is not None

    def summon(self, files: list[str] | None = None, keep: bool = False) -> None:
        """Show the popup, optionally preloaded with files.

        It starts fresh unless a job is running or ``keep`` asks to show the last result again.
        """
        self._hide_timer.stop()
        if files:
            if not self.busy():
                self.reset()
                self.load_paths([Path(f) for f in files])
        elif not self.busy() and not keep:
            self.reset()
        if not self.busy():
            self.controller.busy.reset()
        self._update_clip_hint()
        self._place()
        self.show()
        self.raise_()
        self.activateWindow()
        windows.force_foreground(int(self.winId()))
        self.search.setFocus()

    def _show_drop_page(self, index: int) -> None:
        """Switch the input area page; only the visible page counts for the height."""
        for i in range(self.drop_stack.count()):
            policy = QSizePolicy.Policy.Preferred if i == index else QSizePolicy.Policy.Ignored
            self.drop_stack.widget(i).setSizePolicy(QSizePolicy.Policy.Preferred, policy)
        self.drop_stack.setCurrentIndex(index)
        self.drop_stack.updateGeometry()
        self.drop.layout().invalidate()

    def _fit(self) -> None:
        """Resize the window to its content right away (the layout would otherwise lag a frame)."""
        for widget in (self.findChild(QFrame, "card"), self):
            widget.layout().invalidate()
            widget.layout().activate()
        self.adjustSize()

    def toggle(self) -> None:
        if self.isVisible() and self.isActiveWindow():
            self.hide()
        else:
            self.summon()

    def _place(self) -> None:
        self.adjustSize()
        saved = self.controller.settings.popup_pos
        if saved and self._fits_on_screen(QPoint(*saved)):
            self.move(*saved)
            return
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + int(geo.height() * 0.18)
        self.move(x, y)

    def _fits_on_screen(self, pos: QPoint) -> bool:
        """The header must stay reachable (e.g. a monitor that was disconnected)."""
        grip = pos + QPoint(self._margin + 40, self._margin + 10)
        return QGuiApplication.screenAt(grip) is not None

    def _on_dragged(self) -> None:
        self._dragged = True

    def _reset_position(self) -> None:
        self.controller.settings.popup_pos = None
        self.controller.save_settings()
        self._place()

    def hideEvent(self, event) -> None:
        if self._dragged:
            self._dragged = False
            self.controller.settings.popup_pos = [self.x(), self.y()]
            self.controller.save_settings()
        super().hideEvent(event)

    # The popup deliberately does NOT hide when another window gets focus: files are dragged into it
    # from Explorer and results are dragged out of it into other apps. It closes on Esc, ✕, the hotkey,
    # the tray icon, after a drag-out, or by itself a few seconds after a finished conversion.

    def _auto_hide(self) -> None:
        if self.underMouse() or self.modal_open:  # hovered, a dialog is open, or a file is being dragged out
            self._hide_timer.start()
            return
        self.hide()

    def enterEvent(self, event) -> None:
        if self._hide_timer.isActive():
            self._hide_timer.start()  # restart the countdown while the mouse is over the popup
        super().enterEvent(event)

    # ---- state ----

    def reset(self) -> None:
        if self.busy():
            return
        self.items = []
        self.targets = []
        self.results = []
        self.pending_target = None
        self.search.clear()
        self._show_drop_page(0)
        self.drop.set_state("filled", False)
        self.list.clear()
        self.list.hide()
        self.empty_label.hide()
        self.search.hide()
        self._set_status("idle")
        self._update_clip_hint()

    def _set_status(self, mode: str, text: str = "", primary: str = "", secondary: str = "") -> None:
        self._mode = mode
        self.status.setVisible(mode != "idle")
        self.progress.setVisible(mode in ("running", "installing"))
        self.result_card.setVisible(mode == "done")
        self.btn_cancel.setVisible(mode in ("running", "installing"))
        self.btn_primary.setVisible(bool(primary))
        self.btn_secondary.setVisible(bool(secondary))
        self.btn_primary.setText(primary)
        self.btn_secondary.setText(secondary)
        self.status_label.setText(text)
        name = {"done": "success", "error": "error"}.get(mode, "")
        self.status_label.setObjectName(name)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self._primary_action = self._secondary_action = None
        if mode in ("running", "installing"):
            self.progress.setValue(0)
        self._fit()

    # ---- input ----

    def load_paths(self, paths: list[Path], from_clipboard: bool = False) -> None:
        items: list[InputItem] = []
        bad: list[str] = []
        for path in dict.fromkeys(paths):  # drop duplicates, keep order
            if path.is_dir():
                continue
            fmt = detect(path)
            if fmt is None or fmt.id not in default_registry().sources():
                bad.append(path.name)
            else:
                items.append(InputItem(path, fmt, from_clipboard))
        if not items:
            if bad:
                self._set_status("error", tr("popup.unsupported", name=", ".join(bad[:3])))
            return
        self.items = items
        self._set_status("idle")
        if bad:
            self._set_status("error", tr("popup.unsupported", name=", ".join(bad[:3])))
        self._show_items()
        self._compute_targets()

    def _show_items(self) -> None:
        self._show_drop_page(1)
        self.drop.set_state("filled", True)
        first = self.items[0]
        size = sum(_file_size(i.path) for i in self.items)
        if len(self.items) == 1:
            name = first.path.name
            details = [first.fmt.name, human_size(size)]
            thumb = image_thumbnail(first.path) if first.fmt.category == Category.IMAGE else None
            if thumb:
                self.thumb.setPixmap(thumb[0])
                details.append(f"{thumb[1][0]}×{thumb[1][1]}")
            else:
                self.thumb.setPixmap(
                    badge_pixmap(
                        first.fmt.ext.upper(), theme.CATEGORY_COLORS[first.fmt.category], QSize(48, 48), 10
                    )
                )
        else:
            name = tr("popup.files", count=len(self.items))
            names = sorted({i.fmt.name for i in self.items})
            details = [", ".join(names[:3]) + ("…" if len(names) > 3 else ""), human_size(size)]
            self.thumb.setPixmap(badge_pixmap(str(len(self.items)), self.pal.accent, QSize(48, 48), 10))
        metrics = self.in_name.fontMetrics()
        self.in_name.setText(metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, CARD_WIDTH - 140))
        self.in_details.setText(" · ".join(details))

    def _compute_targets(self) -> None:
        fmts = list({i.fmt.id: i.fmt for i in self.items}.values())
        history = self.controller.settings.history.get(fmts[0].id, {})
        self.targets = rank(available_targets(fmts), fmts[0], history)
        self.search.show()
        self.search.clear()
        self._refilter()
        self.search.setFocus()

    def _missing_engine(self, target: Target) -> str:
        try:
            for item in self.items:
                missing = required_engines(plan(item.fmt, target.format))
                if missing:
                    return missing[0]
        except ConversionError:
            pass
        return ""

    def _refilter(self) -> None:
        if not self.items:
            return
        query = self.search.text().strip().lower().lstrip(".")
        matches = [t for t in self.targets if _matches(t, query)]
        if query:
            exact = [
                t for t in matches if not t.is_preset and (t.id == query or query in t.format.extensions)
            ]
            matches = exact + [t for t in matches if t not in exact]
        self.list.clear()
        self.shown = []
        for t in matches:
            item = QListWidgetItem()
            item.setData(INDEX_ROLE, len(self.shown))
            item.setData(ENGINE_ROLE, self._missing_engine(t))
            self.shown.append(t)
            item.setSizeHint(QSize(CARD_WIDTH - 28, ROW_HEIGHT))
            self.list.addItem(item)
        has = bool(matches)
        self.list.setVisible(has)
        self.empty_label.setVisible(not has)
        if has:
            self.list.setCurrentRow(0)
            self.list.setFixedHeight(min(len(matches), MAX_ROWS) * ROW_HEIGHT + 4)
        self._fit()

    # ---- clipboard / browse / drop ----

    def _update_clip_hint(self) -> None:
        info = clipboard.peek()
        if info is None:
            self.clip_hint.hide()
            return
        if info.kind == "image":
            size = info.image_size
            what = tr("popup.clipboard_image", w=size[0], h=size[1]) if size else tr("category.image")
        else:
            sources = default_registry().sources()
            files = [f for f in info.files[:50] if (fmt := detect(f)) is not None and fmt.id in sources]
            if not files:  # e.g. a copied .exe: nothing we could convert, so don't offer it
                self.clip_hint.hide()
                return
            what = files[0].name if len(files) == 1 else tr("popup.files", count=len(files))
        self.clip_hint.setText(tr("popup.clipboard_hint", what=what))
        self.clip_hint.show()

    def paste(self) -> bool:
        if self.busy():
            return False
        try:
            content = clipboard.read()
        except clipboard.ClipboardBusy:
            return False
        if content is None:
            return False
        self.reset()
        if isinstance(content, Image.Image):
            path = Path(self._tmp.name) / time.strftime("clipboard-%Y%m%d-%H%M%S.png")
            content.save(path, "PNG", compress_level=1)
            self.load_paths([path], from_clipboard=True)
        else:
            self.load_paths(content)
        return True

    def browse(self) -> None:
        if self.busy():
            return
        exts = sorted(
            {e for f in all_formats() if f.id in default_registry().sources() for e in f.extensions}
        )
        pattern = " ".join(f"*.{e}" for e in exts)
        self.modal_open = True
        try:
            files, _ = QFileDialog.getOpenFileNames(
                self, tr("popup.pick_files"), "", f"MorphTo ({pattern});;* (*.*)"
            )
        finally:
            self.modal_open = False
        self.summon_after_modal()
        if files:
            self.reset()
            self.load_paths([Path(f) for f in files])

    def summon_after_modal(self) -> None:
        self.raise_()
        self.activateWindow()
        self.search.setFocus()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and not self.busy():
            event.acceptProposedAction()
            self.drop.set_state("hover", True)

    def dragLeaveEvent(self, event) -> None:
        self.drop.set_state("hover", False)

    def dropEvent(self, event) -> None:
        self.drop.set_state("hover", False)
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.reset()
            self.load_paths(paths)
            self.activateWindow()

    # ---- keyboard ----

    def eventFilter(self, obj, event) -> bool:
        if obj is self.search and event.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            if event.matches(QKeySequence.StandardKey.Paste):
                if event.type() == QEvent.Type.ShortcutOverride:
                    if clipboard.has_content():
                        event.accept()
                        return True
                    return False
                if not self.paste():
                    self.search.paste()  # plain text that is not a file: normal paste into the search box
                return True
            if event.type() == QEvent.Type.KeyPress:
                return self._handle_key(event)
        return super().eventFilter(obj, event)

    def _handle_key(self, event) -> bool:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.hide()
            return True
        if (
            key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp)
            and self.list.count()
        ):
            step = {
                Qt.Key.Key_Down: 1,
                Qt.Key.Key_Up: -1,
                Qt.Key.Key_PageDown: MAX_ROWS,
                Qt.Key.Key_PageUp: -MAX_ROWS,
            }[key]
            row = max(0, min(self.list.count() - 1, self.list.currentRow() + step))
            self.list.setCurrentRow(row)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._mode == "engine":
                self.btn_primary.click()
            elif self._mode == "done":
                self.hide()
            elif self.items and self.list.currentItem() is not None and not self.busy():
                target = self.target_at(self.list.currentRow())
                if target is not None:
                    self.start(target)
            elif not self.items:
                self.paste()
            return True
        if key == Qt.Key.Key_O and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.browse()
            return True
        return False

    def keyPressEvent(self, event) -> None:
        if not self._handle_key(event):
            super().keyPressEvent(event)

    # ---- conversion ----

    def start(self, target: Target) -> None:
        if self.busy() or not self.items:
            return
        missing = sorted({e for i in self.items for e in _safe_required(i.fmt, target)})
        if missing:
            self.pending_target = target
            engine = engines.get(missing[0])
            self._set_status(
                "engine",
                tr("engine.needed", engine=engine_name(engine), size=engine.size_mb),
                primary=tr("engine.install"),
            )
            self._primary_action = lambda: self.install(missing)
            self.btn_primary.setFocus()
            return
        jobs = self._jobs(target)
        if jobs is None:
            return
        for fmt_id in {i.fmt.id for i in self.items}:
            self.controller.settings.record_use(fmt_id, target.id)
        self.controller.save_settings()

        self._source_size = sum(_file_size(i.path) for i in self.items)
        self.task = ConvertTask(jobs, target, copy_to_clipboard=self.controller.settings.copy_to_clipboard)
        self.task.signals.progress.connect(self._on_progress)
        self.task.signals.finished.connect(self._on_finished)
        self._set_status("running", tr("popup.converting", percent=0))
        self.controller.busy.update(0.0, tr("popup.converting", percent=0))
        self.list.setEnabled(False)
        self.search.setEnabled(False)
        QThreadPool.globalInstance().start(self.task)

    def _jobs(self, target: Target) -> list[Job] | None:
        s = self.controller.settings
        folder = Path(s.output_dir)
        if s.output_mode == "ask":
            self.modal_open = True
            try:
                first = self.items[0]
                start_dir = folder if first.from_clipboard else first.path.parent
                if len(self.items) == 1:
                    suggested = start_dir / f"{first.path.stem}.{target.format.ext}"
                    pattern = " ".join(f"*.{e}" for e in target.format.extensions)
                    name, _ = QFileDialog.getSaveFileName(
                        self, tr("popup.save_as"), str(suggested), f"{target.format.name} ({pattern})"
                    )
                    if not name:
                        return None
                    out = Path(name)
                    if out.suffix.lower().lstrip(".") not in target.format.extensions:
                        out = out.with_name(f"{out.name}.{target.format.ext}")
                    return [Job(first.path, output=out)]
                directory = QFileDialog.getExistingDirectory(self, tr("popup.choose_folder"), str(start_dir))
                if not directory:
                    return None
                return [Job(i.path, out_dir=Path(directory)) for i in self.items]
            finally:
                self.modal_open = False
                self.summon_after_modal()
        if s.output_mode == "folder":
            return [Job(i.path, out_dir=folder) for i in self.items]
        return [Job(i.path, out_dir=folder if i.from_clipboard else None) for i in self.items]

    def _on_progress(self, index: int, total: int, fraction: float) -> None:
        overall = (index + max(0.0, min(1.0, fraction))) / total
        self.progress.setValue(int(overall * 1000))
        percent = int(overall * 100)
        if total > 1:
            text = tr("popup.converting_many", current=index + 1, total=total, percent=percent)
        else:
            text = tr("popup.converting", percent=percent)
        self.status_label.setText(text)
        self.controller.busy.update(overall, text)

    def _on_finished(self, results: list[Path], errors: list[str], cancelled: bool, copied: bool) -> None:
        self.task = None
        self.list.setEnabled(True)
        self.search.setEnabled(True)
        self.results = results
        if cancelled:
            self.controller.busy.reset()
            self._set_status("error", tr("popup.cancelled"))
            return
        if not results:
            self._set_status("error", tr("popup.failed", error="; ".join(errors)[:300]))
            self.controller.busy.finish(self.isVisible(), failed=True)
            if not self.isVisible():
                self.controller.notify(tr("tray.failed"), "; ".join(errors)[:200], error=True)
            return
        self.controller.busy.finish(self.isVisible())

        if errors:
            text = tr("popup.done_partial", ok=len(results), total=len(results) + len(errors))
        else:
            text = tr("popup.done_copied") if copied else tr("popup.done")
        text = f"✓ {text}"
        first = results[0]
        self._set_status("done", text, primary=tr("popup.open"), secondary=tr("popup.show_in_folder"))
        self.result_card.set_results(results, self._source_size)
        self._primary_action = lambda: (windows.open_file(first), self.hide())
        self._secondary_action = lambda: (windows.reveal_in_explorer(first), self.hide())
        if errors:
            self.status_label.setToolTip("\n".join(errors))
        if self.isVisible():
            self._hide_timer.start()
        else:
            self.controller.notify(tr("tray.done", name=first.name), str(first.parent))

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel.set()
        if self.install_task is not None:
            self.install_task.cancel.set()

    # ---- engines ----

    def install(self, engine_ids: list[str]) -> None:
        if self.install_task is not None:
            return
        name = ", ".join(engine_name(engines.get(e)) for e in engine_ids)
        self._installing_name = name
        self.install_task = InstallTask(engine_ids)
        self.install_task.signals.progress.connect(self._on_install_progress)
        self.install_task.signals.finished.connect(self._on_installed)
        self._set_status("installing", tr("engine.installing", engine=name, percent=0))
        self.controller.busy.update(0.0, tr("engine.installing", engine=name, percent=0))
        QThreadPool.globalInstance().start(self.install_task)

    def _on_install_progress(self, fraction: float) -> None:
        text = tr("engine.installing", engine=self._installing_name, percent=int(fraction * 100))
        self.progress.setValue(int(fraction * 1000))
        self.status_label.setText(text)
        self.controller.busy.update(fraction, text)

    def _on_installed(self, error: str) -> None:
        name = self._installing_name
        self.install_task = None
        if error:
            self.controller.busy.finish(self.isVisible(), failed=True)
            self._set_status("error", tr("engine.failed", engine=name, error=error))
            return
        self.controller.busy.reset()
        self._refilter()
        target, self.pending_target = self.pending_target, None
        if target is not None and self.items:
            self.start(target)
        else:
            self._set_status("idle")


def _matches(t: Target, query: str) -> bool:
    if not query:
        return True
    if t.id.startswith(query) or any(e.startswith(query) for e in t.format.extensions):
        return True
    return query in target_label(t).lower() or (t.preset is not None and query in t.preset)


def _safe_required(fmt: Format, target: Target) -> list[str]:
    try:
        return required_engines(plan(fmt, target.format))
    except ConversionError:
        return []


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
