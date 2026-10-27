import logging
import threading
import os
import re
import uuid
import asyncio
import concurrent.futures
import time
import hashlib
import math
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
from backend.ai.inference import PlateDetector, OCRReader, FaceRecognizer, validate_plate_crop
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
        # Model initialization can hold this lock for seconds; never wait on it
        # in the web event loop while unrelated pages and progress need service.
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
    s_id_val = int(site_id) if (site_id is not None and str(site_id).isdigit()) else None
    saved = db.execute_query("""
        INSERT INTO pending_plates (pending_id, plate_raw, plate_norm, first_seen_utc, status, site_id, image_path, plate_type)
        VALUES (%s, %s, %s, NOW(), 'pending', %s, %s, %s)
        ON CONFLICT (pending_id) DO NOTHING
    """, (pid, plate_raw, plate_norm, s_id_val, image_path, plate_type), commit=True)
    if not saved:
        logger.error("Pending plate insert returned no row for site %s", site_id)
        return None
    return "inserted"


def _authorize_detection_camera(camera_id: int, site_id) -> None:
    camera = cam_svc.get_camera(camera_id)
    if not camera or not camera.get("is_active"):
        raise HTTPException(status_code=404, detail="Camera không khả dụng.")
    if site_id is None:
        return

    site_ids = camera.get("site_ids")
    if isinstance(site_ids, str):
        try:
            site_ids = json.loads(site_ids)
        except (TypeError, ValueError):
            site_ids = []
    try:
        allowed_sites = {int(value) for value in (site_ids or [])}
        selected_site = int(site_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Camera không khả dụng.")
    if selected_site not in allowed_sites:
        raise HTTPException(status_code=404, detail="Camera không khả dụng.")


def _read_latest_camera_frame(camera_id: int) -> np.ndarray:
    shm = None
    try:
        shm = sm.SharedMemory(name=f"shm_cam_{camera_id}", create=False)
        try:
            from multiprocessing import resource_tracker
            resource_tracker.unregister(shm._name, "shared_memory")
        except Exception:
            pass
        counter = np.ndarray((1,), dtype=np.uint32, buffer=shm.buf[:4])
        frame_buffer = np.ndarray(
            (cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, 3), dtype=np.uint8, buffer=shm.buf[4:]
        )
        for _ in range(3):
            before = int(counter[0])
            if before == 0 or before & 1:
                continue
            frame = frame_buffer.copy()
            after = int(counter[0])
            if after == before and not after & 1:
                return frame
    except (FileNotFoundError, OSError, ValueError, BufferError) as error:
        logger.info("Camera %s has no readable shared frame: %s", camera_id, error)
    finally:
        if shm is not None:
            try:
                shm.close()
            except Exception:
                pass
    raise HTTPException(status_code=503, detail="Camera chưa có khung hình để nhận diện.")


class TrackedPlate:
    def __init__(self, track_id: int, bbox: list, det_conf: float, ts: float, frame_bgr=None):
        self.track_id = track_id
        self.bbox = list(bbox)
        self.det_conf = det_conf
        self.first_ts = ts
        self.last_ts = ts
        self.text = ""
        self.ocr_conf = 0.0
        self.plate_type = "white"
        self.status = "no_plate"
        self.owner_name = "unknown"
        self.confirmed = False
        self.ocr_attempts = 0
        self.missed_frames = 0
        self.vx = 0.0
        self.vy = 0.0
        self.template = None
        self.db_saved = False
        self.plate_candidates = {}
        if frame_bgr is not None:
            self._save_template(frame_bgr, bbox)

    def _save_template(self, frame_bgr, b):
        h, w = frame_bgr.shape[:2]
        x1, y1, x2, y2 = b
        pw, ph = x2 - x1, y2 - y1
        tx1 = max(0, x1 - int(pw * 0.15))
        ty1 = max(0, y1 - int(ph * 0.15))
        tx2 = min(w, x2 + int(pw * 0.15))
        ty2 = min(h, y2 + int(ph * 0.15))
        if tx2 > tx1 and ty2 > ty1:
            self.template = frame_bgr[ty1:ty2, tx1:tx2].copy()

    def update_bbox(self, new_bbox: list, det_conf: float, ts: float, frame_bgr=None):
        dt = max(0.05, ts - self.last_ts)
        old_cx = (self.bbox[0] + self.bbox[2]) / 2.0
        old_cy = (self.bbox[1] + self.bbox[3]) / 2.0
        new_cx = (new_bbox[0] + new_bbox[2]) / 2.0
        new_cy = (new_bbox[1] + new_bbox[3]) / 2.0

        curr_vx = (new_cx - old_cx) / dt * 0.2
        curr_vy = (new_cy - old_cy) / dt * 0.2
        self.vx = 0.6 * self.vx + 0.4 * curr_vx
        self.vy = 0.6 * self.vy + 0.4 * curr_vy

        self.bbox = list(new_bbox)
        self.det_conf = max(self.det_conf, det_conf)
        self.last_ts = ts
        self.missed_frames = 0
        if frame_bgr is not None and self.confirmed:
            self._save_template(frame_bgr, new_bbox)

    def set_ocr_result(self, text: str, conf: float, ptype: str, status: str, owner_name: str, frame_bgr=None):
        self.ocr_attempts += 1
        clean = (text or "").strip().upper()
        if not clean or clean == "(BIỂN SỐ ?)":
            return False

        if clean not in self.plate_candidates:
            self.plate_candidates[clean] = {
                "count": 0, "max_conf": 0.0, "ptype": ptype, "status": status, "owner_name": owner_name
            }
        cand = self.plate_candidates[clean]
        cand["count"] += 1
        cand["max_conf"] = max(cand["max_conf"], conf)
        if status != "unknown" and cand["status"] == "unknown":
            cand["status"] = status
            cand["owner_name"] = owner_name

        def _candidate_score(c_txt):
            c_data = self.plate_candidates[c_txt]
            is_std_pattern = bool(re.match(r"^[0-9]{2}[A-Z]{1,2}-?[0-9]{3,5}", c_txt))
            alphanumeric = re.sub(r"[^A-Z0-9]", "", c_txt)
            has_letter = bool(re.search(r"[A-Z]", alphanumeric))
            full_len = len(alphanumeric) >= 7

            score = c_data["max_conf"] * math.sqrt(c_data["count"])
            if is_std_pattern and full_len:
                score *= 1.6
            elif has_letter and full_len:
                score *= 1.3
            elif not has_letter:
                score *= 0.6
            return score

        best_text = max(self.plate_candidates.keys(), key=_candidate_score)
        best_data = self.plate_candidates[best_text]
        old_text = self.text
        self.text = best_text
        self.ocr_conf = best_data["max_conf"]
        self.plate_type = best_data["ptype"]
        self.status = best_data["status"]
        self.owner_name = best_data["owner_name"]

        alphanumeric = re.sub(r"[^A-Z0-9]", "", best_text)
        has_letter = bool(re.search(r"[A-Z]", alphanumeric))
        if len(alphanumeric) >= 7 and has_letter:
            if (best_data["count"] >= 2 and best_data["max_conf"] >= 0.88) or best_data["max_conf"] >= 0.96:
                self.confirmed = True
                if frame_bgr is not None:
                    self._save_template(frame_bgr, self.bbox)

        return (best_text != old_text)


class SpatialPlateTracker:
    def __init__(self, max_lost_seconds: float = 6.0):
        self.next_id = 1
        self.tracks: dict[int, TrackedPlate] = {}
        self.max_lost_seconds = max_lost_seconds

    @staticmethod
    def _compute_iou(b1, b2) -> float:
        x1 = max(b1[0], b2[0])
        y1 = max(b1[1], b2[1])
        x2 = min(b1[2], b2[2])
        y2 = min(b1[3], b2[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        a1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
        a2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
        union = a1 + a2 - inter
        return inter / union if union > 0 else 0.0

    @staticmethod
    def _center_dist(b1, b2) -> float:
        c1x, c1y = (b1[0] + b1[2]) / 2, (b1[1] + b1[3]) / 2
        c2x, c2y = (b2[0] + b2[2]) / 2, (b2[1] + b2[3]) / 2
        return math.hypot(c1x - c2x, c1y - c2y)

    def match_and_update(self, detections: list, now_ts: float, frame_bgr=None):
        """
        Khớp các detection biển số với tracks đã có bằng IoU, Centroid Distance
        và Visual Coasting (bám vết bằng ảnh mẫu khi xe đi xa hoặc YOLO bỏ sót).
        """
        stale = [tid for tid, tr in self.tracks.items() if now_ts - tr.last_ts > self.max_lost_seconds]
        for tid in stale:
            del self.tracks[tid]

        matched_detections = set()
        matched_tracks = set()
        results = []

        # 1. Khớp IoU trước (vị trí gần hoặc xe đứng yên/chuyển động nhẹ)
        for tid, track in list(self.tracks.items()):
            best_iou = 0.20
            best_d_idx = None
            for d_idx, det in enumerate(detections):
                if d_idx in matched_detections:
                    continue
                score = self._compute_iou(det[:4], track.bbox)
                if score > best_iou:
                    best_iou = score
                    best_d_idx = d_idx

            if best_d_idx is not None:
                det = detections[best_d_idx]
                track.update_bbox(det[:4], det[4], now_ts, frame_bgr=frame_bgr)
                matched_detections.add(best_d_idx)
                matched_tracks.add(tid)
                results.append((track, det[:4], det[4], False))

        # 2. Khớp bổ sung bằng Centroid Distance (cho xe di chuyển hoặc sau khi bị khuất 1-2 giây)
        for tid, track in list(self.tracks.items()):
            if tid in matched_tracks:
                continue
            tw = track.bbox[2] - track.bbox[0]
            th = track.bbox[3] - track.bbox[1]
            max_dist = max(tw, th) * 2.5

            best_dist = max_dist
            best_d_idx = None
            for d_idx, det in enumerate(detections):
                if d_idx in matched_detections:
                    continue
                d = self._center_dist(det[:4], track.bbox)
                if d < best_dist:
                    best_dist = d
                    best_d_idx = d_idx

            if best_d_idx is not None:
                det = detections[best_d_idx]
                track.update_bbox(det[:4], det[4], now_ts, frame_bgr=frame_bgr)
                matched_detections.add(best_d_idx)
                matched_tracks.add(tid)
                results.append((track, det[:4], det[4], False))

        # 3. Tạo track mới cho các phát hiện chưa từng khớp
        for d_idx, det in enumerate(detections):
            if d_idx not in matched_detections:
                tid = self.next_id
                self.next_id += 1
                track = TrackedPlate(tid, det[:4], det[4], now_ts, frame_bgr=frame_bgr)
                self.tracks[tid] = track
                results.append((track, det[:4], det[4], True))

        # 4. Visual Coasting: Giữ khóa vết xe đã nhận diện biển số khi YOLO bị miss do xe đi xa
        for tid, track in list(self.tracks.items()):
            if tid in matched_tracks:
                continue
            if not track.confirmed or not track.text or track.text == "(BIỂN SỐ ?)":
                continue

            track.missed_frames += 1
            if track.missed_frames <= 30 and track.template is not None and frame_bgr is not None:
                fh, fw = frame_bgr.shape[:2]
                tcx = (track.bbox[0] + track.bbox[2]) // 2
                tcy = (track.bbox[1] + track.bbox[3]) // 2

                pred_cx = int(tcx + track.vx)
                pred_cy = int(tcy + track.vy)

                bw = track.bbox[2] - track.bbox[0]
                bh = track.bbox[3] - track.bbox[1]
                sr = max(70, int(max(bw, bh) * 2.5))
                sx1, sy1 = max(0, pred_cx - sr), max(0, pred_cy - sr)
                sx2, sy2 = min(fw, pred_cx + sr), min(fh, pred_cy + sr)

                temp_h, temp_w = track.template.shape[:2]
                if (sy2 - sy1) >= temp_h and (sx2 - sx1) >= temp_w:
                    search_area = frame_bgr[sy1:sy2, sx1:sx2]
                    match_res = cv2.matchTemplate(search_area, track.template, cv2.TM_CCOEFF_NORMED)
                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(match_res)

                    if max_val >= 0.35:
                        mx = sx1 + max_loc[0]
                        my = sy1 + max_loc[1]
                        new_bbox = [mx, my, mx + bw, my + bh]
                        track.bbox = new_bbox
                        track.last_ts = now_ts
                        matched_tracks.add(tid)
                        results.append((track, new_bbox, track.det_conf, False))
                    elif track.missed_frames <= 8:
                        # Nội suy theo vận tốc nếu mất dấu tạm thời trong 1-2 frame
                        new_bbox = [track.bbox[0] + int(track.vx), track.bbox[1] + int(track.vy),
                                    track.bbox[2] + int(track.vx), track.bbox[3] + int(track.vy)]
                        if 0 <= new_bbox[0] < fw and 0 <= new_bbox[1] < fh:
                            track.bbox = new_bbox
                            track.last_ts = now_ts
                            matched_tracks.add(tid)
                            results.append((track, new_bbox, track.det_conf, False))

        return results

    def reset(self):
        self.tracks.clear()
        self.next_id = 1


_trackers_lock = threading.Lock()
_trackers: dict[str, SpatialPlateTracker] = {}


def _get_spatial_tracker(source: str, s_id=None) -> SpatialPlateTracker:
    key = f"{source}_{s_id or 'default'}"
    with _trackers_lock:
        if key not in _trackers:
            _trackers[key] = SpatialPlateTracker(max_lost_seconds=4.0)
        return _trackers[key]


_live_streams_lock = threading.Lock()
_live_streams = OrderedDict()
_LIVE_STREAM_TTL_SECONDS = 20
_MAX_LIVE_STREAMS = 64


def _live_stream_state(stream_key, detector, ocr, *, quick_ocr=True, search_small=True):
    now = time.monotonic()
    with _live_streams_lock:
        expired = [key for key, value in _live_streams.items()
                   if now - value["last_seen"] > _LIVE_STREAM_TTL_SECONDS]
        for key in expired:
            _live_streams.pop(key, None)

        state = _live_streams.get(stream_key)
        if state is None:
            analyzer = PlateFrameAnalyzer(
                detector, ocr, search_small=search_small, detector_lock=_detector_lock,
                ocr_lock=_ocr_lock, include_people=True, quick_ocr=quick_ocr,
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


def _format_vn_plate_for_display(value: str) -> str:
    """Format recognized Vietnamese plate numbers for display only."""
    if not isinstance(value, str) or not value.strip():
        return ""
    original = value.strip().upper()
    clean = re.sub(r"[^A-Z0-9]", "", original)
    patterns = (
        r"^(\d{2}[A-Z]{1,2})(\d{4,5})$",  # 30F-945.35
        r"^(\d{2}[A-Z]\d)(\d{4,5})$",  # 29A1-123.45
        r"^(T\d{2})(\d{4,5})$",  # Temporary plates
        r"^([A-Z]{2})(\d{4,5})$",  # Military / special plates
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, clean)
        if match:
            prefix, number = match.groups()
            suffix = f"{number[:3]}.{number[3:]}" if len(number) == 5 else number
            return f"{prefix}-{suffix}"
    return original


def _realtime_plate_item(event, state, frame_bgr, site_id, source, *, snapshot_frame=None):
    track_id = event["track_id"]
    meta = state["track_meta"].setdefault(track_id, {})
    text = event.get("text") or ""
    display_text = _format_vn_plate_for_display(text)
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
        if status == "unknown" and source != "video_preview" and not meta.get("pending_saved"):
            snapshot = save_snapshot(snapshot_frame if snapshot_frame is not None else frame_bgr,
                                     source, normalized)
            _add_pending_plate_simple(
                normalized, text, source, site_id=site_id,
                image_path=snapshot, plate_type="white",
            )
            meta["pending_saved"] = True

    item = {
        "type": "plate",
        "text": display_text or "(BIỂN SỐ ?)",
        "plate_type": "white",
        "confidence": round(float(event.get("detector_confidence") or 0) * 100, 1),
        "ocr_confidence": round(float(event.get("confidence") or 0) * 100, 1),
        "status": status,
        "owner_name": owner_name,
        "bbox": [int(value) for value in event["bbox"]],
        "ocr_text": display_text,
        "quality": {"quality_score": float(event.get("quality_score") or 0)},
        "valid_format": bool(text and state["analyzer"].is_valid(text)),
        "recognition_status": event.get("status", "unreadable"),
        "track_id": track_id,
        "stream_id": str(state.get("stream_id") or ""),
        "start_time": event.get("start_time"),
        "end_time": event.get("end_time"),
        "best_crop_time": event.get("best_crop_time"),
        "observations": event.get("observations", 0),
    }
    best_crop = event.get("best_crop")
    best_crop_time = event.get("best_crop_time")
    if source == "video_preview" and best_crop is not None and meta.get("preview_time") != best_crop_time:
        raw_crop = np.ascontiguousarray(best_crop)
        preview_crop = prepare_plate_preview(raw_crop)
        ok, encoded = cv2.imencode(".jpg", preview_crop, [cv2.IMWRITE_JPEG_QUALITY, 94])
        if ok:
            item["crop_preview"] = "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")
        ok, encoded = cv2.imencode(".jpg", raw_crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if ok:
            item["crop_original"] = "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")
        crop_quality = assess_image(raw_crop)
        item["quality"] = crop_quality
        meta["preview_time"] = best_crop_time
    return item


def _detect_realtime_frame(frame_bgr, stream_key, site_id, source, *, timestamp=None,
                           finish_tracks=False, quick_ocr=True, search_small=True):
    detector, ocr, _ = _get_models(include_face=False)
    state = _live_stream_state(stream_key, detector, ocr, quick_ocr=quick_ocr, search_small=search_small)
    with state["lock"]:
        state["stream_id"] = stream_key[-1] if stream_key else ""
        timestamp = time.monotonic() if timestamp is None else float(timestamp)
        state["analyzer"].quick_ocr = quick_ocr
        observations = state["analyzer"].observe(frame_bgr, timestamp)
        plate_observations = [item for item in observations if item.get("type") == "plate"]
        people = [{
            "type": "person", "text": "Person", "confidence": round(float(item["confidence"]) * 100, 1),
            "status": "unknown", "bbox": item["bbox"],
        } for item in observations if item.get("type") == "person"]

        expired = state["aggregator"].update(timestamp, plate_observations)
        active = [] if finish_tracks else state["aggregator"].snapshot()
        if finish_tracks:
            expired.extend(state["aggregator"].finish())
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

    plate_dets = [d for d in detections if d[5] == 0]
    other_dets = [d for d in detections if d[5] != 0]

    plate_res = []

    if source == "upload":
        # Ảnh tĩnh tải lên: Xử lý độc lập từng ảnh, không dùng tracker thời gian
        for x1, y1, x2, y2, conf, cls_id in plate_dets:
            h_img, w_img = frame_bgr.shape[:2]
            px = int((x2 - x1) * 0.15) + 10
            py = int((y2 - y1) * 0.15) + 5
            x1_p, y1_p = max(0, x1 - px), max(0, y1 - py)
            x2_p, y2_p = min(w_img, x2 + px), min(h_img, y2 + py)
            crop = frame_bgr[y1_p:y2_p, x1_p:x2_p]
            text, ocr_conf, p_type = "", 0.0, "white"
            if validate_plate_crop(crop):
                ch = _ocr_crop_hash(crop)
                text, ocr_conf, p_type = _read_cached_ocr(ocr, crop, ch)
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
            if not text and conf < 0.45:
                continue
            plate_res.append({
                "type": "plate",
                "text": text if text else "(BIỂN SỐ ?)",
                "plate_type": p_type,
                "confidence": round(conf * 100, 1),
                "ocr_confidence": round(ocr_conf * 100, 1),
                "status": status,
                "owner_name": owner_name,
                "is_confirmed": bool(text and text != "(BIỂN SỐ ?)"),
                "bbox": [x1, y1, x2, y2],
                "debug_ocr": getattr(getattr(ocr, "_ocr", ocr), "last_debug_info", None)
            })
    else:
        tracker = _get_spatial_tracker(source, s_id)
        now_ts = time.time()
        matched_items = tracker.match_and_update(plate_dets, now_ts, frame_bgr=frame_bgr)

        for track, (x1, y1, x2, y2), conf, is_new in matched_items:
            # Nếu xe đã xác nhận biển số chắc chắn (confirmed và có ít nhất 2 lần đọc hoặc conf >= 0.96):
            # -> DÙNG LẠI DỮ LIỆU CŨ NGAY LẬP TỨC!
            # Tiết kiệm CPU, duy trì bám vết xe liên tục kể cả khi xe đi xa hoặc bị mờ.
            if track.confirmed and (track.ocr_conf >= 0.96 or track.ocr_attempts >= 5):
                plate_item = {
                    "type": "plate",
                    "text": track.text,
                    "track_id": track.track_id,
                    "plate_type": track.plate_type,
                    "confidence": round(conf * 100, 1),
                    "ocr_confidence": round(track.ocr_conf * 100, 1),
                    "status": track.status,
                    "owner_name": track.owner_name,
                    "is_confirmed": True,
                    "bbox": [x1, y1, x2, y2],
                }
                if source == "video":
                    plate_item["debug_ocr"] = getattr(getattr(ocr, "_ocr", ocr), "last_debug_info", None)
                plate_res.append(plate_item)
                continue

            # Nếu chưa xác nhận hoặc cần tinh chỉnh thêm ở các khung hình ban đầu:
            h_img, w_img = frame_bgr.shape[:2]
            px = max(2, round((x2 - x1) * .10))
            py = max(1, round((y2 - y1) * .12))
            x1_p, y1_p = max(0, x1 - px), max(0, y1 - py)
            x2_p, y2_p = min(w_img, x2 + px), min(h_img, y2 + py)

            crop = frame_bgr[y1_p:y2_p, x1_p:x2_p]
            quality = assess_image(crop)
            text, ocr_conf, p_type = "", 0.0, "white"
            if validate_plate_crop(crop) and quality.get("valid", True):
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

                track.set_ocr_result(text, ocr_conf, p_type, status, owner_name, frame_bgr=frame_bgr)

                # Chỉ lưu snapshot vào pending_plates 1 lần duy nhất cho mỗi xe khi đã xác nhận biển chuẩn
                if track.confirmed and not track.db_saved and track.status == "unknown" and source != "video_preview":
                    track.db_saved = True
                    def _bg_save(f_copy, src, norm, txt, sid, pt):
                        try:
                            snap_path = save_snapshot(f_copy, src, norm)
                            _add_pending_plate_simple(
                                norm, txt, src, site_id=sid,
                                image_path=snap_path, plate_type=pt,
                            )
                        except Exception as e:
                            logger.debug("Background snapshot save error: %s", e)
                    threading.Thread(
                        target=_bg_save,
                        args=(frame_bgr.copy(), source, cfg.normalize_plate(track.text), track.text, s_id, track.plate_type),
                        daemon=True
                    ).start()

            raw_disp = track.text if track.text else (text if text else "")
            display_text = _format_vn_plate_for_display(raw_disp) if raw_disp else ""
            display_conf = track.ocr_conf if track.text else ocr_conf
            display_ptype = track.plate_type if track.text else p_type
            display_status = track.status if track.text else status
            display_owner = track.owner_name if track.text else owner_name

            # Lọc bỏ các phát hiện rác ở vỉa hè / mặt đất không có chữ (dương tính giả)
            if not display_text and not track.confirmed and conf < 0.45:
                continue

            plate_item = {
                "type": "plate",
                "text": display_text if display_text else "(BIỂN SỐ ?)",
                "track_id": track.track_id,
                "plate_type": display_ptype,
                "confidence": round(conf * 100, 1),
                "ocr_confidence": round(display_conf * 100, 1),
                "status": display_status,
                "owner_name": display_owner,
                "is_confirmed": track.confirmed,
                "bbox": [x1, y1, x2, y2],
                "ocr_text": display_text or "",
                "quality": quality,
                "valid_format": valid_format,
                "recognition_status": "recognized" if valid_format and ocr_conf >= .70 else "uncertain" if text else "unreadable",
            }
            if source in ("upload", "video", "video_preview"):
                plate_item["debug_ocr"] = getattr(getattr(ocr, "_ocr", ocr), "last_debug_info", None)
            if source == "video_preview" and quality.get("valid"):
                preview = prepare_plate_preview(crop)
                ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 94])
                if ok:
                    plate_item["crop_preview"] = "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")
            plate_res.append(plate_item)

    for x1, y1, x2, y2, conf, cls_id in other_dets:
        if cls_id == 1:
            plate_res.append({
                "type": "person", "text": "Person",
                "confidence": round(conf * 100, 1), "status": "unknown", "bbox": [x1, y1, x2, y2]
            })

    return plate_res


