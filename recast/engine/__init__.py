"""Conversion engine: importing this package registers every operation."""

from __future__ import annotations

from pathlib import Path

from .core import REGISTRY, Cancelled, ConversionError, Context, Operation, run
from . import images, media, pdf, archives, text, office  # noqa: F401  (registration side effects)

WHEELS = ("convert", "tools", "actions")


# Tools people reach for most come first on the wheel, whatever order modules register them in.
FIRST = ("compress", "discord", "compress_to", "crop", "trim")


def _rank(item: tuple[int, Operation]) -> tuple[int, int]:
    i, o = item
    kind = o.id.split(".", 1)[1]
    return (FIRST.index(kind) if kind in FIRST else len(FIRST), i)


def operations_for(paths: list[Path], wheel: str) -> list[Operation]:
    found = [(i, o) for i, o in enumerate(REGISTRY.values()) if o.wheel == wheel and o.accepts(paths)]
    return [o for _, o in sorted(found, key=_rank)]


__all__ = ["REGISTRY", "Cancelled", "ConversionError", "Context", "Operation", "WHEELS",
           "operations_for", "run"]
