import logging
import threading
import os
import re
import uuid
import asyncio
import time
import hashlib
import json
import base64
from pathlib import Path
from contextlib import closing
from collections import OrderedDict
from http.client import HTTPConnection
from urllib.parse import urlencode
import numpy as np
import cv2
import multiprocessing.shared_memory as sm
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from starlette.concurrency import run_in_threadpool
from frontend.deps import get_menu_context, require_menu, templates
from backend.util.image_utils import save_snapshot, decode_image
from backend.ai.inference import PlateDetector, OCRReader, FaceRecognizer, enhance_plate, validate_plate_crop
from backend.services import config as cfg
from backend.services.database import db
from backend.util.bbox_utils import merge_plate_boxes
from backend.camera import camera_service as cam_svc
from backend.services.settings_service import get_app_setting
from backend.ai.plate_analysis import PlateFrameAnalyzer, detect_plate_regions
from backend.services.video_ocr import VideoOCRJobs
from backend.util.ocr_pipeline import OCRTrackAggregator, assess_image
from backend.util.ocr_utils import prepare_plate_preview

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/detection")

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
    digest = hashlib.blake2b(digest_size=16)
    digest.update(str(crop.shape).encode("ascii"))
    digest.update(crop.tobytes())
    return digest.hexdigest()

def _read_cached_ocr(ocr_reader, crop, crop_hash, *, quick=False):
    crop_hash = (crop_hash, quick)
    with _ocr_lock:
        cached = _ocr_cache.get(crop_hash)
        if cached and time.time() - cached[3] < 5:
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
        # Model initialization can hold this lock for seconds; never wait on it
        # in the web event loop while unrelated pages and progress need service.
        await run_in_threadpool(_cleanup_idle_models)

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
            # Video models and native inference stay in the bounded child;
            # live/image requests keep their own independent web model state.
            _video_jobs = VideoOCRJobs(Path(cfg.DATA_DIR) / "ocr_jobs",
                                      process_isolation=True, cpu_workers=2)
    return _video_jobs


def _video_identity(request, user):
    return str(user["id"]), str(request.session.get("active_site_id") or 0)


def _owned_video_job(request, user, job_id):
    manager = _get_video_jobs()
    job = manager.get(job_id, *_video_identity(request, user))
    if job is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ video hoặc tác vụ đã hết hạn.")
    return manager, job


@router.post("/api/videos", status_code=202)
async def analyze_video(request: Request, file: UploadFile = File(...),
                        mode: str = Form("fast"), roi: str = Form(""),
                        user: dict = Depends(require_menu("detection"))):
    region = None
    try:
        if roi:
            region = json.loads(roi)
            if (not isinstance(region, list) or len(region) != 4
                    or any(isinstance(v, bool) or not isinstance(v, (float, int)) for v in region)
                    or not (0 <= region[0] < region[2] <= 1 and 0 <= region[1] < region[3] <= 1)):
                raise ValueError("Vùng nhận diện không hợp lệ.")
        manager = await run_in_threadpool(_get_video_jobs)
        job = await run_in_threadpool(manager.reserve, *_video_identity(request, user), file.filename,
                                     mode=mode, roi=region)
    except (ValueError, json.JSONDecodeError) as exc:
        await file.close()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        await file.close()
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    try:
        size = 0
        with job.source.open("wb") as target:
            while chunk := await file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="Video vượt quá 512 MB. Hãy chia thành đoạn nhỏ hơn.")
                # Large uploads must not block status/cancel/live requests on disk I/O.
                await run_in_threadpool(target.write, chunk)
        if not size:
            raise HTTPException(status_code=400, detail="Video tải lên rỗng.")
        await run_in_threadpool(manager.enqueue, job)
        return await run_in_threadpool(manager.snapshot, job)
    except BaseException:
        await run_in_threadpool(manager.discard, job)
        raise
    finally:
        await file.close()


@router.get("/api/videos/latest")
@router.get("/api/videos/current", include_in_schema=False)
async def current_video_analysis(request: Request,
                                 user: dict = Depends(require_menu("detection"))):
    manager = await run_in_threadpool(_get_video_jobs)
    job = await run_in_threadpool(manager.latest, *_video_identity(request, user))
    snapshot = await run_in_threadpool(manager.snapshot, job) if job is not None else {"job_id": None}
    return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})


