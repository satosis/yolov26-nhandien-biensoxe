from dataclasses import dataclass, field
import logging
import math
import re
import time
from typing import Any, Optional, Union

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Valid Vietnamese province license codes
VALID_VN_PROVINCES = {
    11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33,
    34, 35, 36, 37, 38, 43, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62,
    63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 81, 82, 83, 84, 85,
    86, 88, 89, 90, 92, 93, 94, 95, 97, 98, 99,
}


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


@dataclass
class PlateCandidate:
    method: str
    raw_text: str
    cleaned_text: str
    conf: float
    score: float
    is_valid_vn: bool = False
    details: dict = field(default_factory=dict)


class VNPlateOCR:
    """
    Unified Vietnamese License Plate OCR Engine.
    Combines direct detection, deskewing, and line-splitting into a single
    debug-friendly recognition pipeline with detailed scoring and diagnostics.
    """

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = RapidOCR(det_db_unclip_ratio=2.0)
        logger.info("RapidOCR initialised (det_db_unclip_ratio=2.0)")

        # Suffix pattern: 4-5 digits, with or without dots (e.g. 123.45, 12.34, 12345, 1234)
        _suffix = r"(?:[0-9]{3}\.[0-9]{2}|[0-9]{2}\.[0-9]{2}|[0-9]{4,5})"
        # 1. Standard: 29A-123.45, 51B-1234, 51LD-123.45, 29A1-123.45
        self.vn_civ_pattern = re.compile(rf"^[0-9]{{2}}[A-Z]{{1,2}}[0-9]?-?{_suffix}$")
        # 2. Temporary: T60-619.56
        self.vn_temp_pattern = re.compile(rf"^T[0-9]{{2}}-?{_suffix}$")
        # 3. Military: TM-12.34, AA-1234
        self.vn_mil_pattern = re.compile(rf"^[A-Z]{{2}}-?{_suffix}$")

        self.last_debug_info: dict = {}

    def is_valid_vn_plate(self, text: str) -> bool:
        """Check if cleaned plate matches standard Vietnamese formats."""
        if not text or len(text) < 6:
            return False
        clean = text.strip().upper()
        if self.vn_civ_pattern.match(clean):
            return True
        if self.vn_temp_pattern.match(clean):
            return True
        if self.vn_mil_pattern.match(clean):
            return True
        # Check valid province + valid suffix (must have series letter(s) between province code and numbers)
        alphanumeric = re.sub(r"[^A-Z0-9]", "", clean)
        if len(alphanumeric) >= 7 and alphanumeric[:2].isdigit():
            if int(alphanumeric[:2]) in VALID_VN_PROVINCES:
                series = alphanumeric[2:-5] if len(alphanumeric) >= 7 else alphanumeric[2:-4]
                if series and any(c.isalpha() for c in series):
                    if re.search(r"[0-9]{4,5}$", alphanumeric):
                        return True
        return False

    def _parse_det_result(self, res) -> tuple[str, float]:
        """Parse RapidOCR detection bounding boxes and combine multi-line text."""
        if not res:
            return "", 0.0

        lines = []
        for item in res:
            if len(item) >= 3:
                box, raw_text, conf = item[0], item[1], _confidence(item[2])
            elif len(item) == 2:
                box, raw_text, conf = None, item[0], _confidence(item[1])
            else:
                continue

            c_text = re.sub(r"[^A-Z0-9.-]", "", str(raw_text).upper().strip())
            if len(c_text) >= 2:
                y_min = min(p[1] for p in box) if box else 0
                lines.append((y_min, c_text, conf))

        if not lines:
            return "", 0.0

        lines.sort(key=lambda x: x[0])
        texts = [x[1] for x in lines]
        confs = [x[2] for x in lines]
        avg_conf = sum(confs) / len(confs)

        if len(texts) == 1:
            return texts[0], avg_conf
        elif len(texts) >= 2:
            top = texts[0].replace("-", "").replace(".", "")
            bot = texts[1].replace("-", "")
            bot_digits = re.sub(r"[^0-9]", "", bot)
            if len(bot_digits) == 5:
                bot_fmt = f"{bot_digits[:3]}.{bot_digits[3:]}"
            else:
                bot_fmt = bot_digits if bot_digits else bot
            return f"{top}-{bot_fmt}", avg_conf

        return "".join(texts), avg_conf

    def _score_candidate(self, raw_text: str, cleaned: str, conf: float, method: str) -> float:
        """Composite heuristic scoring for candidate plates."""
        score = conf

        is_valid = self.is_valid_vn_plate(cleaned)
        if is_valid:
            score *= 1.45

        # Check valid province
        raw_digits = re.sub(r"[^0-9]", "", cleaned)
        if len(cleaned) >= 2 and cleaned[:2].isdigit():
            prov = int(cleaned[:2])
            if prov in VALID_VN_PROVINCES:
                score *= 1.25

        # Length check (optimal plate length is 7-10 chars e.g. 29A-123.45)
        c_len = len(re.sub(r"[^A-Z0-9]", "", cleaned))
        if 7 <= c_len <= 9:
            score *= 1.15
        elif c_len < 5:
            score *= 0.5
        elif c_len > 10:
            score *= 0.7

        # Reward presence of hyphen separator
        if "-" in cleaned:
            score *= 1.05

        return round(score, 3)

    def _cleanup_plate(self, text: str) -> str:
        if not text:
            return ""

        clean = re.sub(r"[^A-Z0-9.-]", "", text.upper().strip())
        if len(clean) < 3:
            return clean

        # If already formatted with hyphen e.g. T60-619.56 or 29A-123.45
        if "-" in clean:
            parts = clean.split("-", 1)
            prefix = re.sub(r"[^A-Z0-9]", "", parts[0])
            suffix = parts[1]

            # Strip leading noise character before valid province code (e.g. M306 -> 30G/306)
            if len(prefix) >= 3 and not prefix[:2].isdigit():
                if prefix[0] != "T" and prefix[1:3].isdigit() and int(prefix[1:3]) in VALID_VN_PROVINCES:
                    prefix = prefix[1:]

            # Correct common OCR confusion where letter series was read as digit (e.g. 306 -> 30G)
            if len(prefix) == 3 and prefix[:2].isdigit() and prefix[2] in "608":
                conf_map = {"6": "G", "0": "D", "8": "B"}
                prefix = prefix[:2] + conf_map.get(prefix[2], prefix[2])

            digits = re.sub(r"[^0-9]", "", suffix)
            if len(digits) == 5:
                suffix = f"{digits[:3]}.{digits[3:]}"
            elif len(digits) == 4:
                suffix = digits
            return f"{prefix}-{suffix}"

        raw = re.sub(r"[^A-Z0-9]", "", clean)
        # Strip leading single character noise before valid province code (e.g. M30693526 -> 30G-935.26)
        if len(raw) >= 7 and not raw[:2].isdigit():
            if raw[0] != "T" and raw[1:3].isdigit() and int(raw[1:3]) in VALID_VN_PROVINCES:
                raw = raw[1:]

        # 1. Temporary plate: T + 2 digits (e.g. T6061956 -> T60-619.56)
        m_temp = re.match(r"^(T[0-9]{2})([0-9]{4,5})$", raw)
        if m_temp:
            prefix, num = m_temp.groups()
            if len(num) == 5:
                num = f"{num[:3]}.{num[3:]}"
            return f"{prefix}-{num}"

        # 2. Standard civilian plate: 2 digits + 1-2 letters (e.g. 29A12345, 51LD12345)
        m_civ = re.match(r"^([0-9]{2}[A-Z]{1,2})([0-9]{4,5})$", raw)
        if m_civ:
            prefix, num = m_civ.groups()
            if len(num) == 5:
                num = f"{num[:3]}.{num[3:]}"
            return f"{prefix}-{num}"

        # 3. Motorbike plate format: 2 digits + 1 letter + 1 digit (e.g. 29A112345)
        m_moto = re.match(r"^([0-9]{2}[A-Z][0-9])([0-9]{4,5})$", raw)
        if m_moto:
            prefix, num = m_moto.groups()
            if len(num) == 5:
                num = f"{num[:3]}.{num[3:]}"
            return f"{prefix}-{num}"

        # 4. Military / special: 2 letters + digits (e.g. TM1234, AA1234)
        m_mil = re.match(r"^([A-Z]{2})([0-9]{4,5})$", raw)
        if m_mil:
            prefix, num = m_mil.groups()
            if len(num) == 5:
                num = f"{num[:3]}.{num[3:]}"
            return f"{prefix}-{num}"

        # 5. General Vietnamese recovery (starting with valid province code):
        if len(raw) >= 6 and raw[:2].isdigit() and int(raw[:2]) in VALID_VN_PROVINCES:
            prov = raw[:2]
            rest = raw[2:]
            if len(rest) >= 5 and rest[-5:].isdigit():
                suffix = f"{rest[-5:-2]}.{rest[-2:]}"
                series = rest[:-5]
                return f"{prov}{series}-{suffix}" if series else f"{prov}-{suffix}"
            elif len(rest) >= 4 and rest[-4:].isdigit():
                suffix = rest[-4:]
                series = rest[:-4]
                return f"{prov}{series}-{suffix}" if series else f"{prov}-{suffix}"

        if len(raw) > 9:
            candidates = re.findall(r"(?:[0-9]{2}[A-Z]{1,2}|T[0-9]{2})[0-9]{4,5}", raw)
            if candidates:
                return self._cleanup_plate(max(candidates, key=len))

        return raw

    def _structural_repair(self, text: str) -> str:
        if len(text) < 4:
            return text

        chars = list(text.replace("-", "").replace(".", "").replace(" ", ""))
        if len(chars) < 4:
            return text

        digit_map = {
            "O": "0", "Q": "0", "D": "0",
            "I": "1", "L": "1",
            "Z": "2",
            "S": "5",
            "B": "8",
        }
        for i in range(min(2, len(chars))):
            if not chars[i].isdigit() and chars[i] in digit_map:
                chars[i] = digit_map[chars[i]]

        letter_map = {
            "0": "D", "6": "G", "8": "B", "5": "S", "2": "Z",
            "4": "A", "1": "I",
        }
        if len(chars) > 2 and chars[2].isdigit() and chars[2] in letter_map:
            chars[2] = letter_map[chars[2]]

        for i in range(3, len(chars)):
            if not chars[i].isdigit() and chars[i] in digit_map:
                chars[i] = digit_map[chars[i]]

        return "".join(chars)

    def _ocr_with_detection(self, img: np.ndarray, method_name: str) -> Optional[PlateCandidate]:
        """Method: RapidOCR with line detection on image."""
        try:
            res, _ = self._ocr(img, use_det=True)
            if not res:
                return None
            parsed_text, conf = self._parse_det_result(res)
            if not parsed_text or conf < 0.20:
                return None
            cleaned = self._cleanup_plate(parsed_text)
            is_valid = self.is_valid_vn_plate(cleaned)
            score = self._score_candidate(parsed_text, cleaned, conf, method_name)
            return PlateCandidate(
                method=method_name,
                raw_text=parsed_text,
                cleaned_text=cleaned,
                conf=conf,
                score=score,
                is_valid_vn=is_valid,
                details={"res_len": len(res)},
            )
        except Exception as e:
            logger.debug("[VNPlateOCR] %s error: %s", method_name, e)
            return None

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
            line_img = cv2.resize(line_img, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)

        # Recognition-only on line
        res, _ = self._ocr(line_img, use_det=False)
        if not res:
            return "", 0.0

        texts = []
        confs = []
        for item in res:
            if len(item) >= 2:
                texts.append(str(item[0]))
                confs.append(_confidence(item[1]))

        combined = "".join(texts).upper().replace(" ", "").strip()
        avg_conf = sum(confs) / len(confs) if confs else 0.0
        return combined, avg_conf

    def _ocr_two_line_split(self, img: np.ndarray, method_name: str = "split_2line") -> Optional[PlateCandidate]:
        """Method: 2-line vertical projection splitting + per-line OCR."""
        try:
            h, w = img.shape[:2]
            # Padding crop
            bx, by = max(2, int(w * 0.05)), max(2, int(h * 0.05))
            cropped = img[by:h - by, bx:w - bx]
            ch, cw = cropped.shape[:2]
            if ch < 120:
                scale = max(2, min(4, 120 // ch))
                cropped = cv2.resize(cropped, (cw * scale, ch * scale), interpolation=cv2.INTER_LANCZOS4)

            top, bottom = self._split_two_lines(cropped)
            if top is None or bottom is None:
                return None

            top_text, top_conf = self._ocr_line(top)
            bot_text, bot_conf = self._ocr_line(bottom)

            if not top_text and not bot_text:
                return None

            top_clean = top_text.replace("-", "").replace(".", "")
            bot_clean = re.sub(r"[^0-9]", "", bot_text)
            if len(bot_clean) == 5:
                bot_fmt = f"{bot_clean[:3]}.{bot_clean[3:]}"
            else:
                bot_fmt = bot_clean if bot_clean else bot_text

            combined = f"{top_clean}-{bot_fmt}" if top_clean and bot_fmt else f"{top_clean}{bot_fmt}"
            confs = [c for c in (top_conf, bot_conf) if c > 0]
            avg_conf = sum(confs) / len(confs) if confs else 0.0

            cleaned = self._cleanup_plate(combined)
            is_valid = self.is_valid_vn_plate(cleaned)
            score = self._score_candidate(combined, cleaned, avg_conf, method_name)
            return PlateCandidate(
                method=method_name,
                raw_text=combined,
                cleaned_text=cleaned,
                conf=avg_conf,
                score=score,
                is_valid_vn=is_valid,
                details={"top": top_text, "top_conf": top_conf, "bot": bot_text, "bot_conf": bot_conf},
            )
        except Exception as e:
            logger.debug("[VNPlateOCR] split_2line error: %s", e)
            return None


    def _deskew(self, image: np.ndarray) -> np.ndarray:
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
        return cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    def detect_plate_type(self, crop: np.ndarray) -> str:
        """Detect plate background color: white, yellow, blue, red."""
        h, w = crop.shape[:2]
        cy, cx = h // 2, w // 2
        dy, dx = max(1, h // 4), max(1, w // 4)
        roi = crop[cy - dy:cy + dy, cx - dx:cx + dx]
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

    def decode_plate(
        self,
        crop: np.ndarray,
        return_debug: bool = False,
    ) -> Union[tuple[str, float, str], tuple[str, float, str, dict]]:
        """
        Unified license plate decoder: combines raw detection, deskewing,
        and line splitting into a single pipeline.

        Returns (cleaned_text, confidence, plate_type) or with debug info if requested.
        """
        t0 = time.perf_counter()

        if crop is None or crop.size == 0:
            empty_debug = {
                "winner": None,
                "candidates_count": 0,
                "candidates": [],
                "elapsed_ms": 0.0,
                "error": "Crop is empty or None",
            }
            self.last_debug_info = empty_debug
            return ("", 0.0, "white", empty_debug) if return_debug else ("", 0.0, "white")

        plate_type = self.detect_plate_type(crop)
        h, w = crop.shape[:2]
        if h < 75:
            scale = max(2, min(4, 75 // max(h, 1)))
            crop = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)
            h, w = crop.shape[:2]
        aspect = w / max(1, h)

        candidates: list[PlateCandidate] = []

        # -------------------------------------------------------------
        # Method 1: Primary RapidOCR with line detection on raw crop
        # -------------------------------------------------------------
        c_raw = self._ocr_with_detection(crop, "raw_det")
        if c_raw:
            candidates.append(c_raw)
            # Fast-path: reasonable confidence and valid VN pattern -> return immediately
            if c_raw.conf >= 0.58 and c_raw.is_valid_vn:
                return self._finalize_result(c_raw, candidates, plate_type, crop, t0, return_debug)

        # -------------------------------------------------------------
        # Method 2: Deskewed crop
        # -------------------------------------------------------------
        deskewed = self._deskew(crop)
        c_deskew = self._ocr_with_detection(deskewed, "deskew_det")
        if c_deskew:
            candidates.append(c_deskew)
            # Fast-path for deskewed result
            if c_deskew.conf >= 0.58 and c_deskew.is_valid_vn:
                return self._finalize_result(c_deskew, candidates, plate_type, crop, t0, return_debug)

        # -------------------------------------------------------------
        # Method 3: 2-line projection splitting (especially for square/tall plates, aspect < 2.2)
        # -------------------------------------------------------------
        if aspect < 2.2:
            c_split = self._ocr_two_line_split(deskewed, "split_2line")
            if c_split:
                candidates.append(c_split)
                if c_split.conf >= 0.88 and c_split.is_valid_vn:
                    return self._finalize_result(c_split, candidates, plate_type, crop, t0, return_debug)

        # Select overall winner
        valid_candidates = [c for c in candidates if len(c.cleaned_text) >= 3 and c.conf >= 0.30]
        if valid_candidates:
            winner = max(valid_candidates, key=lambda c: c.score)
        elif candidates:
            winner = max(candidates, key=lambda c: c.score)
            if winner.conf < 0.25:
                winner = None
        else:
            winner = None

        return self._finalize_result(winner, candidates, plate_type, crop, t0, return_debug)

    def _finalize_result(
        self,
        winner: Optional[PlateCandidate],
        candidates: list[PlateCandidate],
        plate_type: str,
        crop: np.ndarray,
        t0: float,
        return_debug: bool,
    ):
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        text = winner.cleaned_text if winner else ""
        conf = winner.conf if winner else 0.0

        debug_info = {
            "winner": {
                "text": text,
                "raw_text": winner.raw_text if winner else "",
                "conf": round(conf, 3),
                "score": round(winner.score, 3) if winner else 0.0,
                "method": winner.method if winner else "none",
                "is_valid_vn": winner.is_valid_vn if winner else False,
                "plate_type": plate_type,
            },
            "candidates_count": len(candidates),
            "candidates": [
                {
                    "method": c.method,
                    "raw": c.raw_text,
                    "cleaned": c.cleaned_text,
                    "conf": round(c.conf, 3),
                    "score": round(c.score, 3),
                    "is_valid_vn": c.is_valid_vn,
                }
                for c in candidates
            ],
            "crop_shape": list(crop.shape),
            "elapsed_ms": elapsed_ms,
        }
        self.last_debug_info = debug_info

        cand_summary = (
            ", ".join(f"{c.method}:'{c.cleaned_text}'(conf={c.conf:.2f},score={c.score:.2f})" for c in candidates)
            if candidates
            else "none"
        )
        logger.info(
            "[PlateOCR] Decoded: '%s' (conf=%.2f, method=%s, score=%.2f, %0.1fms) | Candidates (%d): %s",
            text or "<NONE>",
            conf,
            winner.method if winner else "none",
            winner.score if winner else 0.0,
            elapsed_ms,
            len(candidates),
            cand_summary,
        )

        if return_debug:
            return text, conf, plate_type, debug_info
        return text, conf, plate_type

    def read(self, crop: np.ndarray, return_debug: bool = False) -> tuple:
        """Standard entry point for plate OCR, compatible with all callers."""
        return self.decode_plate(crop, return_debug=return_debug)
