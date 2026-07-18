"""
setup_wizard/roi_screen.py — Step 4: ROI (Region of Interest) Selector

Grabs a single frame from the camera configured in Step 2 (the RTSP URL and
credentials already stored in the encrypted ConfigStore) and lets the admin
click-drag a rectangle over it to mark the detection zone. Only faces inside
this region are processed later, which cuts CPU load and avoids false positives
at the frame edges.

The rectangle is drawn on a Tk canvas — not via cv2.imshow, because the app
ships with opencv-python-headless (no HighGUI). The frame is handed to Tk as a
base64 PNG, so no PIL.ImageTk dependency is needed.

The ROI is saved as *fractions* of the frame width/height rather than absolute
pixels, so the zone stays valid if the camera resolution changes later.
"""
from __future__ import annotations

import base64
import threading
import tkinter

import customtkinter as ctk
import cv2

from config.store import ConfigStore

# Reuse Step 2's URL composition so credentials are injected identically.
# Importing camera_screen also applies its OPENCV_FFMPEG_CAPTURE_OPTIONS default
# (TCP transport + socket timeout), so a dead camera fails fast here too.
from setup_wizard.camera_screen import _compose_connection_url

# Frame is scaled to fit this box on screen (keeps the wizard inside its window).
_CANVAS_MAX_W = 468
_CANVAS_MAX_H = 290

# Reject a drawn box smaller than this fraction of the frame in either axis —
# guards against an accidental click (zero/near-zero area) counting as an ROI.
_MIN_ROI_FRAC = 0.02

_RECT_COLOR = "#22c55e"


def _roi_fraction_box(
    x0: float, y0: float, x1: float, y1: float,
    disp_w: int, disp_h: int, min_frac: float = _MIN_ROI_FRAC,
):
    """
    Convert two drag corners (in displayed-image pixels) into a normalized ROI.

    Because the displayed image is a uniform downscale of the source frame, a
    fraction measured on the displayed image equals the same fraction of the
    real frame — so the result is resolution-independent.

    Returns (roi_fractions, (left, top, right, bottom)) with the corners sorted,
    or None if the box is degenerate / below the minimum size.
    """
    if disp_w <= 0 or disp_h <= 0:
        return None
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    fw = (right - left) / disp_w
    fh = (bottom - top) / disp_h
    if fw < min_frac or fh < min_frac:
        return None
    roi = {
        "x": round(left / disp_w, 6),
        "y": round(top / disp_h, 6),
        "w": round(fw, 6),
        "h": round(fh, 6),
    }
    return roi, (left, top, right, bottom)