@router.get("/api/videos/{job_id}")
async def video_analysis_status(job_id: str, request: Request,
                                after: str = "",
                                user: dict = Depends(require_menu("detection"))):
    manager, job = await run_in_threadpool(_owned_video_job, request, user, job_id)
    snapshot = await run_in_threadpool(manager.snapshot, job)
    if after:
        if not re.fullmatch(r"[0-9a-f]{32}", after):
            raise HTTPException(status_code=400, detail="Mốc sự kiện không hợp lệ.")
        events = snapshot["events"]
        index = next((i for i, event in enumerate(events) if event.get("id") == after), None)
        snapshot["events_total"] = len(events)
        # A missing cursor returns a complete snapshot so clients can resync.
        snapshot["events_complete"] = index is None
        if index is not None:
            snapshot["events"] = events[index + 1:]
    return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})


@router.post("/api/videos/{job_id}/cancel")
async def cancel_video_analysis(job_id: str, request: Request,
                                user: dict = Depends(require_menu("detection"))):
    manager, job = await run_in_threadpool(_owned_video_job, request, user, job_id)
    await run_in_threadpool(manager.cancel_job, job)
    return await run_in_threadpool(manager.snapshot, job)


@router.get("/api/videos/{job_id}/assets/{filename}")
async def video_analysis_crop(job_id: str, filename: str, request: Request,
                              user: dict = Depends(require_menu("detection"))):
    _, job = await run_in_threadpool(_owned_video_job, request, user, job_id)
    if not re.fullmatch(r"[0-9a-f]{32}\.jpg", filename):
        raise HTTPException(status_code=404)
    path = job.directory / filename
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.on_event("shutdown")
def _close_video_jobs():
    if _video_jobs is not None:
        _video_jobs.close()


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
    cameras = cam_svc.get_active_cameras()
    statuses = cam_svc.get_cameras_latest_status()
    for c in cameras:
        c["status"] = statuses.get(str(c["id"]), {}).get("status", "unknown")
    ctx["cameras"] = cameras
    return templates.TemplateResponse("pages/detection.html", {"request": request, **ctx})


