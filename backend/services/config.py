import os
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_default_plate_model = os.path.join(BASE_DIR, "backend/models/best.pt")
if not os.path.exists(_default_plate_model):
    _default_plate_model = os.path.join(BASE_DIR, "backend/models/yolo26_plate_people_door_int8.onnx")

PLATE_MODEL_PATH = os.environ.get(
    "PLATE_MODEL_PATH",
    _default_plate_model,
)
RKNN_MODEL_PATH = os.path.join(BASE_DIR, "backend/models/yolo26_plate_people_door.rknn")
FACE_MODEL_DIR = os.path.join(BASE_DIR, "backend/models/insightface")
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
KNOWN_FACES_DIR = os.environ.get("KNOWN_FACES_DIR", os.path.join(DATA_DIR, "faces"))
LOG_DIR = os.environ.get("LOG_DIR", os.path.join(DATA_DIR, "logs"))
SNAPSHOT_DIR = os.environ.get("SNAPSHOT_DIR", os.path.join(DATA_DIR, "snapshots"))
RECORDING_DIR = os.environ.get("RECORDING_DIR", os.path.join(DATA_DIR, "recordings"))

for d in [KNOWN_FACES_DIR, DATA_DIR, LOG_DIR, SNAPSHOT_DIR, RECORDING_DIR]:
    os.makedirs(d, exist_ok=True)


def setup_logging(log_filename: str = "app.log", level: int = None):
    import logging
    from logging.handlers import RotatingFileHandler

    if level is None:
        level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)

    os.makedirs(LOG_DIR, exist_ok=True)
    log_file_path = os.path.abspath(os.path.join(LOG_DIR, log_filename))

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s:%(lineno)d: %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    has_file = any(
        isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == log_file_path
        for h in root_logger.handlers
    )
    if not has_file:
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root_logger.addHandler(file_handler)

    has_stream = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        for h in root_logger.handlers
    )
    if not has_stream:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(level)
        root_logger.addHandler(stream_handler)

FRAME_BUFFER_SIZE = 5
VOTE_MIN_CONF = 0.70
FACE_RECOG_EVERY_N = 10
FACE_RECOG_THRESHOLD = 0.40
STRANGER_MATCH_THRESHOLD = 0.35
FACE_SIM_LOWER = 0.30
FACE_SIM_UPPER = 0.75
PLATE_DETECT_EVERY_N = 3
FAKE_PLATE_THRESHOLD = 0.65
SMART_RECORD_PRE_SEC = 5
SMART_RECORD_POST_SEC = 10
SMART_RECORD_TAIL_SEC = 10
SMART_RECORD_MAX_SEC = 300
SMART_RECORD_RETENTION_DAYS = 15
DATA_RETENTION_DAYS = 30

GRAB_WIDTH = 1280
GRAB_HEIGHT = 720
GRAB_FPS_CAP = 20
SHM_FRAME_BYTES = GRAB_WIDTH * GRAB_HEIGHT * 3
RTSP_BUFFER_SIZE = 1
RTSP_RECONNECT_DELAY = 5

ROI_POLYGON_NORM = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
MOTION_THRESHOLD = 1500
MOTION_BLUR_KSIZE = 5
MOTION_DILATE_ITER = 2

DENOISE_SPATIAL_KSIZE = 0
STREAM_JPEG_QUALITY_LIVE = 88


def normalize_plate(plate: str) -> str:
    if not plate:
        return ""
    return plate.upper().replace(" ", "").replace("-", "").replace(".", "").strip()


def trigger_ai_reload():
    import time
    rt = os.path.join(KNOWN_FACES_DIR, ".reload")
    with open(rt, "w") as f:
        marker = str(time.time())
        if f.write(marker) != len(marker):
            logger.error("AI reload marker write was incomplete")
            raise OSError("AI reload marker write was incomplete")
