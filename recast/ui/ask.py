"""The settings window an operation opens first (crop, trim, compress to…)."""

from __future__ import annotations

from pathlib import Path

from ..engine import Operation
from .theme import Palette


def ask(operation: Operation, files: list[Path], pal: Palette) -> dict | None:
    """Show the operation's window; returns its options, {} if it has none, None when cancelled."""
    accent = pal.accents[operation.wheel]
    kind = operation.id.split(".", 1)[1]
    if kind == "crop":
        from .crop import CropWindow
        win = CropWindow(files, pal, accent)
    elif kind == "trim":
        from .trim import TrimWindow
        win = TrimWindow(files[0], pal, accent)
    elif kind == "compress_to":
        from .compress import CompressWindow
        win = CompressWindow(files, pal, accent)
    else:
        return {}
    return win.exec_options()
