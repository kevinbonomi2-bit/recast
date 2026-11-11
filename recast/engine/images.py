"""Image conversions and tools (Pillow, pillow-heif, Qt for SVG)."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence
import pillow_heif

from ..formats import ext_of
from ..paths import output_for
from .core import ConversionError, Context, op

pillow_heif.register_heif_opener()

IMG = "image"

# target ext -> (Pillow format name, supports alpha, supports animation)
TARGETS = {
    "jpg": ("JPEG", False, False),
    "png": ("PNG", True, True),
    "webp": ("WEBP", True, True),
    "heic": ("HEIF", True, False),
    "avif": ("AVIF", True, True),
    "tiff": ("TIFF", True, True),
    "bmp": ("BMP", False, False),
    "gif": ("GIF", True, True),
    "ico": ("ICO", True, False),
    "pdf": ("PDF", False, True),
}


def _render_svg(path: Path, max_side: int = 2048) -> Image.Image:
    from PySide6.QtCore import QRectF, QSize
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    if QGuiApplication.instance() is None:
        QGuiApplication([])  # fonts in SVG text need a GUI application
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        raise ConversionError(f"{path.name} isn't a valid SVG file")
    size = renderer.defaultSize()
    if size.isEmpty():
        size = QSize(1024, 1024)
    scale = max_side / max(size.width(), size.height()) if max(size.width(), size.height()) < 512 else 1
    size = QSize(round(size.width() * scale), round(size.height() * scale))
    qimg = QImage(size, QImage.Format.Format_RGBA8888)
    qimg.fill(0)
    painter = QPainter(qimg)
    renderer.render(painter, QRectF(0, 0, size.width(), size.height()))
    painter.end()
    return Image.frombuffer("RGBA", (size.width(), size.height()),
                            bytes(qimg.constBits()), "raw", "RGBA", 0, 1)


# Paper white for HDR captures, in nits: how bright ordinary (SDR) content was shown. Set from Settings.
HDR_WHITE_NITS = 200
JXR = ("jxr", "wdp", "hdp")


def tonemap_scrgb(rgb, white_nits: float = 200):
    """Linear scRGB (1.0 = 80 nits, as Windows HDR captures store it) → 8-bit sRGB.

    Paper white maps to 1.0. Above a knee at 0.6, luminance rolls off smoothly (extended
    Reinhard with a matched slope) so the brightest 0.1% lands at 1.0 instead of clipping.
    Colour is scaled by luminance to keep hue; anything still above 1.0 is desaturated
    toward white rather than shifting colour.
    """
    import numpy as np
    rgb = np.nan_to_num(rgb.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    rgb = np.maximum(rgb, 0.0) * (80.0 / white_nits)
    lum = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    peak = max(1.0, float(np.percentile(lum, 99.9)))
    knee = 0.6
    out_l = lum
    if peak > 1.0:
        w = (peak - knee) / (1 - knee)
        u = np.maximum(lum - knee, 0.0) / (1 - knee)
        out_l = np.where(lum > knee, knee + (1 - knee) * u * (1 + u / (w * w)) / (1 + u), lum)
    scale = np.where(lum > 1e-6, out_l / np.maximum(lum, 1e-6), 1.0)
    rgb = rgb * scale[..., None]
    top = rgb.max(axis=-1)
    s = np.where(top > 1.0, (1.0 - out_l) / np.maximum(top - out_l, 1e-6), 1.0)
    rgb = out_l[..., None] + (rgb - out_l[..., None]) * np.clip(s, 0.0, 1.0)[..., None]
    rgb = np.clip(rgb, 0.0, 1.0)
    srgb = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
    return (srgb * 255 + 0.5).astype(np.uint8)


def jxr_decode(data: bytes):
    # Import the codec module directly: imagecodecs' lazy loader can't find it inside the packaged app.
    from imagecodecs._jpegxr import jpegxr_decode
    return jpegxr_decode(data)


def _open_jxr(path: Path) -> Image.Image:
    """JPEG XR, the format Xbox Game Bar uses for HDR screenshots."""
    import numpy as np
    try:
        data = jxr_decode(path.read_bytes())
    except Exception as e:
        raise ConversionError(f"Couldn't read {path.name}: {e}") from e
    if data.ndim == 2:
        data = data[..., None].repeat(3, axis=-1)
    alpha = None
    if data.shape[-1] == 4:
        alpha = data[..., 3]
        data = data[..., :3]
    if data.dtype.kind == "f":  # HDR: linear scRGB
        rgb = tonemap_scrgb(data, HDR_WHITE_NITS)
        if alpha is not None:
            alpha = (np.clip(alpha.astype(np.float32), 0, 1) * 255 + 0.5).astype(np.uint8)
    else:  # SDR JPEG XR
        shift = 8 if data.dtype.itemsize > 1 else 0
        rgb = (data >> shift).astype(np.uint8)
        if alpha is not None:
            alpha = (alpha >> shift).astype(np.uint8)
    if alpha is not None and alpha.min() < 255:
        return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
    return Image.fromarray(np.ascontiguousarray(rgb), "RGB")


def open_image(path: Path) -> Image.Image:
    if ext_of(path) == "svg":
        return _render_svg(path)
    if ext_of(path) in JXR:
        return _open_jxr(path)
    try:
        img = Image.open(path)
        img.load()
    except Exception as e:  # Pillow raises many types for bad input
        raise ConversionError(f"Couldn't read {path.name}: {e}") from e
    return img


def _flatten(img: Image.Image, background=(255, 255, 255)) -> Image.Image:
    """Drop alpha onto a solid background for formats without transparency."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, background)
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    return img.convert("RGB")


