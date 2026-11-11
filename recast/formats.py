"""File categories by extension."""

from __future__ import annotations

from pathlib import Path

CATEGORIES: dict[str, set[str]] = {
    "image": {"jpg", "jpeg", "jfif", "png", "webp", "heic", "heif", "avif", "tif", "tiff",
              "bmp", "gif", "ico", "svg", "jxr", "wdp", "hdp"},
    "audio": {"mp3", "m4a", "aac", "wav", "flac", "ogg", "oga", "opus", "aif", "aiff", "wma"},
    "video": {"mp4", "m4v", "mov", "mkv", "webm", "avi", "wmv", "flv", "mpg", "mpeg", "ts", "3gp"},
    "pdf": {"pdf"},
    "ebook": {"epub", "mobi", "fb2"},
    "document": {"docx", "doc", "odt", "rtf", "md", "markdown", "txt", "html", "htm"},
    "slides": {"pptx", "ppt", "odp", "key"},
    "sheet": {"xlsx", "xls", "ods", "csv", "tsv"},
    "subtitle": {"srt", "vtt"},
    "archive": {"zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar"},
}

_BY_EXT = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts}


def ext_of(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".tar.gz"):
        return "tgz"
    return path.suffix.lower().lstrip(".")


def category_of(path: Path) -> str:
    """Category name for a file, "folder" for directories, or "other"."""
    if path.is_dir():
        return "folder"
    return _BY_EXT.get(ext_of(path), "other")


# Extensions that mean the same format, so we don't offer "JPG" for a .jpeg.
SAME_FORMAT = {
    "jpeg": "jpg", "jfif": "jpg", "tif": "tiff", "heif": "heic", "aif": "aiff",
    "oga": "ogg", "htm": "html", "markdown": "md", "m4v": "mp4", "mpeg": "mpg", "wdp": "jxr", "hdp": "jxr",
}


def norm_ext(path: Path) -> str:
    e = ext_of(path)
    return SAME_FORMAT.get(e, e)
