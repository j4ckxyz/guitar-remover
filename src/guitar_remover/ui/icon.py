"""The app icon, drawn in code so it is crisp at every size and needs no assets."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap)


def pick_path(cx: float, cy: float, w: float) -> QPainterPath:
    """A guitar-pick shape centred on (cx, cy) with width w."""
    h = w * 1.12
    top = cy - h * 0.45
    p = QPainterPath()
    p.moveTo(cx, cy + h * 0.55)
    p.cubicTo(cx - w * 0.28, cy + h * 0.30, cx - w * 0.55, cy - h * 0.05, cx - w * 0.48, top + h * 0.12)
    p.cubicTo(cx - w * 0.42, top - h * 0.04, cx + w * 0.42, top - h * 0.04, cx + w * 0.48, top + h * 0.12)
    p.cubicTo(cx + w * 0.55, cy - h * 0.05, cx + w * 0.28, cy + h * 0.30, cx, cy + h * 0.55)
    return p


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    m = size * 0.09  # macOS-style margin
    r = QRectF(m, m, size - 2 * m, size - 2 * m)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor("#ff8a3d"))
    g.setColorAt(1, QColor("#e2366b"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(r, r.width() * 0.225, r.width() * 0.225)

    cx, cy = size / 2, size / 2 + size * 0.02
    p.setBrush(QColor(255, 255, 255, 240))
    p.drawPath(pick_path(cx, cy, size * 0.46))

    # A waveform "cut" through the pick: the guitar being lifted out.
    pen = QPen(QColor("#e8505b"), max(1.0, size * 0.035))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    bars = [0.10, 0.22, 0.34, 0.18, 0.28, 0.12, 0.06]
    step = size * 0.055
    x0 = cx - step * (len(bars) - 1) / 2
    for i, b in enumerate(bars):
        x = x0 + i * step
        p.drawLine(QPointF(x, cy - size * b / 2 - size * 0.03),
                   QPointF(x, cy + size * b / 2 - size * 0.03))
    p.end()
    return img


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 32, 64, 128, 256, 512, 1024):
        icon.addPixmap(QPixmap.fromImage(render(s)))
    return icon
