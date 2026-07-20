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

import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import cv2

from config.store import ConfigStore
from service.debounce import AttendanceDebounce
from service.detection import FaceDetector
from service.logging_setup import get_logger
from service.recognition import FaceRecognizer
from service.snapshots import cleanup_old_snapshots, save_snapshot_if_needed
from sync.embeddings_cache import read_cache as _read_embeddings_cache, refresh_gallery
from sync.queue import EventQueue
from sync.schedule_cache import (
    cache_exists as _schedule_cache_exists,
    is_active_now as _schedule_is_active_now,
    next_window_start as _schedule_next_window_start,
    read_cache as _read_schedule_cache,
    refresh_schedule,
)

_logger = get_logger()

_DEFAULT_SAMPLE_FPS = 3.0
_DEFAULT_SESSION_WINDOW_HOURS = 4.0
# Reconnect backoff sequence: wait 2s before the first retry, then 5s, 10s,
# capped at 30s for every attempt after that — avoids hammering the
# camera/network during a prolonged outage. Retries run indefinitely.
_RECONNECT_BACKOFF_SEQUENCE = (2.0, 5.0, 10.0, 30.0)
_SNAPSHOT_CLEANUP_INTERVAL_HOURS = 6.0
# How often the schedule cache is re-checked against the current day/time to
# decide whether the camera/recognition loop should be running. Also the
# idle sleep granularity while outside the active window.
_SCHEDULE_CHECK_INTERVAL_SECONDS = 60.0
# How often a single status line is logged so the log file's latest
# timestamp alone can confirm the loop is alive, even when nothing else
# (no matches, no errors) has happened.
_HEARTBEAT_INTERVAL_SECONDS = 300.0

