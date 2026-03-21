"""
parking_hpc/inference.py
Process 2 — AI Inference
"""
import os
import time
import signal
import logging
import collections
from dataclasses import dataclass, field
from multiprocessing import Process, Queue, Event, shared_memory
from typing import Optional

# NO top-level cv2 or numpy imports to avoid Mac SegFaults

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
    """Lazy imports for cv2 inside function."""
    import cv2
    import numpy as np
    h, w = crop.shape[:2]
    upscaled = cv2.resize(crop, (w * 2, h * 2), interpolation=cv2.INTER_LINEAR)
    gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    binary = cv2.adaptiveThreshold(
        blurred, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 11, 2,
    )
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)

# ── Plate buffer / voting ─────────────────────────────────────────────────────

class PlateVoter:
    def __init__(self, buffer_size: int = 5):
        from backend.core import config as cfg
        self._buffer: collections.deque = collections.deque(maxlen=buffer_size)
        self._buffer_size = buffer_size
        self._min_conf = cfg.VOTE_MIN_CONF if hasattr(cfg, 'VOTE_MIN_CONF') else 0.3

    def add(self, text: str, conf: float):
        if text and conf >= self._min_conf:
            self._buffer.append((text, conf))

    def is_ready(self) -> bool:
        return len(self._buffer) >= self._buffer_size

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
        from ultralytics import YOLO
        from backend.core import config as cfg
        path = cfg.PLATE_MODEL_PATH
        onnx_path = path.replace(".pt", ".onnx")
        if os.path.exists(onnx_path):
            path = onnx_path
        self._model = YOLO(path, task="detect")
        logger.info("PlateDetector loaded: %s", path)

    def detect(self, frame):
        import numpy as np
        from backend.core import config as cfg
        imgsz = getattr(cfg, "PLATE_DETECT_IMGSZ", 416)
        if ".onnx" in str(getattr(self._model, 'model', '')):
             imgsz = 640 
             
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

class OCRReader:
    def __init__(self):
        from backend.util.ocr_utils import VNPlateOCR
        self._ocr = VNPlateOCR()
        logger.info("OCRReader initialised")

    def read(self, crop):
        return self._ocr.read(crop)

class FaceRecognizer:
    def __init__(self):
        import onnxruntime as ort
        from backend.core import config as cfg
        providers = ort.get_available_providers()
        preferred = ["CPUExecutionProvider"]
        if "OpenCLExecutionProvider" in providers:
            preferred.insert(0, "OpenCLExecutionProvider")

        from insightface.app import FaceAnalysis
        self._app = FaceAnalysis(name="buffalo_sc", root=cfg.FACE_MODEL_DIR, providers=preferred)
        self._app.prepare(ctx_id=0, det_size=(320, 320))
        self._known: dict[str, object] = {} 
        self._load_known_faces()
        logger.info("FaceRecognizer ready")

    def _load_known_faces(self):
        import cv2
        import numpy as np
        from backend.core import config as cfg
        if not os.path.isdir(cfg.KNOWN_FACES_DIR): return
        for person in os.listdir(cfg.KNOWN_FACES_DIR):
            person_dir = os.path.join(cfg.KNOWN_FACES_DIR, person)
            if not os.path.isdir(person_dir): continue
            embeddings = []
            for fname in os.listdir(person_dir):
                if not fname.lower().endswith((".jpg", ".jpeg", ".png")): continue
                img = cv2.imread(os.path.join(person_dir, fname))
                if img is None: continue
                faces = self._app.get(img)
                if faces: embeddings.append(faces[0].normed_embedding)
            if embeddings: self._known[person] = np.mean(embeddings, axis=0)

    def identify(self, frame) -> tuple[str, float, list[int]]:
        """Return (name, similarity, bbox) for the most prominent face, or ('', 0, [])."""
        import numpy as np
        faces = self._app.get(frame)
        if not faces: return "", 0.0, []
        
        # Sort by box area to get the most prominent face
        faces = sorted(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]), reverse=True)
        face = faces[0]
        emb = face.normed_embedding
        bbox = list(map(int, face.bbox)) # [x1, y1, x2, y2]

        best_name, best_sim = "STRANGER", 0.0
        for name, ref_emb in self._known.items():
            sim = float(np.dot(emb, ref_emb))
            if sim > best_sim:
                best_sim = sim
                best_name = name if sim > 0.35 else "STRANGER"
        
        return best_name, best_sim, bbox

# ── Helpers ───────────────────────────────────────────────────────────────────

