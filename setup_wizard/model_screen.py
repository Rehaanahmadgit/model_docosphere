"""
setup_wizard/model_screen.py — Step 4: Detection + Recognition Model Picker

Final wizard step. Detects whether this machine can accelerate inference on a
GPU (via onnxruntime's available execution providers) and offers a small set of
detection+recognition model *tiers* — a lightweight CPU-friendly pair through a
heavier GPU-oriented pair. The hardware-appropriate tier is pre-selected, but
the admin can override.

On "Finish Setup" the chosen tier is persisted and the corresponding ONNX weight
files are downloaded (on a background thread, with a progress bar) into a
writable models directory next to config.enc. Each file's SHA-256 is verified
against a pinned digest; a mismatch or network error surfaces a clear message
and a Retry, and never advances the wizard or marks setup complete. setup_step
is only bumped to 4 (by main._on_setup_complete via on_success) once every weight
file is on disk.

This step only *selects the tier and fetches the weights*; the actual inference
wiring lives in service/detection.py and service/recognition.py (a later step),
which read the model paths this screen writes into the config.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import customtkinter as ctk
import requests

from config.store import ConfigStore

# ── Model registry ─────────────────────────────────────────────────────────────
# Real, publicly hosted ONNX weights from the OpenCV Model Zoo. URLs, byte sizes
# and SHA-256 digests were captured directly from the served files, so the pinned
# checksums are authoritative — a download that doesn't match is rejected as
# corrupt/tampered rather than silently accepted.
_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"

MODEL_FILES: dict[str, dict] = {
    "yunet_fp32": {
        "filename": "face_detection_yunet_2023mar.onnx",
        "url": f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        "size": 232589,
    },
    "yunet_int8": {
        "filename": "face_detection_yunet_2023mar_int8.onnx",
        "url": f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar_int8.onnx",
        "sha256": "321aa5a6afabf7ecc46a3d06bfab2b579dc96eb5c3be7edd365fa04502ad9294",
        "size": 100416,
    },
    "sface_fp32": {
        "filename": "face_recognition_sface_2021dec.onnx",
        "url": f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        "size": 38696353,
    },
    "sface_int8": {
        "filename": "face_recognition_sface_2021dec_int8.onnx",
        "url": f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec_int8.onnx",
        "sha256": "2b0e941e6f16cc048c20aee0c8e31f569118f65d702914540f7bfdc14048d78a",
        "size": 9896933,
    },
}

# Tiers pair one detector with one recognizer. Ordered lightest → heaviest.
MODEL_TIERS: list[dict] = [
    {
        "id": "lite",
        "name": "Lite",
        "detector": "yunet_int8",
        "recognizer": "sface_int8",
        "for_gpu": False,
        "summary": "Quantized models — smallest download, lowest CPU load. "
                   "Best for older or CPU-only machines.",
    },
    {
        "id": "standard",
        "name": "Standard",
        "detector": "yunet_fp32",
        "recognizer": "sface_int8",
        "for_gpu": False,
        "summary": "Full-precision detector with a quantized recognizer — "
                   "balanced accuracy and speed. Recommended for most CPUs.",
    },
    {
        "id": "accurate",
        "name": "Accurate",
        "detector": "yunet_fp32",
        "recognizer": "sface_fp32",
        "for_gpu": True,
        "summary": "Full-precision detection and recognition — highest accuracy. "
                   "Recommended when a GPU is available.",
    },
]


def _tier_download_bytes(tier: dict) -> int:
    """Total bytes fetched for a tier (both weight files)."""
    return (
        MODEL_FILES[tier["detector"]]["size"]
        + MODEL_FILES[tier["recognizer"]]["size"]
    )


def _recommend_tier(has_gpu: bool) -> str:
    """Return the tier id to pre-select for the detected hardware."""
    return "accurate" if has_gpu else "standard"


def _detect_hardware() -> tuple[str, str, bool]:
    """
    Inspect onnxruntime's execution providers to decide CPU vs GPU.

    Returns (human_label, provider_key, has_gpu). provider_key is one of
    "cuda" | "dml" | "cpu" and is persisted so the inference step can request
    the matching execution provider. onnxruntime may be missing at import time
    (it isn't needed until inference runs), so any failure degrades to CPU.
    """
    try:
        import onnxruntime as ort  # imported lazily; optional at this stage
        providers = ort.get_available_providers()
    except Exception:
        return ("Could not query GPU — will use CPU.", "cpu", False)

    if "CUDAExecutionProvider" in providers:
        return ("GPU detected (NVIDIA CUDA) — acceleration available.", "cuda", True)
    if "DmlExecutionProvider" in providers:
        return ("GPU detected (DirectML) — acceleration available.", "dml", True)
    return ("No GPU detected — will run on CPU.", "cpu", False)


def _models_dir() -> Path:
    """
    Writable, persistent directory for model weights, mirroring config.store's
    location so it survives across launches and stays writable inside the frozen
    .exe (unlike the read-only PyInstaller bundle dir).
    """
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / "NexusAttendanceAgent" / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_valid(path: Path, spec: dict) -> bool:
    """True if the file already exists on disk with the expected checksum."""
    try:
        return path.exists() and _sha256(path) == spec["sha256"]
    except Exception:
        return False


class ModelScreen(ctk.CTkFrame):
    """Step 4 of the setup wizard — model tier selection and weight download."""

    def __init__(self, master: ctk.CTk, on_success, roi_config: dict):
        super().__init__(master, fg_color="transparent")
        self._on_success = on_success
        self._roi_config = roi_config or {}

        self._hw_label, self._provider, self._has_gpu = _detect_hardware()
        self._recommended = _recommend_tier(self._has_gpu)
        self._selected = ctk.StringVar(value=self._recommended)
        self._downloading = False

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        ctk.CTkLabel(
            self,
            text="Step 4 of 4  —  AI Model",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", padx=2, pady=(0, 4))

        ctk.CTkLabel(
            self,
            text="Choose a Model",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w", padx=2, pady=(0, 2))

        # Hardware detection result.
        hw_color = "#22c55e" if self._has_gpu else "gray"
        ctk.CTkLabel(
            self,
            text=self._hw_label,
            font=ctk.CTkFont(size=12),
            text_color=hw_color,
        ).pack(anchor="w", padx=2, pady=(0, 10))

        # ── Bottom navigation bar ───────────────────────────────────────────
        # Packed (side="bottom") BEFORE the content so it reserves its space
        # first — same fix as camera_screen.py / roi_screen.py: content clips
        # rather than the Finish button being pushed off-screen. The button is
        # revealed once a valid tier is selected (one is pre-selected below).
        self._nav = ctk.CTkFrame(self, fg_color="transparent")
        self._nav.pack(side="bottom", fill="x", pady=(8, 0))

        self._finish_btn = ctk.CTkButton(
            self._nav,
            text="Finish Setup",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=430,
            height=44,
            command=self._on_finish,
        )
        # Not packed yet — revealed by _reveal_finish() once a tier is chosen.

        # Progress area sits just above the nav bar; also bottom-packed so it
        # keeps its place regardless of how tall the tier list is. Hidden until
        # a download starts.
        self._progress_wrap = ctk.CTkFrame(self, fg_color="transparent")
        self._progress_bar = ctk.CTkProgressBar(self._progress_wrap, width=430)
        self._progress_bar.set(0)
        self._progress_bar.pack(anchor="w", padx=2, pady=(2, 2))
        self._progress_label = ctk.CTkLabel(
            self._progress_wrap, text="", font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        self._progress_label.pack(anchor="w", padx=2)

        self._status = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color="gray"
        )
        self._status.pack(side="bottom", anchor="w", padx=2, pady=(6, 2))

        # ── Content: the tier options ───────────────────────────────────────
        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.pack(side="top", fill="both", expand=True)

        self._radios: list[ctk.CTkRadioButton] = []
        for tier in MODEL_TIERS:
            self._build_tier_card(tier)

        self._reveal_finish()  # a tier is pre-selected, so allow finishing now

    def _build_tier_card(self, tier: dict) -> None:
        card = ctk.CTkFrame(self._content)
        card.pack(fill="x", padx=2, pady=4)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(8, 0))

        size_mb = _tier_download_bytes(tier) / (1024 * 1024)
        title = f"{tier['name']}   ·   {size_mb:.1f} MB download"
        radio = ctk.CTkRadioButton(
            header,
            text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
            variable=self._selected,
            value=tier["id"],
            command=self._on_select,
        )
        radio.pack(side="left")
        self._radios.append(radio)

        if tier["id"] == self._recommended:
            ctk.CTkLabel(
                header,
                text="  Recommended",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="#22c55e",
            ).pack(side="left")

        ctk.CTkLabel(
            card,
            text=tier["summary"],
            font=ctk.CTkFont(size=12),
            text_color="gray",
            justify="left",
            wraplength=430,
        ).pack(anchor="w", padx=10, pady=(0, 8))

    # ── Selection / nav gating ─────────────────────────────────────────────────

    def _on_select(self) -> None:
        # A tier is always selected here (radios share one variable), so just
        # make sure the Finish button is available. Idempotent.
        if not self._downloading:
            self._reveal_finish()

    def _reveal_finish(self) -> None:
        if self._selected.get():
            self._finish_btn.pack(anchor="w", padx=2)

    def _hide_finish(self) -> None:
        self._finish_btn.pack_forget()

    def _current_tier(self) -> dict | None:
        return next(
            (t for t in MODEL_TIERS if t["id"] == self._selected.get()), None
        )

    # ── Finish / download ───────────────────────────────────────────────────────

    def _on_finish(self) -> None:
        if self._downloading:
            return
        tier = self._current_tier()
        if tier is None:
            self._set_status("Select a model before finishing.", error=True)
            return

        # Persist the selection immediately (without setup_step) so a wizard
        # resumed mid-download remembers the chosen tier. setup_step is only
        # advanced to 4 after every weight file is verified on disk.
        ConfigStore().update({
            "model_tier": tier["id"],
            "detector_model": tier["detector"],
            "recognizer_model": tier["recognizer"],
            "execution_provider": self._provider,
        })

        self._begin_download(tier)

    def _begin_download(self, tier: dict) -> None:
        self._downloading = True
        self._hide_finish()
        for r in self._radios:
            r.configure(state="disabled")
        self._progress_bar.set(0)
        self._progress_wrap.pack(side="bottom", anchor="w", pady=(4, 4))
        self._set_status("Downloading model files…")

        threading.Thread(
            target=self._download_worker, args=(tier,), daemon=True
        ).start()

    def _download_worker(self, tier: dict) -> None:
        """Runs off the UI thread: fetch + verify both weight files."""
        try:
            total = _tier_download_bytes(tier)
            done = 0
            paths: dict[str, str] = {}
            targets = (
                ("detector_path", tier["detector"]),
                ("recognizer_path", tier["recognizer"]),
            )
            for role, key in targets:
                spec = MODEL_FILES[key]
                dest = _models_dir() / spec["filename"]

                # Skip files already present and intact (fast resume / retry).
                if _is_valid(dest, spec):
                    done += spec["size"]
                    paths[role] = str(dest)
                    self._post_progress(done, total)
                    continue

                done = self._download_file(spec, dest, done, total)
                paths[role] = str(dest)

            model_config = {
                "model_tier": tier["id"],
                "detector_model": tier["detector"],
                "recognizer_model": tier["recognizer"],
                "execution_provider": self._provider,
                **paths,
            }
            self.after(0, self._on_download_ok, model_config)
        except Exception as exc:
            self.after(0, self._on_download_err, str(exc))

    def _download_file(
        self, spec: dict, dest: Path, done: int, total: int
    ) -> int:
        """Stream one file to a .part temp, verify checksum, then atomically move."""
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with requests.get(spec["url"], stream=True, timeout=30) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        done += len(chunk)
                        self._post_progress(done, total)

            if _sha256(tmp) != spec["sha256"]:
                raise RuntimeError(
                    f"Checksum mismatch for {spec['filename']} — the download "
                    "may be corrupted. Please retry."
                )
            os.replace(tmp, dest)  # atomic; only a verified file lands at dest
            return done
        except Exception:
            # Never leave a partial/bad file behind — retry starts clean.
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    # ── UI callbacks (main thread) ──────────────────────────────────────────────

    def _post_progress(self, done: int, total: int) -> None:
        self.after(0, self._set_progress, done, total)

    def _set_progress(self, done: int, total: int) -> None:
        if not self.winfo_exists():
            return
        frac = min(1.0, done / total) if total else 0.0
        self._progress_bar.set(frac)
        self._progress_label.configure(
            text=f"{frac * 100:.0f}%   "
                 f"({done / (1024 * 1024):.1f} / {total / (1024 * 1024):.1f} MB)"
        )

    def _on_download_ok(self, model_config: dict) -> None:
        if not self.winfo_exists():
            return
        self._downloading = False
        self._progress_bar.set(1.0)
        self._set_status("✓ Models downloaded — finishing setup…", color="#22c55e")
        # on_success (main._on_setup_complete) merges this, sets setup_step=4,
        # and completes the wizard.
        self._on_success(model_config)

    def _on_download_err(self, message: str) -> None:
        if not self.winfo_exists():
            return
        self._downloading = False
        self._progress_wrap.pack_forget()
        for r in self._radios:
            r.configure(state="normal")
        self._set_status(f"✗ {message}", error=True)
        self._finish_btn.configure(text="Retry Download")
        self._reveal_finish()

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
