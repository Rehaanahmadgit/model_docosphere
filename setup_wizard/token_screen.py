"""
setup_wizard/token_screen.py — Step 1: Token Verification

Lets the admin paste the backend URL and agent token generated from the
Nexus dashboard (Settings → Cameras → New Agent).  Calls
POST /api/agent/verify-token, binds the machine fingerprint, and saves
the encrypted config before advancing to the camera setup screen.
"""
from __future__ import annotations

import socket
import threading

import customtkinter as ctk

from config.store import ConfigStore, get_machine_fingerprint
from sync.api_client import verify_agent_token


def _default_machine_name() -> str:
    try:
        return socket.gethostname()
    except Exception:
        import platform
        return platform.node() or "UnknownPC"


class TokenScreen(ctk.CTkFrame):
    """
    Step 1 of the setup wizard.
    on_success(org_info: dict) is called after the config is saved.
    """

    def __init__(self, master: ctk.CTk, on_success):
        super().__init__(master, fg_color="transparent")
        self._on_success = on_success
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Step indicator
        ctk.CTkLabel(
            self,
            text="Step 1 of 5  —  Connect to Server",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", padx=2, pady=(0, 6))

        # Title
        ctk.CTkLabel(
            self,
            text="Agent Token Verification",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w", padx=2, pady=(0, 4))

        ctk.CTkLabel(
            self,
            text=(
                "Paste the token from your Nexus dashboard\n"
                "Settings  →  Cameras  →  New Agent"
            ),
            font=ctk.CTkFont(size=13),
            text_color="gray",
            justify="left",
        ).pack(anchor="w", padx=2, pady=(0, 22))

        # ── Backend URL ────────────────────────────────────────────────────
        ctk.CTkLabel(
            self, text="Backend URL", font=ctk.CTkFont(size=13, weight="bold")
        ).pack(anchor="w", padx=2)
        self._url_entry = ctk.CTkEntry(
            self,
            placeholder_text="https://your-backend.railway.app",
            width=430,
            height=40,
        )
        self._url_entry.pack(anchor="w", padx=2, pady=(3, 14))

        # ── Agent token ────────────────────────────────────────────────────
        ctk.CTkLabel(
            self, text="Agent Token", font=ctk.CTkFont(size=13, weight="bold")
        ).pack(anchor="w", padx=2)
        self._token_entry = ctk.CTkEntry(
            self,
            placeholder_text="nxa_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            width=430,
            height=40,
            show="•",
        )
        self._token_entry.pack(anchor="w", padx=2, pady=(3, 14))

        # ── Machine name ───────────────────────────────────────────────────
        ctk.CTkLabel(
            self,
            text="Machine Name  (auto-detected, editable)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=2)
        self._name_entry = ctk.CTkEntry(self, width=430, height=40)
        self._name_entry.insert(0, _default_machine_name())
        self._name_entry.pack(anchor="w", padx=2, pady=(3, 22))

        # ── Status label ───────────────────────────────────────────────────
        self._status = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=13), text_color="gray"
        )
        self._status.pack(anchor="w", padx=2, pady=(0, 10))

        # ── Verify button ──────────────────────────────────────────────────
        self._btn = ctk.CTkButton(
            self,
            text="Verify & Continue  →",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=430,
            height=46,
            command=self._start_verify,
        )
        self._btn.pack(anchor="w", padx=2)

    # ── Verification flow ─────────────────────────────────────────────────────

    def _start_verify(self) -> None:
        url = self._url_entry.get().strip().rstrip("/")
        token = self._token_entry.get().strip()
        machine_name = self._name_entry.get().strip()

        if not url:
            self._set_status("Enter the backend URL.", error=True)
            return
        if not token:
            self._set_status("Paste your agent token.", error=True)
            return

        self._btn.configure(state="disabled", text="Verifying…")
        self._set_status("Connecting to server…")

        fingerprint = get_machine_fingerprint()
        threading.Thread(
            target=self._do_verify,
            args=(url, token, machine_name, fingerprint),
            daemon=True,
        ).start()

    def _do_verify(
        self, url: str, token: str, machine_name: str, fingerprint: str
    ) -> None:
        try:
            org_info = verify_agent_token(url, token, fingerprint, machine_name)
            self.after(0, self._on_verify_ok, url, token, fingerprint, machine_name, org_info)
        except Exception as exc:
            self.after(0, self._on_verify_err, str(exc))

    def _on_verify_ok(
        self,
        url: str,
        token: str,
        fingerprint: str,
        machine_name: str,
        org_info: dict,
    ) -> None:
        ConfigStore().save({
            "backend_url": url,
            "agent_token": token,
            "machine_fingerprint": fingerprint,
            "machine_name": machine_name,
            "org_name": org_info.get("org_name", ""),
            "plan": org_info.get("plan", ""),
            "camera_limit": org_info.get("camera_limit", 1),
            "agent_id": org_info.get("agent_id"),
            "setup_step": 1,
        })

        plan = org_info.get("plan", "")
        org_name = org_info.get("org_name", "your organisation")
        self._set_status(
            f"✓ Connected to {org_name} ({plan} plan)",
            color="#22c55e",
        )
        self._btn.configure(text="Verified ✓")
        self.after(900, lambda: self._on_success(org_info))

    def _on_verify_err(self, message: str) -> None:
        self._set_status(f"✗ {message}", error=True)
        self._btn.configure(state="normal", text="Verify & Continue  →")

    def _set_status(
        self, text: str, error: bool = False, color: str | None = None
    ) -> None:
        if color:
            self._status.configure(text=text, text_color=color)
        elif error:
            self._status.configure(text=text, text_color="#ef4444")
        else:
            self._status.configure(text=text, text_color="gray")
