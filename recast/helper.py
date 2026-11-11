"""Heavy work in a short-lived helper process, so the tray app stays small.

The PDF library, the HDR codec, the office libraries and the Trim window's video player
cost 60–150 MB once loaded, and Python never gives that back. So the tray process never
loads them: it starts `recast helper <pipe>` for one request (a preview or a job), talks to
it over a named pipe in JSON, and the memory goes back to Windows when the helper exits.
Light work (images through Pillow, FFmpeg jobs, file actions) stays in the tray process,
where it starts instantly.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import threading
import uuid
from dataclasses import asdict
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path

from .formats import category_of, ext_of

log = logging.getLogger(__name__)

IN_HELPER = False       # True inside the helper process: do the work here, don't delegate again
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HEAVY_EXTS = ("jxr", "wdp", "hdp")
HEAVY_MODULES = ("recast.engine.pdf", "recast.engine.text")
HELPER_WINDOWS = ("trim",)      # tool windows that load heavy parts (the video player)


def heavy_file(path: Path) -> bool:
    return category_of(path) in ("pdf", "ebook") or ext_of(path) in HEAVY_EXTS


def heavy_job(operation, files: list[Path]) -> bool:
    if IN_HELPER or operation.ui_thread:
        return False
    kind = operation.id.split(".", 1)[1]
    module = operation.func.__module__
    if module in HEAVY_MODULES or kind in HELPER_WINDOWS or kind == "combine_pdf":
        return True
    # An image tool on an HDR screenshot needs the JPEG XR codec and numpy.
    return module == "recast.engine.images" and any(ext_of(f) in HEAVY_EXTS for f in files)


def asks_in_helper(operation) -> bool:
    return operation.asks and operation.id.split(".", 1)[1] in HELPER_WINDOWS


# ---- tray side ----------------------------------------------------------------------------

def environment() -> dict:
    """Settings the engine keeps in module globals, for the helper to mirror."""
    from . import paths
    from .engine import images, media
    return {"output_dir": str(paths.OUTPUT_DIR) if paths.OUTPUT_DIR else "", "discord_limit_mb": media.DISCORD_LIMIT_MB,
            "hdr_white": images.HDR_WHITE_NITS}


def send(conn: Connection, message: dict) -> None:
    conn.send_bytes(json.dumps(message).encode("utf-8"))


def receive(conn: Connection) -> dict:
    return json.loads(conn.recv_bytes().decode("utf-8"))


class HelperProcess:
    """One helper process and the pipe to it."""

    def __init__(self, request: dict):
        from .win.shell import launch_command
        address = rf"\\.\pipe\recast-{uuid.uuid4().hex}"
        listener = Listener(address, family="AF_PIPE")
        exe, args = launch_command("helper", address)
        self.proc = subprocess.Popen([exe, *args], creationflags=NO_WINDOW)
        # accept() has no timeout: if the helper dies before connecting, connect ourselves to free it.
        threading.Thread(target=self._unblock, args=(address,), daemon=True).start()
        self.conn = listener.accept()
        listener.close()
        if self.proc.poll() is not None:
            raise OSError("the helper process didn't start")
        send(self.conn, {**request, "env": environment()})

    def _unblock(self, address: str) -> None:
        self.proc.wait()
        try:
            Client(address, family="AF_PIPE").close()
        except OSError:
            pass    # the listener is gone: the helper connected and has finished since

    def messages(self, poll: float = 0.1):
        """Yield messages until the helper closes the pipe; yields None on each idle poll."""
        while True:
            try:
                if self.conn.poll(poll):
                    yield receive(self.conn)
                else:
                    yield None
            except (EOFError, OSError):
                return

    def cancel(self) -> None:
        try:
            send(self.conn, {"cancel": True})
        except OSError:
            pass

    def kill(self) -> None:
        """Stop the helper and anything it started (FFmpeg)."""
        subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True,
                       creationflags=NO_WINDOW)

    def close(self) -> None:
        try:
            self.conn.close()
        except OSError:
            pass


_previews: dict[tuple, dict] = {}
_preview_locks: dict[tuple, threading.Lock] = {}
_guard = threading.Lock()


def preview(path: Path) -> dict:
    """Thumbnail (PNG bytes or None) and file facts for a heavy file, from a helper. Cached per file version."""
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    with _guard:
        lock = _preview_locks.setdefault(key, threading.Lock())
    with lock:      # the thumbnail and the size estimate both ask; only the first starts a helper
        if key not in _previews:
            result: dict = {}
            try:
                helper = HelperProcess({"do": "preview", "file": str(path)})
                for msg in helper.messages(0.25):
                    if msg and "preview" in msg:
                        result = msg["preview"]
                        break
                helper.close()
            except OSError as e:
                log.warning("preview helper failed for %s: %s", path, e)
            if len(_previews) > 64:
                _previews.clear()
            _previews[key] = result
        return _previews[key]


# ---- helper side --------------------------------------------------------------------------

def _apply(env: dict) -> None:
    from . import paths
    from .engine import images, media
    paths.OUTPUT_DIR = Path(env["output_dir"]) if env.get("output_dir") else None
    media.DISCORD_LIMIT_MB = env.get("discord_limit_mb", media.DISCORD_LIMIT_MB)
    images.HDR_WHITE_NITS = env.get("hdr_white", images.HDR_WHITE_NITS)


def _do_preview(conn: Connection, request: dict) -> None:
    from PySide6.QtCore import QBuffer, QIODevice

    from . import estimate
    from .ui.thumbs import make_thumbnail
    path = Path(request["file"])
    info = asdict(estimate.inspect(path))
    info["path"] = str(path)
    png = None
    image = make_thumbnail(path)
    if image is not None and not image.isNull():
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        png = base64.b64encode(bytes(buf.data())).decode("ascii")
    send(conn, {"preview": {"info": info, "png": png}})


def _do_job(conn: Connection, request: dict) -> None:
    from .engine import REGISTRY, Cancelled, ConversionError, Context, run
    operation = REGISTRY[request["op"]]
    files = [Path(f) for f in request["files"]]
    options = dict(request.get("options") or {})
    if request.get("ask"):
        from PySide6.QtWidgets import QApplication

        from .ui import logo, theme
        from .ui.ask import ask
        app = QApplication.instance() or QApplication([])
        app.setWindowIcon(logo.app_icon())
        asked = ask(operation, files, theme.palette(request.get("theme", "system")))
        if asked is None:
            send(conn, {"cancelled": True})
            return
        options.update(asked)
        send(conn, {"asked": True})

    cancel = threading.Event()

    def listen() -> None:
        try:
            while True:
                if receive(conn).get("cancel"):
                    cancel.set()
        except (EOFError, OSError):
            cancel.set()    # the tray app went away: stop working

    threading.Thread(target=listen, daemon=True).start()
    last = [0.0]

    def progress(f: float) -> None:
        if f - last[0] >= 0.01 or f >= 1.0:
            last[0] = f
            send(conn, {"progress": f})

    try:
        outputs = run(operation, files, Context(options=options, on_progress=progress, cancel_event=cancel))
    except Cancelled:
        send(conn, {"cancelled": True})
    except ConversionError as e:
        send(conn, {"error": str(e)})
    except Exception as e:
        log.exception("%s crashed in the helper", operation.id)
        send(conn, {"error": f"Something went wrong: {e}"})
    else:
        send(conn, {"done": [str(o) for o in outputs]})


def main(address: str) -> int:
    global IN_HELPER
    IN_HELPER = True
    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Recast"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=log_dir / "recast.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s (helper): %(message)s")
    conn = Client(address, family="AF_PIPE")
    try:
        request = receive(conn)
        _apply(request.get("env") or {})
        if request.get("do") == "preview":
            _do_preview(conn, request)
        else:
            _do_job(conn, request)
    except (EOFError, OSError):
        return 1
    finally:
        conn.close()
    return 0
