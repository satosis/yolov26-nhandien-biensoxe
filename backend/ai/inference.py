import os
import time
import json
import signal
import logging
import ssl
import certifi
import numpy as np
import cv2
import concurrent.futures
import threading
from multiprocessing import Queue, Event, shared_memory
from typing import Optional

from backend.services import config as cfg
from backend.util.image_utils import save_snapshot
from backend.util.texture_utils import check_plate_authenticity

# Submodule imports and re-exports
from backend.ai.types import InferenceResult
from backend.ai.models import (
    PlateDetector,
    OCRReader,
    FaceRecognizer,
)
from backend.ai.plate_analysis import PlateStream, detect_plate_regions, format_plate
from backend.ai.tracking import PersonTrack, SimpleTracker

logger = logging.getLogger("inference")


class InferenceWorker:
    def __init__(self, infer_queue: Queue, recorder_control_queue: Queue, stop_event: Event):
        self.infer_queue = infer_queue
        self.recorder_control_queue = recorder_control_queue
        self._recorder_control_lock = threading.Lock()
        self.stop_event = stop_event

        self._plate_detector = None
        self._ocr = None
        self._face_recog = None

        # Mỗi camera một luồng biển số dùng chung với trang Nhận diện (backend.ai.plate_analysis).
        self._plate_streams: dict[str, dict] = {}
        self._shm_clients: dict[str, shared_memory.SharedMemory] = {}
        self._seen_plates: dict[str, float] = {}

        from backend.iot.door_auto_close import DoorAutoCloseManager
        self._door_manager = DoorAutoCloseManager()
        self._door_manager.reload_config()
        logger.info("DoorAutoCloseManager initialized")
        self._frame_counter = 0
        
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self._pending_tasks: set[concurrent.futures.Future] = set()
        self._last_face_reload = 0
        self._last_active_ts = time.time()
        self._idle_timeout = 300
        self._trackers: dict[str, SimpleTracker] = {}

    def _is_plate_whitelisted(self, plate_norm: str, site_id: Optional[int] = None) -> bool:
        from backend.services.database import db
        if site_id:
            query = "SELECT 1 FROM plate_whitelist WHERE UPPER(REGEXP_REPLACE(plate_number, '[^A-Za-z0-9]', '', 'g')) = %s AND site_ids @> %s::jsonb AND is_active = TRUE LIMIT 1"
            return db.execute_query(query, (plate_norm, json.dumps([site_id])), fetch=True) is not None
        query = "SELECT 1 FROM plate_whitelist WHERE UPPER(REGEXP_REPLACE(plate_number, '[^A-Za-z0-9]', '', 'g')) = %s AND is_active = TRUE LIMIT 1"
        return db.execute_query(query, (plate_norm,), fetch=True) is not None

    def _record_vehicle_entry(self, plate_norm: str, vehicle_type: str, camera_id: str, image_path: str, site_id: Optional[int] = None) -> bool:
        from backend.services.database import db
        active = db.execute_query(
            "SELECT id FROM vehicle_sessions WHERE plate_norm = %s AND status = 'parking' LIMIT 1",
            (plate_norm,),
            fetch=True
        )
        if active:
            saved = db.execute_query("""
                UPDATE vehicle_sessions
                SET entry_time = NOW(), camera_id_in = %s, entry_image = %s, site_id = %s
                WHERE id = %s
            """, (camera_id, image_path, site_id, active["id"]), commit=True)
        else:
            saved = db.execute_query("""
                INSERT INTO vehicle_sessions (plate_norm, vehicle_type, camera_id_in, entry_image, site_id, status)
                VALUES (%s, %s, %s, %s, %s, 'parking')
            """, (plate_norm, vehicle_type, camera_id, image_path, site_id), commit=True)
        if not saved:
            logger.error("Vehicle session write affected no rows")
            raise RuntimeError("Vehicle session was not stored")
        return True

    def _add_pending_plate(self, plate_norm: str, plate_raw: str, camera_id: Optional[str] = None, site_id: Optional[int] = None, image_path: Optional[str] = None, plate_type: str = "white") -> bool:
        from backend.repositories.plate_repo import add_pending
        outcome = add_pending(plate_norm, plate_raw, camera_id, site_id, image_path, plate_type)
        if outcome == "existing":
            logger.info(f"Plate {plate_norm} pending or in 60s cooldown. Skipping.")
            return False
        if outcome is None:
            raise RuntimeError("Pending plate was not stored")
        logger.info(f"Inserted new unknown plate: {plate_norm} (site: {site_id}, cam: {camera_id})")

        from backend.services.telegram_service import notify_telegram
        notify_telegram(
            f"🚘 Biển số mới: {plate_raw} (Cam: {camera_id or '—'})",
            important=True,
            prefix=""
        )

        return True

    def _ensure_models_loaded(self):
        if self._plate_detector is not None:
            return

        logger.info("--- Lazy Loading AI Models (PlateDetector, OCR, FaceRecognizer) ---")
        st = time.time()
        self._plate_detector = PlateDetector()
        self._ocr = OCRReader()
        self._face_recog = FaceRecognizer()
        logger.info("--- AI Models Loaded successfully in %.2fs ---", time.time() - st)

    def _unload_models(self):
        if self._plate_detector is None:
            return
            
        logger.info("--- Idle Timeout: Releasing AI Models to free memory ---")
        self._plate_streams.clear()
        self._plate_detector.close()
        self._plate_detector = None
        self._ocr = None
        self._face_recog = None
        logger.info("--- AI Models Unloaded and resources released. ---")

    def cleanup(self):
        logger.info("InferenceWorker cleaning up...")
        self._executor.shutdown(wait=True)
        self._unload_models()
        for shm in self._shm_clients.values():
            shm.close()
        self._check_async_tasks()
        logger.info("InferenceWorker cleanup complete")

    def _submit_plate_step(self, cam_id: str, site_id, frame, ts: float, detections) -> None:
        """Bước biển số chạy nền, mỗi camera tối đa một job, để OCR chậm không chặn vòng khung hình."""
        state = self._plate_streams.get(cam_id)
        if state is None:
            state = self._plate_streams[cam_id] = {
                "stream": PlateStream(self._plate_detector, self._ocr, "camera", normalize=cfg.normalize_plate),
                "job": None,
            }
        if state["job"] is not None and not state["job"].done():
            return
        state["job"] = self._run_async(self._plate_step, state["stream"], cam_id, site_id, frame, ts, detections)

    def _plate_step(self, stream: PlateStream, cam_id: str, site_id, frame, ts: float, detections) -> None:
        _, active, expired = stream.step(frame, ts, detections=detections)
        for event, final in [(event, False) for event in active] + [(event, True) for event in expired]:
            meta = stream.track_meta.setdefault(event["track_id"], {})
            # Chốt ngay khi track đủ phiếu, không chờ xe rời khung (xe đứng chờ barrier).
            if event["status"] == "recognized" and event["text"] and not meta.get("acted"):
                meta["acted"] = True
                self._on_plate_recognized(event, cam_id, site_id, frame, final=final)
        for event in expired:
            stream.track_meta.pop(event["track_id"], None)

    def _on_plate_recognized(self, event: dict, cam_id: str, site_id, frame, *, final: bool) -> None:
        text = format_plate(event["text"])
        crop = event.get("best_crop")
        plate_type = "white"
        if crop is not None:
            plate_type = self._ocr._ocr.detect_plate_type(crop)
            is_suspicious, susp_conf, reason = check_plate_authenticity(crop)
            if is_suspicious:
                logger.warning("SUSPICIOUS PLATE DETECTED on %s: %s (Reason: %s, Conf: %.2f)", cam_id, text, reason, susp_conf)
        if (time.time() - self._seen_plates.get(text, 0)) <= 15:
            return
        self._seen_plates[text] = time.time()
        if len(self._seen_plates) > 256:
            _cut = time.time() - 60
            self._seen_plates = {k: v for k, v in self._seen_plates.items() if v > _cut}
        # Track đã kết thúc thì khung hiện tại không còn xe: lưu crop biển tốt nhất.
        snapshot_path = save_snapshot(crop if final and crop is not None else frame, cam_id, text)
        self._enqueue_recording_event({
            "cam_id": cam_id,
            "plate_text": text,
            "ts": time.time(),
            "event_type": "plate",
            "site_id": site_id
        })
        normalized = cfg.normalize_plate(text)
        self._record_vehicle_entry(normalized, "car", cam_id, snapshot_path, site_id)
        if not self._is_plate_whitelisted(normalized, site_id):
            logger.info(f"UNKNOWN PLATE DETECTED: {text}. Adding to pending list...")
            self._add_pending_plate(normalized, text, cam_id, site_id, snapshot_path, plate_type)

    def _get_tracker(self, cam_id: str) -> SimpleTracker:
        if cam_id not in self._trackers:
            self._trackers[cam_id] = SimpleTracker(max_distance=200)
        return self._trackers[cam_id]

    def _get_shm_frame(self, shm_name: str):
        if shm_name not in self._shm_clients:
            client = shared_memory.SharedMemory(name=shm_name, create=False)
            try:
                from multiprocessing import resource_tracker
                resource_tracker.unregister(client._name, "shared_memory")
            except Exception:
                pass
            self._shm_clients[shm_name] = client
        shm = self._shm_clients[shm_name]
        counter = np.ndarray((1,), dtype=np.uint32, buffer=shm.buf[0:4])
        arr = np.ndarray((cfg.GRAB_HEIGHT, cfg.GRAB_WIDTH, 3), dtype=np.uint8, buffer=shm.buf[4:])
        for _ in range(3):
            c1 = int(counter[0])
            if c1 == 0 or c1 & 1:
                continue
            frame = arr.copy()
            if int(counter[0]) == c1:
                return frame
        logger.error("Shared frame changed during all read attempts")
        raise RuntimeError(f"Shared frame changed during all read attempts: {shm_name}")

    def _next_latest_token(self) -> dict | None:
        if not self.infer_queue._reader.poll(0.5):
            return None

        latest = self.infer_queue.get()
        while self.infer_queue._reader.poll():
            latest = self.infer_queue.get()
        return latest

    def _enqueue_recording_event(self, event: dict) -> None:
        if self.recorder_control_queue is None:
            logger.error("Recorder control queue is unavailable")
            raise RuntimeError("Recorder control queue is unavailable")
        with self._recorder_control_lock:
            if self.recorder_control_queue.full():
                logger.error("Recorder control queue is full; event was not delivered")
                raise RuntimeError("Recorder control queue is full; event was not delivered")
            self.recorder_control_queue.put_nowait(event)

    def run(self):
        logger.info("Inference worker started")
        reload_trigger = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
        
        def run_loop():
            while not self.stop_event.is_set():
                self._check_async_tasks()
                now = time.time()

                token = self._next_latest_token()
                if token is None:
                    if (now - self._last_active_ts > self._idle_timeout) and self._plate_detector is not None:
                        self._unload_models()

                    continue

                self._last_active_ts = now
                self._ensure_models_loaded()

                if self._frame_counter % 25 == 0 and os.path.exists(reload_trigger) and self._face_recog is not None:
                    mtime = os.path.getmtime(reload_trigger)
                    if mtime > self._last_face_reload:
                        self._face_recog.reload()
                        self._seen_plates.clear()
                        self._last_face_reload = mtime
                        logger.info("InferenceWorker: AI index synchronized via mtime: %s", mtime)
                cam_id, shm_name, ts = token["cam_id"], token["shm_name"], token["ts"]
                site_id = token.get("site_id")
                frame = self._get_shm_frame(shm_name)

                result = InferenceResult(cam_id=cam_id, ts=ts)
                result.annotated_frame = frame.copy()

                # Phát hiện một lần (gộp biển 2 dòng), dùng chung cho người, cửa và luồng biển số.
                detections = detect_plate_regions(self._plate_detector, frame)

                person_dets = [d for d in detections if d[5] == 1]
                other_dets = [d for d in detections if d[5] != 1]
                
                tracker = self._get_tracker(cam_id)
                tracked_persons = tracker.update(person_dets)
                
                for det, track in tracked_persons:
                    x1, y1, x2, y2, det_conf, _ = det
                    
                    display_name = track.identity
                    color = (0, 165, 255) if display_name == "STRANGER" else (0, 255, 0)
                    
                    cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(result.annotated_frame, f"{display_name} {det_conf:.2f}", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                    
                    if det_conf > 0.80 and track.identity == "STRANGER":
                        now_t = time.time()
                        if now_t - track.last_trigger_ts > 1.0:
                            self._enqueue_recording_event({
                                "cam_id": cam_id,
                                "event_text": "Person (Stranger)",
                                "ts": now_t,
                                "event_type": "person",
                                "site_id": site_id
                            })
                            track.last_trigger_ts = now_t
                
                for x1, y1, x2, y2, det_conf, cls_id in other_dets:
                    if cls_id == 0:
                        cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), (255, 0, 255), 2)
                        cv2.putText(result.annotated_frame, "PLATE", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)
                    elif cls_id == 2:
                        cv2.rectangle(result.annotated_frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
                        cv2.putText(result.annotated_frame, "DOOR", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)

                if self._door_manager:
                    self._door_manager.on_frame(
                        camera_id=cam_id,
                        detections=detections,
                        frame_height=frame.shape[0],
                    )

                self._submit_plate_step(cam_id, site_id, frame, ts, detections)

                self._frame_counter += 1
                if self._frame_counter % cfg.FACE_RECOG_EVERY_N == 0:
                    active_tracks = self._get_tracker(cam_id).tracks
                    for track in active_tracks:
                        if not track.is_locked or track.frames_since_last_id > 100:
                            self._run_async(self._async_face_identify, frame, cam_id, site_id, result, track)

        run_loop()

    def _async_face_identify(self, frame, cam_id: str, site_id: int, result: InferenceResult, track: PersonTrack = None):
        if self._face_recog is None:
            return
        person_bbox = track.last_bbox if track else None
        name, sim, f_bbox, emb, direction = self._face_recog.identify(frame, person_bbox=person_bbox)
        result.face_name, result.face_conf = name, sim
        if name:
            if name == "STRANGER":
                if track and track.is_locked:
                    return
                
                from backend.ai.stranger_manager import get_stranger_manager
                mgr = get_stranger_manager()
                stranger_id, match_sim, is_new = mgr.process_stranger(
                    emb=emb, frame=frame, cam_id=cam_id,
                    bbox=f_bbox, direction=direction, site_id=site_id
                )
                result.face_name = f"Người lạ #{stranger_id}"
                if match_sim > 0:
                    result.face_conf = match_sim

                cv2.putText(result.annotated_frame, f"FACE: {result.face_name}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                
                if is_new:
                    self._enqueue_recording_event({
                        "cam_id": cam_id,
                        "event_text": f"New Stranger Detected #{stranger_id}",
                        "ts": time.time(),
                        "event_type": "stranger",
                        "site_id": site_id
                    })
                    from backend.services.telegram_service import notify_telegram
                    notify_telegram(
                        f"👤 Người lạ #{stranger_id} (Cam: {cam_id})",
                        important=True,
                        prefix=""
                    )
            elif name and name != "STRANGER":
                cv2.putText(result.annotated_frame, f"FACE: {name}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                if track:
                    track.identity = name
                    track.confidence = sim
                    track.is_locked = True
                    track.frames_since_last_id = 0
                    logger.info(f"Fixed identity {name} to track {track.track_id} (sim: {sim:.2f})")

    def _check_async_tasks(self):
        completed = {task for task in self._pending_tasks if task.done()}
        self._pending_tasks.difference_update(completed)
        for task in completed:
            task.result()

    def _run_async(self, fn, *args):
        fut = self._executor.submit(fn, *args)
        self._pending_tasks.add(fut)
        return fut

def inference_process(infer_queue: Queue, recorder_control_queue: Queue, stop_event: Event):
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    cfg.setup_logging("inference.log")

    worker = InferenceWorker(infer_queue, recorder_control_queue, stop_event)
    try:
        worker.run()
    finally:
        worker.cleanup()


__all__ = [
    "PlateDetector",
    "OCRReader",
    "FaceRecognizer",
    "inference_process",
]
