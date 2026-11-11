"""Shared window chrome and controls in the wheel's visual language (Halo).

Tool windows (crop, trim, compress to…) and Settings are ordinary Qt windows with
the native title bar, styled from the same Palette as the wheel: white keycap
surfaces on a cool page, one accent per wheel, pills and 10–14px radii.
"""

from __future__ import annotations

import ctypes
import math
from ctypes import wintypes

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme
from .theme import Palette

PAGE_LIGHT, PAGE_DARK = QColor("#F4F5F8"), QColor("#1B1D22")


def page_color(pal: Palette) -> QColor:
    return PAGE_DARK if pal.dark else PAGE_LIGHT


def stylesheet(pal: Palette, accent: QColor) -> str:
    page = page_color(pal).name()
    tile, text, meta = pal.tile.name(), pal.text.name(), pal.meta.name()
    line = f"rgba({pal.keyline.red()},{pal.keyline.green()},{pal.keyline.blue()},{max(0.10, pal.keyline.alphaF() * 1.4):.2f})"
    acc = accent.name()
    acc_hover = theme.mix(accent, QColor("white"), 0.12).name()
    track = pal.track.name()
    return f"""
    QWidget {{ background: transparent; color: {text}; font-family: "Segoe UI Variable Text", "Segoe UI";
               font-size: 14px; }}
    QDialog, QWidget#page {{ background: {page}; }}
    QLabel[role="title"] {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 20px; font-weight: 600; }}
    QLabel[role="meta"] {{ color: {meta}; font-size: 13px; }}
    QLabel[role="section"] {{ color: {meta}; font-size: 12px; font-weight: 600; letter-spacing: 0.4px; }}
    QLabel[role="big"] {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 28px; font-weight: 700; }}
    QWidget[role="card"] {{ background: {tile}; border: 1px solid {line}; border-radius: 14px; }}
    QWidget[role="card"] QLabel {{ background: transparent; }}
    QPushButton {{ background: {tile}; border: 1px solid {line}; border-radius: 10px; padding: 8px 16px;
                   font-weight: 600; min-height: 20px; }}
    QPushButton:hover {{ background: {track}; }}
    QPushButton:pressed {{ padding-top: 9px; padding-bottom: 7px; }}
    QPushButton:focus {{ border: 1px solid {acc}; }}
    QPushButton[primary="true"] {{ background: {acc}; color: white; border: 1px solid {acc}; }}
    QPushButton[primary="true"]:hover {{ background: {acc_hover}; }}
    QPushButton[primary="true"]:disabled {{ background: {track}; color: {meta}; border-color: {line}; }}
    QLineEdit {{ background: {tile}; border: 1px solid {line}; border-radius: 8px; padding: 6px 10px;
                 selection-background-color: {acc}; font-variant-numeric: tabular-nums; }}
    QLineEdit:focus {{ border: 1px solid {acc}; }}
    QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {track}; }}
    QSlider::sub-page:horizontal {{ height: 6px; border-radius: 3px; background: {acc}; }}
    QSlider::handle:horizontal {{ width: 20px; height: 20px; margin: -8px 0; border-radius: 10px;
                                   background: {tile}; border: 1px solid {line}; }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
    QScrollBar::handle:vertical {{ background: {line}; border-radius: 3px; min-height: 40px; }}
    QScrollBar::handle:vertical:hover {{ background: {meta}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ height: 0; background: none; }}
    QToolTip {{ background: {tile}; color: {text}; border: 1px solid {line}; padding: 4px 8px; }}
    """


# ---- Windows niceties ------------------------------------------------------

def dark_title_bar(widget: QWidget, dark: bool) -> None:
    try:
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(int(widget.winId())), 20,
                                                   ctypes.byref(value), ctypes.sizeof(value))
    except (OSError, AttributeError):
        pass


def force_foreground(widget: QWidget) -> None:
    """Bring a window to the front even though Explorer had focus (after a drop).

    Windows only lets the foreground process hand focus over; attaching to its input
    queue for a moment makes us part of it.
    """
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = int(widget.winId())
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    me = kernel32.GetCurrentThreadId()
    attached = fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    widget.activateWindow()
    widget.raise_()


# ---- controls -----------------------------------------------------------------

