"""
setup_wizard/section_screen.py — Step 3: Section Assignment

Fetches the org's active sections from GET /api/agent/sections (using the
token verified in Step 1) and lets the admin pick which one this camera
monitors. The choice is saved as section_id/section_name in the encrypted
config; service/attendance_loop.py reads section_id from there when
enqueuing attendance events (sync/queue.py's events.section_id is NOT NULL).
"""
from __future__ import annotations

import threading

import customtkinter as ctk

from config.store import ConfigStore
from sync.api_client import list_sections


class SectionScreen(ctk.CTkFrame):
    """Step 3 of the setup wizard — assign this camera to an org section."""

    def __init__(self, master: ctk.CTk, on_success, camera_config: dict):
        super().__init__(master, fg_color="transparent")
        self._on_success = on_success
        self._camera_config = camera_config or {}

        self._sections: list[dict] = []
        self._label_to_id: dict[str, int] = {}
        self._selected_section_id: int | None = None
        self._selected_section_name: str | None = None

        self._build_ui()
        self._start_fetch()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        ctk.CTkLabel(
            self,
            text="Step 3 of 5  —  Section Assignment",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", padx=2, pady=(0, 4))

        ctk.CTkLabel(
            self,
            text="Assign a Section",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w", padx=2, pady=(0, 2))

        ctk.CTkLabel(
            self,
            text="Choose which class section this camera takes attendance for.",
            font=ctk.CTkFont(size=13),
            text_color="gray",
            justify="left",
            wraplength=468,
        ).pack(anchor="w", padx=2, pady=(0, 14))

        ctk.CTkLabel(
            self, text="Section", font=ctk.CTkFont(size=13, weight="bold")
        ).pack(anchor="w", padx=2)
        self._dropdown = ctk.CTkOptionMenu(
            self,
            values=["Loading sections…"],
            width=430,
            height=40,
            command=self._on_select,
            state="disabled",
        )
        self._dropdown.pack(anchor="w", padx=2, pady=(3, 10))

        # Picking a section from the dropdown only stages it — Next stays
        # locked until this is explicitly clicked, so a rushed installer
        # can't advance on whatever the dropdown happened to default to.
        self._confirm_btn = ctk.CTkButton(
            self,
            text="Select a section above",
            font=ctk.CTkFont(size=13, weight="bold"),
            width=430,
            height=40,
            state="disabled",
            command=self._on_confirm,
        )
        self._confirm_btn.pack(anchor="w", padx=2, pady=(0, 10))

        self._status = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color="gray"
        )
        self._status.pack(anchor="w", padx=2, pady=(0, 6))

        # ── Bottom navigation bar ───────────────────────────────────────────
        # Same reserved-space pattern as camera_screen.py / roi_screen.py — pack
        # it before the content that may clip so Next never gets pushed offscreen.
        self._nav = ctk.CTkFrame(self, fg_color="transparent")
        self._nav.pack(side="bottom", fill="x", pady=(8, 0))

        self._next_btn = ctk.CTkButton(
            self._nav,
            text="Next  →",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=430,
            height=44,
            command=self._on_next,
        )
        # Not packed into the bar yet — revealed once a section is available.

        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.pack(side="top", fill="both", expand=True)

    # ── Fetch flow ───────────────────────────────────────────────────────────────

    def _start_fetch(self) -> None:
        self._hide_next()
        self._clear_content()
        cfg = ConfigStore().load() or {}
        base_url = cfg.get("backend_url")
        token = cfg.get("agent_token")
        fingerprint = cfg.get("machine_fingerprint")
        if not base_url or not token:
            self._show_error("No verified token found. Go back to Step 1.")
            return

        self._dropdown.configure(values=["Loading sections…"], state="disabled")
        self._dropdown.set("Loading sections…")
        self._confirm_btn.configure(state="disabled", text="Select a section above")
        self._selected_section_id = None
        self._selected_section_name = None
        self._set_status("Fetching sections from the server…")

        threading.Thread(
            target=self._fetch, args=(base_url, token, fingerprint), daemon=True
        ).start()

    def _fetch(self, base_url: str, token: str, fingerprint: str) -> None:
        try:
            sections = list_sections(base_url, token, fingerprint)
            self.after(0, self._on_fetch_ok, sections)
        except Exception as exc:  # never let a network error crash the wizard
            self.after(0, self._on_fetch_err, str(exc))

    def _on_fetch_ok(self, sections: list[dict]) -> None:
        if not sections:
            self._show_error(
                "No sections found for your organisation. Create one in the "
                "Nexus dashboard (Academic → Sections), then retry."
            )
            return

        self._sections = sections
        self._label_to_id = {}
        labels = []
        for sec in sections:
            sid = sec.get("section_id")
            name = sec.get("section_name") or f"Section {sid}"
            # section_name alone (e.g. "A") repeats across classes — the id
            # suffix keeps dropdown entries unique and unambiguous.
            label = f"{name}  (#{sid})"
            self._label_to_id[label] = sid
            labels.append(label)

        self._dropdown.configure(values=labels, state="normal")
        self._dropdown.set(labels[0])
        self._confirm_btn.configure(state="normal")
        self._stage(labels[0])
        self._set_status(
            f"{len(sections)} section(s) loaded — review and confirm your selection."
        )

    def _on_fetch_err(self, message: str) -> None:
        self._show_error(message)

    def _show_error(self, message: str) -> None:
        self._hide_next()
        self._dropdown.configure(values=["Unavailable"], state="disabled")
        self._dropdown.set("Unavailable")
        self._confirm_btn.configure(state="disabled", text="Select a section above")
        self._selected_section_id = None
        self._selected_section_name = None
        self._set_status(f"✗ {message}", error=True)
        self._clear_content()
        ctk.CTkButton(
            self._content,
            text="Retry",
            width=140,
            height=38,
            command=self._start_fetch,
        ).pack(anchor="w", padx=2, pady=(6, 0))

    def _clear_content(self) -> None:
        for child in self._content.winfo_children():
            child.destroy()

    # ── Selection ──────────────────────────────────────────────────────────────

    def _on_select(self, label: str) -> None:
        """Dropdown command — fires on every explicit pick, staging it for
        confirmation. Never unlocks Next by itself."""
        self._stage(label)

    def _stage(self, label: str) -> None:
        """Update the Confirm button to reflect the currently picked (but not
        yet confirmed) section, and invalidate any prior confirmation."""
        self._confirm_btn.configure(text=f"Confirm: {label}")
        self._selected_section_id = None
        self._selected_section_name = None
        self._hide_next()
        self._set_status("Review the section above, then click Confirm.")

    def _on_confirm(self) -> None:
        label = self._dropdown.get()
        sid = self._label_to_id.get(label)
        if sid is None:
            return
        self._selected_section_id = sid
        # Recover the plain section_name (without the "(#id)" suffix) for storage.
        for sec in self._sections:
            if sec.get("section_id") == sid:
                self._selected_section_name = sec.get("section_name")
                break
        self._set_status(f"✓ Confirmed: {label}", color="#22c55e")
        self._reveal_next()

    # ── Nav ────────────────────────────────────────────────────────────────────

    def _reveal_next(self) -> None:
        self._next_btn.pack(anchor="w", padx=2)

    def _hide_next(self) -> None:
        self._next_btn.pack_forget()

    # ── Advance ────────────────────────────────────────────────────────────────

    def _on_next(self) -> None:
        if self._selected_section_id is None:
            self._set_status("Select a section before continuing.", error=True)
            return
        section_config = {
            "section_id": self._selected_section_id,
            "section_name": self._selected_section_name,
            "setup_step": 3,
        }
        # Merge into the existing encrypted config so main.py's resume logic
        # picks up setup_step=3 and lands on the ROI screen on restart.
        ConfigStore().update(section_config)
        self._on_success(section_config)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _set_status(
        self, text: str, error: bool = False, color: str | None = None
    ) -> None:
        if color:
            self._status.configure(text=text, text_color=color)
        elif error:
            self._status.configure(text=text, text_color="#ef4444")
        else:
            self._status.configure(text=text, text_color="gray")
