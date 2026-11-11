"""Progress cards above the taskbar for jobs that outlast the wheel (see wheel.HANDOFF_AFTER)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import estimate
from ..jobs import Job
from ..win import shell
from . import theme
from .icons import icon_pixmap

W, H, GAP = 340, 68, 8


class Toast(QWidget):
    _stack: list[QWidget] = []

    def __init__(self, job: Job, fraction: float = 0.0, theme_setting: str = "system"):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(W, H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to show the result · right-click to cancel")
        self.pal = theme.palette(theme_setting)
        self.job = job
        self.accent = self.pal.accents.get(job.operation.wheel, self.pal.accents["convert"])
        files = job.files
        what = files[0].name if len(files) == 1 else f"{len(files)} files"
        self.title = f"{job.operation.label} · {what}"
        self.status = f"{fraction:.0%}"
        self.fraction = fraction
        self.state = "running"
        self.outputs: list[Path] = []
        self.undoable: list[Path] = []
        self.setMouseTracking(True)
        self._undo_hover = False
        job.progress.connect(self._on_progress)
        job.finished.connect(self._on_finished)
        job.failed.connect(self._on_failed)
        job.cancelled.connect(self.close_toast)
        Toast._stack.append(self)
        Toast._relayout()
        self.show()

    @classmethod
    def _relayout(cls) -> None:
        area = QGuiApplication.primaryScreen().availableGeometry()
        y = area.bottom() - 16
        for t in reversed(cls._stack):  # progress cards and Clip Inbox cards share the stack
            y -= t.height()
            t.move(area.right() - 16 - t.width(), y)
            y -= GAP

    def _on_progress(self, f: float) -> None:
        self.fraction = max(self.fraction, f)
        self.status = f"{self.fraction:.0%} · right-click to cancel"
        self.update()

    def _on_finished(self, outputs: list) -> None:
        self.outputs = outputs
        self.state = "done"
        self.fraction = 1.0
        if outputs:
            first = outputs[0]
            size = sum(o.stat().st_size for o in outputs if o.is_file())
            name = first.name if len(outputs) == 1 else f"{len(outputs)} files"
            if self.job.operation.copy_result:
                self.status = f"Copied {name} · {estimate.human(size)} · paste with Ctrl+V"
            else:
                self.status = f"Saved {name}" + (f" · {estimate.human(size)}" if size else "") + " · click to show"
            op = self.job.operation
            if op.wheel != "actions" or op.id == "any.zip":     # results this job made, never the originals
                self.undoable = [o for o in outputs if o not in self.job.files and o.exists()]
                if self.undoable:   # the Undo button takes the room the hint had
                    self.status = self.status.replace(" · click to show", "")
        else:
            self.status = "Done"
        self.update()
        QTimer.singleShot(6000 if self.undoable else 4000, self.close_toast)

    def _on_failed(self, message: str) -> None:
        self.state = "error"
        self.status = message
        self.update()
        QTimer.singleShot(8000, self.close_toast)

    def close_toast(self) -> None:
        if self.state == "closed":
            return
        self.state = "closed"
        if self in Toast._stack:
            Toast._stack.remove(self)
        self.close()
        self.deleteLater()
        Toast._relayout()

    def _undo_rect(self) -> QRectF:
        return QRectF(W - 4.5 - 14 - 58, H / 2 - 16, 58, 28)

    def mouseMoveEvent(self, event) -> None:
        over = bool(self.undoable) and self._undo_rect().contains(event.position())
        if over != self._undo_hover:
            self._undo_hover = over
            self.update()

    def leaveEvent(self, event) -> None:
        self._undo_hover = False
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self.undoable and self._undo_rect().contains(event.position()):
            from ..win.watch import recycle
            if recycle(self.undoable):
                self.outputs, self.undoable = [], []
                self.status = "Undone · moved to the Recycle Bin"
                self.update()
                QTimer.singleShot(1500, self.close_toast)
            return
        if self.state == "running" and event.button() == Qt.MouseButton.RightButton:
            self.job.cancel()
        elif self.outputs:
            shell.reveal(self.outputs[0])
            self.close_toast()
        elif self.state != "running":
            self.close_toast()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.pal
        r = QRectF(4.5, 2.5, W - 9, H - 9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 30 if not pal.dark else 80))
        p.drawRoundedRect(r.adjusted(-1, 1, 1, 4), 14, 14)
        p.setPen(QPen(pal.keyline, 1))
        p.setBrush(pal.hub)
        p.drawRoundedRect(r, 13, 13)
        color = pal.error if self.state == "error" else self.accent
        # icon disc: the op's icon, a check when done
        disc = QRectF(r.left() + 12, r.center().y() - 17, 34, 34)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.with_alpha(color, 0.14 if self.state == "running" else 1.0))
        p.drawEllipse(disc)
        if self.state == "running":
            pen = QPen(color, 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(disc.adjusted(1.5, 1.5, -1.5, -1.5), 90 * 16, -round(360 * 16 * self.fraction))
            icon = theme.OP_ICONS.get(self.job.operation.id)
            pm = icon_pixmap(icon, color.name(), 16, self.devicePixelRatioF()) if icon else None
            if pm is not None:
                p.drawPixmap(disc.center() - QPointF(8, 8), pm)
        else:
            pen = QPen(QColor(255, 255, 255), 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            c = disc.center()
            if self.state == "done":
                p.drawPolyline([c + QPointF(-5, 0.5), c + QPointF(-1.5, 4), c + QPointF(5.5, -4)])
            else:
                p.drawLine(c + QPointF(0, -6), c + QPointF(0, 1.5))
                p.drawPoint(c + QPointF(0, 6))
        text_x = disc.right() + 12
        tw = r.right() - 14 - text_x
        if self.undoable:
            u = self._undo_rect()
            tw -= u.width() + 8
            p.setPen(QPen(pal.keyline, 1))
            p.setBrush(pal.text if self._undo_hover else pal.track)
            p.drawRoundedRect(u, 9, 9)
            p.setFont(theme.font(12, QFont.Weight.DemiBold))
            p.setPen(pal.hub if self._undo_hover else pal.text)
            p.drawText(u, Qt.AlignmentFlag.AlignCenter, "Undo")
        f = theme.font(13.5, QFont.Weight.DemiBold, display=True)
        p.setFont(f)
        p.setPen(pal.text)
        p.drawText(QRectF(text_x, r.top() + 12, tw, 20), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(f).elidedText(self.title, Qt.TextElideMode.ElideMiddle, tw))
        f2 = theme.font(12, QFont.Weight.Normal)
        p.setFont(f2)
        p.setPen(pal.error if self.state == "error" else pal.meta)
        p.drawText(QRectF(text_x, r.top() + 32, tw, 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(f2).elidedText(self.status, Qt.TextElideMode.ElideMiddle, tw))
        p.end()
