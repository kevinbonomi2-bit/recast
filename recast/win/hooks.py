"""Global low-level mouse and keyboard hooks that detect "tap a key while dragging a file".

A binding is "shift", "ctrl" or "alt" (either side), "vk:<code>" for any other key, or
"mouse:x1" / "mouse:x2" for the side buttons. Bound keys other than the modifiers are
swallowed during a drag, so a tapped Space or F-key never reaches the app underneath.

The hooks run on the Qt GUI thread (Qt pumps the Windows message loop), so the
callbacks only record state and emit queued signals; they must return fast or
Windows silently removes them.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, QPoint, QTimer, Signal

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x200, 0x201, 0x202
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x20B, 0x20C
LLKHF_INJECTED = 0x10

VK_SHIFT_KEYS = {0xA0, 0xA1}      # LSHIFT, RSHIFT
VK_CONTROL_KEYS = {0xA2, 0xA3}    # LCONTROL, RCONTROL
VK_MENU_KEYS = {0xA4, 0xA5}       # LMENU, RMENU (Alt)
VK_ESCAPE = 0x1B
KEY_GROUPS = {"shift": VK_SHIFT_KEYS, "ctrl": VK_CONTROL_KEYS, "alt": VK_MENU_KEYS}
GENERIC_MODIFIERS = {0x10: "shift", 0x11: "ctrl", 0x12: "alt"}
# Esc cancels the drag, Win opens Start, and the main mouse buttons are the drag itself.
RESERVED_VKS = {VK_ESCAPE, 0x5B, 0x5C, 0x01, 0x02, 0x04}
XBUTTONS = {1: "mouse:x1", 2: "mouse:x2"}
BINDING_LABELS = {"shift": "Shift", "ctrl": "Ctrl", "alt": "Alt", "mouse:x1": "Mouse 4", "mouse:x2": "Mouse 5"}


def binding_for_vk(vk: int) -> str | None:
    """The binding a key press stands for, or None for keys that can't be bound."""
    if vk in GENERIC_MODIFIERS:
        return GENERIC_MODIFIERS[vk]
    for name, keys in KEY_GROUPS.items():
        if vk in keys:
            return name
    if vk in RESERVED_VKS or not 0 < vk < 0xFF:
        return None
    return f"vk:{vk}"


SHORT = {"Caps Lock": "Caps", "Mouse 4": "M4", "Mouse 5": "M5", "Scroll Lock": "ScrLk", "Num Lock": "NumLk",
         "Page Up": "PgUp", "Page Down": "PgDn", "Insert": "Ins", "Delete": "Del", "Backspace": "Bksp",
         "Enter": "Enter", "Space": "Space"}


def short_label(binding: str) -> str:
    """A key name that fits the wheel's switcher keycap."""
    name = key_label(binding)
    return SHORT.get(name, name if len(name) <= 6 else name[:5])


def key_label(binding: str) -> str:
    """"Shift", "Space", "F13", "Mouse 4"…"""
    if binding in BINDING_LABELS:
        return BINDING_LABELS[binding]
    try:
        vk = int(binding.split(":", 1)[1])
    except (IndexError, ValueError):
        return binding
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    scan = user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
    extended = vk in (0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x6F, 0x90)
    buf = ctypes.create_unicode_buffer(64)
    if scan and user32.GetKeyNameTextW((scan << 16) | (extended << 24), buf, 64):
        name = buf.value
        return name if len(name) > 1 else name.upper()
    return f"Key {vk}"

DRAG_THRESHOLD = 8      # px the mouse must move with the button down
TAP_MAX_SECONDS = 0.6   # longer presses are "holding a modifier", not a tap


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD))