class ROIScreen(ctk.CTkFrame):
    """Step 4 of the setup wizard — draw the detection ROI on a camera frame."""

    def __init__(self, master: ctk.CTk, on_success, camera_config: dict):
        super().__init__(master, fg_color="transparent")
        self._on_success = on_success
        self._camera_config = camera_config or {}

        # Drawing / result state.
        self._canvas: tkinter.Canvas | None = None
        self._photo: tkinter.PhotoImage | None = None  # keep a ref (avoid GC)
        self._rect_id: int | None = None
        self._start: tuple[int, int] | None = None
        self._disp_w = 0
        self._disp_h = 0
        self._roi: dict | None = None

        self._build_ui()
        self._start_grab()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        ctk.CTkLabel(
            self,
            text="Step 4 of 5  —  Detection Zone",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", padx=2, pady=(0, 4))

        ctk.CTkLabel(
            self,
            text="Mark the Detection Zone",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w", padx=2, pady=(0, 2))

        ctk.CTkLabel(
            self,
            text="Click and drag on the frame to draw the area to monitor. "
                 "Drag again to redraw.",
            font=ctk.CTkFont(size=13),
            text_color="gray",
            justify="left",
            wraplength=468,
        ).pack(anchor="w", padx=2, pady=(0, 8))

        self._status = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color="gray"
        )
        self._status.pack(anchor="w", padx=2, pady=(0, 6))

        # ── Bottom navigation bar ───────────────────────────────────────────
        # Packed (side="bottom") BEFORE the content area so it reserves its
        # space first — same fix as camera_screen.py: the frame/canvas clips
        # rather than the Next button being pushed off-screen. Empty until a
        # valid ROI is drawn.
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
        # Not packed into the bar yet — revealed only when a valid ROI exists.

        # ── Content area (frame preview / loading / error) ──────────────────
        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.pack(side="top", fill="both", expand=True)

    # ── Frame acquisition ─────────────────────────────────────────────────────

    def _connection_url(self) -> str | None:
        """Compose the authenticated stream URL from the stored camera config."""
        cfg = ConfigStore().load() or {}
        url = cfg.get("rtsp_url") or self._camera_config.get("rtsp_url", "")
        if not url:
            return None
        user = cfg.get("camera_username") or self._camera_config.get(
            "camera_username", ""
        )
        pw = cfg.get("camera_password") or self._camera_config.get(
            "camera_password", ""
        )
        return _compose_connection_url(url, user, pw)

    def _start_grab(self) -> None:
        self._hide_next()
        self._roi = None
        self._clear_content()
        connect_url = self._connection_url()
        if not connect_url:
            self._show_error(
                "No camera is configured. Go back to Step 2 and set up the camera."
            )
            return

        ctk.CTkLabel(
            self._content,
            text="Loading a frame from the camera…",
            text_color="gray",
        ).pack(anchor="w", padx=2, pady=8)
        self._set_status("Connecting to camera…")

        threading.Thread(
            target=self._grab, args=(connect_url,), daemon=True
        ).start()

    def _grab(self, connect_url: str) -> None:
        """Runs off the UI thread: open the stream and read one frame."""
        cap = None
        try:
            cap = cv2.VideoCapture(connect_url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                self.after(
                    0, self._show_error,
                    "Could not open the camera stream. Check the camera is "
                    "online and reachable on this network.",
                )
                return
            ok, frame = cap.read()
            if not ok or frame is None:
                self.after(
                    0, self._show_error,
                    "Connected, but no frame was received from the camera.",
                )
                return
            self.after(0, self._on_frame, frame)
        except Exception as exc:  # never let a capture error crash the wizard
            self.after(0, self._show_error, f"Failed to read from camera: {exc}")
        finally:
            if cap is not None:
                cap.release()

    # ── Rendering ──────────────────────────────────────────────────────────────

    def _on_frame(self, frame) -> None:
        try:
            self._render_canvas(frame)
        except Exception as exc:
            self._show_error(f"Could not display the frame: {exc}")
            return
        self._set_status("Draw a rectangle over the area to monitor.")

    def _render_canvas(self, frame) -> None:
        self._clear_content()

        h, w = frame.shape[:2]
        scale = min(_CANVAS_MAX_W / w, _CANVAS_MAX_H / h, 1.0)
        self._disp_w = max(1, int(w * scale))
        self._disp_h = max(1, int(h * scale))

        disp = cv2.resize(
            frame, (self._disp_w, self._disp_h), interpolation=cv2.INTER_AREA
        )
        # cv2 encodes its native BGR buffer to a correct RGB PNG, so no manual
        # colour conversion is needed. Tk 8.6 reads base64 PNG via data=.
        ok, buf = cv2.imencode(".png", disp)
        if not ok:
            raise RuntimeError("frame PNG encoding failed")
        self._photo = tkinter.PhotoImage(data=base64.b64encode(buf.tobytes()))

        self._canvas = tkinter.Canvas(
            self._content,
            width=self._disp_w,
            height=self._disp_h,
            highlightthickness=0,
            bd=0,
            cursor="crosshair",
        )
        self._canvas.pack(anchor="w", padx=2)
        self._canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self._canvas.bind("<Button-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)

        self._rect_id = None
        self._start = None

    def _show_error(self, message: str) -> None:
        self._clear_content()
        self._set_status(f"✗ {message}", error=True)
        ctk.CTkLabel(
            self._content,
            text=message,
            text_color="#ef4444",
            wraplength=460,
            justify="left",
        ).pack(anchor="w", padx=2, pady=(4, 10))
        ctk.CTkButton(
            self._content,
            text="Retry",
            width=140,
            height=38,
            command=self._start_grab,
        ).pack(anchor="w", padx=2)

    def _clear_content(self) -> None:
        for child in self._content.winfo_children():
            child.destroy()
        self._canvas = None
        self._rect_id = None

    # ── Rectangle drawing ──────────────────────────────────────────────────────

    def _clamp(self, event) -> tuple[int, int]:
        x = min(max(int(event.x), 0), self._disp_w)
        y = min(max(int(event.y), 0), self._disp_h)
        return x, y

    def _on_press(self, event) -> None:
        if self._canvas is None:
            return
        # Starting a new box invalidates the previous ROI until release.
        self._roi = None
        self._hide_next()
        x, y = self._clamp(event)
        self._start = (x, y)
        if self._rect_id is not None:
            self._canvas.delete(self._rect_id)
        self._rect_id = self._canvas.create_rectangle(
            x, y, x, y, outline=_RECT_COLOR, width=2
        )

    def _on_drag(self, event) -> None:
        if self._canvas is None or self._rect_id is None or self._start is None:
            return
        x, y = self._clamp(event)
        x0, y0 = self._start
        self._canvas.coords(self._rect_id, x0, y0, x, y)

    def _on_release(self, event) -> None:
        if self._canvas is None or self._rect_id is None or self._start is None:
            return
        x, y = self._clamp(event)
        x0, y0 = self._start
        result = _roi_fraction_box(x0, y0, x, y, self._disp_w, self._disp_h)
        if result is None:
            self._roi = None
            self._hide_next()
            self._set_status(
                "That box is too small — drag a larger detection zone.",
                error=True,
            )
            return
        roi, (left, top, right, bottom) = result
        self._roi = roi
        # Snap the overlay to the normalized (sorted) corners.
        self._canvas.coords(self._rect_id, left, top, right, bottom)
        self._set_status(
            "✓ Detection zone set. Redraw to adjust, or continue.",
            color="#22c55e",
        )
        self._reveal_next()

    # ── Nav (same reserved-bar pattern as camera_screen.py) ─────────────────────

    def _reveal_next(self) -> None:
        """Reveal the Next button inside the space-reserved bottom nav bar."""
        self._next_btn.pack(anchor="w", padx=2)

    def _hide_next(self) -> None:
        """Hide Next again whenever the current ROI becomes invalid."""
        self._next_btn.pack_forget()

    # ── Advance ────────────────────────────────────────────────────────────────

    def _on_next(self) -> None:
        if not self._roi:
            self._set_status("Draw the detection zone before continuing.", error=True)
            return
        roi_config = {"roi": self._roi, "setup_step": 4}
        # Merge into the existing encrypted config so main.py's resume logic
        # picks up setup_step=4 and lands on the model screen on restart.
        ConfigStore().update(roi_config)
        self._on_success(roi_config)

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
