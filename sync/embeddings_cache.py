"""
sync/embeddings_cache.py — Local cache of student face embeddings.

GET /api/agent/sync-embeddings is the source of truth; this module is the
read/write side of a local cache so recognition can keep working (with
whatever was last synced) when the backend is briefly unreachable — e.g.
agent startup on a machine that hasn't got network yet.

Plain JSON, not Fernet-encrypted like config.enc: embeddings aren't a secret
in the way the agent token/camera credentials are, and a plain file is
easier to inspect while testing the sync flow.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from config.store import ConfigStore, _config_dir, get_machine_fingerprint
from sync.api_client import sync_embeddings

_CACHE_FILE = "embeddings_cache.json"


def _cache_path() -> Path:
    return _config_dir() / _CACHE_FILE


def read_cache() -> dict:
    """Return the cached {student_id: [floats]} gallery, or {} if missing/corrupted."""
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_cache(embeddings: list[dict]) -> dict:
    """
    Write {student_id: [floats]} to disk and return it.

    student_id is coerced to str on write — the backend returns an int
    (students.user_id), but the local pipeline treats student_id as an
    opaque string throughout (service/recognition.py, service/debounce.py,
    sync/queue.py).
    """
    gallery = {
        str(row["student_id"]): row["embedding"]
        for row in embeddings
        if row.get("student_id") is not None and row.get("embedding")
    }
    _cache_path().write_text(json.dumps(gallery), encoding="utf-8")
    return gallery


def refresh_gallery(cfg: Optional[dict] = None) -> dict:
    """
    Pull the latest embeddings from the backend and refresh the local cache.

    Called on agent startup; also callable on demand or from a future
    periodic timer — no scheduler is wired up for that yet, this is just
    the function it will call.

    Never raises and never blocks startup on a bad connection: on any
    failure (no config, network error, auth error) this logs a warning and
    falls back to whatever is already cached on disk.
    """
    cfg = cfg if cfg is not None else (ConfigStore().load() or {})
    base_url = cfg.get("backend_url")
    token = cfg.get("agent_token")
    fingerprint = cfg.get("machine_fingerprint") or get_machine_fingerprint()

    if not base_url or not token:
        print("! No backend URL/token in config — using cached embeddings only.")
        return read_cache()

    try:
        embeddings = sync_embeddings(base_url, token, fingerprint)
    except Exception as exc:
        print(f"! Could not sync embeddings from the backend ({exc}); "
              f"falling back to the local cache.")
        return read_cache()

    gallery = _write_cache(embeddings)
    print(f"✓ Synced {len(gallery)} student embedding(s) from the backend.")
    return gallery
