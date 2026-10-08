import time
import logging
import multiprocessing as mp

from backend.core.grabber import grabber_process
from backend.core.inference import inference_process
from backend.core.recorder import recorder_worker
from backend.core.database import DatabaseManager
from backend.util.system_utils import log_system_health

logger = logging.getLogger("orchestrator")


def _reap(p, timeout):
    """Terminate a process and guarantee it is reaped.
    If SIGTERM + join(timeout) leaves it alive (e.g. stuck in a native cv2/ONNX
    call), escalate to kill() so we never drop a still-running handle (zombie /
    leaked SHM / leaked DB connections)."""
    if not p:
        return
    try:
        if p.is_alive():
            p.terminate()
            p.join(timeout=timeout)
        if p.is_alive():
            logger.warning("Process %s did not stop on SIGTERM — killing", getattr(p, "name", p))
            p.kill()
            p.join(timeout=2)
    except Exception as e:
        logger.error("Failed to reap process %s: %s", getattr(p, "name", p), e)


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
            _reap(p, 3)
        self.core_processes = []
        logger.info("InferenceWorker terminated. Orchestrator returned to SILENT IDLE state.")

    def spawn_grabber(self, cam):
        """Start a grabber process for a specific camera."""
        cam_id = str(cam['id'])
        # Always use the database resolver which handles Imou encoding/fallbacks
        rtsp_url = self.db.get_camera_rtsp_url(cam['id'])

        if not rtsp_url:
            logger.warning(f"Camera {cam_id} has no valid RTSP URL. Setting is_streaming=False...")
            self.db.set_camera_streaming(cam['id'], False)
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
        if p:
            logger.info(f"Terminating Grabber for Camera {cam_id}...")
            _reap(p, 2)
            if p in self.processes:
                self.processes.remove(p)
        if cam_id in self.grabbers:
            del self.grabbers[cam_id]

    def run(self):
        # Always-on mode: auto-start grabbers for all active cameras at startup
        # so that SHM is pre-populated — clicking the stream button is instant.
        self.db._execute_query(
            "UPDATE cameras SET is_streaming = TRUE WHERE is_active = TRUE"
        )
        logger.info("Auto-started all active cameras for instant stream.")

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

                # 2. Fetch all active cameras — grabbers always run
                streaming_cams = self.db._execute_query(
                    "SELECT * FROM cameras WHERE is_active = TRUE", fetchall=True
                ) or []
                streaming_ids = {str(c['id']) for c in streaming_cams}

                # --- INFERENCE ALWAYS ON ---
                # Ensure InferenceWorker is running at all times
                if not self.core_processes:
                     self.start_inference()
                # ---------------------------

                # Start missing grabbers
                for cam in streaming_cams:
                    cid = str(cam['id'])
                    if cid not in self.grabbers or not self.grabbers[cid].is_alive():
                        self.spawn_grabber(cam)

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

                # 3. Periodic data retention — recordings, stranger logs, health
                #    logs + their files; legal_hold rows are preserved.
                if now - self._last_cleanup_ts > 43200: # Every 12 hours
                    try:
                        from backend.core.retention_manager import RetentionManager
                        RetentionManager(self.db).run()
                        self._last_cleanup_ts = now
                    except Exception as e:
                        logger.error("Retention task failed: %s", e)

                time.sleep(1) # Sync interval (seconds)
        except Exception as e:
            logger.critical("Orchestrator encountered a FATAL error: %s", str(e), exc_info=True)
            self.stop_event.set()
            raise

    def cleanup(self):
        logger.info("Cleaning up processes...")
        self.stop_event.set()
        for p in self.processes + self.core_processes:
            logger.info(f"Terminating {p.name}...")
            _reap(p, 3)
        # Close queues so background feeder threads don't block process exit.
        for q in (self.infer_queue, self.result_queue, self.recorder_queue, self.recorder_control_queue):
            try:
                q.cancel_join_thread()
                q.close()
            except Exception:
                pass
        logger.info("Orchestrator cleanup complete.")
