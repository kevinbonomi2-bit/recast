"""Folder watching and the bits of Windows state Clip Inbox needs.

FolderWatcher blocks in ReadDirectoryChangesW on one thread per folder tree, so an
idle Recast costs nothing while it waits for a recorder to save a clip.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import winreg
from ctypes import wintypes
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

FILE_LIST_DIRECTORY = 0x1
FILE_SHARE_ALL = 0x7
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_NOTIFY_CHANGE_FILE_NAME = 0x1
FILE_ACTION_ADDED, FILE_ACTION_RENAMED_NEW_NAME = 1, 5
INVALID_HANDLE = wintypes.HANDLE(-1).value
GENERIC_READ = 0x80000000
ERROR_SHARING_VIOLATION = 32

kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.ReadDirectoryChangesW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.BOOL,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
                                           wintypes.LPVOID]
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class FolderWatcher:
    """Calls on_new(path) (from a background thread) when a file appears anywhere under `root`."""

    def __init__(self, root: Path, on_new: Callable[[Path], None]):
        self.root, self.on_new = root, on_new
        self._handle = None
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name=f"watch {root}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped = True
        if self._handle:
            kernel32.CancelIoEx(self._handle, None)  # wakes the blocked read

    def _run(self) -> None:
        h = kernel32.CreateFileW(str(self.root), FILE_LIST_DIRECTORY, FILE_SHARE_ALL, None, OPEN_EXISTING,
                                 FILE_FLAG_BACKUP_SEMANTICS, None)
        if h == INVALID_HANDLE or not h:
            log.warning("can't watch %s (error %d)", self.root, ctypes.get_last_error())
            return
        self._handle = h
        buf = ctypes.create_string_buffer(64 * 1024)
        got = wintypes.DWORD()
        try:
            while not self._stopped:
                ok = kernel32.ReadDirectoryChangesW(h, buf, len(buf), True, FILE_NOTIFY_CHANGE_FILE_NAME,
                                                    ctypes.byref(got), None, None)
                if not ok or self._stopped:
                    break
                for action, name in _parse(buf.raw[:got.value]):
                    if action in (FILE_ACTION_ADDED, FILE_ACTION_RENAMED_NEW_NAME):
                        try:
                            self.on_new(self.root / name)
                        except Exception:
                            log.exception("clip watcher callback failed")
        finally:
            kernel32.CloseHandle(h)
            self._handle = None


def _parse(data: bytes):
    """FILE_NOTIFY_INFORMATION records: next offset, action, name length (bytes), UTF-16 name."""
    off = 0
    while off + 12 <= len(data):
        nxt, action, length = int.from_bytes(data[off:off + 4], "little"), \
            int.from_bytes(data[off + 4:off + 8], "little"), int.from_bytes(data[off + 8:off + 12], "little")
        yield action, data[off + 12:off + 12 + length].decode("utf-16-le", "replace")
        if not nxt:
            break
        off += nxt


def is_released(path: Path) -> bool:
    """True when no other program holds the file open (the recorder has finished writing it)."""
    h = kernel32.CreateFileW(str(path), GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE or not h:
        return False
    kernel32.CloseHandle(h)
    return True


# ---- where recorders save --------------------------------------------------------

SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
CAPTURES_GUID = "{EDC0FE71-98D8-4F4A-B920-C8DC133CB165}"  # Game Bar's Captures folder, once moved


def _shell_folder(name: str) -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SHELL_FOLDERS) as k:
            return Path(os.path.expandvars(winreg.QueryValueEx(k, name)[0]))
    except OSError:
        return None


def videos_folder() -> Path:
    return _shell_folder("My Video") or Path.home() / "Videos"


def captures_folder() -> Path:
    return _shell_folder(CAPTURES_GUID) or videos_folder() / "Captures"


# ---- what's in front ----------------------------------------------------------------

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


user32.GetForegroundWindow.restype = wintypes.HWND
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
DESKTOP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd"}
NOT_GAMES = {"explorer", "recast", "python", "pythonw", "searchhost", "startmenuexperiencehost",
             "shellexperiencehost", "applicationframehost", "textinputhost", "lockapp",
             "chrome", "msedge", "firefox", "brave", "opera", "discord", "obs64", "steamwebhelper"}


def _class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def game_in_front() -> bool:
    """A full-screen (or borderless full-screen) window other than the desktop has the foreground."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd or _class(hwnd) in DESKTOP_CLASSES:
        return False
    r, mi = RECT(), MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return False
    if not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


def foreground_app() -> tuple[str, str]:
    """(window title, exe name without .exe) of the foreground window; empty strings when unknown."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", ""
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        buf, n = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            exe = Path(buf.value).stem
        kernel32.CloseHandle(h)
    return title.value.strip(), exe


def foreground_game() -> str:
    """A name for the game being played right now, or "" if the foreground isn't one."""
    title, exe = foreground_app()
    if not exe or exe.lower() in NOT_GAMES:
        return ""
    return title if 0 < len(title) <= 60 else exe


# ---- Recycle Bin -----------------------------------------------------------------------

class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", ctypes.c_uint), ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", wintypes.LPVOID), ("lpszProgressTitle", wintypes.LPCWSTR)]


FO_DELETE = 3
FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400


def recycle(paths: list[Path]) -> bool:
    """Move files to the Recycle Bin (never a permanent delete)."""
    if not paths:
        return True
    # pFrom is a list of NUL-separated paths ending in a double NUL.
    names = ctypes.create_unicode_buffer("\0".join(str(p) for p in paths) + "\0")
    op = SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom=ctypes.cast(names, wintypes.LPCWSTR),
                         fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI)
    return shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted
