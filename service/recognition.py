"""
service/recognition.py — Face recognition against locally cached embeddings.

Uses the SFace ONNX embedding model downloaded in Step 4 (path from the encrypted
config's ``recognizer_path``) via OpenCV's ``cv2.FaceRecognizerSF``. The face is
aligned with SFace's own ``alignCrop`` using YuNet's five landmarks (carried on
each DetectedFace), which is what SFace was trained on — a plain resize measurably
hurts accuracy, so alignment is the default path.

Identity is decided by cosine similarity against an in-memory gallery of student
reference embeddings. For now the gallery is loaded from a simple local JSON file
(``student_id -> embedding``); the real sync-embeddings API call is a separate
step. A configurable threshold gates positive matches — SFace's own recommended
cosine threshold for "same identity" is 0.363, which is the default here.

Inference wiring only — no network, no config writes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

import cv2
import numpy as np

if TYPE_CHECKING:
    from service.detection import DetectedFace

# SFace's published cosine threshold for a same-identity match (OpenCV Zoo).
_DEFAULT_SFACE_COSINE_THRESHOLD = 0.363


@dataclass
class RecognitionResult:
    student_id: str
    confidence: float   # cosine similarity 0.0–1.0


def _l2_normalize(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        return vec
    return vec / norm


class FaceRecognizer:
    """
    Matches a detected face against the loaded embedding gallery.

    Args:
        model_path: Path to the SFace .onnx file (config['recognizer_path']).
        similarity_threshold: Minimum cosine similarity to accept a match.
        execution_provider: "cuda" | "dml" | "cpu" from Step 4 (see FaceDetector
            for why this effectively resolves to CPU with the prebuilt wheels).
    """

    def __init__(
        self,
        model_path: str,
        similarity_threshold: float = _DEFAULT_SFACE_COSINE_THRESHOLD,
        execution_provider: str = "cpu",
    ):
        self.model_path = model_path
        self.similarity_threshold = similarity_threshold
        self.execution_provider = (execution_provider or "cpu").lower()
        self._recognizer = None
        # student_id → L2-normalized embedding (so cosine sim is a plain dot).
        self._gallery: Dict[str, np.ndarray] = {}

    def _backend_target(self):
        if self.execution_provider == "cuda":
            return (
                getattr(cv2.dnn, "DNN_BACKEND_CUDA", 0),
                getattr(cv2.dnn, "DNN_TARGET_CUDA", 0),
            )
        return (
            getattr(cv2.dnn, "DNN_BACKEND_DEFAULT", 0),
            getattr(cv2.dnn, "DNN_TARGET_CPU", 0),
        )

    def load(self) -> None:
        """Create the FaceRecognizerSF session. Falls back to CPU if a GPU target fails."""
        backend, target = self._backend_target()
        try:
            self._recognizer = cv2.FaceRecognizerSF.create(
                self.model_path, "", backend, target
            )
        except Exception:
            self._recognizer = cv2.FaceRecognizerSF.create(self.model_path, "")

    # ── Gallery loading ─────────────────────────────────────────────────────────

    def load_gallery(self, embeddings) -> None:
        """
        Replace the in-memory gallery.

        Accepts either:
          * a dict {student_id: [floats]}, or
          * a list of {"student_id": str, "embedding": [floats]} dicts
        student_id is an opaque string identifier (e.g. "test_student" or an
        alphanumeric roll number) — never coerced to int. Vectors are
        L2-normalized on load so identify() is a single dot product.
        """
        gallery: Dict[str, np.ndarray] = {}

        if isinstance(embeddings, dict):
            items = embeddings.items()
        else:
            items = (
                (row.get("student_id"), row.get("embedding"))
                for row in embeddings
            )

        for student_id, vector in items:
            if student_id is None or vector is None:
                continue
            arr = _l2_normalize(np.asarray(vector, dtype=np.float32))
            if arr.size == 0:
                continue
            gallery[str(student_id)] = arr

        self._gallery = gallery

    def load_gallery_file(self, path: str) -> None:
        """Load the gallery from a local JSON file (dict or list form)."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.load_gallery(data)

    @property
    def gallery_size(self) -> int:
        return len(self._gallery)

    # ── Embedding extraction ─────────────────────────────────────────────────────

    def embed(self, frame, face: "DetectedFace") -> np.ndarray:
        """
        Produce an L2-normalized SFace embedding for ``face`` on ``frame``.

        Prefers landmark-based alignment (face.row from YuNet); if the row is
        missing, falls back to aligning the raw crop. Always returns a unit vector.
        """
        if self._recognizer is None:
            self.load()

        if getattr(face, "row", None) is not None:
            aligned = self._recognizer.alignCrop(frame, face.row)
        else:
            # No landmarks available — align the bare crop as a best effort.
            aligned = self._recognizer.alignCrop(face.crop, None)

        feature = self._recognizer.feature(aligned)
        return _l2_normalize(feature)

    def embed_crop(self, face_crop) -> np.ndarray:
        """
        Embed a standalone face crop without landmarks (lower accuracy).

        Provided for the "given a detected face crop" case; the aligned
        ``embed()`` path is preferred whenever a DetectedFace is available.
        """
        if self._recognizer is None:
            self.load()
        aligned = self._recognizer.alignCrop(face_crop, None)
        feature = self._recognizer.feature(aligned)
        return _l2_normalize(feature)

    # ── Matching ─────────────────────────────────────────────────────────────────

    def identify(self, frame, face: "DetectedFace") -> Optional[RecognitionResult]:
        """
        Return the best gallery match for ``face`` above the similarity threshold,
        or None if the gallery is empty or no student clears the threshold.
        """
        if not self._gallery:
            return None
        probe = self.embed(frame, face)
        return self._match(probe)

    def identify_embedding(self, probe) -> Optional[RecognitionResult]:
        """Match an already-computed embedding against the gallery."""
        if not self._gallery:
            return None
        return self._match(_l2_normalize(np.asarray(probe, dtype=np.float32)))

    def _match(self, probe: np.ndarray) -> Optional[RecognitionResult]:
        best_id: Optional[int] = None
        best_sim = -1.0
        for student_id, ref in self._gallery.items():
            # Both vectors are unit-normalized, so the dot product is cosine sim.
            sim = float(np.dot(probe, ref))
            if sim > best_sim:
                best_sim = sim
                best_id = student_id

        if best_id is None or best_sim < self.similarity_threshold:
            return None
        return RecognitionResult(student_id=best_id, confidence=best_sim)
