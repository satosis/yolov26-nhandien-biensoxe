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
from backend.util.network_utils import update_camera_ips_by_discovery

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
        self.core_processes = [] # Tracks Inference worker
        self.grabbers = {} # cam_id -> mp.Process mapping

    def start_inference(self):
        """Dynamic: Start the heavy inference process ONLY when needed."""
        if self.core_processes:
            return

        logger.info("--- AI Core Orchestrator Starting (Triggered by active camera) ---")
        logger.info("Spawning InferenceWorker process...")
        p_infer = mp.Process(
            target=inference_process,
            args=(self.infer_queue, self.result_queue, self.stop_event),
            name="InferenceWorker"
        )
        p_infer.start()
        self.core_processes.append(p_infer)

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
        # We NO LONGER call self.start() here. It will be triggered dynamically.
        # Track what we have already started in THIS session to prevent auto-connect at boot
        self.session_stale_cams = set()
        initial_streaming = self.db._execute_query("SELECT id FROM cameras WHERE is_streaming = TRUE", fetchall=True)
        if initial_streaming:
            self.session_stale_cams = {str(c['id']) for c in initial_streaming}
            # Only log if there's something to report
            logger.info(f"Boot: {len(self.session_stale_cams)} camera(s) marked as STALE (will not auto-connect).")

        while not self.stop_event.is_set():
            # 1. Sync core process health (if running)
            for p in self.core_processes:
                if not p.is_alive():
                    logger.error(f"Core process {p.name} (PID {p.pid}) has exited unexpectedly!")
                    self.stop_event.set()
                    break
            
            if self.stop_event.is_set():
                break

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

            time.sleep(3) # Sync interval (seconds)
        
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

    logger.info("================================================")
    logger.info("   Backend Orchestrator is starting...         ")
    logger.info("   Connect to UI at: http://localhost:8000    ")
    logger.info("================================================")

    while True:
        orch = CameraOrchestrator()
        orch.run()
        
        logger.info("Restarting orchestrator in 5 seconds...")
        time.sleep(5)
