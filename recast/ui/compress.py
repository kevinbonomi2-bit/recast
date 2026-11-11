"""Compress-to window: pick a target size, a reduction or a preset; the bar shows before and after."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QIntValidator, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QSlider, QStackedWidget, QVBoxLayout, QWidget

from ..estimate import human
from ..formats import category_of
from . import kit, theme

PRESETS = {"light": (0.7, "Barely visible change"), "balanced": (0.5, "Good for sharing and email"),
           "strong": (0.3, "Smallest files, softer detail")}


class SizeBar(QWidget):
    """Original size as a full bar, the target as an accent bar of the same scale."""

    def __init__(self, original: int, pal, accent: QColor):
        super().__init__()
        self.original, self.ratio, self.pal, self.accent = original, 0.5, pal, accent
        self.setFixedHeight(92)

    def set_ratio(self, r: float) -> None:
        self.ratio = max(0.01, min(1.0, r))
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        rows = ((f"Now  {human(self.original)}", 1.0, self.pal.track, self.pal.meta),
                (f"Target  ≈ {human(self.original * self.ratio)}", self.ratio, self.accent, self.pal.text))
        for k, (text, frac, color, ink) in enumerate(rows):
            y = 4 + k * 46
            p.setFont(theme.font(13, theme.QFont.Weight.DemiBold))
            p.setPen(ink)
            p.drawText(QRectF(0, y, w, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.pal.track)
            p.drawRoundedRect(QRectF(0, y + 22, w, 12), 6, 6)
            p.setBrush(color if k else theme.mix(self.pal.track, self.pal.meta, 0.35))
            p.drawRoundedRect(QRectF(0, y + 22, max(12.0, w * frac), 12), 6, 6)
        if self.ratio < 1:
            p.setFont(theme.font(13, theme.QFont.Weight.Bold))
            p.setPen(self.accent)
            p.drawText(QRectF(0, 50, w, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{(1 - self.ratio) * 100:.0f}% smaller")
        p.end()


class CompressWindow(kit.ToolWindow):
    def __init__(self, files: list[Path], pal, accent: QColor):
        self.files = files
        self.total = sum(f.stat().st_size for f in files)
        self.cat = category_of(files[0])
        what = files[0].name if len(files) == 1 else f"{len(files)} files"
        super().__init__("Compress to a size", f"{what} · {human(self.total)}", "Compress", pal, accent)

        card = QWidget()
        card.setProperty("role", "card")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 18)
        self.bar = SizeBar(self.total, pal, accent)
        lay.addWidget(self.bar)
        self.body.addWidget(card)

        self.mode = kit.Segmented([("size", "Target size"), ("reduce", "Reduce by"), ("preset", "Preset")],
                                  "reduce", pal, accent)
        self.body.addWidget(self.mode)
        self.stack = QStackedWidget()
        self.body.addWidget(self.stack)

        # target size
        size_page = QWidget()
        row = QHBoxLayout(size_page)
        row.setContentsMargins(0, 0, 0, 0)
        self.size_edit = QLineEdit()
        self.size_edit.setValidator(QDoubleValidator(0.01, 1e6, 2))
        self.size_edit.setFixedWidth(120)
        self.unit = kit.Segmented([("KB", "KB"), ("MB", "MB")], "MB" if self.total > 2e6 else "KB", pal, accent,
                                  seg_width=56)
        half = self.total / 2
        self.size_edit.setText(f"{half / 1e6:.1f}" if self.unit.value == "MB" else f"{half / 1e3:.0f}")
        row.addWidget(self.size_edit)
        row.addWidget(self.unit)
        row.addStretch(1)
        self.stack.addWidget(size_page)

        # reduce by
        reduce_page = QWidget()
        row = QHBoxLayout(reduce_page)
        row.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(10, 95)
        self.slider.setValue(50)
        self.slider_label = kit.label("50% smaller")
        self.slider_label.setFixedWidth(110)
        row.addWidget(self.slider, 1)
        row.addWidget(self.slider_label)
        self.stack.addWidget(reduce_page)

        # preset
        preset_page = QWidget()
        row = QHBoxLayout(preset_page)
        row.setContentsMargins(0, 0, 0, 0)
        self.preset = kit.Segmented([("light", "Light"), ("balanced", "Balanced"), ("strong", "Strong")],
                                    "balanced", pal, accent)
        self.preset_note = kit.label(PRESETS["balanced"][1], "meta")
        row.addWidget(self.preset)
        row.addSpacing(10)
        row.addWidget(self.preset_note)
        row.addStretch(1)
        self.stack.addWidget(preset_page)

        # resolution, where it helps
        self.max_side = self.max_height = None
        extra = QHBoxLayout()
        extra.setSpacing(12)
        if self.cat == "image":
            self.resize_on = kit.Toggle(False, pal, accent)
            self.max_side = QLineEdit("2560")
            self.max_side.setValidator(QIntValidator(64, 20000))
            self.max_side.setFixedWidth(80)
            self.max_side.setEnabled(False)
            self.resize_on.toggled.connect(self.max_side.setEnabled)
            extra.addWidget(self.resize_on)
            extra.addWidget(kit.label("Also shrink to fit within"))
            extra.addWidget(self.max_side)
            extra.addWidget(kit.label("px", "meta"))
        elif self.cat == "video":
            extra.addWidget(kit.label("Resolution"))
            self.max_height = kit.Segmented([("0", "Automatic"), ("1080", "1080p"), ("720", "720p"),
                                             ("480", "480p")], "0", pal, accent)
            extra.addWidget(self.max_height)
        extra.addStretch(1)
        if extra.count() > 1:
            self.body.addLayout(extra)
        self.body.addStretch(1)

        self.mode.changed.connect(self._update)
        self.unit.changed.connect(self._unit_changed)
        self.size_edit.textChanged.connect(self._update)
        self.slider.valueChanged.connect(self._update)
        self.preset.changed.connect(self._update)
        self.hint.setText({"image": "Quality is tuned per image to land on the target.",
                           "video": "Two passes, so the result lands close to the target.",
                           "pdf": "Pictures inside the PDF are made smaller; text stays sharp.",
                           "audio": "Re-encoded as MP3 at the bitrate that fits."}.get(self.cat, ""))
        self._update()
        self.resize(620, 470)

    def _unit_changed(self, unit: str) -> None:
        try:
            v = float(self.size_edit.text())
        except ValueError:
            return
        self.size_edit.setText(f"{v / 1000:.2f}".rstrip("0").rstrip(".") if unit == "MB" else f"{v * 1000:.0f}")

    def ratio(self) -> float:
        mode = self.mode.value
        if mode == "size":
            try:
                v = float(self.size_edit.text()) * (1e6 if self.unit.value == "MB" else 1e3)
            except ValueError:
                return 0.0
            return v / self.total if self.total else 0.0
        if mode == "reduce":
            return 1 - self.slider.value() / 100
        return PRESETS[self.preset.value][0]

    def _update(self, *_) -> None:
        self.stack.setCurrentIndex(["size", "reduce", "preset"].index(self.mode.value))
        self.slider_label.setText(f"{self.slider.value()}% smaller")
        self.preset_note.setText(PRESETS[self.preset.value][1])
        r = self.ratio()
        self.bar.set_ratio(r)
        self.ok.setEnabled(0 < r < 1)

    def options(self) -> dict:
        opts: dict = {"ratio": self.ratio()}
        if self.cat == "image" and self.resize_on.isChecked():
            opts["max_side"] = int(self.max_side.text() or 0)
        if self.cat == "video" and self.max_height is not None:
            opts["max_height"] = int(self.max_height.value)
        return opts
