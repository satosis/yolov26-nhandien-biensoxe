import os
import time
import json
import re
import uuid
import signal
import logging
import collections
import importlib
import importlib.util
import math
from numbers import Real
import threading
import ssl
import certifi
import numpy as np
import cv2
import concurrent.futures
import sys
import types
from dataclasses import dataclass, field
from multiprocessing import Queue, Event, shared_memory
from typing import Optional

ort = importlib.import_module("onnxruntime") if importlib.util.find_spec("onnxruntime") else None
_HAS_ORT = ort is not None

from backend.services import config as cfg
from backend.services.database import db
from backend.util.ocr_utils import VNPlateOCR
from backend.util.image_utils import save_snapshot, decode_image
from backend.util.texture_utils import check_plate_authenticity
from backend.util.bbox_utils import merge_plate_boxes

logger = logging.getLogger("inference")


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
    """Normalize model coordinates once, before they reach snapshot persistence."""
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


def _patch_e2e_loss():
    class E2ELoss:
        pass

    for name in ("ultralytics.utils.loss", "ultralytics.nn.modules.loss"):
        module = sys.modules.setdefault(name, types.ModuleType(name))
        module.E2ELoss = E2ELoss


@dataclass
class InferenceResult:
    cam_id: str
    ts: float
    plate_text: str = ""
    plate_conf: float = 0.0
    plate_type: str = "white"
    face_name: str = ""
    face_conf: float = 0.0
    snapshot_path: str = ""
    annotated_frame: Optional[object] = field(default=None, repr=False)


def validate_plate_crop(crop) -> bool:
    if crop is None or crop.size == 0:
        return False

    h, w = crop.shape[:2]
    aspect = w / h

    if aspect < 0.8 or aspect > 6.0:
        return False

    if w < 25 or h < 12:
        return False

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if gray.mean() < 40:
        return False

    edges = cv2.Canny(gray, 50, 150)
    edge_ratio = np.count_nonzero(edges) / (h * w)
    if edge_ratio < 0.03 or edge_ratio > 0.60:
        return False

    return True


