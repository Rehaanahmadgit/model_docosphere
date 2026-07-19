"""
service/attendance_loop.py — Core detection + recognition + debounce loop.

Pulls frames from the configured RTSP camera at the configured sample rate,
runs each sampled frame through FaceDetector -> FaceRecognizer, and for every
match that clears the session-window debounce (service/debounce.py), enqueues
an attendance event to the local SQLite queue (sync/queue.py) — nothing here
calls the push-attendance API directly, that's the sync worker's job.

This is the core loop only: start()/stop() give it the same shape as
scheduler/ws_listener.py's on_start/on_stop callables so the Windows
Service/Task Scheduler wiring can drive it later. It does not itself decide
when to run.

Runs on Windows only (needs the downloaded models + opencv); no local test
here — review only, build/run on Windows.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import cv2

from config.store import ConfigStore
from service.debounce import AttendanceDebounce
from service.detection import FaceDetector
from service.recognition import FaceRecognizer
from sync.embeddings_cache import read_cache as _read_embeddings_cache, refresh_gallery
from sync.queue import EventQueue

_DEFAULT_SAMPLE_FPS = 3.0
_DEFAULT_SESSION_WINDOW_HOURS = 4.0
_RECONNECT_BACKOFF_START = 1.0
_RECONNECT_BACKOFF_MAX = 30.0


def load_local_gallery() -> dict:
    """
    Read the local embeddings cache (sync/embeddings_cache.py), written by the
    most recent successful sync of GET /api/agent/sync-embeddings.

    Pure read — does not talk to the network. AttendanceLoop.start() calls
    refresh_gallery() first to update the cache, then this to load it; call
    this on its own if you just want whatever was last synced.

    Returns:
        {student_id: [floats]} — same shape FaceRecognizer.load_gallery() and
        generate_test_gallery.py's gallery.json already accept. {} if nothing
        has ever synced successfully.
    """
    return _read_embeddings_cache()


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


class AttendanceLoop:
    """
    RTSP -> FaceDetector -> FaceRecognizer -> AttendanceDebounce -> EventQueue.

    Args:
        camera_id: Identifier for the camera this loop reads from. Persisted
            on every queued event (sync/queue.py's events.camera_id column).
        on_event: Optional Callable[[str, float], None] invoked with
            (student_id, confidence) whenever a new event is queued — a hook
            for the scheduler/UI layer, not required for the loop itself.
    """

    def __init__(
        self,
        camera_id: Optional[str] = None,
        on_event: Optional[Callable[[str, float], None]] = None,
    ):
        self.camera_id = camera_id
        self.on_event = on_event

        cfg = ConfigStore().load() or {}
        self._rtsp_url = _resolve_rtsp_url(cfg)
        self._roi = cfg.get("roi")
        self._fps = float(cfg.get("sample_fps", _DEFAULT_SAMPLE_FPS))
        self._section_id = cfg.get("section_id")
        self._subject_id = cfg.get("subject_id")

        provider = cfg.get("execution_provider", "cpu")
        detector_path = cfg.get("detector_path")
        recognizer_path = cfg.get("recognizer_path")
        if not detector_path:
            raise ValueError("No detector_path in config — finish setup Step 4 first.")
        if not recognizer_path:
            raise ValueError("No recognizer_path in config — finish setup Step 4 first.")

        self._detector = FaceDetector(detector_path, execution_provider=provider)
        self._recognizer = FaceRecognizer(recognizer_path, execution_provider=provider)
        self._debounce = AttendanceDebounce(
            window_hours=float(cfg.get("session_window_hours", _DEFAULT_SESSION_WINDOW_HOURS))
        )
        self._queue = EventQueue()

        self._thread: Optional[threading.Thread] = None
        self._running = False

    # ── Control interface (start/stop, for the scheduler to drive) ────────────────

    def start(self) -> None:
        """Load models + gallery and start the capture/recognition loop."""
        if self._running:
            return
        if not self._rtsp_url:
            raise ValueError("No RTSP URL in config — finish setup Step 2 first.")

        self._detector.load()
        self._recognizer.load()
        # Pull the latest embeddings at startup; falls back to whatever's
        # already cached if the backend is unreachable (never raises).
        refresh_gallery()
        gallery = load_local_gallery()
        if gallery:
            self._recognizer.load_gallery(gallery)
            print(f"✓ Loaded {self._recognizer.gallery_size} embedding(s) into the "
                  f"recognizer gallery from the local cache.")
        else:
            print("! Local gallery is empty — recognition will report no matches "
                  "until the sync-embeddings cache is populated.")

        self._running = True
        self._thread = threading.Thread(target=self._run, name="AttendanceLoop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the loop to stop and wait for the background thread to exit."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None

    # ── Loop internals ──────────────────────────────────────────────────────────

    def _run(self) -> None:
        interval = 1.0 / self._fps if self._fps > 0 else 0.0
        backoff = _RECONNECT_BACKOFF_START
        cap = None
        next_due = 0.0

        try:
            while self._running:
                if cap is None:
                    cap = cv2.VideoCapture(self._rtsp_url, cv2.CAP_FFMPEG)
                    if not cap.isOpened():
                        print(f"✗ Could not open the RTSP stream; retrying in {backoff:.1f}s")
                        cap.release()
                        cap = None
                        if not self._sleep(backoff):
                            break
                        backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX)
                        continue
                    backoff = _RECONNECT_BACKOFF_START

                ok, frame = cap.read()
                if not ok or frame is None:
                    print(f"✗ Lost the RTSP stream; reconnecting in {backoff:.1f}s")
                    cap.release()
                    cap = None
                    if not self._sleep(backoff):
                        break
                    backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX)
                    continue

                now = time.monotonic()
                if now < next_due:
                    continue
                next_due = now + interval

                self._process_frame(frame)
        finally:
            if cap is not None:
                cap.release()

    def _sleep(self, seconds: float) -> bool:
        """Sleep in small increments so stop() takes effect promptly during backoff.

        Returns False if stop() fired during the sleep (caller should exit).
        """
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        return self._running

    def _process_frame(self, frame) -> None:
        if self._recognizer.gallery_size == 0:
            return
        try:
            faces = self._detector.detect(frame, self._roi)
        except Exception as exc:
            print(f"! Detection failed on a frame ({exc}); skipping.")
            return

        for face in faces:
            try:
                match = self._recognizer.identify(frame, face)
            except Exception as exc:
                print(f"! Recognition failed on a detected face ({exc}); skipping.")
                continue
            if match is None:
                print(f"· No match (best candidate below similarity threshold "
                      f"{self._recognizer.similarity_threshold:.3f}).")
                continue
            print(f"✓ Match: student_id={match.student_id} similarity={match.confidence:.3f}")
            if not self._debounce.should_record(match.student_id):
                continue
            self._enqueue_event(match.student_id, match.confidence)

    def _enqueue_event(self, student_id: str, confidence: float) -> None:
        try:
            self._queue.enqueue(
                student_id=student_id,
                section_id=self._section_id,
                check_in_at=datetime.now(timezone.utc),
                confidence_score=confidence,
                subject_id=self._subject_id,
                camera_id=self.camera_id,
            )
        except Exception as exc:
            print(f"✗ Failed to enqueue attendance event for student_id={student_id}: {exc}")
            return
        print(f"✓ Queued attendance event: student_id={student_id} "
              f"camera_id={self.camera_id} confidence={confidence:.3f}")
        if self.on_event is not None:
            self.on_event(student_id, confidence)
