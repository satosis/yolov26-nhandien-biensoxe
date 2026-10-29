import os
import time
import json
import re
import uuid
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
from backend.util.bbox_utils import merge_plate_boxes

# Submodule imports and re-exports
from backend.ai.types import InferenceResult
from backend.ai.models import (
    PlateDetector,
    OCRReader,
    FaceRecognizer,
    validate_plate_crop,
    enhance_plate,
    _face_embedding,
    _face_bbox_in_frame,
    _patch_e2e_loss,
    _onnx_runtime_target,
    _plate_ort_session_options,
)
from backend.ai.tracking import PlateVoter, PersonTrack, SimpleTracker

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

        self._voters: dict[str, PlateVoter] = {}
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
        from backend.services.database import db
        clean_plate = re.sub(r'[^A-Z0-9]', '', plate_norm.upper())
        existing = db.execute_query("""
            SELECT pending_id FROM pending_plates
            WHERE regexp_replace(plate_norm, '[^a-zA-Z0-9]', '', 'g') = %s
            AND (LOWER(TRIM(status)) = 'pending'
                 OR confirmed_at_utc > NOW() - INTERVAL '60 seconds')
            LIMIT 1
        """, (clean_plate,), fetch=True)
        if existing:
            logger.info(f"Plate {plate_norm} pending or in 60s cooldown (match: {clean_plate}). Skipping.")
            return False

        pid = str(uuid.uuid4())[:8]
        logger.info(f"Inserting new unknown plate: {plate_norm} (site: {site_id}, cam: {camera_id})")
        saved = db.execute_query("""
            INSERT INTO pending_plates (pending_id, plate_raw, plate_norm, first_seen_utc, status, site_id, image_path, plate_type)
            VALUES (%s, %s, %s, NOW(), 'pending', %s, %s, %s)
            ON CONFLICT (pending_id) DO NOTHING
        """, (pid, plate_raw, plate_norm, site_id, image_path, plate_type), commit=True)
        if not saved:
            logger.error("Pending plate write affected no rows")
            raise RuntimeError("Pending plate was not stored")

        try:
            from backend.services.telegram_service import notify_telegram
            notify_telegram(
                f"🚘 Biển số mới: {plate_raw} (Cam: {camera_id or '—'})",
                important=True,
                prefix=""
            )
        except Exception as e:
            logger.warning("Could not send Telegram alert for pending plate: %s", e)

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

    def _get_voter(self, cam_id: str) -> PlateVoter:
        if cam_id not in self._voters:
            self._voters[cam_id] = PlateVoter(buffer_size=cfg.FRAME_BUFFER_SIZE)
        return self._voters[cam_id]

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

                detections = self._plate_detector.detect(frame)
                detections = merge_plate_boxes(detections)
                
                person_dets = [d for d in detections if d[5] == 1]
                other_dets = [d for d in detections if d[5] != 1]
                
                tracker = self._get_tracker(cam_id)
                tracked_persons = tracker.update(person_dets)
                
                voter = self._get_voter(cam_id)
                
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
                        h_frame, w_frame = frame.shape[:2]
                        px = int((x2 - x1) * 0.15) + 10
                        py = int((y2 - y1) * 0.15) + 5
                        px1, py1 = max(0, x1 - px), max(0, y1 - py)
                        px2, py2 = min(w_frame, x2 + px), min(h_frame, y2 + py)
                        
                        crop = frame[py1:py2, px1:px2].copy()
                        if crop.size > 0:
                            self._run_async(self._async_ocr, crop, voter, cam_id, result)
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

                if voter.is_ready():
                    best_text, best_conf = voter.best()
                    voter.reset()
                    if best_text:
                        result.plate_text = best_text
                        result.plate_conf = best_conf
                        if (time.time() - self._seen_plates.get(best_text, 0)) > 15:
                            self._seen_plates[best_text] = time.time()
                            if len(self._seen_plates) > 256:
                                _cut = time.time() - 60
                                self._seen_plates = {k: v for k, v in self._seen_plates.items() if v > _cut}
                            result.snapshot_path = save_snapshot(frame, cam_id, best_text)
                            
                            self._enqueue_recording_event({
                                "cam_id": cam_id,
                                "plate_text": best_text,
                                "ts": time.time(),
                                "event_type": "plate",
                                "site_id": site_id
                            })

                            normalized = cfg.normalize_plate(best_text)
                            self._run_async(self._record_vehicle_entry, normalized, "car", cam_id, result.snapshot_path, site_id)
                            if not self._is_plate_whitelisted(normalized, site_id):
                                logger.info(f"UNKNOWN PLATE DETECTED: {best_text}. Adding to pending list...")
                                self._run_async(self._add_pending_plate, normalized, best_text, cam_id, site_id, result.snapshot_path, result.plate_type)

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
                    try:
                        from backend.services.telegram_service import notify_telegram
                        notify_telegram(
                            f"👤 Người lạ #{stranger_id} (Cam: {cam_id})",
                            important=True,
                            prefix=""
                        )
                    except Exception as e:
                        logger.warning("Could not send Telegram alert for stranger: %s", e)
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

    def _async_ocr(self, crop, voter: PlateVoter, cam_id: str, result: InferenceResult):
        if self._ocr is None:
            return
        if not validate_plate_crop(crop):
            logger.debug("Crop rejected by plate validation (cam: %s)", cam_id)
            return
        text, ocr_conf, p_type = self._ocr.read(crop)
        if text:
            voter.add(text, ocr_conf)
            result.plate_type = p_type

            is_suspicious, susp_conf, reason = check_plate_authenticity(crop)
            if is_suspicious:
                logger.warning("SUSPICIOUS PLATE DETECTED on %s: %s (Reason: %s, Conf: %.2f)", cam_id, text, reason, susp_conf)


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
    "InferenceResult",
    "PlateDetector",
    "OCRReader",
    "FaceRecognizer",
    "validate_plate_crop",
    "enhance_plate",
    "PlateVoter",
    "PersonTrack",
    "SimpleTracker",
    "InferenceWorker",
    "inference_process",
    "_face_embedding",
    "_face_bbox_in_frame",
    "_patch_e2e_loss",
    "_onnx_runtime_target",
    "_plate_ort_session_options",
]
