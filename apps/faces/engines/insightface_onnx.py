"""
InsightFace "buffalo_l" models run with onnxruntime + OpenCV (the `insightface`
package is not needed, so nothing has to be compiled on Windows).

  det_10g.onnx   SCRFD detector: face boxes + 5 landmarks (eyes, nose, mouth corners)
  w600k_r50.onnx ArcFace recogniser: aligned 112x112 face -> 512-number fingerprint

The models are free for non-commercial / academic use only (InsightFace licence).
Download them with `python manage.py download_face_models`.
"""
from pathlib import Path

import cv2
import numpy as np

from . import DetectedFace, FaceEngineUnavailable

DETECTOR_FILE = 'det_10g.onnx'
RECOGNIZER_FILE = 'w600k_r50.onnx'

# Where ArcFace expects the 5 landmarks in its 112x112 input.
ARCFACE_LANDMARKS = np.array([
    [38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
    [41.5493, 92.3655], [70.7299, 92.2041],
], dtype=np.float32)

STRIDES = (8, 16, 32)
ANCHORS_PER_CELL = 2
EMBED_BATCH = 32


class InsightFaceOnnxEngine:
    name = 'insightface-buffalo_l'

    def __init__(self, model_dir, det_threshold=0.5, nms_threshold=0.4):
        import onnxruntime as ort

        model_dir = Path(model_dir)
        missing = [f for f in (DETECTOR_FILE, RECOGNIZER_FILE) if not (model_dir / f).is_file()]
        if missing:
            raise FaceEngineUnavailable(
                f'Face model files missing in {model_dir}: {", ".join(missing)}. '
                'Run: python manage.py download_face_models'
            )
        options = ort.SessionOptions()
        options.log_severity_level = 3  # the detector's fixed-size shape hints are expected to differ
        providers = ['CPUExecutionProvider']
        self.detector = ort.InferenceSession(str(model_dir / DETECTOR_FILE), options, providers=providers)
        self.recognizer = ort.InferenceSession(str(model_dir / RECOGNIZER_FILE), options, providers=providers)
        self.det_input = self.detector.get_inputs()[0].name
        self.det_outputs = [o.name for o in self.detector.get_outputs()]
        self.rec_input = self.recognizer.get_inputs()[0].name
        self.det_threshold = det_threshold
        self.nms_threshold = nms_threshold
        self._centers = {}

    def analyze(self, image: np.ndarray, max_side: int = 640) -> list[DetectedFace]:
        """All faces in a BGR image, most confident first."""
        found = self._detect(image, max_side)
        if not found:
            return []
        crops = [self._align(image, kps) for _, _, kps in found]
        embeddings = self._embed(crops)
        return [
            DetectedFace(
                box=tuple(float(v) for v in box), score=float(score), embedding=emb, crop=crop,
            )
            for (box, score, _), crop, emb in zip(found, crops, embeddings)
        ]

    # ── Detection (SCRFD) ────────────────────────────────────────────────────

    def _detect(self, image, max_side):
        # Never enlarge: faces bigger than the detector's largest anchors are missed.
        scale = min(1.0, max_side / max(image.shape[:2]))
        found = self._detect_at(image, scale)
        if not found and min(image.shape[:2]) * scale >= 160:
            # A close-up face can still be too big: try once more at half size.
            found = self._detect_at(image, scale / 2)
        return found

    def _detect_at(self, image, scale):
        h, w = image.shape[:2]
        new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
        pad_w, pad_h = -(-new_w // 32) * 32, -(-new_h // 32) * 32  # multiples of the largest stride
        canvas = np.zeros((pad_h, pad_w, 3), dtype=np.uint8)
        canvas[:new_h, :new_w] = cv2.resize(image, (new_w, new_h))
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128.0, (pad_w, pad_h), (127.5, 127.5, 127.5), swapRB=True)
        outputs = self.detector.run(self.det_outputs, {self.det_input: blob})

        boxes, scores, landmarks = [], [], []
        n = len(STRIDES)
        for i, stride in enumerate(STRIDES):
            cell_scores = outputs[i].reshape(-1)
            keep = np.where(cell_scores >= self.det_threshold)[0]
            if keep.size == 0:
                continue
            centers = self._anchor_centers(pad_h // stride, pad_w // stride, stride)[keep]
            dist = outputs[i + n].reshape(-1, 4)[keep] * stride
            kps = outputs[i + 2 * n].reshape(-1, 5, 2)[keep] * stride
            boxes.append(np.stack([
                centers[:, 0] - dist[:, 0], centers[:, 1] - dist[:, 1],
                centers[:, 0] + dist[:, 2], centers[:, 1] + dist[:, 3],
            ], axis=-1))
            landmarks.append(kps + centers[:, None, :])
            scores.append(cell_scores[keep])
        if not scores:
            return []

        boxes = np.vstack(boxes) / scale
        landmarks = np.vstack(landmarks) / scale
        scores = np.concatenate(scores)
        return [(boxes[i], scores[i], landmarks[i]) for i in self._nms(boxes, scores)]

    def _anchor_centers(self, rows, cols, stride):
        key = (rows, cols, stride)
        centers = self._centers.get(key)
        if centers is None:
            grid = np.stack(np.mgrid[:rows, :cols][::-1], axis=-1).astype(np.float32)
            centers = np.repeat((grid * stride).reshape(-1, 2), ANCHORS_PER_CELL, axis=0)
            if len(self._centers) < 100:  # photo sizes vary; keep the cache small
                self._centers[key] = centers
        return centers

    def _nms(self, boxes, scores):
        x1, y1, x2, y2 = boxes.T
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order = scores.argsort()[::-1]
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            inter = np.maximum(0.0, xx2 - xx1 + 1) * np.maximum(0.0, yy2 - yy1 + 1)
            overlap = inter / (areas[i] + areas[order[1:]] - inter)
            order = order[np.where(overlap <= self.nms_threshold)[0] + 1]
        return keep

    # ── Alignment and recognition (ArcFace) ──────────────────────────────────

    def _align(self, image, kps):
        matrix = _similarity_transform(kps.astype(np.float32), ARCFACE_LANDMARKS)
        return cv2.warpAffine(image, matrix, (112, 112), borderValue=0.0)

    def _embed(self, crops):
        result = []
        for start in range(0, len(crops), EMBED_BATCH):
            batch = crops[start:start + EMBED_BATCH]
            blob = cv2.dnn.blobFromImages(batch, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
            vectors = self.recognizer.run(None, {self.rec_input: blob})[0]
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
            result.extend(vectors.astype(np.float32))
        return result


def _similarity_transform(src, dst):
    """Least-squares rotation + uniform scale + shift mapping src points onto dst (Umeyama)."""
    src_mean, dst_mean = src.mean(axis=0), dst.mean(axis=0)
    src_c, dst_c = src - src_mean, dst - dst_mean
    cov = dst_c.T @ src_c / len(src)
    d = np.ones(2)
    if np.linalg.det(cov) < 0:
        d[-1] = -1
    u, s, vt = np.linalg.svd(cov)
    rotation = u @ np.diag(d) @ vt
    scale = (s @ d) / src_c.var(axis=0).sum()
    shift = dst_mean - scale * rotation @ src_mean
    return np.hstack([scale * rotation, shift[:, None]]).astype(np.float32)
