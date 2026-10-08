"""AI Domain Module — Object detection (YOLO), OCR, Face Recognition, KYC, and Tripwire tracking."""
from backend.ai.tripwire import TripwireTracker
from backend.ai.kyc_manager import KYCManager
from backend.ai.stranger_manager import StrangerManager, get_stranger_manager
from backend.ai.handwriting_ocr import extract_trip_candidates_from_document

try:
    from backend.ai.inference import (
        PlateDetector,
        OCRReader,
        FaceRecognizer,
        InferenceResult,
        inference_process,
        enhance_plate,
        validate_plate_crop,
    )
except ImportError:
    pass

__all__ = [
    "TripwireTracker",
    "KYCManager",
    "StrangerManager",
    "get_stranger_manager",
    "extract_trip_candidates_from_document",
    "PlateDetector",
    "OCRReader",
    "FaceRecognizer",
    "InferenceResult",
    "inference_process",
    "enhance_plate",
    "validate_plate_crop",
]
