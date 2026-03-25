import os
import time
import cv2
from backend.core import config as cfg

def save_snapshot(frame, cam_id: str, plate_text: str) -> str:
    """Save a plate snapshot and return the relative path."""
    os.makedirs(cfg.SNAPSHOT_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_plate = plate_text.replace("/", "_").replace("\\", "_") or "unknown"
    path = os.path.join(cfg.SNAPSHOT_DIR, f"{cam_id}_{safe_plate}_{ts}.jpg")
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return path

def save_face_snapshot(frame, cam_id: str) -> str:
    """Save a face snapshot and return the relative path."""
    os.makedirs(cfg.SNAPSHOT_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"face_{cam_id}_{ts}.jpg"
    path = os.path.join(cfg.SNAPSHOT_DIR, filename)
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return path
