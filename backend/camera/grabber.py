import os
import time
import logging
import signal
import numpy as np
import cv2
from multiprocessing import Queue, Event, shared_memory
from typing import Optional

from backend.services import config as cfg
from backend.services.database import db

logger = logging.getLogger("grabber")
MAX_RTSP_READ_FAILURES = 3
STABLE_STREAM_SECONDS = 10


def _build_roi_mask(h: int, w: int, scale: float = 1.0) -> np.ndarray:
    scaled_h, scaled_w = int(h * scale), int(w * scale)
    pts = np.array(
        [(int(x * scaled_w), int(y * scaled_h)) for x, y in cfg.ROI_POLYGON_NORM],
        dtype=np.int32,
    )
    mask = np.zeros((scaled_h, scaled_w), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    return mask


def _motion_in_roi(prev_gray: np.ndarray, curr_gray: np.ndarray, mask: np.ndarray) -> bool:
    diff = cv2.absdiff(prev_gray, curr_gray)
    blurred = cv2.GaussianBlur(diff, (cfg.MOTION_BLUR_KSIZE, cfg.MOTION_BLUR_KSIZE), 0)
    _, thresh = cv2.threshold(blurred, 25, 255, cv2.THRESH_BINARY)
    roi_thresh = cv2.bitwise_and(thresh, thresh, mask=mask)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    dilated = cv2.dilate(roi_thresh, kernel, iterations=cfg.MOTION_DILATE_ITER)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return any(cv2.contourArea(c) > cfg.MOTION_THRESHOLD for c in contours)


def _enqueue_latest(queue_: Queue, item: dict, producer_lock) -> bool:
    """Drop new tokens at capacity; the consumer collapses queued stale tokens."""
    with producer_lock:
        if queue_.full():
            return False
        queue_.put_nowait(item)
        return True


def _enqueue_recording_frame(queue_: Queue, cam_id: str, frame: np.ndarray, producer_lock) -> bool:
    with producer_lock:
        if queue_.full():
            return False
        queue_.put_nowait({"cam_id": cam_id, "frame": frame.copy()})
        return True


class CameraReader:

    HEADER_BYTES = 4

    def __init__(
        self,
        cam_id: str,
        rtsp_url: str,
        shm_name: str,
        infer_queue: Queue,
        recorder_queue: Queue,
        stop_event: Event,
        infer_queue_lock,
        recorder_queue_lock,
        site_id: int = None,
        camera_db_id: int = None,
    ):
        self.cam_id = cam_id
        self.rtsp_url = rtsp_url
        self.shm_name = shm_name
        self.infer_queue = infer_queue
        self.infer_queue_lock = infer_queue_lock
        self.recorder_queue = recorder_queue
        self.recorder_queue_lock = recorder_queue_lock
        self.stop_event = stop_event
        self.site_id = site_id
        self._camera_db_id = camera_db_id

        total = self.HEADER_BYTES + cfg.SHM_FRAME_BYTES
        try:
            self._shm = shared_memory.SharedMemory(name=shm_name, create=True, size=total)
        except FileExistsError:
            try:
                self._shm = shared_memory.SharedMemory(name=shm_name)
            except Exception:
                try:
                    old_shm = shared_memory.SharedMemory(name=shm_name)
                    old_shm.close()
                    old_shm.unlink()
                except Exception:
                    pass
                self._shm = shared_memory.SharedMemory(name=shm_name, create=True, size=total)

        self._buf = np.ndarray(
            (cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, 3),
            dtype=np.uint8,
            buffer=self._shm.buf[self.HEADER_BYTES:],
        )
        self._counter = np.ndarray((1,), dtype=np.uint32, buffer=self._shm.buf[:self.HEADER_BYTES])
        self._counter[0] = 0

    def run(self):
        logger.info("[%s] Grabber started", self.cam_id)
        frame_interval = 1.0 / cfg.GRAB_FPS_CAP
        roi_mask: Optional[np.ndarray] = None
        prev_gray: Optional[np.ndarray] = None
        frame_idx = 0
        cap = None
        read_failures = 0

        try:
            while not self.stop_event.is_set():
                cap = self._open_capture()
                if cap is None:
                    self._mark_stream_down()
                    break

                logger.info("[%s] Stream opened", self.cam_id)
                self._write_health_event('stream_up')
                t_last = time.monotonic()
                read_failed = False
                stable_frames = 0

                while not self.stop_event.is_set():
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        read_failed = True
                        break
                    stable_frames += 1
                    if stable_frames >= max(1, int(cfg.GRAB_FPS_CAP * STABLE_STREAM_SECONDS)):
                        read_failures = 0

                    t_now = time.monotonic()
                    if t_now < t_last + frame_interval:
                        self.stop_event.wait(max(0, (t_last + frame_interval) - t_now))
                    t_last = time.monotonic()

                    if frame.shape[1] != cfg.GRAB_WIDTH or frame.shape[0] != cfg.GRAB_HEIGHT:
                        frame = cv2.resize(
                            frame, (cfg.GRAB_WIDTH, cfg.GRAB_HEIGHT), interpolation=cv2.INTER_NEAREST
                        )

                    alpha = cfg.DENOISE_TEMPORAL_ALPHA
                    if alpha < 1.0:
                        if hasattr(self, '_prev_f32'):
                            cv2.accumulateWeighted(frame, self._prev_f32, alpha)
                            frame = cv2.convertScaleAbs(self._prev_f32)
                        else:
                            self._prev_f32 = frame.astype(np.float32)

                    np.copyto(self._buf, frame)
                    self._counter[0] = (int(self._counter[0]) + 1) & 0xFFFFFFFF

                    if self.recorder_queue:
                        _enqueue_recording_frame(
                            self.recorder_queue, self.cam_id, frame, self.recorder_queue_lock
                        )

                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    if prev_gray is not None and frame_idx % cfg.PLATE_DETECT_EVERY_N == 0:
                        small_gray = cv2.resize(gray, (0, 0), fx=0.25, fy=0.25, interpolation=cv2.INTER_NEAREST)
                        if roi_mask is None:
                            roi_mask = _build_roi_mask(cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, scale=0.25)

                        if _motion_in_roi(prev_gray, small_gray, roi_mask):
                            token = {
                                "cam_id": self.cam_id,
                                "shm_name": self.shm_name,
                                "ts": time.time(),
                                "site_id": self.site_id,
                            }
                            _enqueue_latest(self.infer_queue, token, self.infer_queue_lock)

                        prev_gray = small_gray
                    elif prev_gray is None:
                        prev_gray = cv2.resize(gray, (0, 0), fx=0.25, fy=0.25, interpolation=cv2.INTER_NEAREST)
                    frame_idx += 1

                cap.release()
                cap = None
                if read_failed and not self.stop_event.is_set():
                    read_failures += 1
                    if read_failures >= MAX_RTSP_READ_FAILURES:
                        logger.error("[%s] Frame read failed %d times", self.cam_id, read_failures)
                        self._mark_stream_down()
                        break
                    logger.debug("[%s] Frame read retry %d/%d in %ds",
                                 self.cam_id, read_failures, MAX_RTSP_READ_FAILURES - 1, cfg.RTSP_RECONNECT_DELAY)
                    self.stop_event.wait(cfg.RTSP_RECONNECT_DELAY)
        finally:
            if cap is not None:
                cap.release()
            self._shm.close()
            self._shm.unlink()
            logger.info("[%s] Grabber stopped and SharedMemory unlinked", self.cam_id)

    def _mark_stream_down(self):
        if self._camera_db_id:
            from backend.camera.camera_service import set_camera_streaming
            if not set_camera_streaming(self._camera_db_id, False):
                logger.error("[%s] Camera streaming state update affected no row", self.cam_id)
                raise RuntimeError(f"Camera {self.cam_id} streaming state could not be disabled")
        self._write_health_event('stream_down')

    def _write_health_event(self, event_type: str):
        saved = db.insert("camera_status_log", {
            "camera_id": str(self.cam_id),
            "status": event_type,
            "event_type": event_type,
            "logged_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if not saved:
            logger.error("[%s] Camera health event insert returned no row (%s)", self.cam_id, event_type)
            raise RuntimeError(f"Camera {self.cam_id} health event could not be stored")

        if event_type in ('stream_down', 'crashed'):
            from backend.services.telegram_service import notify_telegram
            notify_telegram(
                f"Camera {self.cam_id} OFFLINE — {event_type}",
                important=True,
            )

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        if self._camera_db_id:
            from backend.camera.camera_service import get_camera_rtsp_url
            fresh_url = get_camera_rtsp_url(self._camera_db_id)
            if not fresh_url:
                logger.error("[%s] Camera has no valid RTSP URL in DB", self.cam_id)
                return None
            if fresh_url != self.rtsp_url:
                logger.info("[%s] RTSP URL refreshed from DB", self.cam_id)
                self.rtsp_url = fresh_url

        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        
        logger.info("[%s] Attempting to open camera stream", self.cam_id)
        
        params = []
        if hasattr(cv2, 'CAP_PROP_OPEN_TIMEOUT_MSEC'):
            params.extend([cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000])
        if hasattr(cv2, 'CAP_PROP_READ_TIMEOUT_MSEC'):
            params.extend([cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000])

        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG, params) if params else cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        if not cap.isOpened() and not self.rtsp_url.startswith("rtsp://"):
            cap.release()
            cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            cap.release()
            logger.error("[%s] Camera stream could not be opened", self.cam_id)
            return None

        cap.set(cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_NONE)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, cfg.RTSP_BUFFER_SIZE)
        return cap



def grabber_process(
    cam_id: str,
    rtsp_url: str,
    shm_name: str,
    infer_queue: Queue,
    recorder_queue: Queue,
    stop_event: Event,
    infer_queue_lock,
    recorder_queue_lock,
    site_id: int = None,
    camera_db_id: int = None,
):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    cfg.setup_logging("grabber.log")

    reader = CameraReader(cam_id, rtsp_url, shm_name, infer_queue, recorder_queue,
                          stop_event, infer_queue_lock, recorder_queue_lock, site_id, camera_db_id)
    reader.run()
