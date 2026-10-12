import logging
import threading
import os
import re
import uuid
import asyncio
import time
import hashlib
from contextlib import closing
from collections import OrderedDict
from http.client import HTTPConnection
from urllib.parse import urlencode
import numpy as np
import cv2
import multiprocessing.shared_memory as sm
from fastapi import APIRouter, Request, Depends, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from frontend.deps import get_menu_context, require_menu, templates
from backend.util.image_utils import save_snapshot
from backend.ai.inference import PlateDetector, OCRReader, FaceRecognizer, enhance_plate, validate_plate_crop
from backend.services import config as cfg
from backend.services.database import db
from backend.util.bbox_utils import merge_plate_boxes
from backend.camera import camera_service as cam_svc
from backend.services.settings_service import get_app_setting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/detection")

_plate_detector = None
_ocr_reader = None
_face_recognizer = None


_model_lock = threading.Lock()
_last_request_time = 0

_ocr_cache = OrderedDict()
_ocr_lock = threading.Lock()
MAX_OCR_CACHE_ENTRIES = 256
MAX_RTSP_READ_FAILURES = 3

def _ocr_crop_hash(crop):
    return hashlib.md5(crop[::4, ::4].tobytes()).hexdigest()

def _read_cached_ocr(ocr_reader, crop, crop_hash):
    with _ocr_lock:
        cached = _ocr_cache.get(crop_hash)
        if cached and time.time() - cached[3] < 5:
            _ocr_cache.move_to_end(crop_hash)
            return cached[0], cached[1], cached[2]
        text, conf, ptype = ocr_reader.read(crop)
        _ocr_cache[crop_hash] = (text, conf, ptype, time.time())
        _ocr_cache.move_to_end(crop_hash)
        if len(_ocr_cache) > MAX_OCR_CACHE_ENTRIES:
            _ocr_cache.popitem(last=False)
        return text, conf, ptype

async def _cleanup_models_loop():
    global _plate_detector, _ocr_reader, _face_recognizer
    while True:
        await asyncio.sleep(60)
        with _model_lock:
            if _last_request_time > 0 and (time.time() - _last_request_time) > 600:
                if _plate_detector or _ocr_reader or _face_recognizer:
                    logger.info("Auto-unloading detection models after ten minutes idle")
                    _plate_detector = None
                    _ocr_reader = None
                    _face_recognizer = None

_cleanup_task = None

def _start_cleanup_loop():
    global _cleanup_task
    if _cleanup_task is None:
        _cleanup_task = asyncio.create_task(_cleanup_models_loop())

def _dedupe_detections(detections, iou_threshold=0.5):
    if len(detections) <= 1:
        return detections
    dets = sorted(detections, key=lambda d: d[4], reverse=True)
    keep = []
    for d in dets:
        x1, y1, x2, y2 = d[0], d[1], d[2], d[3]
        is_dup = False
        for k in keep:
            kx1, ky1, kx2, ky2 = k[0], k[1], k[2], k[3]
            ix1, iy1 = max(x1, kx1), max(y1, ky1)
            ix2, iy2 = min(x2, kx2), min(y2, ky2)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            area_a = (x2 - x1) * (y2 - y1)
            area_b = (kx2 - kx1) * (ky2 - ky1)
            union = area_a + area_b - inter
            if union > 0 and inter / union > iou_threshold:
                is_dup = True
                break
        if not is_dup:
            keep.append(d)
    return keep

def _get_models():
    global _plate_detector, _ocr_reader, _face_recognizer, _last_request_time
    _last_request_time = time.time()
    
    with _model_lock:
        os.environ["ORT_COREML_FLAGS"] = "0" 
        
        if _plate_detector is None:
            _plate_detector = PlateDetector()
            logger.info("PlateDetector loaded successfully (Frontend CPU mode)")
                
        if _ocr_reader is None:
            _ocr_reader = OCRReader()
            logger.info("OCRReader loaded successfully")

        rt = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
        if _face_recognizer is None:
            _face_recognizer = FaceRecognizer()
            logger.info("FaceRecognizer loaded successfully")
            _get_models._last_face_reload = os.path.getmtime(rt) if os.path.exists(rt) else 0
        elif os.path.exists(rt):
            mtime = os.path.getmtime(rt)
            if mtime > _get_models._last_face_reload:
                _face_recognizer.reload()
                _get_models._last_face_reload = mtime
                    
        return _plate_detector, _ocr_reader, _face_recognizer