class Segmented(QWidget):
    """Pill-shaped choice with a sliding indicator, like the wheel switcher."""

    changed = Signal(str)
    pressed = Signal(str)   # every click, even on the current choice

    def __init__(self, options: list[tuple[str, str]], value: str, pal: Palette, accent: QColor,
                 seg_width: int | None = None, parent=None):
        super().__init__(parent)
        self.options, self.value, self.pal, self.accent = options, value, pal, accent
        f = theme.font(13, theme.QFont.Weight.DemiBold)
        fm = QFontMetricsF(f)
        self.seg_w = seg_width or int(max(fm.horizontalAdvance(lbl) for _, lbl in options) + 28)
        self.setFixedSize(self.seg_w * len(options) + 6, 38)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._x = self._index() * self.seg_w
        self._timer = QTimer(self, interval=16)
        self._timer.timeout.connect(self._animate)
        self.colors: dict[str, QColor] = {}   # optional per-option accent (keys rows)

    def _index(self) -> int:
        keys = [k for k, _ in self.options]
        return keys.index(self.value) if self.value in keys else 0

    def set_value(self, value: str, emit: bool = False) -> None:
        if value == self.value:
            return
        self.value = value
        self._timer.start()
        if emit:
            self.changed.emit(value)
        self.update()

    def _animate(self) -> None:
        target = self._index() * self.seg_w
        self._x += (target - self._x) * 0.3
        if abs(target - self._x) < 0.5:
            self._x = target
            self._timer.stop()
        self.update()

    def mousePressEvent(self, e) -> None:
        i = int((e.position().x() - 3) // self.seg_w)
        if 0 <= i < len(self.options):
            self.set_value(self.options[i][0], emit=True)
            self.pressed.emit(self.options[i][0])

    def keyPressEvent(self, e) -> None:
        i = self._index()
        if e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.set_value(self.options[min(len(self.options) - 1, i + 1)][0], emit=True)
        elif e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.set_value(self.options[max(0, i - 1)][0], emit=True)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(self.pal.keyline, 1))
        p.setBrush(self.pal.track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        ind = QRectF(3 + self._x, 3, self.seg_w, self.height() - 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.with_alpha(self.pal.shadow, 1.0))
        p.drawRoundedRect(ind.translated(0, 1), ind.height() / 2, ind.height() / 2)
        p.setBrush(self.pal.tile)
        if self.hasFocus():
            p.setPen(QPen(self.colors.get(self.value, self.accent), 1.5))
        p.drawRoundedRect(ind, ind.height() / 2, ind.height() / 2)
        p.setFont(theme.font(13, theme.QFont.Weight.DemiBold))
        for i, (key, label) in enumerate(self.options):
            seg = QRectF(3 + i * self.seg_w, 3, self.seg_w, self.height() - 6)
            on = key == self.value
            p.setPen(self.colors.get(key, self.pal.text) if on and key in self.colors else
                     self.pal.text if on else self.pal.meta)
            p.drawText(seg, Qt.AlignmentFlag.AlignCenter, label)
        p.end()


class Toggle(QAbstractButton):
    """On/off switch: accent track and a sliding white knob."""

    def __init__(self, checked: bool, pal: Palette, accent: QColor, parent=None):
        super().__init__(parent)
        self.pal, self.accent = pal, accent
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._timer = QTimer(self, interval=16)
        self._timer.timeout.connect(self._animate)
        self.toggled.connect(lambda _: self._timer.start())

    def _animate(self) -> None:
        target = 1.0 if self.isChecked() else 0.0
        self._pos += (target - self._pos) * 0.35
        if abs(target - self._pos) < 0.01:
            self._pos = target
            self._timer.stop()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(44, 24)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(self.pal.keyline, 1))
        p.setBrush(theme.mix(self.pal.track, self.accent, self._pos))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - 6 - d) * self._pos
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QRectF(x, r.top() + 4, d, d))
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(self.accent, 1.5))
            p.drawRoundedRect(r.adjusted(-0.5, -0.5, 0.5, 0.5), r.height() / 2, r.height() / 2)
        p.end()


