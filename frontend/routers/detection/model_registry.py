import os
import time
import threading
import asyncio
import logging
from starlette.concurrency import run_in_threadpool

from backend.ai.inference import PlateDetector, OCRReader, FaceRecognizer
from backend.services import config as cfg

logger = logging.getLogger(__name__)

_plate_detector = None
_ocr_reader = None
_face_recognizer = None

_model_lock = threading.Lock()
_detector_lock = threading.Lock()
_last_request_time = 0

_ocr_lock = threading.Lock()


def _cleanup_idle_models():
    global _plate_detector, _ocr_reader, _face_recognizer
    with _model_lock:
        if _last_request_time > 0 and (time.time() - _last_request_time) > 600:
            if _plate_detector or _ocr_reader or _face_recognizer:
                logger.info("Auto-unloading detection models after ten minutes idle")
                _plate_detector = None
                _ocr_reader = None
                _face_recognizer = None


async def _cleanup_models_loop():
    while True:
        await asyncio.sleep(60)
        await run_in_threadpool(_cleanup_idle_models)


_cleanup_task = None


def _start_cleanup_loop():
    global _cleanup_task
    if _cleanup_task is None:
        _cleanup_task = asyncio.create_task(_cleanup_models_loop())


def _get_models(include_face=False):
    global _plate_detector, _ocr_reader, _face_recognizer, _last_request_time
    _last_request_time = time.time()
    
    with _model_lock:
        os.environ["ORT_COREML_FLAGS"] = "0" 
        
        if _plate_detector is None:
            _plate_detector = PlateDetector(native_threads=3, prefer_float=True)
            logger.info("PlateDetector loaded successfully (Frontend CPU mode)")
                
        if _ocr_reader is None:
            _ocr_reader = OCRReader(native_threads=2)
            logger.info("OCRReader loaded successfully")

        rt = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
        if include_face and _face_recognizer is None:
            _face_recognizer = FaceRecognizer()
            logger.info("FaceRecognizer loaded successfully")
            _get_models._last_face_reload = os.path.getmtime(rt) if os.path.exists(rt) else 0
        elif include_face and os.path.exists(rt):
            mtime = os.path.getmtime(rt)
            if mtime > _get_models._last_face_reload:
                _face_recognizer.reload()
                _get_models._last_face_reload = mtime
                    
        return _plate_detector, _ocr_reader, _face_recognizer
