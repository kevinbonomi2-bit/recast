"""Small previews of the dragged file for the wheel's hub. Safe to call off the GUI thread."""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from PySide6.QtGui import QImage

from .. import helper, paths
from ..formats import category_of, ext_of

MAX_W, MAX_H = 320, 240
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _from_pil(img) -> QImage:
    from PIL import ImageOps

    img = ImageOps.exif_transpose(img)
    img.thumbnail((MAX_W, MAX_H))
    img = img.convert("RGBA")
    return QImage(img.tobytes(), img.width, img.height, QImage.Format.Format_RGBA8888).copy()


def make_thumbnail(path: Path) -> QImage | None:
    cat = category_of(path)
    if helper.heavy_file(path) and not helper.IN_HELPER:
        # PDFs, e-books and HDR images need libraries the tray app never loads.
        png = helper.preview(path).get("png")
        return QImage.fromData(base64.b64decode(png)) if png else None
    try:
        if cat == "image":
            if ext_of(path) == "svg":
                from ..engine.images import _render_svg
                return _from_pil(_render_svg(path, 512))
            if ext_of(path) in ("jxr", "wdp", "hdp"):
                from ..engine.images import open_image
                return _from_pil(open_image(path))
            from PIL import Image
            import pillow_heif  # noqa: F401  (registered by the engine; import keeps this standalone)
            pillow_heif.register_heif_opener()
            img = Image.open(path)
            img.draft("RGB", (MAX_W * 2, MAX_H * 2))  # fast JPEG decode at reduced size
            return _from_pil(img)
        if cat in ("pdf", "ebook"):
            from ..engine.lazy import pymupdf
            doc = pymupdf.open(path)
            page = doc[0]
            zoom = min(MAX_W / page.rect.width, MAX_H / page.rect.height) * 1.5
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            return QImage.fromData(pix.tobytes("png"))
        if cat == "video":
            exe = paths.ffmpeg()
            if not exe:
                return None
            out = subprocess.run([exe, "-v", "error", "-ss", "1", "-i", str(path), "-frames:v", "1",
                                  "-vf", f"scale={MAX_W}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                                 capture_output=True, timeout=4, creationflags=NO_WINDOW)
            if not out.stdout:  # shorter than a second: take the first frame
                out = subprocess.run([exe, "-v", "error", "-i", str(path), "-frames:v", "1", "-vf",
                                      f"scale={MAX_W}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                                     capture_output=True, timeout=4, creationflags=NO_WINDOW)
            img = QImage.fromData(out.stdout)
            return None if img.isNull() else img
    except Exception:
        return None
    return None
