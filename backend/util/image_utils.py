import os
import time
import cv2
from backend.services import config as cfg

def save_snapshot(frame, cam_id: str, plate_text: str) -> str:
    """Save a plate snapshot and return the relative path."""
    os.makedirs(cfg.SNAPSHOT_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_plate = plate_text.replace("/", "_").replace("\\", "_") or "unknown"
    path = os.path.join(cfg.SNAPSHOT_DIR, f"{cam_id}_{safe_plate}_{ts}.jpg")
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 96])
    return path

def save_face_snapshot(frame, cam_id: str, bbox: tuple = None) -> str:
    """Save a face snapshot. If bbox is provided, crops and pads the face."""
    os.makedirs(cfg.SNAPSHOT_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"face_{cam_id}_{ts}.jpg"
    path = os.path.join(cfg.SNAPSHOT_DIR, filename)

    img_to_save = frame
    if bbox:
        x, y, w, h = [int(v) for v in bbox]
        fh, fw = frame.shape[:2]
        # Add 50% padding around the face for better context
        pad_x = int(w * 0.5)
        pad_y = int(h * 0.5)
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(fw, x + w + pad_x)
        y2 = min(fh, y + h + pad_y)
        crop = frame[y1:y2, x1:x2]
        if crop.size > 0:
            img_to_save = crop

    cv2.imwrite(path, img_to_save, [cv2.IMWRITE_JPEG_QUALITY, 96])
    return path
