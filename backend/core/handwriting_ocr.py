"""
Handwriting OCR pipeline cho sổ ghi chuyến hàng xe tải.

Pipeline:
  1. OpenCV: phát hiện từng dòng text (horizontal projection)
  2. VietOCR: ensemble 3 preprocessing variants, chọn best confidence
  3. Post-correction: fuzzy-match địa danh + hàng hóa bằng từ điển
  4. Rule-based parser: tách text → các trường cargo_trip
"""
import re
import logging
import difflib
from datetime import datetime
from functools import lru_cache
from typing import Optional

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ── Vietnamese dictionary for post-correction ──────────────────────────────────

_VN_PROVINCES = [
    "An Giang", "Bà Rịa Vũng Tàu", "Bắc Giang", "Bắc Kạn", "Bạc Liêu",
    "Bắc Ninh", "Bến Tre", "Bình Định", "Bình Dương", "Bình Phước",
    "Bình Thuận", "Cà Mau", "Cần Thơ", "Cao Bằng", "Đà Nẵng",
    "Đắk Lắk", "Đắk Nông", "Điện Biên", "Đồng Nai", "Đồng Tháp",
    "Gia Lai", "Hà Giang", "Hà Nam", "Hà Nội", "Hà Tĩnh",
    "Hải Dương", "Hải Phòng", "Hậu Giang", "Hòa Bình", "Hưng Yên",
    "Khánh Hòa", "Kiên Giang", "Kon Tum", "Lai Châu", "Lạng Sơn",
    "Lào Cai", "Lâm Đồng", "Long An", "Nam Định", "Nghệ An",
    "Ninh Bình", "Ninh Thuận", "Phú Thọ", "Phú Yên", "Quảng Bình",
    "Quảng Nam", "Quảng Ngãi", "Quảng Ninh", "Quảng Trị", "Sóc Trăng",
    "Sơn La", "Tây Ninh", "Thái Bình", "Thái Nguyên", "Thanh Hóa",
    "Thừa Thiên Huế", "Huế", "Tiền Giang", "TP Hồ Chí Minh", "Sài Gòn",
    "Trà Vinh", "Tuyên Quang", "Vĩnh Long", "Vĩnh Phúc", "Yên Bái",
    # Thêm tên thành phố / quận huyện thông dụng
    "Biên Hòa", "Nha Trang", "Đà Lạt", "Buôn Ma Thuột", "Vinh",
    "Huế", "Hội An", "Tam Kỳ", "Quy Nhơn", "Pleiku", "Bảo Lộc",
    "Phan Thiết", "Phan Rang", "Mỹ Tho", "Cần Thơ", "Long Xuyên",
    "Rạch Giá", "Cà Mau", "Bến Tre", "Tân An", "Vũng Tàu",
    "Thủ Dầu Một", "Tây Ninh", "Đồng Xoài", "Bảo Lộc", "Gia Nghĩa",
]

_CARGO_TYPES = [
    "gạo", "lúa", "ngô", "bắp", "sắn", "khoai", "cám", "trấu",
    "phân bón", "phân NPK", "phân urê", "phân đạm", "vôi bột",
    "xi măng", "cát", "đá", "sỏi", "gạch", "ngói", "cát xây",
    "thép", "sắt", "tôn", "nhôm", "đồng", "dây điện",
    "gỗ", "ván", "ván ép", "tre", "nứa", "lồ ô", "củi",
    "mía", "mủ cao su", "cao su", "mủ", "latex",
    "cà phê", "tiêu", "điều", "hạt điều", "dứa", "chuối",
    "thanh long", "xoài", "nhãn", "vải", "chôm chôm",
    "thủy sản", "tôm", "cá", "mực", "hải sản",
    "hàng đông lạnh", "hàng khô", "hàng bách hóa", "hàng tạp hóa",
    "hàng hóa", "hàng tổng hợp", "vật liệu xây dựng", "nông sản",
    "phế liệu", "giấy", "bìa", "nhựa", "bao bì", "chai lọ",
    "thiết bị", "máy móc", "linh kiện", "đồ điện", "hàng gia dụng",
]

_CARGO_LIST_LOWER = [c.lower() for c in _CARGO_TYPES]


