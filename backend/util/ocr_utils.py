
import cv2
import numpy as np
import logging
import math
import re

logger = logging.getLogger(__name__)


def _confidence(value: object) -> float:
    if isinstance(value, bool):
        return 0.5
    if isinstance(value, (int, np.integer)):
        if abs(value) > 1:
            return 0.5
        confidence = float(value)
    elif isinstance(value, (float, np.floating)):
        confidence = float(value)
    elif isinstance(value, str) and len(value) <= 32 and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value.strip()):
        confidence = float(value)
    else:
        return 0.5
    return confidence if math.isfinite(confidence) and 0 <= confidence <= 1 else 0.5


class VNPlateOCR:

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self._ocr = RapidOCR(det_db_unclip_ratio=2.0)
        logger.info("RapidOCR initialised (det_db_unclip_ratio=2.0)")

        self.vn_pattern = re.compile(
            r"^[0-9]{2}[A-Z]{1,2}[0-9]{3,5}(\.[0-9]{2})?$"
        )


    def read(self, crop: np.ndarray) -> tuple[str, float, str]:
        if crop is None or crop.size == 0:
            return "", 0.0, "white"

        plate_type = self.detect_plate_type(crop)
        deskewed = self._deskew(crop)

        h, w = deskewed.shape[:2]
        aspect = w / max(h, 1)

        if aspect < 2.0:
            logger.debug("2-line plate detected (aspect=%.2f), splitting...", aspect)
            text, conf = self._read_two_line(deskewed)
        else:
            text, conf = self._read_one_line(deskewed)

        if not text:
            return "", 0.0, plate_type

        cleaned = self._cleanup_plate(text)
        return cleaned, conf, plate_type


    def _read_two_line(self, plate_img: np.ndarray) -> tuple[str, float]:
        h, w = plate_img.shape[:2]

        bx, by = max(2, int(w * 0.05)), max(2, int(h * 0.05))
        plate_img = plate_img[by:h - by, bx:w - bx]
        h, w = plate_img.shape[:2]

        if h < 120:
            scale = max(2, min(4, 120 // max(h, 1)))
            plate_img = cv2.resize(plate_img, (w * scale, h * scale),
                                   interpolation=cv2.INTER_LANCZOS4)

        top, bottom = self._split_two_lines(plate_img)

        if top is None or bottom is None:
            logger.debug("2-line split failed, falling back to 1-line strategy")
            return self._read_one_line(plate_img)

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
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        h, w = binary.shape
        projection = np.sum(binary, axis=1) / 255

        search_start = int(h * 0.25)
        search_end = int(h * 0.75)
        search_zone = projection[search_start:search_end]

        if len(search_zone) == 0:
            return None, None

        split_local = int(np.argmin(search_zone))
        split_y = search_start + split_local

        avg_projection = np.mean(projection)
        gap_value = projection[split_y]

        if gap_value > avg_projection * 0.7:
            logger.debug("No clear gap found (gap=%.0f, avg=%.0f)", gap_value, avg_projection)
            return None, None

        pad = max(2, int(h * 0.03))
        top = img[0:max(0, split_y - pad), :]
        bottom = img[split_y + pad:h, :]

        if top.shape[0] < 8 or bottom.shape[0] < 8:
            return None, None

        return top, bottom

    def _ocr_line(self, line_img: np.ndarray) -> tuple[str, float]:
        h, w = line_img.shape[:2]
        if h < 48:
            scale = max(2, 64 // h)
            line_img = cv2.resize(line_img, (w * scale, h * scale),
                                  interpolation=cv2.INTER_LANCZOS4)

        variants = self._make_variants(line_img)
        return self._pick_best_rec_only(variants)


    def _read_one_line(self, plate_img: np.ndarray) -> tuple[str, float]:
        variants = self._make_variants(plate_img)
        return self._pick_best_with_det(variants)


    def _make_variants(self, img: np.ndarray) -> list[np.ndarray]:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        cl = clahe.apply(gray)
        
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 5)
        thresh_bgr = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

        return [
            img,
            self._sharpen(img),
            self._adjust_gamma(img, 0.6),
            cv2.cvtColor(cl, cv2.COLOR_GRAY2BGR),
            thresh_bgr,
        ]

    def _pick_best_rec_only(self, variants: list[np.ndarray]) -> tuple[str, float]:
        candidates = []
        for variant in variants:
            result, _ = self._ocr(variant, use_det=False)
            if not result:
                continue
            texts = []
            confs = []
            for item in result:
                if len(item) >= 2:
                    texts.append(str(item[0]))
                    confs.append(_confidence(item[1]))

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
        candidates = []
        for variant in variants:
            result, _ = self._ocr(variant, use_det=True)
            if not result:
                continue
            h_v = variant.shape[0]
            bucket = max(8, int(h_v * 0.2))
            sorted_result = sorted(
                result,
                key=lambda x: (x[0][0][1] // bucket, x[0][0][0])
            )
            texts = [line[1] for line in sorted_result]
            confs = []
            for line in sorted_result:
                confs.append(_confidence(line[2]))

            combined = "".join(texts).upper().replace(" ", "").strip()
            if len(combined) < 3:
                continue

            avg_conf = sum(confs) / len(confs)

            repaired = self._structural_repair(combined)

            score = self._score_candidate(repaired, avg_conf)
            candidates.append((repaired, avg_conf, score))

        if not candidates:
            return "", 0.0

        best = max(candidates, key=lambda x: x[2])
        return best[0], best[1]

    def _score_candidate(self, text: str, conf: float) -> float:
        score = conf

        if self.vn_pattern.match(text):
            score *= 1.4

        if len(text) >= 2 and text[:2].isdigit():
            score *= 1.2

        if 7 <= len(text) <= 9:
            score *= 1.1

        if len(text) < 5:
            score *= 0.6

        return score


    def _structural_repair(self, text: str) -> str:
        if not text or len(text) < 4:
            return text

        chars = list(text.replace("-", "").replace(".", "").replace(" ", ""))
        if len(chars) < 4:
            return text

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

        letter_map = {
            '0': 'D', '6': 'G', '8': 'B', '5': 'S', '2': 'Z',
            '4': 'A', '1': 'I',
        }
        if len(chars) > 2 and chars[2].isdigit() and chars[2] in letter_map:
            chars[2] = letter_map[chars[2]]

        for i in range(3, len(chars)):
            if not chars[i].isdigit() and chars[i] in digit_map:
                chars[i] = digit_map[chars[i]]

        return "".join(chars)


    def _cleanup_plate(self, text: str) -> str:
        if not text:
            return ""
        text = text.upper().replace(" ", "").replace("-", "").replace(".", "").strip()
        if len(text) < 4:
            return text

        if len(text) > 9:
            candidates = re.findall(r"[0-9]{2}[A-Z]{1,2}[0-9]{3,5}", text)
            if candidates:
                text = max(candidates, key=len)

        match = re.match(r"^([0-9]{2}[A-Z]{1,2})(.*)", text)
        if not match:
            return text

        prefix = match.group(1)
        num_part = match.group(2)

        digits = re.sub(r"[^0-9]", "", num_part)

        if len(digits) == 5:
            formatted = f"{digits[:3]}.{digits[3:]}"
        else:
            formatted = digits

        return f"{prefix}-{formatted}" if formatted else prefix


    def detect_plate_type(self, crop: np.ndarray) -> str:
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


    def _deskew(self, image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

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
