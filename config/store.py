"""
config/store.py — Encrypted local configuration store.

Key is derived from the machine fingerprint so config.enc is unreadable
if moved to a different machine (same security guarantee as the token binding).
Config is stored at %APPDATA%/NexusAttendanceAgent/config.enc on Windows,
or ~/NexusAttendanceAgent/config.enc on Linux/Mac (dev machines).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / "NexusAttendanceAgent"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_machine_fingerprint() -> str:
    """Stable 32-char hex string derived from hardware identifiers."""
    raw = f"{platform.node()}-{uuid.getnode()}-{platform.machine()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _derive_fernet_key() -> bytes:
    """Derive a 32-byte Fernet key from the machine fingerprint."""
    raw = f"{platform.node()}-{uuid.getnode()}-{platform.machine()}"
    digest = hashlib.sha256(raw.encode()).digest()   # always 32 bytes
    return base64.urlsafe_b64encode(digest)


class ConfigStore:
    """
    Thin wrapper around an encrypted JSON file.
    All reads/writes go through Fernet so the file is opaque on disk.
    """

    _CONFIG_FILE = "config.enc"

    def __init__(self):
        self._path = _config_dir() / self._CONFIG_FILE
        self._fernet = Fernet(_derive_fernet_key())

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self) -> dict | None:
        """Return the stored config dict, or None if not found / corrupted."""
        if not self._path.exists():
            return None
        try:
            encrypted = self._path.read_bytes()
            plaintext = self._fernet.decrypt(encrypted)
            return json.loads(plaintext)
        except (InvalidToken, json.JSONDecodeError, Exception):
            return None

    def save(self, data: dict) -> None:
        """Encrypt and persist the config dict."""
        plaintext = json.dumps(data, default=str).encode()
        encrypted = self._fernet.encrypt(plaintext)
        self._path.write_bytes(encrypted)

    def update(self, partial: dict) -> None:
        """Merge partial dict into existing config and save."""
        current = self.load() or {}
        current.update(partial)
        self.save(current)

    def clear(self) -> None:
        """Delete the config file (forces setup wizard on next launch)."""
        if self._path.exists():
            self._path.unlink()

    def is_setup_complete(self) -> bool:
        """True only when all wizard steps have been finished."""
        cfg = self.load()
        return bool(cfg and cfg.get("setup_step", 0) >= 4)
