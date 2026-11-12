import time
import asyncio
import logging
import threading
import numpy as np
import cv2
import multiprocessing.shared_memory as sm
from fastapi import Request
from fastapi.responses import StreamingResponse

from backend.services import config as cfg
from backend.camera import camera_service as cam_svc

logger = logging.getLogger(__name__)


_stream_viewers = {}
_stream_viewers_lock = threading.Lock()


def _mjpeg_frame(frame, quality=75):
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        logger.error("MJPEG frame encoding failed")
        raise RuntimeError("JPEG encoding failed")
    return b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"


async def stream_camera(camera_id: int, request: Request, user: dict):
    shm_name = f"shm_cam_{camera_id}"

    async def _stream():
        shm = None
        attach_start = time.time()
        max_wait_seconds = 15.0

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
                    return
                if elapsed > 10.0:
                    cam = cam_svc.get_camera(camera_id)
                    if not cam or not cam.get("is_streaming", False):
                        logger.warning("Camera %s: Stream is down / offline in DB", camera_id)
                        return
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

                if counter == 0:
                    elapsed = time.time() - attach_start
                    if elapsed > 15.0:
                        logger.warning("Camera %s: No frames received within 15s, camera stream down", camera_id)
                        break
                    await asyncio.sleep(0.5)
                    continue

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
                            break
                    await asyncio.sleep(0.015)
        except Exception as e:
            logger.warning("Camera %s stream loop error: %s", camera_id, e)
        finally:
            if shm is not None:
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