# FFMPEG capture options: force TCP transport (more reliable than UDP over WiFi)
# and cap the socket timeout so a wrong/unreachable host fails fast instead of
# hanging indefinitely. stimeout is in microseconds. Set immediately before
# the cv2.VideoCapture(...) call that needs it (not at module level) — the
# FFmpeg backend reads this env var lazily, so setting it at import time races
# against whichever module happens to import cv2 first.
_RTSP_FFMPEG_OPTS = "rtsp_transport;tcp|stimeout;5000000"


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

    # ── Dual console + file logging ─────────────────────────────────────────────
    # Console print() output is unchanged (interactive debugging); these also
    # write to the persistent per-day log file under app-data (service/logging_setup.py)
    # for the unattended production agent, where console output isn't captured.

    @staticmethod
    def _log_info(msg: str) -> None:
        print(msg)
        _logger.info(msg)

    @staticmethod
    def _log_warning(msg: str) -> None:
        print(msg)
        _logger.warning(msg)

    @staticmethod
    def _log_error(msg: str) -> None:
        print(msg)
        _logger.error(msg)

    # ── Control interface (start/stop, for the scheduler to drive) ────────────────

    def start(self) -> None:
        """Load models + gallery and start the capture/recognition loop."""
        if self._running:
            return
        if not self._rtsp_url:
            raise ValueError("No RTSP URL in config — finish setup Step 2 first.")

        self._detector.load()
        self._recognizer.load()
        cleanup_old_snapshots()
        # Pull the latest embeddings + schedule at startup; both fall back to
        # whatever's already cached if the backend is unreachable (never raise).
        refresh_gallery()
        refresh_schedule()
        gallery = load_local_gallery()
        if gallery:
            self._recognizer.load_gallery(gallery)
            self._log_info(
                f"✓ Loaded {self._recognizer.gallery_size} embedding(s) into the "
                f"recognizer gallery from the local cache."
            )
        else:
            self._log_warning(
                "! Local gallery is empty — recognition will report no matches "
                "until the sync-embeddings cache is populated."
            )

        self._running = True
        self._thread = threading.Thread(target=self._run, name="AttendanceLoop", daemon=True)
        self._thread.start()
        self._log_info(f"✓ Attendance loop started (camera_id={self.camera_id}).")

    def stop(self) -> None:
        """Signal the loop to stop and wait for the background thread to exit."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None
        self._log_info("Attendance loop stopped.")

    # ── Loop internals ──────────────────────────────────────────────────────────

    def _run(self) -> None:
        interval = 1.0 / self._fps if self._fps > 0 else 0.0
        cap = None
        reconnecting = False
        attempt = 0  # 0 = initial connect (no wait); >0 = Nth reconnect attempt
        next_due = 0.0
        next_snapshot_cleanup = time.monotonic() + _SNAPSHOT_CLEANUP_INTERVAL_HOURS * 3600
        next_schedule_check = 0.0
        next_heartbeat = 0.0
        last_frame_at: Optional[datetime] = None
        schedule_state = None  # None | "not_synced" | "inactive" | "active"
        frames_since_heartbeat = 0  # reset each time the heartbeat line is logged

        try:
            while self._running:
                loop_now = time.monotonic()
                if loop_now >= next_schedule_check:
                    next_schedule_check = loop_now + _SCHEDULE_CHECK_INTERVAL_SECONDS
                    new_state = self._current_schedule_state()
                    if new_state != schedule_state:
                        if schedule_state == "active" and cap is not None:
                            self._log_info("Scheduled window ended, going idle")
                            cap.release()
                            cap = None
                            reconnecting = False
                            attempt = 0
                        if new_state == "not_synced":
                            self._log_warning(
                                "No schedule configured yet — sync required before "
                                "the agent will run recognition."
                            )
                        elif new_state == "inactive":
                            next_start = _schedule_next_window_start(_read_schedule_cache())
                            if next_start:
                                self._log_info(f"Outside scheduled hours, idle until {next_start}.")
                            else:
                                self._log_info("Outside scheduled hours, idle.")
                        elif new_state == "active":
                            self._log_info("Entering scheduled active window")
                        schedule_state = new_state

                if loop_now >= next_heartbeat:
                    next_heartbeat = loop_now + _HEARTBEAT_INTERVAL_SECONDS
                    self._log_heartbeat(schedule_state, last_frame_at, frames_since_heartbeat)
                    frames_since_heartbeat = 0

                if schedule_state != "active":
                    if not self._sleep(_SCHEDULE_CHECK_INTERVAL_SECONDS):
                        break
                    continue

                if cap is None:
                    if attempt > 0:
                        wait = self._next_backoff(attempt - 1)
                        self._log_warning(
                            f"Reconnect attempt #{attempt} — retrying the RTSP stream "
                            f"in {wait:.0f}s."
                        )
                        if not self._sleep(wait):
                            break

                    cap = self._open_capture()
                    if not cap.isOpened():
                        cap.release()
                        cap = None
                        attempt += 1
                        reconnecting = True
                        self._log_warning("✗ Could not open the RTSP stream.")
                        continue
                    if reconnecting:
                        self._log_info(
                            f"✓ RTSP stream reconnected after {attempt} attempt(s); "
                            f"resuming normal processing."
                        )
                    reconnecting = False
                    attempt = 0

                ok, frame = cap.read()
                if not ok or frame is None:
                    self._log_warning("✗ Lost the RTSP stream; will attempt to reconnect.")
                    cap.release()
                    cap = None
                    reconnecting = True
                    attempt = 1
                    continue

                # Update on every successfully captured raw frame, regardless
                # of the sample-rate throttle below or whether a face is
                # later found — otherwise a healthy stream with no visible
                # face (or a fast camera relative to sample_fps) would still
                # show a frozen "last frame processed at" in the heartbeat.
                last_frame_at = datetime.now()
                frames_since_heartbeat += 1

                now = time.monotonic()
                if now >= next_snapshot_cleanup:
                    cleanup_old_snapshots()
                    next_snapshot_cleanup = now + _SNAPSHOT_CLEANUP_INTERVAL_HOURS * 3600

                if now < next_due:
                    continue
                next_due = now + interval

                self._process_frame(frame)
        except Exception:
            self._log_error("✗ Unexpected exception in the attendance loop; loop is exiting.")
            raise
        finally:
            if cap is not None:
                cap.release()

    def _open_capture(self):
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = _RTSP_FFMPEG_OPTS
        cap = cv2.VideoCapture(self._rtsp_url, cv2.CAP_FFMPEG)
        self._log_info(f"Opened RTSP stream with transport={_RTSP_FFMPEG_OPTS}")
        return cap

    def _log_heartbeat(
        self,
        schedule_state: Optional[str],
        last_frame_at: Optional[datetime],
        frames_since_heartbeat: int,
    ) -> None:
        """Single INFO line every _HEARTBEAT_INTERVAL_SECONDS so the log file's
        latest timestamp alone confirms the loop is alive, active or idle."""
        if schedule_state == "active":
            frame_str = last_frame_at.strftime("%H:%M:%S") if last_frame_at else "never"
            minutes = _HEARTBEAT_INTERVAL_SECONDS / 60.0
            self._log_info(
                f"Status: active — camera streaming, {frames_since_heartbeat} frames "
                f"read in last {minutes:.0f} min, last frame at {frame_str}, "
                f"{self._recognizer.gallery_size} students in cache"
            )
        else:
            next_start = _schedule_next_window_start(_read_schedule_cache())
            if next_start:
                self._log_info(f"Status: idle — waiting for next window at {next_start}")
            else:
                self._log_info("Status: idle — no active window scheduled")

    @staticmethod
    def _current_schedule_state() -> str:
        """
        "not_synced" if schedule_cache.json has never been written, else
        "active"/"inactive" per is_active_now() against the cached schedule.
        """
        if not _schedule_cache_exists():
            return "not_synced"
        return "active" if _schedule_is_active_now(_read_schedule_cache()) else "inactive"

    @staticmethod
    def _next_backoff(attempt: int) -> float:
        """Reconnect wait for the given 0-indexed attempt: 2s, 5s, 10s, then 30s forever."""
        idx = min(attempt, len(_RECONNECT_BACKOFF_SEQUENCE) - 1)
        return _RECONNECT_BACKOFF_SEQUENCE[idx]

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
            self._log_error(f"! Detection failed on a frame ({exc}); skipping.")
            return

        for face in faces:
            self._log_info(
                f"Face detected (confidence={face.confidence:.2f}) — checking against "
                f"{self._recognizer.gallery_size} cached embeddings"
            )
            try:
                match = self._recognizer.identify(frame, face)
            except Exception as exc:
                self._log_error(f"! Recognition failed on a detected face ({exc}); skipping.")
                continue
            if match is None:
                self._log_info(
                    f"· No match (best candidate below similarity threshold "
                    f"{self._recognizer.similarity_threshold:.3f})."
                )
                continue
            self._log_info(
                f"✓ Match: student_id={match.student_id} similarity={match.confidence:.3f}"
            )
            if not self._debounce.should_record(match.student_id):
                continue
            self._save_snapshot(match.student_id, frame)
            self._enqueue_event(match.student_id, match.confidence)

    def _save_snapshot(self, student_id: str, frame) -> None:
        try:
            dest = save_snapshot_if_needed(student_id, frame)
        except Exception as exc:
            self._log_warning(f"! Failed to save snapshot for student_id={student_id}: {exc}")
            return
        if dest is not None:
            self._log_info(f"Snapshot saved: snapshots/{dest.parent.name}/{dest.name}")

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
            self._log_error(
                f"✗ Failed to enqueue attendance event for student_id={student_id}: {exc}"
            )
            return
        self._log_info(
            f"✓ Queued attendance event: student_id={student_id} "
            f"camera_id={self.camera_id} confidence={confidence:.3f}"
        )
        if self.on_event is not None:
            self.on_event(student_id, confidence)