def _prepare(img: Image.Image, target: str) -> Image.Image:
    _, alpha, _ = TARGETS[target]
    img = ImageOps.exif_transpose(img) if hasattr(img, "getexif") else img
    if not alpha:
        return _flatten(img)
    if img.mode not in ("RGB", "RGBA", "L", "LA", "P", "I;16"):
        return img.convert("RGBA")
    if target in ("heic", "avif", "webp") and img.mode not in ("RGB", "RGBA"):
        return img.convert("RGBA" if "A" in img.mode or "transparency" in img.info else "RGB")
    return img


def _save_kwargs(target: str, src: Image.Image, quality: int) -> dict:
    kw: dict = {}
    if target in ("jpg", "webp", "heic", "avif"):
        kw["quality"] = quality
    if target == "jpg":
        kw.update(optimize=True, progressive=True, subsampling="4:2:0" if quality < 90 else "4:4:4")
    if target == "png":
        kw["optimize"] = True
    if target == "tiff":
        kw["compression"] = "tiff_lzw"
    if target == "webp":
        kw["method"] = 5
    if target == "ico":
        kw["sizes"] = [s for s in [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
                       if s[0] <= max(src.size)] or [(16, 16)]
    if target == "pdf":
        kw["resolution"] = 72.0
    icc = src.info.get("icc_profile")
    if icc and target in ("jpg", "png", "webp", "tiff", "heic", "avif", "pdf"):
        kw["icc_profile"] = icc
    return kw


def save_image(img: Image.Image, dest: Path, target: str, quality: int = 90,
               keep_exif: bool = True) -> Path:
    fmt, _, animated = TARGETS[target]
    frames = getattr(img, "n_frames", 1)
    if animated and frames > 1 and target != "pdf":
        seq = [_prepare(f.copy(), target) for f in ImageSequence.Iterator(img)]
        kw = _save_kwargs(target, img, quality)
        kw.update(save_all=True, append_images=seq[1:], loop=img.info.get("loop", 0),
                  duration=img.info.get("duration", 100))
        seq[0].save(dest, fmt, **kw)
        return dest
    out = _prepare(img, target)
    kw = _save_kwargs(target, img, quality)
    if keep_exif and target in ("jpg", "webp", "heic", "avif", "png", "tiff"):
        exif = img.getexif()
        if exif:
            exif[0x0112] = 1  # orientation is baked in by exif_transpose
            kw["exif"] = exif.tobytes()
    out.save(dest, fmt, **kw)
    return dest


def convert_image(src: Path, target: str, ctx: Context) -> list[Path]:
    img = open_image(src)
    ctx.progress(0.4)
    dest = output_for(src, target)
    save_image(img, dest, target, quality=ctx.options.get("image_quality", 90),
               keep_exif=ctx.options.get("keep_exif", True))
    return [dest]


for _t, _label in [("jpg", "JPG"), ("png", "PNG"), ("webp", "WEBP"), ("heic", "HEIC"),
                   ("avif", "AVIF"), ("tiff", "TIFF"), ("bmp", "BMP"), ("gif", "GIF"),
                   ("ico", "ICO"), ("pdf", "PDF")]:
    op(f"image.to.{_t}", _label, "convert", IMG, target=_t,
       hint=f"Convert to {_label}")(lambda src, ctx, t=_t: convert_image(src, t, ctx))


# ---- tools -----------------------------------------------------------------

COMPRESS_LEVELS = {"light": 85, "balanced": 72, "strong": 55}


@op("image.compress", "Compress", "tools", IMG, hint="Smaller file, same format")
def compress(src: Path, ctx: Context) -> list[Path]:
    level = ctx.options.get("compress_level", "balanced")
    quality = COMPRESS_LEVELS.get(level, 72)
    max_side = ctx.options.get("compress_max_side", 0)
    target = {"jpeg": "jpg", "jfif": "jpg", "tif": "tiff", "heif": "heic"}.get(ext_of(src), ext_of(src))
    if target not in TARGETS or target in ("pdf", "ico", "svg"):
        target = "jpg"
    img = open_image(src)
    img = ImageOps.exif_transpose(img)
    if max_side and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    ctx.progress(0.3)
    dest = output_for(src, target, "compressed")
    if target == "png":
        # Lossy palette reduction, like pngquant; keeps alpha.
        q = img.convert("RGBA").quantize(colors=256 if level != "strong" else 128,
                                         method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG)
        q.save(dest, "PNG", optimize=True)
    elif target in ("bmp", "gif", "tiff"):
        save_image(img, dest, target)
    else:
        save_image(img, dest, target, quality=quality, keep_exif=False)
    return [dest]


@op("image.strip", "Remove metadata", "tools", IMG, hint="Remove camera, location and author tags")
def strip_metadata(src: Path, ctx: Context) -> list[Path]:
    img = ImageOps.exif_transpose(open_image(src))
    target = {"jpeg": "jpg", "jfif": "jpg", "tif": "tiff", "heif": "heic", "svg": "png", "jxr": "png", "wdp": "png", "hdp": "png"}.get(ext_of(src), ext_of(src))
    # Rebuilding from raw pixels leaves every metadata chunk behind.
    clean = Image.frombytes(img.mode, img.size, img.tobytes())
    if img.mode == "P":
        clean.putpalette(img.getpalette())
        if "transparency" in img.info:
            clean.info["transparency"] = img.info["transparency"]
    dest = output_for(src, target, "clean")
    fmt = TARGETS.get(target, ("PNG",))[0]
    kw = {"quality": 95} if target in ("jpg", "webp", "heic", "avif") else {}
    icc = img.info.get("icc_profile")
    if icc:
        kw["icc_profile"] = icc  # colour profile isn't personal data
    _prepare(clean, target if target in TARGETS else "png").save(dest, fmt, **kw)
    return [dest]


def _transform(src: Path, method, tag: str) -> list[Path]:
    img = ImageOps.exif_transpose(open_image(src))
    target = {"jpeg": "jpg", "jfif": "jpg", "tif": "tiff", "heif": "heic", "svg": "png", "jxr": "png", "wdp": "png", "hdp": "png"}.get(ext_of(src), ext_of(src))
    dest = output_for(src, target, tag)
    save_image(method(img), dest, target, quality=95)
    return [dest]


op("image.rotate_right", "Rotate right", "tools", IMG)(
    lambda src, ctx: _transform(src, lambda i: i.transpose(Image.Transpose.ROTATE_270), "rotated"))
op("image.rotate_left", "Rotate left", "tools", IMG)(
    lambda src, ctx: _transform(src, lambda i: i.transpose(Image.Transpose.ROTATE_90), "rotated"))
op("image.flip", "Flip", "tools", IMG, hint="Mirror horizontally")(
    lambda src, ctx: _transform(src, ImageOps.mirror, "flipped"))
op("image.half", "Resize 50%", "tools", IMG)(
    lambda src, ctx: _transform(src, lambda i: i.resize((max(1, i.width // 2), max(1, i.height // 2)),
                                                         Image.Resampling.LANCZOS), "half"))


def _same_format(src: Path) -> str:
    t = {"jpeg": "jpg", "jfif": "jpg", "tif": "tiff", "heif": "heic", "svg": "png", "jxr": "png", "wdp": "png", "hdp": "png"}.get(ext_of(src), ext_of(src))
    return t if t in TARGETS and t not in ("pdf", "ico") else "png"


@op("image.crop", "Crop…", "tools", IMG, asks=True, hint="Pick the framing; a selection shares it")
def crop(src: Path, ctx: Context) -> list[Path]:
    """options["crop"] = (x0, y0, x1, y1) as fractions of the upright image."""
    x0, y0, x1, y1 = ctx.options["crop"]
    img = ImageOps.exif_transpose(open_image(src))
    w, h = img.size
    aspect = ctx.options.get("aspect")  # selections keep the ratio on every image
    box = [round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)]
    if aspect:
        bw, bh = box[2] - box[0], box[3] - box[1]
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if bw / max(1, bh) > aspect:
            bw = bh * aspect
        else:
            bh = bw / aspect
        box = [round(cx - bw / 2), round(cy - bh / 2), round(cx + bw / 2), round(cy + bh / 2)]
    box = [max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])]
    if box[2] - box[0] < 1 or box[3] - box[1] < 1:
        raise ConversionError(f"The crop is outside {src.name}")
    size = ctx.options.get("size")  # exact output pixels, from the window's W × H fields
    out = img.crop(box)
    if size and tuple(size) != out.size:
        out = out.resize(tuple(size), Image.Resampling.LANCZOS)
    target = _same_format(src)
    dest = output_for(src, target, "cropped")
    save_image(out, dest, target, quality=95, keep_exif=False)
    return [dest]


