"""Backward compatibility shim for OCR pipeline.
All implementation has moved to `backend.util.ocr.pipeline`.
"""
from backend.util.ocr.pipeline import (
    assess_image,
    prepare_ocr_image,
    iter_ocr_variants,
    OCRTrackAggregator,
    fuse_temporal_crops,
    best_prediction,
    _bgr,
    _bounded_resize,
    _profile_image,
    _confidence,
    _number,
    _PROFILES,
    _MAX_VARIANTS,
    _MAX_RETAINED_EDGE,
    _MAX_RETAINED_PIXELS,
)

__all__ = [
    "assess_image",
    "prepare_ocr_image",
    "iter_ocr_variants",
    "OCRTrackAggregator",
    "fuse_temporal_crops",
    "best_prediction",
    "_bgr",
    "_bounded_resize",
    "_profile_image",
    "_confidence",
    "_number",
    "_PROFILES",
    "_MAX_VARIANTS",
    "_MAX_RETAINED_EDGE",
    "_MAX_RETAINED_PIXELS",
]
