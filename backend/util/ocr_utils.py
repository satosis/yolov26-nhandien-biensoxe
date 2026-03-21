import cv2
import numpy as np
import logging

logger = logging.getLogger(__name__)

class VNPlateOCR:
    """PaddleOCR lightweight wrapper for Vietnamese license plates."""

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self._ocr = RapidOCR()
        logger.info("RapidOCR initialised for VNPlateOCR (Real-time ONNX)")

    def read(self, crop: np.ndarray) -> tuple[str, float]:
        """
        Return (normalized_text, confidence) from a plate crop image.
        """
        if crop is None or crop.size == 0:
            return "", 0.0
            
        result, _ = self._ocr(crop)
        if not result:
            return "", 0.0
            
        texts, confs = [], []
        # format: [[[[x,y],[x,y],[x,y],[x,y]], "text", conf], ...]
        for line in result:
            txt = line[1]
            conf = float(line[2])
            texts.append(txt)
            confs.append(conf)
            
        if not texts:
            return "", 0.0
            
        # Combine all detected text blocks and clean up
        combined = "".join(texts).upper().replace(" ", "").replace("-", "").replace(".", "")
        avg_conf = sum(confs) / len(confs)
        
        return combined, avg_conf
