import os
import time
import logging
import multiprocessing as mp
from typing import Dict, List, Set

from backend.core.grabber import grabber_process
from backend.core.inference import inference_process
from backend.core.recorder import recorder_worker
from backend.core.database import DatabaseManager
from backend.core import config as cfg
from backend.util.system_utils import log_system_health

logger = logging.getLogger("orchestrator")

class CameraOrchestrator:
    def __init__(self):
        self.stop_event = mp.Event()
        self.infer_queue = mp.Queue(maxsize=100)
        self.result_queue = mp.Queue(maxsize=100)
        self.recorder_queue = mp.Queue(maxsize=500) # Large buffer for history
        self.recorder_control_queue = mp.Queue(maxsize=50)
        self.db = DatabaseManager(max_pool_size=5)
        self.processes = []
        self.core_processes = [] # Tracks Inference worker
        self.grabbers = {} # cam_id -> mp.Process mapping
        self._last_cleanup_ts = 0
        self._last_health_ts = 0
        self.session_stale_cams = set()

    def start_inference(self):
        """Dynamic: Start the heavy inference process ONLY when needed."""
        if self.core_processes:
            return

        logger.info("--- AI Core Orchestrator Starting (Triggered by active camera) ---")
        logger.info("Spawning InferenceWorker and RecorderWorker processes...")
        # Start Inference
        p_infer = mp.Process(
            target=inference_process,
            args=(self.infer_queue, self.result_queue, self.recorder_control_queue, self.stop_event),
            name="InferenceWorker"
        )
        p_infer.start()
        self.core_processes.append(p_infer)

        # Start Recorder
        p_record = mp.Process(
            target=recorder_worker,
            args=(self.recorder_queue, self.recorder_control_queue, self.stop_event),
            name="RecorderWorker"
        )
        p_record.start()
        self.core_processes.append(p_record)

    def stop_inference(self):
        """Dynamic: Stop the heavy inference process when idle."""
        if not self.core_processes:
            return

        logger.info("--- No active cameras. Stopping InferenceWorker to save memory ---")
        for p in self.core_processes:
            if p.is_alive():
                p.terminate()
                p.join(timeout=3)
        self.core_processes = []
        logger.info("InferenceWorker terminated. Orchestrator returned to SILENT IDLE state.")

    def spawn_grabber(self, cam):
        """Start a grabber process for a specific camera."""
        cam_id = str(cam['id'])
        # Always use the database resolver which handles Imou encoding/fallbacks
        rtsp_url = self.db.get_camera_rtsp_url(cam['id'])

        if not rtsp_url:
            logger.warning(f"Camera {cam_id} has no valid RTSP URL. Skipping...")
            return

        # site_ids is a list in the new schema
        site_id = cam['site_ids'][0] if cam.get('site_ids') else None
        # macOS requires SharedMemory names to start with a slash
        shm_name = f"/shm_cam_{cam_id}"
        
        logger.info(f"Spawning Grabber for Camera {cam_id} (Site {site_id}): {rtsp_url}")
        
        p_grab = mp.Process(
            target=grabber_process,
            args=(cam_id, rtsp_url, shm_name, self.infer_queue, self.recorder_queue,
                  self.stop_event, site_id, cam['id']),
            name=f"Grabber_{cam_id}"
        )
        p_grab.start()
        self.grabbers[cam_id] = p_grab
        self.processes.append(p_grab)

    def stop_grabber(self, cam_id):
        """Terminate a grabber process."""
        p = self.grabbers.get(cam_id)
        if p and p.is_alive():
            logger.info(f"Terminating Grabber for Camera {cam_id}...")
            p.terminate()
            p.join(timeout=2)
            if p in self.processes:
                self.processes.remove(p)
        if cam_id in self.grabbers:
            del self.grabbers[cam_id]

    def run(self):
        # We NO LONGER call self.start() here. It will be triggered dynamically.
        # Track what we have already started in THIS session to prevent auto-connect at boot
        self.session_stale_cams = set()
        self._last_cleanup_ts = 0  # Trigger cleanup shortly after boot
        initial_streaming = self.db._execute_query("SELECT id FROM cameras WHERE is_streaming = TRUE", fetchall=True)
        if initial_streaming:
            self.session_stale_cams = {str(c['id']) for c in initial_streaming}
            # Only log if there's something to report
            logger.info(f"Boot: {len(self.session_stale_cams)} camera(s) marked as STALE (will not auto-connect).")

        try:
            while not self.stop_event.is_set():
                # 1. Sync core process health (if running)
                for p in self.core_processes:
                    if not p.is_alive():
                        logger.error(f"Core process {p.name} (PID {p.pid}) has exited unexpectedly!")
                        # Log exit code if possible
                        logger.error(f"Exit code: {p.exitcode}")
                        self.stop_event.set()
                        break
                
                if self.stop_event.is_set():
                    break

                now = time.time()

                # 1.5 System Health Logging (Every 30 minutes)
                if now - self._last_health_ts > 1800:
                    log_system_health()
                    self._last_health_ts = now

                # 2. Fetch cameras that SHOULD be streaming
                streaming_cams = self.db._execute_query(
                    "SELECT * FROM cameras WHERE is_streaming = TRUE", fetchall=True
                ) or []
                streaming_ids = {str(c['id']) for c in streaming_cams}

                # --- DYNAMIC LIFECYCLE MANAGEMENT ---
                # If at least one camera is TRUE, ensure InferenceWorker is running
                if streaming_ids and not self.core_processes:
                     self.start_inference()
                
                # If NO cameras are TRUE, stop InferenceWorker to save memory
                elif not streaming_ids and self.core_processes:
                     self.stop_inference()
                # ------------------------------------

                # Start missing grabbers
                for cam in streaming_cams:
                    cid = str(cam['id'])
                    # Manual start only: bypass auto-start of stale cameras
                    if cid in self.session_stale_cams:
                        continue

                    if cid not in self.grabbers or not self.grabbers[cid].is_alive():
                        self.spawn_grabber(cam)

                # Cleanup stale flag if user manually stopped it
                for cid in list(self.session_stale_cams):
                    if cid not in streaming_ids:
                        self.session_stale_cams.remove(cid)

                # Stop grabbers no longer requested
                active_cid_list = list(self.grabbers.keys())
                for cid in active_cid_list:
                    if cid not in streaming_ids:
                        self.stop_grabber(cid)
                
                # Check for any dead grabbers cleanup
                dead_cids = [cid for cid, p in self.grabbers.items() if not p.is_alive()]
                for cid in dead_cids:
                    if cid in self.grabbers:
                        del self.grabbers[cid]

                # 3. Periodic Cleanup (Recordings retention)
                now = time.time()
                if now - self._last_cleanup_ts > 43200: # Every 12 hours
                    try:
                        days = getattr(cfg, "SMART_RECORD_RETENTION_DAYS", 15)
                        count = self.db.delete_old_recordings(days)
                        if count > 0:
                            logger.info("System Cleanup: Removed %d old recordings (older than %d days).", count, days)
                        self._last_cleanup_ts = now
                    except Exception as e:
                        logger.error("Cleanup task failed: %s", e)

                time.sleep(3) # Sync interval (seconds)
        except Exception as e:
            logger.critical("Orchestrator encountered a FATAL error: %s", str(e), exc_info=True)
            self.stop_event.set()
            raise

    def cleanup(self):
        logger.info("Cleaning up processes...")
        self.stop_event.set()
        for p in self.processes:
            if p.is_alive():
                logger.info(f"Terminating {p.name}...")
                p.terminate()
                p.join(timeout=3)
        logger.info("Orchestrator cleanup complete.")
