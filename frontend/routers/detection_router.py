import logging
from fastapi import APIRouter, Request, Depends, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from frontend.deps import get_db, get_menu_context, require_menu

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/detection")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

# Lazy-loaded AI models
_plate_detector = None
_ocr_reader = None
_face_recognizer = None


def _get_models():
    global _plate_detector, _ocr_reader, _face_recognizer
    
    # Try loading PlateDetector
    if _plate_detector is None:
        try:
            from backend.core.inference import PlateDetector
            _plate_detector = PlateDetector()
            logger.info("PlateDetector loaded successfully")
        except Exception as e:
            logger.error("Failed to load PlateDetector: %s", e)
            _plate_detector = False # Mark as failed
            
    # Try loading OCRReader
    if _ocr_reader is None:
        try:
            from backend.core.inference import OCRReader
            _ocr_reader = OCRReader()
            logger.info("OCRReader loaded successfully")
        except Exception as e:
            logger.error("Failed to load OCRReader: %s", e)
            _ocr_reader = False # Mark as failed

    # Try loading FaceRecognizer
    if _face_recognizer is None:
        try:
            from backend.core.inference import FaceRecognizer
            _face_recognizer = FaceRecognizer()
            logger.info("FaceRecognizer loaded successfully")
        except Exception as e:
            logger.error("Failed to load FaceRecognizer: %s", e)
            _face_recognizer = False # Mark as failed

    return (
        _plate_detector if _plate_detector is not False else None,
        _ocr_reader if _ocr_reader is not False else None,
        _face_recognizer if _face_recognizer is not False else None
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
    try:
        import asyncio
        import cv2
        import numpy as np
        from PIL import Image
        import io

        contents = await file.read()
        detector, ocr, face = _get_models()

        def run_ai_pipeline():
            img = Image.open(io.BytesIO(contents)).convert("RGB")
            frame_bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            res = []

            if detector:
                detections = detector.detect(frame_bgr)
                for x1, y1, x2, y2, conf, cls_id in detections:
                    if cls_id == 0: # Plate
                        crop = frame_bgr[y1:y2, x1:x2]
                        text, ocr_conf = "", 0.0
                        if ocr and crop.size > 0:
                            text, ocr_conf = ocr.read(crop)

                        status = "unknown"
                        db = get_db()
                        if text:
                            normalized = text.upper().replace(" ", "").replace(".", "").replace("-", "")
                            wl = db.check_whitelist(normalized)
                            status = "whitelist" if wl else "unknown"

                        res.append({
                            "type": "plate",
                            "text": text,
                            "confidence": round(conf * 100, 1),
                            "ocr_confidence": round(ocr_conf * 100, 1),
                            "status": status,
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

            if face:
                try:
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
                except Exception as e:
                    logger.error("Face recognition error: %s", e)

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
    except Exception as e:
        logger.error("Detection API error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e), "results": []}, status_code=500)