def _process_name(hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def shell_drag_image_visible() -> bool:
    """Explorer (and most apps using IDragSourceHelper) show this window during a file drag."""
    hwnd = user32.FindWindowW("SysDragImage", None)
    return bool(hwnd) and bool(user32.IsWindowVisible(hwnd))


def _suppress_alt_menu() -> None:
    """Releasing a lone Alt opens the focused app's menu bar; an unassigned key in between prevents it."""
    KEYEVENTF_KEYUP = 0x2
    user32.keybd_event(0xFF, 0, 0, 0)
    user32.keybd_event(0xFF, 0, KEYEVENTF_KEYUP, 0)


class DragKeyHook(QObject):
    """Emits `tapped(group, pos)` when shift/ctrl/alt is tapped during a mouse drag."""

    tapped = Signal(str, QPoint)    # binding, cursor
    released = Signal(QPoint)       # left button released (drop happened somewhere)
    escape = Signal()

    def __init__(self, any_app: bool = False):
        super().__init__()
        self.any_app = any_app      # accept drags from any app, not only shell file drags
        self.wheel_open = False     # set by the app: taps also count while the wheel is showing
        self._down = False
        self._dragging = False
        self._down_pt = (0, 0)
        self._down_process = ""
        self._key_down: tuple[str, float] | None = None
        self._other_key = False
        self.bindings: set[str] = {"shift", "ctrl", "alt"}   # set by the app from Settings
        self._swallowed: set[int] = set()
        self._kb = self._ms = None
        # Keep references: ctypes callbacks are freed when garbage collected.
        self._kb_proc = HOOKPROC(self._on_key)
        self._ms_proc = HOOKPROC(self._on_mouse)

    def install(self) -> None:
        self._ms = user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_proc, None, 0)
        self._kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, None, 0)
        if not self._ms or not self._kb:
            raise OSError(f"SetWindowsHookEx failed: {ctypes.get_last_error()}")
        log.info("hooks installed")

    def uninstall(self) -> None:
        for h in (self._kb, self._ms):
            if h:
                user32.UnhookWindowsHookEx(h)
        self._kb = self._ms = None

    # -- mouse -------------------------------------------------------------

    def _on_mouse(self, code, wparam, lparam):
        if code >= 0:
            info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            x, y = info.pt.x, info.pt.y
            if wparam == WM_LBUTTONDOWN:
                self._down, self._dragging, self._down_pt = True, False, (x, y)
                self._down_process = ""
                hwnd = user32.WindowFromPoint(info.pt)
                # Resolving the process is cheap enough but defer it off the hook path anyway.
                QTimer.singleShot(0, lambda h=hwnd: self._remember_process(h))
            elif wparam == WM_MOUSEMOVE and self._down and not self._dragging:
                dx, dy = x - self._down_pt[0], y - self._down_pt[1]
                if dx * dx + dy * dy > DRAG_THRESHOLD ** 2:
                    self._dragging = True
            elif wparam == WM_LBUTTONUP:
                was = self._dragging
                self._down = self._dragging = False
                if was or self.wheel_open:
                    self.released.emit(QPoint(x, y))
            elif wparam in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                binding = XBUTTONS.get(info.mouseData >> 16)
                if binding in self.bindings and self._active():
                    if wparam == WM_XBUTTONDOWN:
                        self.tapped.emit(binding, QPoint(x, y))
                    return 1   # don't let the browser go Back mid-drag
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _remember_process(self, hwnd) -> None:
        self._down_process = _process_name(user32.GetAncestor(hwnd, 2) or hwnd)

    # -- keyboard ----------------------------------------------------------

    def _group(self, vk: int) -> str | None:
        binding = binding_for_vk(vk)
        return binding if binding in self.bindings else None

    def _active(self) -> bool:
        return (self._dragging and self._is_file_drag()) or self.wheel_open

    def _on_key(self, code, wparam, lparam):
        if code >= 0:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if not info.flags & LLKHF_INJECTED:
                if self._handle_key(info.vkCode, wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)):
                    return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _handle_key(self, vk: int, down: bool) -> bool:
        """Track taps; returns True when the key should be swallowed."""
        if vk == VK_ESCAPE and down and self.wheel_open:
            self.escape.emit()
            return False
        group = self._group(vk)
        active = self._active()
        swallow = False
        if group is not None and group not in KEY_GROUPS:
            # A plain key bound to a wheel: keep it from typing into whatever is under the drag.
            if down and active:
                self._swallowed.add(vk)
            swallow = vk in self._swallowed
            if not down:
                self._swallowed.discard(vk)
        if group is None:
            if down:
                self._other_key = True
            return False
        if down:
            if self._key_down is None or self._key_down[0] != group:  # ignore auto-repeat
                self._key_down = (group, time.monotonic())
                self._other_key = False
                if group == "alt" and active:
                    _suppress_alt_menu()
            return swallow
        if self._key_down and self._key_down[0] == group:
            quick = time.monotonic() - self._key_down[1] <= TAP_MAX_SECONDS
            self._key_down = None
            if quick and not self._other_key and active:
                pos = QPoint(*self._cursor())
                log.info("tap %s (drag from %s, image=%s)", group, self._down_process or "?",
                         shell_drag_image_visible())
                self.tapped.emit(group, pos)
        return swallow

    def _is_file_drag(self) -> bool:
        if self.any_app:
            return True
        return shell_drag_image_visible() or self._down_process == "explorer.exe"

    @staticmethod
    def _cursor() -> tuple[int, int]:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
