import cv2
import numpy as np
import logging
import re

logger = logging.getLogger(__name__)

class VNPlateOCR:
    """PaddleOCR lightweight wrapper for Vietnamese license plates."""

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        # Increase unclip_ratio to 2.0 to prevent character clipping at edges
        self._ocr = RapidOCR(det_db_unclip_ratio=2.0)
        logger.info("RapidOCR initialised with det_db_unclip_ratio=2.0")

    def read(self, crop: np.ndarray) -> tuple[str, float]:
        """
        Structural Ensemble OCR: Advanced alignment and sorting for Vietnamese plates.
        """
        if crop is None or crop.size == 0:
            return "", 0.0

        import re
        vn_pattern = re.compile(r"^[0-9]{2}[A-Z]{1,2}[0-9]{4,5}$")

        # 1. Base Alignment (Deskew the original for the best shot)
        deskewed = self._deskew(crop)
        
        # 2. Variants based on the deskewed image
        variants = [
            deskewed,
            self._adjust_gamma(deskewed, 1.5),
            self._sharpen(deskewed),
            self._v_sharpen(deskewed),
        ]
        
        # 3. Add CLAHE (Better than Gamma for uneven light)
        gray = cv2.cvtColor(deskewed, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        clahe_img = clahe.apply(gray)
        variants.append(cv2.cvtColor(clahe_img, cv2.COLOR_GRAY2BGR))
        
        # 4. Adaptive Threshold
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        variants.append(cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR))
        
        best_text, best_conf = "", 0.0
        best_score = 0.0
        
        for variant in variants:
            result, _ = self._ocr(variant)
            if not result: continue
            
            # --- Box Sorting ---
            # result is list of [bbox, text, conf]
            # For 2-line plates, we MUST sort by Y then X
            # bbox is [[x,y], [x,y], [x,y], [x,y]]
            def get_y(item): return item[0][0][1] # Top-left Y
            def get_x(item): return item[0][0][0] # Top-left X
            
            # Sort by Y with a tolerance (e.g. 10px) to handle slight tilts, then X
            sorted_result = sorted(result, key=lambda x: (get_y(x) // 15, get_x(x)))
                
            texts, confs = [], []
            for line in sorted_result:
                texts.append(line[1])
                confs.append(float(line[2]))
            
            if not texts: continue
            combined = "".join(texts).upper().replace(" ", "").replace("-", "").replace(".", "").strip()
            if len(combined) < 4: continue
                
            avg_conf = sum(confs) / len(confs)
            
            fmt_bonus = 1.30 if vn_pattern.match(combined) else 1.0
            digit_start_bonus = 1.10 if len(combined) >= 2 and combined[:2].isdigit() else 1.0
            
            score = avg_conf * (1.0 + (len(combined) * 0.025)) * fmt_bonus * digit_start_bonus
            
            if score > best_score:
                best_text, best_conf = combined, avg_conf
                best_score = score
                
        return best_text, best_conf

    def _deskew(self, image):
        """Automatically corrects plate rotation/skew."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        
        # Find all white pixels (characters)
        coords = np.column_stack(np.where(thresh > 0))
        if len(coords) == 0: return image
        
        angle = cv2.minAreaRect(coords)[-1]
        # minAreaRect returns angle in range [-90, 0)
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
            
        # Limit correction to avoid wildly rotating the image
        if abs(angle) > 15: return image
        
        (h, w) = image.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        return rotated

    def _adjust_gamma(self, image, gamma=1.0):
        invGamma = 1.0 / gamma
        table = np.array([((i / 255.0) ** invGamma) * 255 for i in np.arange(0, 256)]).astype("uint8")
        return cv2.LUT(image, table)

    def _sharpen(self, image):
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        return cv2.filter2D(image, -1, kernel)

    def _v_sharpen(self, image):
        kernel = np.array([[-1, 2, -1], [-1, 2, -1], [-1, 2, -1]])
        return cv2.filter2D(image, -1, kernel)

    def _rotate(self, image, angle):
        h, w = image.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        return cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
