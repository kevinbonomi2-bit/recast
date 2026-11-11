"""One global hotkey (RegisterHotKey), delivered as a Qt signal. Used for the clipboard wheel."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

log = logging.getLogger(__name__)
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
MODS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT}
HOTKEY_ID = 0x5243    # "RC"


def parse(combo: str) -> tuple[int, int] | None:
    """"ctrl+alt+r" -> (modifier flags, virtual key); None for "" or anything unreadable."""
    parts = [p for p in combo.lower().split("+") if p]
    if len(parts) < 2 or len(parts[-1]) != 1 or not all(p in MODS for p in parts[:-1]):
        return None
    mods = 0
    for p in parts[:-1]:
        mods |= MODS[p]
    return mods, ord(parts[-1].upper())


def label(combo: str) -> str:
    return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in combo.split("+"))


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, fire):
        super().__init__()
        self.fire = fire

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.fire()
                return True, 0
        return False, 0


class Hotkey(QObject):
    pressed = Signal()

    def __init__(self):
        super().__init__()
        self._filter = _Filter(self.pressed.emit)
        QCoreApplication.instance().installNativeEventFilter(self._filter)
        self.combo = ""

    def set(self, combo: str) -> bool:
        """Register `combo` ("" turns it off). False when another program already owns it."""
        if combo == self.combo:
            return True
        user32.UnregisterHotKey(None, HOTKEY_ID)
        self.combo = ""
        parsed = parse(combo)
        if parsed is None:
            return not combo
        if not user32.RegisterHotKey(None, HOTKEY_ID, parsed[0] | MOD_NOREPEAT, parsed[1]):
            log.warning("hotkey %s is taken by another program", combo)
            return False
        self.combo = combo
        return True

    def clear(self) -> None:
        self.set("")
