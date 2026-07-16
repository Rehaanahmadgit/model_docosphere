"""
setup_wizard/roi_screen.py — Step 3: ROI (Region of Interest) Selector

Stub — implementation pending.
Will display a live camera frame and let the user draw a rectangle to define
the detection zone.  Only faces inside this region are processed, which
reduces CPU load and avoids false positives at the frame edges.
"""
from __future__ import annotations

import customtkinter as ctk


class ROIScreen(ctk.CTkFrame):
    """Step 3 of the setup wizard — draw the detection ROI on the live frame."""

    def __init__(self, master: ctk.CTk, on_success, camera_config: dict):
        super().__init__(master, fg_color="transparent")
        self._on_success = on_success
        self._camera_config = camera_config
        self._build_ui()

    def _build_ui(self) -> None:
        ctk.CTkLabel(
            self,
            text="Step 3 of 4  —  Detection Zone",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", padx=2, pady=(0, 6))

        ctk.CTkLabel(
            self,
            text="Region of Interest",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w", padx=2, pady=(0, 10))

        ctk.CTkLabel(
            self,
            text="(Coming soon — drag to select the detection zone on the live frame)",
            text_color="gray",
        ).pack(anchor="w", padx=2)

        ctk.CTkButton(
            self,
            text="Next  →",
            command=lambda: self._on_success({}),
            width=430,
            height=46,
        ).pack(anchor="w", padx=2, pady=(30, 0))
