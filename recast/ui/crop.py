"""Crop window: drag a frame over the image, pick an aspect ratio or type exact pixels.

A selection shares one framing: the frame is stored as fractions of the image, and
with an aspect ratio chosen every image keeps that ratio around the same centre.
"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageOps
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QIntValidator, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QWidget

from ..engine.images import open_image
from . import kit, theme

ASPECTS = [("free", "Free"), ("original", "Original"), ("1:1", "1:1"), ("4:3", "4:3"), ("3:2", "3:2"),
           ("16:9", "16:9"), ("9:16", "9:16")]
HANDLE = 14   # px: grab distance for corners and edges
MIN_PX = 16   # smallest frame in screen px


def load_preview(path: Path, max_side: int = 1600) -> tuple[QImage, tuple[int, int]]:
    img = ImageOps.exif_transpose(open_image(path))
    size = img.size
    img.thumbnail((max_side, max_side))
    img = img.convert("RGBA")
    return QImage(img.tobytes(), img.width, img.height, QImage.Format.Format_RGBA8888).copy(), size


class CropCanvas(QWidget):
    changed = Signal()

    def __init__(self, image: QImage, size: tuple[int, int], pal, accent: QColor):
        super().__init__()
        self.pix = QPixmap.fromImage(image)
        self.src_w, self.src_h = size
        self.pal, self.accent = pal, accent
        self.frac = QRectF(0, 0, 1, 1)        # crop in image fractions
        self.aspect: float | None = None      # width / height in source pixels
        self._drag: str | None = None
        self._start = QPointF()
        self._start_frac = QRectF()
        self.setMouseTracking(True)
        self.setMinimumSize(560, 380)

    # geometry: where the image sits on screen
    def image_rect(self) -> QRectF:
        pad = 18
        avail = QRectF(pad, pad, self.width() - 2 * pad, self.height() - 2 * pad)
        sc = min(avail.width() / self.src_w, avail.height() / self.src_h)
        w, h = self.src_w * sc, self.src_h * sc
        return QRectF(avail.center().x() - w / 2, avail.center().y() - h / 2, w, h)

    def screen_rect(self) -> QRectF:
        ir = self.image_rect()
        return QRectF(ir.left() + self.frac.left() * ir.width(), ir.top() + self.frac.top() * ir.height(),
                      self.frac.width() * ir.width(), self.frac.height() * ir.height())

    def crop_pixels(self) -> tuple[int, int]:
        return round(self.frac.width() * self.src_w), round(self.frac.height() * self.src_h)

    def set_aspect(self, aspect: float | None) -> None:
        self.aspect = aspect
        if aspect:
            self.frac = self._fit_aspect(self.frac, keep_center=True)
        self.changed.emit()
        self.update()

    def reset(self) -> None:
        self.frac = QRectF(0, 0, 1, 1)
        if self.aspect:
            self.frac = self._fit_aspect(self.frac, keep_center=True)
        self.changed.emit()
        self.update()

    def _fit_aspect(self, f: QRectF, keep_center: bool) -> QRectF:
        """Largest rect with the aspect ratio inside f (fractions)."""
        a = self.aspect * self.src_h / self.src_w  # aspect in fraction units
        w, h = f.width(), f.height()
        if w / max(h, 1e-9) > a:
            w = h * a
        else:
            h = w / a
        c = f.center()
        r = QRectF(c.x() - w / 2, c.y() - h / 2, w, h)
        return self._clamp(r)

    @staticmethod
    def _clamp(r: QRectF) -> QRectF:
        w, h = min(1.0, r.width()), min(1.0, r.height())
        x = min(max(0.0, r.left()), 1.0 - w)
        y = min(max(0.0, r.top()), 1.0 - h)
        return QRectF(x, y, w, h)

    def _hit(self, p: QPointF) -> str | None:
        r = self.screen_rect()
        near = lambda a, b: abs(a - b) <= HANDLE
        l, rt, t, b = near(p.x(), r.left()), near(p.x(), r.right()), near(p.y(), r.top()), near(p.y(), r.bottom())
        inside_x = r.left() - HANDLE <= p.x() <= r.right() + HANDLE
        inside_y = r.top() - HANDLE <= p.y() <= r.bottom() + HANDLE
        if t and l: return "tl"
        if t and rt: return "tr"
        if b and l: return "bl"
        if b and rt: return "br"
        if l and inside_y: return "l"
        if rt and inside_y: return "r"
        if t and inside_x: return "t"
        if b and inside_x: return "b"
        if r.contains(p): return "move"
        return "new" if self.image_rect().contains(p) else None

    CURSORS = {"tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
               "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
               "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
               "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
               "move": Qt.CursorShape.SizeAllCursor, "new": Qt.CursorShape.CrossCursor}

    def mousePressEvent(self, e) -> None:
        self._drag = self._hit(e.position())
        self._start = e.position()
        self._start_frac = QRectF(self.frac)
        if self._drag == "new":
            ir = self.image_rect()
            fx = (e.position().x() - ir.left()) / ir.width()
            fy = (e.position().y() - ir.top()) / ir.height()
            self._start_frac = QRectF(fx, fy, 0, 0)

    def mouseMoveEvent(self, e) -> None:
        if self._drag is None:
            hit = self._hit(e.position())
            self.setCursor(self.CURSORS.get(hit, Qt.CursorShape.ArrowCursor))
            return
        ir = self.image_rect()
        dx = (e.position().x() - self._start.x()) / ir.width()
        dy = (e.position().y() - self._start.y()) / ir.height()
        f = QRectF(self._start_frac)
        if self._drag == "move":
            f.translate(dx, dy)
            self.frac = self._clamp(f)
        else:
            left, top, right, bottom = f.left(), f.top(), f.right(), f.bottom()
            d = "br" if self._drag == "new" else self._drag
            if "l" in d: left += dx
            if "r" in d: right += dx
            if "t" in d: top += dy
            if "b" in d: bottom += dy
            left, right = sorted((max(0.0, min(1.0, left)), max(0.0, min(1.0, right))))
            top, bottom = sorted((max(0.0, min(1.0, top)), max(0.0, min(1.0, bottom))))
            min_w, min_h = MIN_PX / ir.width(), MIN_PX / ir.height()
            r = QRectF(left, top, max(min_w, right - left), max(min_h, bottom - top))
            if self.aspect:
                a = self.aspect * self.src_h / self.src_w
                # follow the dominant drag direction, anchored at the opposite corner/edge
                if d in ("t", "b"):
                    w = r.height() * a
                    r = QRectF(r.center().x() - w / 2, r.top(), w, r.height())
                elif d in ("l", "r"):
                    h = r.width() / a
                    r = QRectF(r.left(), r.center().y() - h / 2, r.width(), h)
                else:
                    if r.width() / max(r.height(), 1e-9) > a:
                        h = r.width() / a
                        r = QRectF(r.left(), r.bottom() - h if "t" in d else r.top(), r.width(), h)
                    else:
                        w = r.height() * a
                        r = QRectF(r.right() - w if "l" in d else r.left(), r.top(), w, r.height())
                if r.left() < 0 or r.top() < 0 or r.right() > 1 or r.bottom() > 1:
                    r = self._fit_aspect(r.intersected(QRectF(0, 0, 1, 1)), keep_center=True)
            self.frac = r
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        self._drag = None
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        ir = self.image_rect()
        p.drawPixmap(ir, self.pix, QRectF(self.pix.rect()))
        cr = self.screen_rect()
        outside = QPainterPath()
        outside.addRect(ir)
        inner = QPainterPath()
        inner.addRect(cr)
        p.fillPath(outside.subtracted(inner), QColor(10, 12, 16, 150))
        if self._drag:
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            for k in (1, 2):
                x = cr.left() + cr.width() * k / 3
                y = cr.top() + cr.height() * k / 3
                p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
                p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(cr)
        # corner handles: short accent L-shapes with a dark edge so they read on any photo
        L = min(18.0, cr.width() / 3, cr.height() / 3)
        for cx, cy, sx, sy in ((cr.left(), cr.top(), 1, 1), (cr.right(), cr.top(), -1, 1),
                               (cr.left(), cr.bottom(), 1, -1), (cr.right(), cr.bottom(), -1, -1)):
            path = QPainterPath(QPointF(cx, cy + sy * L))
            path.lineTo(cx, cy)
            path.lineTo(cx + sx * L, cy)
            pen = QPen(QColor(0, 0, 0, 90), 6.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPath(path)
            pen.setColor(QColor("white"))
            pen.setWidthF(4)
            p.setPen(pen)
            p.drawPath(path)
        # size readout pill
        w, h = self.crop_pixels()
        text = f"{w} × {h}"
        f = theme.font(12, theme.QFont.Weight.DemiBold)
        tw = QFontMetricsF(f).horizontalAdvance(text) + 20
        pill = QRectF(cr.center().x() - tw / 2, min(cr.bottom() - 34, self.height() - 34), tw, 24)
        if cr.height() > 60 and cr.width() > tw + 10:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(10, 12, 16, 170))
            p.drawRoundedRect(pill, 12, 12)
            p.setPen(QColor("white"))
            p.setFont(f)
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        p.end()


class CropWindow(kit.ToolWindow):
    def __init__(self, files: list[Path], pal, accent: QColor):
        n = len(files)
        image, size = load_preview(files[0])
        sub = f"{files[0].name} · {size[0]} × {size[1]}" if n == 1 else \
            f"{n} images · the same framing for each, previewed on {files[0].name}"
        super().__init__("Crop" if n == 1 else f"Crop {n} images", sub, "Crop" if n == 1 else f"Crop {n} images",
                         pal, accent)
        self.n = n
        self.canvas = CropCanvas(image, size, pal, accent)
        self.canvas.setStyleSheet(f"background: {theme.mix(kit.page_color(pal), QColor('black'), 0.06).name()};"
                                  " border-radius: 14px;")
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.body.addWidget(self.canvas, 1)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.aspects = kit.Segmented(ASPECTS, "free", pal, accent)
        self.aspects.changed.connect(self._aspect_changed)
        row.addWidget(self.aspects)
        row.addStretch(1)
        self.w_edit, self.h_edit = QLineEdit(), QLineEdit()
        for ed in (self.w_edit, self.h_edit):
            ed.setValidator(QIntValidator(1, 100000))
            ed.setFixedWidth(76)
            ed.setAlignment(Qt.AlignmentFlag.AlignRight)
            ed.editingFinished.connect(self._typed_size)
        row.addWidget(self.w_edit)
        row.addWidget(kit.label("×", "meta"))
        row.addWidget(self.h_edit)
        row.addWidget(kit.label("px", "meta"))
        reset = kit.button("Reset")
        reset.clicked.connect(self._reset)
        row.addWidget(reset)
        self.body.addLayout(row)
        self.exact: tuple[int, int] | None = None
        self.canvas.changed.connect(self._sync_fields)
        self._sync_fields()
        self.hint.setText("Drag the frame or its corners. Drag outside it to start over.")
        self.resize(900, 720)

    def _aspect_changed(self, key: str) -> None:
        self.exact = None
        if key == "free":
            self.canvas.set_aspect(None)
        elif key == "original":
            self.canvas.set_aspect(self.canvas.src_w / self.canvas.src_h)
        else:
            a, b = (int(x) for x in key.split(":"))
            self.canvas.set_aspect(a / b)

    def _typed_size(self) -> None:
        try:
            w, h = int(self.w_edit.text()), int(self.h_edit.text())
        except ValueError:
            return
        if (w, h) == self.canvas.crop_pixels():
            return
        # Typed pixels lock the ratio and become the exact output size.
        self.exact = (w, h)
        self.aspects.set_value("free")
        self.canvas.set_aspect(w / h)
        self._sync_fields()

    def _reset(self) -> None:
        self.exact = None
        self.aspects.set_value("free")
        self.canvas.set_aspect(None)
        self.canvas.reset()

    def _sync_fields(self) -> None:
        w, h = self.exact or self.canvas.crop_pixels()
        for ed, v in ((self.w_edit, w), (self.h_edit, h)):
            if not ed.hasFocus():
                ed.setText(str(v))
        self.ok.setEnabled(self.canvas.frac.width() > 0 and self.canvas.frac.height() > 0)

    def options(self) -> dict:
        f = self.canvas.frac
        opts: dict = {"crop": (f.left(), f.top(), f.right(), f.bottom())}
        if self.n > 1 and self.canvas.aspect:
            opts["aspect"] = self.canvas.aspect
        if self.exact:
            opts["size"] = self.exact
        return opts
