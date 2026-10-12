import base64
import io
import os
import re
import logging
import difflib
import calendar
import math
import unicodedata
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


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
    "Biên Hòa", "Nha Trang", "Đà Lạt", "Buôn Ma Thuột", "Vinh",
    "Huế", "Hội An", "Tam Kỳ", "Quy Nhơn", "Pleiku", "Bảo Lộc",
    "Phan Thiết", "Phan Rang", "Mỹ Tho", "Cần Thơ", "Long Xuyên",
    "Rạch Giá", "Cà Mau", "Bến Tre", "Tân An", "Vũng Tàu",
    "Thủ Dầu Một", "Tây Ninh", "Đồng Xoài", "Bảo Lộc", "Gia Nghĩa",
    "Tề Lỗ", "Yên Lạc", "Vĩnh Phúc",
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
    if len(text) < 3:
        return text
    folded_lookup = {_fold_text(place).lower(): place for place in _VN_PROVINCES}
    folded_text = _fold_text(text).lower()
    if folded_text in folded_lookup:
        return folded_lookup[folded_text]
    matches = difflib.get_close_matches(text, _VN_PROVINCES, n=1, cutoff=0.72)
    if matches:
        logger.debug("place correction: '%s' → '%s'", text, matches[0])
        return matches[0]
    folded_matches = difflib.get_close_matches(folded_text, list(folded_lookup.keys()), n=1, cutoff=0.78)
    if folded_matches:
        corrected = folded_lookup[folded_matches[0]]
        logger.debug("place folded correction: '%s' → '%s'", text, corrected)
        return corrected
    return text


def _correct_cargo(text: str) -> str:
    if len(text) < 3:
        return text
    tl = text.lower()
    matches = difflib.get_close_matches(tl, _CARGO_LIST_LOWER, n=1, cutoff=0.68)
    if matches:
        idx = _CARGO_LIST_LOWER.index(matches[0])
        corrected = _CARGO_TYPES[idx]
        logger.debug("cargo correction: '%s' → '%s'", text, corrected)
        return corrected
    return text



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
    config["predictor"]["beamsearch"] = os.getenv("VIETOCR_BEAMSEARCH", "1").strip().lower() not in {"0", "false", "no"}
    predictor = Predictor(config)
    logger.info("VietOCR model loaded.")
    return predictor



def _remove_ruling_lines(gray: np.ndarray) -> np.ndarray:
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 15,
    )
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(18, gray.shape[1] // 3), 1))
    ruling = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel, iterations=1)
    mask = cv2.dilate(ruling, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 2)), iterations=1)
    return cv2.inpaint(gray, mask, 3, cv2.INPAINT_TELEA)


def _deskew_line(gray: np.ndarray) -> np.ndarray:
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(binary > 0))
    if len(coords) < 20:
        return gray
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.5 or abs(angle) > 10:
        return gray
    h, w = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _normalize_line_crop(pil_img: Image.Image) -> Image.Image:
    rgb = np.array(pil_img.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = _remove_ruling_lines(gray)

    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 13,
    )
    coords = cv2.findNonZero(binary)
    if coords is not None:
        x, y, w, h = cv2.boundingRect(coords)
        pad_x = max(8, int(w * 0.08))
        pad_y = max(5, int(h * 0.35))
        x0 = max(0, x - pad_x)
        y0 = max(0, y - pad_y)
        x1 = min(gray.shape[1], x + w + pad_x)
        y1 = min(gray.shape[0], y + h + pad_y)
        gray = gray[y0:y1, x0:x1]

    gray = _deskew_line(gray)
    h, w = gray.shape[:2]
    if h > 0:
        target_h = 72
        scale = target_h / h
        target_w = max(32, min(1600, int(w * scale)))
        gray = cv2.resize(gray, (target_w, target_h), interpolation=cv2.INTER_CUBIC)

    gray = cv2.copyMakeBorder(gray, 10, 10, 16, 16, cv2.BORDER_CONSTANT, value=255)
    rgb_out = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
    return Image.fromarray(rgb_out)


