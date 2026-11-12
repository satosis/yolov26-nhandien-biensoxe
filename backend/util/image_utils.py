import base64
from collections.abc import Sequence
import io
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Optional, Tuple, Union
import uuid

import cv2
import numpy as np
from PIL import Image, ImageFile, ImageOps

ImageFile.LOAD_TRUNCATED_IMAGES = True

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


def decode_image(
    source: Any,
    *,
    force_bgr: bool = True,
    apply_exif: bool = True,
    return_debug: bool = False,
) -> Union[Optional[np.ndarray], Tuple[Optional[np.ndarray], dict]]:
    """
    Unified image decoder: decodes image data from bytes, bytearray, memoryview,
    base64 (raw string or Data URI), file path (str or Path), PIL Image, or numpy array
    into a standardized BGR numpy array (H, W, 3) uint8.

    Dual-engine decoding (OpenCV + PIL fallback) with automatic EXIF orientation
    handling and comprehensive debug diagnostics.
    """
    debug: dict = {
        "success": False,
        "input_type": type(source).__name__ if source is not None else "None",
        "source_desc": "",
        "shape": None,
        "dtype": None,
        "channels": None,
        "method": None,
        "size_bytes": 0,
        "error": None,
    }

    def _finish(img: Optional[np.ndarray], method: str | None = None, err: str | None = None):
        if img is not None:
            debug["success"] = True
            debug["shape"] = img.shape
            debug["dtype"] = str(img.dtype)
            debug["channels"] = img.shape[2] if img.ndim == 3 else 1
            debug["method"] = method
            logger.debug(
                "[decode_image] Succeeded via '%s': shape=%s, type=%s, size=%d bytes",
                method,
                img.shape,
                debug["input_type"],
                debug["size_bytes"],
            )
        else:
            debug["success"] = False
            debug["error"] = err or "Unknown decode failure"
            logger.warning(
                "[decode_image] Failed: %s (input_type=%s, size=%d bytes, desc=%s)",
                debug["error"],
                debug["input_type"],
                debug["size_bytes"],
                debug["source_desc"],
            )
        return (img, debug) if return_debug else img

    if source is None:
        return _finish(None, err="Input source is None")

    # 1. Already a numpy array
    if isinstance(source, np.ndarray):
        debug["source_desc"] = f"numpy_array(ndim={source.ndim}, dtype={source.dtype})"
        if source.size == 0:
            return _finish(None, err="numpy array is empty (size=0)")

        # 1D buffer of raw bytes (e.g. from np.frombuffer)
        if source.ndim == 1:
            raw_bytes = source.tobytes()
            debug["size_bytes"] = len(raw_bytes)
        elif source.ndim == 2:
            out = cv2.cvtColor(source, cv2.COLOR_GRAY2BGR) if force_bgr else source
            return _finish(out, method="ndarray_2d_gray")
        elif source.ndim == 3:
            ch = source.shape[2]
            if ch == 1:
                out = cv2.cvtColor(source, cv2.COLOR_GRAY2BGR) if force_bgr else source
                return _finish(out, method="ndarray_3d_1ch")
            elif ch == 3:
                return _finish(source, method="ndarray_3d_3ch")
            elif ch == 4:
                out = cv2.cvtColor(source, cv2.COLOR_BGRA2BGR) if force_bgr else source
                return _finish(out, method="ndarray_3d_4ch_bgra")
            else:
                return _finish(None, err=f"Unsupported channel count: {ch}")
        else:
            return _finish(None, err=f"Unsupported ndim: {source.ndim}")
    else:
        raw_bytes = None

    # 2. PIL Image instance
    if isinstance(source, Image.Image):
        debug["source_desc"] = f"PIL.Image(mode={source.mode}, size={source.size})"
        try:
            pil_img = source
            if apply_exif:
                pil_img = ImageOps.exif_transpose(pil_img)
            rgb = pil_img.convert("RGB")
            arr = np.array(rgb)
            out = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR) if force_bgr else arr
            return _finish(out, method="pil_image_object")
        except Exception as e:
            return _finish(None, err=f"PIL Image conversion error: {e}")

    # 3. String or Path (File path, Data URI, or raw Base64)
    if isinstance(source, (str, Path)):
        s_val = str(source).strip()
        debug["source_desc"] = f"str_len={len(s_val)}"

        # Check for Base64 Data URI
        if s_val.startswith("data:image/") or ";base64," in s_val:
            debug["source_desc"] = "data_uri_base64"
            try:
                payload = s_val.split(";base64,", 1)[1] if ";base64," in s_val else s_val
                raw_bytes = base64.b64decode(payload)
                debug["size_bytes"] = len(raw_bytes)
            except Exception as e:
                return _finish(None, err=f"Base64 data URI decode failed: {e}")
        elif os.path.exists(s_val):
            # File on disk
            debug["source_desc"] = f"file_path: {s_val}"
            try:
                with open(s_val, "rb") as f:
                    raw_bytes = f.read()
                debug["size_bytes"] = len(raw_bytes)
            except Exception as e:
                return _finish(None, err=f"Failed to read file '{s_val}': {e}")
        elif len(s_val) > 64 and not s_val.startswith(("/", "\\", ".")) and ("\n" not in s_val):
            # Possible raw base64 string
            try:
                raw_bytes = base64.b64decode(s_val)
                debug["source_desc"] = "raw_base64_string"
                debug["size_bytes"] = len(raw_bytes)
            except Exception:
                return _finish(None, err=f"File not found and not valid base64: {s_val[:50]}...")
        else:
            return _finish(None, err=f"File not found: {s_val}")

    # 4. Binary bytes, bytearray, memoryview
    if isinstance(source, (bytes, bytearray, memoryview)):
        raw_bytes = bytes(source)
        debug["size_bytes"] = len(raw_bytes)
        debug["source_desc"] = f"binary_bytes({len(raw_bytes)} bytes)"

    if raw_bytes is None or len(raw_bytes) == 0:
        return _finish(None, err="Empty byte stream or unhandled input type")

    # Step A: Attempt OpenCV imdecode
    out_img = None
    used_method = None
    try:
        buf = np.frombuffer(raw_bytes, dtype=np.uint8)
        cv_img = cv2.imdecode(buf, cv2.IMREAD_COLOR if force_bgr else cv2.IMREAD_UNCHANGED)
        if cv_img is not None and cv_img.size > 0:
            # Check if EXIF transpose is needed (OpenCV does not transpose EXIF orientation 2-8 by default)
            if apply_exif and (b"Exif" in raw_bytes[:1024] or b"\xff\xe1" in raw_bytes[:64]):
                try:
                    with Image.open(io.BytesIO(raw_bytes)) as pil_test:
                        transposed = ImageOps.exif_transpose(pil_test)
                        if transposed.size != pil_test.size or getattr(pil_test, "_getexif", lambda: None)():
                            rgb = transposed.convert("RGB")
                            out_img = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR) if force_bgr else np.array(rgb)
                            used_method = "pil_exif_transposed"
                except Exception:
                    out_img = cv_img
                    used_method = "cv2_imdecode"
            if out_img is None:
                out_img = cv_img
                used_method = "cv2_imdecode"
    except Exception as e:
        logger.debug("[decode_image] cv2.imdecode exception: %s", e)

    # Step B: Fallback to PIL if OpenCV failed
    if out_img is None:
        try:
            with Image.open(io.BytesIO(raw_bytes)) as pil_img:
                if apply_exif:
                    pil_img = ImageOps.exif_transpose(pil_img)
                rgb = pil_img.convert("RGB")
                arr = np.array(rgb)
                out_img = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR) if force_bgr else arr
                used_method = f"pil_fallback({pil_img.format})"
        except Exception as e:
            return _finish(
                None,
                err=f"Both cv2 and PIL failed to decode {len(raw_bytes)} bytes (header: {raw_bytes[:12]!r}): {e}",
            )

    if out_img is not None and out_img.size > 0:
        return _finish(out_img, method=used_method)

    return _finish(None, err="Decoded image is empty or invalid")
