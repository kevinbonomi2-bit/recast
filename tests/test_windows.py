"""Tool windows and Settings, driven without a screen."""

from __future__ import annotations

import subprocess

import pytest
from PIL import Image
from PySide6.QtCore import QSettings

from recast import paths
from recast.ui import theme

PAL = theme.LIGHT
ACC = PAL.accents["tools"]


@pytest.fixture
def photo(tmp_path):
    p = tmp_path / "holiday.jpg"
    Image.new("RGB", (400, 300), (40, 120, 200)).save(p)
    return p


def test_crop_window_aspect_and_exact_size(photo):
    from recast.ui.crop import CropWindow
    w = CropWindow([photo], PAL, ACC)
    assert w.options() == {"crop": (0.0, 0.0, 1.0, 1.0)}
    w.aspects.set_value("1:1", emit=True)
    px = w.canvas.crop_pixels()
    assert px == (300, 300)
    w.w_edit.setText("120")
    w.h_edit.setText("60")
    w._typed_size()
    opts = w.options()
    assert opts["size"] == (120, 60)
    x0, y0, x1, y1 = opts["crop"]
    assert abs((x1 - x0) * 400 / ((y1 - y0) * 300) - 2.0) < 0.01


def test_crop_window_multi_passes_aspect(photo, tmp_path):
    from recast.ui.crop import CropWindow
    other = tmp_path / "b.jpg"
    Image.new("RGB", (300, 400)).save(other)
    w = CropWindow([photo, other], PAL, ACC)
    assert w.windowTitle() == "Crop 2 images"
    w.aspects.set_value("16:9", emit=True)
    assert abs(w.options()["aspect"] - 16 / 9) < 1e-6


@pytest.mark.skipif(paths.ffmpeg() is None, reason="FFmpeg not installed")
def test_trim_window_times(tmp_path):
    from recast.ui import kit
    from recast.ui.trim import TrimWindow
    mp3 = tmp_path / "a.mp3"
    subprocess.run([paths.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "sine=duration=6", str(mp3)], check=True)
    w = TrimWindow(mp3, PAL, ACC)
    assert not w.ok.isEnabled()  # nothing trimmed yet
    w.start_edit.setText("0:01.5")
    w.end_edit.setText("4")
    w._typed()
    assert w.options() == {"start": 1.5, "end": 4.0}
    assert w.ok.isEnabled()
    assert kit.parse_time("1:02:03.5") == 3723.5 and kit.fmt_time(3723.5) == "1:02:03.5"
    assert kit.fmt_time(7.5) == "0:07.5"
    w.player.stop()


def test_compress_window_modes(photo):
    from recast.ui.compress import CompressWindow
    w = CompressWindow([photo], PAL, ACC)
    assert abs(w.options()["ratio"] - 0.5) < 1e-9
    w.mode.set_value("size", emit=True)
    w.unit.set_value("KB", emit=True)
    w.size_edit.setText(f"{photo.stat().st_size / 4000:.3f}")
    assert abs(w.ratio() - 0.25) < 0.01
    w.mode.set_value("preset", emit=True)
    w.preset.set_value("strong", emit=True)
    assert w.ratio() == 0.3
    w.resize_on.setChecked(True)
    assert w.options()["max_side"] == 2560


def test_settings_keys_stay_unique(tmp_path):
    from recast.ui.settings import SettingsWindow, value
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    changed = []
    actions = {"sendto_installed": lambda: False, "set_sendto": lambda on: None, "autostart_enabled": lambda: False,
               "set_autostart": lambda on: None, "open_log": lambda: None}
    w = SettingsWindow(s, changed.append, actions)
    w.key_controls["convert"].set_value("alt", emit=True)
    assert (value(s, "key_convert"), value(s, "key_actions"), value(s, "key_tools")) == ("alt", "shift", "ctrl")
    assert w.key_controls["actions"].value == "shift"
    assert "key_convert" in changed


def test_settings_custom_key(tmp_path, monkeypatch):
    from recast.ui import kit
    from recast.ui.settings import SettingsWindow, value
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    actions = {"sendto_installed": lambda: False, "set_sendto": lambda on: None, "autostart_enabled": lambda: False,
               "set_autostart": lambda on: None, "open_log": lambda: None}
    w = SettingsWindow(s, lambda k: None, actions)
    monkeypatch.setattr(kit.KeyCapture, "capture", lambda self: "mouse:x1")
    w.key_controls["tools"].pressed.emit("other")
    assert value(s, "key_tools") == "mouse:x1"
    assert w.key_controls["tools"].options[3] == ("mouse:x1", "Mouse 4")
    # Giving Convert's key to Tools swaps: Convert takes over Mouse 4.
    monkeypatch.setattr(kit.KeyCapture, "capture", lambda self: "shift")
    w._set_key("tools", "shift")
    assert value(s, "key_convert") == "mouse:x1" and w.key_controls["convert"].value == "mouse:x1"
    monkeypatch.setattr(kit.KeyCapture, "capture", lambda self: None)   # cancelled: nothing changes
    w.key_controls["actions"].pressed.emit("other")
    assert value(s, "key_actions") == "alt" and w.key_controls["actions"].value == "alt"


def test_hook_custom_keys_tap_and_swallow():
    from recast.win.hooks import DragKeyHook
    hook = DragKeyHook()
    hook.bindings = {"vk:32", "ctrl", "mouse:x1"}
    hook.wheel_open = True
    taps = []
    hook.tapped.connect(lambda b, _p: taps.append(b))
    assert hook._handle_key(32, True) is True      # Space is swallowed, so it doesn't type
    assert hook._handle_key(32, False) is True
    assert hook._handle_key(0xA0, True) is False   # Shift isn't bound now: passes through
    hook._handle_key(0xA0, False)
    assert taps == ["vk:32"]
    hook.wheel_open = False
    assert hook._handle_key(32, True) is False     # outside a drag Space is just Space
    hook._handle_key(32, False)


def test_discord_limits_follow_discords_2026_change(tmp_path):
    from recast.engine.media import limit_text
    from recast.ui.settings import value
    assert (limit_text(20), limit_text(50), limit_text(1000)) == ("20 MB", "50 MB", "1 GB")
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    assert value(s, "discord_limit_mb") == 20             # new default: Discord's free limit
    for old, new in ((10, 20), (50, 50), (500, 1000)):    # choices saved before the change
        s.setValue("discord_limit_mb", old)
        assert value(s, "discord_limit_mb") == new
