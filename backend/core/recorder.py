import os
import time
import logging
import queue
import cv2
import numpy as np
from collections import deque
from multiprocessing import Process, Queue, Event
from backend.core import config as cfg

logger = logging.getLogger("recorder")

class RecorderProcess:
    """
    Background process for smart recording with circular history buffer.
    """
    def __init__(self, record_queue: Queue, control_queue: Queue, stop_event: Event):
        self.record_queue = record_queue  # (cam_id, frame_bgr, ts)
        self.control_queue = control_queue # (cam_id, plate_text, ts)
        self.stop_event = stop_event
        
        # cam_id -> deque of (ts, jpeg_blob)
        self.buffers = {}
        self.max_buffer_len = int(cfg.GRAB_FPS_CAP * cfg.SMART_RECORD_PRE_SEC)
        
        # Current active recordings: cam_id -> {writer, end_ts, frames_written, plate_text}
        self.active_recordings = {}

    def _get_buffer(self, cam_id: str):
        if cam_id not in self.buffers:
            self.buffers[cam_id] = deque(maxlen=self.max_buffer_len)
        return self.buffers[cam_id]

    def _start_recording(self, cam_id: str, plate_text: str, ts: float):
        """Dump buffer and start a new VideoWriter."""
        os.makedirs(cfg.RECORDING_DIR, exist_ok=True)
        filename = f"{cam_id}_{plate_text}_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
        path = os.path.join(cfg.RECORDING_DIR, filename)
        
        # Use XVID for broad compatibility
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(path, fourcc, cfg.GRAB_FPS_CAP, (cfg.GRAB_WIDTH, cfg.GRAB_HEIGHT))
        
        if not writer.isOpened():
            logger.error("Failed to open VideoWriter for path: %s", path)
            return

        # 1. Write the history buffer
        buf = self._get_buffer(cam_id)
        frames_written = 0
        for _, blob in buf:
            frame = cv2.imdecode(blob, cv2.IMREAD_COLOR)
            if frame is not None:
                writer.write(frame)
                frames_written += 1
        
        # 2. Set active recording state
        end_ts = ts + cfg.SMART_RECORD_POST_SEC
        self.active_recordings[cam_id] = {
            'writer': writer,
            'end_ts': end_ts,
            'frames_written': frames_written,
            'plate_text': plate_text,
            'path': path
        }
        logger.info("Started smart recording for %s (plate: %s) -> %s", cam_id, plate_text, path)

    def run(self):
        logger.info("Recorder process started")
        
        while not self.stop_event.is_set():
            # 1. Check for incoming frames to buffer
            try:
                # Use a small timeout to keep the loop responsive
                item = self.record_queue.get(timeout=0.01)
                cam_id, frame, ts = item["cam_id"], item["frame"], item["ts"]
                
                # Compress to JPEG to save RAM in buffer
                _, blob = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                self._get_buffer(cam_id).append((ts, blob))
                
                # If currently recording this cam, write the fresh frame
                if cam_id in self.active_recordings:
                    rec = self.active_recordings[cam_id]
                    if ts <= rec['end_ts']:
                        rec['writer'].write(frame)
                        rec['frames_written'] += 1
                    else:
                        # Recording finished
                        rec['writer'].release()
                        logger.info("Finished smart recording for %s. Total frames: %d", cam_id, rec['frames_written'])
                        del self.active_recordings[cam_id]
                        
            except queue.Empty:
                pass
            
            # 2. Check for triggers
            try:
                trigger = self.control_queue.get_nowait()
                cam_id, plate_text, ts = trigger["cam_id"], trigger["plate_text"], trigger["ts"]
                
                # Only start if not already recording this cam (throttle)
                if cam_id not in self.active_recordings:
                    self._start_recording(cam_id, plate_text, ts)
            except queue.Empty:
                pass

        # Cleanup
        for rec in self.active_recordings.values():
            rec['writer'].release()
        logger.info("Recorder process stopped")

def recorder_worker(record_queue: Queue, control_queue: Queue, stop_event: Event):
    """Entry point for the recorder process."""
    # Setup signal handling to ignore SIGINT, parent handles it via stop_event
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    
    recorder = RecorderProcess(record_queue, control_queue, stop_event)
    recorder.run()
