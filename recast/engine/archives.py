"""ZIP / TAR / GZIP, plus 7z and RAR when 7-Zip is installed."""

from __future__ import annotations

import gzip
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from .. import paths
from ..formats import ext_of
from ..paths import out_dir, output_for, unique_path
from .core import ConversionError, Context, op

ARCHIVE = "archive"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path, ctx: Context) -> None:
    root = dest.resolve()
    members = zf.infolist()
    for i, m in enumerate(members):
        target = (dest / m.filename).resolve()
        if not target.is_relative_to(root):
            raise ConversionError(f"Unsafe path in archive: {m.filename}")
        zf.extract(m, dest)
        ctx.progress((i + 1) / max(1, len(members)))


def extract_to(src: Path, dest: Path, ctx: Context) -> None:
    ext = ext_of(src)
    try:
        if ext == "zip":
            with zipfile.ZipFile(src) as zf:
                _safe_extract_zip(zf, dest, ctx)
        elif ext in ("tar", "tgz", "bz2", "xz") or (ext == "gz" and tarfile.is_tarfile(src)):
            with tarfile.open(src) as tf:
                tf.extractall(dest, filter="data")
        elif ext == "gz":
            with gzip.open(src, "rb") as f, open(dest / src.stem, "wb") as out:
                shutil.copyfileobj(f, out)
        elif ext in ("7z", "rar"):
            exe = paths.sevenzip()
            if not exe:
                raise ConversionError(f"Opening .{ext} files needs 7-Zip: winget install 7zip.7zip")
            r = subprocess.run([exe, "x", "-y", f"-o{dest}", str(src)], capture_output=True, text=True,
                               creationflags=NO_WINDOW)
            if r.returncode != 0:
                raise ConversionError(f"7-Zip couldn't extract {src.name}")
        else:
            raise ConversionError(f"Unsupported archive: {src.name}")
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as e:
        raise ConversionError(f"Couldn't read {src.name}: {e}") from e


def _items(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*"))


def pack(sources: list[Path], dest: Path, kind: str, ctx: Context, base: Path | None = None) -> Path:
    """Pack files/folders into dest. Paths are stored relative to `base` (or each item's parent)."""
    entries: list[tuple[Path, str]] = []
    for s in sources:
        root = base or s.parent
        entries.append((s, s.relative_to(root).as_posix()))
        if s.is_dir():
            entries += [(p, p.relative_to(root).as_posix()) for p in _items(s)]
    try:
        if kind == "zip":
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for i, (p, name) in enumerate(entries):
                    zf.write(p, name)
                    ctx.progress((i + 1) / len(entries))
        else:
            mode = {"tar": "w", "tgz": "w:gz"}[kind]
            with tarfile.open(dest, mode) as tf:
                for i, (p, name) in enumerate(entries):
                    tf.add(p, name, recursive=False)
                    ctx.progress((i + 1) / len(entries))
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return dest


def repack(src: Path, kind: str, ctx: Context) -> list[Path]:
    ext = {"zip": "zip", "tar": "tar", "tgz": "tar.gz"}[kind]
    with tempfile.TemporaryDirectory(prefix="recast-") as tmp:
        t = Path(tmp)
        extract_to(src, t, ctx.sub(0, 0.5))
        dest = output_for(src, ext)
        pack(_top_level(t), dest, kind, ctx.sub(0.5, 1), base=t)
    return [dest]


def _top_level(folder: Path) -> list[Path]:
    return sorted(folder.iterdir())


for _k, _label in [("zip", "ZIP"), ("tar", "TAR"), ("tgz", "TAR.GZ")]:
    op(f"archive.to.{_k}", _label, "convert", ARCHIVE, target=_k)(
        lambda src, ctx, k=_k: repack(src, k, ctx))


@op("archive.extract", "Extract", "tools", ARCHIVE, hint="Unpack into a folder next to it")
def extract(src: Path, ctx: Context) -> list[Path]:
    stem = src.name[:-7] if src.name.lower().endswith(".tar.gz") else src.stem
    with tempfile.TemporaryDirectory(prefix="recast-", dir=out_dir(src)) as tmp:
        t = Path(tmp)
        extract_to(src, t, ctx)
        top = _top_level(t)
        # A single top-level folder becomes the result itself, no "x/x/" nesting.
        if len(top) == 1 and top[0].is_dir():
            dest = unique_path(out_dir(src) / top[0].name)
            shutil.move(top[0], dest)
        elif len(top) == 1:
            dest = unique_path(out_dir(src) / top[0].name)
            shutil.move(top[0], dest)
        else:
            dest = unique_path(out_dir(src) / stem)
            dest.mkdir()
            for p in top:
                shutil.move(p, dest / p.name)
    return [dest]


@op("any.zip", "ZIP", "actions", "any", combine=True, hint="Pack everything into one ZIP")
def zip_selection(srcs: list[Path], ctx: Context) -> list[Path]:
    name = srcs[0].name if len(srcs) == 1 else (srcs[0].parent.name or "Archive")
    dest = unique_path(out_dir(srcs[0]) / f"{Path(name).stem if srcs[0].is_file() and len(srcs) == 1 else name}.zip")
    return [pack(srcs, dest, "zip", ctx)]
