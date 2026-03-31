import logging
import threading
import os
import asyncio
import time
import numpy as np
import cv2
import multiprocessing.shared_memory as sm
from fastapi import APIRouter, Request, Depends, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from frontend.deps import get_db, get_menu_context, require_menu
from backend.util.image_utils import save_face_snapshot, save_snapshot
from backend.core.inference import PlateDetector, OCRReader, FaceRecognizer, enhance_plate
from backend.core import config as cfg

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/detection")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

# Lazy-loaded AI models
_plate_detector = None
_ocr_reader = None
_face_recognizer = None


_model_lock = threading.Lock()
_last_request_time = 0
_detection_request_count = 0
_last_face_results = []
_last_face_ts = 0
_recent_stranger_cache = [] # List of {'emb': np.ndarray, 'ts': float}
_recent_plate_cache = [] # List of {'emb': np.ndarray, 'ts': float}

async def _cleanup_models_loop():
    """Background loop to unload models if idle for > 2 minutes."""
    global _plate_detector, _ocr_reader, _face_recognizer
    while True:
        await asyncio.sleep(60)
        with _model_lock:
            if _last_request_time > 0 and (time.time() - _last_request_time) > 120:
                if _plate_detector or _ocr_reader or _face_recognizer:
                    logger.info("RAM Optimization: Auto-unloading detection models (Idle > 2m)")
                    _plate_detector = None
                    _ocr_reader = None
                    _face_recognizer = None

# Start the cleanup loop when the module is loaded
asyncio.create_task(_cleanup_models_loop())

def _get_models():
    global _plate_detector, _ocr_reader, _face_recognizer, _last_request_time
    _last_request_time = time.time()
    
    with _model_lock:
        # Optimization: for Frontend process, we use CPU only to save RAM/VRAM for the main RTSP engine
        os.environ["ORT_COREML_FLAGS"] = "0" 
        
        # Load PlateDetector
        if _plate_detector is None:
            _plate_detector = PlateDetector()
            logger.info("PlateDetector loaded successfully (Frontend CPU mode)")
                
        # Load OCRReader
        if _ocr_reader is None:
            _ocr_reader = OCRReader()
            logger.info("OCRReader loaded successfully")

        # Load FaceRecognizer
        if _face_recognizer is None:
            _face_recognizer = FaceRecognizer()
            logger.info("FaceRecognizer loaded successfully")
            rt = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
            _get_models._last_face_reload = os.path.getmtime(rt) if os.path.exists(rt) else 0
        else:
            # Check for changes in known faces
            rt = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
            if os.path.exists(rt):
                mtime = os.path.getmtime(rt)
                if mtime > getattr(_get_models, '_last_face_reload', 0):
                    logger.info("Frontend FaceRecognizer: Synchronizing via .reload mtime... (Clearing Throttles)")
                    global _recent_stranger_cache, _recent_plate_cache
                    _face_recognizer.reload()
                    _recent_stranger_cache = []
                    _recent_plate_cache = []
                    _get_models._last_face_reload = mtime
                    
        return _plate_detector, _ocr_reader, _face_recognizer

    return (
        _plate_detector if isinstance(_plate_detector, object) and _plate_detector is not False else None,
        _ocr_reader if isinstance(_ocr_reader, object) and _ocr_reader is not False else None,
        _face_recognizer if isinstance(_face_recognizer, object) and _face_recognizer is not False else None
    )