def encode(img: Image.Image, target: str, quality: int) -> bytes:
    buf = io.BytesIO()
    fmt = TARGETS[target][0]
    kw = _save_kwargs(target, img, quality)
    _prepare(img, target).save(buf, fmt, **kw)
    return buf.getvalue()


def compress_to_bytes(img: Image.Image, target: str, goal: int, ctx: Context) -> tuple[bytes, str]:
    """Best encoding that fits in `goal` bytes: search quality, then shrink the image if needed."""
    img = ImageOps.exif_transpose(img)
    if target not in ("jpg", "webp", "avif", "heic", "png"):
        target = "jpg"
    best: bytes | None = None
    for round_ in range(6):
        ctx.check()
        if target == "png":
            for colors in (256, 128, 64, 32):
                data = io.BytesIO()
                img.convert("RGBA").quantize(colors=colors, method=Image.Quantize.FASTOCTREE).save(
                    data, "PNG", optimize=True)
                b = data.getvalue()
                if best is None or len(b) < len(best):
                    best = b
                if len(b) <= goal:
                    return b, target
        else:
            lo, hi, fit = 20, 92, None
            while lo <= hi:
                q = (lo + hi) // 2
                b = encode(img, target, q)
                if len(b) <= goal:
                    fit, lo = b, q + 1
                else:
                    hi = q - 1
                if best is None or len(b) < len(best):
                    best = b
            if fit is not None:
                return fit, target
        # Even the lowest setting is too big: scale down and try again.
        ratio = goal / max(1, len(best))
        factor = max(0.35, min(0.9, ratio ** 0.5 * 0.95))
        img = img.resize((max(1, round(img.width * factor)), max(1, round(img.height * factor))),
                         Image.Resampling.LANCZOS)
        ctx.progress(0.2 + 0.1 * round_)
    return best, target


