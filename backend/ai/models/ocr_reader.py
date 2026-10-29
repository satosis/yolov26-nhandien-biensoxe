import logging
from backend.util.ocr_utils import VNPlateOCR

logger = logging.getLogger("inference.ocr_reader")


class OCRReader:
    def __init__(self, native_threads=None):
        self._ocr = VNPlateOCR(native_threads=native_threads)
        logger.info("OCRReader initialised")

    def read(self, crop, return_debug: bool = False):
        return self._ocr.read(crop, return_debug=return_debug)

    def read_quick(self, crop):
        return self._ocr.read_quick(crop)

    def is_valid_vn_plate(self, text: str) -> bool:
        return self._ocr.is_valid_vn_plate(text)
