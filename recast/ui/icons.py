"""Render bundled Fluent icons to tinted pixmaps (cached)."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .icon_data import ICONS


@lru_cache(maxsize=256)
def icon_pixmap(name: str, rgba: str, px: int, dpr: float) -> QPixmap | None:
    data = ICONS.get(name)
    if data is None:
        return None
    viewbox, paths = data
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">'
           + "".join(f'<path fill="{rgba}" d="{d}"/>' for d in paths) + "</svg>")
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    size = round(px * dpr)
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm
