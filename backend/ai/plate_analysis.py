"""Plate adapter for the shared OCR pipeline, without database or camera effects."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import math
import threading

import numpy as np

from backend.util.ocr_pipeline import assess_image, fuse_temporal_crops


def plate_key(text):
    return "".join(c for c in str(text or "").upper() if c.isascii() and c.isalnum())


def _iou(a, b):
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0


def detect_plate_regions(detector, frame, *, roi=None, search_small=False, lock=None):
    """Return native-coordinate detections; ROI is normalized to the source image.

    A secondary central view works on low-resolution sources too. It is independent
    of whether a different plate was found in the full view. Explicit ROI avoids
    extra full-scene passes on resource-constrained machines.
    """
    h, w = frame.shape[:2]
    if roi is not None:
        if len(roi) != 4 or not all(math.isfinite(float(v)) for v in roi):
            raise ValueError("Vùng nhận diện không hợp lệ.")
        x1, y1, x2, y2 = map(float, roi)
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("Vùng nhận diện phải nằm trong khung hình.")
        regions = [(int(x1 * w), int(y1 * h), min(w, math.ceil(x2 * w)), min(h, math.ceil(y2 * h)))]
    else:
        regions = [(0, 0, w, h)]
        if search_small and min(h, w) >= 80:
            regions.append((int(w * .2), int(h * .2), math.ceil(w * .8), math.ceil(h * .8)))

    boxes = []
    for left, top, right, bottom in regions:
        if right - left < 4 or bottom - top < 4:
            continue
        image = frame[top:bottom, left:right]
        if lock is None:
            detections = detector.detect(image)
        else:
            with lock:
                detections = detector.detect(image)
        for detection in detections:
            x1, y1, x2, y2, confidence, cls_id = detection
            if not all(math.isfinite(float(v)) for v in detection):
                continue
            # Clamp to the searched region, not just to the full source frame.
            x1, y1 = max(left, int(x1) + left), max(top, int(y1) + top)
            x2, y2 = min(right, int(x2) + left), min(bottom, int(y2) + top)
            if x2 > x1 and y2 > y1:
                boxes.append((x1, y1, x2, y2, min(1., max(0., float(confidence))), int(cls_id)))
    kept = []
    for box in sorted(boxes, key=lambda b: b[4], reverse=True):
        if not any(box[5] == other[5] and _iou(box, other) >= .45 for other in kept):
            kept.append(box)
    return kept


class PlateFrameAnalyzer:
    """Observe native crops now and recognize only retained crops when requested."""

    def __init__(self, detector, reader, *, roi=None, search_small=False, detector_lock=None, ocr_lock=None,
                 include_people=False, quick_ocr=False):
        self.detector, self.reader = detector, reader
        self.roi, self.search_small = roi, search_small
        self.detector_lock = detector_lock
        self.ocr_lock = ocr_lock or threading.Lock()
        self.cache = OrderedDict()
        self.detected_frames = 0
        self.ocr_calls = 0
        self.include_people, self.quick_ocr = include_people, quick_ocr

    def is_valid(self, text):
        engine = getattr(self.reader, "_ocr", self.reader)
        validator = getattr(engine, "is_valid_vn_plate", None)
        return bool(validator(text)) if validator else len(plate_key(text)) >= 6

    def observe(self, frame, timestamp=0):
        observations = []
        for x1, y1, x2, y2, confidence, cls_id in detect_plate_regions(
            self.detector, frame, roi=self.roi, search_small=self.search_small, lock=self.detector_lock
        ):
            if cls_id == 1 and self.include_people:
                observations.append({
                    "type": "person", "bbox": [x1, y1, x2, y2], "text": "Người",
                    "confidence": confidence, "detector_confidence": confidence,
                    "quality": {"quality_score": confidence},
                    "crop": frame[y1:y2, x1:x2],
                })
                continue
            if cls_id != 0:
                continue
            # Scale padding with the plate; fixed 10px padding overwhelms tiny text.
            px, py = max(2, round((x2 - x1) * .10)), max(1, round((y2 - y1) * .12))
            # The temporal aggregator copies only retained crops. Avoid copying
            # every detection before that bounded selection owns its memory.
            crop = frame[max(0, y1 - py):min(frame.shape[0], y2 + py), max(0, x1 - px):min(frame.shape[1], x2 + px)]
            quality = assess_image(crop)
            if not quality["valid"]:
                continue
            observations.append({
                "type": "plate", "bbox": [x1, y1, x2, y2], "text": "", "confidence": 0.,
                "detector_confidence": confidence, "quality": quality, "crop": crop,
                "native_plate_size": [x2 - x1, y2 - y1],
            })
        self.detected_frames += bool(observations)
        return observations

    def recognize_crop(self, crop, *, full=False):
        contiguous = np.ascontiguousarray(crop)
        key = (bool(full), crop.shape, hashlib.blake2b(memoryview(contiguous), digest_size=16).digest())
        if key in self.cache:
            self.cache.move_to_end(key)
            return dict(self.cache[key])
        with self.ocr_lock:
            read = getattr(self.reader, "read_quick", self.reader.read) if self.quick_ocr and not full else self.reader.read
            text, confidence, plate_type = read(crop)
            debug = dict(getattr(getattr(self.reader, "_ocr", self.reader), "last_debug_info", {}))
        self.ocr_calls += 1
        result = {"text": text or "", "confidence": float(confidence), "plate_type": plate_type,
                  "valid_format": self.is_valid(text), "debug_ocr": debug}
        self.cache[key] = result
        if len(self.cache) > 128:
            self.cache.popitem(last=False)
        return dict(result)

    def recognize_track(self, crops):
        """Retry an uncertain moving-plate track with aligned temporal views."""
        fused = fuse_temporal_crops(crops)
        if fused is None:
            return None
        # A fused crop is the expensive fallback for an uncertain track. Use
        # the complete OCR path here, even when per-frame reads are bounded.
        result = self.recognize_crop(fused, full=True)
        result["temporal_fusion"] = True
        return result
