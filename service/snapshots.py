"""
service/snapshots.py — Per-match snapshot capture + retention cleanup.

On every debounced attendance match, AttendanceLoop saves the full camera
frame that produced the match, once per student per day, to:

    <app-data>/NexusAttendanceAgent/snapshots/{student_id}/{YYYY-MM-DD_HH-MM-SS}.jpg

Snapshots live under the same app-data directory config/store.py and
sync/queue.py already use, not the source tree, so they survive app updates.
A retention routine deletes anything older than SNAPSHOT_RETENTION_DAYS.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import cv2

# How long a snapshot is kept on disk before cleanup_old_snapshots() removes
# it. Named constant so retention policy can be tuned without hunting through
# the cleanup logic.
SNAPSHOT_RETENTION_DAYS = 3

_FILENAME_FMT = "%Y-%m-%d_%H-%M-%S"
_DATE_PREFIX_FMT = "%Y-%m-%d"


def _snapshots_root() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / "NexusAttendanceAgent" / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _student_dir(student_id: str) -> Path:
    d = _snapshots_root() / str(student_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _already_saved_today(student_id: str, today_prefix: str) -> bool:
    student_dir = _student_dir(student_id)
    return any(
        f.name.startswith(today_prefix) and f.suffix.lower() == ".jpg"
        for f in student_dir.iterdir()
        if f.is_file()
    )


def save_snapshot_if_needed(student_id: str, frame) -> Path | None:
    """
    Save `frame` as today's snapshot for `student_id` if one doesn't already
    exist for today. No-op (returns None) if a snapshot was already saved
    today for this student.
    """
    now = datetime.now()
    today_prefix = now.strftime(_DATE_PREFIX_FMT)
    if _already_saved_today(student_id, today_prefix):
        return None

    filename = f"{now.strftime(_FILENAME_FMT)}.jpg"
    dest = _student_dir(student_id) / filename
    cv2.imwrite(str(dest), frame)
    print(f"✓ Snapshot saved: student_id={student_id} file={dest.name}")
    return dest


def cleanup_old_snapshots(retention_days: float = SNAPSHOT_RETENTION_DAYS) -> int:
    """
    Delete any snapshot file whose modified time is older than
    `retention_days`. Returns the number of files deleted. Safe to call
    repeatedly (startup + periodic) — silently skips files that vanish
    mid-scan (e.g. a concurrent cleanup or manual deletion).
    """
    cutoff = time.time() - (retention_days * 86400)
    deleted = 0
    for student_dir in _snapshots_root().glob("*"):
        if not student_dir.is_dir():
            continue
        for f in student_dir.iterdir():
            if not f.is_file():
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
            except OSError:
                continue

    print(f"Snapshot cleanup: {deleted} file(s) deleted (retention={retention_days}d).")
    return deleted
