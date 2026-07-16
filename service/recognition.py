"""
service/recognition.py — Face recognition against locally cached embeddings.

Stub — implementation pending.
Will use an ArcFace/InsightFace-compatible ONNX embedding model.
Embeddings are pulled from the backend via sync/api_client.sync_embeddings()
and cached on disk.  Cosine similarity against the cached gallery determines
identity.  A configurable threshold (default 0.6) gates positive matches.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import numpy as np


@dataclass
class RecognitionResult:
    student_id: int
    confidence: float   # cosine similarity 0.0–1.0


class FaceRecognizer:
    """
    Matches a detected face crop against the loaded embedding gallery.

    Args:
        model_path: Path to the ONNX embedding extractor in models/
        similarity_threshold: Minimum cosine similarity to accept a match
    """

    def __init__(self, model_path: str, similarity_threshold: float = 0.60):
        self.model_path = model_path
        self.similarity_threshold = similarity_threshold
        self._session = None
        self._gallery: dict[int, np.ndarray] = {}   # student_id → embedding vector

    def load(self) -> None:
        # TODO: import onnxruntime; load model session
        raise NotImplementedError

    def load_gallery(self, embeddings: list[dict]) -> None:
        """Replace in-memory gallery from sync payload."""
        # TODO: parse embeddings list, convert to numpy arrays
        raise NotImplementedError

    def identify(self, face_crop) -> Optional[RecognitionResult]:
        # TODO: run crop through model, compute cosine sim against gallery
        raise NotImplementedError