@op("image.compress_to", "Compress to size…", "tools", IMG, asks=True, hint="Choose a target size or reduction")
def compress_to(src: Path, ctx: Context) -> list[Path]:
    """options: ratio (target size ÷ original size), max_side (0 = keep)."""
    img = open_image(src)
    max_side = ctx.options.get("max_side", 0)
    if max_side and max(img.size) > max_side:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    goal = max(2048, int(src.stat().st_size * ctx.options.get("ratio", 0.5)))
    data, target = compress_to_bytes(img, _same_format(src), goal, ctx)
    dest = output_for(src, target, "compressed")
    dest.write_bytes(data)
    return [dest]


def image_pdf_page_bytes(img: Image.Image) -> bytes:
    """Encode an image for embedding in a PDF page (JPEG unless it has alpha)."""
    img = ImageOps.exif_transpose(img)
    buf = io.BytesIO()
    if img.mode in ("RGBA", "LA") or "transparency" in img.info:
        img.convert("RGBA").save(buf, "PNG")
    else:
        img.convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue()


@op("image.combine_pdf", "Combine into PDF", "tools", IMG, combine=True, min_files=2,
    hint="One PDF, one page per image")
def combine_pdf(srcs: list[Path], ctx: Context) -> list[Path]:
    from .lazy import pymupdf

    doc = pymupdf.open()
    for i, src in enumerate(srcs):
        img = open_image(src)
        w, h = ImageOps.exif_transpose(img).size
        page = doc.new_page(width=w * 72 / 96, height=h * 72 / 96)
        page.insert_image(page.rect, stream=image_pdf_page_bytes(img))
        ctx.progress((i + 1) / len(srcs) * 0.95)
    dest = output_for(srcs[0], "pdf", "combined")
    doc.save(dest, garbage=3, deflate=True)
    return [dest]
