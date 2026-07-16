"""
setup_wizard/camera_screen.py — Step 2: Camera Configuration + Live Preview

Stub — implementation pending.
Will enumerate available cameras via OpenCV, show a live preview frame,
and let the user pick the active camera index and target resolution.
"""
from __future__ import annotations

import customtkinter as ctk


class CameraScreen(ctk.CTkFrame):
    """Step 2 of the setup wizard — camera selection and live preview."""

    def __init__(self, master: ctk.CTk, on_success, org_info: dict):
        super().__init__(master, fg_color="transparent")
        self._on_success = on_success
        self._org_info = org_info
        self._build_ui()

    def _build_ui(self) -> None:
        ctk.CTkLabel(
            self,
            text="Step 2 of 4  —  Camera Configuration",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", padx=2, pady=(0, 6))

        ctk.CTkLabel(
            self,
            text="Camera Setup",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w", padx=2, pady=(0, 10))

        ctk.CTkLabel(
            self,
            text="(Coming soon — camera enumeration + live preview)",
            text_color="gray",
        ).pack(anchor="w", padx=2)

        ctk.CTkButton(
            self,
            text="Next  →",
            command=lambda: self._on_success({}),
            width=430,
            height=46,
        ).pack(anchor="w", padx=2, pady=(30, 0))
