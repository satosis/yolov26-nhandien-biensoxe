import queue
import os
import time
import signal
import logging
import collections
import threading
import ssl
import certifi
import numpy as np
import cv2
import concurrent.futures
from dataclasses import dataclass, field
from multiprocessing import Process, Queue, Event, shared_memory
from typing import Optional, List, Tuple, Dict, Any

import onnxruntime as ort
import insightface
from insightface.app import FaceAnalysis
from ultralytics import YOLO

from backend.core import config as cfg
from backend.core.database import DatabaseManager
from backend.util import ocr_utils, image_utils, texture_utils
from backend.util.ocr_utils import VNPlateOCR
from backend.util.image_utils import save_face_snapshot, save_snapshot
from backend.util.texture_utils import check_plate_authenticity
from backend.util.bbox_utils import merge_plate_boxes

"""
parking_hpc/inference.py
Process 2 — AI Inference
"""

logger = logging.getLogger("inference")

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class InferenceResult:
    cam_id: str
    ts: float
    plate_text: str = ""
    plate_conf: float = 0.0
    plate_type: str = "white"
    plate_bbox: tuple = ()          # (x1, y1, x2, y2)
    face_name: str = ""
    face_conf: float = 0.0
    snapshot_path: str = ""
    annotated_frame: Optional[object] = field(default=None, repr=False)

# ── Plate validation ──────────────────────────────────────────────────────────

def validate_plate_crop(crop) -> bool:
    """Quick validation: is this crop likely a license plate?

    Checks aspect ratio, minimum size, brightness, and edge structure
    to filter out false-positive YOLO detections before OCR.
    """
    if crop is None or crop.size == 0:
        return False

    h, w = crop.shape[:2]
    aspect = w / max(h, 1)

    # 1. Aspect ratio: VN plates range ~1.0-5.5 (2-line: ~1.2-2.0, 1-line: ~2.5-5.0)
    if aspect < 0.8 or aspect > 6.0:
        return False

    # 2. Minimum size: too small = noise
    if w < 25 or h < 12:
        return False

    # 3. Brightness: very dark crops are unlikely plates
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if gray.mean() < 40:
        return False

    # 4. Edge structure: plates have text → edge density in a reasonable range
    edges = cv2.Canny(gray, 50, 150)
    edge_ratio = np.count_nonzero(edges) / (h * w)
    if edge_ratio < 0.03 or edge_ratio > 0.60:
        return False

    return True

# ── Plate enhancement ─────────────────────────────────────────────────────────

