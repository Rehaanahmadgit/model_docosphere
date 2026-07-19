"""
generate_test_gallery.py — build a one-off gallery.json for test_pipeline.py.

There is no real backend enrollment flow yet, so this script fakes it: take a
single photo (file or one RTSP frame), run it through the real YuNet + SFace
pipeline, and write the resulting embedding out as a gallery.json that
test_pipeline.py's --gallery flag can consume directly.

    # from a photo
    python generate_test_gallery.py --image path\\to\\photo.jpg --id 42

    # from the configured camera
    python generate_test_gallery.py --rtsp auto --id 42 --out gallery.json

Model paths and camera URL/credentials are read from the encrypted config
written by the setup wizard, same as test_pipeline.py; both can be overridden
on the command line.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

import cv2

DEBUG_CAPTURE_PATH = "debug_capture.jpg"
_RTSP_WARMUP_FRAMES = 8
_RTSP_COUNTDOWN_SECONDS = 3

# FFMPEG capture options: force TCP transport (more reliable than UDP over WiFi)
# and cap the socket timeout so a wrong/unreachable host fails fast instead of
# hanging indefinitely. stimeout is in microseconds. Set immediately before
# the cv2.VideoCapture(...) call that needs it (not at module level) — the
# FFmpeg backend reads this env var lazily, so setting it at import time races
# against whichever module happens to import cv2 first.
_RTSP_FFMPEG_OPTS = "rtsp_transport;tcp|stimeout;5000000"

from config.store import ConfigStore
from service.detection import FaceDetector
from service.recognition import FaceRecognizer


def _load_config() -> dict:
    try:
        return ConfigStore().load() or {}
    except Exception as exc:
        print(f"! Could not load config ({exc}); relying on CLI arguments.")
        return {}


def _resolve_rtsp_url(cfg: dict) -> Optional[str]:
    """Compose the authenticated RTSP URL from saved config (same as the wizard)."""
    url = cfg.get("rtsp_url", "")
    if not url:
        return None
    try:
        from setup_wizard.camera_screen import _compose_connection_url
        return _compose_connection_url(
            url, cfg.get("camera_username", ""), cfg.get("camera_password", "")
        )
    except Exception:
        return url


def _capture_rtsp_frame(url: str):
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = _RTSP_FFMPEG_OPTS
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    print(f"Opened RTSP stream with transport={_RTSP_FFMPEG_OPTS}")
    if not cap.isOpened():
        print("✗ Could not open the RTSP stream.")
        return None
    print("Stream opened.")
    try:
        # RTSP streams often hand back stale/corrupted frames right after
        # opening (buffered from before the decoder caught up) — burn a few
        # before trusting any of them.
        for _ in range(_RTSP_WARMUP_FRAMES):
            cap.read()

        for remaining in range(_RTSP_COUNTDOWN_SECONDS, 0, -1):
            print(f"Capturing in {remaining}...")
            time.sleep(1)

        ok, frame = cap.read()
        print("Frame captured.")
    finally:
        cap.release()
    if not ok or frame is None:
        print("✗ Could not read a frame from the RTSP stream.")
        return None
    return frame


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a test gallery.json (student_id -> embedding) for test_pipeline.py"
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", help="Path to a photo containing the student's face.")
    src.add_argument("--rtsp", help="RTSP URL, or 'auto' to use the camera saved in config.")
    parser.add_argument("--id", default="test_student", help="Student id key in the gallery.")
    parser.add_argument("--out", default="gallery.json", help="Output gallery JSON path.")
    parser.add_argument("--detector", help="Override YuNet .onnx path.")
    parser.add_argument("--recognizer", help="Override SFace .onnx path.")
    parser.add_argument("--conf", type=float, default=0.75, help="Detection score threshold.")
    args = parser.parse_args(argv)

    cfg = _load_config()

    detector_path = args.detector or cfg.get("detector_path")
    recognizer_path = args.recognizer or cfg.get("recognizer_path")
    provider = cfg.get("execution_provider", "cpu")

    if not detector_path:
        print("✗ No detector model path (pass --detector or finish setup Step 4).")
        return 2
    if not recognizer_path:
        print("✗ No recognizer model path (pass --recognizer or finish setup Step 4).")
        return 2

    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            print(f"✗ Could not read image: {args.image}")
            return 2
        print(f"Image loaded: {args.image}  ({frame.shape[1]}x{frame.shape[0]})")
    else:
        url = _resolve_rtsp_url(cfg) if args.rtsp == "auto" else args.rtsp
        if not url:
            print("✗ No RTSP URL (pass --rtsp <url> or finish Step 2).")
            return 2
        frame = _capture_rtsp_frame(url)
        if frame is None:
            return 2
        print(f"Frame captured from RTSP  ({frame.shape[1]}x{frame.shape[0]})")

    cv2.imwrite(DEBUG_CAPTURE_PATH, frame)
    print(f"Raw captured frame saved to {DEBUG_CAPTURE_PATH} for inspection.")

    detector = FaceDetector(detector_path, confidence_threshold=args.conf, execution_provider=provider)
    try:
        detector.load()
    except Exception as exc:
        print(f"✗ Failed to load detector: {exc}")
        return 2

    recognizer = FaceRecognizer(recognizer_path, execution_provider=provider)
    try:
        recognizer.load()
    except Exception as exc:
        print(f"✗ Failed to load recognizer: {exc}")
        return 2

    faces = detector.detect(frame)
    if not faces:
        print("✗ No face detected in the input — refusing to write an empty gallery entry.")
        return 1

    # detect() sorts by descending confidence, so the first face is the best.
    best = faces[0]
    print(f"Face found: box=({best.x},{best.y},{best.w},{best.h})  score={best.confidence:.3f}")
    if len(faces) > 1:
        print(f"! {len(faces)} faces detected — using the highest-confidence one.")

    embedding = recognizer.embed(frame, best)

    gallery = {str(args.id): embedding.tolist()}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(gallery, f, indent=2)

    print(f"✓ Wrote embedding for student_id={args.id} to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
