"""What each wheel slice will produce: result name and a rough output size.

Estimates come from headers only (image dimensions, ffprobe, PDF page count) so they're
ready within a fraction of a second; they're shown with "≈" and never block a conversion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .engine.core import Operation
from .formats import category_of, ext_of, norm_ext

NOUNS = {"image": "images", "audio": "audio files", "video": "videos", "pdf": "PDFs", "ebook": "e-books",
         "document": "documents", "slides": "presentations", "sheet": "spreadsheets", "subtitle": "subtitle files",
         "archive": "archives", "folder": "folders"}
DISPLAY_EXT = {"tgz": "tar.gz"}


def human(n: float) -> str:
    for unit, step in (("B", 1), ("KB", 1e3), ("MB", 1e6), ("GB", 1e9)):
        if n < step * 1000 or unit == "GB":
            v = n / step
            return f"{v:.0f} {unit}" if unit in ("B", "KB") or v >= 100 else f"{v:.1f} {unit}"
    return f"{n:.0f} B"


@dataclass
class Info:
    path: Path
    size: int = 0
    cat: str = "other"
    w: int = 0
    h: int = 0
    frames: int = 1
    duration: float = 0.0
    fps: float = 0.0
    vcodec: str | None = None
    acodec: str | None = None
    abitrate: float = 0.0
    sample_rate: int = 44100
    channels: int = 2
    pages: int = 0
    extra: dict = field(default_factory=dict)


def inspect(path: Path) -> Info:
    from . import helper
    if helper.heavy_file(path) and not helper.IN_HELPER:
        facts = helper.preview(path).get("info")   # read in a helper process: see recast/helper.py
        if facts:
            return Info(**{**facts, "path": path})
    info = Info(path, cat=category_of(path))
    try:
        info.size = path.stat().st_size if path.is_file() else 0
    except OSError:
        pass
    try:
        if info.cat == "image" and ext_of(path) in ("jxr", "wdp", "hdp"):
            from .engine.images import jxr_decode
            shape = jxr_decode(path.read_bytes()).shape
            info.h, info.w = shape[0], shape[1]
        elif info.cat == "image" and ext_of(path) != "svg":
            from PIL import Image
            with Image.open(path) as im:
                info.w, info.h = im.size
                info.frames = getattr(im, "n_frames", 1)
                orient = im.getexif().get(0x0112, 1) if hasattr(im, "getexif") else 1
                if orient in (5, 6, 7, 8):
                    info.w, info.h = info.h, info.w
        elif info.cat in ("audio", "video"):
            from .engine.media import codecs, duration, probe
            data = probe(path)
            info.duration = duration(data)
            info.vcodec, info.acodec = codecs(data)
            for s in data.get("streams", []):
                if s.get("codec_type") == "video" and not info.w:
                    info.w, info.h = int(s.get("width", 0)), int(s.get("height", 0))
                    num, _, den = str(s.get("avg_frame_rate", "0/1")).partition("/")
                    info.fps = float(num) / float(den or 1) if float(den or 1) else 0
                elif s.get("codec_type") == "audio" and not info.abitrate:
                    info.abitrate = float(s.get("bit_rate", 0) or 0)
                    info.sample_rate = int(s.get("sample_rate", 44100) or 44100)
                    info.channels = int(s.get("channels", 2) or 2)
        elif info.cat in ("pdf", "ebook"):
            from .engine.lazy import pymupdf
            info.pages = pymupdf.open(path).page_count
    except Exception:
        pass
    return info


@dataclass
class Preview:
    stem: str          # pill: name before the coloured extension
    ext: str           # pill: ".webp" (coloured when it's a conversion)
    meta: str = ""     # pill: "≈ 410 KB · 68% smaller"
    sub: str = ""      # tile: second line, "≈ 410 KB"
    chip: str = ""     # hub chip text while hovering a conversion


# ---- size models -------------------------------------------------------------

# bytes per pixel for a typical photo; scaled by how compressible the source looks
IMG_BPP = {"jpg": 0.30, "webp": 0.17, "heic": 0.13, "avif": 0.10, "png": 1.6, "tiff": 2.4, "gif": 0.45}
SRC_BPP = {"jpg": 0.30, "webp": 0.17, "heic": 0.13, "avif": 0.10, "png": 1.6, "tiff": 2.4, "bmp": 3.0, "gif": 0.45}
AUDIO_KBPS = {"mp3": 190, "m4a": 192, "ogg": 160, "opus": 128, "wma": 192}
VIDEO_BPP = {"mp4": 0.10, "mov": 0.10, "mkv": 0.10, "webm": 0.07, "avi": 0.18, "wmv": 0.20}


def _complexity(i: Info) -> float:
    px = i.w * i.h * max(1, i.frames)
    typical = SRC_BPP.get(norm_ext(i.path))
    if not px or not typical or not i.size:
        return 1.0
    return max(0.02, min(3.0, (i.size / px) / typical))


def image_size(i: Info, target: str, quality_scale: float = 1.0) -> float | None:
    px = i.w * i.h
    if not px:
        return None
    if target == "bmp":
        return px * 3 + 54
    bpp = IMG_BPP.get(target)
    if bpp is None:
        return None
    return px * bpp * _complexity(i) * quality_scale


def pcm_bytes(i: Info, dur: float | None = None) -> float:
    return (dur or i.duration) * i.sample_rate * i.channels * 2


def audio_size(i: Info, target: str) -> float | None:
    if not i.duration:
        return None
    if target in ("wav", "aiff"):
        return pcm_bytes(i)
    if target == "flac":
        return pcm_bytes(i) * 0.58
    kbps = AUDIO_KBPS.get(target)
    return i.duration * kbps * 125 if kbps else None


def video_size(i: Info, target: str) -> tuple[float | None, str]:
    from .engine.media import REMUX_OK
    ok = REMUX_OK.get(target)
    if ok and i.vcodec in ok[0] and (i.acodec is None or i.acodec in ok[1]):
        return float(i.size), "no re-encode"
    if not (i.duration and i.w and i.h):
        return None, "re-encode"
    fps = i.fps or 30
    video = i.w * i.h * fps * i.duration * VIDEO_BPP.get(target, 0.12) / 8
    return video + i.duration * 192 * 125, "re-encode"


def gif_size(i: Info) -> float | None:
    if not (i.duration and i.w and i.h):
        return None
    w = min(640, i.w)
    h = i.h * w / i.w
    return w * h * 15 * i.duration * 0.12


# ---- per-operation previews ----------------------------------------------------

def _smaller(new: float | None, old: int) -> str:
    if not new or not old:
        return ""
    if new < old * 0.95:
        return f"{(1 - new / old) * 100:.0f}% smaller"
    if new > old * 1.05:
        return f"{new / old:.1f}× larger" if new / old >= 1.5 else f"{(new / old - 1) * 100:.0f}% larger"
    return "about the same"


def _sized(total: float | None, old: int, note: str = "") -> tuple[str, str]:
    """(tile line, pill meta) for an estimated output size."""
    if total is None:
        return note, note
    sub = f"≈ {human(total)}"
    meta = " · ".join(x for x in (sub, note or _smaller(total, old)) if x)
    return sub, meta


def _single_name(op: Operation, f: Path) -> tuple[str, str]:
    stem = f.name[:-7] if f.name.lower().endswith(".tar.gz") else f.stem
    ext = "." + ext_of(f)
    oid = op.id
    if op.wheel == "convert":
        return stem, "." + DISPLAY_EXT.get(op.target, op.target)
    tags = {"compress": "compressed", "strip": "clean", "rotate_right": "rotated", "rotate_left": "rotated",
            "rotate": "rotated", "flip": "flipped", "half": "half", "normalize": "normalized", "mute": "muted"}
    kind = oid.split(".", 1)[1]
    if oid == "video.compress":
        return f"{stem}-compressed", ".mp4"
    if oid == "audio.compress":
        return f"{stem}-compressed", ".mp3"
    if oid in ("video.extract_audio",):
        return stem, ".m4a"
    if oid == "video.make_gif":
        return stem, ".gif"
    if oid == "pdf.split":
        return f"{stem} pages", ""
    if oid == "pdf.extract_images":
        return f"{stem} images", ""
    if oid == "archive.extract":
        return stem, ""
    if kind in tags:
        return f"{stem}-{tags[kind]}", ext
    return stem, ext


ACTION_TEXT = {
    "action.copy": ("Copy {what}", "Ready to paste in Explorer"),
    "action.copy_path": ("Copy the full path", "{path}"),
    "action.move": ("Move {what}", "Pick a folder"),
    "action.terminal": ("Open Terminal here", "{folder}"),
    "action.reveal": ("Show in Explorer", "{folder}"),
}


def preview(op: Operation, infos: list[Info]) -> Preview:
    files = [i.path for i in infos]
    first = infos[0]
    n = len(infos)
    total_src = sum(i.size for i in infos)
    what = files[0].name if n == 1 else f"{n} items"

    if op.wheel == "actions" and op.id in ACTION_TEXT:
        title, meta = ACTION_TEXT[op.id]
        path = str(files[0]) if n == 1 else f"{n} paths, one per line"
        folder = files[0].name if files[0].is_dir() else files[0].parent.name or str(files[0].parent)
        return Preview(title.format(what=what), "", meta.format(path=path, folder=folder))
    if op.id == "any.zip":
        name = files[0].stem if n == 1 and files[0].is_file() else (files[0].name if n == 1 else files[0].parent.name)
        return Preview(name, ".zip", f"≈ {human(total_src * 0.9)}" if total_src else "")

    if op.combine:
        stem = files[0].stem
        if op.id == "pdf.merge":
            pages = sum(i.pages for i in infos)
            return Preview(f"{stem}-merged", ".pdf", f"{pages} pages from {n} PDFs" if pages else f"{n} PDFs joined")
        if op.id == "image.combine_pdf":
            return Preview(f"{stem}-combined", ".pdf", f"{n} pages, one per image")

    if n == 1:
        stem, ext = _single_name(op, files[0])
    else:
        stem = f"{n} {NOUNS.get(first.cat, 'files')} → "
        ext = "." + DISPLAY_EXT.get(op.target, op.target) if op.wheel == "convert" else ""
        if op.wheel != "convert":
            stem = f"{op.label} {n} {NOUNS.get(first.cat, 'files')}"
    p = Preview(stem, ext, chip=(op.target.upper() if op.wheel == "convert" else ""))
    if op.wheel == "convert" and op.label and op.label.upper() != op.target.upper():
        p.chip = op.target.upper() if len(op.target) <= 4 else op.label.upper()[:4]

    t, oid = op.target, op.id
    if oid == "video.discord":
        from .engine.media import DISCORD_LIMIT_MB, limit_text
        limit = DISCORD_LIMIT_MB * 1_000_000
        cap = limit_text(DISCORD_LIMIT_MB)
        if n == 1:
            p.stem, p.ext = files[0].stem, ".mp4"
        if total_src <= limit * 0.98:
            p.sub, p.meta = "fits", f"Already under {cap} · copied, paste with Ctrl+V"
        else:
            p.sub = f"≤ {cap}"
            p.meta = f"Shrunk under {cap}, then copied · paste with Ctrl+V"
        return p
    if op.asks:
        kind = oid.split(".", 1)[1]
        p.meta = {"crop": "Drop to pick the framing", "trim": "Drop to pick the start and end",
                  "compress_to": "Drop to choose a target size"}.get(kind, "Drop to choose settings")
        if n == 1:
            p.stem, p.ext = files[0].stem, "." + ext_of(files[0])
        return p
    est: float | None = None
    note = ""
    sample = infos[:40]
    scale = n / len(sample)

    def total(fn) -> float | None:
        vals = [fn(i) for i in sample]
        if any(v is None for v in vals):
            return None
        return sum(vals) * scale

    if oid.startswith("image.to."):
        if t == "ico":
            side = max(first.w, first.h)
            p.sub = p.meta = f"{min(256, side)} px" if side else "16–256 px"
            return p
        if t == "pdf":
            p.sub = p.meta = "1 page" if n == 1 else f"{n} PDFs"
            return p
        est = total(lambda i: image_size(i, t))
        if t == "gif":
            note = "256 colours"
    elif oid == "gif.to.mp4":
        est, note = None, "video"
    elif oid == "image.compress":
        est = total(lambda i: (image_size(i, norm_ext(i.path), 0.55) if norm_ext(i.path) != "png"
                               else i.size * 0.45))
    elif oid in ("image.rotate_right", "image.rotate_left") and n == 1 and first.w:
        p.sub = p.meta = f"{first.h} × {first.w}"
        return p
    elif oid == "image.half" and n == 1 and first.w:
        p.sub = p.meta = f"{first.w // 2} × {first.h // 2}"
        return p
    elif oid in ("image.strip", "pdf.strip"):
        p.meta = "Camera, GPS and author tags removed" if oid == "image.strip" else "Author and app tags removed"
        return p
    elif oid == "image.flip":
        p.meta = "Mirrored left to right"
        return p
    elif oid.startswith("audio.to.") or (oid.startswith("video.to.") and t in AUDIO_KBPS | {"wav": 0}):
        est = total(lambda i: audio_size(i, t))
        if oid.startswith("video.to."):
            note = "audio only"
    elif oid.startswith("video.to.") and t != "gif":
        sizes = [video_size(i, t) for i in sample]
        note = sizes[0][1]
        est = None if any(s[0] is None for s in sizes) else sum(s[0] for s in sizes) * scale
    elif oid in ("video.to.gif", "video.make_gif"):
        est, note = total(gif_size), "640 px, 15 fps"
    elif oid == "audio.compress":
        est, note = total(lambda i: i.duration * 128 * 125 if i.duration else None), "128 kbps MP3"
    elif oid == "audio.normalize":
        p.meta = "Even loudness (EBU R128)"
        return p
    elif oid == "video.compress":
        def comp(i: Info) -> float | None:
            if not (i.duration and i.w):
                return None
            h = min(i.h, 1080)
            w = i.w * h / i.h if i.h else i.w
            return w * h * (i.fps or 30) * i.duration * 0.045 / 8 + i.duration * 128 * 125
        est = total(comp)
    elif oid == "video.mute":
        est = total(lambda i: max(0.0, i.size - i.abitrate / 8 * i.duration) if i.abitrate else None)
        note = "video kept as is"
    elif oid == "video.extract_audio":
        est = total(lambda i: i.abitrate / 8 * i.duration if i.abitrate else audio_size(i, "m4a"))
        note = "original quality"
    elif oid in ("pdf.to.png", "pdf.to.jpg"):
        pages = sum(i.pages for i in infos)
        p.sub = f"{pages} image{'s' if pages != 1 else ''}" if pages else "every page"
        p.meta = f"{p.sub} · 300 DPI"
        return p
    elif oid in ("pdf.to.txt", "pdf.to.md"):
        p.sub, p.meta = "text", "Text only" if t == "txt" else "Text with headings"
        return p
    elif oid == "pdf.split":
        p.sub = p.meta = f"{first.pages} PDFs" if first.pages else "one PDF per page"
        return p
    elif oid == "pdf.rotate":
        p.meta = "Every page, 90° clockwise"
        return p
    elif oid == "pdf.compress":
        p.meta = "Images downsampled, file cleaned up"
        return p
    elif oid == "archive.extract":
        p.meta = "Into a folder next to it"
        return p
    else:
        return p

    p.sub, p.meta = _sized(est, total_src, note)
    if not p.sub and note:
        p.sub = note
    return p


def idle_preview(infos: list[Info]) -> Preview:
    """Pill text before anything is hovered."""
    if not infos:
        return Preview("", "", "")
    first = infos[0]
    total = sum(i.size for i in infos)
    if len(infos) == 1:
        f = first.path
        stem = f.name[:-7] if f.name.lower().endswith(".tar.gz") else f.stem
        ext = f.name[len(stem):]
        bits = [human(total)] if total else []
        if first.w and first.cat == "image":
            bits.append(f"{first.w} × {first.h}")
        elif first.duration:
            m, s = divmod(round(first.duration), 60)
            bits.append(f"{m}:{s:02d}")
        elif first.pages:
            bits.append(f"{first.pages} page{'s' if first.pages != 1 else ''}")
        return Preview(stem, ext, " · ".join(bits))
    cats = {i.cat for i in infos}
    noun = NOUNS.get(cats.pop(), "items") if len(cats) == 1 else "items"
    return Preview(f"{len(infos)} {noun}", "", human(total) if total else "")


def previews(ops: list[Operation], infos: list[Info]) -> dict[str, Preview]:
    out = {}
    for o in ops:
        try:
            out[o.id] = preview(o, infos)
        except Exception:
            out[o.id] = Preview(o.label, "")
    return out
