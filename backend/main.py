import sys
import os

# Add project root to Python path (works on Mac, Linux, Windows)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import logging
import multiprocessing as mp
import ssl
import certifi

# Professional fix for SSL: CERTIFICATE_VERIFY_FAILED on macOS
ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

from dotenv import load_dotenv
load_dotenv()

from backend.services import config as cfg
from backend.services.orchestrator import CameraOrchestrator

# Configure logging (console + data/logs/backend.log)
cfg.setup_logging("backend.log")
logger = logging.getLogger("main")

if __name__ == "__main__":
    # Ensure we use 'spawn' for clear memory separation on Mac/Linux
    mp.set_start_method('spawn', force=True)

    logger.info("================================================")
    logger.info("   Backend Orchestrator is starting...         ")
    logger.info("   Connect to UI at: http://localhost:8000    ")
    logger.info("================================================")

    while True:
        orch = CameraOrchestrator()
        try:
            orch.run()
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received — shutting down immediately...")
            orch.cleanup()
            break
        except Exception as e:
            logger.error(f"Orchestrator stopped unexpectedly: {e}")
            orch.cleanup()
        else:
            # run() returned normally (stop_event set, e.g. core process crashed)
            orch.cleanup()

        logger.info("Restarting orchestrator in 5 seconds...")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("Shutdown requested during restart wait. Exiting.")
            break

    logger.info("Backend stopped.")
    sys.exit(0)
