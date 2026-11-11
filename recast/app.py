"""Tray application: wires the drag hook, the wheel, jobs and settings together."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QSettings, QStandardPaths, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import APP_NAME, __version__, paths
from . import actions  # noqa: F401  (registers the Alt wheel)
from . import clips, helper, jobs
from .engine import REGISTRY, Operation, images, media
from .estimate import human
from .ui import logo, theme
from .ui.settings import SettingsWindow, value
from .ui.toast import Toast
from .ui.wheel import MODES, Wheel, clear_preview_cache
from .win import shell, watch
from .win.hooks import DragKeyHook, short_label
from .win.hotkey import Hotkey, label as hotkey_label

log = logging.getLogger(__name__)
SERVER_NAME = f"{APP_NAME}-{os.environ.get('USERNAME', 'user')}"
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME


def setup_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / "recast.log"
    logging.basicConfig(filename=path, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return path


class _Clipboard(QObject):
    """Lives on the GUI thread, so job results (emitted from a worker) reach the clipboard safely."""

    @Slot(list)
    def put(self, outputs: list) -> None:
        if outputs:
            actions.files_to_clipboard(outputs)


class _OldClips(QObject):
    found = Signal(list, int, bool)   # clips, days, asked from the tray menu


class RecastApp:
    def __init__(self, app: QApplication):
        self.app = app
        self.settings = QSettings(APP_NAME, APP_NAME)
        self.settings_window: SettingsWindow | None = None
        self.wheel = Wheel(self.theme_setting)
        self.wheel.chosen.connect(self.start_job)
        self.wheel.handoff.connect(self.show_card)
        self.wheel.closed.connect(self._wheel_closed)
        self.wheel.undone.connect(lambda made: log.info("undone: %s", [str(m) for m in made]))
        self.wheel.pins_changed.connect(lambda: self.settings.setValue("pins", json.dumps(self.wheel.pins)))
        try:
            self.wheel.last_ops = dict(json.loads(value(self.settings, "last_ops")))
            self.wheel.pins = {k: list(v) for k, v in json.loads(value(self.settings, "pins")).items()}
        except (TypeError, ValueError, AttributeError):
            pass
        self.hotkey = Hotkey()
        self.hotkey.pressed.connect(self.wheel_for_clipboard)

        self.hook = DragKeyHook(any_app=value(self.settings, "any_app"))
        queued = Qt.ConnectionType.QueuedConnection  # never run slots inside the hook callback
        self.hook.tapped.connect(self.on_tap, queued)
        self.hook.released.connect(self.on_release, queued)
        self.hook.escape.connect(self.wheel.dismiss, queued)
        self.hook.install()

        self.server = QLocalServer()
        QLocalServer.removeServer(SERVER_NAME)
        self.server.listen(SERVER_NAME)
        self.server.newConnection.connect(self._on_connection)

        self._dark_taskbar = logo.taskbar_is_dark()
        self.tray = QSystemTrayIcon(logo.tray_icon(self._dark_taskbar))
        self.tray.setToolTip(f"{APP_NAME}: drag a file, then tap a key")
        self.tray.setContextMenu(self._menu())
        self.tray.activated.connect(self._on_tray)
        self.tray.show()
        # Follow the taskbar's light/dark setting so the mark's square half stays visible.
        self._theme_timer = QTimer(interval=15000, timeout=self._check_taskbar)
        self._theme_timer.start()
        self._jobs: set[jobs.Job] = set()
        self._clipboard = _Clipboard()
        self.clips = clips.ClipInbox()
        self.clips.ready.connect(self._show_clip)
        self._watching: tuple | None = None
        self._old = _OldClips()
        self._old.found.connect(self._old_clips_found)
        # Look for old clips a little after startup, then a few times a day.
        QTimer.singleShot(90_000, self._check_old_clips)
        self._old_timer = QTimer(interval=6 * 3600 * 1000, timeout=self._check_old_clips)
        self._old_timer.start()
        self.apply_settings()
        # Idle housekeeping: warm the wheel up once, and give memory back whenever things go quiet.
        QTimer.singleShot(1500, self.wheel.prewarm)
        self._idle = QTimer(interval=20_000, singleShot=True, timeout=self._went_idle)
        self._idle.start()

    # -- settings ---------------------------------------------------------------

    def _check_taskbar(self) -> None:
        dark = logo.taskbar_is_dark()
        if dark != self._dark_taskbar:
            self._dark_taskbar = dark
            self.tray.setIcon(logo.tray_icon(dark))

    def key_to_mode(self) -> dict[str, str]:
        return {value(self.settings, f"key_{m}"): m for m in MODES}

    def theme_setting(self) -> str:
        return value(self.settings, "wheel_theme")

    def apply_settings(self, key: str = "") -> None:
        self.hook.any_app = value(self.settings, "any_app")
        if value(self.settings, "output_mode") == "folder":
            folder = value(self.settings, "output_folder") or str(Path.home() / "Documents" / APP_NAME)
            paths.OUTPUT_DIR = Path(folder)
        else:
            paths.OUTPUT_DIR = None
        self.wheel.key_labels = {m: short_label(value(self.settings, f"key_{m}")) for m in MODES}
        self.hook.bindings = set(self.key_to_mode())
        media.DISCORD_LIMIT_MB = value(self.settings, "discord_limit_mb")
        images.HDR_WHITE_NITS = value(self.settings, "hdr_white")
        watching = (value(self.settings, "clip_inbox"), value(self.settings, "clip_folder"))
        if watching != self._watching:
            self._watching = watching
            if watching[0]:
                self.clips.start(clips.watch_roots(watching[1]))
            else:
                self.clips.stop()
        self.wheel.repeat_on = value(self.settings, "repeat_on_hub")
        combo = value(self.settings, "clipboard_hotkey")
        if not self.hotkey.set(combo) and key == "clipboard_hotkey":
            self.tray.showMessage(APP_NAME, f"{hotkey_label(combo)} is already used by another program.",
                                  self.tray.icon(), 5000)
        clear_preview_cache()      # estimates depend on quality and limits
        if key:
            log.info("setting %s changed", key)

    def options(self) -> dict:
        return {k: value(self.settings, k)
                for k in ("compress_level", "image_quality", "keep_exif", "discord_limit_mb")}

    def open_settings(self) -> None:
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.settings, self.apply_settings, {
                "sendto_installed": shell.sendto_installed,
                "set_sendto": lambda on: shell.install_sendto() if on else shell.uninstall_sendto(),
                "autostart_enabled": shell.autostart_enabled,
                "set_autostart": shell.set_autostart,
                "open_log": lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR / "recast.log"))),
            })
        self.settings_window.show()
        self.settings_window.raise_()

    # -- wheel -------------------------------------------------------------

    def on_tap(self, group: str, _pos: QPoint) -> None:
        mode = self.key_to_mode()[group]
        if self.wheel.isVisible() and self.wheel.state == "pick":
            if mode == self.wheel.mode:
                self.wheel.toggle_pin()     # same key again over a tile: favourite it
            else:
                self.wheel.set_mode(mode)
            return
        # A new drag while the last drop is still showing: move that job to a card.
        self.wheel.release_job()
        self.hook.wheel_open = True
        self.wheel.open_at(QCursor.pos(), mode)

    def on_release(self, _pos: QPoint) -> None:
        # The hook sees the button come up before Explorer delivers the drop to us,
        # so wait a moment: if the drop landed on the wheel, it's no longer picking.
        QTimer.singleShot(300, self._after_release)

    def _after_release(self) -> None:
        if self.wheel.isVisible() and not self.wheel.click_mode and self.wheel.state == "pick":
            self.wheel.dismiss()

    def _wheel_closed(self) -> None:
        self.hook.wheel_open = False
        self._idle.start()

    def _went_idle(self) -> None:
        if self._jobs or self.wheel.isVisible():
            self._idle.start()      # still busy: look again later
        else:
            shell.trim_memory()

    def show_wheel_for(self, files: list[Path]) -> None:
        files = [f for f in files if f.exists()]
        if not files:
            return
        self.hook.wheel_open = True
        self.wheel.open_at(QCursor.pos(), "convert", files)

    def wheel_for_clipboard(self) -> None:
        """The hotkey: open the wheel for whatever was copied (files, or an image saved to Pictures)."""
        if self.wheel.isVisible():
            return
        md = QApplication.clipboard().mimeData()
        files = [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []
        if not files and md.hasImage():
            image = QApplication.clipboard().image()
            if not image.isNull():
                folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)) / APP_NAME
                folder.mkdir(parents=True, exist_ok=True)
                target = paths.unique_path(folder / f"Clipboard {time.strftime('%Y-%m-%d %H-%M-%S')}.png")
                if image.save(str(target), "PNG"):
                    files = [target]
        if files:
            self.show_wheel_for(files)
        else:
            self.tray.showMessage(APP_NAME, "Copy a file or an image first, then press the hotkey.",
                                  self.tray.icon(), 4000)

    # -- jobs ------------------------------------------------------------------

    def start_job(self, operation: Operation, files: list[Path]) -> None:
        """A tile was picked. Runs after the drop returns: Explorer waits on our drop handler,
        so dialogs and jobs must not start inside it."""
        self.hook.wheel_open = False
        self.settings.setValue("last_ops", json.dumps(self.wheel.last_ops))   # for "drop in the middle to repeat"
        QTimer.singleShot(0, lambda: self._start(operation, files))

    def _start(self, operation: Operation, files: list[Path], from_card: bool = False) -> None:
        options = self.options()
        in_helper = helper.asks_in_helper(operation)    # its window opens in the helper process
        if operation.asks:
            self.wheel.dismiss()
            if not in_helper:
                asked = self.ask(operation, files)
                if asked is None:
                    return
                options.update(asked)
        job = jobs.Job(operation, files, options, ask=in_helper, theme=self.theme_setting())
        self._jobs.add(job)
        self._idle.start()
        for sig in (job.finished, job.failed, job.cancelled):
            sig.connect(lambda *_, j=job: self._jobs.discard(j))
        # Clip Inbox mustn't mistake our own results for new clips.
        self.clips.job_started(files)
        job.finished.connect(lambda outs, f=files: self.clips.job_ended(f, outs))
        job.failed.connect(lambda _m, f=files: self.clips.job_ended(f, []))
        job.cancelled.connect(lambda f=files: self.clips.job_ended(f, []))
        if operation.copy_result:
            job.finished.connect(self._clipboard.put)
        if in_helper:
            job.accepted.connect(lambda j=job: self.show_card(j, 0.0))   # once its window is confirmed
        elif operation.asks or from_card:
            self.show_card(job, 0.0)
        else:
            self.wheel.track(job)
        jobs.start(job)

    def ask(self, operation: Operation, files: list[Path]) -> dict | None:
        from .ui.ask import ask
        try:
            return ask(operation, files, theme.palette(self.theme_setting()))
        except Exception:
            log.exception("couldn't open the %s window", operation.id)
            self.tray.showMessage(APP_NAME, f"Couldn't open {files[0].name}", self.tray.icon(), 5000)
            return None

    def show_card(self, job: jobs.Job, fraction: float) -> None:
        Toast(job, fraction, self.theme_setting())

    # -- clip inbox ---------------------------------------------------------------

    def _show_clip(self, clip: clips.Clip) -> None:
        from .ui.clipcard import clip_card
        card = clip_card(clip, self.theme_setting())
        # Run the answer after the card has gone: Trim opens a window with its own event loop.
        card.chosen.connect(lambda key, c=clip: QTimer.singleShot(0, lambda: self._clip_chosen(c, key)))
        card.closed.connect(self.clips.card_closed)

    def _clip_chosen(self, clip: clips.Clip, key: str) -> None:
        if key == "delete":
            if watch.recycle([clip.path]):
                log.info("clip %s moved to the Recycle Bin", clip.path)
            return
        path = self.clips.keep(clip, value(self.settings, "clip_sort"))
        if not path.exists():
            return
        if key == "trim":
            self._start(REGISTRY["video.trim"], [path], from_card=True)
        elif key == "discord":
            self._start(REGISTRY["video.discord"], [path], from_card=True)

    def _check_old_clips(self, force: bool = False) -> None:
        days = value(self.settings, "clip_cleanup_days") or (30 if force else 0)
        if not days or (not force and not value(self.settings, "clip_inbox")):
            return
        if not force and time.time() - value(self.settings, "clip_cleanup_last") < days * 86400:
            return
        roots = clips.watch_roots(value(self.settings, "clip_folder"))
        threading.Thread(target=lambda: self._old.found.emit(clips.old_clips(roots, days), days, force),
                         daemon=True).start()

    def _old_clips_found(self, found: list, days: int, asked: bool) -> None:
        total = sum(p.stat().st_size for p in found if p.exists())
        if asked:
            self._review_old_clips(found, days)
            return
        if total < clips.REMIND_BYTES:
            return
        self.settings.setValue("clip_cleanup_last", time.time())
        from .ui.clipcard import ClipCard
        card = ClipCard(f"{human(total)} of old clips", f"{len(found)} clips older than {days} days",
                        [("review", "Review", "image_multiple"), ("later", "Later", "")], self.theme_setting(),
                        timeout_key="later")
        card.chosen.connect(lambda key: key == "review" and QTimer.singleShot(
            0, lambda: self._review_old_clips(found, days)))

    def _review_old_clips(self, found: list[Path], days: int) -> None:
        if not found:
            self.tray.showMessage(APP_NAME, f"No clips older than {days} days.", self.tray.icon(), 4000)
            return
        from .ui.cleanup import CleanupWindow
        pal = theme.palette(self.theme_setting())
        picked = CleanupWindow(found, days, pal, pal.accents["tools"]).exec_options()
        if picked and picked["recycle"]:
            watch.recycle(picked["recycle"])
            log.info("moved %d old clips to the Recycle Bin", len(picked["recycle"]))

    def show_intro(self) -> None:
        """First run: one card that says how it works."""
        from .ui.clipcard import ClipCard
        keys = self.wheel.key_labels
        card = ClipCard(f"Drag a file, then tap {keys['convert']}",
                        f"Drop on a tile in the wheel. "
                        f"{keys['tools']} opens tools, {keys['actions']} opens actions.",
                        [("review", "Got it", ""), ("settings", "Settings", "")], self.theme_setting(),
                        lifetime=45.0, timeout_key="later")
        card.chosen.connect(lambda key: (key != "later" and self.settings.setValue("seen_intro", True),
                                         key == "settings" and self.open_settings()))
        self._intro = card

    # -- single instance -----------------------------------------------------

    def _on_connection(self) -> None:
        sock = self.server.nextPendingConnection()

        def read() -> None:
            data = bytes(sock.readAll()).decode("utf-8", "replace")
            try:
                files = [Path(f) for f in json.loads(data)]
            except json.JSONDecodeError:
                return
            if files:
                self.show_wheel_for(files)
            else:
                self.open_settings()  # started again from the Start menu

        sock.readyRead.connect(read)
        sock.disconnected.connect(sock.deleteLater)

    # -- tray ------------------------------------------------------------------

    def _on_tray(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_settings()

    def _menu(self) -> QMenu:
        m = QMenu()
        head = m.addAction(f"{APP_NAME} {__version__}: drag a file, then tap a key")
        head.setEnabled(False)
        m.addSeparator()
        m.addAction("Settings…").triggered.connect(self.open_settings)
        m.addAction("Review old clips…").triggered.connect(lambda: self._check_old_clips(force=True))
        m.addAction("Open log").triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR / "recast.log"))))
        m.addSeparator()
        m.addAction("Quit").triggered.connect(self.quit)
        return m

    def quit(self) -> None:
        self.hotkey.clear()
        self.clips.stop()
        self.hook.uninstall()
        self.tray.hide()
        self.app.quit()


def send_to_running(files: list[str]) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(300):
        return False
    sock.write(json.dumps(files).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def main(argv: list[str]) -> int:
    setup_logging()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(logo.app_icon())

    files: list[str] = []
    if argv and argv[0] == "wheel":
        files = [str(Path(a).resolve()) for a in argv[1:]]
        if send_to_running(files):
            return 0
    elif send_to_running([]):
        return 0  # already running: that instance opens its settings

    recast = RecastApp(app)
    log.info("started %s", __version__)
    if files:
        recast.show_wheel_for([Path(f) for f in files])
    elif not value(recast.settings, "seen_intro"):
        recast.show_intro()
    elif "--background" not in argv:
        recast.tray.showMessage(APP_NAME, "Running in the tray. Drag a file, then tap a key. "
                                "Click the icon for settings.", recast.tray.icon(), 5000)
    return app.exec()
