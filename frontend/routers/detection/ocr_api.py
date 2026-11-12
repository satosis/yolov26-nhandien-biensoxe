import time
import asyncio
import logging
import numpy as np
from fastapi import Request, UploadFile
from fastapi.responses import JSONResponse

from backend.util.image_utils import decode_image
from .tracking import _trackers_lock, _trackers
from .plate_helpers import (
    _authorize_detection_camera,
    _read_latest_camera_frame,
    _detect_realtime_frame,
    _detect_plates,
)

logger = logging.getLogger(__name__)


async def detect_image(
    request: Request,
    file: UploadFile | None,
    source: str,
    preview_view: str,
    preview_ocr: str,
    stream_id: str,
    camera_id: int,
    video_time: float,
    finish_tracks: bool,
    user: dict,
):
    frame_bgr = None
    if source == "rtsp" and camera_id > 0:
        active_site_id = request.session.get("active_site_id") or None
        await asyncio.to_thread(_authorize_detection_camera, camera_id, active_site_id)
        frame_bgr = await asyncio.to_thread(_read_latest_camera_frame, camera_id)
    else:
        if file is None:
            return JSONResponse(
                {"ok": False, "message": "Thiếu ảnh nhận diện."}, status_code=400,
            )
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
            decode_error = dbg_decode.get("error") or "invalid format"
            return JSONResponse(
                {"ok": False, "message": f"Ảnh tải lên không hợp lệ hoặc lỗi giải mã: {decode_error}"},
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

        if source in ("rtsp", "webcam"):
            logger.debug("API Detect (%s): %s person/plate results", source, len(res))
        else:
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


async def reset_tracker(user: dict):
    with _trackers_lock:
        _trackers.clear()
    return {"ok": True, "message": "Tracker cache cleared"}