def _make_variants(normalized_crop: Image.Image) -> list[Image.Image]:
    variants = [normalized_crop]

    gray = cv2.cvtColor(np.array(normalized_crop), cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    eq = clahe.apply(gray)
    variants.append(Image.fromarray(cv2.cvtColor(eq, cv2.COLOR_GRAY2RGB)))

    binary = cv2.adaptiveThreshold(
        eq, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 11,
    )
    variants.append(Image.fromarray(cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB)))

    background = cv2.medianBlur(eq, 31)
    normalized = cv2.divide(eq, background, scale=255)
    variants.append(Image.fromarray(cv2.cvtColor(normalized, cv2.COLOR_GRAY2RGB)))

    bgr = cv2.cvtColor(np.array(normalized_crop), cv2.COLOR_RGB2BGR)
    denoised = cv2.fastNlMeansDenoisingColored(bgr, None, 10, 10, 7, 21)
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharpened = cv2.filter2D(denoised, -1, kernel)
    variants.append(Image.fromarray(cv2.cvtColor(sharpened, cv2.COLOR_BGR2RGB)))

    return variants


def _ocr_best_result(normalized_crop: Image.Image, predictor) -> dict:
    best_text = ""
    best_prob = -1.0

    for variant in _make_variants(normalized_crop):
        text, prob = predictor.predict(variant, return_prob=True)
        text = (text or "").strip()
        logger.debug("OCR variant prob=%.3f text=%r", prob, text[:40])
        if prob > best_prob:
            best_prob = prob
            best_text = text

    return {"text": best_text, "confidence": max(0.0, float(best_prob))}


def _image_to_data_uri(pil_img: Image.Image) -> str:
    buffer = io.BytesIO()
    pil_img.save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _resize_for_ocr(img_bgr: np.ndarray, min_width: int = 1800, max_width: int = 3200) -> np.ndarray:
    h, w = img_bgr.shape[:2]
    if w < min_width:
        scale = min_width / w
    elif w > max_width:
        scale = max_width / w
    else:
        return img_bgr
    return cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)


def _order_quad_points(points: np.ndarray) -> np.ndarray:
    points = points.reshape(4, 2).astype("float32")
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1)
    return np.array([
        points[np.argmin(sums)],
        points[np.argmin(diffs)],
        points[np.argmax(sums)],
        points[np.argmax(diffs)],
    ], dtype="float32")


def _auto_crop_document(img_bgr: np.ndarray) -> np.ndarray:
    h, w = img_bgr.shape[:2]
    if h < 200 or w < 200:
        return img_bgr

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.dilate(edges, kernel, iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    page_area = h * w
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        area = cv2.contourArea(contour)
        if area < page_area * 0.25:
            break
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.025 * peri, True)
        if len(approx) != 4:
            continue

        rect = _order_quad_points(approx)
        tl, tr, br, bl = rect
        width_a = np.linalg.norm(br - bl)
        width_b = np.linalg.norm(tr - tl)
        height_a = np.linalg.norm(tr - br)
        height_b = np.linalg.norm(tl - bl)
        target_w = int(max(width_a, width_b))
        target_h = int(max(height_a, height_b))
        if target_w < w * 0.35 or target_h < h * 0.35:
            continue

        dst = np.array([[0, 0], [target_w - 1, 0], [target_w - 1, target_h - 1], [0, target_h - 1]], dtype="float32")
        matrix = cv2.getPerspectiveTransform(rect, dst)
        return cv2.warpPerspective(img_bgr, matrix, (target_w, target_h), flags=cv2.INTER_CUBIC)

    return img_bgr


def _deskew_page(img_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.bitwise_not(gray)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 200:
        return img_bgr
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.4 or abs(angle) > 8:
        return img_bgr
    h, w = img_bgr.shape[:2]
    matrix = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(img_bgr, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _prepare_page_bgr(img_bgr: np.ndarray) -> np.ndarray:
    img_bgr = _auto_crop_document(img_bgr)
    img_bgr = _resize_for_ocr(img_bgr)
    img_bgr = _deskew_page(img_bgr)
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))
    enhanced = cv2.cvtColor(cv2.merge((clahe.apply(l_ch), a_ch, b_ch)), cv2.COLOR_LAB2BGR)
    return enhanced