def enhance_plate(crop):
    h, w = crop.shape[:2]
    MIN_OCR_HEIGHT = 100

    if h < 40:
        scale = max(3, min(4, MIN_OCR_HEIGHT // h))
        upscaled = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_LINEAR)
    elif h < MIN_OCR_HEIGHT:
        scale = max(2, min(3, MIN_OCR_HEIGHT // h))
        upscaled = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_LINEAR)
    elif h < 200:
        upscaled = cv2.resize(crop, (w * 2, h * 2), interpolation=cv2.INTER_LINEAR)
    else:
        upscaled = crop

    lab = cv2.cvtColor(upscaled, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    cl = clahe.apply(l_ch)
    enhanced = cv2.cvtColor(cv2.merge((cl, a_ch, b_ch)), cv2.COLOR_LAB2BGR)

    gaussian = cv2.GaussianBlur(enhanced, (0, 0), 1.0)
    sharpened = cv2.addWeighted(enhanced, 1.5, gaussian, -0.5, 0)

    return sharpened



class PlateVoter:
    def __init__(self, buffer_size: int = 5):
        self._buffer: collections.deque = collections.deque(maxlen=buffer_size)
        self._buffer_size = buffer_size
        self._min_conf = cfg.VOTE_MIN_CONF
        self._lock = threading.RLock()

    def add(self, text: str, conf: float):
        if text and conf >= self._min_conf:
            with self._lock:
                self._buffer.append((text, conf))

    def is_ready(self) -> bool:
        with self._lock:
            if len(self._buffer) == 0: return False
            if len(self._buffer) >= self._buffer_size: return True
            if len(self._buffer) >= 3:
                best_text, best_conf = self.best()
                if best_conf > 0.88: return True
            return False

    def best(self) -> tuple[str, float]:
        with self._lock:
            if not self._buffer: return "", 0.0
            scores: dict[str, float] = {}
            counts: dict[str, int] = {}
            for text, conf in self._buffer:
                scores[text] = scores.get(text, 0.0) + conf
                counts[text] = counts.get(text, 0) + 1
            best_text = max(scores, key=lambda t: scores[t])
            avg_conf = scores[best_text] / counts[best_text]
            return best_text, avg_conf

    def reset(self):
        with self._lock:
            self._buffer.clear()


class PlateDetector:
    def __init__(self):
        self._engine_type = "cpu"
        self._ort_session = None
        self._model = None
        self._rknn = None
        model_path = cfg.PLATE_MODEL_PATH
        
        logger.info("PlateDetector: Initializing... Target model: %s", model_path)

        if model_path.endswith(".pt") and os.path.exists(model_path):
            import torch
            from ultralytics import YOLO

            _patch_e2e_loss()
            self._model = YOLO(model_path, task="detect")
            device = "mps" if torch.backends.mps.is_available() else "cpu"
            self._model.to(device)
            logger.info("PlateDetector: PyTorch loaded using %s", device.upper())
            self._engine_type = f"yolo_{device}"
            return

        rknn_path = cfg.RKNN_MODEL_PATH
        if rknn_path and os.path.exists(rknn_path) and importlib.util.find_spec("rknnlite"):
            from rknnlite.api import RKNNLite
            self._rknn = RKNNLite()
            ret = self._rknn.load_rknn(rknn_path)
            if ret == 0:
                ret = self._rknn.init_runtime()
            if ret != 0:
                logger.error("RKNN model load or initialization failed (code=%s)", ret)
                raise RuntimeError(f"RKNN load/init failed with code {ret}")
            self._engine_type = "rknn"
            logger.info("PlateDetector: Successfully loaded RKNN (NPU) engine: %s", rknn_path)

        if self._engine_type == "cpu" and _HAS_ORT:
            path = model_path
            if path.endswith(".pt"):
                onnx_path = path.replace(".pt", ".onnx")
                int8_path = path.replace(".pt", "_int8.onnx")
                if os.path.exists(int8_path):
                    path = int8_path
                elif os.path.exists(onnx_path):
                    path = onnx_path

            if path.endswith(".onnx") and os.path.exists(path):
                is_int8 = "int8" in path.lower()
                providers = ['CPUExecutionProvider']
                provider_options = [{}]
                if not is_int8:
                    providers.insert(0, 'CoreMLExecutionProvider')
                    provider_options.insert(0, {"MLComputeUnits": "CPUAndGPU"})
                self._ort_session = ort.InferenceSession(path, providers=providers, provider_options=provider_options)
                active = self._ort_session.get_providers()
                if "CoreMLExecutionProvider" in active:
                    self._engine_type = "coreml"
                    logger.info("PlateDetector: ONNX + CoreML: %s", path)
                else:
                    self._engine_type = "onnx_cpu"
                    logger.info("PlateDetector: ONNX CPU: %s", path)

        if self._engine_type == "cpu":
            if not os.path.exists(model_path):
                logger.error("Configured plate model is missing")
                raise FileNotFoundError(f"PlateDetector model not found: {model_path}")
            if model_path.endswith(".onnx"):
                logger.error("ONNX Runtime is unavailable for the configured plate model")
                raise RuntimeError("ONNX Runtime is required for the configured plate model")
            from ultralytics import YOLO

            _patch_e2e_loss()
            self._model = YOLO(cfg.PLATE_MODEL_PATH, task="detect")
            self._model.to("cpu")
            self._engine_type = "yolo_cpu"
            logger.info("PlateDetector: Fallback to YOLO CPU: %s", cfg.PLATE_MODEL_PATH)

    def detect(self, frame):
        if self._model:
            return self._detect_yolo(frame)
        elif self._ort_session:
            return self._detect_onnx(frame)
        elif self._engine_type == "rknn":
            return self._detect_rknn(frame)
        logger.error("PlateDetector has no available inference engine")
        raise RuntimeError("No inference engine available")

    def _detect_rknn(self, frame):
        blob = cv2.resize(frame, (640, 640))
        outputs = self._rknn.inference(inputs=[blob])
        pred = outputs[0][0]
        return self._process_pred(pred, frame.shape[:2])

    def _detect_onnx(self, frame):
        input_name = self._ort_session.get_inputs()[0].name
        blob = cv2.dnn.blobFromImage(frame, 1/255.0, (640, 640), swapRB=True)
        outputs = self._ort_session.run(None, {input_name: blob})
        pred = outputs[0][0]
        return self._process_pred(pred, frame.shape[:2])

    def _detect_yolo(self, frame):
        results = self._model(frame, imgsz=640, conf=0.25, verbose=False)
        boxes = []
        for r in results:
            if not r.boxes: continue
            for b in r.boxes:
                x1, y1, x2, y2 = map(int, b.xyxy[0])
                conf = float(b.conf[0])
                cls_id = int(b.cls[0])
                boxes.append((x1, y1, x2, y2, conf, cls_id))
        return boxes

    def _process_pred(self, pred, img_shape):
        h, w = img_shape
        sx, sy = w / 640, h / 640
        boxes = []
        for row in pred:
            conf = float(row[4])
            if conf < 0.25: continue
            
            cls_id = int(row[5])
            b = row[:4]
            if b[2] < b[0] or b[3] < b[1]:
                cx, cy, wb, hb = b
                x1, y1, x2, y2 = cx - wb/2, cy - hb/2, cx + wb/2, cy + hb/2
            else:
                x1, y1, x2, y2 = b

            if x2 <= 1.05 and y2 <= 1.05:
                x1, y1, x2, y2 = x1 * 640, y1 * 640, x2 * 640, y2 * 640

            boxes.append((int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy), conf, cls_id))
        return boxes

    def close(self):
        if self._rknn:
            self._rknn.release()
            logger.info("PlateDetector: RKNN released")
        self._model = None

class OCRReader:
    def __init__(self):
        self._ocr = VNPlateOCR()
        logger.info("OCRReader initialised")

    def read(self, crop):
        return self._ocr.read(crop)

class FaceRecognizer:
    def __init__(self):
        from insightface.app import FaceAnalysis

        preferred = ["CPUExecutionProvider"]
        self._app = FaceAnalysis(name="buffalo_sc", root=cfg.FACE_MODEL_DIR, providers=preferred)
        self._app.prepare(ctx_id=-1, det_size=(640, 640))
        logger.info("FaceRecognizer initialized with buffalo_sc (InsightFace) on CPU")

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

        # Crop person ROI when available to optimize InsightFace inference on ARM
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
            # Fallback to full frame only if crop had no faces detected
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


class PersonTrack:
    def __init__(self, track_id: int, bbox: list):
        self.track_id = track_id
        self.last_bbox = bbox
        self.centroid = self._get_centroid(bbox)
        self.identity = "STRANGER"
        self.confidence = 0.0
        self.is_locked = False
        self.last_seen = time.time()
        self.frames_since_last_id = 0
        self.last_trigger_ts = 0.0

    def _get_centroid(self, bbox):
        return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

    def update(self, bbox):
        self.last_bbox = bbox
        self.centroid = self._get_centroid(bbox)
        self.last_seen = time.time()
        self.frames_since_last_id += 1

class SimpleTracker:
    def __init__(self, max_distance: int = 150, max_age: float = 3.0):
        self.tracks: list[PersonTrack] = []
        self._next_id = 1
        self.max_distance = max_distance
        self.max_age = max_age

    def update(self, detections: list[tuple]) -> list[tuple[tuple, PersonTrack]]:
        now = time.time()
        
        self.tracks = [t for t in self.tracks if now - t.last_seen < self.max_age]
        
        results = []
        unmatched_dets = list(detections)
        
        for track in self.tracks:
            if not unmatched_dets: break
            
            best_idx = -1
            min_dist = self.max_distance
            
            for i, det in enumerate(unmatched_dets):
                x1, y1, x2, y2, conf, cls_id = det
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                dist = np.sqrt((cx - track.centroid[0])**2 + (cy - track.centroid[1])**2)
                
                if dist < min_dist:
                    min_dist = dist
                    best_idx = i
            
            if best_idx != -1:
                det = unmatched_dets.pop(best_idx)
                track.update(det[:4])
                results.append((det, track))
        
        for det in unmatched_dets:
            new_track = PersonTrack(self._next_id, list(det[:4]))
            self._next_id += 1
            self.tracks.append(new_track)
            results.append((det, new_track))
            
        return results




class InferenceWorker:
    def __init__(self, infer_queue: Queue, recorder_control_queue: Queue, stop_event: Event):
        self.infer_queue = infer_queue
        self.recorder_control_queue = recorder_control_queue
        self._recorder_control_lock = threading.Lock()
        self.stop_event = stop_event

        self._plate_detector = None
        self._ocr = None
        self._face_recog = None

        self._voters: dict[str, PlateVoter] = {}
        self._shm_clients: dict[str, shared_memory.SharedMemory] = {}
        self._seen_plates: dict[str, float] = {}

        from backend.iot.door_auto_close import DoorAutoCloseManager
        self._door_manager = DoorAutoCloseManager()
        self._door_manager.reload_config()
        logger.info("DoorAutoCloseManager initialized")
        self._frame_counter = 0
        
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self._pending_tasks: set[concurrent.futures.Future] = set()
        self._last_face_reload = 0
        self._last_active_ts = time.time()
        self._idle_timeout = 300
        self._trackers: dict[str, SimpleTracker] = {}

    def _is_plate_whitelisted(self, plate_norm: str, site_id: Optional[int] = None) -> bool:
        if site_id:
            query = "SELECT 1 FROM plate_whitelist WHERE plate_number = %s AND site_ids @> %s::jsonb AND is_active = TRUE LIMIT 1"
            return db.execute_query(query, (plate_norm, json.dumps([site_id])), fetch=True) is not None
        query = "SELECT 1 FROM plate_whitelist WHERE plate_number = %s AND is_active = TRUE LIMIT 1"
        return db.execute_query(query, (plate_norm,), fetch=True) is not None

    def _record_vehicle_entry(self, plate_norm: str, vehicle_type: str, camera_id: str, image_path: str, site_id: Optional[int] = None) -> bool:
        active = db.execute_query(
            "SELECT id FROM vehicle_sessions WHERE plate_norm = %s AND status = 'parking' LIMIT 1",
            (plate_norm,),
            fetch=True
        )
        if active:
            saved = db.execute_query("""
                UPDATE vehicle_sessions
                SET entry_time = NOW(), camera_id_in = %s, entry_image = %s, site_id = %s
                WHERE id = %s
            """, (camera_id, image_path, site_id, active["id"]), commit=True)
        else:
            saved = db.execute_query("""
                INSERT INTO vehicle_sessions (plate_norm, vehicle_type, camera_id_in, entry_image, site_id, status)
                VALUES (%s, %s, %s, %s, %s, 'parking')
            """, (plate_norm, vehicle_type, camera_id, image_path, site_id), commit=True)
        if not saved:
            logger.error("Vehicle session write affected no rows")
            raise RuntimeError("Vehicle session was not stored")
        return True

    def _add_pending_plate(self, plate_norm: str, plate_raw: str, camera_id: Optional[str] = None, site_id: Optional[int] = None, image_path: Optional[str] = None, plate_type: str = "white") -> bool:
        clean_plate = re.sub(r'[^A-Z0-9]', '', plate_norm.upper())
        existing = db.execute_query("""
            SELECT pending_id FROM pending_plates
            WHERE regexp_replace(plate_norm, '[^a-zA-Z0-9]', '', 'g') = %s
            AND (LOWER(TRIM(status)) = 'pending'
                 OR confirmed_at_utc > NOW() - INTERVAL '60 seconds')
            LIMIT 1
        """, (clean_plate,), fetch=True)
        if existing:
            logger.info(f"Plate {plate_norm} pending or in 60s cooldown (match: {clean_plate}). Skipping.")
            return False

        pid = str(uuid.uuid4())[:8]
        logger.info(f"Inserting new unknown plate: {plate_norm} (site: {site_id}, cam: {camera_id})")
        saved = db.execute_query("""
            INSERT INTO pending_plates (pending_id, plate_raw, plate_norm, first_seen_utc, status, site_id, image_path, plate_type)
            VALUES (%s, %s, %s, NOW(), 'pending', %s, %s, %s)
            ON CONFLICT (pending_id) DO NOTHING
        """, (pid, plate_raw, plate_norm, site_id, image_path, plate_type), commit=True)
        if not saved:
            logger.error("Pending plate write affected no rows")
            raise RuntimeError("Pending plate was not stored")
        return True

    def _ensure_models_loaded(self):
        if self._plate_detector is not None:
            return

        logger.info("--- Lazy Loading AI Models (PlateDetector, OCR, FaceRecognizer) ---")
        st = time.time()
        self._plate_detector = PlateDetector()
        self._ocr = OCRReader()
        self._face_recog = FaceRecognizer()
        logger.info("--- AI Models Loaded successfully in %.2fs ---", time.time() - st)

    def _unload_models(self):
        if self._plate_detector is None:
            return
            
        logger.info("--- Idle Timeout: Releasing AI Models to free memory ---")
        self._plate_detector.close()
        self._plate_detector = None
        self._ocr = None
        self._face_recog = None
        logger.info("--- AI Models Unloaded and resources released. ---")

    def cleanup(self):
        logger.info("InferenceWorker cleaning up...")
        self._executor.shutdown(wait=True)
        self._unload_models()
        for shm in self._shm_clients.values():
            shm.close()
        self._check_async_tasks()
        logger.info("InferenceWorker cleanup complete")

    def _get_voter(self, cam_id: str) -> PlateVoter:
        if cam_id not in self._voters:
            self._voters[cam_id] = PlateVoter(buffer_size=cfg.FRAME_BUFFER_SIZE)
        return self._voters[cam_id]

    def _get_tracker(self, cam_id: str) -> SimpleTracker:
        if cam_id not in self._trackers:
            self._trackers[cam_id] = SimpleTracker(max_distance=200)
        return self._trackers[cam_id]

    def _get_shm_frame(self, shm_name: str):
        if shm_name not in self._shm_clients:
            client = shared_memory.SharedMemory(name=shm_name, create=False)
            try:
                from multiprocessing import resource_tracker
                resource_tracker.unregister(client._name, "shared_memory")
            except Exception:
                pass
            self._shm_clients[shm_name] = client
        shm = self._shm_clients[shm_name]
        counter = np.ndarray((1,), dtype=np.uint32, buffer=shm.buf[0:4])
        arr = np.ndarray((cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, 3), dtype=np.uint8, buffer=shm.buf[4:])
        for _ in range(3):
            c1 = int(counter[0])
            frame = arr.copy()
            if int(counter[0]) == c1:
                return frame
        logger.error("Shared frame changed during all read attempts")
        raise RuntimeError(f"Shared frame changed during all read attempts: {shm_name}")

    def _next_latest_token(self) -> dict | None:
        """Read one token, then collapse any queued stale frames at the queue boundary."""
        if not self.infer_queue._reader.poll(0.5):
            return None

        latest = self.infer_queue.get()
        while self.infer_queue._reader.poll():
            latest = self.infer_queue.get()
        return latest

    def _enqueue_recording_event(self, event: dict) -> None:
        if self.recorder_control_queue is None:
            logger.error("Recorder control queue is unavailable")
            raise RuntimeError("Recorder control queue is unavailable")
        with self._recorder_control_lock:
            if self.recorder_control_queue.full():
                logger.error("Recorder control queue is full; event was not delivered")
                raise RuntimeError("Recorder control queue is full; event was not delivered")
            self.recorder_control_queue.put_nowait(event)

    def run(self):
        logger.info("Inference worker started")
        reload_trigger = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
        
        def run_loop():
            while not self.stop_event.is_set():
                self._check_async_tasks()
                now = time.time()

                token = self._next_latest_token()
                if token is None:
                    if (now - self._last_active_ts > self._idle_timeout) and self._plate_detector is not None:
                        self._unload_models()

                    continue

                self._last_active_ts = now
                self._ensure_models_loaded()

                if self._frame_counter % 25 == 0 and os.path.exists(reload_trigger) and self._face_recog is not None:
                    mtime = os.path.getmtime(reload_trigger)
                    if mtime > self._last_face_reload:
                        self._face_recog.reload()
                        self._seen_plates.clear()
                        self._last_face_reload = mtime
                        logger.info("InferenceWorker: AI index synchronized via mtime: %s", mtime)
                cam_id, shm_name, ts = token["cam_id"], token["shm_name"], token["ts"]
                site_id = token.get("site_id")
                frame = self._get_shm_frame(shm_name)

                result = InferenceResult(cam_id=cam_id, ts=ts)
                result.annotated_frame = frame.copy()

                detections = self._plate_detector.detect(frame)
                detections = merge_plate_boxes(detections)
                
                person_dets = [d for d in detections if d[5] == 1]
                other_dets = [d for d in detections if d[5] != 1]
                
                tracker = self._get_tracker(cam_id)
                tracked_persons = tracker.update(person_dets)
                
                voter = self._get_voter(cam_id)
                
                for det, track in tracked_persons:
                    x1, y1, x2, y2, det_conf, _ = det
                    
                    display_name = track.identity
                    color = (0, 165, 255) if display_name == "STRANGER" else (0, 255, 0)
                    
                    cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(result.annotated_frame, f"{display_name} {det_conf:.2f}", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                    
                    if det_conf > 0.80 and track.identity == "STRANGER":
                        now_t = time.time()
                        if now_t - track.last_trigger_ts > 1.0:
                            self._enqueue_recording_event({
                                "cam_id": cam_id,
                                "event_text": "Person (Stranger)",
                                "ts": now_t,
                                "event_type": "person",
                                "site_id": site_id
                            })
                            track.last_trigger_ts = now_t
                
                for x1, y1, x2, y2, det_conf, cls_id in other_dets:
                    if cls_id == 0:
                        h_frame, w_frame = frame.shape[:2]
                        px = int((x2 - x1) * 0.15) + 10
                        py = int((y2 - y1) * 0.15) + 5
                        px1, py1 = max(0, x1 - px), max(0, y1 - py)
                        px2, py2 = min(w_frame, x2 + px), min(h_frame, y2 + py)
                        
                        crop = frame[py1:py2, px1:px2].copy()
                        if crop.size > 0:
                            self._run_async(self._async_ocr, crop, voter, cam_id, result)
                        cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), (255, 0, 255), 2)
                        cv2.putText(result.annotated_frame, "PLATE", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)
                    elif cls_id == 2:
                        cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
                        cv2.putText(result.annotated_frame, "DOOR", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)

                if self._door_manager:
                    self._door_manager.on_frame(
                        camera_id=cam_id,
                        detections=detections,
                        frame_height=frame.shape[0],
                    )

                if voter.is_ready():
                    best_text, best_conf = voter.best()
                    voter.reset()
                    if best_text:
                        result.plate_text = best_text
                        result.plate_conf = best_conf
                        if (time.time() - self._seen_plates.get(best_text, 0)) > 15:
                            self._seen_plates[best_text] = time.time()
                            if len(self._seen_plates) > 256:
                                _cut = time.time() - 60
                                self._seen_plates = {k: v for k, v in self._seen_plates.items() if v > _cut}
                            result.snapshot_path = save_snapshot(frame, cam_id, best_text)
                            
                            self._enqueue_recording_event({
                                "cam_id": cam_id,
                                "plate_text": best_text,
                                "ts": time.time(),
                                "event_type": "plate",
                                "site_id": site_id
                            })

                            normalized = cfg.normalize_plate(best_text)
                            self._run_async(self._record_vehicle_entry, normalized, "car", cam_id, result.snapshot_path, site_id)
                            if not self._is_plate_whitelisted(normalized, site_id):
                                logger.info(f"UNKNOWN PLATE DETECTED: {best_text}. Adding to pending list...")
                                self._run_async(self._add_pending_plate, normalized, best_text, cam_id, site_id, result.snapshot_path, result.plate_type)

                self._frame_counter += 1
                if self._frame_counter % cfg.FACE_RECOG_EVERY_N == 0:
                    active_tracks = self._get_tracker(cam_id).tracks
                    for track in active_tracks:
                        if not track.is_locked or track.frames_since_last_id > 100:
                            self._run_async(self._async_face_identify, frame, cam_id, site_id, result, track)

        run_loop()

    def _async_face_identify(self, frame, cam_id: str, site_id: int, result: InferenceResult, track: PersonTrack = None):
        if self._face_recog is None:
            return
        person_bbox = track.last_bbox if track else None
        name, sim, f_bbox, emb, direction = self._face_recog.identify(frame, person_bbox=person_bbox)
        result.face_name, result.face_conf = name, sim
        if name:
            if name == "STRANGER":
                if track and track.is_locked:
                    return
                
                from backend.ai.stranger_manager import get_stranger_manager
                mgr = get_stranger_manager()
                stranger_id, match_sim, is_new = mgr.process_stranger(
                    emb=emb, frame=frame, cam_id=cam_id,
                    bbox=f_bbox, direction=direction, site_id=site_id
                )
                result.face_name = f"Người lạ #{stranger_id}"
                if match_sim > 0:
                    result.face_conf = match_sim

                cv2.putText(result.annotated_frame, f"FACE: {result.face_name}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                
                if is_new:
                    self._enqueue_recording_event({
                        "cam_id": cam_id,
                        "event_text": f"New Stranger Detected #{stranger_id}",
                        "ts": time.time(),
                        "event_type": "stranger",
                        "site_id": site_id
                    })
            elif name and name != "STRANGER":
                cv2.putText(result.annotated_frame, f"FACE: {name}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                if track:
                    track.identity = name
                    track.confidence = sim
                    track.is_locked = True
                    track.frames_since_last_id = 0
                    logger.info(f"Fixed identity {name} to track {track.track_id} (sim: {sim:.2f})")

    def _check_async_tasks(self):
        completed = {task for task in self._pending_tasks if task.done()}
        self._pending_tasks.difference_update(completed)
        for task in completed:
            task.result()

    def _run_async(self, fn, *args):
        fut = self._executor.submit(fn, *args)
        self._pending_tasks.add(fut)
        return fut

    def _async_ocr(self, crop, voter: PlateVoter, cam_id: str, result: InferenceResult):
        if self._ocr is None:
            return
        if not validate_plate_crop(crop):
            logger.debug("Crop rejected by plate validation (cam: %s)", cam_id)
            return
        processed = enhance_plate(crop)
        text, ocr_conf, p_type = self._ocr.read(processed)
        if text:
            voter.add(text, ocr_conf)
            result.plate_type = p_type

            is_suspicious, susp_conf, reason = check_plate_authenticity(crop)
            if is_suspicious:
                logger.warning("SUSPICIOUS PLATE DETECTED on %s: %s (Reason: %s, Conf: %.2f)", cam_id, text, reason, susp_conf)

def inference_process(infer_queue: Queue, recorder_control_queue: Queue, stop_event: Event):
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    cfg.setup_logging("inference.log")

    worker = InferenceWorker(infer_queue, recorder_control_queue, stop_event)
    try:
        worker.run()
    finally:
        worker.cleanup()
