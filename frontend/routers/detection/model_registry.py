import os
import time
import hashlib
import threading
import asyncio
import logging
from collections import OrderedDict
from pathlib import Path
from starlette.concurrency import run_in_threadpool
from fastapi import HTTPException

from backend.ai.inference import PlateDetector, OCRReader, FaceRecognizer
from backend.services import config as cfg
from backend.services.video_ocr import VideoOCRJobs

logger = logging.getLogger(__name__)

_plate_detector = None
_ocr_reader = None
_face_recognizer = None

_model_lock = threading.Lock()
_video_jobs_lock = threading.Lock()
_detector_lock = threading.Lock()
_last_request_time = 0
_video_jobs = None

_ocr_cache = OrderedDict()
_ocr_lock = threading.Lock()
MAX_OCR_CACHE_ENTRIES = 256


def _ocr_crop_hash(crop):
    digest = hashlib.bl2b(digest_size=16) if hasattr(hashlib, "bl2b") else hashlib.blake2b(digest_size=16)
    digest.update(str(crop.shape).encode("ascii"))
    digest.update(crop.tobytes())
    return digest.hexdigest()


def _read_cached_ocr(ocr_reader, crop, crop_hash, *, quick=False):
    crop_hash = (crop_hash, quick)
    with _ocr_lock:
        cached = _ocr_cache.get(crop_hash)
        ttl = 10 if (cached and cached[0]) else 0.8
        if cached and time.time() - cached[3] < ttl:
            _ocr_cache.move_to_end(crop_hash)
            return cached[0], cached[1], cached[2]
        read = ocr_reader.read_quick if quick else ocr_reader.read
        text, conf, ptype = read(crop)
        _ocr_cache[crop_hash] = (text, conf, ptype, time.time())
        _ocr_cache.move_to_end(crop_hash)
        if len(_ocr_cache) > MAX_OCR_CACHE_ENTRIES:
            _ocr_cache.popitem(last=False)
        return text, conf, ptype


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
            _plate_detector = PlateDetector(native_threads=2, prefer_float=True)
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


def _get_video_jobs():
    global _video_jobs
    with _video_jobs_lock:
        if _video_jobs is None:
            _video_jobs = VideoOCRJobs(Path(cfg.DATA_DIR) / "ocr_jobs",
                                      process_isolation=True, cpu_workers=2)
    return _video_jobs


def _video_identity(request, user):
    user_id = user.get("id") or user.get("user_id") or user.get("username") or "1"
    return str(user_id), str(request.session.get("active_site_id") or 0)


def _owned_video_job(request, user, job_id):
    manager = _get_video_jobs()
    job = manager.get(job_id, *_video_identity(request, user))
    if job is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ video hoặc tác vụ đã hết hạn.")
    return manager, job
