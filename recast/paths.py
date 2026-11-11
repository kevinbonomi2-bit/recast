"""Output naming and external tool lookup."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def unique_path(path: Path) -> Path:
    """Return `path`, or `name (1).ext`, `name (2).ext`... if it already exists."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    # "archive.tar.gz" keeps its double suffix
    if path.name.lower().endswith(".tar.gz"):
        stem, suffix = path.name[:-7], ".tar.gz"
    n = 1
    while True:
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


# Settings: save every result in one folder instead of next to the original (None = next to it).
OUTPUT_DIR: Path | None = None


def out_dir(src: Path) -> Path:
    if OUTPUT_DIR is not None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        return OUTPUT_DIR
    return src.parent


def output_for(src: Path, ext: str, tag: str = "", folder: Path | None = None) -> Path:
    """Pick a free output path next to `src` (or in `folder` / the output folder) with a new extension.

    `tag` is appended to the stem, e.g. "photo-compressed.jpg".
    """
    stem = src.name[:-7] if src.name.lower().endswith(".tar.gz") else src.stem
    if tag:
        stem = f"{stem}-{tag}"
    return unique_path((folder or out_dir(src)) / f"{stem}.{ext.lstrip('.')}")


def find_tool(name: str, extra_dirs: tuple[str, ...] = ()) -> str | None:
    """Locate an executable on PATH or in well-known install folders."""
    found = shutil.which(name)
    if found:
        return found
    if getattr(sys, "frozen", False):  # installed build: tools ship in a folder next to Recast.exe
        bundled = Path(sys.executable).parent / "tools" / f"{name}.exe"
        if bundled.exists():
            return str(bundled)
    for d in extra_dirs:
        p = Path(os.path.expandvars(d)) / f"{name}.exe"
        if p.exists():
            return str(p)
    return None


def ffmpeg() -> str | None:
    return find_tool("ffmpeg")


def ffprobe() -> str | None:
    return find_tool("ffprobe")


def soffice() -> str | None:
    return find_tool("soffice", (r"%ProgramFiles%\LibreOffice\program",
                                 r"%ProgramFiles(x86)%\LibreOffice\program"))


def sevenzip() -> str | None:
    return find_tool("7z", (r"%ProgramFiles%\7-Zip", r"%ProgramFiles(x86)%\7-Zip"))
