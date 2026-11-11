"""Trim window: a timeline (filmstrip for video, waveform for audio) with start and end handles,
a preview that plays only the kept part, and exact time fields."""

from __future__ import annotations

import array
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, QThreadPool, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLineEdit, QWidget

from .. import paths
from ..engine.media import duration, probe
from ..formats import category_of
from . import kit, theme
from .icons import icon_pixmap

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FRAMES = 14
PEAKS = 600
MIN_LEN = 0.1  # s


class _Signals(QObject):
    loaded = Signal(object, object)   # filmstrip QImage | None, peaks list | None


class _Loader(QRunnable):
    def __init__(self, path: Path, dur: float, video: bool, signals: _Signals):
        super().__init__()
        self.path, self.dur, self.video, self.signals = path, dur, video, signals

    def run(self) -> None:
        exe = paths.ffmpeg()
        strip = peaks = None
        if exe and self.video and self.dur > 0:
            fps = FRAMES / self.dur
            out = subprocess.run([exe, "-v", "error", "-i", str(self.path), "-vf",
                                  f"fps={fps:.6f},scale=-2:72,tile={FRAMES}x1", "-frames:v", "1",
                                  "-f", "image2pipe", "-vcodec", "png", "-"],
                                 capture_output=True, timeout=60, creationflags=NO_WINDOW)
            img = QImage.fromData(out.stdout)
            strip = None if img.isNull() else img
        if exe and not self.video:
            rate = 4000 if self.dur < 600 else 1000
            out = subprocess.run([exe, "-v", "error", "-i", str(self.path), "-ac", "1", "-ar", str(rate),
                                  "-f", "s16le", "-"], capture_output=True, timeout=120, creationflags=NO_WINDOW)
            samples = array.array("h", out.stdout[: len(out.stdout) // 2 * 2])
            if samples:
                step = max(1, len(samples) // PEAKS)
                peaks = [max(max(samples[i:i + step]), -min(samples[i:i + step])) / 32768
                         for i in range(0, len(samples), step)]
        try:
            self.signals.loaded.emit(strip, peaks)
        except RuntimeError:
            pass  # the window was closed before the timeline finished loading


class Timeline(QWidget):
    changed = Signal()
    seek = Signal(float)

    def __init__(self, dur: float, pal, accent: QColor, tall: bool):
        super().__init__()
        self.dur, self.pal, self.accent = dur, pal, accent
        self.start, self.end, self.playhead = 0.0, dur, 0.0
        self.strip: QImage | None = None
        self.peaks: list[float] | None = None
        self._drag: str | None = None
        self._grab = 0.0
        self.setFixedHeight(150 if tall else 84)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def track(self) -> QRectF:
        return QRectF(14, 6, self.width() - 28, self.height() - 12)

    def x_of(self, t: float) -> float:
        r = self.track()
        return r.left() + r.width() * (t / self.dur if self.dur else 0)

    def t_of(self, x: float) -> float:
        r = self.track()
        return max(0.0, min(self.dur, (x - r.left()) / r.width() * self.dur))

    def _hit(self, x: float) -> str:
        if abs(x - self.x_of(self.start)) < 12:
            return "start"
        if abs(x - self.x_of(self.end)) < 12:
            return "end"
        if self.x_of(self.start) < x < self.x_of(self.end):
            return "range"
        return "seek"

    def mousePressEvent(self, e) -> None:
        x = e.position().x()
        self._drag = self._hit(x)
        self._grab = self.t_of(x)
        if self._drag == "seek":
            self.seek.emit(self.t_of(x))
        self._s0, self._e0 = self.start, self.end

    def mouseMoveEvent(self, e) -> None:
        x = e.position().x()
        if self._drag is None:
            hit = self._hit(x)
            self.setCursor(Qt.CursorShape.SizeHorCursor if hit in ("start", "end") else
                           Qt.CursorShape.OpenHandCursor if hit == "range" else Qt.CursorShape.PointingHandCursor)
            return
        t = self.t_of(x)
        if self._drag == "start":
            self.start = min(t, self.end - MIN_LEN)
            self.seek.emit(self.start)
        elif self._drag == "end":
            self.end = max(t, self.start + MIN_LEN)
            self.seek.emit(self.end)
        elif self._drag == "range":
            d = t - self._grab
            d = max(-self._s0, min(self.dur - self._e0, d))
            self.start, self.end = self._s0 + d, self._e0 + d
        else:
            self.seek.emit(t)
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag == "range" and abs(self.t_of(e.position().x()) - self._grab) < 0.01:
            self.seek.emit(self.t_of(e.position().x()))  # a click inside the range just seeks
        self._drag = None

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = self.track()
        clip = QPainterPath()
        clip.addRoundedRect(r, 10, 10)
        p.setClipPath(clip)
        p.fillRect(r, self.pal.track)
        if self.strip is not None:
            p.drawImage(r, self.strip)
        elif self.peaks:
            n = len(self.peaks)
            bw = r.width() / n
            mid = r.center().y()
            for i, v in enumerate(self.peaks):
                t = (i + 0.5) / n * self.dur
                inside = self.start <= t <= self.end
                p.fillRect(QRectF(r.left() + i * bw, mid - v * r.height() * 0.45, max(1.0, bw - 0.6),
                                  max(1.0, v * r.height() * 0.9)), self.accent if inside else self.pal.meta)
        # dim what will be cut
        shade = theme.with_alpha(kit.page_color(self.pal), 0.72)
        xs, xe = self.x_of(self.start), self.x_of(self.end)
        p.fillRect(QRectF(r.left(), r.top(), xs - r.left(), r.height()), shade)
        p.fillRect(QRectF(xe, r.top(), r.right() - xe, r.height()), shade)
        p.setClipping(False)
        # selection frame with grips
        sel = QRectF(xs, r.top() + 1, xe - xs, r.height() - 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(self.accent, 3))
        p.drawRoundedRect(sel, 8, 8)
        for x in (xs, xe):
            grip = QRectF(x - 6, r.top() - 2, 12, r.height() + 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.accent)
            p.drawRoundedRect(grip, 6, 6)
            p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
            for dy in (-6, 0, 6):
                p.drawLine(QPointF(x - 1.5, r.center().y() + dy), QPointF(x + 1.5, r.center().y() + dy))
        # playhead
        xp = self.x_of(self.playhead)
        p.setPen(QPen(QColor(0, 0, 0, 120), 4))
        p.drawLine(QPointF(xp, r.top() - 4), QPointF(xp, r.bottom() + 4))
        p.setPen(QPen(QColor("white"), 2))
        p.drawLine(QPointF(xp, r.top() - 4), QPointF(xp, r.bottom() + 4))
        p.end()


class PlayButton(QAbstractButton):
    """Round accent button with a play or pause glyph. Not a QPushButton: the window's button
    style (padding, minimum height) would squeeze the circle."""

    def __init__(self, accent: QColor):
        super().__init__()
        self.accent, self.playing = accent, False
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Play the kept part (Space)")

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.mix(self.accent, QColor("white"), 0.12) if self.underMouse() else self.accent)
        p.drawEllipse(QRectF(1, 1, 42, 42))
        pm = icon_pixmap("pause_filled" if self.playing else "play_filled", "#ffffff", 20, self.devicePixelRatioF())
        if pm is not None:
            p.drawPixmap(QPointF(12 if self.playing else 13, 12), pm)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(self.accent, 1.5))
            p.drawEllipse(QRectF(0.5, 0.5, 43, 43))
        p.end()


class TrimWindow(kit.ToolWindow):
    def __init__(self, path: Path, pal, accent: QColor):
        self.path = path
        self.dur = duration(probe(path)) or 0.0
        self.video = category_of(path) == "video"
        super().__init__(f"Trim {path.name}", f"{kit.fmt_time(self.dur)} long", "Trim", pal, accent)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        if self.video:
            self.screen_ = QVideoWidget()
            self.screen_.setMinimumSize(640, 360)
            self.screen_.setStyleSheet("background: black; border-radius: 14px;")
            self.player.setVideoOutput(self.screen_)
            self.body.addWidget(self.screen_, 1)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.timeline = Timeline(self.dur, pal, accent, tall=not self.video)
        self.body.addWidget(self.timeline)

        controls = QWidget()
        controls.setFixedHeight(48)     # never squeezed by the video above it
        row = QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.play = PlayButton(accent)
        self.play.clicked.connect(self.toggle_play)
        row.addWidget(self.play)
        row.addSpacing(6)
        self.start_edit, self.end_edit = QLineEdit(), QLineEdit()
        for ed in (self.start_edit, self.end_edit):
            ed.setFixedWidth(92)
            ed.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ed.editingFinished.connect(self._typed)
        row.addWidget(kit.label("Start", "meta"))
        row.addWidget(self.start_edit)
        row.addWidget(kit.label("End", "meta"))
        row.addWidget(self.end_edit)
        self.length = kit.label("", "meta")
        row.addSpacing(8)
        row.addWidget(self.length)
        row.addStretch(1)
        self.body.addWidget(controls)
        self.hint.setText("Space plays the kept part · I and O set start and end at the playhead")

        self.timeline.changed.connect(self._sync)
        self.timeline.seek.connect(self._seek)
        self.player.positionChanged.connect(self._position)
        self.player.playbackStateChanged.connect(lambda _: self._icon())
        self.player.mediaStatusChanged.connect(self._media_status)
        self._sync()
        self._icon()
        signals = _Signals(self)
        signals.loaded.connect(self._loaded)
        QThreadPool.globalInstance().start(_Loader(path, self.dur, self.video, signals))
        self.resize(900, 640 if self.video else 380)

    def _loaded(self, strip, peaks) -> None:
        self.timeline.strip, self.timeline.peaks = strip, peaks
        self.timeline.update()

    def _icon(self) -> None:
        self.play.playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play.update()

    def _media_status(self, status) -> None:
        # Show the first frame instead of a black box until Play is pressed.
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self.video:
            self.player.pause()

    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        tl = self.timeline
        if not (tl.start <= tl.playhead < tl.end - 0.05):
            self._seek(tl.start)
        self.player.play()

    def _seek(self, t: float) -> None:
        self.timeline.playhead = t
        self.player.setPosition(int(t * 1000))
        self.timeline.update()

    def _position(self, ms: int) -> None:
        t = ms / 1000
        tl = self.timeline
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and t >= tl.end:
            self.player.pause()
            self._seek(tl.start)  # ready to play the kept part again
            return
        tl.playhead = t
        tl.update()

    def _typed(self) -> None:
        s, e = kit.parse_time(self.start_edit.text()), kit.parse_time(self.end_edit.text())
        tl = self.timeline
        if s is not None:
            tl.start = max(0.0, min(s, tl.end - MIN_LEN))
        if e is not None:
            tl.end = min(self.dur, max(e, tl.start + MIN_LEN))
        tl.update()
        self._sync()

    def _sync(self) -> None:
        tl = self.timeline
        if not self.start_edit.hasFocus():
            self.start_edit.setText(kit.fmt_time(tl.start))
        if not self.end_edit.hasFocus():
            self.end_edit.setText(kit.fmt_time(tl.end))
        kept = tl.end - tl.start
        self.length.setText(f"Keeping {kit.fmt_time(kept)} of {kit.fmt_time(self.dur)}")
        self.ok.setEnabled(kept >= MIN_LEN and (tl.start > 0.05 or tl.end < self.dur - 0.05))

    def keyPressEvent(self, e) -> None:
        tl = self.timeline
        if e.key() == Qt.Key.Key_Space and not isinstance(self.focusWidget(), QLineEdit):
            self.toggle_play()
        elif e.key() == Qt.Key.Key_I and not isinstance(self.focusWidget(), QLineEdit):
            tl.start = min(tl.playhead, tl.end - MIN_LEN)
            tl.update()
            self._sync()
        elif e.key() == Qt.Key.Key_O and not isinstance(self.focusWidget(), QLineEdit):
            tl.end = max(tl.playhead, tl.start + MIN_LEN)
            tl.update()
            self._sync()
        else:
            super().keyPressEvent(e)

    def done(self, r: int) -> None:
        self.player.stop()
        super().done(r)

    def options(self) -> dict:
        return {"start": self.timeline.start, "end": self.timeline.end}
