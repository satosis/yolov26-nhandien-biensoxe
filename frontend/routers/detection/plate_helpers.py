import re
import uuid
import time
import json
import base64
import threading
import logging
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
import numpy as np
import cv2
import multiprocessing.shared_memory as sm
from fastapi import HTTPException

from backend.services import config as cfg
from backend.services.database import db
from backend.camera import camera_service as cam_svc
from backend.util.image_utils import save_snapshot
from backend.ai.plate_analysis import PlateFrameAnalyzer, detect_plate_regions
from backend.util.ocr_pipeline import OCRTrackAggregator, assess_image
from backend.util.ocr_utils import prepare_plate_preview
from backend.ai.inference import validate_plate_crop

from .model_registry import (
    _get_models,
    _read_cached_ocr,
    _ocr_crop_hash,
    _detector_lock,
    _ocr_lock,
)
from .tracking import _get_spatial_tracker

logger = logging.getLogger(__name__)
_bg_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="plate-snap")


from backend.repositories.plate_repo import check_whitelist as _check_whitelist
from backend.repositories.plate_repo import add_pending as _add_pending_plate_simple


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
                    _bg_pool.submit(
                        _bg_save,
                        frame_bgr.copy(), source, cfg.normalize_plate(track.text), track.text, s_id, track.plate_type,
                    )

            raw_disp = track.text if track.text else (text if text else "")
            display_text = _format_vn_plate_for_display(raw_disp) if raw_disp else ""
            display_conf = track.ocr_conf if track.text else ocr_conf
            display_ptype = track.plate_type if track.text else p_type
            display_status = track.status if track.text else status
            display_owner = track.owner_name if track.text else owner_name

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
