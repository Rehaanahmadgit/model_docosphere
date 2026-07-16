"""
service/detection.py — Face detection inside the configured ROI.

Stub — implementation pending.
Will use a lightweight ONNX face detector (e.g. YOLOv8-face-lite or
RetinaFace-MobileNet) loaded from models/ to locate faces in each frame.
Only pixels inside the ROI bounding box are passed to the detector.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class DetectedFace:
    x: int
    y: int
    w: int
    h: int
    confidence: float
    crop: object   # numpy.ndarray


class FaceDetector:
    """
    Wraps an ONNX detection model.  Returns a list of DetectedFace objects.

    Args:
        model_path: Path to the .onnx weights file in models/
        confidence_threshold: Minimum detection score to accept
    """

    def __init__(self, model_path: str, confidence_threshold: float = 0.75):
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self._session = None

    def load(self) -> None:
        # TODO: import onnxruntime; load model session
        raise NotImplementedError

    def detect(self, frame, roi=None) -> List[DetectedFace]:
        # TODO: crop to ROI, run inference, return results
        raise NotImplementedError
