"""PDF and e-book operations (PyMuPDF)."""

from __future__ import annotations

import statistics
from pathlib import Path

from .lazy import pymupdf

from ..paths import out_dir, output_for, unique_path
from .core import ConversionError, Context, op

PDF, EBOOK = "pdf", "ebook"


def _open(src: Path) -> pymupdf.Document:
    try:
        doc = pymupdf.open(src)
    except Exception as e:
        raise ConversionError(f"Couldn't open {src.name}: {e}") from e
    if doc.needs_pass:
        raise ConversionError(f"{src.name} is password protected")
    return doc


def _pages_folder(src: Path) -> Path:
    folder = unique_path(out_dir(src) / f"{src.stem} pages")
    folder.mkdir()
    return folder


def to_images(src: Path, target: str, ctx: Context) -> list[Path]:
    doc = _open(src)
    dpi = ctx.options.get("pdf_dpi", 300)
    n = doc.page_count
    if n == 1:
        dests = [output_for(src, target)]
    else:
        folder = _pages_folder(src)
        width = len(str(n))
        dests = [folder / f"{src.stem}-{i + 1:0{width}d}.{target}" for i in range(n)]
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        if target == "jpg":
            pix.save(dests[i], jpg_quality=92)
        else:
            pix.save(dests[i])
        ctx.progress((i + 1) / n)
    return dests if n == 1 else [dests[0].parent]


op("pdf.to.png", "PNG", "convert", {PDF, EBOOK}, target="png", hint="Every page at 300 DPI")(
    lambda src, ctx: to_images(src, "png", ctx))
op("pdf.to.jpg", "JPG", "convert", {PDF, EBOOK}, target="jpg", hint="Every page at 300 DPI")(
    lambda src, ctx: to_images(src, "jpg", ctx))


@op("pdf.to.txt", "TXT", "convert", {PDF, EBOOK}, target="txt")
def to_txt(src: Path, ctx: Context) -> list[Path]:
    doc = _open(src)
    parts = []
    for i, page in enumerate(doc):
        parts.append(page.get_text("text", sort=True).rstrip())
        ctx.progress((i + 1) / doc.page_count)
    dest = output_for(src, "txt")
    dest.write_text("\n\n\f".join(parts) + "\n", encoding="utf-8")
    return [dest]