def _correct_place(text: str) -> str:
    """Fuzzy-correct địa danh bằng từ điển tỉnh thành (cutoff 0.72)."""
    if not text or len(text) < 3:
        return text
    matches = difflib.get_close_matches(text, _VN_PROVINCES, n=1, cutoff=0.72)
    if matches:
        logger.debug("place correction: '%s' → '%s'", text, matches[0])
        return matches[0]
    return text


def _correct_cargo(text: str) -> str:
    """Fuzzy-correct loại hàng bằng từ điển (cutoff 0.68)."""
    if not text or len(text) < 3:
        return text
    tl = text.lower()
    matches = difflib.get_close_matches(tl, _CARGO_LIST_LOWER, n=1, cutoff=0.68)
    if matches:
        idx = _CARGO_LIST_LOWER.index(matches[0])
        corrected = _CARGO_TYPES[idx]
        logger.debug("cargo correction: '%s' → '%s'", text, corrected)
        return corrected
    return text


# ── VietOCR loader (lazy, singleton) ──────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_predictor():
    from vietocr.tool.predictor import Predictor
    from vietocr.tool.config import Cfg
    from huggingface_hub import hf_hub_download

    logger.info("Loading VietOCR handwriting model...")
    config = Cfg.load_config_from_name("vgg_transformer")
    weights = hf_hub_download(
        repo_id="DungHugging/vietocr-handwritten-finetune",
        filename="transformerocr.pth",
    )
    config["weights"] = weights
    config["device"] = "cpu"
    config["predictor"]["beamsearch"] = False
    predictor = Predictor(config)
    logger.info("VietOCR model loaded.")
    return predictor


# ── Image preprocessing variants ───────────────────────────────────────────────

def _make_variants(pil_img: Image.Image) -> list[Image.Image]:
    """
    Tạo 3 biến thể ảnh cho ensemble OCR:
      1. Gốc
      2. CLAHE contrast enhancement (tốt cho chữ nhạt/mờ)
      3. Denoise + sharpen (tốt cho ảnh nhiễu/scan kém)
    """
    variants = [pil_img]

    # CLAHE trên kênh gray, convert lại RGB
    gray = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    eq = clahe.apply(gray)
    variants.append(Image.fromarray(cv2.cvtColor(eq, cv2.COLOR_GRAY2RGB)))

    # Denoise + unsharp mask
    bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    denoised = cv2.fastNlMeansDenoisingColored(bgr, None, 10, 10, 7, 21)
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharpened = cv2.filter2D(denoised, -1, kernel)
    variants.append(Image.fromarray(cv2.cvtColor(sharpened, cv2.COLOR_BGR2RGB)))

    return variants


def _ocr_best(pil_img: Image.Image, predictor) -> str:
    """
    Ensemble OCR: thử 3 preprocessing variants, chọn kết quả có confidence cao nhất.
    VietOCR hỗ trợ predict(img, return_prob=True) → (text, prob).
    """
    best_text = ""
    best_prob = -1.0

    for variant in _make_variants(pil_img):
        try:
            text, prob = predictor.predict(variant, return_prob=True)
            text = (text or "").strip()
            logger.debug("OCR variant prob=%.3f text=%r", prob, text[:40])
            if prob > best_prob:
                best_prob = prob
                best_text = text
        except Exception as e:
            logger.debug("OCR variant error: %s", e)

    return best_text


# ── Line detection ─────────────────────────────────────────────────────────────

