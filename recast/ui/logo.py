"""Recast's logo ("Shift"): a file mid-recast, square on one side of the cut, round on the other.

Geometry on a 256 grid (masters in brand/, written by brand/build_brand.py):
- square half: 75 wide, 156 tall, outer corners r18, inner corners r4 (just softened)
- gap: 14 (the cut); the small-size cut uses 26 so it stays open at 16 px
- round half: a semicircle of r78 (2 units of overshoot so it looks as tall as the square)
- the pair sits 3 units right of centre: the square half carries more weight
"""

from __future__ import annotations

import winreg
from functools import lru_cache

INK, INK_REV, BLUE, BLUE_DARK, TILE = "#14161B", "#F3F4F6", "#2F6BFF", "#4C82FF", "#14161B"
SMALL = dict(gap=26, outer_r=26, inner_r=6)  # for 16–24 px


def _f(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def mark_paths(gap: float = 14, outer_r: float = 18, inner_r: float = 4, scale: float = 1.0,
               cx0: float = 128, cy0: float = 128) -> tuple[str, str]:
    """(square half, round half) SVG path data, centred on (cx0, cy0)."""
    s = scale
    w, h, r = 75 * s, 156 * s, 78 * s
    gap *= s
    r0, r1 = outer_r * s, inner_r * s
    x0 = cx0 - (w + gap + r) / 2 + 3 * s
    top, bot = cy0 - h / 2, cy0 + h / 2
    x1 = x0 + w
    f = _f
    square = (f"M{f(x0 + r0)} {f(top)}H{f(x1 - r1)}A{f(r1)} {f(r1)} 0 0 1 {f(x1)} {f(top + r1)}"
              f"V{f(bot - r1)}A{f(r1)} {f(r1)} 0 0 1 {f(x1 - r1)} {f(bot)}H{f(x0 + r0)}"
              f"A{f(r0)} {f(r0)} 0 0 1 {f(x0)} {f(bot - r0)}V{f(top + r0)}A{f(r0)} {f(r0)} 0 0 1 {f(x0 + r0)} {f(top)}Z")
    cx = x1 + gap
    rnd = f"M{f(cx)} {f(cy0 - r)}A{f(r)} {f(r)} 0 0 1 {f(cx)} {f(cy0 + r)}Z"
    return square, rnd


def svg(body: str, title: str = "Recast") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><title>{title}</title>'
            f"{body}</svg>\n")


def two_tone(ink: str, blue: str, **kw) -> str:
    sq, rd = mark_paths(**kw)
    return f'<path fill="{ink}" d="{sq}"/><path fill="{blue}" d="{rd}"/>'


# A faint light edge keeps the dark tile from dissolving into a dark Start menu or taskbar.
APP_TILE = (f'<rect x="8" y="8" width="240" height="240" rx="56" fill="{TILE}"/>'
            '<rect x="10" y="10" width="236" height="236" rx="54" fill="none" stroke="#ffffff" '
            'stroke-opacity="0.14" stroke-width="4"/>')

MASTERS = {
    "recast-mark.svg": svg(two_tone(INK, BLUE)),
    "recast-mark-reversed.svg": svg(two_tone(INK_REV, BLUE_DARK)),
    "recast-mark-black.svg": svg(two_tone("#000", "#000")),
    "recast-mark-white.svg": svg(two_tone("#fff", "#fff")),
    "recast-mark-small.svg": svg(two_tone(INK, BLUE, **SMALL)),
    "recast-mark-small-reversed.svg": svg(two_tone(INK_REV, BLUE_DARK, **SMALL)),
    "recast-app-icon.svg": svg(APP_TILE + two_tone(INK_REV, BLUE_DARK, scale=0.74)),
    "recast-app-icon-small.svg": svg(APP_TILE + two_tone(INK_REV, BLUE_DARK, scale=0.78, **SMALL)),
}


# ---- Qt ------------------------------------------------------------------------------

def render(svg_text: str, px: int):
    """SVG → QImage of px × px."""
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg_text.encode())).render(p, QRectF(0, 0, px, px))
    p.end()
    return img


def _icon(small: str, large: str):
    from PySide6.QtGui import QIcon, QPixmap
    icon = QIcon()
    for px in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(render(MASTERS[small if px <= 24 else large], px)))
    return icon


@lru_cache(maxsize=None)
def app_icon():
    """Windows, Start menu, installer: the mark on a dark tile."""
    return _icon("recast-app-icon-small.svg", "recast-app-icon.svg")


@lru_cache(maxsize=None)
def tray_icon(dark_taskbar: bool):
    """The bare mark, ink matched to the taskbar so the square half never disappears."""
    if dark_taskbar:
        return _icon("recast-mark-small-reversed.svg", "recast-mark-reversed.svg")
    return _icon("recast-mark-small.svg", "recast-mark.svg")


def taskbar_is_dark() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return not winreg.QueryValueEx(k, "SystemUsesLightTheme")[0]
    except OSError:
        return True
