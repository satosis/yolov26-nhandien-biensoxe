import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import multiprocessing as mp
import ssl
import certifi

ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

from dotenv import load_dotenv
load_dotenv()

from backend.services import config as cfg
cfg.setup_logging("backend.log")

from backend.services.orchestrator import CameraOrchestrator

logger = logging.getLogger("main")

if __name__ == "__main__":
    mp.set_start_method('spawn', force=True)

    logger.info("================================================")
    logger.info("   Backend Orchestrator is starting...         ")
    logger.info("   Connect to UI at: http://localhost:8000    ")
    logger.info("================================================")

    orch = CameraOrchestrator()
    try:
        orch.run()
    finally:
        orch.cleanup()
        logger.info("Backend stopped.")