def _detect_line_crops(img_bgr: np.ndarray, min_width_ratio: float = 0.05) -> list[Image.Image]:
    """Horizontal projection → danh sách PIL crop từng dòng text."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h_proj = np.sum(binary, axis=1)

    in_text = False
    line_spans: list[tuple[int, int]] = []
    start = 0
    row_threshold = binary.shape[1] * 0.01  # ít nhất 1% chiều rộng có chữ

    for y, val in enumerate(h_proj):
        if not in_text and val > row_threshold:
            in_text = True
            start = y
        elif in_text and val <= row_threshold:
            in_text = False
            line_spans.append((start, y))
    if in_text:
        line_spans.append((start, len(h_proj)))

    crops: list[Image.Image] = []
    min_w = int(img_bgr.shape[1] * min_width_ratio)
    for y0, y1 in line_spans:
        if (y1 - y0) < 8:
            continue
        pad = 4
        crop_bgr = img_bgr[max(0, y0 - pad):min(img_bgr.shape[0], y1 + pad), :]
        col_proj = np.sum(binary[y0:y1, :], axis=0)
        if np.sum(col_proj > 0) < min_w:
            continue
        crops.append(Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)))

    return crops


# ── OCR từng dòng ──────────────────────────────────────────────────────────────

def _ocr_lines(image_bytes: bytes) -> list[str]:
    """Ảnh bytes → list câu text từng dòng (dùng ensemble + voting)."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise ValueError("Không đọc được ảnh")

    crops = _detect_line_crops(img_bgr)
    if not crops:
        logger.warning("handwriting_ocr: không phát hiện được dòng nào trong ảnh")
        return []

    predictor = _get_predictor()
    lines: list[str] = []
    for crop in crops:
        try:
            text = _ocr_best(crop, predictor)
            if text:
                lines.append(text)
        except Exception as e:
            logger.warning("VietOCR line error: %s", e)

    logger.info("handwriting_ocr: đọc được %d dòng", len(lines))
    return lines


# ── Rule-based parser ──────────────────────────────────────────────────────────

_DATE_RE = re.compile(
    r"\b(\d{1,2})\s*[/\-\.]\s*(\d{1,2})(?:\s*[/\-\.]\s*(\d{2,4}))?\b"
)
_TIME_RE = re.compile(r"\b(\d{1,2})\s*[hH:]\s*(\d{0,2})\b")
_NUMBER_RE = re.compile(r"[\d,\.]{2,}")
_FUEL_RE = re.compile(
    r"(?:d[ầạaă][ụuy]?|dau|dầu|dạn|lít?|lit|nhiên\s*liệu|nl)\s*[:\-]?\s*([\d,\.]+)",
    re.IGNORECASE,
)
_REVENUE_RE = re.compile(
    r"(?:c[ướuộ][cộ]|cuoc|cuộc|ti[ềe][n]?|tien|gi[áa]|gia|ph[íi]|thu\s*nhập|doanh\s*thu|dt)\s*[:\-]?\s*([\d,\.]+)",
    re.IGNORECASE,
)
_WEIGHT_RE = re.compile(
    r"(?:t[ảaă]i\s*tr[ọo]ng|tr[ọo]ng\s*t[ảaă]i|kh[ốo]i\s*l[ượu][ợu]ng|kl|t[ạa]i|tải)\s*[:\-]?\s*([\d,\.]+)\s*(?:t[ấầa]n?|tạ|kg|T\b)",
    re.IGNORECASE,
)
_TRIP_NO_RE = re.compile(
    r"(?:chuy[eế]n\s*(?:s[ốo]|#|th[ứu])?|s[ốo]\s*chuy[eế]n|trip)\s*[:\-]?\s*(\d{1,3})",
    re.IGNORECASE,
)
_RETURN_KEYWORD_RE = re.compile(
    r"\b(?:v[ềe]\s*ng[àa]y|ng[àa]y\s*v[ềe]|v[ềe]|return|tr[ởo]\s*v[ềe])\b",
    re.IGNORECASE,
)
_ARROW_RE = re.compile(r"[-→>]+")


def _parse_number(s: str) -> Optional[float]:
    # Nếu chuỗi dùng dấu phẩy làm dấu thập phân (VN style: "1.500,5") → chuẩn hóa
    s = s.strip()
    if "." in s and "," in s:
        # "1.500,5" → remove dots (thousand sep), replace comma with dot
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        # "1500,5" → "1500.5" (comma = decimal)
        s = s.replace(",", ".")
    else:
        # "1.500" → could be 1500 (thousand sep) or 1.5; treat as integer when no other sep
        s = s.replace(".", "")
    try:
        return float(s)
    except Exception:
        return None


def _parse_date(line: str, year_hint: int = None) -> Optional[str]:
    m = _DATE_RE.search(line)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    year = int(m.group(3)) if m.group(3) else (year_hint or datetime.now().year)
    if year < 100:
        year += 2000
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    t = _TIME_RE.search(line)
    hour = int(t.group(1)) if t else 0
    minute = int(t.group(2)) if t and t.group(2) else 0
    try:
        dt = datetime(year, month, day, min(hour, 23), min(minute, 59))
        return dt.isoformat(timespec="minutes")
    except Exception:
        return None