def _check_whitelist(plate_text: str) -> dict | None:
    if not plate_text:
        return None
    clean_input = re.sub(r'[^A-Z0-9]', '', plate_text.upper())
    query = """
        SELECT * FROM plate_whitelist 
        WHERE UPPER(regexp_replace(plate_number, '[^a-zA-Z0-9]', '', 'g')) = %s
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


_live_streams_lock = threading.Lock()
_live_streams = OrderedDict()
_LIVE_STREAM_TTL_SECONDS = 20
_MAX_LIVE_STREAMS = 64


def _live_stream_state(stream_key, detector, ocr):
    now = time.monotonic()
    with _live_streams_lock:
        expired = [key for key, value in _live_streams.items()
                   if now - value["last_seen"] > _LIVE_STREAM_TTL_SECONDS]
        for key in expired:
            _live_streams.pop(key, None)

        state = _live_streams.get(stream_key)
        if state is None:
            analyzer = PlateFrameAnalyzer(
                detector, ocr, search_small=True, detector_lock=_detector_lock,
                ocr_lock=_ocr_lock, include_people=True, quick_ocr=True,
            )
            aggregator = OCRTrackAggregator(
                normalize=cfg.normalize_plate,
                validator=analyzer.is_valid,
                recognize=analyzer.recognize_crop,
                recognize_track=analyzer.recognize_track,
                max_gap_seconds=1.25,
                max_crops=5,
                iou_threshold=0.10,
                max_tracks=64,
            )
            state = {
                "analyzer": analyzer,
                "aggregator": aggregator,
                "lock": threading.Lock(),
                "last_seen": now,
                "track_meta": {},
            }
            _live_streams[stream_key] = state
        else:
            _live_streams.move_to_end(stream_key)
            state["last_seen"] = now

        while len(_live_streams) > _MAX_LIVE_STREAMS:
            _live_streams.popitem(last=False)
        return state


def _realtime_plate_item(event, state, frame_bgr, site_id, source, *, snapshot_frame=None):
    track_id = event["track_id"]
    meta = state["track_meta"].setdefault(track_id, {})
    text = event.get("text") or ""
    recognized = event.get("status") == "recognized" and bool(text)
    normalized = cfg.normalize_plate(text) if text else ""
    owner_name = meta.get("owner_name", "unknown")
    status = "no_plate"

    if recognized:
        if not meta.get("whitelist_checked"):
            whitelist = _check_whitelist(normalized)
            meta["owner_name"] = (whitelist.get("owner_name") or "Whitelist") if whitelist else "unknown"
            meta["whitelist_checked"] = True
            owner_name = meta["owner_name"]
        status = "whitelist" if owner_name != "unknown" else "unknown"
        if status == "unknown" and not meta.get("pending_saved"):
            snapshot = save_snapshot(snapshot_frame if snapshot_frame is not None else frame_bgr,
                                     source, normalized)
            _add_pending_plate_simple(
                normalized, text, source, site_id=site_id,
                image_path=snapshot, plate_type="white",
            )
            meta["pending_saved"] = True

    return {
        "type": "plate",
        "text": text or "(BIỂN SỐ ?)",
        "plate_type": "white",
        "confidence": round(float(event.get("detector_confidence") or 0) * 100, 1),
        "ocr_confidence": round(float(event.get("confidence") or 0) * 100, 1),
        "status": status,
        "owner_name": owner_name,
        "bbox": [int(value) for value in event["bbox"]],
        "ocr_text": text,
        "quality": {"quality_score": float(event.get("quality_score") or 0)},
        "valid_format": bool(text and state["analyzer"].is_valid(text)),
        "recognition_status": event.get("status", "unreadable"),
        "track_id": track_id,
        "observations": event.get("observations", 0),
    }


def _detect_realtime_frame(frame_bgr, stream_key, site_id, source):
    detector, ocr, _ = _get_models(include_face=False)
    state = _live_stream_state(stream_key, detector, ocr)
    with state["lock"]:
        timestamp = time.monotonic()
        observations = state["analyzer"].observe(frame_bgr, timestamp)
        plate_observations = [item for item in observations if item.get("type") == "plate"]
        people = [{
            "type": "person", "text": "Person", "confidence": round(float(item["confidence"]) * 100, 1),
            "status": "unknown", "bbox": item["bbox"],
        } for item in observations if item.get("type") == "person"]

        expired = state["aggregator"].update(timestamp, plate_observations)
        active = state["aggregator"].snapshot()
        events_by_id = {event["track_id"]: (event, True) for event in expired}
        events_by_id.update({event["track_id"]: (event, False) for event in active})
        results = people + [
            _realtime_plate_item(
                event, state, frame_bgr, site_id, source,
                snapshot_frame=event.get("best_crop") if is_expired else None,
            )
            for event, is_expired in events_by_id.values()
        ]
        for event in expired:
            state["track_meta"].pop(event["track_id"], None)
        return results


def _detect_plates(frame_bgr, s_id, source="webcam", *, preview_view="both", full_ocr=False):
    detector, ocr, _ = _get_models(include_face=False)
    is_preview = source == "video_preview"
    detections = detect_plate_regions(detector, frame_bgr,
                                     roi=(.2, .2, .8, .8) if is_preview and preview_view == "center" else None,
                                     search_small=source in ("upload", "video") or (is_preview and preview_view == "both"),
                                     lock=_detector_lock)

    plate_res = []
    for x1, y1, x2, y2, conf, cls_id in detections:
        if cls_id == 0:
            h_img, w_img = frame_bgr.shape[:2]
            px = max(2, round((x2 - x1) * .10))
            py = max(1, round((y2 - y1) * .12))
            x1_p, y1_p = max(0, x1 - px), max(0, y1 - py)
            x2_p, y2_p = min(w_img, x2 + px), min(h_img, y2 + py)

            crop = frame_bgr[y1_p:y2_p, x1_p:x2_p]
            quality = assess_image(crop)
            text, ocr_conf, p_type = "", 0.0, "white"
            if quality["valid"]:
                ch = _ocr_crop_hash(crop)
                # Playback frames use the bounded quick reader. Paused frames
                # request both views and can afford the full multi-variant OCR
                # pass, which tries deskewing, sharpening, and thresholding.
                quick_ocr = source == "video_preview" and preview_view != "both" and not full_ocr
                text, ocr_conf, p_type = _read_cached_ocr(ocr, crop, ch, quick=quick_ocr)

            status = "no_plate"
            owner_name = "unknown"
            valid_format = bool(text and ocr._ocr.is_valid_vn_plate(text))
            if valid_format and ocr_conf >= .70:
                normalized = cfg.normalize_plate(text)
                wl = _check_whitelist(normalized)
                if wl:
                    status = "whitelist"
                    owner_name = wl.get("owner_name", "Whitelist")
                else:
                    status = "unknown"

            if status == "unknown" and source != "video_preview":
                snap_path = save_snapshot(frame_bgr, source, normalized)
                _add_pending_plate_simple(
                    normalized, text, source, site_id=s_id,
                    image_path=snap_path, plate_type=p_type,
                )

            plate_item = {
                "type": "plate",
                "text": text if text else "(BIỂN SỐ ?)",
                "plate_type": p_type,
                "confidence": round(conf * 100, 1),
                "ocr_confidence": round(ocr_conf * 100, 1),
                "status": status,
                "owner_name": owner_name,
                "bbox": [x1, y1, x2, y2],
                "ocr_text": text or "",
                "quality": quality,
                "valid_format": valid_format,
                "recognition_status": "recognized" if valid_format and ocr_conf >= .70 else "uncertain" if text else "unreadable",
            }
            if source in ("upload", "video", "video_preview"):
                plate_item["debug_ocr"] = getattr(getattr(ocr, "_ocr", ocr), "last_debug_info", None)
            if source == "video_preview" and quality["valid"]:
                preview = prepare_plate_preview(crop)
                ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 94])
                if ok:
                    plate_item["crop_preview"] = "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")
            plate_res.append(plate_item)
        elif cls_id == 1:
            plate_res.append({
                "type": "person", "text": "Person",
                "confidence": round(conf * 100, 1), "status": "unknown", "bbox": [x1, y1, x2, y2]
            })

    return plate_res


@router.post("/api/detect")
async def detect_image(request: Request,
                       file: UploadFile = File(...),
                       source: str = Form("webcam"),
                       preview_view: str = Form("both"),
                       preview_ocr: str = Form("quick"),
                       stream_id: str = Form(""),
                       user: dict = Depends(require_menu("detection"))):
    contents = await file.read()
    if not contents:
        logger.warning("Detection upload is empty")
        return JSONResponse(
            {"ok": False, "message": "Ảnh tải lên không hợp lệ."},
            status_code=400,
        )
    frame_bgr, dbg_decode = await asyncio.to_thread(decode_image, contents, return_debug=True)
    del contents
    if frame_bgr is None:
        logger.warning("Detection upload decode failed: %s", dbg_decode.get("error"))
        return JSONResponse(
            {"ok": False, "message": f"Ảnh tải lên không hợp lệ hoặc lỗi giải mã: {dbg_decode.get('error', 'Lỗi định dạng')}"},
            status_code=400,
        )
    def run_ai_pipeline():
        res = []

        s_id = request.session.get("active_site_id") or None
        if source in ("upload", "video", "video_preview"):
            # For uploaded images and videos, run plate/vehicle detection synchronously
            plate_res = _detect_plates(frame_bgr, s_id, source=source,
                                      preview_view=preview_view if preview_view in ("full", "center", "both") else "both",
                                      full_ocr=source == "video_preview" and preview_ocr == "full")
            res.extend(plate_res)
        else:
            client_host = request.client.host if request.client else "unknown"
            client_stream_id = (stream_id or f"{source}:{client_host}")[:120]
            stream_key = (
                str(user.get("id") or "anonymous"),
                str(s_id or 0),
                str(source),
                client_stream_id,
            )
            res.extend(_detect_realtime_frame(frame_bgr, stream_key, s_id, source))

        logger.info("API Detect (%s): %s person/plate results", source, len(res))
        return res

    started = time.perf_counter()
    results = await asyncio.to_thread(run_ai_pipeline)
    return JSONResponse({
        "results": results,
        "count": len(results),
        "processing_ms": round((time.perf_counter() - started) * 1000, 1),
        "frame_size": [int(frame_bgr.shape[1]), int(frame_bgr.shape[0])],
        "info": {
            "detector": True,
            "ocr": True,
            "face": False,
            "classes": ["person", "plate"],
            "cached_face": False,
        }
    })


def _mjpeg_frame(frame, quality=75):
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        logger.error("MJPEG frame encoding failed")
        raise RuntimeError("JPEG encoding failed")
    return b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"


def _placeholder_frame(text: str = "Dang ket noi camera...", width=854, height=480):
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (26, 22, 20)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.75
    thickness = 2
    size = cv2.getTextSize(text, font, font_scale, thickness)[0]
    tx = max(10, (width - size[0]) // 2)
    ty = (height + size[1]) // 2
    cv2.putText(img, text, (tx, ty), font, font_scale, (220, 220, 220), thickness, cv2.LINE_AA)
    return _mjpeg_frame(img, 70)


_stream_viewers = {}
_stream_viewers_lock = threading.Lock()


@router.get("/stream/{camera_id}")
async def stream_camera(camera_id: int, request: Request, user: dict = Depends(require_menu("detection"))):
    shm_name = f"shm_cam_{camera_id}"

    async def _stream():
        shm = None
        attach_start = time.time()
        max_wait_seconds = 15.0

        # Wait and retry until Backend Grabber creates SharedMemory
        while shm is None and not await request.is_disconnected():
            try:
                shm = sm.SharedMemory(name=shm_name)
                try:
                    from multiprocessing import resource_tracker
                    resource_tracker.unregister(shm._name, "shared_memory")
                except Exception:
                    pass
                logger.info("Camera %s: Attached to Backend SharedMemory successfully", camera_id)
            except FileNotFoundError:
                elapsed = time.time() - attach_start
                if elapsed > max_wait_seconds:
                    logger.warning("Camera %s: Timed out waiting for SharedMemory (%s)", camera_id, shm_name)
                    yield _placeholder_frame("Camera dang tat hoac mat ket noi")
                    return
                if elapsed > 10.0:
                    cam = cam_svc.get_camera(camera_id)
                    if not cam or not cam.get("is_streaming", False):
                        logger.warning("Camera %s: Stream is down / offline in DB", camera_id)
                        yield _placeholder_frame("Camera dang tat hoac mat ket noi")
                        return
                # Send waiting frame to keep HTTP connection alive and responsive
                yield _placeholder_frame(f"Dang ket noi Camera {camera_id}...")
                await asyncio.sleep(0.5)

        if shm is None:
            return

        last_counter = -1
        last_frame_time = time.monotonic()
        try:
            while not await request.is_disconnected():
                try:
                    counter = int(np.frombuffer(shm.buf, dtype=np.uint32, count=1)[0])
                except (ValueError, BufferError):
                    break

                # If grabber hasn't written any frame yet
                if counter == 0:
                    elapsed = time.time() - attach_start
                    if elapsed > 15.0:
                        logger.warning("Camera %s: No frames received within 15s, camera stream down", camera_id)
                        yield _placeholder_frame("Camera dang tat hoac mat ket noi")
                        break
                    yield _placeholder_frame(f"Dang ket noi Camera {camera_id}...")
                    await asyncio.sleep(0.5)
                    continue

                if counter != last_counter:
                    frame = np.frombuffer(shm.buf, dtype=np.uint8, offset=4, count=cfg.SHM_FRAME_BYTES).reshape((cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, 3)).copy()
                    yield _mjpeg_frame(frame, cfg.STREAM_JPEG_QUALITY_LIVE)
                    last_counter = counter
                    last_frame_time = time.monotonic()
                    await asyncio.sleep(0)
                else:
                    idle_secs = time.monotonic() - last_frame_time
                    if idle_secs > 4.0:
                        cam = cam_svc.get_camera(camera_id)
                        if not cam or not cam.get("is_streaming", False) or idle_secs > 7.0:
                            logger.warning("Camera %s: Stream idle %.1fs, camera disconnected", camera_id, idle_secs)
                            yield _placeholder_frame("Mat ket noi camera (Offline)")
                            break
                    await asyncio.sleep(0.015)
        except Exception as e:
            logger.warning("Camera %s stream loop error: %s", camera_id, e)
        finally:
            try:
                shm.close()
            except Exception:
                pass

    async def frame_generator():
        with _stream_viewers_lock:
            _stream_viewers[camera_id] = _stream_viewers.get(camera_id, 0) + 1
            cam_svc.set_camera_streaming(camera_id, True)

        try:
            async for chunk in _stream():
                yield chunk
        finally:
            with _stream_viewers_lock:
                _stream_viewers[camera_id] = max(0, _stream_viewers.get(camera_id, 1) - 1)
                if _stream_viewers[camera_id] == 0:
                    cam_svc.set_camera_streaming(camera_id, False)
                    logger.info("Camera %s: No active stream viewers, stopped camera streaming in backend.", camera_id)

    return StreamingResponse(frame_generator(), media_type="multipart/x-mixed-replace; boundary=frame")

