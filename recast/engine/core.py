"""Operation model shared by every engine module.

An Operation is one slice on a wheel. It either runs once per file
(`combine=False`) or once for the whole selection (`combine=True`).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..formats import category_of, norm_ext


class Cancelled(Exception):
    pass


class ConversionError(Exception):
    """A failure with a message that can be shown to the user as is."""


@dataclass
class Context:
    """Passed to every operation: progress reporting, cancellation, options."""

    options: dict = field(default_factory=dict)
    on_progress: Callable[[float], None] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def progress(self, fraction: float) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()
        if self.on_progress:
            self.on_progress(max(0.0, min(1.0, fraction)))

    def check(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def sub(self, start: float, end: float) -> "Context":
        """A context whose 0..1 progress maps onto start..end of this one."""
        parent = self

        def forward(f: float) -> None:
            parent.progress(start + (end - start) * f)

        return Context(self.options, forward, self.cancel_event)


@dataclass(frozen=True)
class Operation:
    id: str
    label: str
    wheel: str                       # "convert" | "tools" | "actions"
    categories: frozenset[str]
    func: Callable
    target: str = ""                 # output extension for conversions
    combine: bool = False            # func(list[Path], ctx) instead of func(Path, ctx)
    min_files: int = 1
    only_ext: frozenset[str] = frozenset()   # restrict to these input extensions
    hint: str = ""                   # one-line description for tooltips/settings
    ui_thread: bool = False          # needs dialogs/clipboard: run on the GUI thread, no progress
    asks: bool = False               # opens a window for its settings first (crop, trim, compress to…)
    copy_result: bool = False        # put the result on the clipboard, ready to paste (e.g. into Discord)

    def accepts(self, paths: list[Path]) -> bool:
        if len(paths) < self.min_files:
            return False
        for p in paths:
            if category_of(p) not in self.categories and "any" not in self.categories:
                return False
            if self.only_ext and norm_ext(p) not in self.only_ext:
                return False
        # Don't offer "PNG" when every file already is a PNG.
        if self.target and all(norm_ext(p) == self.target for p in paths):
            return False
        return True


REGISTRY: dict[str, Operation] = {}


def register(op: Operation) -> Operation:
    if op.id in REGISTRY:
        raise ValueError(f"duplicate operation id {op.id}")
    REGISTRY[op.id] = op
    return op


def op(id: str, label: str, wheel: str, categories: set[str] | str, **kw):
    """Decorator form of register()."""
    cats = frozenset({categories} if isinstance(categories, str) else categories)

    def deco(func):
        register(Operation(id=id, label=label, wheel=wheel, categories=cats, func=func,
                           only_ext=frozenset(kw.pop("only_ext", ())), **kw))
        return func

    return deco


def run(operation: Operation, paths: list[Path], ctx: Context) -> list[Path]:
    """Run an operation over a selection; returns every file it created."""
    if operation.combine:
        return list(operation.func(paths, ctx))
    outputs: list[Path] = []
    n = len(paths)
    for i, p in enumerate(paths):
        ctx.check()
        outputs += operation.func(p, ctx.sub(i / n, (i + 1) / n))
    ctx.progress(1.0)
    return outputs