def _clean_place(text: str) -> Optional[str]:
    """Xóa artifact OCR ở đầu/cuối rồi fuzzy-correct tên địa danh."""
    if not text:
        return None
    text = re.sub(r"^\s*[\d\s]{1,6}\s+", "", text)
    text = text.strip(" .-,ô:")
    if len(text) <= 1:
        return None
    return _correct_place(text)


def _parse_route(line: str) -> tuple[Optional[str], Optional[str]]:
    """Tách điểm đi → điểm đến từ dòng có mũi tên/gạch."""
    line_clean = _DATE_RE.sub("", line)
    line_clean = _TIME_RE.sub("", line_clean).strip(" -/.")
    parts = _ARROW_RE.split(line_clean)
    if len(parts) >= 2:
        return _clean_place(parts[0]), _clean_place(parts[-1])
    return None, None




def _extract_weight(line: str) -> Optional[float]:
    """Tách trọng tải / khối lượng hàng (tấn/tạ/kg)."""
    m = _WEIGHT_RE.search(line)
    if not m:
        return None
    val = _parse_number(m.group(1))
    if val is None:
        return None
    # Chuẩn hóa về tấn
    raw = m.group(0).lower()
    if "tạ" in raw:
        val = val / 10.0
    elif "kg" in raw:
        val = val / 1000.0
    return val


def _lines_to_trips(lines: list[str]) -> list[dict]:
    """
    Gom các dòng thành chuyến hàng.
    Heuristic: dòng có ngày → bắt đầu chuyến mới.
    Nhận diện: departure_time, return_time, origin, destination,
               fuel_liters, freight_revenue,
               weight_tons, trip_no.
    """
    trips: list[dict] = []
    current: Optional[dict] = None
    year_hint = datetime.now().year

    def _flush():
        if current and current.get("departure_time"):
            trips.append(dict(current))

    def _enrich(line: str, trip: dict, overwrite: bool = False):
        """Trích xuất tất cả trường từ một dòng vào dict trip."""
        origin, dest = _parse_route(line)
        if origin and (overwrite or not trip.get("origin")):
            trip["origin"] = origin
        if dest and (overwrite or not trip.get("destination")):
            trip["destination"] = dest

        fm = _FUEL_RE.search(line)
        if fm and not trip.get("fuel_liters"):
            trip["fuel_liters"] = _parse_number(fm.group(1))

        rm = _REVENUE_RE.search(line)
        if rm and not trip.get("freight_revenue"):
            trip["freight_revenue"] = _parse_number(rm.group(1))

        wm = _extract_weight(line)
        if wm is not None and not trip.get("weight_tons"):
            trip["weight_tons"] = wm


        tn = _TRIP_NO_RE.search(line)
        if tn and not trip.get("trip_no"):
            trip["trip_no"] = int(tn.group(1))

    for line in lines:
        line = line.strip()
        if not line:
            continue

        dep = _parse_date(line, year_hint)

        if dep:
            _flush()
            current = {"departure_time": dep}
            _enrich(line, current)

        elif current is not None:
            # Kiểm tra dòng "ngày về / về ngày..."
            if _RETURN_KEYWORD_RE.search(line):
                ret = _parse_date(line, year_hint)
                if ret and not current.get("return_time"):
                    current["return_time"] = ret
                    continue  # không parse trường khác từ dòng "về"

            _enrich(line, current)

            has_arrow = bool(_ARROW_RE.search(line))
            has_number = bool(_NUMBER_RE.search(line))
            if not has_arrow and not has_number and len(line) > 2:
                if not current.get("note") and line != current.get("departure_time"):
                    current["note"] = line

    _flush()
    return trips


# ── Public API ─────────────────────────────────────────────────────────────────

def extract_trips_from_image(image_bytes: bytes) -> list[dict]:
    """
    Nhận ảnh sổ ghi tay → list dict chuyến hàng.
    Mỗi dict có thể chứa: departure_time, origin, destination,
    fuel_liters, freight_revenue, note.
    """
    lines = _ocr_lines(image_bytes)
    if not lines:
        return []
    trips = _lines_to_trips(lines)
    logger.info("handwriting_ocr: parse được %d chuyến từ %d dòng", len(trips), len(lines))
    return trips
