"""
Face engines: find faces in a photo and turn each into a 512-number fingerprint.

The engine is chosen with settings.FACE_ENGINE, so another one (e.g. Azure Face,
if Microsoft approves access) can replace InsightFace without touching the views.
"""
import threading
from dataclasses import dataclass

import numpy as np
from django.conf import settings


@dataclass
class DetectedFace:
    box: tuple            # (x1, y1, x2, y2) in pixels of the analysed photo
    score: float          # detector confidence, 0..1
    embedding: np.ndarray  # 512 float32 values, length 1 (cosine similarity = dot product)
    crop: np.ndarray      # aligned 112x112 BGR face image


class FaceEngineUnavailable(Exception):
    """The engine cannot run (e.g. model files not downloaded yet)."""


_engine = None
_engine_lock = threading.Lock()


def get_engine():
    """The configured engine, loaded once. A failed load is retried next time."""
    global _engine
    with _engine_lock:
        if _engine is None:
            name = settings.FACE_ENGINE
            if name == 'insightface':
                from .insightface_onnx import InsightFaceOnnxEngine
                try:
                    _engine = InsightFaceOnnxEngine(
                        settings.FACE_MODEL_DIR, det_threshold=settings.FACE_DETECTION_THRESHOLD
                    )
                except FaceEngineUnavailable:
                    raise
                except Exception as e:  # e.g. a damaged model file
                    raise FaceEngineUnavailable(
                        f'Could not load the face models ({e}). Run: python manage.py download_face_models'
                    ) from e
            else:
                raise FaceEngineUnavailable(f'Unknown FACE_ENGINE "{name}".')
        return _engine
