"""
sync/queue.py — Local SQLite queue for attendance events.

Events are written here first (even when offline), then flushed to the
backend by the sync worker.  A simple status column (pending/synced/failed)
drives retry logic.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import os


def _db_path() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / "NexusAttendanceAgent"
    d.mkdir(parents=True, exist_ok=True)
    return d / "events.db"


class EventQueue:
    def __init__(self):
        self._conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id      TEXT    NOT NULL,
                section_id      INTEGER NOT NULL,
                subject_id      INTEGER,
                check_in_at     TEXT    NOT NULL,   -- ISO-8601
                confidence_score REAL   NOT NULL,
                status          TEXT    NOT NULL DEFAULT 'pending',
                retry_count     INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT    NOT NULL
            )
            """
        )
        self._conn.commit()

    def enqueue(
        self,
        student_id: str,
        section_id: int,
        check_in_at: datetime,
        confidence_score: float,
        subject_id: int | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO events
                (student_id, section_id, subject_id, check_in_at, confidence_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                student_id,
                section_id,
                subject_id,
                check_in_at.isoformat(),
                confidence_score,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def pending(self, limit: int = 100) -> list[dict]:
        cur = self._conn.execute(
            "SELECT id, student_id, section_id, subject_id, check_in_at, confidence_score "
            "FROM events WHERE status='pending' ORDER BY id LIMIT ?",
            (limit,),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def mark_synced(self, ids: list[int]) -> None:
        self._conn.executemany(
            "UPDATE events SET status='synced' WHERE id=?",
            [(i,) for i in ids],
        )
        self._conn.commit()

    def mark_failed(self, ids: list[int]) -> None:
        self._conn.executemany(
            "UPDATE events SET status='failed', retry_count=retry_count+1 WHERE id=?",
            [(i,) for i in ids],
        )
        self._conn.commit()

    def reset_failed(self) -> int:
        """Re-queue failed events for retry (called on reconnect)."""
        cur = self._conn.execute(
            "UPDATE events SET status='pending' WHERE status='failed' AND retry_count < 5"
        )
        self._conn.commit()
        return cur.rowcount
