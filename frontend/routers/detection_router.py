import logging
import threading
import os
import asyncio
import numpy as np
import cv2
import multiprocessing.shared_memory as sm
from fastapi import APIRouter, Request, Depends, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from frontend.deps import get_db, get_menu_context, require_menu
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

def _get_models():
    global _plate_detector, _ocr_reader, _face_recognizer
    
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
async def detect_image(file: UploadFile = File(...)):
    """Accept an image, run plate detection + OCR, return JSON results."""
    contents = await file.read()
    detector, ocr, face = _get_models()

    def run_ai_pipeline():
        import cv2
        import numpy as np

        # Direct decode — skip PIL→np→cv2 chain
        nparr = np.frombuffer(contents, np.uint8)
        frame_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame_bgr is None:
            return []

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
                        # Auto-enhance small crops
                        h, w = crop.shape[:2]
                        processed = enhance_plate(crop) if h < 50 else crop
                        text, ocr_conf = ocr.read(processed)
                        logger.info(f"DEBUG: OCR Result for {w}x{h} crop: '{text}' (conf: {ocr_conf})")

                    status = "unknown"
                    owner_name = "unknown"
                    if text:
                        normalized = text.upper().replace(" ", "").replace(".", "").replace("-", "")
                        wl = db.check_whitelist(normalized)
                        if wl:
                            status = "whitelist"
                            owner_name = wl.get("owner_name", "Whitelist")

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

            # Only run Face identification if a person was found and Face model is available
            if face and has_person:
                name, sim, f_bbox = face.identify(frame_bgr)
                if name:
                    display_text = name
                    status = "stranger"
                    
                    if name != "STRANGER":
                        db = get_db()
                        user_info = db.get_person_by_identity(name)
                        if user_info:
                            display_text = f"Tên: {user_info['full_name']} + id: {user_info['identity']}"
                            status = "known"
                    
                    res.append({
                        "type": "face", "text": display_text,
                        "confidence": round(sim * 100, 1), "status": status, "bbox": f_bbox
                    })

        return res

    results = await asyncio.to_thread(run_ai_pipeline)

    return JSONResponse({
        "results": results, 
        "count": len(results),
        "info": {
            "detector": detector is not False and detector is not None,
            "ocr": ocr is not False and ocr is not None,
            "face": face is not False and face is not None
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
        for _ in range(10): 
            shm = sm.SharedMemory(name=shm_name)
            if shm:
                break
            await asyncio.sleep(0.5)
        
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
                frame_raw = np.frombuffer(shm.buf[4:], dtype=np.uint8)
                # Reshape manually to avoid copy if possible, but BGR to JPEG needs copy anyway
                frame = frame_raw.reshape((cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, 3))

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
