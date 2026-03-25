import os

# --- Model Paths ---
# verified path from orchestrator.log
PLATE_MODEL_PATH = "/Users/mac/Documents/yolov26-nhandien-biensoxe/backend/models/yolo26_plate_people_door_int8.onnx"
# Buffalo_sc directory must contain 'models/buffalo_sc'
FACE_MODEL_DIR = "/Users/mac/Documents/yolov26-nhandien-biensoxe/backend/models/insightface"
KNOWN_FACES_DIR = "/Users/mac/Documents/yolov26-nhandien-biensoxe/backend/config/faces"
SNAPSHOT_DIR = "/Users/mac/Documents/yolov26-nhandien-biensoxe/backend/data/snapshots"

# --- Recognition Settings ---
FRAME_BUFFER_SIZE = 5
VOTE_MIN_CONF = 0.70
FACE_RECOG_EVERY_N = 10
PLATE_DETECT_EVERY_N = 3

# --- SHM / Streaming Settings ---
GRAB_WIDTH = 1280
GRAB_HEIGHT = 720
GRAB_FPS_CAP = 15
SHM_FRAME_BYTES = GRAB_WIDTH * GRAB_HEIGHT * 3
RTSP_BUFFER_SIZE = 4
RTSP_RECONNECT_DELAY = 5

# --- Motion Settings ---
# ROI_POLYGON_NORM: normalized coordinates (0.0 to 1.0)
ROI_POLYGON_NORM = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)] # Full screen default
MOTION_THRESHOLD = 1500
MOTION_BLUR_KSIZE = 5
MOTION_DILATE_ITER = 2

# --- Additional Optimization ---
DENOISE_TEMPORAL_ALPHA = 1.0 # 1.0 = Off
DENOISE_SPATIAL_KSIZE = 3
STREAM_JPEG_QUALITY_LIVE = 88

# --- Telegram Bot Config (Restored) ---
TOKEN = os.environ.get("TELEGRAM_TOKEN", "7525014385:AAFOoctmRZUNS8_xt6nRzVBs2gIuLSSlVDc")
CHAT_IMPORTANT = os.environ.get("TELEGRAM_CHAT_IMPORTANT", "-5273529392")
CHAT_REGULAR = os.environ.get("TELEGRAM_CHAT_REGULAR", "-5171619580")
FACES_DIR = os.environ.get("FACES_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data/faces"))

def normalize_plate(plate: str) -> str:
    if not plate: return ""
    return plate.upper().replace(" ", "").replace("-", "").replace(".", "").strip()
