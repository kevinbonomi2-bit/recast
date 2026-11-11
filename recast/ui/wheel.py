"""The radial wheel shown around the cursor ("Halo" design, see .oil-ui/wheel-directions/DESIGN-NOTES.md).

Two ways in:
- drag mode: shown mid-drag; options appear once the dragged files enter the
  window, and dropping on a tile picks it. The window never takes focus.
- click mode ("Send to"): files are known up front; click a tile, or use the
  arrow keys and Enter.

After a pick the wheel collapses onto the file in the hub and shows the job's
progress there. Jobs still running after HANDOFF_AFTER seconds move to a
progress card (see toast.py) so the wheel never lingers at the pointer.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QRunnable, QThreadPool, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter, QPainterPath,
                           QPainterPathStroker, QPen, QRadialGradient, QTransform)
from PySide6.QtWidgets import QWidget

from .. import estimate
from ..engine import Operation, operations_for
from ..formats import category_of, ext_of
from ..jobs import Job
from ..win import shell
from . import theme
from .icons import icon_pixmap
from .thumbs import make_thumbnail

MODES = ("convert", "tools", "actions")
MODE_NAMES = {"convert": "Convert", "tools": "Tools", "actions": "Actions"}
MODE_KEYS = {"convert": "Shift", "tools": "Ctrl", "actions": "Alt"}
KEY_TO_MODE = {"shift": "convert", "ctrl": "tools", "alt": "actions"}

W, H = 480, 600              # window, logical px
CX, CY = W / 2, 300          # wheel centre inside the window
R_IN, RC, GAP, LIFT = 70, 12, 7, 7
TRACK_PAD = 9
HUB_R = 62
SEG_W, SEG_H = 112, 32
MAX_TILES = 12
HANDOFF_AFTER = 1.6          # s: longer jobs continue on a progress card
DONE_HOLD = 1.3              # s the finished state stays up
UNDO_HOLD = 6.0              # s it stays while the pointer rests on Undo
UNDO_W, UNDO_H, UNDO_Y = 84, 30, 84   # the Undo button under the hub once a job is done


def r_out(n: int) -> float:
    return 158 + max(0, n - 9) * 10


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_in(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * t


def polar(r: float, a: float) -> QPointF:
    """a is clockwise from 12 o'clock, in radians."""
    return QPointF(r * math.sin(a), -r * math.cos(a))


def tile_shape(i: int, n: int, ro_full: float) -> QPainterPath:
    """Annular sector with rounded corners: an inset sector grown by RC with round joins."""
    step = 2 * math.pi / n
    c = i * step
    ro, ri = ro_full - RC, R_IN + RC
    ho = step / 2 - (GAP / 2 + RC) / ro
    hi = step / 2 - (GAP / 2 + RC) / ri
    deg = math.degrees
    path = QPainterPath(polar(ro, c - ho))
    path.arcTo(QRectF(-ro, -ro, 2 * ro, 2 * ro), 90 - deg(c - ho), -deg(2 * ho))
    path.lineTo(polar(ri, c + hi))
    path.arcTo(QRectF(-ri, -ri, 2 * ri, 2 * ri), 90 - deg(c + hi), deg(2 * hi))
    path.closeSubpath()
    stroker = QPainterPathStroker()
    stroker.setWidth(2 * RC)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return path.united(stroker.createStroke(path)).simplified()


class _LoadSignals(QObject):
    loaded = Signal(int, object, object, object)   # token, QImage|None, idle Preview, {op.id: Preview}


_CACHE: dict[tuple, tuple] = {}     # files as dragged (path, mtime, size) -> (thumb, idle, previews)


def _cache_key(files: list[Path]) -> tuple | None:
    key = []
    for f in files[:40]:
        try:
            st = f.stat()
        except OSError:
            return None
        key.append((str(f), st.st_mtime_ns, st.st_size))
    return (len(files), *key)


def clear_preview_cache() -> None:
    """Estimates depend on settings (quality, Discord limit), so the app clears this when they change."""
    _CACHE.clear()


class _Loader(QRunnable):
    def __init__(self, token: int, files: list[Path], signals: _LoadSignals):
        super().__init__()
        self.token, self.files, self.signals = token, files, signals

    def run(self) -> None:
        key = _cache_key(self.files)
        result = self._load()
        if key is not None:
            if len(_CACHE) >= 24:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[key] = result
        self.signals.loaded.emit(self.token, *result)

    def _load(self) -> tuple:
        infos = [estimate.inspect(f) for f in self.files[:40]]
        thumb = make_thumbnail(self.files[0]) if self.files[0].is_file() else None
        ops = [o for m in MODES for o in operations_for(self.files, m)]
        # Previews describe the whole selection; inspect only a sample of large ones.
        if len(self.files) > len(infos):
            infos += [estimate.Info(f, size=_size(f), cat=infos[0].cat) for f in self.files[len(infos):]]
        return thumb, estimate.idle_preview(infos), estimate.previews(ops, infos)


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


