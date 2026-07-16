"""
service/frame_capture.py — Low-rate camera frame capture (2-5 fps).

Stub — implementation pending.
Will open the configured camera index via OpenCV, capture at the configured
interval, and push frames into an asyncio queue for the detection worker.
Full-framerate capture is intentionally avoided to keep CPU usage low.
"""
from __future__ import annotations


class FrameCapture:
    """
    Captures frames from a camera at a fixed interval and feeds them
    to a callback for detection processing.

    Args:
        camera_index: OpenCV camera index (0 = first camera)
        fps: Target capture rate (2-5 recommended)
        roi: Optional (x, y, w, h) tuple for the detection zone
        on_frame: Callable[[numpy.ndarray], None]
    """

    def __init__(self, camera_index: int = 0, fps: float = 3.0, roi=None, on_frame=None):
        self.camera_index = camera_index
        self.fps = fps
        self.roi = roi
        self.on_frame = on_frame
        self._running = False

    def start(self) -> None:
        # TODO: open cv2.VideoCapture, run capture loop in background thread
        raise NotImplementedError

    def stop(self) -> None:
        self._running = False
