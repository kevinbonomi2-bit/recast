"""File actions (the Alt wheel). They work on any file or folder and run on the GUI thread."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog

from .engine.core import Context, ConversionError, op
from .paths import unique_path
from .win import shell

ANY = "any"
DROPEFFECT_COPY = b"\x01\x00\x00\x00"


def files_to_clipboard(paths: list[Path]) -> None:
    """Put files on the clipboard the way Explorer does, so Ctrl+V pastes them (Explorer, Discord, mail…)."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    mime.setData("Preferred DropEffect", QByteArray(DROPEFFECT_COPY))
    QGuiApplication.clipboard().setMimeData(mime)


@op("action.copy", "Copy", "actions", ANY, combine=True, ui_thread=True, hint="Ready to paste in Explorer")
def copy_files(srcs: list[Path], ctx: Context) -> list[Path]:
    files_to_clipboard(srcs)
    return []


@op("action.copy_path", "Copy path", "actions", ANY, combine=True, ui_thread=True,
    hint="Full paths as text, one per line")
def copy_path(srcs: list[Path], ctx: Context) -> list[Path]:
    QGuiApplication.clipboard().setText("\r\n".join(str(p) for p in srcs))
    return []


@op("action.move", "Move to…", "actions", ANY, combine=True, ui_thread=True, hint="Pick a folder")
def move_to(srcs: list[Path], ctx: Context) -> list[Path]:
    folder = QFileDialog.getExistingDirectory(None, "Move to", str(srcs[0].parent))
    if not folder:
        return []
    moved = []
    for p in srcs:
        dest = unique_path(Path(folder) / p.name)
        try:
            shutil.move(p, dest)
        except OSError as e:
            raise ConversionError(f"Couldn't move {p.name}: {e.strerror}") from e
        moved.append(dest)
    return moved


@op("action.terminal", "Terminal", "actions", ANY, combine=True, ui_thread=True,
    hint="Open the folder in Windows Terminal")
def terminal(srcs: list[Path], ctx: Context) -> list[Path]:
    p = srcs[0]
    shell.open_terminal(p if p.is_dir() else p.parent)
    return []


@op("action.reveal", "Show in folder", "actions", ANY, combine=True, ui_thread=True)
def reveal(srcs: list[Path], ctx: Context) -> list[Path]:
    shell.reveal(srcs[0])
    return []
