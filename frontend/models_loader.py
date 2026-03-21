"""Singleton AI model loaders — thread-safe lazy init."""
import threading

_lock = threading.Lock()
_yolo = None
_ocr = None
_face = None


def preload_models():
    """Warm up AI models at startup."""
    global _yolo, _ocr, _face
    with _lock:
        if _yolo is None:
            try:
                from ultralytics import YOLO
                from backend.core.config import PLATE_MODEL_PATH
                _yolo = YOLO(PLATE_MODEL_PATH, task="detect")
            except Exception:
                pass
        if _ocr is None:
            try:
                from paddleocr import PaddleOCR
                _ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False, use_gpu=False)
            except Exception:
                pass
        if _face is None:
            try:
                from insightface.app import FaceAnalysis
                _face = FaceAnalysis(name="buffalo_sc", providers=["CPUExecutionProvider"])
                _face.prepare(ctx_id=0, det_size=(320, 320))
            except Exception:
                pass


def get_yolo():
    return _yolo


def get_ocr():
    return _ocr


def get_face():
    return _face
