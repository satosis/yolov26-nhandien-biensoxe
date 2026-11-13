import time
import asyncio
import logging
import numpy as np
from fastapi import Request, UploadFile
from fastapi.responses import JSONResponse

from backend.util.image_utils import decode_image
from .plate_helpers import (
    _authorize_detection_camera,
    _read_latest_camera_frame,
    clear_streams,
    detect_frame,
)

logger = logging.getLogger(__name__)


def _webcam_debug_logger() -> logging.Logger:
    """Log từng khung webcam ra file riêng data/logs/webcam_debug.log (xoay vòng 5 MB x 3), không lẫn log chung."""
    debug_logger = logging.getLogger("webcam_debug")
    if not debug_logger.handlers:
        import os
        from logging.handlers import RotatingFileHandler
        from backend.services import config as cfg
        handler = RotatingFileHandler(os.path.join(cfg.LOG_DIR, "webcam_debug.log"),
                                      maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        debug_logger.addHandler(handler)
        debug_logger.setLevel(logging.INFO)
        debug_logger.propagate = False
    return debug_logger


def _result_summary(results: list) -> str:
    plates = [f"{r.get('text')}[{r.get('recognition_status')} det={r.get('confidence')} ocr={r.get('ocr_confidence')}"
              f" box={r.get('bbox')}]" for r in results if r.get("type") == "plate"]
    people = sum(1 for r in results if r.get("type") == "person")
    return f"people={people} plates={len(plates)} {' '.join(plates)}".rstrip()


async def detect_image(
    request: Request,
    file: UploadFile | None,
    source: str,
    stream_id: str,
    camera_id: int,
    video_time: float,
    finish_tracks: bool,
    user: dict,
    client_info: str = "",
):
    request_started = time.perf_counter()
    upload_bytes = 0
    decode_ms = 0.0
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
        upload_bytes = len(contents)
        decode_started = time.perf_counter()
        frame_bgr, dbg_decode = await asyncio.to_thread(decode_image, contents, return_debug=True)
        decode_ms = (time.perf_counter() - decode_started) * 1000
        del contents
        if frame_bgr is None:
            logger.warning("Detection upload decode failed: %s", dbg_decode.get("error"))
            if source == "webcam":
                _webcam_debug_logger().info("stream=%s bytes=%d DECODE_FAILED %s client={%s}", stream_id[-12:],
                                            upload_bytes, dbg_decode.get("error"), client_info[:200])
            decode_error = dbg_decode.get("error") or "invalid format"
            return JSONResponse(
                {"ok": False, "message": f"Ảnh tải lên không hợp lệ hoặc lỗi giải mã: {decode_error}"},
                status_code=400,
            )

    def run_ai_pipeline():
        # Mọi tab đi một đường; tab chỉ khác nhau ở nơi lấy khung hình (phía trên) và tham số gửi kèm.
        s_id = request.session.get("active_site_id") or None
        client_host = request.client.host if request.client else "unknown"
        stream_key = (
            str(user.get("id") or "anonymous"),
            str(s_id or 0),
            str(source),
            (stream_id or f"{source}:{client_host}")[:120],
        )
        timestamp = float(video_time) if np.isfinite(video_time) and video_time >= 0 else None
        res = detect_frame(frame_bgr, stream_key, s_id, source, timestamp=timestamp, finish=finish_tracks)

        if source in ("rtsp", "webcam"):
            logger.debug("API Detect (%s): %s person/plate results", source, len(res))
        else:
            logger.info("API Detect (%s): %s person/plate results", source, len(res))
        return res

    started = time.perf_counter()
    try:
        results = await asyncio.to_thread(run_ai_pipeline)
    except Exception:
        if source == "webcam":
            _webcam_debug_logger().exception("stream=%s bytes=%d PIPELINE_ERROR client={%s}",
                                             stream_id[-12:], upload_bytes, client_info[:200])
        raise
    if source == "webcam":
        # Một dòng mỗi khung: kích thước gửi lên, thời gian giải mã / nhận diện / tổng trên máy chủ, số liệu
        # phía điện thoại (client: thời gian khứ hồi của khung trước, mã hóa JPEG, thiết bị) và kết quả.
        now = time.perf_counter()
        _webcam_debug_logger().info(
            "stream=%s ip=%s bytes=%d frame=%dx%d decode=%.0fms detect=%.0fms total=%.0fms client={%s} %s",
            stream_id[-12:], request.client.host if request.client else "?", upload_bytes,
            frame_bgr.shape[1], frame_bgr.shape[0], decode_ms, (now - started) * 1000,
            (now - request_started) * 1000, client_info[:200], _result_summary(results))
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
    clear_streams()
    return {"ok": True, "message": "Tracker cache cleared"}
