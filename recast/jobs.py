"""Run operations off the GUI thread and report back through Qt signals."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from . import helper
from .engine import Cancelled, ConversionError, Context, Operation, run

log = logging.getLogger(__name__)


class Job(QObject):
    progress = Signal(float)
    finished = Signal(list)          # list[Path]
    failed = Signal(str)
    cancelled = Signal()
    accepted = Signal()              # helper jobs with a window: the user confirmed it

    def __init__(self, operation: Operation, files: list[Path], options: dict, ask: bool = False,
                 theme: str = "system"):
        super().__init__()
        self.operation = operation
        self.files = files
        self.options, self.ask, self.theme = options, ask, theme
        self.cancel_event = threading.Event()
        self.ctx = Context(options=options, on_progress=self.progress.emit, cancel_event=self.cancel_event)

    def cancel(self) -> None:
        self.cancel_event.set()

    def execute(self) -> None:
        if helper.heavy_job(self.operation, self.files):
            self._execute_in_helper()
            return
        log.info("run %s on %d file(s)", self.operation.id, len(self.files))
        try:
            outputs = run(self.operation, self.files, self.ctx)
        except Cancelled:
            self.cancelled.emit()
        except ConversionError as e:
            log.warning("%s failed: %s", self.operation.id, e)
            self.failed.emit(str(e))
        except Exception as e:  # unexpected: keep the app alive, log the trace
            log.error("%s crashed:\n%s", self.operation.id, traceback.format_exc())
            self.failed.emit(f"Something went wrong: {e}")
        else:
            log.info("done %s -> %s", self.operation.id, [str(o) for o in outputs])
            self.finished.emit(outputs)


    def _execute_in_helper(self) -> None:
        """Run in a helper process (see recast.helper) and relay what it reports."""
        log.info("run %s on %d file(s) in a helper", self.operation.id, len(self.files))
        try:
            proc = helper.HelperProcess({"do": "job", "op": self.operation.id, "files": [str(f) for f in self.files],
                                         "options": self.options, "ask": self.ask, "theme": self.theme})
        except OSError as e:
            log.error("couldn't start the helper: %s", e)
            self.failed.emit("Couldn't start the converter")
            return
        ended, cancel_sent = False, 0.0
        for msg in proc.messages():
            if msg is None:
                if self.cancel_event.is_set():
                    if not cancel_sent:
                        proc.cancel()
                        cancel_sent = time.monotonic()
                    elif time.monotonic() - cancel_sent > 3:
                        proc.kill()     # didn't stop by itself
                continue
            if "progress" in msg:
                self.progress.emit(msg["progress"])
            elif "asked" in msg:
                self.accepted.emit()
            elif "done" in msg:
                outputs = [Path(o) for o in msg["done"]]
                log.info("done %s -> %s", self.operation.id, [str(o) for o in outputs])
                self.finished.emit(outputs)
                ended = True
            elif "error" in msg:
                log.warning("%s failed: %s", self.operation.id, msg["error"])
                self.failed.emit(msg["error"])
                ended = True
            elif "cancelled" in msg:
                self.cancelled.emit()
                ended = True
        proc.close()
        if not ended:
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                log.error("the helper for %s ended without a result", self.operation.id)
                self.failed.emit("The converter stopped unexpectedly")


class _Runnable(QRunnable):
    def __init__(self, job: Job):
        super().__init__()
        self.job = job

    def run(self) -> None:
        self.job.execute()


POOL = QThreadPool()
POOL.setMaxThreadCount(2)  # FFmpeg already uses every core; two jobs at once is plenty


def start(job: Job) -> None:
    if job.operation.ui_thread:
        job.execute()
    else:
        POOL.start(_Runnable(job))
