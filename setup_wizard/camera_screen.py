"""
setup_wizard/camera_screen.py — Step 2: RTSP/IP Camera Setup + Live Preview

Production deployments always point at a network (RTSP) security camera — there
is no local-webcam path. The admin pastes the camera's RTSP URL and, if the URL
doesn't already embed them, an optional username/password. "Test Connection"
opens the stream with OpenCV and grabs a single frame; on success the frame is
shown as a live preview and the "Next" button unlocks.

Credentials are never persisted inside the URL string. They are split out and
stored as separate fields in the shared ConfigStore, which Fernet-encrypts the
whole config at rest with a machine-bound key (same guarantee as Step 1). The
background service recomposes the authenticated URL from these fields at runtime.
"""
from __future__ import annotations

import os
import threading
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import customtkinter as ctk
import cv2
from PIL import Image

from config.store import ConfigStore

# FFMPEG capture options: force TCP transport (more reliable than UDP over WiFi)
# and cap the socket timeout so a wrong/unreachable host fails fast instead of
# hanging the test thread indefinitely. stimeout is in microseconds.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000"
)

_PREVIEW_MAX_W = 380
_PREVIEW_MAX_H = 150


def _split_credentials(
    raw_url: str, username: str, password: str
) -> tuple[str, str, str]:
    """
    Return (sanitized_url, username, password).

    Credentials embedded directly in the URL (rtsp://user:pass@host/…) take
    precedence and are stripped out into the returned fields; otherwise the
    separately-entered username/password are used. The sanitized URL never
    carries credentials, so it is safe to store, log, or display.
    """
    parts = urlsplit(raw_url)
    if parts.username:
        username = unquote(parts.username)
        password = unquote(parts.password or "")

    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    sanitized = urlunsplit(
        (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
    )
    return sanitized, username, password


def _compose_connection_url(sanitized_url: str, username: str, password: str) -> str:
    """Re-inject credentials into a sanitized URL to build the URL OpenCV opens."""
    if not username:
        return sanitized_url
    parts = urlsplit(sanitized_url)
    userinfo = quote(username, safe="")
    if password:
        userinfo += ":" + quote(password, safe="")
    host = parts.hostname or ""
    netloc = f"{userinfo}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit(
        (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
    )


class CameraScreen(ctk.CTkFrame):
    """Step 2 of the setup wizard — RTSP camera connection and live preview."""

    def __init__(self, master: ctk.CTk, on_success, org_info: dict):
        super().__init__(master, fg_color="transparent")
        self._on_success = on_success
        self._org_info = org_info

        # Populated by a successful test; consumed on Next.
        self._verified_url: str | None = None
        self._verified_user: str = ""
        self._verified_pw: str = ""
        self._preview_image: ctk.CTkImage | None = None  # keep a ref (avoid GC)

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        ctk.CTkLabel(
            self,
            text="Step 2 of 4  —  Camera Configuration",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", padx=2, pady=(0, 4))

        ctk.CTkLabel(
            self,
            text="Connect Your Camera",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w", padx=2, pady=(0, 2))

        ctk.CTkLabel(
            self,
            text="Enter the RTSP URL of your IP / network camera.",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        ).pack(anchor="w", padx=2, pady=(0, 10))

        # ── RTSP URL ───────────────────────────────────────────────────────
        ctk.CTkLabel(
            self, text="RTSP / Camera URL", font=ctk.CTkFont(size=13, weight="bold")
        ).pack(anchor="w", padx=2)
        self._url_entry = ctk.CTkEntry(
            self,
            placeholder_text="rtsp://192.168.1.42:554/h264_ulaw.sdp",
            width=430,
            height=38,
        )
        self._url_entry.pack(anchor="w", padx=2, pady=(3, 10))

        # ── Optional credentials ───────────────────────────────────────────
        creds = ctk.CTkFrame(self, fg_color="transparent")
        creds.pack(anchor="w", fill="x", padx=0)

        user_col = ctk.CTkFrame(creds, fg_color="transparent")
        user_col.pack(side="left", padx=(2, 8))
        ctk.CTkLabel(
            user_col, text="Username (optional)",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")
        self._user_entry = ctk.CTkEntry(user_col, width=205, height=38)
        self._user_entry.pack(anchor="w", pady=(3, 0))

        pw_col = ctk.CTkFrame(creds, fg_color="transparent")
        pw_col.pack(side="left")
        ctk.CTkLabel(
            pw_col, text="Password (optional)",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")
        self._pw_entry = ctk.CTkEntry(pw_col, width=205, height=38, show="•")
        self._pw_entry.pack(anchor="w", pady=(3, 0))

        ctk.CTkLabel(
            self,
            text="Leave blank if the URL already contains user:pass@…",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", padx=2, pady=(4, 10))

        # ── Test connection ────────────────────────────────────────────────
        self._test_btn = ctk.CTkButton(
            self,
            text="Test Connection",
            font=ctk.CTkFont(size=13, weight="bold"),
            width=430,
            height=40,
            command=self._start_test,
        )
        self._test_btn.pack(anchor="w", padx=2)

        self._status = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color="gray"
        )
        self._status.pack(anchor="w", padx=2, pady=(6, 4))

        # ── Bottom navigation bar ───────────────────────────────────────────
        # Packed (side="bottom") BEFORE the preview so it reserves its space
        # first. In Tk's pack geometry a widget only gets a parcel if cavity
        # still remains when it is packed; by claiming the bottom before the
        # (large) preview is packed, the preview clips when the window is short
        # instead of the Next button being pushed off-screen — which was the
        # root cause of the "no Next button" bug. The bar stays empty until a
        # successful test packs the Next button into it.
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
        # Not packed into the bar yet — revealed only after a successful test.

        # ── Preview area ───────────────────────────────────────────────────
        # Packed last (side="top") so it fills the gap between the fields and
        # the reserved nav bar, and yields space to Next when room is tight.
        self._preview = ctk.CTkLabel(
            self,
            text="No preview yet",
            width=_PREVIEW_MAX_W,
            height=_PREVIEW_MAX_H,
            fg_color=("gray85", "gray20"),
            text_color="gray",
            corner_radius=8,
        )
        self._preview.pack(side="top", anchor="w", padx=2, pady=(0, 4))

    def _reveal_next(self) -> None:
        """Reveal the Next button inside the space-reserved bottom nav bar."""
        self._next_btn.pack(anchor="w", padx=2)

    def _hide_next(self) -> None:
        """Hide Next again when a new test invalidates the prior success."""
        self._next_btn.pack_forget()

    # ── Test-connection flow ──────────────────────────────────────────────────

    def _start_test(self) -> None:
        raw_url = self._url_entry.get().strip()
        if not raw_url:
            self._set_status("Enter the camera's RTSP URL.", error=True)
            return
        if not raw_url.lower().startswith(("rtsp://", "http://", "https://")):
            self._set_status(
                "URL must start with rtsp:// (or http:// for MJPEG).", error=True
            )
            return

        sanitized_url, username, password = _split_credentials(
            raw_url, self._user_entry.get().strip(), self._pw_entry.get()
        )
        connect_url = _compose_connection_url(sanitized_url, username, password)

        # A new test invalidates any prior success until it completes.
        self._verified_url = None
        self._hide_next()
        self._test_btn.configure(state="disabled", text="Connecting…")
        self._set_status("Opening stream — this can take a few seconds…")

        threading.Thread(
            target=self._do_test,
            args=(connect_url, sanitized_url, username, password),
            daemon=True,
        ).start()

    def _do_test(
        self, connect_url: str, sanitized_url: str, username: str, password: str
    ) -> None:
        """Runs off the UI thread: open the stream and grab one frame."""
        cap = None
        try:
            cap = cv2.VideoCapture(connect_url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                self._post_error(
                    "Could not open the stream. Check the URL is correct and "
                    "the camera is reachable on this network."
                )
                return
            ok, frame = cap.read()
            if not ok or frame is None:
                self._post_error(
                    "Connected, but no video frame was received. This usually "
                    "means wrong credentials or an incorrect stream path."
                )
                return
            self.after(0, self._on_test_ok, frame, sanitized_url, username, password)
        except Exception as exc:  # never let a capture error crash the wizard
            self._post_error(f"Connection failed: {exc}")
        finally:
            if cap is not None:
                cap.release()

    def _post_error(self, message: str) -> None:
        self.after(0, self._on_test_err, message)

    def _on_test_ok(
        self, frame, sanitized_url: str, username: str, password: str
    ) -> None:
        self._verified_url = sanitized_url
        self._verified_user = username
        self._verified_pw = password

        # Unlock advancing first — a preview hiccup must never strand the user
        # on a verified camera with no way forward.
        self._test_btn.configure(state="normal", text="Test Connection")
        self._reveal_next()

        try:
            self._render_preview(frame)
            self._set_status("✓ Camera connected — preview below.", color="#22c55e")
        except Exception:
            self._preview.configure(text="Connected — preview unavailable", image=None)
            self._set_status("✓ Camera connected.", color="#22c55e")

    def _on_test_err(self, message: str) -> None:
        self._set_status(f"✗ {message}", error=True)
        self._test_btn.configure(state="normal", text="Test Connection")

    def _render_preview(self, frame) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        scale = min(_PREVIEW_MAX_W / w, _PREVIEW_MAX_H / h, 1.0)
        size = (max(1, int(w * scale)), max(1, int(h * scale)))

        image = Image.fromarray(rgb)
        self._preview_image = ctk.CTkImage(light_image=image, dark_image=image, size=size)
        self._preview.configure(image=self._preview_image, text="")

    # ── Advance ────────────────────────────────────────────────────────────────

    def _on_next(self) -> None:
        if not self._verified_url:
            self._set_status("Test the connection before continuing.", error=True)
            return

        camera_config = {
            "rtsp_url": self._verified_url,          # sanitized, no credentials
            "camera_username": self._verified_user,  # encrypted at rest by ConfigStore
            "camera_password": self._verified_pw,    # encrypted at rest by ConfigStore
            "setup_step": 2,
        }
        # Merge into the existing (Step 1) encrypted config so resume logic in
        # main.py picks up setup_step=2 and lands on the ROI screen on restart.
        ConfigStore().update(camera_config)
        self._on_success(camera_config)

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
