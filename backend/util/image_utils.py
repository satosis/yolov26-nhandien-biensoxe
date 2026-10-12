from collections.abc import Sequence
import logging
import os
import re
import time
import uuid

import cv2

from backend.services import config as cfg

logger = logging.getLogger(__name__)


def _snapshot_token(value: str, label: str, *, default: str | None = None) -> str:
    if not isinstance(value, str):
        logger.error("Invalid snapshot filename component: %s must be text", label)
        raise ValueError(f"{label} must be text")
    token = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")[:80]
    if token:
        return token
    if default is not None:
        return default
    logger.error("Invalid snapshot filename component: %s is empty", label)
    raise ValueError(f"{label} is empty")


def _snapshot_path(prefix: str) -> str:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(cfg.SNAPSHOT_DIR, f"{prefix}_{timestamp}_{uuid.uuid4().hex}.jpg")


def _ensure_snapshot_directory() -> None:
    os.makedirs(cfg.SNAPSHOT_DIR, exist_ok=True)


def _write_snapshot(path: str, image, kind: str, failure_message: str) -> None:
    written = cv2.imwrite(path, image, [cv2.IMWRITE_JPEG_QUALITY, 96])
    if not written:
        logger.error("%s snapshot write returned False", kind)
        raise OSError(failure_message)

def save_snapshot(frame, cam_id: str, plate_text: str) -> str:
    _ensure_snapshot_directory()
    camera = _snapshot_token(cam_id, "Camera ID")
    plate = _snapshot_token(plate_text, "Plate text", default="unknown")
    path = _snapshot_path(f"{camera}_{plate}")
    _write_snapshot(path, frame, "Plate", "Could not save snapshot image")
    return path

def save_face_snapshot(frame, cam_id: str, bbox: Sequence[int]) -> str:
    """Persist a padded crop of the canonical frame-bounded face box."""
    x1, y1, x2, y2 = bbox
    fh, fw = frame.shape[:2]
    pad_x = (x2 - x1) // 2
    pad_y = (y2 - y1) // 2
    left = max(0, x1 - pad_x)
    top = max(0, y1 - pad_y)
    right = min(fw, x2 + pad_x)
    bottom = min(fh, y2 + pad_y)

    _ensure_snapshot_directory()
    camera = _snapshot_token(cam_id, "Camera ID")
    path = _snapshot_path(f"face_{camera}")
    _write_snapshot(
        path, frame[top:bottom, left:right], "Face", "Could not save face snapshot image"
    )
    return path
