import time
import logging
import multiprocessing as mp

from backend.camera.grabber import grabber_process
from backend.ai.inference import inference_process
from backend.camera.recorder import recorder_worker
from backend.services.database import db
from backend.util.system_utils import log_system_health
logger = logging.getLogger("orchestrator")

def _reap(p, timeout):
    try:
        if p.is_alive():
            p.terminate()
            p.join(timeout=timeout)
        if p.is_alive():
            logger.warning("Process %s did not stop on SIGTERM — killing", p.name)
            p.kill()
            p.join(timeout=2)
    except Exception as e:
        logger.error("Failed to reap process %s: %s", p.name, e)


class CameraOrchestrator:
    def __init__(self):
        self.stop_event = mp.Event()
        self.infer_queue = mp.Queue(maxsize=10)
        self.recorder_queue = mp.Queue(maxsize=60)
        self.recorder_control_queue = mp.Queue(maxsize=50)
        self.processes = []
        self.core_processes = []
        self.grabbers = {}
        self._last_cleanup_ts = 0
        self._last_health_ts = 0

    def start_inference(self):
        if self.core_processes:
            return

        logger.info("--- AI Core Orchestrator Starting (Triggered by active camera) ---")
        logger.info("Spawning InferenceWorker and RecorderWorker processes...")
        p_infer = mp.Process(
            target=inference_process,
            args=(self.infer_queue, self.recorder_control_queue, self.stop_event),
            name="InferenceWorker"
        )
        p_infer.start()
        self.core_processes.append(p_infer)

        p_record = mp.Process(
            target=recorder_worker,
            args=(self.recorder_queue, self.recorder_control_queue, self.stop_event),
            name="RecorderWorker"
        )
        p_record.start()
        self.core_processes.append(p_record)

    def spawn_grabber(self, cam):
        cam_id = str(cam['id'])
        from backend.camera import camera_service as cam_svc
        rtsp_url = cam_svc.get_camera_rtsp_url(cam['id'])

        if not rtsp_url:
            logger.warning(f"Camera {cam_id} has no valid RTSP URL. Setting is_streaming=False...")
            cam_svc.set_camera_streaming(cam['id'], False)
            return

        site_id = cam['site_ids'][0] if cam.get('site_ids') else None
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
        p = self.grabbers.get(cam_id)
        if p:
            logger.info(f"Terminating Grabber for Camera {cam_id}...")
            _reap(p, 2)
            if p in self.processes:
                self.processes.remove(p)
        if cam_id in self.grabbers:
            del self.grabbers[cam_id]

    def run(self):
        try:
            db._execute_query(
                "UPDATE cameras SET is_streaming = TRUE WHERE is_active = TRUE"
            )
            logger.info("Auto-started all active cameras for instant stream.")

            while not self.stop_event.is_set():
                for p in self.core_processes:
                    if not p.is_alive():
                        logger.error("Core process %s (PID %s) exited with code %s", p.name, p.pid, p.exitcode)
                        self.stop_event.set()
                        break
                
                if self.stop_event.is_set():
                    break

                now = time.time()

                if now - self._last_health_ts > 1800:
                    self._last_health_ts = now
                    log_system_health()

                streaming_cams = db._execute_query(
                    "SELECT * FROM cameras WHERE is_active = TRUE", fetchall=True
                )
                streaming_ids = {str(c['id']) for c in streaming_cams}

                if not self.core_processes:
                     self.start_inference()

                for cam in streaming_cams:
                    cid = str(cam['id'])
                    if cid not in self.grabbers or not self.grabbers[cid].is_alive():
                        self.spawn_grabber(cam)

                active_cid_list = list(self.grabbers.keys())
                for cid in active_cid_list:
                    if cid not in streaming_ids:
                        self.stop_grabber(cid)
                
                dead_cids = [cid for cid, p in self.grabbers.items() if not p.is_alive()]
                for cid in dead_cids:
                    if cid in self.grabbers:
                        del self.grabbers[cid]

                if now - self._last_cleanup_ts > 43200:
                    self._last_cleanup_ts = now
                    try:
                        from backend.services.retention_manager import RetentionManager
                        RetentionManager().run()
                    except Exception as e:
                        logger.error("Retention task failed: %s", e)

                time.sleep(1)
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
        for q in (self.infer_queue, self.recorder_queue, self.recorder_control_queue):
            try:
                q.cancel_join_thread()
                q.close()
            except Exception:
                logger.exception("Queue cleanup failed")
        logger.info("Orchestrator cleanup complete.")
