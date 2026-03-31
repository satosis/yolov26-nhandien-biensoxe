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

        # VN Plate format: 2 digits + 1-2 letters + (optional -) + 4-5 digits (with optional .)
        # Examples: 88A-000.24, 30A12345, 29K1-1111
        self.vn_pattern = re.compile(r"^[0-9]{2}[A-Z]{1,2}-?[0-9]{3,5}(\.[0-9]{2})?$")

    def read(self, crop: np.ndarray) -> tuple[str, float]:
        """
        Structural Ensemble OCR: Advanced alignment and sorting for Vietnamese plates.
        """
        if crop is None or crop.size == 0:
            return "", 0.0

        # The regex pattern is now an instance attribute, no need to re-import or re-compile here.

        # 1. Base Alignment (Deskew the original for the best shot)
        deskewed = self._deskew(crop)
        
        # 2. Variants based on the deskewed image (Reduced for RAM/CPU optimization)
        variants = [
            deskewed,
            self._adjust_gamma(deskewed, 1.4), # Increased contrast variant
            self._sharpen(deskewed),           # Sharpen variant
        ]
        
        # 3. Skip heavy processing if plate is likely clear/large
        if crop.shape[0] > 60:
             # For high res plates, original deskewed is often enough. 
             # We just use original and sharpen.
             variants = [deskewed, self._sharpen(deskewed)]
        
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
            # Keep - and . characters as requested by user
            combined = "".join(texts).upper().replace(" ", "").strip()
            if len(combined) < 4: continue
                
            avg_conf = sum(confs) / len(confs)
            
            # Format-aware scoring using the improved regex that allows - and .
            fmt_bonus = 1.30 if self.vn_pattern.match(combined) else 1.0
            digit_start_bonus = 1.10 if len(combined) >= 2 and combined[:2].isdigit() else 1.0
            
            score = avg_conf * (1.0 + (len(combined) * 0.025)) * fmt_bonus * digit_start_bonus
            
            if score > best_score:
                best_text, best_conf = combined, avg_conf
                best_score = score
                
        return self._cleanup_plate(best_text), best_conf

    def _cleanup_plate(self, text: str) -> str:
        """
        Post-processes OCR result to fix common misreads and formatting errors.
        Handles '88C0073.04' -> '88C073.04' by enforcing Vietnamese 5-digit rules.
        """
        if not text or len(text) < 5:
            return text
            
        # 1. Separate prefix (Region + Category, e.g. 88C, 29K1) from number part
        if '-' in text:
            parts = text.split('-', 1)
            prefix, num_part = parts[0] + '-', parts[1]
        else:
            # Fallback to non-greedy regex for plates without hyphens (common in OCR hallucinations)
            match = re.search(r"^([0-9]{2}[A-Z]{1,2}[0-9]??)(.*)", text)
            if not match:
                return text
            prefix, num_part = match.group(1), match.group(2)
        
        # 2. Clean numeric part: replace common misreads and keep only valid digits/dots
        clean_num = num_part.replace('O', '0').replace('I', '1').replace('S', '5').replace('Z', '2')
        
        # 3. Handle redundant leading zeros (The '88C0073.04' case)
        only_digits = re.sub(r"[^0-9]", "", clean_num)
        
        if len(only_digits) == 6:
            # If 6 digits and starts with 00, it's likely a hallucination of one 0
            if only_digits.startswith("00"):
                # Remove the first 0
                clean_num = clean_num.replace("00", "0", 1)
        
        # 4. Standardize Dot positioning for 5-digit number parts (XXX.XX)
        final_digits = re.sub(r"[^0-9]", "", clean_num)
        if len(final_digits) == 5:
            # Reconstruct as XXX.XX only if it wasn't a 4-digit plate with junk
            clean_num = f"{final_digits[:3]}.{final_digits[3:]}"
        elif len(final_digits) == 4:
            # Reconstruct as XXXX
            clean_num = final_digits
            
        return f"{prefix}{clean_num}"

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

    def _laplacian(self, image):
        """Emphasizes edges and small details like dots/dashes."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        laplacian = np.uint8(np.absolute(laplacian))
        # Convert back to BGR for the ensemble processor
        return cv2.cvtColor(laplacian, cv2.COLOR_GRAY2BGR)
