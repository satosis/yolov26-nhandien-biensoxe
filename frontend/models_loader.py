"""Singleton AI model loaders — thread-safe lazy init."""
import threading
from ultralytics import YOLO
from paddleocr import PaddleOCR
from insightface.app import FaceAnalysis
from backend.core.config import PLATE_MODEL_PATH

_lock = threading.Lock()
_yolo = None
_ocr = None
_face = None


def preload_models():
    """Warm up AI models at startup."""
    global _yolo, _ocr, _face
    with _lock:
        # General model warming (Lazy load)
        if _yolo is None:
            _yolo = YOLO(PLATE_MODEL_PATH, task="detect")
            _yolo.to("cpu")
        if _ocr is None:
            _ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False, use_gpu=False)
        if _face is None:
            _face = FaceAnalysis(name="buffalo_sc", providers=["CPUExecutionProvider"])
            _face.prepare(ctx_id=-1, det_size=(320, 320))


def get_yolo():
    return _yolo


def get_ocr():
    return _ocr


def get_face():
    return _face