def label(text: str, role: str = "", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    lbl.setWordWrap(wrap)
    return lbl


def button(text: str, primary: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        b.setProperty("primary", True)
        b.setDefault(True)
    return b


class ToolWindow(QDialog):
    """A tool's settings window: title, subtitle, a body, and Cancel / primary action.

    exec_options() returns the options dict, or None when cancelled.
    """

    def __init__(self, title: str, subtitle: str, action: str, pal: Palette, accent: QColor):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.pal, self.accent = pal, accent
        self.setWindowTitle(title)
        self.setStyleSheet(stylesheet(pal, accent))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(16)
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(label(title, "title"))
        self.subtitle = label(subtitle, "meta")
        head.addWidget(self.subtitle)
        outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        outer.addLayout(self.body, 1)
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.hint = label("", "meta")
        self.hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        foot.addWidget(self.hint)
        self.cancel = button("Cancel")
        self.ok = button(action, primary=True)
        self.cancel.clicked.connect(self.reject)
        self.ok.clicked.connect(self.accept)
        foot.addWidget(self.cancel)
        foot.addWidget(self.ok)
        outer.addLayout(foot)

    def options(self) -> dict:
        raise NotImplementedError

    def showEvent(self, e) -> None:
        super().showEvent(e)
        dark_title_bar(self, self.pal.dark)
        QTimer.singleShot(0, lambda: force_foreground(self))

    def exec_options(self) -> dict | None:
        return self.options() if self.exec() == QDialog.DialogCode.Accepted else None


def fmt_time(seconds: float) -> str:
    """0:07.5, 12:30.0, 1:02:03.4"""
    m, s = divmod(max(0.0, seconds), 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


def parse_time(text: str) -> float | None:
    try:
        parts = [float(x) for x in text.strip().replace(",", ".").split(":")]
    except ValueError:
        return None
    total = 0.0
    for x in parts:
        total = total * 60 + x
    return total if math.isfinite(total) else None


class KeyCapture(QDialog):
    """"Press a key for Tools": returns a hook binding ("vk:32", "mouse:x1", "shift"…) or None."""

    def __init__(self, wheel: str, pal: Palette, accent: QColor, parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.pal, self.accent, self.wheel = pal, accent, wheel
        self.binding: str | None = None
        self.message = "Esc to cancel"
        self.setFixedSize(360, 150)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def capture(self) -> str | None:
        self.exec()
        return self.binding

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self.parentWidget():
            self.move(self.parentWidget().geometry().center() - self.rect().center())
        QTimer.singleShot(0, lambda: (force_foreground(self), self.setFocus()))

    def event(self, e) -> bool:
        # Catch Tab, arrows and the like before Qt turns them into focus moves.
        if e.type() == e.Type.KeyPress:
            from ..win.hooks import binding_for_vk
            if e.key() == Qt.Key.Key_Escape:
                self.reject()
                return True
            binding = binding_for_vk(e.nativeVirtualKey())
            if binding is None:
                self.message = "That key is reserved. Try another, or Esc to cancel."
                self.update()
            else:
                self.binding = binding
                self.accept()
            return True
        if e.type() == e.Type.MouseButtonPress:
            side = {Qt.MouseButton.BackButton: "mouse:x1", Qt.MouseButton.ForwardButton: "mouse:x2"}
            if e.button() in side:
                self.binding = side[e.button()]
                self.accept()
                return True
        return super().event(e)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(6.5, 4.5, self.width() - 13, self.height() - 13)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 40 if not self.pal.dark else 110))
        p.drawRoundedRect(r.adjusted(-1, 1, 1, 5), 16, 16)
        p.setPen(QPen(self.pal.keyline, 1))
        p.setBrush(self.pal.hub)
        p.drawRoundedRect(r, 15, 15)
        p.setPen(self.pal.text)
        p.setFont(theme.font(17, theme.QFont.Weight.DemiBold, display=True))
        p.drawText(r.adjusted(20, 22, -20, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   f"Press a key for {self.wheel}")
        p.setPen(self.pal.meta)
        p.setFont(theme.font(13))
        p.drawText(r.adjusted(20, 54, -20, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   "Any key, or a mouse side button")
        cap = QRectF(r.center().x() - 110, r.bottom() - 46, 220, 30)
        p.setPen(QPen(self.accent, 1.5, Qt.PenStyle.DashLine))
        p.setBrush(theme.with_alpha(self.accent, 0.08))
        p.drawRoundedRect(cap, 9, 9)
        p.setPen(self.pal.error if "reserved" in self.message else self.pal.meta)
        p.setFont(theme.font(12))
        reserved = "reserved" in self.message
        p.drawText(cap, Qt.AlignmentFlag.AlignCenter, "Reserved, try another key" if reserved else "listening…  Esc cancels")
        p.end()
