"""
service/logging_setup.py — Persistent file logging for the unattended agent.

Writes to a daily-rotating log file under the same app-data directory
config/store.py and sync/queue.py already use, not the source tree, so logs
survive app updates:

    <app-data>/NexusAttendanceAgent/logs/agent_{YYYY-MM-DD}.log

This is additional to the existing console print() output used throughout
the codebase for interactive debugging (e.g. test_pipeline.py) — nothing
here replaces or redirects that; it's a separate sink for the unattended
production agent, where console output isn't captured anywhere.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path

# How long a daily log file is kept before cleanup deletes it. Named
# constant, deliberately separate from service/snapshots.py's
# SNAPSHOT_RETENTION_DAYS — logs are much smaller and worth keeping longer
# for debugging.
LOG_RETENTION_DAYS = 7

_LOGGER_NAME = "attendance_agent"
_DATE_FMT = "%Y-%m-%d"


def _logs_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / "NexusAttendanceAgent" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cleanup_old_logs(retention_days: float = LOG_RETENTION_DAYS) -> int:
    """Delete log files older than `retention_days`. Returns count deleted."""
    cutoff = time.time() - (retention_days * 86400)
    deleted = 0
    for f in _logs_dir().glob("agent_*.log"):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        except OSError:
            continue
    if deleted:
        logging.getLogger(_LOGGER_NAME).info(
            "Log cleanup: %d file(s) deleted (retention=%sd).", deleted, retention_days
        )
    return deleted


class _DailyFileHandler(logging.Handler):
    """
    Routes records to today's dated log file, opening a new one (and running
    retention cleanup) whenever the date rolls over. Unlike
    logging.handlers.TimedRotatingFileHandler, the *active* file itself is
    always named agent_{today}.log rather than a fixed name that only gets
    a date suffix once rotated away.
    """

    def __init__(self) -> None:
        super().__init__()
        self._current_date: str | None = None
        self._file_handler: logging.FileHandler | None = None
        self._roll_if_needed()

    def _roll_if_needed(self) -> None:
        today = datetime.now().strftime(_DATE_FMT)
        if today == self._current_date:
            return
        if self._file_handler is not None:
            self._file_handler.close()
        path = _logs_dir() / f"agent_{today}.log"
        handler = logging.FileHandler(path, encoding="utf-8")
        if self.formatter is not None:
            handler.setFormatter(self.formatter)
        self._file_handler = handler
        self._current_date = today
        cleanup_old_logs()

    def setFormatter(self, fmt) -> None:  # noqa: N802 (matches logging.Handler API)
        super().setFormatter(fmt)
        if self._file_handler is not None:
            self._file_handler.setFormatter(fmt)

    def emit(self, record: logging.LogRecord) -> None:
        self._roll_if_needed()
        self._file_handler.emit(record)

    def close(self) -> None:
        if self._file_handler is not None:
            self._file_handler.close()
        super().close()


def get_logger() -> logging.Logger:
    """
    Return the shared attendance-agent file logger, configuring it on first
    call. Safe to call repeatedly (e.g. once per module) — configuration
    only happens once.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = _DailyFileHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        # Don't propagate to the root logger — this sink is additional to,
        # not a replacement for, the existing console print() output.
        logger.propagate = False
    return logger
