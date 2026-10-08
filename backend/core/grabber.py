"""
parking_hpc/grabber.py
Process 1 — Frame Grabber

Responsibilities:
  - Open RTSP stream(s) with hardware-accelerated decode (FFmpeg/V4L2 backend)
  - Detect motion inside the ROI polygon
  - Write latest frame into SharedMemory (zero-copy IPC)
  - Push (cam_id, shm_offset, timestamp) tokens into infer_queue when motion fires
  - Reconnect automatically on stream loss

Runs as a standalone multiprocessing.Process — no imports from inference.py or ui_server.py.
"""
import os
import time
import logging
import signal
import numpy as np
import cv2
from multiprocessing import Queue, Event, shared_memory
from typing import Optional

from backend.core import config as cfg

logger = logging.getLogger("grabber")

# ── Circuit Breaker Configuration ────────────────────────────────────────────
# Circuit breaker configuration (hardcoded — override via app_settings in DB)
_CB_MAX_FAILS = 5
_CB_OPEN_SECONDS = 120
_CB_BACKOFF_BASE = 5      # seconds, doubles each failure
_CB_BACKOFF_MAX = 300      # cap at 5 minutes


# ── ROI helpers ───────────────────────────────────────────────────────────────

def _build_roi_mask(h: int, w: int, scale: float = 1.0) -> np.ndarray:
    """Return a uint8 mask (255 inside ROI, 0 outside) for the given frame size."""
    scaled_h, scaled_w = int(h * scale), int(w * scale)
    pts = np.array(
        [(int(x * scaled_w), int(y * scaled_h)) for x, y in cfg.ROI_POLYGON_NORM],
        dtype=np.int32,
    )
    mask = np.zeros((scaled_h, scaled_w), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    return mask


def _motion_in_roi(prev_gray: np.ndarray, curr_gray: np.ndarray, mask: np.ndarray) -> bool:
    """Return True if significant motion is detected inside the ROI."""
    diff = cv2.absdiff(prev_gray, curr_gray)
    blurred = cv2.GaussianBlur(diff, (cfg.MOTION_BLUR_KSIZE, cfg.MOTION_BLUR_KSIZE), 0)
    _, thresh = cv2.threshold(blurred, 25, 255, cv2.THRESH_BINARY)
    roi_thresh = cv2.bitwise_and(thresh, thresh, mask=mask)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    dilated = cv2.dilate(roi_thresh, kernel, iterations=cfg.MOTION_DILATE_ITER)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return any(cv2.contourArea(c) > cfg.MOTION_THRESHOLD for c in contours)


# ── Camera reader ─────────────────────────────────────────────────────────────

class CameraReader:
    """
    Reads one RTSP stream, writes frames to SharedMemory, signals motion.

    SharedMemory layout (SHM_FRAME_BYTES bytes):
        [0..3]   : uint32 frame counter (little-endian) — reader checks for new frame
        [4..]    : BGR uint8 flat array (GRAB_HEIGHT × GRAB_WIDTH × 3)
    """

    HEADER_BYTES = 4  # frame counter prefix

    def __init__(
        self,
        cam_id: str,
        rtsp_url: str,
        shm_name: str,
        infer_queue: Queue,
        recorder_queue: Queue,
        stop_event: Event,
        site_id: int = None,
        camera_db_id: int = None,
    ):
        self.cam_id = cam_id
        self.rtsp_url = rtsp_url
        self.shm_name = shm_name
        self.infer_queue = infer_queue
        self.recorder_queue = recorder_queue
        self.stop_event = stop_event
        self.site_id = site_id
        self._camera_db_id = camera_db_id

        # Circuit breaker state
        self._fail_count = 0
        self._cb_state = 'CLOSED'    # CLOSED | OPEN | HALF_OPEN
        self._cb_opened_at = 0.0

        total = self.HEADER_BYTES + cfg.SHM_FRAME_BYTES
        # Clean up stale SHM from previous crash before creating fresh one
        try:
            stale = shared_memory.SharedMemory(name=shm_name, create=False)
            stale.close()
            stale.unlink()
            logger.warning("[%s] Cleaned stale SHM '%s' — InferenceWorker cache may need refresh", cam_id, shm_name)
        except FileNotFoundError:
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
        logger.info("[%s] Grabber started → %s", self.cam_id, self.rtsp_url)
        frame_interval = 1.0 / cfg.GRAB_FPS_CAP
        roi_mask: Optional[np.ndarray] = None
        prev_gray: Optional[np.ndarray] = None
        frame_idx = 0

        try:
            while not self.stop_event.is_set():
                # ── Circuit Breaker Check ────────────────────────────
                if self._cb_state == 'OPEN':
                    elapsed = time.monotonic() - self._cb_opened_at
                    if elapsed < _CB_OPEN_SECONDS:
                        wait_remaining = _CB_OPEN_SECONDS - elapsed
                        logger.warning("[%s] Circuit OPEN — waiting %.0fs",
                                       self.cam_id, wait_remaining)
                        self.stop_event.wait(min(wait_remaining, 30))
                        continue
                    else:
                        self._cb_state = 'HALF_OPEN'
                        logger.info("[%s] Circuit HALF_OPEN — testing", self.cam_id)
                # ─────────────────────────────────────────────────────

                cap = self._open_capture()
                if cap is None:
                    logger.error("[%s] Connection failed. Aborting grabber immediately without retry.", self.cam_id)
                    self._write_health_event('stream_down')
                    if self._camera_db_id:
                        try:
                            db = self._get_db()
                            db.set_camera_streaming(self._camera_db_id, False)
                        except Exception as e:
                            logger.debug("[%s] Failed to reset is_streaming: %s", self.cam_id, e)
                    break

                # ── Connection succeeded ─────────────────────────────
                if self._fail_count > 0 or self._cb_state == 'HALF_OPEN':
                    self._write_health_event('stream_up')
                self._fail_count = 0
                self._cb_state = 'CLOSED'
                # ─────────────────────────────────────────────────────

                logger.info("[%s] Stream opened", self.cam_id)
                t_last = time.monotonic()

                while not self.stop_event.is_set():
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        logger.warning("[%s] Frame read failed — reconnecting", self.cam_id)
                        break

                    t_now = time.monotonic()
                    if t_now < t_last + frame_interval:
                        self.stop_event.wait(max(0, (t_last + frame_interval) - t_now))
                    t_last = time.monotonic()

                    if frame.shape[1] != cfg.GRAB_WIDTH or frame.shape[0] != cfg.GRAB_HEIGHT:
                        frame = cv2.resize(
                            frame, (cfg.GRAB_WIDTH, cfg.GRAB_HEIGHT), interpolation=cv2.INTER_NEAREST
                        )

                    alpha = getattr(cfg, 'DENOISE_TEMPORAL_ALPHA', 1.0)
                    if alpha < 1.0:
                        if hasattr(self, '_prev_f32'):
                            cv2.accumulateWeighted(frame, self._prev_f32, alpha)
                            frame = cv2.convertScaleAbs(self._prev_f32)
                        else:
                            self._prev_f32 = frame.astype(np.float32)

                    np.copyto(self._buf, frame)
                    self._counter[0] = (int(self._counter[0]) + 1) & 0xFFFFFFFF

                    if self.recorder_queue and not self.recorder_queue.full():
                        self.recorder_queue.put_nowait({
                            "cam_id": self.cam_id,
                            "frame": frame.copy(),
                            "ts": time.time()
                        })

                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    if prev_gray is not None and frame_idx % cfg.PLATE_DETECT_EVERY_N == 0:
                        small_gray = cv2.resize(gray, (0, 0), fx=0.25, fy=0.25, interpolation=cv2.INTER_NEAREST)
                        if roi_mask is None:
                            roi_mask = _build_roi_mask(cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, scale=0.25)

                        if _motion_in_roi(prev_gray, small_gray, roi_mask):
                            token = {
                                "cam_id": self.cam_id,
                                "shm_name": self.shm_name,
                                "frame_idx": frame_idx,
                                "ts": time.time(),
                                "site_id": self.site_id,
                            }
                            if not self.infer_queue.full():
                                self.infer_queue.put_nowait(token)
                        prev_gray = small_gray
                    elif prev_gray is None:
                        prev_gray = cv2.resize(gray, (0, 0), fx=0.25, fy=0.25, interpolation=cv2.INTER_NEAREST)
                    frame_idx += 1

                cap.release()
                if not self.stop_event.is_set():
                    logger.info("[%s] Reconnecting in %ds…", self.cam_id, cfg.RTSP_RECONNECT_DELAY)
                    self.stop_event.wait(cfg.RTSP_RECONNECT_DELAY)
        except Exception as e:
            logger.critical("[%s] Grabber CRASHED: %s", self.cam_id, str(e), exc_info=True)
            self._write_health_event('crashed')
            raise

        self._shm.close()
        self._shm.unlink()
        logger.info("[%s] Grabber stopped and SharedMemory unlinked", self.cam_id)

    def _get_db(self):
        """Lazy DatabaseManager — shared pool, created once."""
        if not hasattr(self, '_db_instance'):
            from backend.core.database import DatabaseManager
            self._db_instance = DatabaseManager(max_pool_size=2)
        return self._db_instance

    def _write_health_event(self, event_type: str):
        """Write a health event to camera_status_log + Telegram alert on stream_down."""
        try:
            db = self._get_db()
            db.log_camera_event(
                cam_id=str(self.cam_id),
                event_type=event_type,
                started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception as e:
            logger.debug("[%s] Health event write failed: %s", self.cam_id, e)

        # Telegram alert khi camera offline (stream_down hoặc crashed)
        if event_type in ('stream_down', 'crashed'):
            try:
                from backend.core.telegram_service import notify_telegram
                notify_telegram(
                    f"Camera {self.cam_id} OFFLINE — {event_type} sau {self._fail_count} failures",
                    important=True,
                )
            except Exception:
                pass

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        """Open RTSP with FFmpeg backend + forced TCP transport for stability.
        Re-reads RTSP URL from DB on each attempt to pick up IP changes."""
        # Refresh URL from DB if we have the DB id
        if self._camera_db_id:
            try:
                db = self._get_db()
                fresh_url = db.get_camera_rtsp_url(self._camera_db_id)
                if fresh_url and fresh_url != self.rtsp_url:
                    logger.info("[%s] RTSP URL refreshed from DB", self.cam_id)
                    self.rtsp_url = fresh_url
            except Exception as e:
                logger.debug("[%s] URL refresh failed: %s", self.cam_id, e)

        # Force TCP transport globally for this process
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        
        masked_url = self.rtsp_url
        if "@" in self.rtsp_url:
            parts = self.rtsp_url.split("@")
            prefix = parts[0].split(":")
            if len(prefix) > 2:
                masked_url = f"{prefix[0]}:***@{parts[1]}"
        
        logger.info("[%s] Attempting to open: %s", self.cam_id, masked_url)
        
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        if not cap.isOpened() and not self.rtsp_url.startswith("rtsp://"):
            cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            logger.error("[%s] Connection FAILED to: %s", self.cam_id, masked_url)
            return None

        # Force CPU-only decoding for stability and to avoid GPU context polling
        cap.set(cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_NONE)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, cfg.RTSP_BUFFER_SIZE)
        return cap


# ── Process entry point ───────────────────────────────────────────────────────

def grabber_process(
    cam_id: str,
    rtsp_url: str,
    shm_name: str,
    infer_queue: Queue,
    recorder_queue: Queue,
    stop_event: Event,
    site_id: int = None,
    camera_db_id: int = None,
):
    """Entry point for multiprocessing.Process(target=grabber_process, ...)."""
    # Ignore SIGINT in child — parent handles shutdown via stop_event
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    reader = CameraReader(cam_id, rtsp_url, shm_name, infer_queue, recorder_queue,
                          stop_event, site_id, camera_db_id)
    reader.run()
