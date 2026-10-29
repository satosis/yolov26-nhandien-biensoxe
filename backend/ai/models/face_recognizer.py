import os
import time
import math
import logging
import threading
from numbers import Real
from typing import Optional
import numpy as np

from backend.services import config as cfg
from backend.util.image_utils import decode_image
from backend.ai.face_service import FaceAnalysisService

logger = logging.getLogger("inference.face_recognizer")


def _face_embedding(face) -> np.ndarray:
    emb = getattr(face, "normed_embedding", None)
    if not isinstance(emb, np.ndarray) or emb.shape != (512,) or emb.dtype.kind not in "iuf":
        logger.error("Face model returned an invalid embedding shape or type")
        raise ValueError("Face model returned an invalid embedding shape")
    emb = emb.astype(np.float32, copy=False)
    if not np.isfinite(emb).all():
        logger.error("Face model returned a non-finite embedding")
        raise ValueError("Face model returned a non-finite embedding")
    norm = float(np.linalg.norm(emb))
    if not np.isfinite(norm) or norm <= 1e-6:
        logger.error("Face model returned a zero embedding")
        raise ValueError("Face model returned a zero embedding")
    return emb / norm


def _face_bbox_in_frame(raw_bbox, offset_x: int, offset_y: int, frame_shape) -> list[int] | None:
    if (
        not isinstance(raw_bbox, (list, tuple, np.ndarray))
        or (isinstance(raw_bbox, np.ndarray) and raw_bbox.ndim != 1)
        or len(raw_bbox) != 4
        or any(not isinstance(value, Real) for value in raw_bbox)
    ):
        logger.error("Face model returned an invalid bounding box")
        raise ValueError("Face model returned an invalid bounding box")
    bbox = np.asarray(raw_bbox, dtype=np.float64)
    if not np.isfinite(bbox).all():
        logger.error("Face model returned an invalid bounding box")
        raise ValueError("Face model returned an invalid bounding box")

    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        logger.error("Face model returned a bounding box with invalid dimensions")
        raise ValueError("Face model returned a bounding box with invalid dimensions")

    frame_h, frame_w = frame_shape[:2]
    left = math.floor(np.clip(x1 + offset_x, 0, frame_w))
    top = math.floor(np.clip(y1 + offset_y, 0, frame_h))
    right = math.ceil(np.clip(x2 + offset_x, 0, frame_w))
    bottom = math.ceil(np.clip(y2 + offset_y, 0, frame_h))
    if right - left < 28 or bottom - top < 28:
        return None
    return [left, top, right, bottom]


