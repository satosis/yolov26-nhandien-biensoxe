import os

# Project root: three levels up from backend/core/config.py
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Model paths
PLATE_MODEL_PATH = os.environ.get(
    "PLATE_MODEL_PATH",
    os.path.join(BASE_DIR, "backend/models/yolo26_plate_people_door_int8.onnx"),
)
RKNN_MODEL_PATH = os.path.join(BASE_DIR, "backend/models/yolo26_plate_people_door.rknn")
FACE_MODEL_DIR = os.path.join(BASE_DIR, "backend/models/insightface")
KNOWN_FACES_DIR = os.path.join(BASE_DIR, "backend/config/faces")
SNAPSHOT_DIR = os.path.join(BASE_DIR, "backend/data/snapshots")
RECORDING_DIR = os.path.join(BASE_DIR, "backend/data/recordings")

for d in [KNOWN_FACES_DIR, SNAPSHOT_DIR, RECORDING_DIR]:
    os.makedirs(d, exist_ok=True)

# Recognition settings
FRAME_BUFFER_SIZE = 5
VOTE_MIN_CONF = 0.70
FACE_RECOG_EVERY_N = 10
FACE_RECOG_THRESHOLD = 0.40
FACE_SIM_LOWER = 0.35
FACE_SIM_UPPER = 0.75
PLATE_DETECT_EVERY_N = 3
FAKE_PLATE_THRESHOLD = 0.65
SMART_RECORD_PRE_SEC = 5
SMART_RECORD_POST_SEC = 10
SMART_RECORD_TAIL_SEC = 10
SMART_RECORD_MAX_SEC = 300
SMART_RECORD_RETENTION_DAYS = 15

# Shared memory / streaming settings
GRAB_WIDTH = 1280
GRAB_HEIGHT = 720
GRAB_FPS_CAP = 20
SHM_FRAME_BYTES = GRAB_WIDTH * GRAB_HEIGHT * 3
RTSP_BUFFER_SIZE = 4
RTSP_RECONNECT_DELAY = 5

# Motion settings
ROI_POLYGON_NORM = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
MOTION_THRESHOLD = 1500
MOTION_BLUR_KSIZE = 5
MOTION_DILATE_ITER = 2

# Denoise settings
DENOISE_TEMPORAL_ALPHA = 1.0
DENOISE_SPATIAL_KSIZE = 0
STREAM_JPEG_QUALITY_LIVE = 88


def normalize_plate(plate: str) -> str:
    if not plate:
        return ""
    return plate.upper().replace(" ", "").replace("-", "").replace(".", "").strip()