def _assess_image_quality(img_bgr: np.ndarray) -> dict:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    brightness = float(np.mean(gray))
    contrast = float(np.std(gray))
    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    h, w = gray.shape[:2]

    warnings: list[str] = []
    if min(h, w) < 900:
        warnings.append("Ảnh có độ phân giải thấp, nên chụp gần hơn hoặc dùng PDF scan.")
    if blur_score < 45:
        warnings.append("Ảnh có dấu hiệu bị mờ/rung tay.")
    if brightness < 75:
        warnings.append("Ảnh hơi tối, nên quét nơi đủ sáng.")
    if brightness > 215:
        warnings.append("Ảnh quá sáng, chữ có thể bị mất nét.")
    if contrast < 28:
        warnings.append("Độ tương phản thấp, chữ và nền chưa tách rõ.")

    return {
        "width": w,
        "height": h,
        "brightness": round(brightness, 1),
        "contrast": round(contrast, 1),
        "blur_score": round(blur_score, 1),
        "warnings": warnings,
    }


def _ocr_line_results_from_bgr(img_bgr: np.ndarray) -> list[dict]:
    img_bgr = _prepare_page_bgr(img_bgr)
    crops = _detect_line_crops(img_bgr)
    if not crops:
        logger.warning("handwriting_ocr: không phát hiện được dòng nào trong ảnh")
        return []

    predictor = _get_predictor()
    lines: list[dict] = []
    for idx, crop in enumerate(crops):
        crop_preview = _normalize_line_crop(crop)
        result = _ocr_best_result(crop_preview, predictor)
        text = result["text"]
        if text:
            lines.append({
                "index": idx + 1,
                "text": text,
                "confidence": round(result["confidence"], 4),
                "crop_image": _image_to_data_uri(crop_preview),
            })

    logger.info("handwriting_ocr: đọc được %d dòng", len(lines))
    return lines


def _detect_line_crops(img_bgr: np.ndarray, min_width_ratio: float = 0.05) -> list[Image.Image]:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 15,
    )

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, img_bgr.shape[1] // 18), 1))
    ruling = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel, iterations=1)
    binary = cv2.subtract(binary, ruling)

    small_noise = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, small_noise, iterations=1)
    h_proj = np.sum(binary, axis=1)

    in_text = False
    line_spans: list[tuple[int, int]] = []
    start = 0
    row_threshold = binary.shape[1] * 0.006

    for y, val in enumerate(h_proj):
        if not in_text and val > row_threshold:
            in_text = True
            start = y
        elif in_text and val <= row_threshold:
            in_text = False
            line_spans.append((start, y))
    if in_text:
        line_spans.append((start, len(h_proj)))

    merged_spans: list[tuple[int, int]] = []
    for y0, y1 in line_spans:
        if not merged_spans or y0 - merged_spans[-1][1] > 10:
            merged_spans.append((y0, y1))
        else:
            prev_y0, _ = merged_spans[-1]
            merged_spans[-1] = (prev_y0, y1)
    line_spans = merged_spans

    crops: list[Image.Image] = []
    min_w = int(img_bgr.shape[1] * min_width_ratio)
    for y0, y1 in line_spans:
        if (y1 - y0) < 8:
            continue
        col_proj = np.sum(binary[y0:y1, :], axis=0)
        cols = np.where(col_proj > 0)[0]
        if len(cols) < min_w:
            continue
        pad_y = max(6, int((y1 - y0) * 0.35))
        pad_x = 24
        x0 = max(0, int(cols[0]) - pad_x)
        x1 = min(img_bgr.shape[1], int(cols[-1]) + pad_x)
        crop_bgr = img_bgr[max(0, y0 - pad_y):min(img_bgr.shape[0], y1 + pad_y), x0:x1]
        crops.append(Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)))

    if len(crops) >= 2:
        return crops

    return _detect_line_crops_by_contours(img_bgr, binary, min_width_ratio=min_width_ratio) or crops