@router.get("")
async def detection_page(request: Request, user: dict = Depends(require_menu("detection"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "detection"
    db = get_db()
    ctx["cameras"] = db.get_active_cameras()
    return templates.TemplateResponse("pages/detection.html", {"request": request, **ctx})


@router.post("/api/detect")
async def detect_image(request: Request, file: UploadFile = File(...)):
    """Accept an image, run plate detection + OCR, return JSON results."""
    global _detection_request_count, _last_face_results, _last_face_ts
    _detection_request_count += 1
    
    contents = await file.read()
    detector, ocr, face = _get_models()

    # Optimization: Only run face recognition every 2nd request from the frontend (increased frequency)
    # OR if we have no cached results, to keep it responsive while saving RAM/CPU.
    run_face = face is not None and (_detection_request_count % 2 == 0 or not _last_face_results)

    def run_ai_pipeline():
        # Direct decode — skip PIL→np→cv2 chain
        nparr = np.frombuffer(contents, np.uint8)
        frame_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame_bgr is None:
            return []

        # Save frame for debugging if /tmp/debug_frames exists
        debug_dir = "/tmp/debug_frames"
        if os.path.exists(debug_dir):
            # Use a simple counter to save only the first few frames
            # This is a very basic implementation and not thread-safe or persistent across calls
            # For a robust solution, a shared counter or more sophisticated logic would be needed.
            # For now, it will just save every frame if the directory exists.
            curr_time = int(time.time())
            cv2.imwrite(f"{debug_dir}/frame_{curr_time}.jpg", frame_bgr)

        res = []
        if detector:
            detections = detector.detect(frame_bgr)
            logger.info(f"DEBUG: Found {len(detections)} potential detections")
            has_person = any(d[5] == 1 for d in detections)

            db = get_db()  # Get once — not per-detection
            for x1, y1, x2, y2, conf, cls_id in detections:
                if cls_id == 0: # Plate
                    # Add 5% padding to improve OCR context
                    h_img, w_img = frame_bgr.shape[:2]
                    pad_w = int((x2 - x1) * 0.05)
                    pad_h = int((y2 - y1) * 0.05)
                    x1_p = max(0, x1 - pad_w)
                    y1_p = max(0, y1 - pad_h)
                    x2_p = min(w_img, x2 + pad_w)
                    y2_p = min(h_img, y2 + pad_h)
                    
                    crop = frame_bgr[y1_p:y2_p, x1_p:x2_p]
                    text, ocr_conf = "", 0.0
                    if ocr and crop.size > 0:
                        # Auto-enhance small or blurry crops (improved threshold to 120px)
                        h, w = crop.shape[:2]
                        processed = enhance_plate(crop) if h < 120 else crop
                        text, ocr_conf = ocr.read(processed)
                        logger.info(f"DEBUG: OCR Result for {w}x{h} crop: '{text}' (conf: {ocr_conf})")

                    status = "unknown"
                    owner_name = "unknown"
                    if text:
                        # Use centralized normalization to handle spaces, dashes, and dots consistently
                        normalized = cfg.normalize_plate(text)
                        wl = db.check_whitelist(normalized)
                        if wl:
                            status = "whitelist"
                            owner_name = wl.get("owner_name", "Whitelist")

                    if status == "unknown" and text:
                        # Manual detection: Skip the in-memory cache and let the database handle deduplication.
                        # This ensures immediate re-detection after a deletion.
                        snap_path = save_snapshot(frame_bgr, "webcam", normalized)
                        # Propagate active site from session so it shows up in dashboards
                        active_site_id = request.session.get("active_site_id")
                        s_id = int(active_site_id) if active_site_id and int(active_site_id) > 0 else None

                        logger.info(f"Adding unknown plate to pending list: {normalized} (site: {s_id})")
                        db.add_pending_plate_simple(normalized, normalized, "webcam", site_id=s_id, image_path=snap_path)
                        logger.info(f"Stranger plate '{normalized}' logged from manual detection at site {s_id}")

                    res.append({
                        "type": "plate",
                        "text": text if text else "(BIỂN SỐ ?)",
                        "confidence": round(conf * 100, 1),
                        "ocr_confidence": round(ocr_conf * 100, 1),
                        "status": status,
                        "owner_name": owner_name,
                        "bbox": [x1, y1, x2, y2],
                    })
                elif cls_id == 1: # Person
                    res.append({
                        "type": "person", "text": "Person",
                        "confidence": round(conf * 100, 1), "status": "unknown", "bbox": [x1, y1, x2, y2]
                    })
                elif cls_id == 2: # Door
                    res.append({
                        "type": "door", "text": "Door",
                        "confidence": round(conf * 100, 1), "status": "unknown", "bbox": [x1, y1, x2, y2]
                    })

            # Always run combined face identification if face model is available AND scheduled
            if run_face:
                    name, sim, f_bbox, emb, direction = face.identify(frame_bgr)
                    if name:
                        display_text = name
                        status = "stranger"
                        
                        if name != "STRANGER":
                            # db = get_db() # Already called above
                            user_info = db.get_person_by_identity(name)
                            if user_info:
                                display_text = f"Tên: {user_info['full_name']} + id: {user_info['identity']}"
                                status = "known"
                        else:
                            # Advanced Deduplication: minimize photos for same stranger
                            if emb is not None:
                                now = time.time()
                                # Cleanup old cache (> 5 min)
                                global _recent_stranger_cache
                                _recent_stranger_cache = [s for s in _recent_stranger_cache if now - s['ts'] < 300]
                                
                                is_duplicate = False
                                for s in _recent_stranger_cache:
                                    # Cosine similarity (dot product since they are normed)
                                    similarity = np.dot(emb, s['emb'])
                                    if similarity > 0.70: # Same person
                                        if now - s['ts'] < 60: # Within 1 min window
                                            is_duplicate = True
                                            break
                                
                                if not is_duplicate:
                                    snap_path = save_face_snapshot(frame_bgr, "webcam", bbox=f_bbox)
                                    db.log_stranger("webcam", sim, snap_path, site_id=None, face_embedding=emb.tobytes())
                                    _recent_stranger_cache.append({'emb': emb, 'ts': now})
                                    logger.info("New stranger logged from Live Webcam (sim: %f)", sim)
                        
                        res.append({
                            "type": "face", "text": display_text,
                            "confidence": round(sim * 100, 1), "status": status, "bbox": f_bbox
                        })

        logger.info(f"API Detect: results={res}")
        return res

    results = await asyncio.to_thread(run_ai_pipeline)
    
    # Persistence Logic: If face detection was skipped/failed this turn, 
    # use cached results if they are recent (within 1.5s).
    current_faces = [r for r in results if r['type'] == 'face']
    if run_face:
        # Update cache on success (even if empty)
        _last_face_results = current_faces
        _last_face_ts = time.time()
    elif not current_faces and (time.time() - _last_face_ts < 1.5):
        # Merge cached faces if we skipped model this turn
        results.extend(_last_face_results)

    return JSONResponse({
        "results": results, 
        "count": len(results),
        "info": {
            "detector": detector is not False and detector is not None,
            "ocr": ocr is not False and ocr is not None,
            "face": face is not False and face is not None,
            "cached_face": not run_face and len([r for r in results if r['type'] == 'face']) > 0
        }
    })


@router.get("/stream/{camera_id}")
async def stream_camera(camera_id: int, request: Request, user: dict = Depends(require_menu("detection"))):
    """MJPEG stream from shared memory."""
    """MJPEG stream from shared memory."""
    shm_name = f"shm_cam_{camera_id}"
    total_size = 4 + cfg.SHM_FRAME_BYTES
    
    async def frame_generator():
        shm = None
        # Wait for SHM to be available (orchestrator might still be starting it)
        for _ in range(20): 
            shm = sm.SharedMemory(name=shm_name)
            break
        
        if not shm:
            logger.error(f"SHM {shm_name} not found after retries")
            return

        last_counter = -1
        while True:
            if await request.is_disconnected():
                break
            
            # Read counter and frame
            # SHM layout: [0..3] counter, [4..] BGR frame
            counter = np.frombuffer(shm.buf[:4], dtype=np.uint32)[0]
            
            if counter != last_counter:
                # Use slicing to handle any extra padding in the buffer
                frame_raw = np.frombuffer(shm.buf[4:], dtype=np.uint8)
                frame = frame_raw[:cfg.SHM_FRAME_BYTES].reshape((cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, 3))

                # Light spatial denoising for display quality
                ksize = getattr(cfg, 'DENOISE_SPATIAL_KSIZE', 3)
                if ksize > 0:
                    frame = cv2.GaussianBlur(frame, (ksize, ksize), 0)

                jpeg_quality = getattr(cfg, 'STREAM_JPEG_QUALITY_LIVE', 88)
                ret, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
                if ret:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
                last_counter = counter
            
            await asyncio.sleep(1.0 / cfg.GRAB_FPS_CAP)
        if shm:
            shm.close()

    return StreamingResponse(frame_generator(), media_type="multipart/x-mixed-replace; boundary=frame")
