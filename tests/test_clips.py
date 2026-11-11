import os
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from recast import clips
from recast.clips import Clip, ClipInbox, game_from_name, looks_like_clip, old_clips, sort_clip


@pytest.mark.parametrize("name, game", [
    ("Captures/Battlefield™ 6 2026-10-28 21-03-11.mp4", "Battlefield 6"),                # Game Bar
    ("NVIDIA/Valorant/Valorant 2026.10.28 - 21.03.11.02.DVR.mp4", "Valorant"),         # NVIDIA
    ("Radeon ReLive/Apex Legends/Apex Legends_replay_2026.10.28-21.03.mp4", "Apex Legends"),  # AMD
    ("Videos/Replay 2026-10-28 21-03-11.mkv", ""),                                    # OBS: no game in the name
    ("Videos/2026-10-28 21-03-11.mkv", ""),
    ("Discord Clips/Clip_20261028_210311.mp4", ""),
])
def test_game_from_name(name, game):
    assert game_from_name(Path(name)) == game


def test_what_counts_as_a_clip():
    assert looks_like_clip(Path("Valorant 2026-10-28 21-03-11.mp4"))
    assert not looks_like_clip(Path("Screen Recording 2026-10-11 234145.mp4"))   # Snipping Tool
    assert not looks_like_clip(Path("Recording 2026-10-11 234209.mp4"))
    assert not looks_like_clip(Path("Valorant 2026-10-28 21-03-11-discord.mp4"))  # Recast's own result
    assert not looks_like_clip(Path("Valorant 2026-10-28 21-03-11.png"))
    assert not looks_like_clip(Path("holiday.mp4"))


def test_sorting(tmp_path):
    gb = tmp_path / "Captures" / "Valorant 2026-10-28 21-03-11.mp4"
    gb.parent.mkdir()
    gb.write_bytes(b"x")
    moved = sort_clip(Clip(gb, "Valorant", named=True))
    assert moved == tmp_path / "Captures" / "Valorant" / gb.name and moved.exists() and not gb.exists()

    obs = tmp_path / "2026-10-28 21-03-11.mkv"
    obs.write_bytes(b"x")
    moved = sort_clip(Clip(obs, "Deep Rock: Galactic", named=False))
    assert moved == tmp_path / "Deep Rock Galactic" / "Deep Rock Galactic 2026-10-28 21-03-11.mkv"

    nv = tmp_path / "Valorant" / "Valorant 2026.10.28 - 21.03.11.02.DVR.mp4"
    nv.parent.mkdir(exist_ok=True)
    nv.write_bytes(b"x")
    assert sort_clip(Clip(nv, "Valorant", named=True)) == nv     # already in its game's folder


def _wait(cond, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def inbox(tmp_path):
    box = ClipInbox()
    box.game_in_front = lambda: False
    box.foreground_game = lambda: "Hades II"
    got: list[Clip] = []
    box.ready.connect(got.append)
    box.start([tmp_path])
    time.sleep(0.2)   # let the watcher thread open the folder
    yield box, got
    box.stop()


def _record(path: Path, chunks=3):
    """Write a file the way a recorder does: grows for a while, then closes."""
    with open(path, "wb") as f:
        for _ in range(chunks):
            f.write(os.urandom(20_000))
            f.flush()
            time.sleep(0.4)


def test_watcher_announces_finished_clips(inbox, tmp_path):
    box, got = inbox
    (tmp_path / "Captures").mkdir()
    _record(tmp_path / "Captures" / "Valorant 2026-10-28 21-03-11.mp4")
    _record(tmp_path / "2026-10-28 22-00-00.mkv")
    (tmp_path / "notes.txt").write_text("not a clip")
    assert _wait(lambda: len(got) == 2)
    by_name = {c.path.name: c for c in got}
    assert by_name["Valorant 2026-10-28 21-03-11.mp4"].game == "Valorant"
    obs = by_name["2026-10-28 22-00-00.mkv"]
    assert obs.game == "Hades II" and not obs.named and obs.size == 60_000


def test_cards_wait_until_the_game_is_closed(inbox, tmp_path):
    box, got = inbox
    playing = [True]
    box.game_in_front = lambda: playing[0]
    _record(tmp_path / "Valorant 2026-10-28 21-03-11.mp4", chunks=1)
    _wait(lambda: bool(box._queue), 6)
    assert box._queue and not got
    playing[0] = False
    assert _wait(lambda: len(got) == 1, 5)


def test_at_most_three_cards_at_once(inbox, tmp_path):
    box, got = inbox
    for i in range(5):
        (tmp_path / f"Valorant 2026-10-28 21-03-1{i}.mp4").write_bytes(b"x" * 1000)
    assert _wait(lambda: len(got) == 3)
    assert len(box._queue) == 2
    box.card_closed()
    assert len(got) == 4


def test_recast_results_are_not_clips(inbox, tmp_path):
    box, got = inbox
    src = tmp_path / "Valorant 2026-10-28 21-03-11.mp4"
    out = tmp_path / "Valorant 2026-10-28 21-03-11 (1).mp4"   # e.g. an MKV→MP4 conversion's name
    box._ignore.add(src)
    box.job_started([src])
    _record(out, chunks=1)
    time.sleep(3)
    box.job_ended([src], [out])
    assert not _wait(lambda: bool(got), 3)


def test_old_clips(tmp_path):
    fresh = tmp_path / "Valorant 2026-10-28 21-03-11.mp4"
    old = tmp_path / "Valorant" / "Valorant 2026-08-01 21-03-11.mp4"
    old.parent.mkdir()
    for f in (fresh, old):
        f.write_bytes(b"x")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    assert old_clips([tmp_path], 30) == [old]


def test_watch_roots_drop_nested(tmp_path, monkeypatch):
    videos = tmp_path / "Videos"
    (videos / "Captures").mkdir(parents=True)
    monkeypatch.setattr(clips.watch, "videos_folder", lambda: videos)
    monkeypatch.setattr(clips.watch, "captures_folder", lambda: videos / "Captures")
    other = tmp_path / "Medal"
    other.mkdir()
    assert set(clips.watch_roots(str(other))) == {videos.resolve(), other.resolve()}


def test_clip_card_keeps_on_timeout_and_undoes_delete(tmp_path):
    from recast.ui.clipcard import ClipCard
    picked = []
    card = ClipCard("Valorant", "46 MB", [("trim", "Trim", "cut"), ("keep", "Keep", ""), ("delete", "Delete", "")],
                    "light", lifetime=0.4)
    card.chosen.connect(picked.append)
    card._start_undo()
    assert card.state == "undo" and [b.key for b in card.buttons] == ["undo"]
    card._restore()
    assert card.state == "open" and len(card.buttons) == 3
    card.grab()   # paints without errors
    assert _wait(lambda: picked == ["keep"], 3)
