"""
ocr_utils.py – Vietnamese License Plate OCR

Core strategy:
  1. Detect if the plate is 1-LINE or 2-LINE using aspect ratio.
  2. For 2-line plates: physically split the image into top/bottom halves
     using horizontal projection analysis, then OCR each half independently.
  3. For 1-line plates: OCR the whole image normally.
  4. Run multiple preprocessing variants (ensemble) and pick the best result
     using Vietnamese plate format scoring.
  5. Minimal post-processing: only enforce structural rules
     (position 0-1 must be digit, position 2 must be letter),
     NO hardcoded character substitution like G→C.
"""

import cv2
import numpy as np
import logging
import re

logger = logging.getLogger(__name__)


class VNPlateOCR:
    """RapidOCR-based Vietnamese license plate reader with 2-line support."""

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        # Newer rapidocr_onnxruntime releases changed how kwargs are merged into the
        # engine config and raise (e.g. KeyError 'model_path') when fed the
        # det_db_unclip_ratio tweak directly. Try the tuned config, fall back to
        # defaults so OCR still loads across library versions / Python builds.
        try:
            self._ocr = RapidOCR(det_db_unclip_ratio=2.0)
            logger.info("RapidOCR initialised (det_db_unclip_ratio=2.0)")
        except Exception as e:
            logger.warning(
                "RapidOCR(det_db_unclip_ratio=2.0) not supported by installed version "
                "(%s); falling back to default RapidOCR()", e
            )
            self._ocr = RapidOCR()
            logger.info("RapidOCR initialised (default config)")

        # Vietnamese plate patterns (relaxed to let OCR speak for itself)
        # Format: 2 digits + 1-2 letters + optional separator + 3-5 digits + optional .XX
        self.vn_pattern = re.compile(
            r"^[0-9]{2}[A-Z]{1,2}[0-9]{3,5}(\.[0-9]{2})?$"
        )

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def read(self, crop: np.ndarray) -> tuple[str, float, str]:
        """
        Read a Vietnamese license plate from a cropped BGR image.
        Returns (text, confidence, plate_type).
        """
        if crop is None or crop.size == 0:
            return "", 0.0, "white"

        plate_type = self.detect_plate_type(crop)
        deskewed = self._deskew(crop)

        # Decide strategy based on aspect ratio
        h, w = deskewed.shape[:2]
        aspect = w / max(h, 1)

        if aspect < 2.0:
            # Likely a 2-LINE plate (square-ish: trucks, old motorbikes)
            logger.debug("2-line plate detected (aspect=%.2f), splitting...", aspect)
            text, conf = self._read_two_line(deskewed)
        else:
            # Likely a 1-LINE plate (wide rectangle: cars, new motorbikes)
            text, conf = self._read_one_line(deskewed)

        if not text:
            return "", 0.0, plate_type

        cleaned = self._cleanup_plate(text)
        return cleaned, conf, plate_type

    # ──────────────────────────────────────────────────────────────────────
    # 2-LINE plate strategy: split → OCR each line → combine
    # ──────────────────────────────────────────────────────────────────────

    def _read_two_line(self, plate_img: np.ndarray) -> tuple[str, float]:
        """Split a 2-line plate into top/bottom and OCR each separately."""
        h, w = plate_img.shape[:2]

        # Trim ~5% border to remove plate frame/edge noise that upscaling amplifies
        bx, by = max(2, int(w * 0.05)), max(2, int(h * 0.05))
        plate_img = plate_img[by:h - by, bx:w - bx]
        h, w = plate_img.shape[:2]

        # Upscale small 2-line plates before splitting so each line has enough pixels
        if h < 120:
            scale = max(2, min(4, 120 // max(h, 1)))
            plate_img = cv2.resize(plate_img, (w * scale, h * scale),
                                   interpolation=cv2.INTER_LANCZOS4)

        top, bottom = self._split_two_lines(plate_img)

        if top is None or bottom is None:
            # Fallback: couldn't find a clear split, treat as 1-line
            logger.debug("2-line split failed, falling back to 1-line strategy")
            return self._read_one_line(plate_img)

        # OCR each line independently with ensemble
        top_text, top_conf = self._ocr_line(top)
        bot_text, bot_conf = self._ocr_line(bottom)

        logger.info("2-LINE OCR: top='%s' (%.2f), bottom='%s' (%.2f)",
                     top_text, top_conf, bot_text, bot_conf)

        if not top_text and not bot_text:
            return "", 0.0

        combined = (top_text + bot_text).strip()
        avg_conf = 0.0
        n = 0
        if top_conf > 0:
            avg_conf += top_conf
            n += 1
        if bot_conf > 0:
            avg_conf += bot_conf
            n += 1
        if n > 0:
            avg_conf /= n

        return combined, avg_conf

    def _split_two_lines(self, img: np.ndarray):
        """
        Use horizontal projection to find the gap between two text lines.
        Returns (top_crop, bottom_crop) or (None, None) if no clear gap.
        """
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # Binarize
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        h, w = binary.shape
        # Horizontal projection: count white pixels per row
        projection = np.sum(binary, axis=1) / 255

        # Look for the gap in the middle 40% of the image (between 30%-70% height)
        search_start = int(h * 0.25)
        search_end = int(h * 0.75)
        search_zone = projection[search_start:search_end]

        if len(search_zone) == 0:
            return None, None

        # The split point is where the projection is minimal (fewest ink pixels)
        split_local = int(np.argmin(search_zone))
        split_y = search_start + split_local

        # Validate: the gap should be significantly lower than the line regions
        avg_projection = np.mean(projection)
        gap_value = projection[split_y]

        if gap_value > avg_projection * 0.7:
            # No clear gap found — probably not a real 2-line plate
            logger.debug("No clear gap found (gap=%.0f, avg=%.0f)", gap_value, avg_projection)
            return None, None

        # Small gap (NOT overlap) around the seam so a character straddling the
        # split isn't fed to — and read twice by — both line OCRs.
        pad = max(2, int(h * 0.03))
        top = img[0:max(0, split_y - pad), :]
        bottom = img[split_y + pad:h, :]

        # Validate both halves have enough content
        if top.shape[0] < 8 or bottom.shape[0] < 8:
            return None, None

        return top, bottom

    def _ocr_line(self, line_img: np.ndarray) -> tuple[str, float]:
        """Run ensemble OCR on a single line image (already split — use rec-only)."""
        h, w = line_img.shape[:2]
        if h < 48:
            scale = max(2, 64 // h)
            line_img = cv2.resize(line_img, (w * scale, h * scale),
                                  interpolation=cv2.INTER_LANCZOS4)

        variants = self._make_variants(line_img)
        return self._pick_best_rec_only(variants)

    # ──────────────────────────────────────────────────────────────────────
    # 1-LINE plate strategy: ensemble OCR on the whole image
    # ──────────────────────────────────────────────────────────────────────

    def _read_one_line(self, plate_img: np.ndarray) -> tuple[str, float]:
        """Ensemble OCR on a single-line plate (needs detection for text localization)."""
        variants = self._make_variants(plate_img)
        return self._pick_best_with_det(variants)

    # ──────────────────────────────────────────────────────────────────────
    # Shared ensemble + scoring
    # ──────────────────────────────────────────────────────────────────────

    def _make_variants(self, img: np.ndarray) -> list[np.ndarray]:
        """Create 5 preprocessing variants balancing speed and coverage."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        cl = clahe.apply(gray)
        
        # Adaptive Thresholding is highly effective at recovering characters 
        # under strong local glare (which causes CTC collapse for repeating chars like '11')
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 5)
        thresh_bgr = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

        return [
            img,                                          # Original (good lighting)
            self._sharpen(img),                           # Motion blur
            self._adjust_gamma(img, 0.6),                 # Dark plates (night)
            cv2.cvtColor(cl, cv2.COLOR_GRAY2BGR),         # CLAHE (low contrast)
            thresh_bgr,                                   # Adaptive Thresh (strong glare)
        ]

    def _pick_best_rec_only(self, variants: list[np.ndarray]) -> tuple[str, float]:
        """Run OCR recognition-only on variants (for pre-split single lines)."""
        candidates = []
        for variant in variants:
            try:
                result, _ = self._ocr(variant, use_det=False)
            except Exception:
                continue
            if not result:
                continue
            texts = []
            confs = []
            for item in result:
                if len(item) >= 2:
                    texts.append(str(item[0]))
                    try:
                        confs.append(float(item[1]))
                    except (ValueError, TypeError):
                        confs.append(0.5)

            combined = "".join(texts).upper().replace(" ", "").strip()
            if len(combined) < 3 or not confs:
                continue
            avg_conf = sum(confs) / len(confs)
            repaired = self._structural_repair(combined)
            score = self._score_candidate(repaired, avg_conf)
            candidates.append((repaired, avg_conf, score))

        if not candidates:
            return "", 0.0
        best = max(candidates, key=lambda x: x[2])
        return best[0], best[1]

    def _pick_best_with_det(self, variants: list[np.ndarray]) -> tuple[str, float]:
        """Run OCR with text detection on variants (for full plate images)."""
        candidates = []
        for variant in variants:
            try:
                result, _ = self._ocr(variant, use_det=True)
            except Exception:
                continue
            if not result:
                continue
            # Sort by position: top-to-bottom, left-to-right
            h_v = variant.shape[0]
            bucket = max(8, int(h_v * 0.2))
            sorted_result = sorted(
                result,
                key=lambda x: (x[0][0][1] // bucket, x[0][0][0])
            )
            texts = [line[1] for line in sorted_result]
            confs = []
            for line in sorted_result:
                try:
                    confs.append(float(line[2]))
                except (ValueError, TypeError):
                    confs.append(0.5)

            combined = "".join(texts).upper().replace(" ", "").strip()
            if len(combined) < 3:
                continue

            avg_conf = sum(confs) / len(confs)

            # Structural repair ONLY: enforce position rules
            repaired = self._structural_repair(combined)

            # Score
            score = self._score_candidate(repaired, avg_conf)
            candidates.append((repaired, avg_conf, score))

        if not candidates:
            return "", 0.0

        best = max(candidates, key=lambda x: x[2])
        return best[0], best[1]

    def _score_candidate(self, text: str, conf: float) -> float:
        """Score a text candidate. Higher = better."""
        score = conf

        # Bonus for matching Vietnamese plate format
        if self.vn_pattern.match(text):
            score *= 1.4

        # Bonus for starting with 2 digits (region code)
        if len(text) >= 2 and text[:2].isdigit():
            score *= 1.2

        # Bonus for appropriate length (7-9 chars common)
        if 7 <= len(text) <= 9:
            score *= 1.1

        # Penalty for too short
        if len(text) < 5:
            score *= 0.6

        return score

    # ──────────────────────────────────────────────────────────────────────
    # Structural repair (position-based, NOT character-guessing)
    # ──────────────────────────────────────────────────────────────────────

    def _structural_repair(self, text: str) -> str:
        """
        Apply ONLY structural rules based on Vietnamese plate format:
          Position 0-1: MUST be digits (region code)
          Position 2:   MUST be a letter (category)
          Position 3+:  MUST be digits (serial number)
        
        This does NOT guess which specific character was intended —
        it only corrects when the OCR output violates the structure.
        """
        if not text or len(text) < 4:
            return text

        chars = list(text.replace("-", "").replace(".", "").replace(" ", ""))
        if len(chars) < 4:
            return text

        # Position 0-1: Must be digits
        digit_map = {
            'O': '0', 'Q': '0', 'D': '0',
            'I': '1', 'L': '1',
            'Z': '2',
            'S': '5',
            'B': '8',
        }
        for i in range(min(2, len(chars))):
            if not chars[i].isdigit() and chars[i] in digit_map:
                chars[i] = digit_map[chars[i]]

        # Position 2: Must be a letter
        letter_map = {
            '0': 'D', '6': 'G', '8': 'B', '5': 'S', '2': 'Z',
            '4': 'A', '1': 'I',
        }
        if len(chars) > 2 and chars[2].isdigit() and chars[2] in letter_map:
            chars[2] = letter_map[chars[2]]

        # Position 3+: Must be digits
        for i in range(3, len(chars)):
            if not chars[i].isdigit() and chars[i] in digit_map:
                chars[i] = digit_map[chars[i]]

        return "".join(chars)

    # ──────────────────────────────────────────────────────────────────────
    # Cleanup / formatting
    # ──────────────────────────────────────────────────────────────────────

    def _cleanup_plate(self, text: str) -> str:
        """Format the raw OCR text into standard Vietnamese plate format."""
        if not text:
            return ""
        text = text.upper().replace(" ", "").replace("-", "").replace(".", "").strip()
        if len(text) < 4:
            return text

        # Vietnamese plates are 7-9 chars (e.g. 88C07304, 51F12345).
        # If text is too long, OCR likely picked up border noise.
        # Try to find the best VN plate pattern within the text.
        if len(text) > 9:
            # Search for the standard pattern: 2digits + 1-2letters + 3-5digits
            candidates = re.findall(r"[0-9]{2}[A-Z]{1,2}[0-9]{3,5}", text)
            if candidates:
                # Pick longest match (most complete plate)
                text = max(candidates, key=len)

        # Extract prefix: 2 digits + 1-2 letters
        match = re.match(r"^([0-9]{2}[A-Z]{1,2})(.*)", text)
        if not match:
            return text

        prefix = match.group(1)
        num_part = match.group(2)

        # Clean non-digit characters from numeric part
        digits = re.sub(r"[^0-9]", "", num_part)

        # Format: 5 digits → XXX.XX, 4 digits → XXXX
        if len(digits) == 5:
            formatted = f"{digits[:3]}.{digits[3:]}"
        else:
            formatted = digits

        return f"{prefix}-{formatted}" if formatted else prefix

    # ──────────────────────────────────────────────────────────────────────
    # Plate type detection
    # ──────────────────────────────────────────────────────────────────────

    def detect_plate_type(self, crop: np.ndarray) -> str:
        """Detect plate background color: white, yellow, blue, red."""
        if crop is None or crop.size == 0:
            return "white"

        h, w = crop.shape[:2]
        cy, cx = h // 2, w // 2
        dy, dx = max(1, h // 4), max(1, w // 4)
        roi = crop[cy - dy:cy + dy, cx - dx:cx + dx]
        if roi.size == 0:
            return "white"

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        avg_h = np.mean(hsv[:, :, 0])
        avg_s = np.mean(hsv[:, :, 1])

        if 10 <= avg_h <= 45 and avg_s > 70:
            return "yellow"
        if 85 <= avg_h <= 140 and avg_s > 50:
            return "blue"
        if (avg_h <= 12 or avg_h >= 160) and avg_s > 50:
            return "red"
        return "white"

    # ──────────────────────────────────────────────────────────────────────
    # Image preprocessing helpers
    # ──────────────────────────────────────────────────────────────────────

    def _deskew(self, image):
        """Correct plate rotation using minAreaRect."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

        # np.where gives (row=y, col=x); minAreaRect expects (x, y) points.
        coords = np.column_stack(np.where(thresh > 0))[:, ::-1]
        if len(coords) == 0:
            return image

        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle

        if abs(angle) > 15:
            return image

        h, w = image.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        return cv2.warpAffine(image, M, (w, h),
                              flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)

    def _adjust_gamma(self, image, gamma=1.0):
        inv = 1.0 / gamma
        table = np.array([((i / 255.0) ** inv) * 255
                          for i in range(256)]).astype("uint8")
        return cv2.LUT(image, table)

    def _sharpen(self, image):
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        return cv2.filter2D(image, -1, kernel)
