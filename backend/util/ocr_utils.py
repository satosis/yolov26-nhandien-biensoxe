import cv2
import numpy as np
import logging

logger = logging.getLogger(__name__)

class VNPlateOCR:
    """PaddleOCR lightweight wrapper for Vietnamese license plates."""

    def __init__(self):
        from paddleocr import PaddleOCR
        # use_angle_cls=False speeds up inference; lang='en' for plate chars
        # Lang='en' works well for Vietnamese plates as they use Latin characters
        self._ocr = PaddleOCR(
            use_angle_cls=False, 
            lang="en", 
            show_log=False, 
            use_gpu=False,
            det_limit_side_len=320,  # Fast detection for small plate crops
            cpu_threads=6,           # Multi-threaded inference
            use_space_char=False     # Skip space detection 
        )
        logger.info("PaddleOCR initialised for VNPlateOCR")

    def read(self, crop: np.ndarray) -> tuple[str, float]:
        """
        Return (normalized_text, confidence) from a plate crop image.
        """
        if crop is None or crop.size == 0:
            return "", 0.0
            
        result = self._ocr.ocr(crop, cls=False)
        if not result or not result[0]:
            return "", 0.0
            
        texts, confs = [], []
        for line in result[0]:
            if line and len(line) >= 2:
                txt = line[1][0]
                conf = float(line[1][1])
                texts.append(txt)
                confs.append(conf)
                
        if not texts:
            return "", 0.0
            
        # Combine all detected text blocks and clean up
        combined = "".join(texts).upper().replace(" ", "").replace("-", "").replace(".", "")
        avg_conf = sum(confs) / len(confs)
        
        return combined, avg_conf
