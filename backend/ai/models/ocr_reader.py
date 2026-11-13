import logging
from backend.util.ocr.vn_plate_ocr import VNPlateOCR

logger = logging.getLogger("inference.ocr_reader")


class OCRReader:
    def __init__(self, native_threads=None):
        self._ocr = VNPlateOCR(native_threads=native_threads)
        logger.info("OCRReader initialised")

    def read(self, crop):
        return self._ocr.read(crop)

    def is_valid_vn_plate(self, text: str) -> bool:
        return self._ocr.is_valid_vn_plate(text)