@op("pdf.to.md", "Markdown", "convert", {PDF, EBOOK}, target="md")
def to_markdown(src: Path, ctx: Context) -> list[Path]:
    """Text with headings guessed from font size relative to body text."""
    doc = _open(src)
    pages = [page.get_text("dict", sort=True) for page in doc]
    sizes = [round(s["size"], 1) for p in pages for b in p["blocks"] if b["type"] == 0
             for l in b["lines"] for s in l["spans"] if s["text"].strip()]
    body = statistics.median(sizes) if sizes else 11
    out: list[str] = []
    for i, p in enumerate(pages):
        for b in p["blocks"]:
            if b["type"] != 0:
                continue
            spans = [s for l in b["lines"] for s in l["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = " ".join(" ".join(s["text"] for s in l["spans"]).strip() for l in b["lines"]).strip()
            size = max(s["size"] for s in spans)
            bold = all(s["flags"] & 16 for s in spans)
            if size >= body * 1.6:
                out.append(f"# {text}")
            elif size >= body * 1.3:
                out.append(f"## {text}")
            elif (size >= body * 1.1 or bold) and len(text) < 120:
                out.append(f"### {text}")
            else:
                out.append(text)
        ctx.progress((i + 1) / len(pages))
    dest = output_for(src, "md")
    dest.write_text("\n\n".join(out) + "\n", encoding="utf-8")
    return [dest]


@op("ebook.to.pdf", "PDF", "convert", EBOOK, target="pdf")
def ebook_to_pdf(src: Path, ctx: Context) -> list[Path]:
    doc = _open(src)
    pdf = pymupdf.open("pdf", doc.convert_to_pdf())
    ctx.progress(0.8)
    dest = output_for(src, "pdf")
    pdf.save(dest, garbage=3, deflate=True)
    return [dest]


# ---- tools -----------------------------------------------------------------

@op("pdf.compress", "Compress", "tools", PDF, hint="Downsample images and clean up")
def compress(src: Path, ctx: Context) -> list[Path]:
    doc = _open(src)
    level = ctx.options.get("compress_level", "balanced")
    dpi, quality = {"light": (200, 85), "balanced": (150, 72), "strong": (100, 55)}.get(level, (150, 72))
    try:
        doc.rewrite_images(dpi_threshold=dpi + 20, dpi_target=dpi, quality=quality)
    except AttributeError:
        pass  # older PyMuPDF: structural clean-up only
    ctx.progress(0.7)
    doc.scrub(metadata=False, xml_metadata=False, javascript=False, embedded_files=False,
              attached_files=False, clean_pages=True, reset_fields=False, reset_responses=False,
              thumbnails=True, redact_images=0, remove_links=False, hidden_text=False)
    dest = output_for(src, "pdf", "compressed")
    doc.save(dest, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, use_objstms=1)
    return [dest]


@op("pdf.merge", "Merge", "tools", PDF, combine=True, min_files=2, hint="Join PDFs in the order dropped")
def merge(srcs: list[Path], ctx: Context) -> list[Path]:
    out = pymupdf.open()
    for i, src in enumerate(srcs):
        out.insert_pdf(_open(src))
        ctx.progress((i + 1) / len(srcs) * 0.9)
    dest = output_for(srcs[0], "pdf", "merged")
    out.save(dest, garbage=3, deflate=True)
    return [dest]


@op("pdf.split", "Split pages", "tools", PDF, hint="One PDF per page")
def split(src: Path, ctx: Context) -> list[Path]:
    doc = _open(src)
    if doc.page_count < 2:
        raise ConversionError(f"{src.name} has only one page")
    folder = _pages_folder(src)
    width = len(str(doc.page_count))
    for i in range(doc.page_count):
        one = pymupdf.open()
        one.insert_pdf(doc, from_page=i, to_page=i)
        one.save(folder / f"{src.stem}-{i + 1:0{width}d}.pdf", garbage=3, deflate=True)
        ctx.progress((i + 1) / doc.page_count)
    return [folder]


@op("pdf.rotate", "Rotate pages", "tools", PDF, hint="Turn every page 90° clockwise")
def rotate(src: Path, ctx: Context) -> list[Path]:
    doc = _open(src)
    for page in doc:
        page.set_rotation((page.rotation + 90) % 360)
    dest = output_for(src, "pdf", "rotated")
    doc.save(dest, garbage=3, deflate=True)
    return [dest]


@op("pdf.strip", "Remove metadata", "tools", PDF, hint="Remove author, title and app tags")
def strip(src: Path, ctx: Context) -> list[Path]:
    doc = _open(src)
    doc.set_metadata({})
    doc.del_xml_metadata()
    dest = output_for(src, "pdf", "clean")
    doc.save(dest, garbage=4, deflate=True)
    return [dest]


@op("pdf.extract_images", "Extract images", "tools", PDF, hint="Save every embedded picture")
def extract_images(src: Path, ctx: Context) -> list[Path]:
    doc = _open(src)
    seen: set[int] = set()
    folder: Path | None = None
    count = 0
    for i, page in enumerate(doc):
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            data = doc.extract_image(xref)
            if not data or data.get("width", 0) < 32:
                continue
            if folder is None:
                folder = unique_path(out_dir(src) / f"{src.stem} images")
                folder.mkdir()
            count += 1
            (folder / f"{src.stem}-{count:03d}.{data['ext']}").write_bytes(data["image"])
        ctx.progress((i + 1) / doc.page_count)
    if folder is None:
        raise ConversionError(f"{src.name} has no images")
    return [folder]


@op("pdf.compress_to", "Compress to size…", "tools", PDF, asks=True, hint="Choose a target size or reduction")
def compress_to(src: Path, ctx: Context) -> list[Path]:
    """Step image resolution and quality down until the file fits (options: ratio)."""
    goal = src.stat().st_size * ctx.options.get("ratio", 0.5)
    steps = [(200, 85), (150, 75), (120, 65), (96, 55), (72, 45), (60, 35)]
    best: bytes | None = None
    for k, (dpi, quality) in enumerate(steps):
        doc = _open(src)
        try:
            doc.rewrite_images(dpi_threshold=dpi + 10, dpi_target=dpi, quality=quality)
        except AttributeError:
            pass
        data = doc.tobytes(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, use_objstms=1)
        if best is None or len(data) < len(best):
            best = data
        ctx.progress((k + 1) / len(steps))
        if len(data) <= goal:
            break
    dest = output_for(src, "pdf", "compressed")
    dest.write_bytes(best)
    return [dest]