@router.post("/api/detect")
async def detect_image(request: Request,
                       file: UploadFile | None = File(None),
                       source: str = Form("webcam"),
                       preview_view: str = Form("both"),
                       preview_ocr: str = Form("quick"),
                       stream_id: str = Form(""),
                       camera_id: int = Form(0),
                       video_time: float = Form(-1),
                       finish_tracks: bool = Form(False),
                       user: dict = Depends(require_menu("detection"))):
    frame_bgr = None
    if source == "rtsp" and camera_id > 0:
        active_site_id = request.session.get("active_site_id") or None
        await asyncio.to_thread(_authorize_detection_camera, camera_id, active_site_id)
        frame_bgr = await asyncio.to_thread(_read_latest_camera_frame, camera_id)
    else:
        if file is None:
            return JSONResponse(
                {"ok": False, "message": "Thi\u1ebfu \u1ea3nh nh\u1eadn di\u1ec7n."}, status_code=400,
            )
        contents = await file.read()
        if not contents:
            logger.warning("Detection upload is empty")
            return JSONResponse(
                {"ok": False, "message": "\u1ea2nh t\u1ea3i l\u00ean kh\u00f4ng h\u1ee3p l\u1ec7."},
                status_code=400,
            )
        frame_bgr, dbg_decode = await asyncio.to_thread(decode_image, contents, return_debug=True)
        del contents
        if frame_bgr is None:
            logger.warning("Detection upload decode failed: %s", dbg_decode.get("error"))
            decode_error = dbg_decode.get("error") or "invalid format"
            return JSONResponse(
                {"ok": False, "message": f"\u1ea2nh t\u1ea3i l\u00ean kh\u00f4ng h\u1ee3p l\u1ec7 ho\u1eb7c l\u1ed7i gi\u1ea3i m\u00e3: {decode_error}"},
                status_code=400,
            )

    def run_ai_pipeline():
        res = []

        s_id = request.session.get("active_site_id") or None
        if source == "video_preview":
            client_host = request.client.host if request.client else "unknown"
            client_stream_id = (stream_id or f"video_preview:{client_host}")[:120]
            stream_key = (
                str(user.get("id") or "anonymous"),
                str(s_id or 0),
                source,
                client_stream_id,
            )
            timestamp = float(video_time) if np.isfinite(video_time) and video_time >= 0 else None
            res.extend(_detect_realtime_frame(
                frame_bgr, stream_key, s_id, source,
                timestamp=timestamp,
                finish_tracks=finish_tracks,
                quick_ocr=preview_ocr != "full",
                search_small=False,
            ))
        elif source in ("upload", "video"):
            # Uploaded stills and queued video jobs use their dedicated OCR path.
            plate_res = _detect_plates(frame_bgr, s_id, source=source,
                                      preview_view=preview_view if preview_view in ("full", "center", "both") else "both",
                                      full_ocr=False)
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

                # Odd sequence values mean the grabber is replacing the frame.
                if counter & 1:
                    await asyncio.sleep(0)
                    continue

                if counter != last_counter:
                    frame = np.frombuffer(shm.buf, dtype=np.uint8, offset=4, count=cfg.SHM_FRAME_BYTES).reshape((cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, 3)).copy()
                    after = int(np.frombuffer(shm.buf, dtype=np.uint32, count=1)[0])
                    if after != counter or after & 1:
                        await asyncio.sleep(0)
                        continue
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


@router.post("/api/reset-tracker")
async def reset_tracker(user: dict = Depends(require_menu("detection"))):
    with _trackers_lock:
        _trackers.clear()
    return {"ok": True, "message": "Tracker cache cleared"}


