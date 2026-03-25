import os
import time
import signal
import logging
import multiprocessing as mp
import ssl
import certifi

# Professional fix for SSL: CERTIFICATE_VERIFY_FAILED on macOS
ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

from dotenv import load_dotenv
load_dotenv()

from backend.core.grabber import grabber_process
from backend.core.inference import inference_process
from backend.core.database import DatabaseManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("orchestrator")

class CameraOrchestrator:
    def __init__(self):
        self.stop_event = mp.Event()
        self.infer_queue = mp.Queue(maxsize=200)
        self.result_queue = mp.Queue(maxsize=200)
        self.db = DatabaseManager()
        self.processes = []
        self.core_processes = []
        self.grabbers = {} # cam_id -> mp.Process mapping

    def reset_streaming_flags(self):
        """Reset all is_streaming flags to False at startup."""
        logger.info("Resetting all camera streaming flags to FALSE for a clean start...")
        try:
            self.db._execute_query("UPDATE cameras SET is_streaming = FALSE")
        except Exception as e:
            logger.error(f"Failed to reset streaming flags: {e}")

    def start(self):
        logger.info("--- AI Core Orchestrator Starting ---")
        self.reset_streaming_flags()
        
        # 1. Spawn Inference Worker (Core Process)
        logger.info("Spawning InferenceWorker process...")
        p_infer = mp.Process(
            target=inference_process,
            args=(self.infer_queue, self.result_queue, self.stop_event),
            name="InferenceWorker"
        )
        p_infer.start()
        self.processes.append(p_infer)
        self.core_processes.append(p_infer)

    def spawn_grabber(self, cam):
        """Start a grabber process for a specific camera."""
        cam_id = str(cam['id'])
        # Use dynamic URL generation for Imou, otherwise fallback to static rtsp_url
        if cam.get('camera_type') == 'imou':
            rtsp_url = self.db.get_imou_rtsp_url(cam['id'])
        else:
            rtsp_url = cam.get('rtsp_url')

        if not rtsp_url:
            logger.warning(f"Camera {cam_id} has no valid RTSP URL. Skipping...")
            return

        # site_ids is a list in the new schema
        site_id = cam['site_ids'][0] if cam.get('site_ids') else None
        shm_name = f"shm_cam_{cam_id}"
        
        logger.info(f"Spawning Grabber for Camera {cam_id} (Site {site_id}): {rtsp_url}")
        
        p_grab = mp.Process(
            target=grabber_process,
            args=(cam_id, rtsp_url, shm_name, self.infer_queue, self.stop_event, site_id),
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
        self.start()
        try:
            while not self.stop_event.is_set():
                # 1. Health check for core processes
                for p in self.core_processes:
                    if not p.is_alive():
                        logger.error(f"Core process {p.name} (PID {p.pid}) has exited unexpectedly!")
                        self.stop_event.set()
                        break
                
                if self.stop_event.is_set():
                    break

                # 2. Sync grabbers with database state
                try:
                    # Fetch cameras that SHOULD be streaming
                    streaming_cams = self.db._execute_query(
                        "SELECT * FROM cameras WHERE is_streaming = TRUE", fetchall=True
                    ) or []
                    streaming_ids = {str(c['id']) for c in streaming_cams}

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
                            
                except Exception as e:
                    logger.error(f"Error during grabber sync: {e}")
                
                # Check for any dead grabbers that should be running and cleanup the mapping
                dead_cids = [cid for cid, p in self.grabbers.items() if not p.is_alive()]
                for cid in dead_cids:
                    logger.warning(f"Grabber for camera {cid} died. It will be restarted in next sync if still marked as streaming.")
                    if cid in self.grabbers:
                        p = self.grabbers[cid]
                        if p in self.processes:
                            self.processes.remove(p)
                        del self.grabbers[cid]

                time.sleep(3) # Sync interval (seconds)
                
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received.")
            self.stop_event.set()
        finally:
            self.cleanup()

    def cleanup(self):
        logger.info("Cleaning up processes...")
        self.stop_event.set()
        for p in self.processes:
            if p.is_alive():
                logger.info(f"Terminating {p.name}...")
                p.terminate()
                p.join(timeout=3)
        logger.info("Orchestrator cleanup complete.")

if __name__ == "__main__":
    # Ensure we use 'spawn' for clear memory separation on Mac/Linux
    mp.set_start_method('spawn', force=True)

    while True:
        try:
            orch = CameraOrchestrator()
            orch.run()
        except Exception:
            logger.exception("Orchestrator encountered a fatal error")
        
        logger.info("Restarting orchestrator in 5 seconds...")
        time.sleep(5)
