"""Wheel behaviour without a screen: picking a tile runs the job and lands in the done state."""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from recast import jobs
from recast.ui import wheel as wm


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_pick_runs_job_and_shows_result(qapp, tmp_path):
    src = tmp_path / "holiday.jpg"
    Image.new("RGB", (400, 300), (40, 120, 200)).save(src)
    w = wm.Wheel(lambda: "light")
    w._set_files([src])
    w.mode = "convert"
    w._layout()
    labels = [o.label for o in w.ops]
    assert labels[:2] == ["PNG", "WEBP"]

    # Direction picks the tile: a point up and to the right of the hub is WEBP (2nd slot).
    step = 360 / len(w.ops)
    import math
    a = math.radians(step)
    i = w._tile_at(QPointF(wm.CX + 120 * math.sin(a), wm.CY - 120 * math.cos(a)))
    assert w.ops[i].label == "WEBP"
    assert w._tile_at(QPointF(wm.CX, wm.CY)) == -1  # the hub cancels

    started = []

    def start(op, files):
        job = jobs.Job(op, files, {})
        w.track(job)
        started.append(job)
        job.execute()  # synchronous here; the app uses the thread pool

    w.chosen.connect(start)
    w._pick(i)
    assert started and w.state == "done"
    assert w._result.stem == "Saved holiday" and w._result.ext == ".webp"
    assert (tmp_path / "holiday.webp").exists()
    w.grab()  # paints the done state without errors


def test_failed_job_shows_error(qapp, tmp_path):
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not an image")
    w = wm.Wheel(lambda: "dark")
    w._set_files([bad])
    w._layout()
    w.chosen.connect(lambda op, files: (lambda j: (w.track(j), j.execute()))(jobs.Job(op, files, {})))
    w._pick(0)
    assert w.state == "error" and "broken.jpg" in w._result.stem
    w.grab()


def test_release_job_hands_off_running_job(qapp, tmp_path):
    src = tmp_path / "a.jpg"
    Image.new("RGB", (40, 30)).save(src)
    w = wm.Wheel()
    w._set_files([src])
    w._layout()
    handed = []
    w.handoff.connect(lambda job, f: handed.append((job, f)))
    w.chosen.connect(lambda op, files: w.track(jobs.Job(op, files, {})))  # never executed: stays running
    w.show()
    w._pick(0)
    assert w.is_busy()
    w.release_job()
    assert handed and not w.isVisible()


def _run_on_pick(w):
    w.chosen.connect(lambda op, files: (lambda j: (w.track(j), j.execute()))(jobs.Job(op, files, {})))


def test_drop_on_hub_repeats_last_pick_and_undo_removes_only_the_result(qapp, tmp_path, monkeypatch):
    src = tmp_path / "holiday.jpg"
    Image.new("RGB", (400, 300), (40, 120, 200)).save(src)
    hub = QPointF(wm.CX, wm.CY)
    w = wm.Wheel(lambda: "light")
    w._set_files([src])
    w._layout()
    assert w._target_at(hub) == -1                       # nothing picked before: the hub still cancels
    _run_on_pick(w)
    webp = next(i for i, o in enumerate(w.ops) if o.label == "WEBP")
    w._pick(webp)
    assert w.state == "done" and w.last_ops == {"convert:image": w.ops[webp].id}

    # Undo takes the new file to the Recycle Bin and never the original.
    recycled = []
    monkeypatch.setattr("recast.win.watch.recycle", lambda paths: recycled.extend(paths) or True)
    assert w._undoable() == [tmp_path / "holiday.webp"]
    w.grab()                                             # paints the Undo button
    w._undo()
    assert recycled == [tmp_path / "holiday.webp"] and w._result.stem == "Undone"

    # Next drag of a photo: resting on the hub aims at WEBP again.
    w.state = "pick"
    other = tmp_path / "beach.jpg"
    Image.new("RGB", (300, 300), (200, 180, 90)).save(other)
    w._set_files([other])
    w._layout()
    assert w.ops[w._target_at(hub)].label == "WEBP" and w._from_hub
    w.repeat_on = False
    assert w._target_at(hub) == -1                       # the setting turns it off


def test_previews_are_cached_until_the_file_changes(qapp, tmp_path):
    src = tmp_path / "holiday.jpg"
    Image.new("RGB", (400, 300), (40, 120, 200)).save(src)
    wm.clear_preview_cache()
    w = wm.Wheel(lambda: "light")
    w._set_files([src])
    assert w.thumb is None                               # first drag: loading in the background
    end = time.monotonic() + 10
    while w.thumb is None and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.02)
    assert w.thumb is not None and w.previews
    w2 = wm.Wheel(lambda: "light")
    w2._set_files([src])
    assert w2.thumb is not None and w2.previews          # second drag: there at once
    time.sleep(0.02)
    Image.new("RGB", (500, 300), (10, 10, 10)).save(src)
    w3 = wm.Wheel(lambda: "light")
    w3._set_files([src])
    assert w3.thumb is None                              # edited since: not served stale


def test_pinned_tiles_come_first_from_the_next_open(qapp, tmp_path):
    src = tmp_path / "holiday.jpg"
    Image.new("RGB", (400, 300), (40, 120, 200)).save(src)
    w = wm.Wheel(lambda: "light")
    w._set_files([src])
    w._layout()
    before = [o.label for o in w.ops]
    avif = before.index("AVIF")
    changed = []
    w.pins_changed.connect(lambda: changed.append(1))
    w.toggle_pin(avif)
    assert [o.label for o in w.ops] == before            # nothing jumps while the wheel is open
    assert w.pins == {"convert:image": [w.ops[avif].id]} and changed
    w.grab()                                             # paints the favourite dot
    w._layout()                                          # next open
    assert [o.label for o in w.ops][0] == "AVIF" and [o.label for o in w.ops][1:3] == before[:2]
    w.toggle_pin(0)                                      # unpin
    w._layout()
    assert [o.label for o in w.ops] == before


def test_hotkey_combos():
    from recast.win import hotkey
    assert hotkey.parse("ctrl+alt+r") == (hotkey.MOD_CONTROL | hotkey.MOD_ALT, ord("R"))
    assert hotkey.parse("") is None and hotkey.parse("r") is None and hotkey.parse("hyper+r") is None
    assert hotkey.label("ctrl+alt+v") == "Ctrl+Alt+V"