def enhance_plate(crop):
    """Smart zoom + enhance plate crop for better OCR.

    Always ensures the plate reaches a minimum height for reliable OCR.
    Small plates get aggressive upscale + full pipeline.
    Medium plates get moderate upscale + enhance.
    Large plates get CLAHE + sharpen only (no upscale).
    """
    if crop is None or crop.size == 0:
        return crop

    h, w = crop.shape[:2]
    MIN_OCR_HEIGHT = 100  # Target minimum height for reliable OCR

    if h < 40:
        # Very small: aggressive upscale (3-4x) + full pipeline
        scale = max(3, min(4, MIN_OCR_HEIGHT // max(h, 1)))
        denoised = cv2.bilateralFilter(crop, d=3, sigmaColor=15, sigmaSpace=15)
        upscaled = cv2.resize(denoised, (w * scale, h * scale),
                              interpolation=cv2.INTER_LANCZOS4)
    elif h < MIN_OCR_HEIGHT:
        # Small: moderate upscale (2-3x) to reach target
        scale = max(2, min(3, MIN_OCR_HEIGHT // max(h, 1)))
        denoised = cv2.bilateralFilter(crop, d=3, sigmaColor=15, sigmaSpace=15)
        upscaled = cv2.resize(denoised, (w * scale, h * scale),
                              interpolation=cv2.INTER_LANCZOS4)
    elif h < 200:
        # Medium: light upscale (2x) for clarity
        denoised = cv2.bilateralFilter(crop, d=3, sigmaColor=15, sigmaSpace=15)
        upscaled = cv2.resize(denoised, (w * 2, h * 2),
                              interpolation=cv2.INTER_LANCZOS4)
    else:
        # Large (>= 200px): no upscale needed
        upscaled = crop

    # CLAHE on L channel for contrast normalization
    lab = cv2.cvtColor(upscaled, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    cl = clahe.apply(l_ch)
    enhanced = cv2.cvtColor(cv2.merge((cl, a_ch, b_ch)), cv2.COLOR_LAB2BGR)

    # Controlled sharpening
    gaussian = cv2.GaussianBlur(enhanced, (0, 0), 1.0)
    sharpened = cv2.addWeighted(enhanced, 1.5, gaussian, -0.5, 0)

    return sharpened


# ── Plate buffer / voting ─────────────────────────────────────────────────────

class PlateVoter:
    def __init__(self, buffer_size: int = 5):
        self._buffer: collections.deque = collections.deque(maxlen=buffer_size)
        self._buffer_size = buffer_size
        self._min_conf = cfg.VOTE_MIN_CONF if hasattr(cfg, 'VOTE_MIN_CONF') else 0.3

    def add(self, text: str, conf: float):
        if text and conf >= self._min_conf:
            self._buffer.append((text, conf))

    def is_ready(self) -> bool:
        if len(self._buffer) == 0: return False
        if len(self._buffer) >= self._buffer_size: return True
        if len(self._buffer) >= 3:
            best_text, best_conf = self.best()
            if best_conf > 0.88: return True
        return False

    def best(self) -> tuple[str, float]:
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
        self._buffer.clear()

# ── Model wrappers ────────────────────────────────────────────────────────────

class PlateDetector:
    def __init__(self):
        self._rknn = None
        self._ort_session = None
        self._model = None
        self._engine_type = "cpu"

        # 1. Try RKNN (Rockchip NPU)
        rknn_path = getattr(cfg, 'RKNN_MODEL_PATH', None)
        if rknn_path and os.path.exists(rknn_path):
            try:
                from rknnlite.api import RKNNLite
                self._rknn = RKNNLite()
                ret = self._rknn.load_rknn(rknn_path)
                if ret == 0:
                    ret = self._rknn.init_runtime()
                if ret == 0:
                    self._engine_type = "rknn"
                    logger.info("PlateDetector: Successfully loaded RKNN (NPU) engine: %s", rknn_path)
                else:
                    logger.warning("PlateDetector: RKNN load/init failed (code: %d)", ret)
                    self._rknn = None
            except ImportError:
                pass # rknnlite not installed - normal for development machines
            except Exception as e:
                logger.warning("PlateDetector: RKNN initialization error: %s", e)

        # 2. Try ONNX with Hardware Acceleration (CoreML / TensorRT)
        if self._engine_type == "cpu":
            path = cfg.PLATE_MODEL_PATH
            onnx_path = path.replace(".pt", ".onnx")
            if os.path.exists(onnx_path):
                path = onnx_path
            
            is_onnx = path.endswith(".onnx")
            if is_onnx:
                self._model_path = path
                # Optimization: AI models (especially INT8 quantized) are often incompatible with Apple's ANE.
                # To avoid initialization delays and noisy logs, we detect INT8 models and default to CPU on Mac.
                is_int8 = "int8" in path.lower()
                providers = ['CPUExecutionProvider']
                provider_options = [{}]
                
                if not is_int8:
                    # For FP16/FP32 models, try CoreML but avoid the Neural Engine (ANE) which can be unstable
                    providers.insert(0, 'CoreMLExecutionProvider')
                    provider_options.insert(0, {"MLComputeUnits": "CPUAndGPU"})

                try:
                    self._ort_session = ort.InferenceSession(
                        path, 
                        providers=providers,
                        provider_options=provider_options
                    )
                    active_providers = self._ort_session.get_providers()
                    if "CoreMLExecutionProvider" in active_providers:
                        self._engine_type = "coreml"
                        logger.info("PlateDetector: Hardware Acceleration ENABLED (CoreML): %s", path)
                    else:
                        self._engine_type = "onnx_cpu"
                        logger.info("PlateDetector: ONNX loaded using CPU: %s", path)
                except Exception as e:
                    logger.warning("PlateDetector: ONNX init failed, trying CPU fallback: %s", e)
                    try:
                        self._ort_session = ort.InferenceSession(path, providers=['CPUExecutionProvider'])
                        self._engine_type = "onnx_cpu"
                    except Exception as e2:
                        logger.error("PlateDetector: ONNX CPU fallback failed: %s", e2)

        # 3. Fallback to Ultralytics YOLO (CPU)
        if self._engine_type == "cpu":
            try:
                self._model = YOLO(cfg.PLATE_MODEL_PATH, task="detect")
                self._model.to("cpu")
                self._engine_type = "yolo_cpu"
                logger.info("PlateDetector: Fallback to YOLO CPU: %s", cfg.PLATE_MODEL_PATH)
            except Exception as e:
                logger.critical("PlateDetector: CRITICAL FAILURE - No inference engine available: %s", e)

    def detect(self, frame):
        """Perform detection using the fastest available engine."""
        if self._engine_type == "rknn":
            return self._detect_rknn(frame)
        elif self._ort_session:
            return self._detect_onnx(frame)
        elif self._model:
            return self._detect_yolo(frame)
        return []

    def _detect_rknn(self, frame):
        """RKNN NPU Inference (Rockchip)."""
        # RKNN expects [640, 640, 3] usually
        blob = cv2.resize(frame, (640, 640))
        outputs = self._rknn.inference(inputs=[blob])
        # Format [1, 300, 6] -> [x1, y1, x2, y2, score, cls_id]
        pred = outputs[0][0]
        return self._process_pred(pred, frame.shape[:2])

    def _detect_onnx(self, frame):
        """ONNX Inference (CPU/CoreML) with automatic fallback."""
        try:
            input_name = self._ort_session.get_inputs()[0].name
            # blob size is usually 640x640 for this project
            blob = cv2.dnn.blobFromImage(frame, 1/255.0, (640, 640), swapRB=True)
            outputs = self._ort_session.run(None, {input_name: blob})
            pred = outputs[0][0]
            return self._process_pred(pred, frame.shape[:2])
        except Exception as e:
            if self._engine_type == "coreml":
                logger.error("CoreML execution failed (likely INT8 incompatibility), falling back to CPU: %s", e)
                self._engine_type = "onnx_cpu"
                try:
                    # Clear session and reload with CPU only
                    self._ort_session = ort.InferenceSession(self._model_path, providers=["CPUExecutionProvider"])
                    return self._detect_onnx(frame) # Retry
                except Exception as e2:
                    logger.critical("Critical error: CPU fallback failed: %s", e2)
                    return []
            else:
                logger.error("ONNX inference error: %s", e)
                return []

    def _detect_yolo(self, frame):
        """Fallback YOLO CPU Inference."""
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
        """Shared post-processing for ONNX/RKNN outputs."""
        h, w = img_shape
        sx, sy = w / 640, h / 640
        boxes = []
        for row in pred:
            conf = float(row[4])
            if conf < 0.25: continue
            
            cls_id = int(row[5])
            b = row[:4]
            # Standard YOLO output scaling
            if b[2] < b[0] or b[3] < b[1]: # cx,cy,w,h
                cx, cy, wb, hb = b
                x1, y1, x2, y2 = cx - wb/2, cy - hb/2, cx + wb/2, cy + hb/2
            else: # x1,y1,x2,y2
                x1, y1, x2, y2 = b

            # Scale to frame
            if x2 <= 1.05 and y2 <= 1.05: # Normalized
                x1, y1, x2, y2 = x1 * 640, y1 * 640, x2 * 640, y2 * 640

            boxes.append((int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy), conf, cls_id))
        return boxes

    def close(self):
        if self._rknn:
            self._rknn.release()
            logger.info("PlateDetector: RKNN released")
        self._ort_session = None
        self._model = None

class OCRReader:
    def __init__(self):
        self._ocr = VNPlateOCR()
        logger.info("OCRReader initialised")

    def read(self, crop):
        return self._ocr.read(crop)

class FaceRecognizer:
    def __init__(self):
        # Force CPUExecutionProvider for FaceAnalysis
        preferred = ["CPUExecutionProvider"]
        self._app = FaceAnalysis(name="buffalo_sc", root=cfg.FACE_MODEL_DIR, providers=preferred)
        self._app.prepare(ctx_id=-1, det_size=(320, 320)) # ctx_id=-1 for CPU
        logger.info("FaceRecognizer initialized with buffalo_sc (InsightFace) on CPU")
        
        self._names = []
        self._matrix = None
        
        self._load_known_faces()
        logger.info(f"FaceRecognizer ready with {len(self._names)} known identities")

    def reload(self):
        """Reload known faces from disk."""
        logger.info("FaceRecognizer: Reloading known faces...")
        self._load_known_faces()
        logger.info(f"FaceRecognizer: Reload complete. {len(self._names)} identities loaded.")

    def _load_known_faces(self):
        if not os.path.isdir(cfg.KNOWN_FACES_DIR): return
        
        all_embs = []
        all_names = []
        
        for person in sorted(os.listdir(cfg.KNOWN_FACES_DIR)):
            person_dir = os.path.join(cfg.KNOWN_FACES_DIR, person)
            if not os.path.isdir(person_dir): continue
            
            embeddings = []
            for fname in os.listdir(person_dir):
                if not fname.lower().endswith((".jpg", ".jpeg", ".png")): continue
                img = cv2.imread(os.path.join(person_dir, fname))
                if img is None: continue
                faces = self._app.get(img)
                if faces: embeddings.append(faces[0].normed_embedding)
            
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
        """Map [pitch, yaw, roll] to descriptive orientation labels."""
        if pose is None or len(pose) < 2:
            return "frontal"
        pitch, yaw, roll = pose
        if yaw > 25: return "right"  # Turning head left exposes right cheek
        if yaw < -25: return "left"  # Turning head right exposes left cheek
        if pitch > 20: return "up"
        if pitch < -20: return "down"
        return "frontal"

    def _score_to_percent(self, sim: float) -> float:
        lower, upper = 0.40, 0.80
        percent = (sim - lower) / (upper - lower)
        return float(np.clip(percent, 0.0, 1.0))

    def identify(self, frame) -> tuple[str, float, list[int], Optional[np.ndarray], str]:
        """Recognize a face and return (name, similarity, bbox, embedding, direction)."""
        faces = self._app.get(frame)
        if not faces: return "", 0.0, [], None, "frontal"
        
        faces = sorted(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]), reverse=True)
        face = faces[0]
        
        w, h = face.bbox[2]-face.bbox[0], face.bbox[3]-face.bbox[1]
        emb = face.normed_embedding
        bbox = list(map(int, face.bbox))
        
        # Get head orientation
        pose = getattr(face, 'pose', None)
        direction = self._map_pose_to_direction(pose)
        
        if w < 40 or h < 40:
            return "TOO_SMALL", 0.0, bbox, emb, direction

        if self._matrix is None:
            return "STRANGER", 0.0, bbox, emb, direction

        similarities = np.dot(self._matrix, emb)
        idx = np.argmax(similarities)
        best_sim = float(similarities[idx])
        
        if best_sim >= 0.40:
            return self._names[idx], self._score_to_percent(best_sim), bbox, emb, direction
        return "STRANGER", 0.0, bbox, emb, direction

    def close(self):
        """Release InsightFace resources."""
        # InsightFace doesn't provide a public .close(), but we can clear the app ref
        self._app = None
        logger.info("FaceRecognizer session released")

# ── Helpers ───────────────────────────────────────────────────────────────────


# ── Inference worker ──────────────────────────────────────────────────────────

class InferenceWorker:
    def __init__(self, infer_queue: Queue, result_queue: Queue, recorder_control_queue: Queue, stop_event: Event):
        self.infer_queue = infer_queue
        self.result_queue = result_queue
        self.recorder_control_queue = recorder_control_queue
        self.stop_event = stop_event

        # Defer model loading until needed (Lazy Loading)
        self._plate_detector = None
        self._ocr = None
        self._face_recog = None
        self._db = DatabaseManager()

        self._voters: dict[str, PlateVoter] = {}
        self._shm_clients: dict[str, shared_memory.SharedMemory] = {}
        self._seen_plates: dict[str, float] = {}

        # Door auto-close manager (lazy init)
        self._door_manager = None
        try:
            from backend.core.door_auto_close import DoorAutoCloseManager
            self._door_manager = DoorAutoCloseManager()
            self._door_manager.reload_config(self._db)
            logger.info("DoorAutoCloseManager initialized")
        except Exception as e:
            logger.debug(f"DoorAutoCloseManager not available: {e}")
        self._recent_strangers: dict[str, list] = {} # cam_id -> list of {'emb': np.ndarray, 'ts': float}
        self._frame_counter = 0
        
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self._last_face_reload = 0
        self._last_active_ts = time.time()
        self._idle_timeout = 300 # 5 minutes

    def _ensure_models_loaded(self):
        """Lazy load AI models only when a frame is received."""
        if self._plate_detector is not None:
            return

        logger.info("--- Lazy Loading AI Models (PlateDetector, OCR, FaceRecognizer) ---")
        st = time.time()
        try:
            self._plate_detector = PlateDetector()
            self._ocr = OCRReader()
            self._face_recog = FaceRecognizer()
            logger.info("--- AI Models Loaded successfully in %.2fs ---", time.time() - st)
        except Exception as e:
            logger.error("CRITICAL ERROR loading AI models: %s", e, exc_info=True)

    def _unload_models(self):
        """Release AI models to free system memory during idle periods."""
        if self._plate_detector is None:
            return
            
        logger.info("--- Idle Timeout: Releasing AI Models to free memory ---")
        try:
            if hasattr(self._plate_detector, 'close'):
                self._plate_detector.close()
            if hasattr(self._face_recog, 'close'):
                self._face_recog.close()
            
            self._plate_detector = None
            self._ocr = None
            self._face_recog = None
            logger.info("--- AI Models Unloaded and resources released. ---")
        except Exception as e:
            logger.warning("Error during model unloading: %s", e)

    def cleanup(self):
        """Explicitly release all AI resources."""
        logger.info("InferenceWorker cleaning up...")
        self._unload_models() # Release models if loaded
        self._executor.shutdown(wait=True)
        # Close SHM clients
        for name, shm in list(self._shm_clients.items()):
            try:
                shm.close()
            except: pass
        logger.info("InferenceWorker cleanup complete")

    def _get_voter(self, cam_id: str) -> PlateVoter:
        if cam_id not in self._voters:
            self._voters[cam_id] = PlateVoter(buffer_size=cfg.FRAME_BUFFER_SIZE)
        return self._voters[cam_id]

    def _get_shm_frame(self, shm_name: str):
        if shm_name not in self._shm_clients:
            self._shm_clients[shm_name] = shared_memory.SharedMemory(name=shm_name, create=False)
        shm = self._shm_clients[shm_name]
        # [0..3] is counter, [4..] is image
        arr = np.ndarray((cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, 3), dtype=np.uint8, buffer=shm.buf[4:])
        return arr.copy()

    def run(self):
        logger.info("Inference worker started")
        reload_trigger = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
        
        try:
            while not self.stop_event.is_set():
                now = time.time()

                if self.infer_queue.empty():
                    # Check for idle timeout (e.g. 5 mins) to unload models
                    if (now - self._last_active_ts > self._idle_timeout) and self._plate_detector is not None:
                        self._unload_models()

                    time.sleep(0.1)
                    continue

                # Frame received! Update activity and ensure models are loaded
                self._last_active_ts = now
                self._ensure_models_loaded()

                # Check for face model reload trigger
                # Check for face model reload trigger via mtime sync
                if self._frame_counter % 25 == 0 and os.path.exists(reload_trigger) and self._face_recog is not None:
                    mtime = os.path.getmtime(reload_trigger)
                    if mtime > self._last_face_reload:
                        self._face_recog.reload()
                        self._seen_plates.clear() # Reset recognition history
                        self._recent_strangers.clear()
                        self._last_face_reload = mtime
                        logger.info("InferenceWorker: AI index synchronized via mtime: %s", mtime)
                token = self.infer_queue.get(timeout=0.5)
                
                cam_id, shm_name, ts = token["cam_id"], token["shm_name"], token["ts"]
                site_id = token.get("site_id")
                frame = self._get_shm_frame(shm_name)
                if frame is None: continue

                result = InferenceResult(cam_id=cam_id, ts=ts)
                result.annotated_frame = frame 

                detections = self._plate_detector.detect(frame)
                # Merge 2-line plate detections into single bounding boxes
                detections = merge_plate_boxes(detections)
                voter = self._get_voter(cam_id)
                
                for x1, y1, x2, y2, det_conf, cls_id in detections:
                    if cls_id == 0: # Plate
                        # Padding: Expand bbox by ~15% (min 10px) to prevent character/punctuation clipping
                        h_frame, w_frame = frame.shape[:2]
                        px = int((x2 - x1) * 0.15) + 10
                        py = int((y2 - y1) * 0.15) + 5
                        px1, py1 = max(0, x1 - px), max(0, y1 - py)
                        px2, py2 = min(w_frame, x2 + px), min(h_frame, y2 + py)
                        
                        crop = frame[py1:py2, px1:px2].copy()
                        if crop.size > 0:
                            self._executor.submit(self._async_ocr, crop, det_conf, voter, cam_id, frame, result)
                        cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), (255, 0, 255), 2)
                        cv2.putText(result.annotated_frame, "PLATE", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)
                    elif cls_id == 1: # Person
                        cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), (0, 165, 255), 2) # Amber
                        cv2.putText(result.annotated_frame, f"PERSON {det_conf:.2f}", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)
                    elif cls_id == 2: # Door
                        cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), (255, 0, 0), 2) # Blue
                        cv2.putText(result.annotated_frame, "DOOR", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)

                # Feed detections to door auto-close manager
                if self._door_manager:
                    self._door_manager.on_frame(
                        camera_id=cam_id,
                        detections=detections,
                        frame_height=frame.shape[0],
                        db=self._db,
                    )

                if voter.is_ready():
                    best_text, best_conf = voter.best()
                    voter.reset()
                    if best_text:
                        result.plate_text = best_text
                        result.plate_conf = best_conf
                        # The color is added to result via _async_ocr callback usually,
                        # but since InferenceResult is mutable, it's already there if detected.
                        if (time.time() - self._seen_plates.get(best_text, 0)) > 15:
                            self._seen_plates[best_text] = time.time()
                            result.snapshot_path = save_snapshot(frame, cam_id, best_text)
                            
                            # Trigger Smart Recording
                            if self.recorder_control_queue and not self.recorder_control_queue.full():
                                self.recorder_control_queue.put_nowait({
                                    "cam_id": cam_id,
                                    "plate_text": best_text,
                                    "ts": time.time()
                                })

                            self._executor.submit(self._db.record_vehicle_entry, best_text, "car", cam_id, result.snapshot_path, site_id)
                            if not self._db.is_plate_whitelisted(best_text, site_id):
                                logger.info(f"UNKNOWN PLATE DETECTED: {best_text}. Adding to pending list...")
                                # Use centralized normalization to handle spaces, dashes, and dots consistently for lookup
                                normalized = cfg.normalize_plate(best_text)
                                self._executor.submit(self._db.add_pending_plate_simple, normalized, best_text, cam_id, site_id, result.snapshot_path, result.plate_type)

                self._frame_counter += 1
                if self._frame_counter % cfg.FACE_RECOG_EVERY_N == 0:
                    self._executor.submit(self._async_face_identify, frame, cam_id, site_id, result)

                if not self.result_queue.full():
                    self.result_queue.put_nowait(result)
        except Exception as e:
            logger.critical("InferenceWorker CRASHED: %s", str(e), exc_info=True)
            raise

    def _async_face_identify(self, frame, cam_id: str, site_id: int, result: InferenceResult):
        if self._face_recog is None:
            return
        name, sim, f_bbox, emb, direction = self._face_recog.identify(frame)
        result.face_name, result.face_conf = name, sim
        if name:
            color = (0, 255, 0) if name != "STRANGER" else (0, 0, 255)
            cv2.putText(result.annotated_frame, f"FACE: {name}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            
            if name == "STRANGER" and emb is not None:
                # Advanced Deduplication: minimize photos for same stranger
                now = time.time()
                if cam_id not in self._recent_strangers:
                    self._recent_strangers[cam_id] = []
                
                # Cleanup old cache entries (> 5 min)
                self._recent_strangers[cam_id] = [s for s in self._recent_strangers[cam_id] if now - s['ts'] < 300]
                
                is_duplicate = False
                for s in self._recent_strangers[cam_id]:
                    # Cosine similarity (dot product since they are normed)
                    similarity = np.dot(emb, s['emb'])
                    if similarity > 0.70: # Same person
                        if now - s['ts'] < 60: # Within 1 min window
                            is_duplicate = True
                            break
                
                if not is_duplicate:
                    from backend.util.image_utils import save_face_snapshot
                    face_snap = save_face_snapshot(frame, cam_id, bbox=f_bbox)
                    if face_snap:
                        self._executor.submit(self._db.log_stranger, cam_id, sim, face_snap, site_id, emb.tobytes(), direction)
                        self._recent_strangers[cam_id].append({'emb': emb, 'ts': now})
                    logger.info("New stranger logged for cam %s (sim: %f)", cam_id, sim)

    def _async_ocr(self, crop, det_conf: float, voter: PlateVoter, cam_id: str, full_frame, result: InferenceResult):
        if self._ocr is None:
            return
        # Validate: is this crop actually a license plate?
        if not validate_plate_crop(crop):
            logger.debug("Crop rejected by plate validation (cam: %s)", cam_id)
            return
        # Smart zoom: always enhance (scale adapts to crop size)
        processed = enhance_plate(crop)
        text, ocr_conf, p_type = self._ocr.read(processed)
        if text:
            voter.add(text, ocr_conf * det_conf)
            result.plate_type = p_type

            # Check for Fake / Obstructed Plate
            is_suspicious, susp_conf, reason = check_plate_authenticity(crop)
            if is_suspicious:
                logger.warning("SUSPICIOUS PLATE DETECTED on %s: %s (Reason: %s, Conf: %.2f)", cam_id, text, reason, susp_conf)

def inference_process(infer_queue: Queue, result_queue: Queue, recorder_control_queue: Queue, stop_event: Event):
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    worker = InferenceWorker(infer_queue, result_queue, recorder_control_queue, stop_event)
    worker.run()
    worker.cleanup()
