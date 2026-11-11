"""Heavy libraries, loaded on first use.

Recast sits in the tray all day, so nothing here is imported until a conversion or a
preview actually needs it: the PDF library alone is ~60 MB of memory and half a second.
"""

from __future__ import annotations

import sys


class LazyModule:
    """Stands in for a module; the real one is imported the first time an attribute is read."""

    def __init__(self, load):
        self._load, self._module = load, None

    def __getattr__(self, name: str):
        if self._module is None:
            self._module = self._load()
        return getattr(self._module, name)


def _pymupdf():
    # PyMuPDF tries to pull in its optional page-layout AI (onnxruntime, numpy: ~100 MB and
    # 2.5 s) when it's installed. Recast never uses it, so make that import fail fast.
    sys.modules.setdefault("pymupdf.layout", None)
    import pymupdf
    return pymupdf


pymupdf = LazyModule(_pymupdf)
