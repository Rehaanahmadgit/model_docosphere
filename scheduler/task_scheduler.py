"""
scheduler/task_scheduler.py — Windows Task Scheduler integration.

Stub — implementation pending.
Will register / unregister a Windows Scheduled Task via `schtasks.exe`
to auto-start the agent at the configured session start time and stop it
at the session end time, both pulled from the dashboard configuration.
"""
from __future__ import annotations

import subprocess
import sys


TASK_NAME = "NexusAttendanceAgent"


def register(start_time: str, stop_time: str, exe_path: str) -> None:
    """
    Create two scheduled tasks: one to start the agent at start_time,
    one to kill it at stop_time.  Times are 24h "HH:MM" strings.
    Only runs on Windows; no-op on other platforms.
    """
    if sys.platform != "win32":
        return
    # TODO: build schtasks /Create commands and subprocess.run them
    raise NotImplementedError


def unregister() -> None:
    """Delete both scheduled tasks if they exist."""
    if sys.platform != "win32":
        return
    subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
    )
    subprocess.run(
        ["schtasks", "/Delete", "/TN", f"{TASK_NAME}_Stop", "/F"],
        capture_output=True,
    )
