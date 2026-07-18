"""
service/detection.py — Face detection inside the configured ROI.

Uses the YuNet ONNX detector downloaded in Step 4 (path taken from the encrypted
config's ``detector_path``) via OpenCV's purpose-built ``cv2.FaceDetectorYN``.
That API owns YuNet's prior-box decoding, score filtering and NMS, and — crucially
— returns the five facial landmarks SFace needs for alignment, so we don't
re-implement any of that tensor post-processing by hand.

Only the pixels inside the saved ROI (Step 3, stored as width/height *fractions*)
are handed to the detector. Boxes and landmarks are then offset back into
full-frame coordinates, so a DetectedFace is always expressed in the original
frame's pixel space regardless of the ROI crop.

Inference wiring only — this module never touches the network or the config
writer; callers pass in the model path and frames.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np


@dataclass
class DetectedFace:
    x: int
    y: int
    w: int
    h: int
    confidence: float
    crop: np.ndarray                       # BGR pixels of the face box (full-frame)
    landmarks: List[Tuple[int, int]] = field(default_factory=list)
    # Raw YuNet row (1x15: bbox[4] + 5 landmarks[10] + score[1]) expressed in
    # full-frame coordinates. SFace's alignCrop() consumes this directly, so the
    # recognizer can align from the true landmarks instead of a plain resize.
    row: Optional[np.ndarray] = None


def _roi_pixel_box(
    roi: Optional[dict], frame_w: int, frame_h: int
) -> Tuple[int, int, int, int]:
    """
    Convert a fractional ROI ({"x","y","w","h"} in 0..1) into a clamped pixel box
    (left, top, width, height). A missing/empty ROI means the whole frame.
    """
    if not roi:
        return 0, 0, frame_w, frame_h
    left = int(round(float(roi.get("x", 0.0)) * frame_w))
    top = int(round(float(roi.get("y", 0.0)) * frame_h))
    width = int(round(float(roi.get("w", 1.0)) * frame_w))
    height = int(round(float(roi.get("h", 1.0)) * frame_h))

    # Clamp fully inside the frame; guard against a degenerate (zero-area) box.
    left = max(0, min(left, frame_w - 1))
    top = max(0, min(top, frame_h - 1))
    width = max(1, min(width, frame_w - left))
    height = max(1, min(height, frame_h - top))
    return left, top, width, height


class FaceDetector:
    """
    Wraps the YuNet ONNX model behind ``cv2.FaceDetectorYN``.

    Args:
        model_path: Path to the YuNet .onnx file (config['detector_path']).
        confidence_threshold: Minimum detection score to accept.
        nms_threshold: IoU threshold for non-maximum suppression.
        top_k: Cap on candidate boxes kept before NMS.
        execution_provider: "cuda" | "dml" | "cpu" from Step 4. Used only to try
            an OpenCV DNN CUDA target when available; every other case (including
            DirectML, which OpenCV's DNN module cannot target) runs on CPU.
    """

    def __init__(
        self,
        model_path: str,
        confidence_threshold: float = 0.75,
        nms_threshold: float = 0.30,
        top_k: int = 5000,
        execution_provider: str = "cpu",
    ):
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.top_k = top_k
        self.execution_provider = (execution_provider or "cpu").lower()
        self._detector = None
        self._input_size: Tuple[int, int] = (0, 0)

    def _backend_target(self) -> Tuple[int, int]:
        """
        Pick an OpenCV DNN backend/target for the requested provider.

        OpenCV's DNN module has no DirectML target, and the prebuilt
        opencv-python wheels aren't CUDA-enabled, so in practice this resolves to
        CPU. We still *try* CUDA when asked so a CUDA-built OpenCV would use it;
        create() falling back to CPU is handled by the caller.
        """
        if self.execution_provider == "cuda":
            backend = getattr(cv2.dnn, "DNN_BACKEND_CUDA", 0)
            target = getattr(cv2.dnn, "DNN_TARGET_CUDA", 0)
            return backend, target
        return (
            getattr(cv2.dnn, "DNN_BACKEND_DEFAULT", 0),
            getattr(cv2.dnn, "DNN_TARGET_CPU", 0),
        )

    def load(self) -> None:
        """Create the FaceDetectorYN session. Falls back to CPU if a GPU target fails."""
        backend, target = self._backend_target()
        try:
            self._detector = cv2.FaceDetectorYN.create(
                self.model_path,
                "",
                (320, 320),               # placeholder; reset per-frame in detect()
                self.confidence_threshold,
                self.nms_threshold,
                self.top_k,
                backend,
                target,
            )
        except Exception:
            # A GPU target that the installed OpenCV can't honour — retry on CPU.
            self._detector = cv2.FaceDetectorYN.create(
                self.model_path,
                "",
                (320, 320),
                self.confidence_threshold,
                self.nms_threshold,
                self.top_k,
            )
        self._input_size = (0, 0)

    def _ensure_input_size(self, width: int, height: int) -> None:
        # YuNet requires the input size to match the image it will run on; only
        # push it when it actually changes (setInputSize rebuilds priors).
        if (width, height) != self._input_size:
            self._detector.setInputSize((width, height))
            self._input_size = (width, height)

    def detect(self, frame, roi: Optional[dict] = None) -> List[DetectedFace]:
        """
        Detect faces inside ``roi`` (fractional) on a single BGR frame.

        Returns DetectedFace objects in full-frame coordinates, sorted by
        descending confidence. Never raises on a "no faces" frame — returns [].
        """
        if self._detector is None:
            self.load()
        if frame is None or getattr(frame, "size", 0) == 0:
            return []

        fh, fw = frame.shape[:2]
        left, top, rw, rh = _roi_pixel_box(roi, fw, fh)
        region = frame[top:top + rh, left:left + rw]
        if region.size == 0:
            return []

        self._ensure_input_size(region.shape[1], region.shape[0])
        _, faces = self._detector.detect(region)
        if faces is None:
            return []

        results: List[DetectedFace] = []
        for raw in faces:
            score = float(raw[14])
            if score < self.confidence_threshold:
                continue

            # Offset the ROI-local geometry back into full-frame coordinates.
            row = raw.astype(np.float32).copy()
            row[0] += left
            row[1] += top
            for i in range(4, 14, 2):
                row[i] += left
                row[i + 1] += top

            x = int(round(row[0]))
            y = int(round(row[1]))
            w = int(round(row[2]))
            h = int(round(row[3]))
            # Clamp the box to the frame before slicing the crop.
            x = max(0, min(x, fw - 1))
            y = max(0, min(y, fh - 1))
            w = max(1, min(w, fw - x))
            h = max(1, min(h, fh - y))

            landmarks = [
                (int(round(row[i])), int(round(row[i + 1])))
                for i in range(4, 14, 2)
            ]
            results.append(
                DetectedFace(
                    x=x, y=y, w=w, h=h,
                    confidence=score,
                    crop=frame[y:y + h, x:x + w].copy(),
                    landmarks=landmarks,
                    row=row.reshape(1, -1),
                )
            )

        results.sort(key=lambda f: f.confidence, reverse=True)
        return results