def save_snapshot(frame, cam_id: str, plate_text: str) -> str:
    import cv2
    from backend.core import config as cfg
    os.makedirs(cfg.SNAPSHOT_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_plate = plate_text.replace("/", "_").replace("\\", "_") or "unknown"
    path = os.path.join(cfg.SNAPSHOT_DIR, f"{cam_id}_{safe_plate}_{ts}.jpg")
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return path

# ── Inference worker ──────────────────────────────────────────────────────────

class InferenceWorker:
    def __init__(self, infer_queue: Queue, result_queue: Queue, stop_event: Event):
        from backend.core.database import DatabaseManager
        from backend.core import config as cfg
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
        self._frame_counter = 0
        
        import concurrent.futures
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    def _get_voter(self, cam_id: str) -> PlateVoter:
        if cam_id not in self._voters:
            from backend.core import config as cfg
            self._voters[cam_id] = PlateVoter(buffer_size=cfg.FRAME_BUFFER_SIZE)
        return self._voters[cam_id]

    def _get_shm_frame(self, shm_name: str):
        import numpy as np
        from backend.core import config as cfg
        try:
            if shm_name not in self._shm_clients:
                self._shm_clients[shm_name] = shared_memory.SharedMemory(name=shm_name, create=False)
            shm = self._shm_clients[shm_name]
            arr = np.ndarray((cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, 3), dtype=np.uint8, buffer=shm.buf[4:])
            return arr.copy()
        except Exception as e:
            logger.warning("SHM error: %s", e)
            self._shm_clients.pop(shm_name, None)
            return None

    def run(self):
        import cv2
        from backend.core import config as cfg
        logger.info("Inference worker started")
        while not self.stop_event.is_set():
            try: token = self.infer_queue.get(timeout=0.5)
            except Exception: continue

            cam_id, shm_name, ts = token["cam_id"], token["shm_name"], token["ts"]
            site_id = token.get("site_id")
            frame = self._get_shm_frame(shm_name)
            if frame is None: continue

            result = InferenceResult(cam_id=cam_id, ts=ts)
            result.annotated_frame = frame 

            # ── Plate detection ───────────────────────────────────────────────
            detections = self._plate_detector.detect(frame)
            voter = self._get_voter(cam_id)
            
            for x1, y1, x2, y2, det_conf, cls_id in detections:
                if cls_id == 0: # Assume 0 is Plate
                    crop = frame[y1:y2, x1:x2].copy()
                    if crop.size > 0:
                        self._executor.submit(self._async_ocr, crop, det_conf, voter, cam_id, frame)
                    cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), (255, 0, 255), 2)

            if voter.is_ready():
                best_text, best_conf = voter.best()
                voter.reset()
                if best_text:
                    result.plate_text = best_text
                    result.plate_conf = best_conf
                    if (time.time() - self._seen_plates.get(best_text, 0)) > 30:
                        self._seen_plates[best_text] = time.time()
                        result.snapshot_path = save_snapshot(frame, cam_id, best_text)
                        self._executor.submit(self._db.record_vehicle_entry, best_text, "car", cam_id, result.snapshot_path, site_id)
                        if not self._db.is_plate_whitelisted(best_text, site_id):
                            self._executor.submit(self._db.add_pending_plate_simple, best_text, best_text, cam_id, site_id)

            # ── Face recognition ─────────────────────────────────────────────
            self._frame_counter += 1
            if self._frame_counter % cfg.FACE_RECOG_EVERY_N == 0:
                name, sim = self._face_recog.identify(frame)
                result.face_name, result.face_conf = name, sim
                if name:
                    color = (0, 255, 0) if name != "STRANGER" else (0, 0, 255)
                    cv2.putText(result.annotated_frame, f"FACE: {name}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                    if name == "STRANGER":
                        from backend.util.image_utils import save_face_snapshot
                        face_snap = save_face_snapshot(frame, cam_id)
                        self._executor.submit(self._db.log_stranger, cam_id, sim, face_snap, site_id)

            if not self.result_queue.full():
                self.result_queue.put_nowait(result)

    def _async_ocr(self, crop, det_conf: float, voter: PlateVoter, cam_id: str, full_frame):
        try:
            h, w = crop.shape[:2]
            processed = enhance_plate(crop) if h < 50 else crop
            text, ocr_conf = self._ocr.read(processed)
            if text: voter.add(text, ocr_conf * det_conf)
        except Exception as e: logger.error("Async OCR error: %s", e)

def inference_process(infer_queue: Queue, result_queue: Queue, stop_event: Event):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    worker = InferenceWorker(infer_queue, result_queue, stop_event)
    worker.run()
