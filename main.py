"""
main.py — Nexus Attendance Agent entry point.

Launch sequence:
  1. Load encrypted config from disk.
  2. If setup is not complete → open setup wizard at the appropriate step.
  3. If setup is complete → run heartbeat check, then start service + system tray.

The wizard flow:
  TokenScreen  →  CameraScreen  →  ROIScreen  →  ModelScreen  →  main loop
"""
from __future__ import annotations

import sys

import customtkinter as ctk

from config.store import ConfigStore

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
        self._show_token_screen()

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
            on_success=self._show_roi_screen,
            org_info=org_info,
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
        ConfigStore().update({"setup_step": 4, **model_config})
        # TODO: destroy wizard, start system tray + background service
        self._clear()
        label = ctk.CTkLabel(
            self._container,
            text="Setup complete!\nThe agent will start on next launch.",
            font=ctk.CTkFont(size=18),
            justify="center",
        )
        label.pack(expand=True)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    store = ConfigStore()
    cfg = store.load()

    if store.is_setup_complete():
        # TODO: skip wizard, run service + tray
        # For now fall through to wizard so setup can be re-run
        pass

    app = WizardApp()
    app.mainloop()


if __name__ == "__main__":
    main()
