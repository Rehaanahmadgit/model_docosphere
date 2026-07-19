"""
test_pipeline.py — standalone detection + recognition sanity check (console only).

Runs the real YuNet + SFace pipeline against either a saved image or a live RTSP
feed and prints, per processed frame, the detected face boxes/scores and any
recognition matches. No GUI — purely stdout — so pipeline logic can be validated
without the wizard.

Model paths, camera URL/credentials and the ROI are read from the encrypted
config written by the setup wizard; every value can be overridden on the command
line. Frame sampling runs at a configurable FPS (default 3), NOT the camera's
full framerate — pass --fps to change it.

    # image
    python test_pipeline.py --image path\\to\\photo.jpg
    # live camera from saved config, ~3 fps, 30 frames, with a gallery
    python test_pipeline.py --rtsp auto --fps 3 --max-frames 30 --gallery students.json

This script runs on Windows only (needs the downloaded models + opencv); run it
there and report the console output.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

import cv2

from config.store import ConfigStore
from service.detection import FaceDetector
from service.recognition import FaceRecognizer

# FFMPEG capture options: force TCP transport (more reliable than UDP over WiFi)
# and cap the socket timeout so a wrong/unreachable host fails fast instead of
# hanging indefinitely. stimeout is in microseconds.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000"
)

# Default sampling rate — kept well below camera framerate (requirement: 2-5 fps,
# configurable, not hardcoded into the pipeline). Overridable via --fps / config.
_DEFAULT_SAMPLE_FPS = 3.0


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


def _print_frame_results(tag: str, faces, recognizer, frame, sim_threshold: float) -> None:
    if not faces:
        print(f"[{tag}] no faces detected")
        return
    print(f"[{tag}] {len(faces)} face(s):")
    for i, face in enumerate(faces):
        line = (
            f"    #{i}  box=({face.x},{face.y},{face.w},{face.h})  "
            f"score={face.confidence:.3f}"
        )
        if recognizer is not None and recognizer.gallery_size:
            match = recognizer.identify(frame, face)
            if match is not None:
                line += (
                    f"  ->  student_id={match.student_id} "
                    f"(sim={match.confidence:.3f} >= {sim_threshold:.3f})"
                )
            else:
                line += "  ->  no match"
        print(line)


def _run_image(path, detector, recognizer, roi, sim_threshold) -> int:
    frame = cv2.imread(path)
    if frame is None:
        print(f"✗ Could not read image: {path}")
        return 2
    print(f"Image loaded: {path}  ({frame.shape[1]}x{frame.shape[0]})")
    faces = detector.detect(frame, roi)
    _print_frame_results("image", faces, recognizer, frame, sim_threshold)
    return 0


def _run_rtsp(url, detector, recognizer, roi, sim_threshold, fps, max_frames) -> int:
    print(
        f"Opening RTSP stream with transport="
        f"{os.environ.get('OPENCV_FFMPEG_CAPTURE_OPTIONS', '<default>')}"
    )
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        print("✗ Could not open the RTSP stream.")
        return 2

    interval = 1.0 / fps if fps > 0 else 0.0
    print(f"Streaming — sampling ~{fps:.1f} fps (every {interval:.2f}s), "
          f"max {max_frames} frames. Ctrl-C to stop.")
    processed = 0
    next_due = 0.0
    try:
        while processed < max_frames:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("✗ Lost the stream (no frame).")
                break
            now = time.monotonic()
            if now < next_due:
                continue  # drop frames between sample ticks (throttle to target fps)
            next_due = now + interval
            processed += 1
            faces = detector.detect(frame, roi)
            _print_frame_results(f"frame {processed}", faces, recognizer, frame, sim_threshold)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        cap.release()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Detection + recognition pipeline test")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", help="Path to a test image.")
    src.add_argument(
        "--rtsp",
        help="RTSP URL, or 'auto' to use the camera saved in config.",
    )
    parser.add_argument("--detector", help="Override YuNet .onnx path.")
    parser.add_argument("--recognizer", help="Override SFace .onnx path.")
    parser.add_argument("--gallery", help="JSON gallery: student_id -> embedding.")
    parser.add_argument("--fps", type=float, help="Sampling rate for RTSP.")
    parser.add_argument("--max-frames", type=int, default=30, help="RTSP frames to process.")
    parser.add_argument("--conf", type=float, default=0.75, help="Detection score threshold.")
    parser.add_argument("--sim-threshold", type=float, help="Recognition cosine threshold.")
    parser.add_argument("--no-roi", action="store_true", help="Ignore the saved ROI.")
    args = parser.parse_args(argv)

    cfg = _load_config()

    detector_path = args.detector or cfg.get("detector_path")
    recognizer_path = args.recognizer or cfg.get("recognizer_path")
    provider = cfg.get("execution_provider", "cpu")
    roi = None if args.no_roi else cfg.get("roi")
    fps = args.fps or float(cfg.get("sample_fps", _DEFAULT_SAMPLE_FPS))

    if not detector_path:
        print("✗ No detector model path (pass --detector or finish setup Step 4).")
        return 2
    print(f"Detector : {detector_path}")
    print(f"Provider : {provider}")
    print(f"ROI      : {roi if roi else 'full frame'}")

    detector = FaceDetector(
        detector_path, confidence_threshold=args.conf, execution_provider=provider
    )
    try:
        detector.load()
    except Exception as exc:
        print(f"✗ Failed to load detector: {exc}")
        return 2

    recognizer = None
    if recognizer_path:
        recognizer = FaceRecognizer(recognizer_path, execution_provider=provider)
        if args.sim_threshold is not None:
            recognizer.similarity_threshold = args.sim_threshold
        try:
            recognizer.load()
        except Exception as exc:
            print(f"! Failed to load recognizer ({exc}); detection only.")
            recognizer = None
        if recognizer is not None and args.gallery:
            try:
                recognizer.load_gallery_file(args.gallery)
                print(f"Gallery  : {recognizer.gallery_size} student(s) "
                      f"from {args.gallery}")
            except Exception as exc:
                print(f"! Could not load gallery ({exc}); recognition disabled.")
        elif recognizer is not None:
            print("Gallery  : none (recognition will report 'no match')")
    else:
        print("! No recognizer path — running detection only.")

    sim_threshold = recognizer.similarity_threshold if recognizer else 0.0

    if args.image:
        return _run_image(args.image, detector, recognizer, roi, sim_threshold)

    url = _resolve_rtsp_url(cfg) if args.rtsp == "auto" else args.rtsp
    if not url:
        print("✗ No RTSP URL (pass --rtsp <url> or finish Step 2).")
        return 2
    return _run_rtsp(url, detector, recognizer, roi, sim_threshold, fps, args.max_frames)


if __name__ == "__main__":
    sys.exit(main())
