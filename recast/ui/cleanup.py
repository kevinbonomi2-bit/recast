"""Old clips review: pick which old clips go to the Recycle Bin. Nothing is ticked to begin with."""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from .. import estimate
from ..clips import game_from_name
from . import kit, theme
from .theme import Palette

THUMB = QSize(96, 54)


class _Thumbs(QObject):
    ready = Signal(int, object)   # row, QImage | None


class CleanupWindow(kit.ToolWindow):
    def __init__(self, clips: list[Path], days: int, pal: Palette, accent: QColor):
        super().__init__("Old clips", "", "Move to Recycle Bin", pal, pal.error)
        # Set on each box: appending to the window's sheet after it's been applied doesn't reach them.
        check_style = _check_style(pal, accent)
        self.clips = clips
        self.sizes = []
        for c in clips:
            try:
                self.sizes.append(c.stat().st_size)
            except OSError:
                self.sizes.append(0)
        self.subtitle.setText(f"{len(clips)} clips older than {days} days · {estimate.human(sum(self.sizes))}. "
                              "Tick the ones to let go.")
        top = QHBoxLayout()
        self.all = QCheckBox("Select all")
        self.all.setStyleSheet(check_style)
        self.all.toggled.connect(self._select_all)
        top.addWidget(self.all)
        top.addStretch(1)
        self.body.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        page.setProperty("role", "card")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        rows = QVBoxLayout(page)
        rows.setContentsMargins(14, 8, 14, 8)
        rows.setSpacing(2)
        self.checks: list[QCheckBox] = []
        self.thumbs: list[QLabel] = []
        now = time.time()
        for path, size in zip(clips, self.sizes):
            row = QHBoxLayout()
            row.setSpacing(12)
            cb = QCheckBox()
            cb.setStyleSheet(check_style)
            cb.toggled.connect(self._update)
            self.checks.append(cb)
            row.addWidget(cb)
            th = QLabel()
            th.setFixedSize(THUMB)
            th.setStyleSheet(f"background: {pal.track.name()}; border-radius: 6px;")
            self.thumbs.append(th)
            row.addWidget(th)
            text = QVBoxLayout()
            text.setSpacing(0)
            name = QLabel(game_from_name(path) or path.stem)
            name.setStyleSheet("font-weight: 600;")
            text.addWidget(name)
            try:
                age = int((now - path.stat().st_mtime) // 86400)
            except OSError:
                age = 0
            text.addWidget(kit.label(f"{age} days old · {estimate.human(size)} · {path.name}", "meta"))
            row.addLayout(text, 1)
            holder = QWidget()
            holder.setLayout(row)
            holder.setCursor(Qt.CursorShape.PointingHandCursor)
            holder.mouseReleaseEvent = lambda e, cb=cb: cb.toggle()
            rows.addWidget(holder)
        rows.addStretch(1)
        scroll.setWidget(page)
        self.body.addWidget(scroll, 1)
        self.resize(620, 640)
        self._update()

        self._thumbs = _Thumbs()
        self._thumbs.ready.connect(self._show_thumb)
        self._stop = threading.Event()
        threading.Thread(target=self._load_thumbs, daemon=True).start()
        self.finished.connect(lambda _: self._stop.set())

    def _load_thumbs(self) -> None:
        from .thumbs import make_thumbnail
        for i, path in enumerate(self.clips):
            if self._stop.is_set():
                return
            self._thumbs.ready.emit(i, make_thumbnail(path))

    def _show_thumb(self, i: int, img: QImage | None) -> None:
        if img is None or img.isNull() or i >= len(self.thumbs):
            return
        dpr = self.devicePixelRatioF()
        pm = QPixmap.fromImage(img.scaled(THUMB * dpr, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                          Qt.TransformationMode.SmoothTransformation))
        pm.setDevicePixelRatio(dpr)
        self.thumbs[i].setPixmap(pm)

    def _select_all(self, on: bool) -> None:
        for cb in self.checks:
            cb.blockSignals(True)
            cb.setChecked(on)
            cb.blockSignals(False)
        self._update()

    def _update(self) -> None:
        picked = [s for cb, s in zip(self.checks, self.sizes) if cb.isChecked()]
        self.ok.setEnabled(bool(picked))
        self.ok.setText(f"Move {len(picked)} to Recycle Bin" if picked else "Move to Recycle Bin")
        self.hint.setText(f"Frees {estimate.human(sum(picked))}" if picked else "")

    def options(self) -> dict:
        return {"recycle": [p for cb, p in zip(self.checks, self.clips) if cb.isChecked()]}


def _check_mark() -> str:
    """A white tick for the checked box (Qt style sheets take images from files only)."""
    path = Path(tempfile.gettempdir()) / "recast-check.svg"
    if not path.exists():
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 18"><path d="M4.5 9.2l3 3 6-6.4" '
                        'fill="none" stroke="white" stroke-width="2" stroke-linecap="round" '
                        'stroke-linejoin="round"/></svg>', encoding="utf-8")
    return path.as_posix()


def _check_style(pal: Palette, accent: QColor) -> str:
    line = theme.mix(pal.tile, pal.meta, 0.55).name()
    return f"""
    QCheckBox {{ spacing: 8px; font-weight: 600; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px; border: 2px solid {line};
                            background: {pal.tile.name()}; }}
    QCheckBox::indicator:hover {{ border-color: {accent.name()}; }}
    QCheckBox::indicator:checked {{ background: {accent.name()}; border-color: {accent.name()};
                                    image: url("{_check_mark()}"); }}
    """