def _request_go2rtc_webrtc(params: dict, sdp_offer: str) -> tuple[int, str]:
    # HTTPConnection's socket errors do not embed the request target, which can
    # contain TURN credentials in the ICE query parameter.
    target = "/api/webrtc?" + urlencode(params)
    with closing(HTTPConnection("127.0.0.1", 1984, timeout=10)) as connection:
        connection.request(
            "POST", target, body=sdp_offer.encode("utf-8"),
            headers={"Content-Type": "application/sdp"},
        )
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8", errors="replace")


@router.post("/go2rtc-proxy/{camera_id}")
async def go2rtc_webrtc_proxy(camera_id: int, request: Request, user: dict = Depends(require_menu("detection"))):
    import requests as http_req
    sdp_offer = (await request.body()).decode("utf-8", errors="replace")
    stream_name = f"cam_{camera_id}"

    cam = cam_svc.get_camera(camera_id)
    if not cam:
        logger.warning("go2rtc proxy: camera_id=%s was not found", camera_id)
        return JSONResponse(
            {"ok": False, "message": "Camera không tồn tại."},
            status_code=404,
        )
    if not cam.get("rtsp_url"):
        logger.warning("go2rtc proxy: camera_id=%s has no RTSP URL", camera_id)
        return JSONResponse(
            {"ok": False, "message": "Camera chưa có luồng RTSP."},
            status_code=400,
        )
    registration = http_req.put(
        "http://localhost:1984/api/streams",
        json={stream_name: cam["rtsp_url"]},
        timeout=3,
    )
    if not registration.ok:
        logger.error("go2rtc stream registration failed for camera_id=%s (status=%s)",
                     camera_id, registration.status_code)
        return JSONResponse(
            {"ok": False, "message": "Không thể đăng ký luồng camera với go2rtc."},
            status_code=502,
        )

    params = {"src": stream_name, "video": "h264/baseline", "audio": "opus"}

    turn_url = get_app_setting("WEBRTC_TURN_URL", "")
    turn_user = get_app_setting("WEBRTC_TURN_USERNAME", "")
    turn_pass = get_app_setting("WEBRTC_TURN_PASSWORD", "")
    if turn_url:
        ice_param = turn_url
        if turn_user and turn_pass:
            ice_param = f"{turn_url}:{turn_user}:{turn_pass}"
        params["ice"] = ice_param

    status_code, response_body = await asyncio.to_thread(_request_go2rtc_webrtc, params, sdp_offer)
    if status_code == 200:
        from starlette.responses import Response
        return Response(content=response_body, media_type="application/sdp")
    logger.error("go2rtc WebRTC negotiation failed for camera_id=%s (status=%s)",
                 camera_id, status_code)
    return JSONResponse(
        {"ok": False, "message": "go2rtc không thể thiết lập WebRTC."},
        status_code=502,
    )


@router.get("")
async def detection_page(request: Request, user: dict = Depends(require_menu("detection"))):
    _start_cleanup_loop()
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "detection"
    ctx["cameras"] = cam_svc.get_active_cameras()
    return templates.TemplateResponse("pages/detection.html", {"request": request, **ctx})


def _check_whitelist(plate_text: str) -> dict | None:
    if not plate_text:
        return None
    clean_input = re.sub(r'[^A-Z0-9]', '', plate_text.upper())
    query = """
        SELECT * FROM plate_whitelist 
        WHERE regexp_replace(plate_number, '[^a-zA-Z0-9]', '', 'g') = %s 
        AND is_active = TRUE 
        LIMIT 1
    """
    return db.execute_query(query, (clean_input,), fetch=True)