LOADER_POOL = QThreadPool()
LOADER_POOL.setMaxThreadCount(1)


class Wheel(QWidget):
    chosen = Signal(object, list)    # Operation, files
    undone = Signal(list)            # results moved to the Recycle Bin
    pins_changed = Signal()
    handoff = Signal(object, float)  # Job still running, progress so far: show it on a card
    closed = Signal()

    def __init__(self, theme_setting=lambda: "system"):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFixedSize(W, H)
        self.theme_setting = theme_setting
        self.key_labels = dict(MODE_KEYS)  # set by the app from Settings
        self.pal = theme.palette()
        self.mode = "convert"
        self.files: list[Path] = []
        self.ops: list[Operation] = []
        self.hover = -1
        self.click_mode = False
        self.thumb: QImage | None = None
        self.idle = estimate.Preview("", "")
        self.previews: dict[str, estimate.Preview] = {}
        self._token = 0
        self._signals = _LoadSignals()
        self._signals.loaded.connect(self._on_loaded)
        self._shapes: list[QPainterPath] = []
        self._h: list[float] = []            # per-tile hover amount 0..1, eased
        self._deal_t0 = 0.0
        self._open_t0 = 0.0
        self._ind_x = 0.0                    # switcher indicator position (eased)
        self._ext_t = self._chip_t = 0.0     # when the pill extension / hub chip last changed
        self._last_ext = self._last_chip = ""
        self._last_tick = time.monotonic()
        # drop / job state
        self.state = "pick"                  # pick | running | done | error
        self._job: Job | None = None
        self._chosen = -1
        self._drop_t0 = 0.0
        self._progress = self._progress_target = 0.0
        self._end_t0 = 0.0
        self._fade = 1.0
        self._result = estimate.Preview("", "")
        self._outputs: list[Path] = []
        self._undo_hover = False
        # Dropping on the hub repeats the last pick for this kind of file ("convert:image" -> op id).
        self.last_ops: dict[str, str] = {}
        self.repeat_on = True
        # Favourites: pinned tiles come first ("convert:image" -> [op ids], newest pin first).
        self.pins: dict[str, list[str]] = {}
        self._from_hub = False
        self._timer = QTimer(self, interval=16)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    # -- opening and closing -------------------------------------------------

    def prewarm(self) -> None:
        """Paint once off screen so fonts, icons and brushes are loaded before the first real open."""
        if self.isVisible():
            return
        self.pal = theme.palette(self.theme_setting())
        for name in set(theme.OP_ICONS.values()):
            icon_pixmap(name, self.pal.text.name(), 20, self.devicePixelRatioF())
        self._layout()
        self.grab()

    def open_at(self, pos: QPoint, mode: str, files: list[Path] | None = None) -> None:
        self.pal = theme.palette(self.theme_setting())
        self.click_mode = files is not None
        self.state = "pick"
        self._job = None
        self._fade = 1.0
        self.hover = -1
        self.files = []
        self.thumb = None
        self.idle = estimate.Preview("", "")
        self.previews = {}
        if files:
            self._set_files(list(files))
        self.mode = mode
        self._ind_x = MODES.index(mode) * SEG_W
        self._layout()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        # One frame per screen refresh: 7 ms on a 144 Hz monitor, 16 ms at 60 Hz.
        self._timer.setInterval(max(4, round(1000 / max(30.0, screen.refreshRate()))))
        self._from_hub = self._undo_hover = False
        area = screen.availableGeometry()
        x = min(max(pos.x() - round(CX), area.left()), area.right() - W)
        y = min(max(pos.y() - round(CY), area.top()), area.bottom() - H)
        self.move(x, y)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, not self.click_mode)
        self._open_t0 = self._deal_t0 = time.monotonic()
        self.show()
        self.raise_()
        if self.click_mode:
            self.activateWindow()
            self.setFocus()
        self._timer.start()

    def set_mode(self, mode: str) -> None:
        if self.state != "pick" or mode == self.mode:
            return
        self.mode = mode
        self.hover = -1
        self._layout()
        self._deal_t0 = time.monotonic()

    def is_busy(self) -> bool:
        return self.isVisible() and self.state == "running"

    def dismiss(self) -> None:
        if self.isVisible():
            self._timer.stop()
            self.hide()
            self.state = "pick"
            self.closed.emit()

    def release_job(self) -> None:
        """Move a running job to a progress card right away (e.g. a new drag started)."""
        if self._job is not None and self.state == "running":
            job, self._job = self._job, None
            self.handoff.emit(job, self._progress_target)
        self.dismiss()

    # -- files, ops and previews -----------------------------------------------

    def _set_files(self, files: list[Path]) -> None:
        self.files = files
        self.thumb = None
        self.previews = {}
        self.idle = estimate.Preview(files[0].stem if len(files) == 1 else f"{len(files)} items", "")
        self._token += 1
        cached = _CACHE.get(_cache_key(files))
        if cached is not None:      # dragged before and unchanged: no flicker, no work
            self.thumb, self.idle, self.previews = cached
        else:
            LOADER_POOL.start(_Loader(self._token, files, self._signals))

    def _on_loaded(self, token: int, thumb, idle, previews) -> None:
        if token != self._token:
            return
        self.thumb, self.idle, self.previews = thumb, idle, previews
        self.update()

    def _layout(self) -> None:
        ops = operations_for(self.files, self.mode) if self.files else []
        pinned = self.pins.get(self._repeat_key(), [])
        ops.sort(key=lambda o: pinned.index(o.id) if o.id in pinned else len(pinned))   # stable: the rest keep order
        self.ops = ops[:MAX_TILES]
        n = len(self.ops)
        ro = r_out(n)
        self._shapes = [tile_shape(i, n, ro) for i in range(n)]
        self._h = [0.0] * n
        self.hover = min(self.hover, n - 1)
        self.update()

    # -- geometry -------------------------------------------------------------

    def _tile_at(self, p: QPointF) -> int:
        n = len(self.ops)
        if not n or self.state != "pick":
            return -1
        dx, dy = p.x() - CX, p.y() - CY
        r = math.hypot(dx, dy)
        if r < R_IN - 8 or r > r_out(n) + 70:
            return -1
        a = (math.atan2(dx, -dy) + 2 * math.pi) % (2 * math.pi)
        step = 2 * math.pi / n
        return int(((a + step / 2) % (2 * math.pi)) // step)

    def _repeat_key(self) -> str:
        return f"{self.mode}:{category_of(self.files[0])}" if self.files else ""

    def _target_at(self, p: QPointF) -> int:
        """The tile under the pointer; on the hub, the tile picked last time for this kind of file."""
        i = self._tile_at(p)
        self._from_hub = False
        if i < 0 and self.repeat_on and self.state == "pick" and math.hypot(p.x() - CX, p.y() - CY) < R_IN - 8:
            last = self.last_ops.get(self._repeat_key())
            i = next((k for k, o in enumerate(self.ops) if o.id == last), -1)
            self._from_hub = i >= 0
        return i

    def toggle_pin(self, i: int | None = None) -> None:
        """Pin or unpin a tile (the hovered one by default). The new order applies from the next open,
        so the tile doesn't jump out from under the pointer."""
        i = self.hover if i is None else i
        if self.state != "pick" or not (0 <= i < len(self.ops)):
            return
        pinned = self.pins.setdefault(self._repeat_key(), [])
        op_id = self.ops[i].id
        if op_id in pinned:
            pinned.remove(op_id)
        else:
            pinned.insert(0, op_id)
        self.pins_changed.emit()
        self.update()

    def _undo_rect(self) -> QRectF:
        return QRectF(CX - UNDO_W / 2, CY + UNDO_Y, UNDO_W, UNDO_H)

    def _undoable(self) -> list[Path]:
        """Results that Undo may remove: files this job made, never the originals."""
        if self.state != "done" or not (0 <= self._chosen < len(self.ops)):
            return []
        op = self.ops[self._chosen]
        if op.wheel == "actions" and op.id != "any.zip":
            return []
        return [o for o in self._outputs if o not in self.files and o.exists()]

    def _switcher_rect(self) -> QRectF:
        w = 3 * SEG_W + 6
        top = CY + r_out(len(self.ops)) + TRACK_PAD + 18
        return QRectF(CX - w / 2, top, w, SEG_H + 6)

    # -- drag and drop -----------------------------------------------------------

    @staticmethod
    def _local_files(event) -> list[Path]:
        md = event.mimeData()
        if not md.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]

    def _accept_copy(self, event) -> None:
        # Always Copy: accepting the proposed Move (Shift) would let Explorer delete the original.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragEnterEvent(self, event) -> None:
        files = self._local_files(event)
        if not files or self.state != "pick":
            event.ignore()
            return
        if files != self.files:
            self._set_files(files)
            self._layout()
            self._deal_t0 = time.monotonic()
        self._accept_copy(event)

    def dragMoveEvent(self, event) -> None:
        self._set_hover(self._target_at(event.position()))
        self._accept_copy(event)

    def dragLeaveEvent(self, event) -> None:
        self._set_hover(-1)

    def dropEvent(self, event) -> None:
        i = self._target_at(event.position())
        self._accept_copy(event)
        if i < 0:
            self.dismiss()
        else:
            self._pick(i)

    # -- click mode ----------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:
        if self.state == "pick":
            self._set_hover(self._tile_at(event.position()))
        elif self.state == "done":
            self._undo_hover = self._undo_rect().contains(event.position()) and bool(self._undoable())
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._undo_hover else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, event) -> None:
        self._undo_hover = False

    def _undo(self) -> None:
        from ..win.watch import recycle
        made = self._undoable()
        if made and recycle(made):
            self._outputs = []
            self._result = estimate.Preview("Undone", "", "Moved to the Recycle Bin")
            self._end_t0 = time.monotonic() - DONE_HOLD + 0.9    # show it briefly, then go
            self._undo_hover = False
            self.undone.emit(made)

    def mousePressEvent(self, event) -> None:
        p = event.position()
        if self.state == "done" and self._undo_rect().contains(p) and self._undoable():
            self._undo()
            return
        if self.state == "done" and math.hypot(p.x() - CX, p.y() - CY) < HUB_R + 6 and self._outputs:
            shell.reveal(self._outputs[0])
            self.dismiss()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.toggle_pin(self._tile_at(p))
            return
        sw = self._switcher_rect()
        if self.state == "pick" and sw.contains(p):
            self.set_mode(MODES[min(2, int((p.x() - sw.left() - 3) // SEG_W))])

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            return
        if self.click_mode and self.state == "pick" and not self._switcher_rect().contains(event.position()):
            i = self._tile_at(event.position())
            if i >= 0:
                self._pick(i)
            elif math.hypot(event.position().x() - CX, event.position().y() - CY) > r_out(len(self.ops)) + 70:
                self.dismiss()

    def keyPressEvent(self, event) -> None:
        k = event.key()
        n = len(self.ops)
        if k == Qt.Key.Key_Escape:
            self.dismiss()
        elif self.state != "pick":
            return
        elif n and k in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Tab):
            self._set_hover((self.hover + 1) % n)
        elif n and k in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Backtab):
            self._set_hover((self.hover - 1) % n if self.hover >= 0 else n - 1)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._pick(self.hover)
        elif k in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3):
            self.set_mode(MODES[k - Qt.Key.Key_1])

    def focusOutEvent(self, event) -> None:
        if self.click_mode and self.state == "pick":
            self.dismiss()

    def _set_hover(self, i: int) -> None:
        if i != self.hover:
            self.hover = i

    def _pick(self, i: int) -> None:
        if not (0 <= i < len(self.ops)) or self.state != "pick":
            return
        self._chosen = i
        if self.files:
            self.last_ops[self._repeat_key()] = self.ops[i].id
        self.state = "running"
        self._drop_t0 = time.monotonic()
        self._progress = self._progress_target = 0.0
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self.show()  # re-show after the flag change
        self.chosen.emit(self.ops[i], self.files)

    # -- job tracking ------------------------------------------------------------

    def track(self, job: Job) -> None:
        self._job = job
        job.progress.connect(self._on_progress)
        job.finished.connect(lambda outs, j=job: self._on_finished(j, outs))
        job.failed.connect(lambda msg, j=job: self._on_failed(j, msg))
        job.cancelled.connect(lambda j=job: self._job is j and self.dismiss())

    def _on_progress(self, f: float) -> None:
        self._progress_target = max(self._progress_target, f)

    def _on_finished(self, job: Job, outputs: list) -> None:
        if job is not self._job:
            return
        self._job = None
        self._outputs = outputs
        self._progress_target = 1.0
        self.state = "done"
        self._end_t0 = time.monotonic()
        self._result = done_text(self.ops[self._chosen] if 0 <= self._chosen < len(self.ops) else job.operation,
                                 outputs)

    def _on_failed(self, job: Job, message: str) -> None:
        if job is not self._job:
            return
        self._job = None
        self.state = "error"
        self._end_t0 = time.monotonic()
        self._result = estimate.Preview(message, "", "")

    # -- animation clock -------------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.05, now - self._last_tick)
        self._last_tick = now
        k = 1 - math.exp(-dt / 0.045)  # ~160 ms to settle
        for i in range(len(self._h)):
            self._h[i] += ((1.0 if i == self.hover else 0.0) - self._h[i]) * k
        self._ind_x += (MODES.index(self.mode) * SEG_W - self._ind_x) * k
        self._progress += (self._progress_target - self._progress) * (1 - math.exp(-dt / 0.12))
        if self.state == "running" and self._job is not None and now - self._drop_t0 > HANDOFF_AFTER:
            self.release_job()
            return
        if self.state in ("done", "error"):
            hold = DONE_HOLD if self.state == "done" else 3.5
            if self._undo_hover:
                hold = UNDO_HOLD
            t = now - self._end_t0 - hold
            if t > 0:
                self._fade = 1 - ease(t / 0.2)
                if self._fade <= 0.01:
                    self.dismiss()
                    return
        self.update()

    # -- painting ----------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        now = time.monotonic()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(self._fade)
        n = len(self.ops)
        ro = r_out(n)
        drop = ease((now - self._drop_t0) / 0.26) if self.state != "pick" else 0.0
        appear = ease((now - self._open_t0) / 0.16)

        p.translate(CX, CY)
        if self.state == "pick":
            # Nearly invisible fill so drops anywhere near the wheel land on us.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 2))
            p.drawEllipse(QPointF(0, 0), ro + 40, ro + 40)
        s = 0.92 + 0.08 * appear
        p.scale(s, s)
        self._paint_track(p, ro, drop)
        for i in range(n):
            self._paint_tile(p, i, n, ro, now, drop)
        self._paint_hub(p, now)
        p.resetTransform()
        self._paint_pill(p, now, ro, drop)
        if self.state == "done" and self._undoable():
            self._paint_undo(p, now)
        if drop < 1:
            p.setOpacity(self._fade * (1 - drop))
            self._paint_switcher(p, ro)
        p.end()

    def _paint_undo(self, p: QPainter, now: float) -> None:
        t = ease((now - self._end_t0 - 0.12) / 0.2)
        if t <= 0:
            return
        r = self._undo_rect().translated(0, 6 * (1 - t))
        p.save()
        p.setOpacity(p.opacity() * t)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 36 if not self.pal.dark else 80))
        p.drawRoundedRect(r.adjusted(-1, 1, 1, 4), UNDO_H / 2 + 1, UNDO_H / 2 + 1)
        p.setPen(QPen(self.pal.keyline, 1))
        p.setBrush(self.pal.text if self._undo_hover else self.pal.hub)
        p.drawRoundedRect(r, UNDO_H / 2, UNDO_H / 2)
        ink = self.pal.hub if self._undo_hover else self.pal.text
        pm = icon_pixmap("arrow_rotate_counterclockwise", ink.name(), 14, self.devicePixelRatioF())
        if pm is not None:
            p.drawPixmap(QPointF(r.left() + 13, r.center().y() - 7), pm)
        p.setFont(theme.font(12.5, QFont.Weight.DemiBold))
        p.setPen(ink)
        p.drawText(QRectF(r.left() + 32, r.top(), r.width() - 40, r.height()), Qt.AlignmentFlag.AlignVCenter, "Undo")
        p.restore()

    def _paint_track(self, p: QPainter, ro: float, drop: float) -> None:
        if drop >= 1:
            return
        p.save()
        p.setOpacity(p.opacity() * (1 - drop))
        p.scale(1 - 0.14 * drop, 1 - 0.14 * drop)
        rt = ro + TRACK_PAD
        shadow = QColor(0, 0, 0, 70 if not self.pal.dark else 130)
        g = QRadialGradient(QPointF(0, 12), rt + 34)
        g.setColorAt((rt - 16) / (rt + 34), shadow)
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 12), rt + 34, rt + 34)
        p.setBrush(self.pal.track)
        p.setPen(QPen(self.pal.keyline, 1))
        p.drawEllipse(QPointF(0, 0), rt, rt)
        p.restore()

    def _paint_tile(self, p: QPainter, i: int, n: int, ro: float, now: float, drop: float) -> None:
        shape = self._shapes[i]
        a = i * 2 * math.pi / n
        rm = (ro + R_IN) / 2
        centre = polar(rm, a)
        h = self._h[i] if i < len(self._h) else 0.0
        accent = self.pal.accents[self.mode]
        d = ease((now - self._deal_t0 - i * 0.018) / 0.3)
        opacity, scale, rot = d, 0.82 + 0.18 * d, -6 * (1 - d)
        offset = polar(LIFT * h, a)
        scale *= 1 + 0.045 * h
        if self.state != "pick":
            if i == self._chosen:
                t = ease_in((now - self._drop_t0) / 0.26)
                offset = QPointF(-centre.x() * 0.85 * t, -centre.y() * 0.85 * t) + polar(LIFT, a) * (1 - t)
                scale *= 1 - 0.6 * t
                opacity *= 1 - t
            else:
                opacity *= 1 - drop
                scale *= 1 - 0.1 * drop
        if opacity <= 0.01:
            return
        p.save()
        p.setOpacity(p.opacity() * opacity)
        tr = QTransform()
        tr.translate(centre.x() + offset.x(), centre.y() + offset.y())
        tr.rotate(rot)
        tr.scale(scale, scale)
        tr.translate(-centre.x(), -centre.y())
        p.setTransform(tr, True)
        p.setPen(Qt.PenStyle.NoPen)
        # soft shadow, then the accent glow while hovered
        p.setBrush(theme.with_alpha(self.pal.shadow, 1.0))
        p.drawPath(shape.translated(0, 2))
        if h > 0.02:
            for width, alpha, dy in ((12, 0.07, 6), (6, 0.12, 5), (2, 0.18, 3)):
                pen = QPen(theme.with_alpha(accent, alpha * h), width)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                p.setBrush(theme.with_alpha(accent, alpha * h))
                p.drawPath(shape.translated(0, dy))
            p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.mix(self.pal.tile, accent, h))
        p.drawPath(shape)
        self._paint_label(p, self.ops[i], centre, h, ro - R_IN, 2 * math.pi * rm / n - GAP - 8)
        if self.ops[i].id in self.pins.get(self._repeat_key(), ()):
            # favourite: a small dot at the tile's outer edge
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.mix(accent, QColor(255, 255, 255), h))
            p.drawEllipse(polar(ro - 11, a), 3, 3)
        p.restore()

    def _paint_label(self, p: QPainter, op: Operation, c: QPointF, h: float, depth: float, width: float) -> None:
        white = QColor(255, 255, 255)
        fg = theme.mix(self.pal.text, white, h)
        sub_c = theme.mix(self.pal.meta, QColor(255, 255, 255, 220), h)
        icon = theme.OP_ICONS.get(op.id)
        preview = self.previews.get(op.id)
        if icon is None:
            f = theme.font(16, QFont.Weight.Bold, display=True)
            fm = QFontMetricsF(f)
            if fm.horizontalAdvance(op.label) > width:
                f.setPixelSize(max(11, int(16 * width / fm.horizontalAdvance(op.label))))
            p.setFont(f)
            p.setPen(fg)
            sub = preview.sub if preview else ""
            dy = -8 if sub else 0
            p.drawText(QRectF(c.x() - width / 2, c.y() + dy - 11, width, 22), Qt.AlignmentFlag.AlignCenter, op.label)
            if sub:
                f2 = theme.font(10.5, QFont.Weight.DemiBold)
                p.setFont(f2)
                p.setPen(sub_c)
                text = QFontMetricsF(f2).elidedText(sub, Qt.TextElideMode.ElideRight, width)
                p.drawText(QRectF(c.x() - width / 2, c.y() + 6, width, 16), Qt.AlignmentFlag.AlignCenter, text)
            return
        words = op.label.split(" ")
        f = theme.font(11.5, QFont.Weight.DemiBold)
        fm = QFontMetricsF(f)
        lines = [op.label]
        if fm.horizontalAdvance(op.label) > width - 4 and len(words) > 1:
            # two lines, split where they come out most even ("Compress" / "to size…")
            k = min(range(1, len(words)), key=lambda j: max(fm.horizontalAdvance(" ".join(words[:j])),
                                                            fm.horizontalAdvance(" ".join(words[j:]))))
            lines = [" ".join(words[:k]), " ".join(words[k:])]
        top = c.y() - (22 if len(lines) == 2 else 16)
        dpr = self.devicePixelRatioF()
        pm = icon_pixmap(icon, fg.name(), 20, dpr)
        if pm is not None:
            p.drawPixmap(QPointF(c.x() - 10, top), pm)
        p.setFont(f)
        p.setPen(fg)
        for k, line in enumerate(lines):
            text = fm.elidedText(line, Qt.TextElideMode.ElideRight, width)
            p.drawText(QRectF(c.x() - width / 2, top + 22 + k * 13.5, width, 16), Qt.AlignmentFlag.AlignCenter, text)

    def _hovered_preview(self) -> tuple[Operation | None, estimate.Preview | None]:
        if self.state == "pick" and 0 <= self.hover < len(self.ops):
            op = self.ops[self.hover]
            return op, self.previews.get(op.id)
        return None, None

    def _paint_hub(self, p: QPainter, now: float) -> None:
        accent = self.pal.accents[self.mode]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.with_alpha(self.pal.shadow, 1.6))
        p.drawEllipse(QPointF(0, 4), HUB_R + 1, HUB_R + 1)
        p.setBrush(self.pal.hub)
        p.setPen(QPen(self.pal.keyline, 1))
        p.drawEllipse(QPointF(0, 0), HUB_R, HUB_R)
        # thumbnail
        tw, th = 80, 60
        rect = QRectF(-tw / 2, -th / 2 - 8, tw, th)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 9, 9)
        if self.thumb is not None and not self.thumb.isNull():
            p.save()
            p.setClipPath(clip)
            img = self.thumb
            sc = max(tw / img.width(), th / img.height())
            w, h = img.width() * sc, img.height() * sc
            # Portrait pages and documents show their top, where the title is.
            y = rect.top() if h > th * 1.15 else rect.center().y() - h / 2
            p.drawImage(QRectF(-w / 2, y, w, h), img)
            p.restore()
        else:
            p.setBrush(self.pal.track)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(clip)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(self.pal.keyline, 1))
        p.drawPath(clip)
        if len(self.files) > 1:
            badge = QPointF(rect.right() - 2, rect.top() + 2)
            text = str(len(self.files)) if len(self.files) < 1000 else "999+"
            f = theme.font(10.5, QFont.Weight.Bold)
            bw = max(20, QFontMetricsF(f).horizontalAdvance(text) + 10)
            p.setPen(QPen(self.pal.hub, 2))
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(badge.x() - bw / 2, badge.y() - 10, bw, 20), 10, 10)
            p.setPen(QColor(255, 255, 255))
            p.setFont(f)
            p.drawText(QRectF(badge.x() - bw / 2, badge.y() - 10, bw, 20), Qt.AlignmentFlag.AlignCenter, text)

        # format chip: current extension, or the target while hovering a conversion
        op, prev = self._hovered_preview()
        if self.state != "pick":
            chosen = self.ops[self._chosen] if 0 <= self._chosen < len(self.ops) else None
            target = chosen.target.upper() if chosen is not None and chosen.wheel == "convert" else ""
        else:
            target = (prev.chip if prev and prev.chip else (op.target.upper() if op and op.wheel == "convert" else ""))
        current = ext_of(self.files[0]).upper() if self.files else ""
        if len({ext_of(f) for f in self.files[:40]}) > 1:
            current = "MIX"
        chip = target or current
        if chip != self._last_chip:
            self._last_chip, self._chip_t = chip, now
        if chip:
            f = theme.font(11, QFont.Weight.Bold, display=True)
            cw = max(44, QFontMetricsF(f).horizontalAdvance(chip) + 18)
            t = ease((now - self._chip_t) / 0.18)
            r = QRectF(-cw / 2, 20 + 5 * (1 - t), cw, 22)
            p.save()
            p.setOpacity(p.opacity() * t)
            p.setPen(QPen(self.pal.hub, 2))
            p.setBrush(accent if target else self.pal.text)
            p.drawRoundedRect(r, 11, 11)
            p.setPen(QColor(255, 255, 255) if target or not self.pal.dark else QColor("#14161B"))
            p.setFont(f)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, chip)
            p.restore()

        # progress ring and the finishing badge
        if self.state != "pick":
            show = ease((now - self._drop_t0 - 0.15) / 0.15)
            ring_c = self.pal.error if self.state == "error" else accent
            rr = HUB_R + 2
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(theme.with_alpha(self.pal.keyline, show * 2), 4))
            p.drawEllipse(QPointF(0, 0), rr, rr)
            pen = QPen(theme.with_alpha(ring_c, show), 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            frac = 1.0 if self.state == "error" else self._progress
            if frac > 0.002:
                p.drawArc(QRectF(-rr, -rr, 2 * rr, 2 * rr), 90 * 16, -round(360 * 16 * frac))
            if self.state in ("done", "error"):
                t = (now - self._end_t0) / 0.26
                # overshoot ease for the badge pop
                s = 0 if t <= 0 else (1 + 2.2 * (min(t, 1) - 1) ** 3 + 1.2 * (min(t, 1) - 1) ** 2) if t < 1 else 1
                if s > 0:
                    p.save()
                    p.scale(s, s)
                    p.setPen(QPen(self.pal.hub, 3))
                    p.setBrush(ring_c)
                    p.drawEllipse(QPointF(0, -8), 17, 17)
                    pen = QPen(QColor(255, 255, 255), 2.4)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    p.setPen(pen)
                    if self.state == "done":
                        check = QPainterPath(QPointF(-5, -7.5))
                        check.lineTo(-1.6, -4)
                        check.lineTo(5, -11.5)
                        p.drawPath(check)
                    else:
                        p.drawLine(QPointF(0, -14), QPointF(0, -7))
                        p.drawPoint(QPointF(0, -2.5))
                    p.restore()

    def _paint_pill(self, p: QPainter, now: float, ro: float, drop: float) -> None:
        accent = self.pal.accents[self.mode]
        op, prev = self._hovered_preview()
        coloured = False
        if self.state in ("done", "error"):
            pv = self._result
            coloured = self.state == "done" and bool(pv.ext)
        elif self.state == "running":
            chosen = self.ops[self._chosen] if 0 <= self._chosen < len(self.ops) else None
            target = self.previews.get(chosen.id) if chosen else None
            label = {"convert": "Converting", "tools": "Working", "actions": "Working"}[self.mode]
            pv = estimate.Preview(f"{label}…", "", (target.stem + target.ext) if target else "")
        elif op is not None:
            pv = prev or estimate.Preview(op.label, "", op.hint)
            if self._from_hub:      # resting on the hub: say what letting go will do
                pv = estimate.Preview(pv.stem, pv.ext, "drop here to repeat", pv.sub, pv.chip)
            coloured = op.wheel == "convert"
        else:
            pv = self.idle
        if not (pv.stem or pv.ext):
            return
        if pv.ext != self._last_ext:
            self._last_ext, self._ext_t = pv.ext, now
        name_f = theme.font(15, QFont.Weight.DemiBold, display=True)
        meta_f = theme.font(12, QFont.Weight.DemiBold)
        nfm, mfm = QFontMetricsF(name_f), QFontMetricsF(meta_f)
        max_w = 440
        meta = mfm.elidedText(pv.meta, Qt.TextElideMode.ElideMiddle, 200) if pv.meta else ""
        meta_w = mfm.horizontalAdvance(meta)
        ext_w = nfm.horizontalAdvance(pv.ext)
        stem = nfm.elidedText(pv.stem, Qt.TextElideMode.ElideMiddle,
                              max_w - 32 - ext_w - (meta_w + 10 if meta else 0))
        stem_w = nfm.horizontalAdvance(stem)
        w = 32 + stem_w + ext_w + (10 + meta_w if meta else 0)
        hgt = 38
        top = CY - ro - TRACK_PAD - 18 - hgt
        # after a drop the pill settles just above the file
        target_top = CY - HUB_R - 14 - hgt
        top += (target_top - top) * drop
        r = QRectF(CX - w / 2, top, w, hgt)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 40 if not self.pal.dark else 90))
        p.drawRoundedRect(r.adjusted(-1, 1, 1, 5), hgt / 2 + 2, hgt / 2 + 2)
        p.setBrush(self.pal.hub)
        p.setPen(QPen(theme.with_alpha(self.pal.keyline, 1.2), 1))
        p.drawRoundedRect(r, hgt / 2, hgt / 2)
        x = r.left() + 16
        base = r.top() + 24.5
        p.setFont(name_f)
        p.setPen(self.pal.error if self.state == "error" else self.pal.text)
        p.drawText(QPointF(x, base), stem)
        x += stem_w
        if pv.ext:
            t = ease((now - self._ext_t) / 0.18)
            p.save()
            p.setOpacity(p.opacity() * t)
            p.setPen(accent if coloured else self.pal.text)
            p.drawText(QPointF(x, base + 5 * (1 - t)), pv.ext)
            p.restore()
            x += ext_w
        if meta:
            p.setFont(meta_f)
            p.setPen(self.pal.meta)
            p.drawText(QPointF(x + 10, base - 1), meta)

    def _paint_switcher(self, p: QPainter, ro: float) -> None:
        r = self._switcher_rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 36 if not self.pal.dark else 80))
        p.drawRoundedRect(r.adjusted(-1, 1, 1, 5), r.height() / 2 + 2, r.height() / 2 + 2)
        p.setBrush(self.pal.track)
        p.setPen(QPen(self.pal.keyline, 1))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        ind = QRectF(r.left() + 3 + self._ind_x, r.top() + 3, SEG_W, SEG_H)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.with_alpha(self.pal.shadow, 1.0))
        p.drawRoundedRect(ind.translated(0, 1), SEG_H / 2, SEG_H / 2)
        p.setBrush(self.pal.tile)
        p.drawRoundedRect(ind, SEG_H / 2, SEG_H / 2)
        key_f = theme.font(10, QFont.Weight.DemiBold)
        lab_f = theme.font(12.5, QFont.Weight.DemiBold)
        kfm, lfm = QFontMetricsF(key_f), QFontMetricsF(lab_f)
        for k, mode in enumerate(MODES):
            seg = QRectF(r.left() + 3 + k * SEG_W, r.top() + 3, SEG_W, SEG_H)
            key, name = self.key_labels[mode], MODE_NAMES[mode]
            kw = kfm.horizontalAdvance(key) + 10
            total = kw + 7 + lfm.horizontalAdvance(name)
            x = seg.center().x() - total / 2
            on = mode == self.mode
            kr = QRectF(x, seg.center().y() - 9, kw, 18)
            if on:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self.pal.accents[mode])
            else:
                p.setPen(QPen(self.pal.keyline, 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(kr, 5, 5)
            p.setFont(key_f)
            p.setPen(QColor(255, 255, 255) if on else self.pal.meta)
            p.drawText(kr, Qt.AlignmentFlag.AlignCenter, key)
            p.setFont(lab_f)
            p.setPen(self.pal.text if on else self.pal.meta)
            p.drawText(QRectF(kr.right() + 7, seg.top(), SEG_W, SEG_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)


def done_text(op: Operation, outputs: list[Path]) -> estimate.Preview:
    """Pill text once a job has finished: real names and sizes."""
    if op.wheel == "actions" and op.id != "any.zip":
        text = {"action.copy": ("Copied", "Paste it in Explorer"), "action.copy_path": ("Path copied", ""),
                "action.move": ("Moved", outputs[0].parent.name if outputs else "Nothing moved"),
                "action.terminal": ("Terminal opened", ""), "action.reveal": ("Opened in Explorer", "")}
        stem, meta = text.get(op.id, ("Done", ""))
        return estimate.Preview(stem, "", meta)
    if not outputs:
        return estimate.Preview("Done", "", "")
    if op.copy_result:
        size = sum(_size(o) for o in outputs)
        return estimate.Preview("Ready for Discord" if op.id == "video.discord" else "Copied", "",
                                f"{estimate.human(size)} · paste with Ctrl+V")
    if len(outputs) > 1:
        size = sum(_size(o) for o in outputs)
        return estimate.Preview(f"Saved {len(outputs)} files", "", estimate.human(size) if size else "")
    out = outputs[0]
    if out.is_dir():
        count = sum(1 for _ in out.iterdir())
        return estimate.Preview(f"Saved {out.name}", "", f"{count} files in a folder")
    name = out.name
    stem, ext = (name[:-7], ".tar.gz") if name.lower().endswith(".tar.gz") else (out.stem, out.suffix)
    return estimate.Preview(f"Saved {stem}", ext, estimate.human(_size(out)) + " · click to show")
