"""Subtitles, spreadsheets (CSV/TSV/XLSX) and text documents."""

from __future__ import annotations

import csv
import html
import re
from pathlib import Path

from ..formats import ext_of
from ..paths import output_for
from .core import ConversionError, Context, op

SUB, SHEET, DOC = "subtitle", "sheet", "document"


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1252", errors="replace")


# ---- subtitles ---------------------------------------------------------------

TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def _ms(m: re.Match) -> int:
    h, mi, s, frac = m.groups()
    return ((int(h or 0) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0"))


def parse_cues(text: str) -> list[tuple[int, int, str]]:
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            if "-->" in line:
                a, b = line.split("-->", 1)
                ma, mb = TIME.search(a), TIME.search(b)
                if ma and mb:
                    cues.append((_ms(ma), _ms(mb), "\n".join(lines[i + 1:]).strip()))
                break
    return cues


def _fmt(ms: int, sep: str) -> str:
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _subs(src: Path, target: str, ctx: Context) -> list[Path]:
    cues = parse_cues(read_text(src))
    if not cues:
        raise ConversionError(f"No subtitles found in {src.name}")
    if target == "srt":
        out = "\n".join(f"{i}\n{_fmt(a, ',')} --> {_fmt(b, ',')}\n{t}\n" for i, (a, b, t) in enumerate(cues, 1))
    elif target == "vtt":
        out = "WEBVTT\n\n" + "\n".join(f"{_fmt(a, '.')} --> {_fmt(b, '.')}\n{t}\n" for a, b, t in cues)
    else:
        out = "\n".join(re.sub(r"<[^>]+>", "", t) for _, _, t in cues) + "\n"
    dest = output_for(src, target)
    dest.write_text(out, encoding="utf-8")
    return [dest]


for _t, _label in [("srt", "SRT"), ("vtt", "VTT"), ("txt", "TXT")]:
    op(f"subtitle.to.{_t}", _label, "convert", SUB, target=_t)(lambda src, ctx, t=_t: _subs(src, t, ctx))


# ---- spreadsheets ------------------------------------------------------------

def read_rows(src: Path) -> list[tuple[str, list[list]]]:
    """[(sheet name, rows)] for CSV, TSV or XLSX."""
    ext = ext_of(src)
    if ext in ("csv", "tsv"):
        text = read_text(src)
        delim = "\t" if ext == "tsv" else (csv.Sniffer().sniff(text[:4096], ",;\t|").delimiter
                                          if text.strip() else ",")
        return [(src.stem, list(csv.reader(text.splitlines(), delimiter=delim)))]
    if ext == "xlsx":
        import openpyxl
        try:
            wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
        except Exception as e:
            raise ConversionError(f"Couldn't open {src.name}: {e}") from e
        return [(ws.title, [["" if v is None else v for v in row] for row in ws.iter_rows(values_only=True)])
                for ws in wb.worksheets]
    raise ConversionError(f"Can't read .{ext} spreadsheets without LibreOffice")


def _sheet(src: Path, target: str, ctx: Context) -> list[Path]:
    sheets = read_rows(src)
    ctx.progress(0.5)
    if target == "xlsx":
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheets[0][0][:31] or "Sheet1"
        for row in sheets[0][1]:
            ws.append([_cell(v) for v in row])
        dest = output_for(src, "xlsx")
        wb.save(dest)
        return [dest]
    delim = "\t" if target == "tsv" else ","
    dests = []
    for name, rows in sheets:
        if len(sheets) > 1 and not any(any(str(v) for v in r) for r in rows):
            continue  # skip empty extra sheets
        tag = name if len(sheets) > 1 else ""
        dest = output_for(src, target, tag)
        with open(dest, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f, delimiter=delim).writerows(rows)
        dests.append(dest)
    return dests


NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _cell(v):
    """Numbers in CSV become real numbers in XLSX; leading zeros (IDs, ZIP codes) stay text."""
    if isinstance(v, str) and NUM.match(v) and not (len(v) > 1 and v.lstrip("-").startswith("0") and "." not in v):
        return float(v) if "." in v else int(v)
    return v


for _t, _label in [("xlsx", "XLSX"), ("csv", "CSV"), ("tsv", "TSV")]:
    op(f"sheet.to.{_t}", _label, "convert", SHEET, target=_t, only_ext={"csv", "tsv", "xlsx"})(
        lambda src, ctx, t=_t: _sheet(src, t, ctx))


# ---- documents ---------------------------------------------------------------

def _doc_html(src: Path) -> str:
    ext = ext_of(src)
    if ext == "docx":
        import mammoth
        with open(src, "rb") as f:
            return mammoth.convert_to_html(f).value
    if ext in ("md", "markdown"):
        import markdown
        return markdown.markdown(read_text(src), extensions=["tables", "fenced_code", "sane_lists"])
    if ext in ("html", "htm"):
        return read_text(src)
    if ext == "txt":
        return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>\n"
                       for p in re.split(r"\n\s*\n", read_text(src)) if p.strip())
    raise ConversionError(f"Can't read .{ext} without LibreOffice")


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>", "\n", markup)
    markup = re.sub(r"(?i)</(p|div|h\d|li|tr|blockquote|pre)>", "\n\n", markup)
    markup = re.sub(r"(?i)<li[^>]*>", "• ", markup)
    text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font:16px/1.6 "Segoe UI",system-ui,sans-serif;max-width:46rem;margin:3rem auto;padding:0 1rem;color:#1b1b1f}}
img{{max-width:100%}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:.3rem .6rem}}
pre{{background:#f4f4f4;padding:1rem;overflow:auto}}</style></head>
<body>
{body}
</body></html>
"""


def _doc(src: Path, target: str, ctx: Context) -> list[Path]:
    ext = ext_of(src)
    dest = output_for(src, target)
    if target == "md" and ext == "docx":
        import mammoth
        with open(src, "rb") as f:
            out = mammoth.convert_to_markdown(f).value
    elif target == "md" and ext == "txt":
        out = read_text(src)
    elif target == "txt" and ext in ("md", "markdown"):
        out = read_text(src)
    else:
        body = _doc_html(src)
        if target == "html":
            out = body if "<html" in body.lower() else PAGE.format(title=html.escape(src.stem), body=body)
        elif target == "txt":
            out = _html_to_text(body)
        else:  # html -> md
            out = _html_to_text(body)
    dest.write_text(out, encoding="utf-8")
    return [dest]


for _t, _label in [("html", "HTML"), ("md", "Markdown"), ("txt", "TXT")]:
    op(f"doc.to.{_t}", _label, "convert", DOC, target=_t,
       only_ext={"docx", "md", "txt", "html"})(lambda src, ctx, t=_t: _doc(src, t, ctx))