def _detect_line_crops_by_contours(
    img_bgr: np.ndarray,
    binary: np.ndarray,
    min_width_ratio: float = 0.05,
) -> list[Image.Image]:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3))
    merged = cv2.dilate(binary, kernel, iterations=2)
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes: list[tuple[int, int, int, int]] = []
    img_h, img_w = img_bgr.shape[:2]
    min_w = int(img_w * min_width_ratio)
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if h < 8 or w < 18:
            continue
        if h > img_h * 0.18:
            continue
        boxes.append((x, y, x + w, y + h))

    if not boxes:
        return []

    boxes.sort(key=lambda b: (b[1] + b[3]) / 2)
    groups: list[list[tuple[int, int, int, int]]] = []
    for box in boxes:
        y_mid = (box[1] + box[3]) / 2
        if not groups:
            groups.append([box])
            continue
        group = groups[-1]
        group_mid = sum((b[1] + b[3]) / 2 for b in group) / len(group)
        group_h = max(b[3] - b[1] for b in group)
        if abs(y_mid - group_mid) <= max(14, group_h * 0.75):
            group.append(box)
        else:
            groups.append([box])

    crops: list[Image.Image] = []
    for group in groups:
        x0 = max(0, min(b[0] for b in group) - 24)
        y0 = max(0, min(b[1] for b in group) - 8)
        x1 = min(img_w, max(b[2] for b in group) + 24)
        y1 = min(img_h, max(b[3] for b in group) + 8)
        if x1 - x0 < min_w or y1 - y0 < 8:
            continue
        crop_bgr = img_bgr[y0:y1, x0:x1]
        crops.append(Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)))

    return crops



def _decode_image_bgr(image_bytes: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def _ocr_line_results(image_bytes: bytes) -> list[dict]:
    return _ocr_line_results_from_bgr(_decode_image_bgr(image_bytes))


def _is_pdf(filename: str = "", content_type: str = "") -> bool:
    return (content_type or "").lower().startswith("application/pdf") or (filename or "").lower().endswith(".pdf")


def _ocr_pdf_line_results(pdf_bytes: bytes, max_pages: int = 8, dpi: int = 300) -> list[dict]:
    import fitz

    lines: list[dict] = []
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_index in range(min(max_pages, doc.page_count)):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img_rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                img_rgb = cv2.cvtColor(img_rgb, cv2.COLOR_RGBA2RGB)
            img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
            page_lines = _ocr_line_results_from_bgr(img_bgr)
            for line in page_lines:
                line = dict(line)
                line["page"] = page_index + 1
                line["index"] = len(lines) + 1
                lines.append(line)

    return lines


def _pdf_quality_summary(pdf_bytes: bytes, max_pages: int = 3, dpi: int = 150) -> dict:
    import fitz

    page_qualities: list[dict] = []
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_index in range(min(max_pages, doc.page_count)):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img_rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                img_rgb = cv2.cvtColor(img_rgb, cv2.COLOR_RGBA2RGB)
            img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
            quality = _assess_image_quality(img_bgr)
            quality["page"] = page_index + 1
            page_qualities.append(quality)

    warnings: list[str] = []
    for quality in page_qualities:
        warnings.extend(f"Trang {quality['page']}: {msg}" for msg in quality.get("warnings", []))
    return {"pages": page_qualities, "warnings": warnings}



_DATE_RE = re.compile(
    r"\b(\d{1,2})\s*[/\-\.]\s*(\d{1,2})(?:\s*[/\-\.]\s*(\d{2,4}))?\b"
)
_DATE_OCR_RE = re.compile(
    r"\b([\dOoIlLSsB]{1,2})\s*[/\-\.]\s*([\dOoIlLSsB]{1,2})(?:\s*[/\-\.]\s*([\dOoIlLSsB]{2,4}))?\b"
)
_TIME_RE = re.compile(r"\b(\d{1,2})\s*[hH:]\s*(\d{0,2})\b")
_NUMBER_RE = re.compile(r"[\dOoIlLSsB,\.]{2,}")
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
_NUM_TOKEN_RE = r"([\dOoIlLSsB,\.]+(?:\s*[lL])?)"
_FUEL_FOLD_RE = re.compile(r"\b(?:dau|lit|nhien\s*lieu|nl)\b\s*[:\-]?\s*" + _NUM_TOKEN_RE, re.IGNORECASE)
_REVENUE_FOLD_RE = re.compile(
    r"\b(?:cuoc|tien|gia|phi|thu\s*nhap|doanh\s*thu|dt)\b\s*[:\-]?\s*" + _NUM_TOKEN_RE,
    re.IGNORECASE,
)
_DISTANCE_FOLD_RE = re.compile(r"\b(?:km|quang\s*duong|duong)\b\s*[:\-]?\s*" + _NUM_TOKEN_RE, re.IGNORECASE)
_OCR_DIGIT_TRANS = str.maketrans({
    "O": "0", "o": "0",
    "I": "1", "l": "1", "L": "1",
    "S": "5", "s": "5",
    "B": "8",
})


def _fold_text(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d").replace("Đ", "D")


def _normalize_ocr_digits(text: str) -> str:
    return (text or "").translate(_OCR_DIGIT_TRANS)


def _parse_number(s: str) -> Optional[float]:
    s = (s or "").strip()
    if re.search(r"\d\s*[lL]\s*$", s):
        s = re.sub(r"\s*[lL]\s*$", "", s)
    s = _normalize_ocr_digits(s).strip()
    s = re.sub(r"[^\d,\.]", "", s)
    if not s:
        return None
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif "." in s and re.fullmatch(r"\d{1,4}\.\d{1,2}", s):
        pass
    else:
        s = s.replace(".", "")
    if len(s) > 32 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", s):
        return None
    value = float(s)
    return value if math.isfinite(value) else None


def _parse_date(line: str, year_hint: int = None) -> Optional[str]:
    m = _DATE_OCR_RE.search(line)
    if not m:
        return None
    day, month = int(_normalize_ocr_digits(m.group(1))), int(_normalize_ocr_digits(m.group(2)))
    year = int(_normalize_ocr_digits(m.group(3))) if m.group(3) else (year_hint or datetime.now().year)
    if year < 100:
        year += 2000
    if not (1 <= year <= 9999 and 1 <= month <= 12):
        return None
    if not (1 <= day <= calendar.monthrange(year, month)[1]):
        return None
    t = _TIME_RE.search(line)
    hour = int(t.group(1)) if t else 0
    minute = int(t.group(2)) if t and t.group(2) else 0
    dt = datetime(year, month, day, min(hour, 23), min(minute, 59))
    return dt.isoformat(timespec="minutes")


_ISO_DATETIME_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})"
)
_DECIMAL_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")


