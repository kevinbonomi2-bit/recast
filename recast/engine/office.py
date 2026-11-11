"""Office formats through LibreOffice (optional: only offered when it's installed)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import paths
from ..paths import output_for
from .core import ConversionError, Context, op
from .pdf import to_images

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
OFFICE = {"document", "slides", "sheet"}


def available() -> bool:
    return paths.soffice() is not None


def soffice_convert(src: Path, filter_: str, ext: str, ctx: Context) -> Path:
    exe = paths.soffice()
    if not exe:
        raise ConversionError("This needs LibreOffice: winget install TheDocumentFoundation.LibreOffice")
    with tempfile.TemporaryDirectory(prefix="recast-") as tmp:
        # A private profile lets this run while the user has LibreOffice open.
        profile = Path(tmp, "profile").as_uri()
        cmd = [exe, f"-env:UserInstallation={profile}", "--headless", "--norestore",
               "--convert-to", filter_, "--outdir", tmp, str(src)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        while proc.poll() is None:
            try:
                proc.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                if ctx.cancel_event.is_set():
                    proc.kill()
                ctx.check()
        produced = Path(tmp) / f"{src.stem}.{ext}"
        if not produced.exists():
            raise ConversionError(f"LibreOffice couldn't convert {src.name}")
        dest = output_for(src, ext)
        shutil.move(produced, dest)
        return dest


def _register() -> None:
    if not available():
        return
    op("office.to.pdf", "PDF", "convert", OFFICE, target="pdf",
       only_ext={"docx", "doc", "odt", "rtf", "pptx", "ppt", "odp", "xlsx", "xls", "ods"})(
        lambda src, ctx: [soffice_convert(src, "pdf", "pdf", ctx)])
    op("office.to.docx", "DOCX", "convert", "document", target="docx", only_ext={"doc", "odt", "rtf", "md", "txt", "html"})(
        lambda src, ctx: [soffice_convert(src, "docx:MS Word 2007 XML", "docx", ctx)])
    op("office.to.odt", "ODT", "convert", "document", target="odt", only_ext={"docx", "doc", "rtf"})(
        lambda src, ctx: [soffice_convert(src, "odt", "odt", ctx)])
    op("office.to.xlsx", "XLSX", "convert", "sheet", target="xlsx", only_ext={"xls", "ods"})(
        lambda src, ctx: [soffice_convert(src, "xlsx:Calc MS Excel 2007 XML", "xlsx", ctx)])
    op("office.to.ods", "ODS", "convert", "sheet", target="ods", only_ext={"xlsx", "xls", "csv"})(
        lambda src, ctx: [soffice_convert(src, "ods", "ods", ctx)])
    op("office.to.pptx", "PPTX", "convert", "slides", target="pptx", only_ext={"ppt", "odp"})(
        lambda src, ctx: [soffice_convert(src, "pptx:Impress MS PowerPoint 2007 XML", "pptx", ctx)])

    def slides_to_images(src: Path, ctx: Context, fmt: str) -> list[Path]:
        pdf = soffice_convert(src, "pdf", "pdf", ctx.sub(0, 0.5))
        try:
            return to_images(pdf, fmt, ctx.sub(0.5, 1))
        finally:
            pdf.unlink(missing_ok=True)

    for fmt in ("png", "jpg"):
        op(f"slides.to.{fmt}", fmt.upper(), "convert", "slides", target=fmt, only_ext={"pptx", "ppt", "odp"})(
            lambda src, ctx, f=fmt: slides_to_images(src, ctx, f))


_register()
