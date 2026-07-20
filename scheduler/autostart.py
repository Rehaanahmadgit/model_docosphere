"""
scheduler/autostart.py — "Start automatically when Windows starts".

Adds/removes a HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run entry
pointing at the packaged agent executable, so it auto-launches on login.
HKCU (not HKLM) needs no admin rights and only affects the current user,
matching where config.enc/the app-data dir already live.

Only runs on Windows; no-op elsewhere (dev machines on Linux/Mac).
"""
from __future__ import annotations

import sys

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "NexusAttendanceAgent"


def _exe_path() -> str:
    """Path to the running executable — the packaged .exe when frozen (PyInstaller)."""
    return sys.executable


def enable() -> None:
    """Create the Run key entry. No-op on non-Windows platforms."""
    if sys.platform != "win32":
        return
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, f'"{_exe_path()}"')


def disable() -> None:
    """Remove the Run key entry, if present. No-op on non-Windows platforms."""
    if sys.platform != "win32":
        return
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_WRITE) as key:
            winreg.DeleteValue(key, RUN_VALUE_NAME)
    except FileNotFoundError:
        pass


def is_enabled() -> bool:
    """True if the Run key entry currently exists. Always False on non-Windows platforms."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, RUN_VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
