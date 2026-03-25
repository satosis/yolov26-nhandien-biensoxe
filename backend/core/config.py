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
