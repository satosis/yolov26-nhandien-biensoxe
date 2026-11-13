import time
import json
import threading
import logging
from collections import OrderedDict
import numpy as np
import cv2
import multiprocessing.shared_memory as sm
from fastapi import HTTPException

from backend.services import config as cfg
from backend.camera import camera_service as cam_svc
from backend.util.image_utils import jpeg_data_uri, save_snapshot
from backend.ai.plate_analysis import PlateStream, format_plate
from backend.util.ocr.plate_image import display_image
from backend.repositories.plate_repo import check_whitelist as _check_whitelist
from backend.repositories.plate_repo import add_pending as _add_pending_plate_simple

from .model_registry import _get_models, _detector_lock, _ocr_lock

logger = logging.getLogger(__name__)

# Nguồn camera thật trên trang: biển lạ đã chốt được đưa vào danh sách chờ duyệt.
_PENDING_SOURCES = ("webcam", "rtsp")
# Ảnh người chỉ để nhận ra trên thẻ, không cần to hơn mức này.
_PERSON_IMAGE_HEIGHT = 256


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


_streams_lock = threading.Lock()
_streams = OrderedDict()
_STREAM_TTL_SECONDS = 20
_MAX_STREAMS = 64


def _stream_state(stream_key):
    """Luồng biển số của một nguồn trên trang (một webcam, một camera, một video đang xem, một ảnh)."""
    detector, ocr, _ = _get_models(include_face=False)
    now = time.monotonic()
    with _streams_lock:
        expired = [key for key, value in _streams.items() if now - value["last_seen"] > _STREAM_TTL_SECONDS]
        for key in expired:
            _streams.pop(key, None)

        state = _streams.get(stream_key)
        if state is None:
            state = {
                "stream": PlateStream(detector, ocr, "page", normalize=cfg.normalize_plate,
                                      detector_lock=_detector_lock, ocr_lock=_ocr_lock),
                "lock": threading.Lock(),
                "last_seen": now,
                "stream_id": stream_key[-1],
            }
            _streams[stream_key] = state
        else:
            _streams.move_to_end(stream_key)
            state["last_seen"] = now

        while len(_streams) > _MAX_STREAMS:
            _streams.popitem(last=False)
        return state


def clear_streams():
    with _streams_lock:
        _streams.clear()


def _person_item(observation):
    crop = observation["crop"]
    if crop.shape[0] > _PERSON_IMAGE_HEIGHT:
        scale = _PERSON_IMAGE_HEIGHT / crop.shape[0]
        crop = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)), _PERSON_IMAGE_HEIGHT),
                          interpolation=cv2.INTER_AREA)
    return {
        "type": "person", "confidence": round(float(observation["confidence"]) * 100, 1),
        "bbox": [int(value) for value in observation["bbox"]], "crop_preview": jpeg_data_uri(crop),
    }


def _plate_item(event, state, frame_bgr, site_id, source, *, final):
    """Một item biển số cho mọi nguồn: chữ, trạng thái whitelist, ảnh gốc + ảnh đã xử lý."""
    stream = state["stream"]
    meta = stream.track_meta.setdefault(event["track_id"], {})
    text = event.get("text") or ""
    display_text = format_plate(text)
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
        if status == "unknown" and source in _PENDING_SOURCES and not meta.get("pending_saved"):
            # Track đã kết thúc thì khung hiện tại không còn xe: lưu crop biển tốt nhất.
            best = event.get("best_crop")
            snapshot = save_snapshot(best if final and best is not None else frame_bgr, source, normalized)
            _add_pending_plate_simple(
                normalized, text, source, site_id=site_id,
                image_path=snapshot, plate_type="white",
            )
            meta["pending_saved"] = True

    item = {
        "type": "plate",
        "text": display_text or "(BIỂN SỐ ?)",
        "confidence": round(float(event.get("detector_confidence") or 0) * 100, 1),
        "ocr_confidence": round(float(event.get("confidence") or 0) * 100, 1),
        "status": status,
        "owner_name": owner_name,
        "bbox": [int(value) for value in event["bbox"]],
        "ocr_text": display_text,
        "valid_format": bool(text and stream.analyzer.is_valid(text)),
        "recognition_status": event.get("status", "unreadable"),
        "track_id": event["track_id"],
        "stream_id": str(state.get("stream_id") or ""),
        "best_crop_time": event.get("best_crop_time"),
    }
    best_crop = event.get("best_crop")
    best_crop_time = event.get("best_crop_time")
    if best_crop is not None and meta.get("crop_time") != best_crop_time:
        item["crop_preview"] = jpeg_data_uri(display_image(best_crop))
        item["crop_original"] = jpeg_data_uri(best_crop)
        meta["crop_time"] = best_crop_time
    return item


def detect_frame(frame_bgr, stream_key, site_id, source, *, timestamp=None, finish=False):
    """Một khung của bất kỳ tab nào trên trang Nhận diện → người và biển số có trong khung đó.

    Camera, Webcam, Ảnh và Video đi đúng một đường: luồng theo stream_key, một bước PlateStream.step,
    một cách dựng item. Tab chỉ khác nhau ở nơi lấy khung hình; source chỉ còn quyết định biển lạ có
    được ghi vào danh sách chờ duyệt hay không. Không thêm nhánh xử lý riêng theo tab ở đây.
    timestamp: thời điểm của khung trong nguồn (video); nguồn không có thì lấy đồng hồ máy chủ.
    finish: khung đứng yên (ảnh đơn, video tạm dừng) — soát kỹ khung đó và chốt luôn mọi track.
    """
    state = _stream_state(stream_key)
    stream = state["stream"]
    with state["lock"]:
        moment = time.monotonic() if timestamp is None else float(timestamp)
        people, active, expired = stream.step(frame_bgr, moment, finish=finish)
        results = [_person_item(person) for person in people]
        # Xe không còn ở khung này (track vừa kết thúc, hoặc còn chờ ghép sau khi xe rời đi) thì
        # không có khung để vẽ: track kết thúc vẫn gửi về để cập nhật thẻ, kèm visible = False.
        results += [{**_plate_item(event, state, frame_bgr, site_id, source, final=True),
                     "visible": event["end_time"] == moment} for event in expired]
        results += [_plate_item(event, state, frame_bgr, site_id, source, final=False)
                    for event in active if event["end_time"] == moment]
        for event in expired:
            stream.track_meta.pop(event["track_id"], None)
        return results
