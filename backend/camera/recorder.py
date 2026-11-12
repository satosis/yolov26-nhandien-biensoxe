import os
import time
import logging
import cv2
from collections import deque
from multiprocessing import Queue, Event
from backend.services import config as cfg
from backend.services.database import db
from backend.services.telegram_service import notify_telegram_video
from backend.util.image_utils import decode_image

logger = logging.getLogger("recorder")

EVENT_TAIL_SEC = cfg.SMART_RECORD_TAIL_SEC
MAX_RECORD_SEC = cfg.SMART_RECORD_MAX_SEC


class RecorderProcess:
    def __init__(self, record_queue: Queue, control_queue: Queue, stop_event: Event):
        self.record_queue = record_queue
        self.control_queue = control_queue
        self.stop_event = stop_event

        self.buffers = {}
        self.max_buffer_len = int(cfg.GRAB_FPS_CAP * cfg.SMART_RECORD_PRE_SEC)

        self.active_recordings = {}

    def _get_buffer(self, cam_id: str):
        if cam_id not in self.buffers:
            self.buffers[cam_id] = deque(maxlen=self.max_buffer_len)
        return self.buffers[cam_id]

    def _start_recording(self, cam_id: str, event_text: str, event_type="plate", site_id=None):
        os.makedirs(cfg.RECORDING_DIR, exist_ok=True)
        timestamp_str = time.strftime('%Y%m%d_%H%M%S')
        filename = f"{event_type}_{cam_id}_{timestamp_str}.mp4"
        path = os.path.join(cfg.RECORDING_DIR, filename)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(path, fourcc, cfg.GRAB_FPS_CAP, (cfg.GRAB_WIDTH, cfg.GRAB_HEIGHT))

        if not writer.isOpened():
            logger.error("Recorder could not open event recording for camera %s", cam_id)
            writer.release()
            raise OSError(f"Could not open event recording: {path}")

        buf = self._get_buffer(cam_id)
        frames_written = 0
        thumbnail_path = None
        recording_started = False
        try:
            for i, blob in enumerate(buf):
                frame = decode_image(blob)
                if frame is None:
                    logger.error("Recorder prebuffer frame %d could not be decoded for camera %s", i, cam_id)
                    raise ValueError(f"Recorder prebuffer frame could not be decoded for camera {cam_id}")
                writer.write(frame)
                frames_written += 1
                if i == len(buf) // 2:
                    thumb_name = f"thumb_{event_type}_{cam_id}_{timestamp_str}.jpg"
                    t_path = os.path.join(cfg.RECORDING_DIR, thumb_name)
                    if not cv2.imwrite(t_path, cv2.resize(frame, (320, 180))):
                        logger.error("Recorder thumbnail write failed for camera %s", cam_id)
                        raise OSError(f"Could not save event thumbnail: {t_path}")
                    thumbnail_path = t_path

            now = time.time()
            self.active_recordings[cam_id] = {
                'writer': writer,
                'start_time': now,
                'deadline_ts': now + EVENT_TAIL_SEC,
                'hard_deadline_ts': now + MAX_RECORD_SEC,
                'frames_written': frames_written,
                'event_text': event_text,
                'event_type': event_type,
                'event_types': {event_type},
                'site_id': site_id,
                'path': path,
                'thumbnail_path': thumbnail_path
            }
            recording_started = True
        finally:
            if not recording_started:
                writer.release()
        logger.info("Started event recording for %s (event: %s) -> %s", cam_id, event_text, path)

    def _extend_recording(self, cam_id: str, event_text: str, event_type: str):
        rec = self.active_recordings[cam_id]
        now = time.time()
        new_deadline = min(now + EVENT_TAIL_SEC, rec['hard_deadline_ts'])
        if new_deadline > rec['deadline_ts']:
            rec['deadline_ts'] = new_deadline
        rec['event_types'].add(event_type)
        if rec['event_text'] in ("Event", "Person (Stranger)") and event_text not in ("Event", "Person (Stranger)"):
            rec['event_text'] = event_text

    def _finish_recording(self, cam_id: str):
        rec = self.active_recordings.pop(cam_id)
        rec['writer'].release()
        duration = time.time() - rec['start_time']

        file_size = os.path.getsize(rec['path'])

        event_type = rec['event_type'] if len(rec['event_types']) == 1 else '+'.join(sorted(rec['event_types']))

        recording_id = db.insert("video_recordings", {
            "cam_id": str(cam_id),
            "site_id": rec['site_id'],
            "event_type": event_type,
            "event_text": rec['event_text'],
            "file_path": rec['path'],
            "thumbnail_path": rec['thumbnail_path'],
            "duration_seconds": int(duration),
            "file_size_bytes": file_size
        }, returning="id")
        if isinstance(recording_id, bool) or not isinstance(recording_id, int) or recording_id <= 0:
            logger.error("Recorder metadata insert returned no ID for camera %s", cam_id)
            raise RuntimeError("Recording metadata insert did not return a valid ID")

        logger.info("Finished recording for %s. Frames: %d, Duration: %.1fs, Events: %s",
                     cam_id, rec['frames_written'], duration, rec['event_types'])

        if rec['event_types'] & {'stranger', 'plate'}:
            caption = f"Sự kiện: {rec['event_text']}\nCamera: {cam_id}\nThời lượng: {duration:.1f}s"
            notify_telegram_video(rec['path'], caption, important=True)

    def run(self):
        logger.info("Recorder process started (event-based, tail=%ds, max=%ds)",
                     EVENT_TAIL_SEC, MAX_RECORD_SEC)

        while not self.stop_event.is_set():
            while self.control_queue._reader.poll():
                trigger = self.control_queue.get()
                cam_id = trigger["cam_id"]
                event_text = trigger.get("plate_text") or trigger["event_text"]
                event_type = trigger["event_type"]
                site_id = trigger["site_id"]

                if cam_id in self.active_recordings:
                    self._extend_recording(cam_id, event_text, event_type)
                else:
                    self._start_recording(cam_id, event_text, event_type, site_id)

            if self.record_queue._reader.poll(0.05):
                item = self.record_queue.get()
                cam_id, frame = item["cam_id"], item["frame"]

                encoded, blob = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not encoded:
                    logger.error("Recorder JPEG encoding failed for camera %s", cam_id)
                    raise RuntimeError(f"Could not encode recording frame for camera {cam_id}")
                self._get_buffer(cam_id).append(blob)

                if cam_id in self.active_recordings:
                    rec = self.active_recordings[cam_id]
                    now = time.time()
                    if now <= rec['deadline_ts'] and now <= rec['hard_deadline_ts']:
                        rec['writer'].write(frame)
                        rec['frames_written'] += 1
                    else:
                        self._finish_recording(cam_id)

            else:
                now = time.time()
                expired = [cid for cid, r in self.active_recordings.items()
                           if now > r['deadline_ts'] or now > r['hard_deadline_ts']]
                for cid in expired:
                    self._finish_recording(cid)

        for cam_id in list(self.active_recordings):
            self._finish_recording(cam_id)
        logger.info("Recorder process stopped")


def recorder_worker(record_queue: Queue, control_queue: Queue, stop_event: Event):
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    recorder = RecorderProcess(record_queue, control_queue, stop_event)
    recorder.run()
