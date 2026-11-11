"""Clip Inbox cards: a thumbnail of the new clip and four answers (Trim, For Discord, Keep, Delete).

Cards share the progress cards' stack above the taskbar. Ignoring a card keeps the clip;
Delete waits a few seconds with an Undo before anything reaches the Recycle Bin.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .. import estimate
from . import theme
from .icons import icon_pixmap
from .kit import fmt_time
from .toast import Toast

W = 436
PAD, THUMB_W, THUMB_H, BTN_H, GAP = 12, 128, 72, 36, 6
H = PAD + THUMB_H + 10 + BTN_H + PAD + 2
LIFETIME = 30.0      # seconds a card waits before it quietly keeps the clip
UNDO_SECONDS = 5.0


class _Loader(QObject):
    done = Signal(object, float)   # QImage | None, duration


def load_preview(path: Path, sink: _Loader) -> None:
    def work() -> None:
        from .thumbs import make_thumbnail
        img = make_thumbnail(path)
        try:
            dur = estimate.inspect(path).duration
        except Exception:
            dur = 0.0
        sink.done.emit(img, dur)
    threading.Thread(target=work, daemon=True).start()


class Button:
    def __init__(self, key: str, label: str, icon: str, color: QColor, primary: bool = False):
        self.key, self.label, self.icon, self.color, self.primary = key, label, icon, color, primary
        self.rect = QRectF()
        self.hover = 0.0
        self.anim: QVariantAnimation | None = None


class ClipCard(QWidget):
    """`chosen(key)` fires once with the button's key, or "keep" when the card times out."""

    chosen = Signal(str)
    closed = Signal()

    def __init__(self, title: str, meta: str, buttons: list[tuple[str, str, str]], theme_setting: str = "system",
                 thumb_for: Path | None = None, lifetime: float = LIFETIME, timeout_key: str = "keep"):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.pal = theme.palette(theme_setting)
        self.accent = self.pal.accents["tools"]
        self.title, self.meta, self.note = title, meta, ""
        self.has_thumb = thumb_for is not None
        self.setFixedSize(W, H if self.has_thumb else H - THUMB_H + 40)
        self.thumb: QImage | None = None
        self.buttons = [Button(k, lbl, icon, self.pal.error if k == "delete" else self.accent, primary=k == "review")
                        for k, lbl, icon in buttons]
        self._layout_buttons()
        self.state = "open"            # open → undo (after Delete) → closed
        self.lifetime, self.timeout_key = lifetime, timeout_key
        self.deadline = time.monotonic() + lifetime
        self.paused_left: float | None = None
        self._timer = QTimer(self, interval=100, timeout=self._tick)
        self._timer.start()
        if thumb_for is not None:
            self._loader = _Loader()
            self._loader.done.connect(self._loaded)
            load_preview(thumb_for, self._loader)
        Toast._stack.append(self)
        Toast._relayout()
        self.show()

    # -- data --------------------------------------------------------------------

    def _loaded(self, img: QImage | None, duration: float) -> None:
        self.thumb = img
        if duration:
            self.meta = f"{fmt_time(duration).split('.')[0]} · {self.meta}"
        self.update()

    def _layout_buttons(self) -> None:
        top = self.height() - PAD - 2 - BTN_H
        n = len(self.buttons)
        bw = (W - 2 * PAD - 8 - GAP * (n - 1)) / n
        for i, b in enumerate(self.buttons):
            b.rect = QRectF(PAD + 4 + i * (bw + GAP), top, bw, BTN_H)

    # -- lifetime ---------------------------------------------------------------

    def _left(self) -> float:
        return self.paused_left if self.paused_left is not None else self.deadline - time.monotonic()

    def _tick(self) -> None:
        if self._left() <= 0:
            if self.state == "undo":
                self._choose("delete")
            else:
                self._choose(self.timeout_key)
            return
        self.update()

    def enterEvent(self, e) -> None:
        if self.state == "open":
            self.paused_left = self._left()

    def leaveEvent(self, e) -> None:
        if self.paused_left is not None:
            self.deadline = time.monotonic() + max(self.paused_left, 4.0)
            self.paused_left = None
        for b in self.buttons:
            self._hover(b, False)

    def _choose(self, key: str) -> None:
        if self.state == "closed":
            return
        self.state = "closed"
        self.chosen.emit(key)
        self.close_card()

    def close_card(self) -> None:
        self.state = "closed"
        self._timer.stop()
        if self in Toast._stack:
            Toast._stack.remove(self)
            Toast._relayout()
        self.close()
        self.deleteLater()
        self.closed.emit()

    # -- input -------------------------------------------------------------------

    def _button_at(self, pos: QPointF) -> Button | None:
        return next((b for b in self.buttons if b.rect.contains(pos)), None)

    def _hover(self, b: Button, on: bool) -> None:
        target = 1.0 if on else 0.0
        if (b.anim and b.anim.endValue() == target) or (b.anim is None and b.hover == target):
            return
        if b.anim:
            b.anim.stop()
        b.anim = QVariantAnimation(self, startValue=b.hover, endValue=target, duration=140,
                                   easingCurve=QEasingCurve.Type.OutCubic)
        b.anim.valueChanged.connect(lambda v, b=b: (setattr(b, "hover", float(v)), self.update()))
        b.anim.start()

    def mouseMoveEvent(self, e) -> None:
        over = self._button_at(e.position())
        for b in self.buttons:
            self._hover(b, b is over)
        self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self._choose(self.timeout_key if self.state == "open" else "delete")
            return
        b = self._button_at(e.position())
        if b is None:
            return
        if self.state == "undo":
            if b.key == "undo":
                self._restore()
            return
        if b.key == "delete":
            self._start_undo()
        else:
            self._choose(b.key)

    def _start_undo(self) -> None:
        self.state = "undo"
        self._saved = (self.buttons, self.note)
        self.note = "Moving to the Recycle Bin…"
        self.buttons = [Button("undo", "Undo", "arrow_rotate_counterclockwise", self.accent, primary=True)]
        self._layout_buttons()
        self.paused_left = None
        self.deadline = time.monotonic() + UNDO_SECONDS
        self.update()

    def _restore(self) -> None:
        self.buttons, self.note = self._saved
        self._layout_buttons()
        self.state = "open"
        self.deadline = time.monotonic() + self.lifetime
        self.update()

    # -- painting ----------------------------------------------------------------

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.pal
        r = QRectF(4.5, 2.5, self.width() - 9, self.height() - 9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 30 if not pal.dark else 80))
        p.drawRoundedRect(r.adjusted(-1, 1, 1, 4), 14, 14)
        p.setPen(QPen(pal.keyline, 1))
        p.setBrush(pal.hub)
        p.drawRoundedRect(r, 13, 13)

        text_x = r.left() + PAD
        if self.has_thumb:
            tr = QRectF(r.left() + PAD, r.top() + PAD, THUMB_W, THUMB_H)
            clip = QPainterPath()
            clip.addRoundedRect(tr, 9, 9)
            p.save()
            p.setClipPath(clip)
            p.fillRect(tr, pal.track)
            if self.thumb is not None and not self.thumb.isNull():
                img = self.thumb
                scale = max(tr.width() / img.width(), tr.height() / img.height())
                sw, sh = tr.width() / scale, tr.height() / scale
                src = QRectF((img.width() - sw) / 2, (img.height() - sh) / 2, sw, sh)
                p.drawImage(tr, img, src)
            else:
                pm = icon_pixmap("play_filled", pal.meta.name(), 20, self.devicePixelRatioF())
                if pm is not None:
                    p.drawPixmap(tr.center() - QPointF(10, 10), pm)
            p.restore()
            text_x = tr.right() + 12
        tw = r.right() - PAD - text_x
        y = r.top() + PAD + (2 if self.has_thumb else 0)
        f = theme.font(14, QFont.Weight.DemiBold, display=True)
        p.setFont(f)
        p.setPen(pal.text)
        p.drawText(QRectF(text_x, y, tw, 22), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(f).elidedText(self.title, Qt.TextElideMode.ElideRight, tw))
        f2 = theme.font(12.5)
        p.setFont(f2)
        p.setPen(pal.meta)
        p.drawText(QRectF(text_x, y + 22, tw, 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(f2).elidedText(self.note or self.meta, Qt.TextElideMode.ElideMiddle, tw))

        for b in self.buttons:
            self._paint_button(p, b)

        # time left: a hairline that shortens until the card keeps the clip by itself
        frac = max(0.0, min(1.0, self._left() / (UNDO_SECONDS if self.state == "undo" else self.lifetime)))
        line = QRectF(r.left() + 14, r.bottom() - 4, (r.width() - 28) * frac, 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.with_alpha(pal.error if self.state == "undo" else self.accent, 0.55))
        p.drawRoundedRect(line, 1, 1)
        p.end()

    def _paint_button(self, p: QPainter, b: Button) -> None:
        pal, h = self.pal, b.hover
        rect = b.rect.adjusted(0, -1.5 * h, 0, -1.5 * h)   # lifts a little on hover, like the wheel tiles
        if b.primary and b.key != "delete":
            fill = theme.mix(theme.with_alpha(b.color, 0.12), b.color, h)
            ink = theme.mix(b.color, QColor("white"), h)
        else:
            fill = theme.mix(pal.track, theme.with_alpha(b.color, 0.14), h)
            ink = theme.mix(pal.text, b.color, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(rect, 10, 10)
        f = theme.font(12.5, QFont.Weight.DemiBold)
        fm = QFontMetricsF(f)
        label_w = fm.horizontalAdvance(b.label)
        icon_w = 22 if (b.icon or b.key in ("keep", "delete")) else 0
        x = rect.center().x() - (icon_w + label_w) / 2
        icon_box = QRectF(x, rect.center().y() - 8, 16 if icon_w else 0, 16)
        if not icon_w:
            pass
        elif b.key == "keep":
            pen = QPen(ink, 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            c = icon_box.center()
            p.drawPolyline([c + QPointF(-5.5, 0.5), c + QPointF(-1.8, 4.2), c + QPointF(5.5, -4)])
        elif b.key == "delete":
            _trash(p, icon_box, ink)
        else:
            pm = icon_pixmap(b.icon, ink.name(), 16, self.devicePixelRatioF())
            if pm is not None:
                p.drawPixmap(icon_box.topLeft(), pm)
        p.setFont(f)
        p.setPen(ink)
        p.drawText(QRectF(x + icon_w, rect.top(), label_w + 2, rect.height()),
                   Qt.AlignmentFlag.AlignVCenter, b.label)


def _trash(p: QPainter, box: QRectF, ink: QColor) -> None:
    pen = QPen(ink, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    x, y = box.left(), box.top()
    p.drawLine(QPointF(x + 2, y + 4), QPointF(x + 14, y + 4))
    p.drawLine(QPointF(x + 6, y + 4), QPointF(x + 6.5, y + 1.8))
    p.drawLine(QPointF(x + 6.5, y + 1.8), QPointF(x + 9.5, y + 1.8))
    p.drawLine(QPointF(x + 9.5, y + 1.8), QPointF(x + 10, y + 4))
    body = QPainterPath()
    body.moveTo(x + 3.6, y + 4)
    body.lineTo(x + 4.6, y + 14.2)
    body.lineTo(x + 11.4, y + 14.2)
    body.lineTo(x + 12.4, y + 4)
    p.drawPath(body)
    p.drawLine(QPointF(x + 6.8, y + 7), QPointF(x + 7, y + 11.5))
    p.drawLine(QPointF(x + 9.2, y + 7), QPointF(x + 9, y + 11.5))


def clip_card(clip, theme_setting: str) -> ClipCard:
    where = clip.path.parent.name
    meta = f"{estimate.human(clip.size)} · {where}" if where else estimate.human(clip.size)
    return ClipCard(clip.game or clip.path.stem, meta,
                    [("trim", "Trim", "cut"), ("discord", "For Discord", "send"), ("keep", "Keep", ""),
                     ("delete", "Delete", "")], theme_setting, thumb_for=clip.path)
