"""
sync/schedule_cache.py — Local cache of the backend-configured active-hours schedule.

GET /api/agent/schedule is the source of truth; this module is the
read/write side of a local cache so the schedule gate in
service/attendance_loop.py keeps enforcing the last-known schedule when the
backend is briefly unreachable — same pattern as sync/embeddings_cache.py.

Plain JSON, not Fernet-encrypted like config.enc: the schedule isn't a
secret, and a plain file is easier to inspect while testing the sync flow.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.store import ConfigStore, _config_dir, get_machine_fingerprint
from sync.api_client import get_schedule

_CACHE_FILE = "schedule_cache.json"

_DAY_ORDER = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
]


def _cache_path() -> Path:
    return _config_dir() / _CACHE_FILE


def cache_exists() -> bool:
    """True once a schedule has been synced at least once (ever, even if stale)."""
    return _cache_path().exists()


def read_cache() -> dict:
    """Return the cached {day_name: {enabled, start, end}} schedule, or {} if missing/corrupted."""
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_cache(schedule: dict) -> dict:
    _cache_path().write_text(json.dumps(schedule), encoding="utf-8")
    return schedule


def refresh_schedule(cfg: Optional[dict] = None) -> dict:
    """
    Pull the latest schedule from the backend and refresh the local cache.

    Called alongside refresh_gallery() on agent startup and on-demand "Sync
    Now". Never raises and never blocks on a bad connection: on any failure
    (no config, network error, auth error) this logs a warning and falls
    back to whatever is already cached on disk.
    """
    cfg = cfg if cfg is not None else (ConfigStore().load() or {})
    base_url = cfg.get("backend_url")
    token = cfg.get("agent_token")
    fingerprint = cfg.get("machine_fingerprint") or get_machine_fingerprint()

    if not base_url or not token:
        print("! No backend URL/token in config — using cached schedule only.")
        return read_cache()

    try:
        schedule = get_schedule(base_url, token, fingerprint)
    except Exception as exc:
        print(f"! Could not sync schedule from the backend ({exc}); "
              f"falling back to the local cache.")
        return read_cache()

    enabled_count = sum(1 for day in schedule.values() if day.get("enabled"))
    print(f"→ Fetched schedule: {enabled_count} day(s) enabled "
          f"(GET /api/agent/schedule, {len(schedule)} day(s) total).")

    cached = _write_cache(schedule)
    print(f"✓ Cached schedule for {len(cached)} day(s) locally ({_cache_path()}).")

    return cached


# ── Schedule evaluation ──────────────────────────────────────────────────────
# Pure functions over the cached {day_name: {enabled, start, end}} shape —
# used by service/attendance_loop.py to gate when the camera/recognition
# loop is allowed to run. No I/O here; callers pass in whatever read_cache()
# returned.

def is_active_now(schedule: dict, now: Optional[datetime] = None) -> bool:
    """True if `now` (default: current local time) falls inside today's active window."""
    now = now or datetime.now()
    day = schedule.get(_DAY_ORDER[now.weekday()])
    if not day or not day.get("enabled"):
        return False
    start, end = day.get("start"), day.get("end")
    if not start or not end:
        return False
    current = now.strftime("%H:%M")
    return start <= current < end


def next_window_start(schedule: dict, now: Optional[datetime] = None) -> Optional[str]:
    """
    "HH:MM" of the next active window's start, scanning forward from `now`
    (today first, then up to 7 days ahead). Only meaningful when called
    while is_active_now() is False. Returns None if no day in the schedule
    is enabled.
    """
    now = now or datetime.now()
    current_time = now.strftime("%H:%M")
    today_idx = now.weekday()
    for offset in range(8):
        day = schedule.get(_DAY_ORDER[(today_idx + offset) % 7])
        if not day or not day.get("enabled") or not day.get("start"):
            continue
        start = day["start"]
        if offset == 0 and start <= current_time:
            continue  # today's window already started or passed
        return start
    return None
