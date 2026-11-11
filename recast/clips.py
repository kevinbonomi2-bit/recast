"""Clip Inbox: notice new game clips from Game Bar, NVIDIA, AMD, OBS or Discord and offer what to do.

Nothing polls while Recast is idle: one thread per folder tree waits on Windows for new
files. A new clip is announced once the recorder has finished writing it, and held
back while a full-screen game is in front so the card never lands mid-match.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .paths import out_dir, unique_path
from .win import watch

log = logging.getLogger(__name__)

CLIP_EXTS = {".mp4", ".mkv", ".mov", ".webm"}
# Recorders stamp the date into the name: Game Bar "Game 2026-10-28 21-03-11", NVIDIA
# "Game 2026.10.28 - 21.03.11.02.DVR", AMD "Game_replay_2026.10.28-21.03", OBS "2026-10-28 21-03-11".
DATE = re.compile(r"(?<!\d)20\d{2}[-._]\d{2}[-._]\d{2}|(?<!\d)20\d{2}(?:0[1-9]|1[0-2])\d{2}(?!\d)")
SNIPPING_TOOL = re.compile(r"^(Screen )?Recording \d{4}-")
RECAST_OUTPUT = re.compile(r"-(compressed|muted|trimmed|rotated|discord|normalized)( \(\d+\))?$")
GENERIC = {"", "replay", "instant replay", "clip", "clips", "recording", "capture", "captures", "video", "videos",
           "obs", "highlight", "screen recording", "screen recordings", "radeon relive", "nvidia",
           "discord clips", "desktop", "game"}
REMIND_BYTES = 2_000_000_000   # old clips worth a reminder
BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def looks_like_clip(path: Path) -> bool:
    if path.suffix.lower() not in CLIP_EXTS:
        return False
    stem = path.stem
    return bool(DATE.search(stem)) and not SNIPPING_TOOL.match(stem) and not RECAST_OUTPUT.search(stem)


def _tidy(name: str) -> str:
    name = re.sub(r"[™®©]", "", name)
    name = re.sub(r"[\s_]+", " ", name)
    return name.strip(" -_.")


def game_from_name(path: Path, use_folder: bool = True) -> str:
    """The game a recorder put into the file name (or, with use_folder, its folder), or "" if it didn't."""
    stem = re.sub(r"\.DVR$", "", path.stem, flags=re.I)
    m = DATE.search(stem)
    name = stem[:m.start()] if m else stem
    name = re.sub(r"[_ -]*(instant )?replay[_ -]*$", "", name, flags=re.I)
    name = _tidy(name)
    if name.lower() not in GENERIC:
        return name
    if not use_folder:
        return ""
    parent = _tidy(path.parent.name)
    return "" if parent.lower() in GENERIC else parent


def folder_name(game: str) -> str:
    return BAD_CHARS.sub("", game).rstrip(" .") or "Other"


@dataclass
class Clip:
    path: Path
    game: str               # "" when nobody could tell
    named: bool             # the game came from the file name, so no rename is needed when sorting
    size: int = 0


def sort_clip(clip: Clip) -> Path:
    """Move a kept clip into a folder named after its game, next to where it was saved.

    Recorders that already sort by game (NVIDIA, AMD) are left where they are.
    """
    src = clip.path
    if not clip.game or not src.exists():
        return src
    folder = folder_name(clip.game)
    target_dir = src.parent if src.parent.name.casefold() == folder.casefold() else src.parent / folder
    name = src.name if clip.named else f"{folder} {src.name}"
    if target_dir == src.parent and name == src.name:
        return src
    target_dir.mkdir(exist_ok=True)
    dest = unique_path(target_dir / name)
    shutil.move(str(src), str(dest))
    return dest


def watch_roots(extra: str = "") -> list[Path]:
    """Folders worth watching, with any that sit inside another dropped (the watch is recursive)."""
    found = [watch.videos_folder(), watch.captures_folder()]
    if extra:
        found.append(Path(extra))
    roots: list[Path] = []
    for p in sorted({p.resolve() for p in found if p.is_dir()}, key=lambda p: len(p.parts)):
        if not any(p.is_relative_to(r) for r in roots):
            roots.append(p)
    return roots


def old_clips(roots: list[Path], days: int) -> list[Path]:
    """Clips older than `days`, oldest first. Plain directory walk; fine off the GUI thread."""
    cutoff = time.time() - days * 86400
    found: list[tuple[float, Path]] = []
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                p = Path(dirpath) / f
                if looks_like_clip(p):
                    try:
                        mtime = p.stat().st_mtime
                    except OSError:
                        continue
                    if mtime < cutoff:
                        found.append((mtime, p))
    return [p for _, p in sorted(found)]


