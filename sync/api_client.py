"""
sync/api_client.py — HTTP client for Nexus backend agent endpoints.

All calls use a short timeout and raise RuntimeError with the server's
detail message on non-200 responses so the UI can display it directly.
"""
from __future__ import annotations

import requests

AGENT_VERSION = "1.0.0"
_TIMEOUT = 15  # seconds


def _headers(token: str) -> dict:
    return {
        "X-Agent-Token": token,
        "User-Agent": f"NexusAttendanceAgent/{AGENT_VERSION}",
        "Content-Type": "application/json",
    }


def _raise_for(resp: requests.Response) -> None:
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text
    raise RuntimeError(detail)


# ── Endpoints ─────────────────────────────────────────────────────────────────

def verify_agent_token(
    base_url: str,
    token: str,
    machine_fingerprint: str,
    machine_name: str,
) -> dict:
    """
    POST /api/agent/verify-token
    Returns org_info dict: {agent_id, org_name, plan, camera_limit}
    Raises RuntimeError on failure (message is display-safe).
    """
    resp = requests.post(
        f"{base_url}/api/agent/verify-token",
        json={
            "token": token,
            "machine_fingerprint": machine_fingerprint,
            "machine_name": machine_name,
            "agent_version": AGENT_VERSION,
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code == 200:
        return resp.json()
    _raise_for(resp)


def heartbeat(
    base_url: str,
    token: str,
    fingerprint: str,
    camera_count: int = 0,
) -> dict:
    """POST /api/agent/heartbeat — periodic subscription revalidation."""
    resp = requests.post(
        f"{base_url}/api/agent/heartbeat",
        json={
            "token": token,
            "machine_fingerprint": fingerprint,
            "camera_count": camera_count,
        },
        headers=_headers(token),
        timeout=_TIMEOUT,
    )
    if resp.status_code == 200:
        return resp.json()
    _raise_for(resp)


def sync_embeddings(
    base_url: str,
    token: str,
    fingerprint: str,
) -> list[dict]:
    """
    GET /api/agent/sync-embeddings — org-wide face embeddings, as
    [{student_id, embedding: [float, ...]}, ...]. The backend sync is
    org-wide (not filtered by section), so unlike the old signature this
    takes no section_ids/since — same minimal pattern as list_sections().
    """
    resp = requests.get(
        f"{base_url}/api/agent/sync-embeddings",
        params={"token": token, "machine_fingerprint": fingerprint},
        timeout=_TIMEOUT,
    )
    if resp.status_code == 200:
        return resp.json().get("embeddings", [])
    _raise_for(resp)


def list_sections(
    base_url: str,
    token: str,
    fingerprint: str,
) -> list[dict]:
    """GET /api/agent/sections — org's active sections, for the setup wizard's section picker."""
    resp = requests.get(
        f"{base_url}/api/agent/sections",
        params={"token": token, "machine_fingerprint": fingerprint},
        timeout=_TIMEOUT,
    )
    if resp.status_code == 200:
        return resp.json().get("sections", [])
    _raise_for(resp)


def push_attendance(
    base_url: str,
    token: str,
    fingerprint: str,
    events: list[dict],
) -> dict:
    """POST /api/agent/push-attendance — flush local SQLite queue to backend."""
    resp = requests.post(
        f"{base_url}/api/agent/push-attendance",
        json={
            "token": token,
            "machine_fingerprint": fingerprint,
            "events": events,
        },
        headers=_headers(token),
        timeout=_TIMEOUT,
    )
    if resp.status_code == 200:
        return resp.json()
    _raise_for(resp)
