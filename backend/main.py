import os
import time
import signal
import logging
import multiprocessing as mp
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

    def start(self):
        logger.info("--- AI Core Orchestrator Starting ---")
        
        # 1. Spawn Inference Worker
        logger.info("Spawning InferenceWorker process...")
        p_infer = mp.Process(
            target=inference_process,
            args=(self.infer_queue, self.result_queue, self.stop_event),
            name="InferenceWorker"
        )
        p_infer.start()
        self.processes.append(p_infer)

        # 2. Spawn Grabbers for active cameras
        try:
            cameras = self.db.get_active_cameras()
            if not cameras:
                logger.warning("No active cameras found in database.")
            
            for cam in cameras:
                cam_id = str(cam['id'])
                rtsp_url = cam['rtsp_url']
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
                self.processes.append(p_grab)
        except Exception as e:
            logger.error(f"Failed to initialize cameras: {e}")

    def run(self):
        self.start()
        try:
            while not self.stop_event.is_set():
                # Check health of child processes
                alive_count = 0
                for p in self.processes:
                    if p.is_alive():
                        alive_count += 1
                    else:
                        logger.error(f"Process {p.name} (PID {p.pid}) has exited unexpectedly!")
                        # If a core process dies, we trigger a full restart
                        self.stop_event.set()
                        break
                
                if self.stop_event.is_set():
                    break
                    
                time.sleep(10) # Health check interval
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
    try:
        mp.set_start_method('spawn', force=True)
    except RuntimeError:
        pass

    while True:
        try:
            orch = CameraOrchestrator()
            orch.run()
        except Exception as e:
            logger.error(f"Orchestrator encountered a fatal error: {e}")
        
        logger.info("Restarting orchestrator in 5 seconds...")
        time.sleep(5)
