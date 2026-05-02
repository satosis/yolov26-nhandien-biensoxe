import os
import time
import logging
import queue
import cv2
from collections import deque
from multiprocessing import Queue, Event
from backend.core import config as cfg
from backend.core.database import DatabaseManager
from backend.core.telegram_service import notify_telegram_video

logger = logging.getLogger("recorder")

# After the last event trigger, keep recording for this many seconds of silence
EVENT_TAIL_SEC = getattr(cfg, 'SMART_RECORD_TAIL_SEC', 10)
# Hard cap so a single recording can't run forever (5 minutes)
MAX_RECORD_SEC = getattr(cfg, 'SMART_RECORD_MAX_SEC', 300)


class RecorderProcess:
    """
    Event-based recorder:
    - Starts on first trigger, dumps pre-event buffer
    - Extends deadline on every new trigger for same camera
    - Stops only after EVENT_TAIL_SEC of silence (no new triggers)
    - Hard cap at MAX_RECORD_SEC to prevent runaway recordings
    """
    def __init__(self, record_queue: Queue, control_queue: Queue, stop_event: Event):
        self.record_queue = record_queue
        self.control_queue = control_queue
        self.stop_event = stop_event
        self.db = DatabaseManager(max_pool_size=5)

        # cam_id -> deque of (ts, jpeg_blob)
        self.buffers = {}
        self.max_buffer_len = int(cfg.GRAB_FPS_CAP * cfg.SMART_RECORD_PRE_SEC)

        # Active recordings: cam_id -> {writer, start_time, deadline_ts, hard_deadline_ts, ...}
        self.active_recordings = {}

    def _get_buffer(self, cam_id: str):
        if cam_id not in self.buffers:
            self.buffers[cam_id] = deque(maxlen=self.max_buffer_len)
        return self.buffers[cam_id]

    def _start_recording(self, cam_id: str, event_text: str, event_type="plate", site_id=None):
        """Dump pre-event buffer and start a new VideoWriter."""
        os.makedirs(cfg.RECORDING_DIR, exist_ok=True)
        timestamp_str = time.strftime('%Y%m%d_%H%M%S')
        filename = f"{event_type}_{cam_id}_{timestamp_str}.mp4"
        path = os.path.join(cfg.RECORDING_DIR, filename)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(path, fourcc, cfg.GRAB_FPS_CAP, (cfg.GRAB_WIDTH, cfg.GRAB_HEIGHT))

        if not writer.isOpened():
            logger.error("Failed to open VideoWriter for path: %s", path)
            return

        # Write pre-event buffer
        buf = self._get_buffer(cam_id)
        frames_written = 0
        thumbnail_path = None

        for i, (f_ts, blob) in enumerate(buf):
            frame = cv2.imdecode(blob, cv2.IMREAD_COLOR)
            if frame is not None:
                writer.write(frame)
                frames_written += 1
                if i == len(buf) // 2:
                    thumb_name = f"thumb_{event_type}_{cam_id}_{timestamp_str}.jpg"
                    t_path = os.path.join(cfg.RECORDING_DIR, thumb_name)
                    cv2.imwrite(t_path, cv2.resize(frame, (320, 180)))
                    thumbnail_path = t_path

        now = time.time()
        self.active_recordings[cam_id] = {
            'writer': writer,
            'start_time': now,
            'deadline_ts': now + EVENT_TAIL_SEC,           # soft deadline — extends on new events
            'hard_deadline_ts': now + MAX_RECORD_SEC,      # hard cap
            'frames_written': frames_written,
            'event_text': event_text,                      # keep first event_text (most meaningful)
            'event_type': event_type,
            'event_types': {event_type},
            'site_id': site_id,
            'path': path,
            'thumbnail_path': thumbnail_path
        }
        logger.info("Started event recording for %s (event: %s) -> %s", cam_id, event_text, path)

    def _extend_recording(self, cam_id: str, event_text: str, event_type: str):
        """Extend an active recording's deadline because a new event arrived."""
        rec = self.active_recordings[cam_id]
        now = time.time()
        new_deadline = min(now + EVENT_TAIL_SEC, rec['hard_deadline_ts'])
        if new_deadline > rec['deadline_ts']:
            rec['deadline_ts'] = new_deadline
        # Merge event type
        rec['event_types'].add(event_type)
        # Keep first event_text (e.g. plate number), only update if first was generic
        if rec['event_text'] in ("Event", "Person (Stranger)") and event_text not in ("Event", "Person (Stranger)"):
            rec['event_text'] = event_text

    def _finish_recording(self, cam_id: str):
        """Finalize and save a recording."""
        rec = self.active_recordings.pop(cam_id)
        rec['writer'].release()
        duration = time.time() - rec['start_time']

        file_size = 0
        try:
            if os.path.exists(rec['path']):
                file_size = os.path.getsize(rec['path'])
        except Exception as size_err:
            logger.error("Failed to get file size for %s: %s", rec['path'], size_err)

        # Combined event type label for mixed events
        event_type = rec['event_type'] if len(rec['event_types']) == 1 else '+'.join(sorted(rec['event_types']))

        try:
            self.db.add_video_recording(
                cam_id=cam_id,
                site_id=rec['site_id'],
                event_type=event_type,
                event_text=rec['event_text'],
                file_path=rec['path'],
                thumbnail_path=rec['thumbnail_path'],
                duration=duration,
                file_size=file_size
            )
        except Exception as db_err:
            logger.error("Failed to save recording to DB: %s", db_err)

        logger.info("Finished recording for %s. Frames: %d, Duration: %.1fs, Events: %s",
                     cam_id, rec['frames_written'], duration, rec['event_types'])

        # Telegram alert for priority events
        if rec['event_types'] & {'stranger', 'plate'}:
            caption = f"Sự kiện: {rec['event_text']}\nCamera: {cam_id}\nThời lượng: {duration:.1f}s"
            try:
                notify_telegram_video(rec['path'], caption, important=True)
            except Exception as tel_err:
                logger.error("Failed to send Telegram video: %s", tel_err)

    def run(self):
        logger.info("Recorder process started (event-based, tail=%ds, max=%ds)",
                     EVENT_TAIL_SEC, MAX_RECORD_SEC)

        while not self.stop_event.is_set():
            # --- 1. Drain ALL pending triggers first (before processing frames) ---
            # This ensures we extend deadlines before checking frame expiry
            while True:
                try:
                    trigger = self.control_queue.get_nowait()
                except queue.Empty:
                    break
                cam_id = trigger.get("cam_id")
                if not cam_id:
                    continue
                event_text = trigger.get("plate_text") or trigger.get("event_text") or "Event"
                event_type = trigger.get("event_type", "plate")
                site_id = trigger.get("site_id")

                if cam_id in self.active_recordings:
                    self._extend_recording(cam_id, event_text, event_type)
                else:
                    self._start_recording(cam_id, event_text, event_type, site_id)

            # --- 2. Process one frame ---
            try:
                item = self.record_queue.get(timeout=0.05)
                cam_id, frame, ts = item["cam_id"], item["frame"], item["ts"]

                # Compress to JPEG for circular buffer
                _, blob = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                self._get_buffer(cam_id).append((ts, blob))

                # Write to active recording if present
                if cam_id in self.active_recordings:
                    rec = self.active_recordings[cam_id]
                    now = time.time()
                    if now <= rec['deadline_ts'] and now <= rec['hard_deadline_ts']:
                        rec['writer'].write(frame)
                        rec['frames_written'] += 1
                    else:
                        self._finish_recording(cam_id)

            except queue.Empty:
                # No frames — still check if any active recordings expired
                now = time.time()
                expired = [cid for cid, r in self.active_recordings.items()
                           if now > r['deadline_ts'] or now > r['hard_deadline_ts']]
                for cid in expired:
                    self._finish_recording(cid)

        # Cleanup on stop — flush all active recordings to DB
        for cam_id in list(self.active_recordings):
            self._finish_recording(cam_id)
        logger.info("Recorder process stopped")


def recorder_worker(record_queue: Queue, control_queue: Queue, stop_event: Event):
    """Entry point for the recorder process."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    recorder = RecorderProcess(record_queue, control_queue, stop_event)
    recorder.run()
