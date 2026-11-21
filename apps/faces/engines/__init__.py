import threading
from dataclasses import dataclass

import numpy as np
from django.conf import settings

@dataclass
class DetectedFace:
    box: tuple
    score: float
    embedding: np.ndarray
    crop: np.ndarray

class FaceEngineUnavailable(Exception):
    pass

_engine = None
_engine_lock = threading.Lock()

def get_engine():
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
                except Exception as e:
                    raise FaceEngineUnavailable(
                        f'Could not load the face models ({e}). Run: python manage.py download_face_models'
                    ) from e
            else:
                raise FaceEngineUnavailable(f'Unknown FACE_ENGINE "{name}".')
        return _engine
