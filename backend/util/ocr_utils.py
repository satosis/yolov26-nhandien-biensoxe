"""Backward compatibility shim for OCR utilities.
All implementation has moved to `backend.util.ocr.vn_plate_ocr` and `backend.util.ocr.constants`.
"""
from backend.util.ocr.constants import VALID_VN_PROVINCES
from backend.util.ocr.vn_plate_ocr import (
    VNPlateOCR,
    PlateCandidate,
    prepare_plate_preview,
    _rectify_light_plate,
    _confidence,
)

__all__ = [
    "VALID_VN_PROVINCES",
    "VNPlateOCR",
    "PlateCandidate",
    "prepare_plate_preview",
    "_rectify_light_plate",
    "_confidence",
]
