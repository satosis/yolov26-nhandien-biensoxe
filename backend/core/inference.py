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
from backend.util import ocr_utils, image_utils
from backend.util.ocr_utils import VNPlateOCR
from backend.util.image_utils import save_face_snapshot, save_snapshot

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
    plate_bbox: tuple = ()          # (x1, y1, x2, y2)
    face_name: str = ""
    face_conf: float = 0.0
    snapshot_path: str = ""
    annotated_frame: Optional[object] = field(default=None, repr=False)

# ── Plate enhancement ─────────────────────────────────────────────────────────

def enhance_plate(crop):
    """Deep enhancement for small/blurry plates: Denoise + Upscale + CLAHE + Stronger Sharpen."""
    if crop is None or crop.size == 0:
        return crop
    
    # 1. Edge-preserving Denoising (Bilateral)
    # Cleans up camera noise before upscaling to avoid 'magnifying' artifacts
    denoised = cv2.bilateralFilter(crop, d=7, sigmaColor=50, sigmaSpace=50)
    
    h, w = denoised.shape[:2]
    
    # 2. Upscale 4x (Lanczos) for maximum detail
    upscaled = cv2.resize(denoised, (w * 4, h * 4), interpolation=cv2.INTER_LANCZOS4)
    
    # 3. CLAHE (Contrast Limited Adaptive Histogram Equalization) on Lab format
    # Slightly higher clipLimit for small distant objects
    lab = cv2.cvtColor(upscaled, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    cl = clahe.apply(l)
    limg = cv2.merge((cl, a, b))
    enhanced = cv2.cvtColor(limg, cv2.COLOR_LAB2BGR)
    
    # 4. Stronger Unsharp Mask (Sharpening)
    # Weighting increased to 1.8 for crisper character edges
    gaussian = cv2.GaussianBlur(enhanced, (0, 0), 2.5)
    sharpened = cv2.addWeighted(enhanced, 1.8, gaussian, -0.8, 0)
    
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
        path = cfg.PLATE_MODEL_PATH
        onnx_path = path.replace(".pt", ".onnx")
        if os.path.exists(onnx_path):
            path = onnx_path
        
        self._path = path
        self._is_onnx = path.endswith(".onnx")
        self._ort_session = None

        if self._is_onnx:
            # Strictly use CPU to avoid GPU driver context leaks
            preferred = ["CPUExecutionProvider"]
            sess_opt = ort.SessionOptions()
            sess_opt.enable_mem_pattern = False
            self._ort_session = ort.InferenceSession(path, sess_opt, providers=preferred)
            logger.info("PlateDetector loaded via ONNXRuntime (CPU-ONLY): %s", path)

        if self._is_onnx and self._ort_session:
            self._model = None
        else:
            # Force CPU for YOLO fallback to avoid GPU context leaks
            self._model = YOLO(path, task="detect")
            self._model.to("cpu")
            logger.info("PlateDetector initialized via YOLO (FORCED CPU): %s", path)

    def detect(self, frame):
        """Perform plate detection using ONNX (preferred) or YOLO (fallback)."""
        if self._ort_session:
            input_name = self._ort_session.get_inputs()[0].name
            # Input is 640x640 for this model
            blob = cv2.dnn.blobFromImage(frame, 1/255.0, (640, 640), swapRB=True)
            outputs = self._ort_session.run(None, {input_name: blob})
            
            # E2E format detection: [1, 300, 6] -> [x1, y1, x2, y2, score, cls_id]
            pred = outputs[0][0]
            
            h, w = frame.shape[:2]
            sx, sy = w / 640, h / 640
            
            boxes = []
            for row in pred:
                conf = float(row[4])
                if conf < 0.25: continue
                
                cls_id = int(row[5])
                b = row[:4]
                # Robust coordinate handling for different YOLO export formats
                # Check if it's likely [cx, cy, w, h] by seeing if width/height are smaller than min/max
                if b[2] < b[0] or b[3] < b[1]: 
                    cx, cy, w_box, h_box = b
                    x1, y1, x2, y2 = cx - w_box/2, cy - h_box/2, cx + w_box/2, cy + h_box/2
                else: # Assume [x1, y1, x2, y2]
                    x1, y1, x2, y2 = b

                # If coordinates are normalized (0-1), scale to 640 (model input size)
                if x2 <= 1.05 and y2 <= 1.05: # A small buffer for floating point inaccuracies
                    x1, y1, x2, y2 = x1 * 640, y1 * 640, x2 * 640, y2 * 640

                # Scale to source image resolution
                x1 = int(x1 * sx)
                y1 = int(y1 * sy)
                x2 = int(x2 * sx)
                y2 = int(y2 * sy)
                
                boxes.append((x1, y1, x2, y2, conf, cls_id))
            return boxes

        # Fallback to YOLO if ONNX is missing or failed
        if self._model:
            imgsz = 640 if self._is_onnx else 416
            results = self._model(frame, imgsz=imgsz, conf=0.25, verbose=False)
            boxes = []
            for r in results:
                if not r.boxes: continue
                for b in r.boxes:
                    x1, y1, x2, y2 = map(int, b.xyxy[0])
                    conf = float(b.conf[0])
                    cls_id = int(b.cls[0])
                    boxes.append((x1, y1, x2, y2, conf, cls_id))
            return boxes
        return []

    def close(self):
        """Explicitly release all resources."""
        if self._ort_session:
            self._ort_session = None
            logger.info("PlateDetector session released")
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
        
        if w < 60 or h < 60:
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
    def __init__(self, infer_queue: Queue, result_queue: Queue, stop_event: Event):
        self.infer_queue = infer_queue
        self.result_queue = result_queue
        self.stop_event = stop_event

        self._plate_detector = PlateDetector()
        self._ocr = OCRReader()
        self._face_recog = FaceRecognizer()
        self._db = DatabaseManager()

        self._voters: dict[str, PlateVoter] = {}
        self._shm_clients: dict[str, shared_memory.SharedMemory] = {}
        self._seen_plates: dict[str, float] = {}
        self._recent_strangers: dict[str, list] = {} # cam_id -> list of {'emb': np.ndarray, 'ts': float}
        self._frame_counter = 0
        
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self._last_face_reload = 0

    def cleanup(self):
        """Explicitly release all AI resources."""
        logger.info("InferenceWorker cleaning up...")
        if hasattr(self._plate_detector, 'close'):
            self._plate_detector.close()
        if hasattr(self._face_recog, 'close'):
            self._face_recog.close()
        self._executor.shutdown(wait=True)
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
        
        while not self.stop_event.is_set():
            # Check for face model reload trigger
            # Check for face model reload trigger via mtime sync
            if self._frame_counter % 25 == 0 and os.path.exists(reload_trigger):
                mtime = os.path.getmtime(reload_trigger)
                if mtime > self._last_face_reload:
                    self._face_recog.reload()
                    self._seen_plates.clear() # Reset recognition history
                    self._recent_strangers.clear()
                    self._last_face_reload = mtime
                    logger.info("InferenceWorker: AI index synchronized via mtime: %s", mtime)

            if self.infer_queue.empty():
                time.sleep(0.1)
                continue
            token = self.infer_queue.get(timeout=0.5)
            
            cam_id, shm_name, ts = token["cam_id"], token["shm_name"], token["ts"]
            site_id = token.get("site_id")
            frame = self._get_shm_frame(shm_name)
            if frame is None: continue

            result = InferenceResult(cam_id=cam_id, ts=ts)
            result.annotated_frame = frame 

            detections = self._plate_detector.detect(frame)
            voter = self._get_voter(cam_id)
            
            for x1, y1, x2, y2, det_conf, cls_id in detections:
                if cls_id == 0: # Plate
                    # Padding: Expand bbox by ~10% (min 5px) to prevent character clipping at edges
                    h_frame, w_frame = frame.shape[:2]
                    px = int((x2 - x1) * 0.10) + 5
                    py = int((y2 - y1) * 0.10) + 2
                    px1, py1 = max(0, x1 - px), max(0, y1 - py)
                    px2, py2 = min(w_frame, x2 + px), min(h_frame, y2 + py)
                    
                    crop = frame[py1:py2, px1:px2].copy()
                    if crop.size > 0:
                        self._executor.submit(self._async_ocr, crop, det_conf, voter, cam_id, frame)
                    cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), (255, 0, 255), 2)
                    cv2.putText(result.annotated_frame, "PLATE", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)
                elif cls_id == 1: # Person
                    cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), (0, 165, 255), 2) # Amber
                    cv2.putText(result.annotated_frame, f"PERSON {det_conf:.2f}", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)
                elif cls_id == 2: # Door
                    cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), (255, 0, 0), 2) # Blue
                    cv2.putText(result.annotated_frame, "DOOR", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)

            if voter.is_ready():
                best_text, best_conf = voter.best()
                voter.reset()
                if best_text:
                    result.plate_text = best_text
                    result.plate_conf = best_conf
                    if (time.time() - self._seen_plates.get(best_text, 0)) > 15:
                        self._seen_plates[best_text] = time.time()
                        result.snapshot_path = save_snapshot(frame, cam_id, best_text)
                        self._executor.submit(self._db.record_vehicle_entry, best_text, "car", cam_id, result.snapshot_path, site_id)
                        if not self._db.is_plate_whitelisted(best_text, site_id):
                            logger.info(f"UNKNOWN PLATE DETECTED: {best_text}. Adding to pending list...")
                            self._executor.submit(self._db.add_pending_plate_simple, best_text, best_text, cam_id, site_id, result.snapshot_path)

            self._frame_counter += 1
            if self._frame_counter % cfg.FACE_RECOG_EVERY_N == 0:
                self._executor.submit(self._async_face_identify, frame, cam_id, site_id, result)

            if not self.result_queue.full():
                self.result_queue.put_nowait(result)

    def _async_face_identify(self, frame, cam_id: str, site_id: int, result: InferenceResult):
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

    def _async_ocr(self, crop, det_conf: float, voter: PlateVoter, cam_id: str, full_frame):
        h, w = crop.shape[:2]
        # Increase threshold to 120px to capture medium-sized blurry plates
        processed = enhance_plate(crop) if h < 120 else crop
        text, ocr_conf = self._ocr.read(processed)
        if text: voter.add(text, ocr_conf * det_conf)

def inference_process(infer_queue: Queue, result_queue: Queue, stop_event: Event):
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    worker = InferenceWorker(infer_queue, result_queue, stop_event)
    worker.run()
    worker.cleanup()