def _validated_datetime(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    match = _ISO_DATETIME_RE.fullmatch(value)
    if not match:
        return None
    year, month, day, hour, minute = (int(part) for part in match.groups())
    if not (1 <= year <= 9999 and 1 <= month <= 12):
        return None
    if not (1 <= day <= calendar.monthrange(year, month)[1]):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return datetime(year, month, day, hour, minute)


def _validated_number(value: object) -> Optional[float]:
    if isinstance(value, int) and not isinstance(value, bool):
        return float(value) if abs(value) <= 1_000_000_000_000_000 else None
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > 1_000_000_000_000_000:
            return None
        return float(value)
    if isinstance(value, str) and len(value) <= 32 and _DECIMAL_RE.fullmatch(value):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    return None


_ROUTE_METRIC_RE = re.compile(
    r"\b(?:dau|lit|nhien\s*lieu|nl|cuoc|tien|gia|phi|thu\s*nhap|doanh\s*thu|dt|km|quang\s*duong|duong)\b"
    r"\s*[:\-]?\s*[\dOoIlLSsB,\.]",
    re.IGNORECASE,
)


def _route_text_before_metrics(line: str) -> str:
    text = _DATE_OCR_RE.sub("", line or "")
    text = _TIME_RE.sub("", text)
    folded = _fold_text(text).lower()
    marker = _ROUTE_METRIC_RE.search(folded)
    if marker:
        text = text[:marker.start()]
    return re.sub(r"^\s*(?:ngay|date)\s+", "", text, flags=re.IGNORECASE).strip(" -/.")


def _strip_trailing_compact_metrics(text: str) -> str:
    num = r"[\dOoIlLSsB]+(?:[\.,][\dOoIlLSsB]+)*(?:\s*(?:km|l|lit))?"
    folded = _fold_text(text or "").lower()
    match = re.search(r"\s+" + num + r"(?:\s+" + num + r"){1,}\s*$", folded, re.IGNORECASE)
    if match:
        return text[:match.start()].strip()
    return text


def _clean_place(text: str) -> Optional[str]:
    if not text:
        return None
    text = _strip_trailing_compact_metrics(text)
    text = re.sub(r"^\s*[\d\s]{1,6}\s+", "", text)
    text = re.sub(r"^\s*(?:tu|từ|di|đi|den|đến|toi|tới|giao|lay|lấy)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*(?:tu|từ|di|đi|den|đến|toi|tới|giao|lay|lấy)\s*$", "", text, flags=re.IGNORECASE)
    text = text.strip(" .-,ô:")
    if len(text) <= 1:
        return None
    return _correct_place(text)


def _parse_route(line: str) -> tuple[Optional[str], Optional[str]]:
    line_clean = _route_text_before_metrics(line)
    parts = _ARROW_RE.split(line_clean)
    if len(parts) >= 2:
        return _clean_place(parts[0]), _clean_place(parts[-1])

    folded = _fold_text(line_clean).lower()
    marker = re.search(r"\s+(?:di|den|toi|giao)\s+", folded)
    if marker:
        return _clean_place(line_clean[:marker.start()]), _clean_place(line_clean[marker.end():])

    m = re.search(r"^\s*(?:tu|lay)\s+(.+?)\s+(?:den|toi|giao)\s+(.+?)\s*$", folded)
    if m:
        return _clean_place(m.group(1)), _clean_place(m.group(2))
    return None, None


def _extract_unlabeled_trip_numbers(line: str) -> dict:
    text = _DATE_OCR_RE.sub(" ", line or "")
    text = _TIME_RE.sub(" ", text)
    token_re = re.compile(r"\b[\dOoIlLSsB]+(?:[\.,][\dOoIlLSsB]+)*(?:\s*(?:km|l|lit))?\b", re.IGNORECASE)
    tokens = []
    for match in token_re.finditer(_fold_text(text).lower()):
        raw = match.group(0).strip()
        value = _parse_number(raw)
        if value is None:
            continue
        tokens.append({"raw": raw, "value": value})

    result: dict[str, float] = {}
    plain_values: list[float] = []
    for token in tokens:
        raw = token["raw"]
        value = token["value"]
        if raw.endswith("km"):
            result.setdefault("distance_km", value)
        elif raw.endswith(("l", "lit")) and value <= 500:
            result.setdefault("fuel_liters", value)
        elif value >= 10_000:
            result.setdefault("freight_revenue", value)
        else:
            plain_values.append(value)

    if "fuel_liters" not in result and plain_values:
        first = plain_values[0]
        if 0 < first <= 500:
            result["fuel_liters"] = first
    if "distance_km" not in result and len(plain_values) >= 2:
        last = plain_values[-1]
        if 0 < last <= 2500:
            result["distance_km"] = last
    return result




def _extract_weight(line: str) -> Optional[float]:
    m = _WEIGHT_RE.search(line)
    if not m:
        return None
    val = _parse_number(m.group(1))
    if val is None:
        return None
    raw = m.group(0).lower()
    if "tạ" in raw:
        val = val / 10.0
    elif "kg" in raw:
        val = val / 1000.0
    return val


def _lines_to_trips(lines: list[str]) -> list[dict]:
    trips: list[dict] = []
    current: Optional[dict] = None
    year_hint = datetime.now().year

    def _flush():
        if current and current.get("departure_time"):
            trips.append(dict(current))

    def _enrich(line: str, trip: dict, overwrite: bool = False):
        folded_line = _fold_text(line)
        origin, dest = _parse_route(line)
        if origin and (overwrite or not trip.get("origin")):
            trip["origin"] = origin
        if dest and (overwrite or not trip.get("destination")):
            trip["destination"] = dest

        fm = _FUEL_FOLD_RE.search(folded_line) or _FUEL_RE.search(line)
        if fm and not trip.get("fuel_liters"):
            trip["fuel_liters"] = _parse_number(fm.group(1))

        rm = _REVENUE_FOLD_RE.search(folded_line) or _REVENUE_RE.search(line)
        if rm and not trip.get("freight_revenue"):
            trip["freight_revenue"] = _parse_number(rm.group(1))

        dm = _DISTANCE_FOLD_RE.search(folded_line)
        if dm and not trip.get("distance_km"):
            trip["distance_km"] = _parse_number(dm.group(1))

        inferred = _extract_unlabeled_trip_numbers(line)
        if not trip.get("fuel_liters") and inferred.get("fuel_liters") is not None:
            trip["fuel_liters"] = inferred["fuel_liters"]
        if not trip.get("freight_revenue") and inferred.get("freight_revenue") is not None:
            trip["freight_revenue"] = inferred["freight_revenue"]
        if not trip.get("distance_km") and inferred.get("distance_km") is not None:
            trip["distance_km"] = inferred["distance_km"]

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

        if dep and current is not None and _RETURN_KEYWORD_RE.search(line):
            if not current.get("return_time"):
                current["return_time"] = dep
            continue

        if dep:
            _flush()
            current = {"departure_time": dep}
            _enrich(line, current)

        elif current is not None:
            if _RETURN_KEYWORD_RE.search(line):
                ret = _parse_date(line, year_hint)
                if ret and not current.get("return_time"):
                    current["return_time"] = ret
                    continue

            _enrich(line, current)

            has_arrow = bool(_ARROW_RE.search(line))
            has_number = bool(_NUMBER_RE.search(line))
            if not has_arrow and not has_number and len(line) > 2:
                if not current.get("note") and line != current.get("departure_time"):
                    current["note"] = line

    _flush()
    return trips


_TRIP_FIELDS = (
    "departure_time", "return_time", "origin", "destination",
    "fuel_liters", "fuel_price_per_liter", "freight_revenue",
    "distance_km", "note",
)


def _public_trip(candidate: dict) -> dict:
    return {k: candidate.get(k) for k in _TRIP_FIELDS if candidate.get(k) not in (None, "")}


def _set_candidate_field(candidate: dict, field: str, value, line_item: dict, overwrite: bool = False):
    if value in (None, ""):
        return
    if not overwrite and candidate.get(field) not in (None, ""):
        return
    candidate[field] = value
    candidate.setdefault("_field_confidence", {})[field] = line_item.get("confidence", 1.0)
    candidate.setdefault("_field_sources", {})[field] = line_item.get("text", "")


def _validate_candidate(candidate: dict) -> list[dict]:
    warnings: list[dict] = []

    def add(level: str, field: str, message: str):
        warnings.append({"level": level, "field": field, "message": message})

    confs = candidate.get("_field_confidence", {})
    for field, conf in confs.items():
        if conf < 0.62:
            add("danger", field, "OCR confidence thấp, cần kiểm tra lại.")
        elif conf < 0.78:
            add("warning", field, "OCR chưa chắc chắn, nên rà lại.")

    dep = candidate.get("departure_time")
    ret = candidate.get("return_time")
    dep_dt = None
    if not dep:
        add("danger", "departure_time", "Thiếu ngày xuất phát.")
    else:
        dep_dt = _validated_datetime(dep)
        if dep_dt is None:
            add("danger", "departure_time", "Ngày xuất phát không hợp lệ.")
        else:
            now = datetime.now()
            if dep_dt < now - timedelta(days=730) or dep_dt > now + timedelta(days=90):
                add("warning", "departure_time", "Ngày xuất phát nằm ngoài khoảng thường gặp.")

    if ret:
        ret_dt = _validated_datetime(ret)
        if ret_dt is None:
            add("danger", "return_time", "Ngày về không hợp lệ.")
        elif dep_dt and ret_dt < dep_dt:
            add("danger", "return_time", "Ngày về nhỏ hơn ngày xuất phát.")

    for field in ("origin", "destination"):
        value = (candidate.get(field) or "").strip()
        if value and len(value) <= 2:
            add("warning", field, "Địa điểm quá ngắn, có thể OCR sai.")

    fuel = candidate.get("fuel_liters")
    if fuel is not None:
        fuel_val = _validated_number(fuel)
        if fuel_val is None:
            add("warning", "fuel_liters", "Số lít dầu không hợp lệ.")
        elif fuel_val <= 0 or fuel_val > 500:
            add("warning", "fuel_liters", "Số lít dầu nằm ngoài khoảng thường gặp.")

    revenue = candidate.get("freight_revenue")
    if revenue is not None:
        revenue_val = _validated_number(revenue)
        if revenue_val is None:
            add("warning", "freight_revenue", "Cước vận chuyển không hợp lệ.")
        elif revenue_val <= 0 or revenue_val > 100_000_000:
            add("warning", "freight_revenue", "Cước vận chuyển nằm ngoài khoảng thường gặp.")
        elif revenue_val < 10_000:
            add("warning", "freight_revenue", "Cước vận chuyển quá nhỏ, có thể thiếu số 0.")

    distance = candidate.get("distance_km")
    if distance is not None:
        distance_val = _validated_number(distance)
        if distance_val is None:
            add("warning", "distance_km", "Số km không hợp lệ.")
        elif distance_val <= 0 or distance_val > 2500:
            add("warning", "distance_km", "Số km nằm ngoài khoảng thường gặp.")

    if not candidate.get("origin") and not candidate.get("destination"):
        add("warning", "route", "Chưa nhận ra tuyến đường.")

    return warnings


def _lines_to_trip_candidates(line_items: list[dict]) -> list[dict]:
    candidates: list[dict] = []
    current: Optional[dict] = None
    year_hint = datetime.now().year

    def _flush():
        if not current:
            return
        current["warnings"] = _validate_candidate(current)
        current["needs_review"] = bool(current["warnings"])
        candidates.append(dict(current))

    def _enrich(item: dict, candidate: dict, overwrite: bool = False):
        line = item["text"]
        folded_line = _fold_text(line)
        origin, dest = _parse_route(line)
        _set_candidate_field(candidate, "origin", origin, item, overwrite)
        _set_candidate_field(candidate, "destination", dest, item, overwrite)

        fm = _FUEL_FOLD_RE.search(folded_line) or _FUEL_RE.search(line)
        if fm:
            _set_candidate_field(candidate, "fuel_liters", _parse_number(fm.group(1)), item, overwrite)

        rm = _REVENUE_FOLD_RE.search(folded_line) or _REVENUE_RE.search(line)
        if rm:
            _set_candidate_field(candidate, "freight_revenue", _parse_number(rm.group(1)), item, overwrite)

        dm = _DISTANCE_FOLD_RE.search(folded_line)
        if dm:
            _set_candidate_field(candidate, "distance_km", _parse_number(dm.group(1)), item, overwrite)

        inferred = _extract_unlabeled_trip_numbers(line)
        for field in ("fuel_liters", "freight_revenue", "distance_km"):
            if inferred.get(field) is not None:
                _set_candidate_field(candidate, field, inferred[field], item, overwrite)

    for item in line_items:
        line = item["text"]
        dep = _parse_date(line, year_hint)

        if dep and current is not None and _RETURN_KEYWORD_RE.search(line):
            _set_candidate_field(current, "return_time", dep, item)
            current.setdefault("source_lines", []).append(item)
            continue

        if dep:
            _flush()
            current = {"source_lines": [], "_field_confidence": {}, "_field_sources": {}}
            _set_candidate_field(current, "departure_time", dep, item, overwrite=True)
            current["source_lines"].append(item)
            _enrich(item, current)
            continue

        if current is None:
            continue

        current.setdefault("source_lines", []).append(item)
        _enrich(item, current)

        has_arrow = bool(_ARROW_RE.search(line))
        has_number = bool(_NUMBER_RE.search(line))
        if not has_arrow and not has_number and len(line) > 2:
            _set_candidate_field(current, "note", line, item)

    _flush()
    for idx, candidate in enumerate(candidates, start=1):
        candidate["index"] = idx
    return candidates



def extract_trips_from_image(image_bytes: bytes) -> list[dict]:
    lines = _ocr_line_results(image_bytes)
    if not lines:
        return []
    trips = [_public_trip(t) for t in _lines_to_trip_candidates(lines)]
    logger.info("handwriting_ocr: parse được %d chuyến từ %d dòng", len(trips), len(lines))
    return trips


def extract_trips_from_document(file_bytes: bytes, filename: str = "", content_type: str = "") -> list[dict]:
    if _is_pdf(filename, content_type):
        lines = _ocr_pdf_line_results(file_bytes)
        if not lines:
            return []
        trips = [_public_trip(t) for t in _lines_to_trip_candidates(lines)]
        logger.info("handwriting_ocr: parse được %d chuyến từ %d dòng PDF", len(trips), len(lines))
        return trips
    return extract_trips_from_image(file_bytes)


def extract_trip_candidates_from_document(file_bytes: bytes, filename: str = "", content_type: str = "") -> dict:
    if _is_pdf(filename, content_type):
        quality = _pdf_quality_summary(file_bytes)
        lines = _ocr_pdf_line_results(file_bytes)
    else:
        img_bgr = _decode_image_bgr(file_bytes)
        quality = _assess_image_quality(img_bgr)
        lines = _ocr_line_results_from_bgr(img_bgr)

    candidates = _lines_to_trip_candidates(lines)
    return {
        "lines": lines,
        "candidates": candidates,
        "line_count": len(lines),
        "candidate_count": len(candidates),
        "quality": quality,
        "quality_warnings": quality.get("warnings", []),
    }
