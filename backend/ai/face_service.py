import logging
import threading
from backend.services import config as cfg

logger = logging.getLogger("face_service")


class FaceAnalysisService:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, det_size=(640, 640)):
        with cls._lock:
            if cls._instance is None:
                from insightface.app import FaceAnalysis
                logger.info("Initializing shared FaceAnalysis service (buffalo_sc) on CPU...")
                app = FaceAnalysis(name="buffalo_sc", root=cfg.FACE_MODEL_DIR, providers=["CPUExecutionProvider"])
                app.prepare(ctx_id=-1, det_size=det_size)
                cls._instance = app
                logger.info("Shared FaceAnalysis service ready.")
            return cls._instance