def _add_pending_plate_simple(plate_norm: str, plate_raw: str, camera_id: str = None, site_id: int = None, image_path: str = None, plate_type: str = "white") -> str | None:
    clean_plate = re.sub(r'[^A-Z0-9]', '', plate_norm.upper())
    existing = db.execute_query("""
        SELECT pending_id FROM pending_plates
        WHERE regexp_replace(plate_norm, '[^a-zA-Z0-9]', '', 'g') = %s
        AND (LOWER(TRIM(status)) = 'pending'
             OR confirmed_at_utc > NOW() - INTERVAL '60 seconds')
        LIMIT 1
    """, (clean_plate,), fetch=True)
    if existing:
        return "existing"

    pid = str(uuid.uuid4())[:8]
    saved = db.execute_query("""
        INSERT INTO pending_plates (pending_id, plate_raw, plate_norm, first_seen_utc, status, site_id, image_path, plate_type)
        VALUES (%s, %s, %s, NOW(), 'pending', %s, %s, %s)
        ON CONFLICT (pending_id) DO NOTHING
    """, (pid, plate_raw, plate_norm, site_id, image_path, plate_type), commit=True)
    if not saved:
        logger.error("Pending plate insert returned no row for site %s", site_id)
        return None
    return "inserted"


def _get_person_by_identity(identity):
    if not identity or identity == "STRANGER":
        return None
    if isinstance(identity, int) or (isinstance(identity, str) and identity.isdigit()):
        query = "SELECT id, full_name, site_ids, note, username, person_identity FROM users WHERE id = %s OR username = %s OR person_identity = %s LIMIT 1"
        row = db.execute_query(query, (int(identity), str(identity), str(identity)), fetch=True)
    else:
        query = "SELECT id, full_name, site_ids, note, username, person_identity FROM users WHERE username = %s OR person_identity = %s LIMIT 1"
        row = db.execute_query(query, (str(identity), str(identity)), fetch=True)

    if row:
        return {
            "identity": str(row['id']),
            "full_name": row['full_name'],
            "username": row['username'],
            "person_identity": row['person_identity'],
            "site_ids": row['site_ids'],
            "note": row['note']
        }
    return None