class FaceRecognizer:
    def __init__(self):
        self._app = FaceAnalysisService.get_instance(det_size=(640, 640))
        logger.info("FaceRecognizer initialized with FaceAnalysisService")

        self._names = []
        self._matrix = None
        self._known_session = []
        self._lock = threading.Lock()

        self._load_known_faces()
        logger.info("FaceRecognizer ready with %d known identities", len(self._names))

    def reload(self):
        logger.info("FaceRecognizer: Reloading known faces...")
        with self._lock:
            self._load_known_faces()
            self._known_session.clear()
        logger.info("FaceRecognizer: Reload complete. %d identities loaded.", len(self._names))

    def _load_known_faces(self):
        if not os.path.isdir(cfg.KNOWN_FACES_DIR):
            logger.error("Known faces directory is missing")
            raise FileNotFoundError(f"Known faces directory is missing: {cfg.KNOWN_FACES_DIR}")
        
        all_embs = []
        all_names = []
        
        for person in sorted(os.listdir(cfg.KNOWN_FACES_DIR)):
            person_dir = os.path.join(cfg.KNOWN_FACES_DIR, person)
            if not os.path.isdir(person_dir): continue
            
            embeddings = []
            for fname in os.listdir(person_dir):
                if not fname.lower().endswith((".jpg", ".jpeg", ".png")): continue
                image_path = os.path.join(person_dir, fname)
                img = decode_image(image_path)
                if img is None:
                    logger.error("Known face image cannot be decoded: %s", image_path)
                    raise ValueError(f"Known face image cannot be decoded: {image_path}")
                faces = self._app.get(img)
                if faces:
                    emb = _face_embedding(faces[0])
                    embeddings.append(emb)
                    all_embs.append(emb)
                    all_names.append(person)
            
            if embeddings:
                avg_emb = np.mean(embeddings, axis=0)
                avg_emb = avg_emb / np.linalg.norm(avg_emb)
                all_embs.append(avg_emb)
                all_names.append(person)
        
        if all_embs:
            self._matrix = np.array(all_embs)
            self._names = all_names
        else:
            self._matrix = None
            self._names = []

    def _map_pose_to_direction(self, pose) -> str:
        if pose is None or len(pose) < 2:
            return "frontal"
        pitch, yaw, roll = pose
        if yaw > 25: return "right"
        if yaw < -25: return "left"
        if pitch > 20: return "up"
        if pitch < -20: return "down"
        return "frontal"

    def _score_to_percent(self, sim: float) -> float:
        if sim < 0.35:
            return float(np.clip(sim / 0.35 * 0.70, 0.0, 0.70))
        percent = 0.70 + (sim - 0.35) / (0.65 - 0.35) * 0.28
        return float(np.clip(percent, 0.0, 0.99))

    def identify(self, frame, person_bbox=None) -> tuple[str, float, list[int], Optional[np.ndarray], str]:
        offset_x, offset_y = 0, 0
        input_img = frame

        if person_bbox is not None and len(person_bbox) >= 4:
            x1, y1, x2, y2 = map(int, person_bbox[:4])
            h_f, w_f = frame.shape[:2]
            pad_w = int((x2 - x1) * 0.15)
            pad_h = int((y2 - y1) * 0.15)
            px1 = max(0, x1 - pad_w)
            py1 = max(0, y1 - pad_h)
            px2 = min(w_f, x2 + pad_w)
            py2 = min(h_f, y2 + pad_h)

            crop = frame[py1:py2, px1:px2]
            if crop.shape[0] >= 32 and crop.shape[1] >= 32:
                input_img = crop
                offset_x, offset_y = px1, py1

        raw_faces = self._app.get(input_img)
        if not raw_faces and input_img is not frame:
            raw_faces = self._app.get(frame)
            offset_x, offset_y = 0, 0

        if not raw_faces:
            return "", 0.0, [], None, "frontal"

        faces = [f for f in raw_faces if getattr(f, 'det_score', 1.0) >= 0.55]
        if not faces:
            faces = raw_faces

        visible_faces = []
        for face in faces:
            bbox = _face_bbox_in_frame(face.bbox, offset_x, offset_y, frame.shape)
            if bbox is not None:
                visible_faces.append((face, bbox))
        if not visible_faces:
            return "", 0.0, [], None, "frontal"

        face, bbox = max(
            visible_faces,
            key=lambda item: (item[1][2] - item[1][0]) * (item[1][3] - item[1][1]),
        )
        emb = _face_embedding(face)

        pose = getattr(face, 'pose', None)
        direction = self._map_pose_to_direction(pose)

        with self._lock:
            if self._matrix is None:
                return "STRANGER", 0.0, bbox, emb, direction

            similarities = np.dot(self._matrix, emb)
            idx = np.argmax(similarities)
            best_sim = float(similarities[idx])

            threshold = cfg.FACE_RECOG_THRESHOLD
            now = time.time()
            self._known_session = [s for s in self._known_session if now - s['ts'] < 30]

            if best_sim >= threshold:
                self._known_session.append({'emb': emb, 'name': self._names[idx], 'ts': now})
                if len(self._known_session) > 20:
                    self._known_session = self._known_session[-10:]
                return self._names[idx], self._score_to_percent(best_sim), bbox, emb, direction

            for s in self._known_session:
                cache_sim = float(np.dot(emb, s['emb']))
                if cache_sim > 0.45:
                    return s['name'], self._score_to_percent(cache_sim), bbox, emb, direction

        return "STRANGER", 0.0, bbox, emb, direction
