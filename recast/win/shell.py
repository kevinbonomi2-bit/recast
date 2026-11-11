"""Explorer integration: Send To shortcut, start with Windows, reveal, terminal."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

from .. import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def launch_command(*args: str) -> tuple[str, list[str]]:
    """Executable and arguments that start this app (frozen build or `pythonw -m recast`)."""
    if getattr(sys, "frozen", False):
        return sys.executable, list(args)
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else exe), ["-m", "recast", *args]


def _quote(a: str) -> str:
    return f'"{a}"' if " " in a else a


def sendto_path() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "SendTo" / f"{APP_NAME}.lnk"


def install_sendto() -> Path:
    exe, args = launch_command("wheel")
    lnk = sendto_path()
    workdir = str(Path(exe).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2])
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:RC_LNK);"
          "$s.TargetPath=$env:RC_EXE;$s.Arguments=$env:RC_ARGS;$s.WorkingDirectory=$env:RC_DIR;"
          "$s.Description='Convert with Recast';$s.Save()")
    env = {**os.environ, "RC_LNK": str(lnk), "RC_EXE": exe, "RC_ARGS": " ".join(_quote(a) for a in args),
           "RC_DIR": workdir}
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                   creationflags=NO_WINDOW)
    return lnk


def uninstall_sendto() -> None:
    sendto_path().unlink(missing_ok=True)


def sendto_installed() -> bool:
    return sendto_path().exists()


def set_autostart(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            exe, args = launch_command("--background")
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, " ".join(_quote(a) for a in [exe, *args]))
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except FileNotFoundError:
        return False


def trim_memory() -> None:
    """Hand pages the process isn't using back to Windows (what Task Manager shows drops with it).
    They fault back in from standby if needed again, so this is only worth doing when idle."""
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
    kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))


def reveal(path: Path) -> None:
    """Open Explorer with the file selected."""
    subprocess.Popen(["explorer", "/select,", str(path)])


def open_terminal(folder: Path) -> None:
    wt = shutil.which("wt")
    if wt:
        subprocess.Popen([wt, "-d", str(folder)])
    else:
        subprocess.Popen(["powershell", "-NoExit", "-Command", "Set-Location -LiteralPath $env:RC_DIR"],
                         cwd=str(folder), env={**os.environ, "RC_DIR": str(folder)},
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