@router.post("/api/detect")
async def detect_image(request: Request, file: UploadFile = File(...),
                       user: dict = Depends(require_menu("detection"))):
    contents = await file.read()
    if not contents:
        logger.warning("Detection upload is empty")
        return JSONResponse(
            {"ok": False, "message": "Ảnh tải lên không hợp lệ."},
            status_code=400,
        )
    frame_bgr = await asyncio.to_thread(cv2.imdecode, np.frombuffer(contents, np.uint8), cv2.IMREAD_COLOR)
    del contents
    if frame_bgr is None:
        logger.warning("Detection upload could not be decoded as an image")
        return JSONResponse(
            {"ok": False, "message": "Ảnh tải lên không hợp lệ."},
            status_code=400,
        )
    detector, ocr, face = _get_models()

    def run_ai_pipeline():
        debug_dir = "/tmp/debug_frames"
        if os.path.exists(debug_dir):
            curr_time = int(time.time())
            if not cv2.imwrite(f"{debug_dir}/frame_{curr_time}.jpg", frame_bgr):
                logger.error("Detection debug frame write failed in %s", debug_dir)
                raise OSError("Could not save detection debug frame")

        res = []
        orig_h, orig_w = frame_bgr.shape[:2]

        detections = detector.detect(frame_bgr)
        detections = merge_plate_boxes(detections)

        plate_count = sum(1 for d in detections if d[5] == 0)
        if orig_h > 800 and plate_count == 0:
            bottom_crop = frame_bgr[orig_h // 2:, :]
            bottom_dets = detector.detect(bottom_crop)
            bottom_dets = merge_plate_boxes(bottom_dets)
            for x1, y1, x2, y2, conf, cls_id in bottom_dets:
                detections.append((x1, y1 + orig_h // 2, x2, y2 + orig_h // 2, conf, cls_id))

            cx, cy = orig_w // 2, orig_h // 2
            cw, ch = int(orig_w * 0.6), int(orig_h * 0.6)
            cx1, cy1 = cx - cw // 2, cy - ch // 2
            center_crop = frame_bgr[cy1:cy1 + ch, cx1:cx1 + cw]
            center_dets = detector.detect(center_crop)
            center_dets = merge_plate_boxes(center_dets)
            for x1, y1, x2, y2, conf, cls_id in center_dets:
                detections.append((x1 + cx1, y1 + cy1, x2 + cx1, y2 + cy1, conf, cls_id))

            detections = _dedupe_detections(detections)

        logger.info(f"DEBUG: Found {len(detections)} detections (after merge)")

        for x1, y1, x2, y2, conf, cls_id in detections:
            if cls_id == 0:
                h_img, w_img = frame_bgr.shape[:2]
                px = int((x2 - x1) * 0.15) + 10
                py = int((y2 - y1) * 0.15) + 5
                x1_p, y1_p = max(0, x1 - px), max(0, y1 - py)
                x2_p, y2_p = min(w_img, x2 + px), min(h_img, y2 + py)

                crop = frame_bgr[y1_p:y2_p, x1_p:x2_p]
                text, ocr_conf, p_type = "", 0.0, "white"
                if validate_plate_crop(crop):
                    processed = enhance_plate(crop)
                    ch = _ocr_crop_hash(processed)
                    text, ocr_conf, p_type = _read_cached_ocr(ocr, processed, ch)

                status = "no_plate"
                owner_name = "unknown"
                if text:
                    normalized = cfg.normalize_plate(text)
                    wl = _check_whitelist(normalized)
                    if wl:
                        status = "whitelist"
                        owner_name = wl.get("owner_name", "Whitelist")
                    else:
                        status = "unknown"

                if status == "unknown":
                    snap_path = save_snapshot(frame_bgr, "webcam", normalized)
                    s_id = request.session.get("active_site_id") or None

                    outcome = _add_pending_plate_simple(
                        normalized, text, "webcam", site_id=s_id,
                        image_path=snap_path, plate_type=p_type,
                    )
                    if outcome is None:
                        return None
                    if outcome == "inserted":
                        logger.info("Unknown plate saved from manual detection at site %s", s_id)

                res.append({
                    "type": "plate",
                    "text": text if text else "(BIỂN SỐ ?)",
                    "plate_type": p_type,
                    "confidence": round(conf * 100, 1),
                    "ocr_confidence": round(ocr_conf * 100, 1),
                    "status": status,
                    "owner_name": owner_name,
                    "bbox": [x1, y1, x2, y2],
                })
            elif cls_id == 1:
                res.append({
                    "type": "person", "text": "Person",
                    "confidence": round(conf * 100, 1), "status": "unknown", "bbox": [x1, y1, x2, y2]
                })
            elif cls_id == 2:
                res.append({
                    "type": "door", "text": "Door",
                    "confidence": round(conf * 100, 1), "status": "unknown", "bbox": [x1, y1, x2, y2]
                })

        name, sim, f_bbox, emb, direction = face.identify(frame_bgr)
        if name:
            display_text = name
            status = "stranger"

            if name != "STRANGER":
                user_info = _get_person_by_identity(name)
                if user_info:
                    display_name = user_info.get('full_name') or user_info.get('username') or "Không xác định"
                    display_text = f"Tên: {display_name} + id: {user_info['identity']}"
                    status = "known"
            else:
                from backend.ai.stranger_manager import get_stranger_manager
                mgr = get_stranger_manager()
                stranger_id, match_sim, _ = mgr.process_stranger(
                    emb=emb, frame=frame_bgr, cam_id="webcam",
                    bbox=f_bbox, direction=direction, site_id=None
                )
                display_text = f"Người lạ #{stranger_id}"
                if match_sim > 0:
                    sim = match_sim

            res.append({
                "type": "face", "text": display_text,
                "confidence": round(sim * 100, 1), "status": status, "bbox": f_bbox
            })

        logger.info("API Detect: %s results", len(res))
        return res

    results = await asyncio.to_thread(run_ai_pipeline)
    if results is None:
        return JSONResponse(
            {"ok": False, "message": "Không thể lưu biển số chờ duyệt."},
            status_code=500,
        )
    
    return JSONResponse({
        "results": results, 
        "count": len(results),
        "info": {
            "detector": True,
            "ocr": True,
            "face": True,
            "cached_face": False,
        }
    })


def _mjpeg_frame(frame, quality=75):
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        logger.error("MJPEG frame encoding failed")
        raise RuntimeError("JPEG encoding failed")
    return b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"


def _stream_error(message):
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(frame, message, (40, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (100, 100, 255), 2)
    return _mjpeg_frame(frame)


@router.get("/stream/{camera_id}")
async def stream_camera(camera_id: int, request: Request, user: dict = Depends(require_menu("detection"))):
    shm_name = f"shm_cam_{camera_id}"

    async def frame_generator():
        shm_path = f"/dev/shm/{shm_name}"
        if os.path.isdir("/dev/shm"):
            for _ in range(30):
                if os.path.exists(shm_path):
                    break
                await asyncio.sleep(0.5)

        if os.path.exists(shm_path):
            shm = sm.SharedMemory(name=shm_name)
            try:
                last_counter = -1
                while not await request.is_disconnected():
                    counter = np.frombuffer(shm.buf, dtype=np.uint32, count=1)[0]
                    if counter != last_counter:
                        frame = np.frombuffer(shm.buf, dtype=np.uint8, offset=4, count=cfg.SHM_FRAME_BYTES).reshape((cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, 3)).copy()
                        frame = cv2.resize(frame, (854, 480), interpolation=cv2.INTER_NEAREST)
                        yield _mjpeg_frame(frame, cfg.STREAM_JPEG_QUALITY_LIVE)
                        last_counter = counter
                        await asyncio.sleep(0)
                    else:
                        await asyncio.sleep(0.02)
            finally:
                shm.close()
            return

        cam = cam_svc.get_camera(camera_id)
        rtsp_url = cam.get("rtsp_url", "") if cam else ""
        if not rtsp_url:
            logger.error(f"Camera {camera_id}: no RTSP URL configured")
            yield _stream_error("No RTSP URL configured")
            return

        logger.info(f"Camera {camera_id}: SHM not available, falling back to direct RTSP")

        latest_frame = [None]
        reader_stop = threading.Event()

        def _rtsp_reader():
            cap = cv2.VideoCapture(rtsp_url)
            try:
                if not cap.isOpened():
                    logger.error(f"Camera {camera_id}: cannot open RTSP")
                    return
                failures = 0
                while not reader_stop.is_set():
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        failures = 0
                        if frame.shape[1] > 854:
                            frame = cv2.resize(frame, (854, 480), interpolation=cv2.INTER_LINEAR)
                        latest_frame[0] = frame
                    else:
                        failures += 1
                        if failures >= MAX_RTSP_READ_FAILURES:
                            logger.error(
                                "Camera %s: RTSP stopped after %s consecutive read failures",
                                camera_id,
                                failures,
                            )
                            return
                        reader_stop.wait(0.3)
            finally:
                cap.release()
                reader_stop.set()

        reader_thread = threading.Thread(target=_rtsp_reader, daemon=True)
        reader_thread.start()

        try:
            for _ in range(30):
                if latest_frame[0] is not None or reader_stop.is_set() or not reader_thread.is_alive():
                    break
                await asyncio.sleep(0.2)

            if latest_frame[0] is None:
                yield _stream_error("Cannot connect to camera")
                return

            while not await request.is_disconnected():
                if reader_stop.is_set() or not reader_thread.is_alive():
                    yield _stream_error("Camera disconnected")
                    return
                frame = latest_frame[0]
                if frame is not None:
                    yield _mjpeg_frame(frame)
                await asyncio.sleep(0.033)
        finally:
            reader_stop.set()

    return StreamingResponse(frame_generator(), media_type="multipart/x-mixed-replace; boundary=frame")
