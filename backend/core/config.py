import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit



def load_env_file(path: str, override: bool = False) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value


# Load môi trường (.env)
load_env_file(".env")
logging.getLogger("ultralytics").setLevel(logging.WARNING)



# --- Database ---

# Model Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RAW_PATH = os.path.join(BASE_DIR, "models", "yolo26_plate_people_door.pt")

def _get_best_model():
    candidates = [
        _RAW_PATH.replace(".pt", "_int8.onnx"),
        _RAW_PATH.replace(".pt", ".onnx"),
        _RAW_PATH,
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return _RAW_PATH

PLATE_MODEL_PATH = _get_best_model()
GENERAL_MODEL_PATH = PLATE_MODEL_PATH
DOOR_MODEL_PATH = os.path.join(BASE_DIR, "models", "door_model.pt")
FACE_MODEL_DIR = os.path.join(BASE_DIR, "models", "insightface")
KNOWN_FACES_DIR = os.path.join(BASE_DIR, "config", "faces")

# --- RTPS & Cameras ---

# --- Detection & Tripwire ---
LINE_Y_RATIO = 0.62
LINE_Y_PIXELS = 0
ROI_POLYGON_NORM = [
    (0.25, 0.40),
    (0.75, 0.40),
    (0.90, 1.00),
    (0.10, 1.00),
]

# --- Tripwire tracker ---
# Số frame liên tiếp cùng phía để xác nhận hướng (giảm noise bbox jitter)
TRIPWIRE_BUFFER_FRAMES = 3
# Thời gian chờ (giây) trước khi fire lại cùng object (tránh đếm lặp khi đứng tại vạch)
TRIPWIRE_COOLDOWN_SECS = 3.0

# Ép OpenCV dùng giao thức TCP để tránh rớt gói tin gây xám màn hình
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

OCR_SOURCE = "rtsp"
SIGNAL_LOSS_TIMEOUT = 30


# --- Settings Manager (Dynamic Config) ---
from backend.core.settings import SettingsManager
SETTINGS_FILE = os.path.join(BASE_DIR, "config", "settings.json")
settings_mgr = SettingsManager(SETTINGS_FILE)

PROCESS_WIDTH = settings_mgr.get("PROCESS_WIDTH")
STREAM_WIDTH = settings_mgr.get("STREAM_WIDTH")
STREAM_FPS = settings_mgr.get("STREAM_FPS")
STREAM_JPEG_QUALITY = settings_mgr.get("STREAM_JPEG_QUALITY")
GENERAL_DETECT_IMGSZ = settings_mgr.get("GENERAL_DETECT_IMGSZ")
GENERAL_DETECT_CONF = settings_mgr.get("GENERAL_DETECT_CONF")
PLATE_DETECT_EVERY_N_FRAMES = settings_mgr.get("PLATE_DETECT_EVERY_N_FRAMES")
LINE_Y_RATIO = settings_mgr.get("LINE_Y_RATIO")
SIGNAL_LOSS_TIMEOUT = settings_mgr.get("SIGNAL_LOSS_TIMEOUT")

# HPC Engine Overrides
MOTION_THRESHOLD = settings_mgr.get("MOTION_THRESHOLD") or 500
GRAB_FPS_CAP = settings_mgr.get("GRAB_FPS_CAP") or 15
FRAME_BUFFER_SIZE = settings_mgr.get("FRAME_BUFFER_SIZE") or 5
PLATE_DETECT_EVERY_N = settings_mgr.get("PLATE_DETECT_EVERY_N_FRAMES") or 4
VOTE_MIN_CONF = settings_mgr.get("VOTE_MIN_CONF") or 0.3
FACE_RECOG_EVERY_N = settings_mgr.get("FACE_RECOG_EVERY_N") or 30

# HPC Infrastructure Constants
PLATE_DETECT_IMGSZ = 416
PLATE_DETECT_CONF = settings_mgr.get("PLATE_DETECT_CONF") or 0.45
GRAB_WIDTH = 1280
GRAB_HEIGHT = 720
SHM_NAME_CAM1 = "hpc_cam1_frame"
SHM_NAME_CAM2 = "hpc_cam2_frame"
SHM_FRAME_BYTES = GRAB_WIDTH * GRAB_HEIGHT * 3
HPC_UI_PORT = 5050
UI_PORT = HPC_UI_PORT
CPU_GOVERNOR = "performance"
SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshots")

# HPC Queue & Connection Constants
INFER_QUEUE_MAXSIZE = 32
RESULT_QUEUE_MAXSIZE = 10
RTSP_RECONNECT_DELAY = 5
RTSP_BUFFER_SIZE = 2
MOTION_BLUR_KSIZE = 5
MOTION_DILATE_ITER = 2

# --- Camera orientation monitor ---
CAMERA_SHIFT_CHECK_EVERY_FRAMES = 8
CAMERA_SHIFT_MIN_INLIER_RATIO = 0.18
CAMERA_SHIFT_MAX_ROTATION_DEG = 3.5
CAMERA_SHIFT_MAX_TRANSLATION_PX = 18
CAMERA_SHIFT_MAX_SCALE_DELTA = 0.08
CAMERA_SHIFT_ALERT_CONSECUTIVE = 3

# --- Cửa cuốn (Brightness-based fallback) ---
DOOR_ROI = (100, 50, 540, 400)
BRIGHTNESS_THRESHOLD = 80
USE_AI_DOOR_DETECTION = os.path.exists(DOOR_MODEL_PATH)

# --- Authorized list ---
CONFIG_PATH = os.path.join(BASE_DIR, "config", "authorized.json")
FACES_DIR = os.path.join(BASE_DIR, "config", "faces")

authorized_plates = []
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
        authorized_plates = [p.upper().replace(" ", "") for p in config.get("plates", [])]
        logging.getLogger(__name__).info("Loaded %d authorized plates", len(authorized_plates))

# --- Utility ---
def normalize_plate(plate_text):
    return re.sub(r"[^A-Z0-9]", "", plate_text.upper())