class ClipInbox(QObject):
    """Announces finished clips through `ready`, at most `max_cards` at a time."""

    ready = Signal(object)          # Clip
    _seen = Signal(object, str)     # from watcher threads: path, foreground game at that moment

    STABLE_TICKS = 2                # one-second ticks with no growth before a clip counts as written
    GIVE_UP = 3 * 3600              # a file still growing after this long isn't a clip

    def __init__(self, max_cards: int = 3):
        super().__init__()
        self.max_cards = max_cards
        self.open_cards = 0
        self.watchers: list[watch.FolderWatcher] = []
        self.roots: list[Path] = []
        self._pending: dict[Path, list] = {}    # path -> [size, stable ticks, first seen, game hint]
        self._queue: list[Clip] = []
        self._busy: dict[Path, int] = {}         # folders a Recast job is writing into
        self._ignore: set[Path] = set()          # files Recast itself made or moved
        self._seen.connect(self._on_seen)
        self._tick_timer = QTimer(self, interval=1000, timeout=self._tick)
        self._hold_timer = QTimer(self, interval=2000, timeout=self._release)
        self.game_in_front = watch.game_in_front
        self.foreground_game = watch.foreground_game

    # -- watching -----------------------------------------------------------------

    def start(self, roots: list[Path]) -> None:
        self.stop()
        self.roots = roots
        for r in roots:
            w = watch.FolderWatcher(r, self._on_new)
            w.start()
            self.watchers.append(w)
        log.info("clip inbox watching %s", [str(r) for r in roots])

    def stop(self) -> None:
        for w in self.watchers:
            w.stop()
        self.watchers = []

    def _on_new(self, path: Path) -> None:  # watcher thread
        if looks_like_clip(path):
            self._seen.emit(path, self.foreground_game())

    def _on_seen(self, path: Path, hint: str) -> None:
        if path in self._ignore or path in self._pending:
            return
        self._pending[path] = [-1, 0, time.monotonic(), hint]
        self._tick_timer.start()

    def _tick(self) -> None:
        now = time.monotonic()
        for path, state in list(self._pending.items()):
            try:
                size = path.stat().st_size
            except OSError:
                del self._pending[path]     # renamed or deleted before it settled
                continue
            state[1] = state[1] + 1 if size == state[0] and size > 0 else 0
            state[0] = size
            if now - state[2] > self.GIVE_UP:
                del self._pending[path]
            elif state[1] >= self.STABLE_TICKS and path.parent not in self._busy and watch.is_released(path):
                del self._pending[path]
                self._arrived(path, size, state[3])
        if not self._pending:
            self._tick_timer.stop()

    def _arrived(self, path: Path, size: int, hint: str) -> None:
        if path in self._ignore:
            return
        # The file name is the best witness, then whatever was in front when the clip was saved,
        # then a per-game folder (but not one of the watched folders themselves).
        game = game_from_name(path, use_folder=False)
        if not game and not hint and path.parent not in self.roots:
            hint = game_from_name(path)
        clip = Clip(path, game or hint, named=bool(game), size=size)
        log.info("new clip %s (game: %s)", path, clip.game or "unknown")
        self._queue.append(clip)
        self._release()

    def _release(self) -> None:
        """Hand queued clips out, but not while a full-screen game is in front."""
        if not self._queue:
            self._hold_timer.stop()
            return
        if self.game_in_front():
            self._hold_timer.start()
            return
        while self._queue and self.open_cards < self.max_cards:
            self.open_cards += 1
            self.ready.emit(self._queue.pop(0))
        if self._queue:
            self._hold_timer.start()
        else:
            self._hold_timer.stop()

    def card_closed(self) -> None:
        self.open_cards = max(0, self.open_cards - 1)
        self._release()

    # -- Recast's own files -------------------------------------------------------

    def job_started(self, files: list[Path]) -> None:
        for d in {out_dir(f) for f in files}:
            self._busy[d] = self._busy.get(d, 0) + 1

    def job_ended(self, files: list[Path], outputs: list[Path]) -> None:
        self._ignore.update(outputs)
        for d in {out_dir(f) for f in files}:
            n = self._busy.get(d, 0) - 1
            if n > 0:
                self._busy[d] = n
            else:
                self._busy.pop(d, None)

    def keep(self, clip: Clip, sort: bool) -> Path:
        """The clip stays; with `sort`, it moves into its game's folder. Returns where it is now."""
        if not sort:
            return clip.path
        try:
            dest = sort_clip(clip)
        except OSError as e:
            log.warning("couldn't sort %s: %s", clip.path, e)
            return clip.path
        self._ignore.add(dest)
        return dest
