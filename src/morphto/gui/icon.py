"""The app icon ("Morph": a square turning into a circle), drawn in code so the repo needs no binary assets.

Coordinates are on a 64-unit grid, scaled to the requested size.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap


def render(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    u = size / 64.0

    tile = QRectF(2 * u, 2 * u, 60 * u, 60 * u)
    grad = QLinearGradient(tile.topLeft(), tile.bottomRight())
    grad.setColorAt(0, QColor("#8B5CF6"))
    grad.setColorAt(1, QColor("#22C3E6"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(tile, 15 * u, 15 * u)

    white = QColor("white")
    width = max(1.3, 4.2 * u)  # keep strokes visible at 16 px
    pen = QPen(white, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

    # square (before)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(13 * u, 13 * u, 20 * u, 20 * u), 4.5 * u, 4.5 * u)

    # circle (after)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(white)
    p.drawEllipse(QPointF(43 * u, 43 * u), 10.5 * u, 10.5 * u)

    # arrow from one to the other
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    arc = QPainterPath(QPointF(38 * u, 13.5 * u))
    arc.quadTo(QPointF(50.5 * u, 13.5 * u), QPointF(50.5 * u, 25.5 * u))
    p.drawPath(arc)
    head = QPainterPath(QPointF(44.5 * u, 21 * u))
    head.lineTo(QPointF(50.5 * u, 27 * u))
    head.lineTo(QPointF(56.5 * u, 21 * u))
    p.drawPath(head)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(render(size))
    return icon
