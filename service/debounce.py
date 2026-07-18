"""
service/debounce.py — Session-window deduplication for attendance events.

A student is marked present at most once per session window (configurable,
default 4 hours).  This prevents duplicate records when the same face
appears in multiple consecutive frames or the agent restarts mid-session.
State is kept in memory only; a restart resets it (safe because the backend
also deduplicates via the attendance_records unique constraint).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class AttendanceDebounce:
    """
    Tracks which students have already been marked in the current session window.

    Args:
        window_hours: How long (hours) before a student can be marked again
    """

    def __init__(self, window_hours: float = 4.0):
        self._window = timedelta(hours=window_hours)
        self._last_seen: dict[str, datetime] = {}   # student_id → timestamp

    def should_record(self, student_id: str) -> bool:
        """Return True if this student should generate a new attendance event."""
        now = datetime.now(timezone.utc)
        last = self._last_seen.get(student_id)
        if last is None or (now - last) >= self._window:
            self._last_seen[student_id] = now
            return True
        return False

    def reset(self) -> None:
        """Clear all state (call at the start of each school day/session)."""
        self._last_seen.clear()
