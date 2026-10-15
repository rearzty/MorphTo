"""Colors and stylesheet. Follows the Windows light/dark setting."""

from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication

from morphto.core.formats import Category


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str
    surface: str
    surface_hover: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    selection: str
    success: str
    danger: str


DARK = Palette(
    dark=True, bg="#1C1D22", surface="#25272E", surface_hover="#2E3039", border="#363944", text="#ECEDF1",
    muted="#8F96A3", accent="#7C6CFF", accent_hover="#8F81FF", selection="rgba(124,108,255,0.22)",
    success="#3DD68C", danger="#FF6B6B",
)  # fmt: skip
LIGHT = Palette(
    dark=False, bg="#FFFFFF", surface="#F4F5F8", surface_hover="#ECEEF3", border="#E2E4EA", text="#1A1C21",
    muted="#6B7280", accent="#6A55F5", accent_hover="#5A44EE", selection="rgba(106,85,245,0.12)",
    success="#16A34A", danger="#DC2626",
)  # fmt: skip

CATEGORY_COLORS = {
    Category.IMAGE: "#FF8A3D",
    Category.VIDEO: "#F0457E",
    Category.AUDIO: "#1FB89C",
    Category.DOCUMENT: "#4F8BFF",
    Category.SPREADSHEET: "#22A861",
    Category.PRESENTATION: "#E8613A",
}
PRESET_COLOR = "#7C6CFF"


def current() -> Palette:
    hints = QGuiApplication.styleHints()
    return DARK if hints.colorScheme() == Qt.ColorScheme.Dark else LIGHT


def ui_font(point_size: float = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies(
        ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Yu Gothic UI", "Malgun Gothic"]
    )
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    return font


def qcolor(value: str) -> QColor:
    if value.startswith("rgba("):
        r, g, b, a = (float(x) for x in value[5:-1].split(","))
        color = QColor(int(r), int(g), int(b))
        color.setAlphaF(a)
        return color
    return QColor(value)


def apply_accent(app) -> None:
    """Make native controls (radio buttons, check boxes, focus rings) use our accent color."""
    from PySide6.QtGui import QPalette

    p = current()
    palette = app.palette()
    for role in (QPalette.ColorRole.Accent, QPalette.ColorRole.Highlight):
        palette.setColor(role, QColor(p.accent))
    app.setPalette(palette)


def glass(p: Palette) -> Palette:
    """Translucent surfaces for use on top of the Windows acrylic backdrop."""
    if p.dark:
        return replace(
            p, surface="rgba(255,255,255,0.06)", surface_hover="rgba(255,255,255,0.10)",
            border="rgba(255,255,255,0.10)",
        )  # fmt: skip
    return replace(
        p, surface="rgba(255,255,255,0.55)", surface_hover="rgba(255,255,255,0.80)",
        border="rgba(0,0,0,0.08)",
    )  # fmt: skip


def stylesheet(p: Palette, glass: bool = False, square: bool = False) -> str:
    """``glass``: the window has a system backdrop, so the card itself stays transparent.
    ``square``: the system draws border and corners, so the card should fill the window flat."""
    if glass:
        card = "background: transparent; border: none;"
    elif square:
        card = f"background: {p.bg}; border: none;"
    else:
        card = f"background: {p.bg}; border: 1px solid {p.border}; border-radius: 14px;"
    return f"""
    * {{ color: {p.text}; }}
    #card {{ {card} }}
    #result {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; }}
    #result:hover {{ border-color: {p.accent}; }}
    #title {{ color: {p.muted}; font-weight: 600; }}
    #muted {{ color: {p.muted}; }}
    #hint {{ color: {p.accent}; }}
    #error {{ color: {p.danger}; }}
    #success {{ color: {p.success}; font-weight: 600; }}
    #drop {{ background: {p.surface}; border: 1.5px dashed {p.border}; border-radius: 10px; }}
    #drop[hover="true"] {{ border-color: {p.accent}; background: {p.surface_hover}; }}
    #drop[filled="true"] {{ border-style: solid; }}
    QLineEdit {{
        background: {p.surface}; border: 1px solid {p.border}; border-radius: 9px;
        padding: 8px 12px; selection-background-color: {p.accent}; font-size: 11pt;
    }}
    QLineEdit:focus {{ border-color: {p.accent}; }}
    QListView {{ background: transparent; border: none; outline: none; }}
    QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 4px; color: {p.muted}; }}
    QToolButton:hover {{ background: {p.surface_hover}; color: {p.text}; }}
    QPushButton {{
        background: {p.surface}; border: 1px solid {p.border}; border-radius: 7px; padding: 5px 12px;
    }}
    QPushButton:hover {{ background: {p.surface_hover}; }}
    QPushButton#primary {{ background: {p.accent}; border-color: {p.accent}; color: white; font-weight: 600; }}
    QPushButton#primary:hover {{ background: {p.accent_hover}; }}
    QProgressBar {{ background: {p.surface}; border: none; border-radius: 3px; max-height: 6px; }}
    QProgressBar::chunk {{ background: {p.accent}; border-radius: 3px; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 3px; min-height: 24px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    QDialog {{ background: {p.bg}; }}
    QComboBox {{
        background: {p.surface}; border: 1px solid {p.border}; border-radius: 7px; padding: 4px 10px;
    }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {p.bg}; border: 1px solid {p.border}; selection-background-color: {p.selection};
        selection-color: {p.text};
    }}
    QGroupBox {{ border: 1px solid {p.border}; border-radius: 10px; margin-top: 14px; padding: 12px 10px 8px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {p.muted}; }}
    QPushButton[mod="true"]:checked {{ background: {p.selection}; border-color: {p.accent}; }}
    QMenu {{ background: {p.bg}; border: 1px solid {p.border}; padding: 4px; }}
    QMenu::item {{ padding: 6px 20px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {p.selection}; }}
    """
