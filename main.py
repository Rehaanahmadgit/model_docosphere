"""
main.py — Nexus Attendance Agent entry point.

Launch sequence:
  1. Load encrypted config from disk.
  2. If setup is not complete → open setup wizard at the appropriate step.
  3. If setup is complete → run heartbeat check, then start service + system tray.

The wizard flow:
  TokenScreen  →  CameraScreen  →  SectionScreen  →  ROIScreen  →  ModelScreen  →  main loop
"""
from __future__ import annotations

import sys
import threading

import customtkinter as ctk

from config.store import ConfigStore, get_machine_fingerprint
from scheduler import autostart
from sync.embeddings_cache import refresh_gallery
from sync.schedule_cache import refresh_schedule

# ── Appearance ────────────────────────────────────────────────────────────────

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

WINDOW_TITLE = "Nexus Attendance Agent"
WINDOW_W = 560
WINDOW_H = 560


# ── Wizard orchestrator ────────────────────────────────────────────────────────

class WizardApp(ctk.CTk):
    """
    Root window that hosts one wizard screen at a time.
    Each screen calls _next(payload) when the user advances.
    """

    def __init__(self):
        super().__init__()
        self.title(WINDOW_TITLE)
        self.geometry(f"{WINDOW_W}x{WINDOW_H}")
        self.resizable(False, False)
        self._center()

        self._container = ctk.CTkFrame(self, fg_color="transparent")
        self._container.pack(fill="both", expand=True, padx=36, pady=36)

        self._current_screen = None
        self._resume()

    # ── Resume logic ───────────────────────────────────────────────────────────

    def _resume(self) -> None:
        """
        Decide which screen to open on launch based on the saved config.

        If config.enc decrypts, holds a verified token, and was bound to *this*
        machine, Step 1 (token verification) is skipped and the wizard resumes
        at the next unfinished step. Otherwise (no config, corrupted/undecryptable
        file, or a different machine) Step 1 is shown so the agent re-verifies.
        """
        step = self._resume_step()

        if step >= 5:
            self._show_already_configured()
        elif step >= 4:
            self._show_model_screen({})
        elif step >= 3:
            self._show_roi_screen({})
        elif step >= 2:
            self._show_section_screen({})
        elif step >= 1:
            # Token already verified — rebuild the org_info payload from config.
            self._show_camera_screen(self._org_info_from_config())
        else:
            self._show_token_screen()

    def _resume_step(self) -> int:
        """
        Return the saved setup_step only when the config is safe to resume:
          - config.enc decrypts (Fernet key is machine-bound, so a corrupted
            file or a different machine yields None here),
          - a verified agent token is present,
          - the stored machine fingerprint still matches this machine.
        Any failure returns 0, which forces Step 1 (re-verification).
        """
        cfg = ConfigStore().load()
        if not cfg:
            return 0
        if not cfg.get("agent_token"):
            return 0
        if cfg.get("machine_fingerprint") != get_machine_fingerprint():
            return 0
        try:
            return int(cfg.get("setup_step", 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _org_info_from_config() -> dict:
        cfg = ConfigStore().load() or {}
        return {
            "org_name": cfg.get("org_name", ""),
            "plan": cfg.get("plan", ""),
            "camera_limit": cfg.get("camera_limit", 1),
            "agent_id": cfg.get("agent_id"),
        }

    def _show_already_configured(self) -> None:
        self._clear()
        wrap = ctk.CTkFrame(self._container, fg_color="transparent")
        wrap.pack(expand=True)

        label = ctk.CTkLabel(
            wrap,
            text="Setup complete!\nThe agent will start on next launch.",
            font=ctk.CTkFont(size=18),
            justify="center",
        )
        label.pack(pady=(0, 16))

        sync_btn = ctk.CTkButton(
            wrap, text="Sync Now",
            command=lambda: self._on_sync_now(sync_btn, status_label),
        )
        sync_btn.pack()

        status_label = ctk.CTkLabel(wrap, text="", font=ctk.CTkFont(size=12))
        status_label.pack(pady=(10, 0))

        autostart_var = ctk.BooleanVar(value=bool((ConfigStore().load() or {}).get("start_on_login", False)))
        autostart_check = ctk.CTkCheckBox(
            wrap,
            text="Start automatically when Windows starts",
            variable=autostart_var,
            command=lambda: self._on_autostart_toggle(autostart_var.get()),
        )
        autostart_check.pack(pady=(20, 0))

        self._current_screen = wrap

        # Startup sync: refresh embeddings + schedule in the background as
        # soon as this screen loads, same as a manual "Sync Now" click.
        self._on_sync_now(sync_btn, status_label)

    @staticmethod
    def _on_autostart_toggle(enabled: bool) -> None:
        """Persist the preference and add/remove the HKCU Run key entry to match."""
        if enabled:
            autostart.enable()
        else:
            autostart.disable()
        ConfigStore().update({"start_on_login": enabled})

    def _on_sync_now(self, button: ctk.CTkButton, status_label: ctk.CTkLabel) -> None:
        """
        Manual trigger for GET /api/agent/sync-embeddings, for testing/refreshing
        the local gallery without waiting for the next AttendanceLoop.start().
        Runs the network call off the UI thread so the window doesn't freeze.
        """
        button.configure(state="disabled", text="Syncing…")
        status_label.configure(text="")

        def worker():
            # refresh_gallery()/refresh_schedule() never raise — on a sync
            # failure they log a warning and fall back to whatever's already
            # cached, so this message reflects the cache's resulting state
            # either way; check the console/log output for the
            # fetch-vs-fallback distinction.
            gallery = refresh_gallery()
            schedule = refresh_schedule()
            message = (
                f"✓ Local cache now has {len(gallery)} student(s) "
                f"and a schedule for {len(schedule)} day(s)."
            )
            self.after(0, lambda: self._on_sync_done(button, status_label, message))

        threading.Thread(target=worker, daemon=True).start()

    def _on_sync_done(self, button: ctk.CTkButton, status_label: ctk.CTkLabel, message: str) -> None:
        button.configure(state="normal", text="Sync Now")
        status_label.configure(text=message)

    def _center(self) -> None:
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - WINDOW_W) // 2
        y = (sh - WINDOW_H) // 2
        self.geometry(f"{WINDOW_W}x{WINDOW_H}+{x}+{y}")

    def _clear(self) -> None:
        if self._current_screen:
            self._current_screen.destroy()
            self._current_screen = None

    # ── Screen transitions ─────────────────────────────────────────────────────

    def _show_token_screen(self) -> None:
        self._clear()
        from setup_wizard.token_screen import TokenScreen
        self._current_screen = TokenScreen(
            self._container,
            on_success=self._show_camera_screen,
        )
        self._current_screen.pack(fill="both", expand=True)

    def _show_camera_screen(self, org_info: dict) -> None:
        self._clear()
        from setup_wizard.camera_screen import CameraScreen
        self._current_screen = CameraScreen(
            self._container,
            on_success=self._show_section_screen,
            org_info=org_info,
        )
        self._current_screen.pack(fill="both", expand=True)

    def _show_section_screen(self, camera_config: dict) -> None:
        self._clear()
        from setup_wizard.section_screen import SectionScreen
        self._current_screen = SectionScreen(
            self._container,
            on_success=self._show_roi_screen,
            camera_config=camera_config,
        )
        self._current_screen.pack(fill="both", expand=True)

    def _show_roi_screen(self, camera_config: dict) -> None:
        self._clear()
        from setup_wizard.roi_screen import ROIScreen
        self._current_screen = ROIScreen(
            self._container,
            on_success=self._show_model_screen,
            camera_config=camera_config,
        )
        self._current_screen.pack(fill="both", expand=True)

    def _show_model_screen(self, roi_config: dict) -> None:
        self._clear()
        from setup_wizard.model_screen import ModelScreen
        self._current_screen = ModelScreen(
            self._container,
            on_success=self._on_setup_complete,
            roi_config=roi_config,
        )
        self._current_screen.pack(fill="both", expand=True)

    def _on_setup_complete(self, model_config: dict) -> None:
        ConfigStore().update({"setup_step": 5, **model_config})
        # TODO: destroy wizard, start system tray + background service
        self._show_already_configured()


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    # WizardApp inspects the saved config and resumes at the correct step
    # (skipping token verification when a valid, machine-bound config exists).
    app = WizardApp()
    app.mainloop()


if __name__ == "__main__":
    main()
