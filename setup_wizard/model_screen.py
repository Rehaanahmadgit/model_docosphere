"""
setup_wizard/model_screen.py — Step 4: Detection + Recognition Model Picker

Stub — implementation pending.
Will detect CPU/GPU capabilities, auto-recommend a model tier
(lite / standard / accurate), download the selected models to models/,
and verify SHA-256 checksums before finishing setup.
"""
from __future__ import annotations

import customtkinter as ctk


class ModelScreen(ctk.CTkFrame):
    """Step 4 of the setup wizard — model selection and download."""

    def __init__(self, master: ctk.CTk, on_success, roi_config: dict):
        super().__init__(master, fg_color="transparent")
        self._on_success = on_success
        self._roi_config = roi_config
        self._build_ui()

    def _build_ui(self) -> None:
        ctk.CTkLabel(
            self,
            text="Step 4 of 4  —  AI Model",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", padx=2, pady=(0, 6))

        ctk.CTkLabel(
            self,
            text="Detection Model",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w", padx=2, pady=(0, 10))

        ctk.CTkLabel(
            self,
            text="(Coming soon — auto-detects GPU, recommends model tier, downloads weights)",
            text_color="gray",
        ).pack(anchor="w", padx=2)

        ctk.CTkButton(
            self,
            text="Finish Setup",
            command=lambda: self._on_success({}),
            width=430,
            height=46,
        ).pack(anchor="w", padx=2, pady=(30, 0))
