"""Settings window. Every change applies immediately; there is no Save button."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QSlider, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from . import kit, theme
from ..win.hooks import key_label

KEYS = [("shift", "Shift"), ("ctrl", "Ctrl"), ("alt", "Alt")]
OTHER = ("other", "Other…")   # fourth segment: records any key or a mouse side button
MODES = [("convert", "Convert", "Formats: PNG, MP4, PDF…"), ("tools", "Tools", "Compress, crop, trim, rotate…"),
         ("actions", "Actions", "Copy, move, open a terminal…")]

DEFAULTS = {"key_convert": "shift", "key_tools": "ctrl", "key_actions": "alt", "wheel_theme": "system",
            "any_app": False, "output_mode": "next", "output_folder": "", "image_quality": 90,
            "keep_exif": True, "compress_level": "balanced", "discord_limit_mb": 20, "hdr_white": 200,
            "clip_inbox": True, "clip_sort": True, "clip_folder": "", "clip_cleanup_days": 30,
            "clip_cleanup_last": 0.0, "repeat_on_hub": True, "last_ops": "{}", "seen_intro": False,
            "pins": "{}", "clipboard_hotkey": "ctrl+alt+r"}
# Discord raised its limits in 2026 (free 10 → 20 MB, Nitro 500 MB → 1 GB): saved old choices follow.
RENAMED = {"discord_limit_mb": {10: 20, 500: 1000}}


def value(settings: QSettings, key: str):
    default = DEFAULTS[key]
    v = settings.value(key, default, type=type(default))
    return RENAMED.get(key, {}).get(v, v)


class SettingsWindow(QWidget):
    def __init__(self, settings: QSettings, on_change, actions: dict):
        """on_change(key) is called after a setting is written; actions holds the Windows hooks
        (sendto_installed, set_sendto, autostart_enabled, set_autostart, open_log)."""
        super().__init__(None, Qt.WindowType.Window)
        self.settings, self.on_change, self.actions = settings, on_change, actions
        self.setWindowTitle(f"{APP_NAME} settings")
        self.pal = theme.palette(value(settings, "wheel_theme"))
        self.accent = self.pal.accents["convert"]
        self._build()
        self.resize(680, 760)

    # -- building blocks ---------------------------------------------------

    def _build(self) -> None:
        pal, accent = self.pal, self.accent
        self.setStyleSheet(kit.stylesheet(pal, accent))
        self.setObjectName("page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        if self.layout():
            QWidget().setLayout(self.layout())  # drop the old tree when the theme changes
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        page.setObjectName("page")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        col = QVBoxLayout(page)
        col.setContentsMargins(28, 24, 28, 28)
        col.setSpacing(10)
        col.addWidget(kit.label(f"{APP_NAME} settings", "title"))
        col.addWidget(kit.label("Changes apply right away.", "meta"))
        col.addSpacing(10)

        # keys
        col.addWidget(kit.label("KEYS TO TAP WHILE DRAGGING", "section"))
        col.addWidget(kit.label("Other… records any key or a mouse side button (Mouse 4 and 5). Tap a wheel's key "
                                "again over a tile to pin it to the front as a favourite.", "meta", wrap=True))
        card, rows = self._card()
        self.key_controls: dict[str, kit.Segmented] = {}
        for mode, name, desc in MODES:
            seg = kit.Segmented([*KEYS, OTHER], "shift", pal, pal.accents[mode], seg_width=78)
            self.key_controls[mode] = seg
            self._show_key(mode, value(self.settings, f"key_{mode}"), animate=False)
            seg.changed.connect(lambda key, m=mode: key != "other" and not key.startswith(("vk:", "mouse:"))
                                and self._set_key(m, key))
            seg.pressed.connect(lambda key, m=mode: key not in dict(KEYS) and self._record_key(m))
            self._row(rows, name, desc, seg, dot=pal.accents[mode])
        col.addWidget(card)

        # wheel
        col.addSpacing(8)
        col.addWidget(kit.label("WHEEL", "section"))
        card, rows = self._card()
        th = kit.Segmented([("system", "Match Windows"), ("light", "Light"), ("dark", "Dark")],
                           value(self.settings, "wheel_theme"), pal, accent)
        th.changed.connect(self._set_theme)
        self._row(rows, "Theme", "Also used by these windows", th)
        self._row(rows, "Work with drags from any app", "Not only File Explorer and the desktop",
                  self._toggle("any_app"))
        self._row(rows, "Drop in the middle to repeat", "Letting go on the centre redoes your last pick for that "
                  "kind of file. Off: the centre cancels.", self._toggle("repeat_on_hub"))
        hk = kit.Segmented([("", "Off"), ("ctrl+alt+r", "Ctrl+Alt+R"), ("ctrl+alt+v", "Ctrl+Alt+V")],
                           value(self.settings, "clipboard_hotkey"), pal, accent)
        hk.changed.connect(lambda v: self._put("clipboard_hotkey", v))
        self._row(rows, "Wheel for the clipboard", "Opens the wheel for a file or image you've copied, "
                  "without dragging anything", hk)
        col.addWidget(card)

        # saving
        col.addSpacing(8)
        col.addWidget(kit.label("SAVING", "section"))
        card, rows = self._card()
        where = kit.Segmented([("next", "Next to the original"), ("folder", "In one folder")],
                              value(self.settings, "output_mode"), pal, accent)
        where.changed.connect(lambda v: self._put("output_mode", v) or self._refresh_folder())
        self._row(rows, "Save results", "Nothing is ever overwritten", where)
        self.folder_label = kit.label("", "meta")
        choose = kit.button("Choose folder…")
        choose.clicked.connect(self._choose_folder)
        box = QHBoxLayout()
        box.setSpacing(10)
        box.addWidget(self.folder_label)
        box.addWidget(choose)
        holder = QWidget()
        holder.setLayout(box)
        self.folder_row = self._row(rows, "Folder", "", holder)
        self._refresh_folder()

        q = QSlider(Qt.Orientation.Horizontal)
        q.setRange(60, 100)
        q.setValue(value(self.settings, "image_quality"))
        q.setFixedWidth(180)
        qlabel = kit.label(str(q.value()))
        qlabel.setFixedWidth(28)
        q.valueChanged.connect(lambda v: (qlabel.setText(str(v)), self._put("image_quality", v)))
        qbox = QHBoxLayout()
        qbox.addWidget(q)
        qbox.addWidget(qlabel)
        qholder = QWidget()
        qholder.setLayout(qbox)
        self._row(rows, "Photo quality", "For JPG, WEBP, HEIC and AVIF conversions", qholder)
        self._row(rows, "Keep camera and location data", "When converting photos. Remove metadata always strips it.",
                  self._toggle("keep_exif"))
        lvl = kit.Segmented([("light", "Light"), ("balanced", "Balanced"), ("strong", "Strong")],
                            value(self.settings, "compress_level"), pal, accent)
        lvl.changed.connect(lambda v: self._put("compress_level", v))
        self._row(rows, "Quick Compress strength", "The Compress tile; Compress to size… asks each time", lvl)
        dc = kit.Segmented([("20", "20 MB"), ("50", "50 MB"), ("1000", "1 GB")],
                           str(value(self.settings, "discord_limit_mb")), pal, accent)
        dc.changed.connect(lambda v: self._put("discord_limit_mb", int(v)))
        self._row(rows, "Discord upload limit", "For Discord fits clips under this: 20 MB free, 50 MB with Nitro Basic, "
                  "1 GB with Nitro",
                  dc)
        hw = kit.Segmented([("120", "Dim"), ("200", "Standard"), ("300", "Bright")],
                           str(value(self.settings, "hdr_white")), pal, accent)
        hw.changed.connect(lambda v: self._put("hdr_white", int(v)))
        self._row(rows, "HDR screenshot brightness", "How bright .jxr captures come out after conversion", hw)
        col.addWidget(card)

        # clip inbox
        col.addSpacing(8)
        col.addWidget(kit.label("CLIP INBOX", "section"))
        card, rows = self._card()
        self._row(rows, "Show a card for new game clips",
                  "Game Bar, NVIDIA, AMD, OBS and Discord clips. Cards wait until you leave the game.",
                  self._toggle("clip_inbox"))
        self._row(rows, "Sort kept clips by game",
                  "Moves each clip into a folder named after its game. Game Bar's own gallery only lists "
                  "clips left loose in Captures.", self._toggle("clip_sort"))
        pick = kit.button("Choose…")
        pick.clicked.connect(self._choose_clip_folder)
        clear = kit.button("Clear")
        clear.clicked.connect(lambda: self._put("clip_folder", "") or self._refresh_clip_folder())
        cbox = QHBoxLayout()
        cbox.setSpacing(8)
        cbox.addWidget(pick)
        cbox.addWidget(clear)
        cholder = QWidget()
        cholder.setLayout(cbox)
        row = self._row(rows, "Also watch a folder", "-", cholder)
        self.clip_folder_label = next(lb for lb in row.findChildren(QLabel) if lb.property("role") == "meta")
        self._refresh_clip_folder()
        old = kit.Segmented([("0", "Off"), ("30", "30 days"), ("90", "90 days")],
                            str(value(self.settings, "clip_cleanup_days")), pal, accent)
        old.changed.connect(lambda v: self._put("clip_cleanup_days", int(v)))
        self._row(rows, "Remind me about old clips", "Only once they add up to 2 GB. Old clips go to the "
                  "Recycle Bin only when you tick them.", old)
        col.addWidget(card)

        # windows
        col.addSpacing(8)
        col.addWidget(kit.label("WINDOWS", "section"))
        card, rows = self._card()
        auto = kit.Toggle(self.actions["autostart_enabled"](), pal, accent)
        auto.toggled.connect(self.actions["set_autostart"])
        self._row(rows, "Start with Windows", "Recast waits quietly in the tray", auto)
        send = kit.Toggle(self.actions["sendto_installed"](), pal, accent)
        send.toggled.connect(self.actions["set_sendto"])
        self._row(rows, "Show in the Send to menu", "Right-click files › Send to › Recast", send)
        log = kit.button("Open log")
        log.clicked.connect(self.actions["open_log"])
        self._row(rows, "Log file", "Useful if something doesn't convert", log)
        col.addWidget(card)

        col.addSpacing(6)
        col.addWidget(kit.label(f"{APP_NAME} {__version__} · Everything runs on this PC; nothing is uploaded.",
                                "meta"))
        col.addStretch(1)
        scroll.setWidget(page)
        outer.addWidget(scroll)

    def _card(self) -> tuple[QWidget, QVBoxLayout]:
        card = QWidget()
        card.setProperty("role", "card")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        rows = QVBoxLayout(card)
        rows.setContentsMargins(18, 6, 18, 6)
        rows.setSpacing(0)
        return card, rows

    def _row(self, rows: QVBoxLayout, title: str, desc: str, control: QWidget, dot: QColor | None = None) -> QWidget:
        if rows.count():
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background: {theme.with_alpha(self.pal.keyline, 1.2).name(QColor.NameFormat.HexArgb)};"
                               " border: none;")
            rows.addWidget(line)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 12, 0, 12)
        h.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(1)
        head = QHBoxLayout()
        head.setSpacing(8)
        if dot is not None:
            d = QWidget()
            d.setFixedSize(10, 10)
            d.setStyleSheet(f"background: {dot.name()}; border-radius: 5px;")
            head.addWidget(d)
        t = kit.label(title)
        t.setStyleSheet("font-weight: 600;")
        head.addWidget(t)
        head.addStretch(1)
        text.addLayout(head)
        if desc:
            text.addWidget(kit.label(desc, "meta", wrap=True))
        h.addLayout(text, 1)
        h.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        rows.addWidget(row)
        return row

    def _toggle(self, key: str) -> kit.Toggle:
        t = kit.Toggle(value(self.settings, key), self.pal, self.accent)
        t.toggled.connect(lambda on: self._put(key, on))
        return t

    # -- behaviour ---------------------------------------------------------------

    def _put(self, key: str, v) -> None:
        self.settings.setValue(key, v)
        self.on_change(key)

    def _show_key(self, mode: str, binding: str, animate: bool = True) -> None:
        """Select `binding` in the wheel's control; a custom key takes over the fourth segment."""
        seg = self.key_controls[mode]
        seg.options[3] = OTHER if binding in dict(KEYS) else (binding, key_label(binding))
        seg.colors = {k: self.pal.accents[mode] for k, _ in seg.options}
        if animate:
            seg.set_value(binding)
        else:
            seg.value = binding
            seg._x = seg._index() * seg.seg_w
        seg.update()

    def _record_key(self, mode: str) -> None:
        name = next(n for m, n, _ in MODES if m == mode)
        binding = kit.KeyCapture(name, self.pal, self.pal.accents[mode], self).capture()
        if binding:
            self._set_key(mode, binding)
        else:
            self._show_key(mode, value(self.settings, f"key_{mode}"))

    def _set_key(self, mode: str, key: str) -> None:
        # Each key drives one wheel: the wheel that had this key takes the freed one.
        old = value(self.settings, f"key_{mode}")
        for other, _, _ in MODES:
            if other != mode and value(self.settings, f"key_{other}") == key:
                self.settings.setValue(f"key_{other}", old)
                self._show_key(other, old)
        self._put(f"key_{mode}", key)
        self._show_key(mode, key)

    def _set_theme(self, v: str) -> None:
        self._put("wheel_theme", v)
        self.pal = theme.palette(v)
        self.accent = self.pal.accents["convert"]
        QTimer.singleShot(150, self._rebuild)  # let the segment finish sliding first

    def _rebuild(self) -> None:
        self._build()
        kit.dark_title_bar(self, self.pal.dark)

    def _refresh_folder(self) -> None:
        folder = value(self.settings, "output_folder") or str(Path.home() / "Documents" / "Recast")
        self.folder_label.setText(folder)
        self.folder_row.setVisible(value(self.settings, "output_mode") == "folder")

    def _choose_folder(self) -> None:
        start = value(self.settings, "output_folder") or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Save results in", start)
        if folder:
            self._put("output_folder", folder)
            self._refresh_folder()

    def _refresh_clip_folder(self) -> None:
        folder = value(self.settings, "clip_folder")
        self.clip_folder_label.setText(folder or "Videos and Game Bar's Captures are always watched. "
                                       "Add one more, like a Medal or Steam export folder.")

    def _choose_clip_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Also watch for clips in",
                                                  value(self.settings, "clip_folder") or str(Path.home()))
        if folder:
            self._put("clip_folder", folder)
            self._refresh_clip_folder()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        kit.dark_title_bar(self, self.pal.dark)
        QTimer.singleShot(0, lambda: kit.force_foreground(self))
