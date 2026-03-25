import queue
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
        if len(self._buffer) == 0: return False
        if len(self._buffer) >= self._buffer_size: return True
        # Adaptive Optimization: If we have at least 3 samples and the best one is highly confident,
        # return ready early to reduce perceived latency (200-400ms gain).
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

# Pre-import heavy libs to avoid per-frame overhead, but use guards for Mac safety
try:
    import numpy as np
    import cv2
    from ultralytics import YOLO
    import onnxruntime as ort
except ImportError:
    pass

class PlateDetector:
    def __init__(self):
        from backend.core import config as cfg
        path = cfg.PLATE_MODEL_PATH
        onnx_path = path.replace(".pt", ".onnx")
        if os.path.exists(onnx_path):
            path = onnx_path
        
        self._path = path
        self._is_onnx = path.endswith(".onnx")
        self._ort_session = None

        if self._is_onnx:
            try:
                providers = ort.get_available_providers()
                preferred = []
                # Expert: Allow disabling CoreML via env var to save RAM in frontend process
                use_coreml = os.environ.get("ORT_COREML_FLAGS") != "0"
                if use_coreml and "CoreMLExecutionProvider" in providers:
                    preferred.append("CoreMLExecutionProvider")
                preferred.append("CPUExecutionProvider")
                # Expert Optimization: Disable mem_pattern to potentially speed up dynamic shapes
                sess_opt = ort.SessionOptions()
                sess_opt.enable_mem_pattern = False
                self._ort_session = ort.InferenceSession(path, sess_opt, providers=preferred)
                logger.info("PlateDetector loaded via ONNXRuntime (CoreML enabled): %s", path)
            except Exception as e:
                logger.warning("Could not init ONNXRuntime for CoreML: %s. Using YOLO fallback.", e)

        if self._is_onnx and self._ort_session:
            self._model = None
            logger.info("PlateDetector using ONNXRuntime only (YOLO wrapper skipped): %s", path)
        else:
            self._model = YOLO(path, task="detect")
            logger.info("PlateDetector initialized via YOLO: %s", path)

    def detect(self, frame):
        from backend.core import config as cfg
        
        # Try CoreML-accelerated ONNXRuntime if available (10x faster on Mac)
        if self._ort_session:
            try:
                input_name = self._ort_session.get_inputs()[0].name
                # Expert: Faster blob creation without dnn.blobFromImage overhead if possible
                # However, dnn.blobFromImage is quite optimized, so we keep it but avoid re-imports
                blob = cv2.dnn.blobFromImage(frame, 1/255.0, (640, 640), swapRB=True)
                outputs = self._ort_session.run(None, {input_name: blob})

                pred = outputs[0][0].T  # [8400, n_outputs] — transpose once

                # Vectorized postprocessing: replace Python for-loop over 8400 anchors
                scores_all = pred[:, 4:]                                          # [8400, n_classes]
                class_ids  = np.argmax(scores_all, axis=1)                        # [8400]
                confs      = scores_all[np.arange(len(pred)), class_ids]          # [8400]
                keep       = confs > 0.25
                if not keep.any():
                    return []

                pred_k  = pred[keep]
                cids_k  = class_ids[keep].tolist()
                confs_k = confs[keep].tolist()

                h, w    = frame.shape[:2]
                sx, sy  = w / 640, h / 640
                cx = pred_k[:, 0] * sx;  cy = pred_k[:, 1] * sy
                cw = pred_k[:, 2] * sx;  ch = pred_k[:, 3] * sy
                x1s = (cx - cw / 2).astype(int).tolist()
                y1s = (cy - ch / 2).astype(int).tolist()
                bws = cw.astype(int).tolist()
                bhs = ch.astype(int).tolist()

                indices = cv2.dnn.NMSBoxes(
                    list(zip(x1s, y1s, bws, bhs)), confs_k, 0.25, 0.45
                )
                if len(indices) == 0:
                    return []
                return [
                    (x1s[i], y1s[i], x1s[i] + bws[i], y1s[i] + bhs[i], confs_k[i], cids_k[i])
                    for i in indices.flatten()
                ]
            except Exception as e:
                logger.error("Error in ORT inference: %s", e)
        
        if self._model is None:
            return []  # ORT-only mode, no fallback model
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
        import numpy as np
        
        providers = ort.get_available_providers()
        # NOTE: CoreMLExecutionProvider is currently INCOMPATIBLE with InsightFace buffalo_sc 
        # due to output shape mismatch error. Falling back to CPU/OpenCL.
        preferred = []
        if "OpenCLExecutionProvider" in providers:
            preferred.append("OpenCLExecutionProvider")
        preferred.append("CPUExecutionProvider")

        from insightface.app import FaceAnalysis
        self._app = FaceAnalysis(name="buffalo_sc", root=cfg.FACE_MODEL_DIR, providers=preferred)
        self._app.prepare(ctx_id=0, det_size=(320, 320))
        
        self._names = []
        self._matrix = None # Will hold [N, 512] embeddings
        
        self._load_known_faces()
        logger.info(f"FaceRecognizer ready with {len(self._names)} known identities")

    def _load_known_faces(self):
        import cv2
        import numpy as np
        from backend.core import config as cfg
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
                # Expert Tip: Average embeddings for stability + Normalize
                avg_emb = np.mean(embeddings, axis=0)
                avg_emb = avg_emb / np.linalg.norm(avg_emb)
                all_embs.append(avg_emb)
                all_names.append(person)
        
        if all_embs:
            self._matrix = np.array(all_embs) # [N, 512]
            self._names = all_names

    def _score_to_percent(self, sim: float) -> float:
        """Expert Calibration: Map ArcFace similarity [0.40, 0.80] to [0.0, 1.0]."""
        import numpy as np
        lower, upper = 0.40, 0.80
        percent = (sim - lower) / (upper - lower)
        return float(np.clip(percent, 0.0, 1.0))

    def identify(self, frame) -> tuple[str, float, list[int]]:
        """High-performance Face Identification."""
        import numpy as np
        faces = self._app.get(frame)
        if not faces: return "", 0.0, []
        
        # Prominence sorting
        faces = sorted(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]), reverse=True)
        face = faces[0]
        
        # Quality check
        w, h = face.bbox[2]-face.bbox[0], face.bbox[3]-face.bbox[1]
        if w < 50 or h < 50:
            return "TOO_SMALL", 0.0, list(map(int, face.bbox))

        emb = face.normed_embedding
        bbox = list(map(int, face.bbox))

        if self._matrix is None:
            return "STRANGER", 0.0, bbox

        # Vectorized Cosine Similarity
        similarities = np.dot(self._matrix, emb)
        idx = np.argmax(similarities)
        best_sim = float(similarities[idx])
        
        if best_sim >= 0.40:
            return self._names[idx], self._score_to_percent(best_sim), bbox
        return "STRANGER", 0.0, bbox

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
        if shm_name not in self._shm_clients:
            self._shm_clients[shm_name] = shared_memory.SharedMemory(name=shm_name, create=False)
        shm = self._shm_clients[shm_name]
        arr = np.ndarray((cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, 3), dtype=np.uint8, buffer=shm.buf[4:])
        return arr.copy()

    def run(self):
        import cv2
        from backend.core import config as cfg
        logger.info("Inference worker started")
        while not self.stop_event.is_set():
            try:
                token = self.infer_queue.get(timeout=0.5)
            except queue.Empty: # Timeout or empty queue
                continue
            
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
                if cls_id == 0: # Plate
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
            # Optimization: Run face recognition in thread pool to avoid blocking plate detection
            self._frame_counter += 1
            if self._frame_counter % cfg.FACE_RECOG_EVERY_N == 0:
                self._executor.submit(self._async_face_identify, frame, cam_id, site_id, result)

            if not self.result_queue.full():
                self.result_queue.put_nowait(result)

    def _async_face_identify(self, frame, cam_id: str, site_id: int, result: InferenceResult):
        from backend.core import config as cfg
        try:
            name, sim, f_bbox = self._face_recog.identify(frame)
            result.face_name, result.face_conf = name, sim
            if name:
                color = (0, 255, 0) if name != "STRANGER" else (0, 0, 255)
                # Note: modifying result.annotated_frame from thread is safe as long as we don't draw overlapping
                cv2.putText(result.annotated_frame, f"FACE: {name}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                if name == "STRANGER":
                    from backend.util.image_utils import save_face_snapshot
                    face_snap = save_face_snapshot(frame, cam_id)
                    self._executor.submit(self._db.log_stranger, cam_id, sim, face_snap, site_id)
        except Exception as e:
            logger.error("Error in async face identity: %s", e)

    def _async_ocr(self, crop, det_conf: float, voter: PlateVoter, cam_id: str, full_frame):
        h, w = crop.shape[:2]
        processed = enhance_plate(crop) if h < 50 else crop
        text, ocr_conf = self._ocr.read(processed)
        if text: voter.add(text, ocr_conf * det_conf)

def inference_process(infer_queue: Queue, result_queue: Queue, stop_event: Event):
    import ssl
    import certifi
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
