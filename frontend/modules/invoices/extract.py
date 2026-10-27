"""Extract supported purchase invoice fields without persisting the upload."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from functools import lru_cache
import logging
import os
from pathlib import Path
import re
import unicodedata


logger = logging.getLogger(__name__)
_MODEL_URL = "https://vocr.vn/data/vietocr/vgg_transformer.pth"
_MODEL_BYTES = 151815373
_MODEL_PATH = Path(__file__).resolve().parents[3] / "data" / "models" / "vietocr" / "vgg_transformer.pth"


@lru_cache(maxsize=1)
def _vietocr_predictor():
    try:
        if not _MODEL_PATH.is_file() or _MODEL_PATH.stat().st_size != _MODEL_BYTES:
            import requests

            _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = _MODEL_PATH.with_suffix(".part")
            with requests.get(_MODEL_URL, stream=True, timeout=(15, 60)) as response:
                response.raise_for_status()
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
            if temporary.stat().st_size != _MODEL_BYTES:
                raise ValueError("Kích thước mô hình VietOCR không hợp lệ.")
            os.replace(temporary, _MODEL_PATH)

        from PIL import Image
        import torch
        from vietocr.tool.config import Cfg
        from vietocr.tool.predictor import Predictor

        if not hasattr(Image, "ANTIALIAS"):
            Image.ANTIALIAS = Image.Resampling.LANCZOS
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        config = Cfg.load_config_from_file(str(Path(__file__).resolve().parents[2] / "assets" / "vietocr_vgg_transformer.yml"))
        config["weights"] = str(_MODEL_PATH)
        config["device"] = "cpu"
        config["cnn"]["pretrained"] = False
        config["predictor"]["beamsearch"] = False
        return Predictor(config)
    except Exception:
        logger.exception("VietOCR is unavailable for invoice descriptions")
        return None


def _prefer_accented_description(original: str, candidate: str) -> bool:
    original = original.strip()
    candidate = candidate.strip()
    if not original or not candidate or len(candidate) > 500:
        return False
    if not any(ord(char) > 127 for char in candidate):
        return False
    if SequenceMatcher(None, _plain(original), _plain(candidate)).ratio() < 0.8:
        return False
    numbers = lambda value: re.findall(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?(?![A-Za-z0-9])", value)
    return numbers(original) == numbers(candidate)


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFD", value).replace("đ", "d").replace("Đ", "D")
    return "".join(char for char in value if not unicodedata.combining(char)).lower()


def _number(value: str) -> Decimal | None:
    raw = re.sub(r"[^0-9,.]", "", value)
    if not raw:
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".") if len(raw.rsplit(",", 1)[1]) in (1, 2) else raw.replace(",", "")
    elif raw.count(".") > 1 or ("." in raw and len(raw.rsplit(".", 1)[1]) == 3):
        raw = raw.replace(".", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


_ALIAS = r"(?:\s*\([^)]{1,40}\))?\s*[:：-]?\s*"
_OTHER_LABEL = re.compile(
    r"^(?:ma so thue|mst|tax code|dia chi|address|don vi mua|don vi ban|ten nguoi|"
    r"so hoa don|ky hieu|ngay|thue suat|hinh thuc thanh toan)\b"
)


def _label_match(line: str, labels: tuple[str, ...]):
    plain = _plain(line)
    for label in sorted(labels, key=len, reverse=True):
        if label == "so" and not re.match(r"^\s*so\s*(?:\([^)]*\))?\s*[:：-]", plain):
            continue
        match = re.search(r"\b" + re.escape(label) + r"\)?" + _ALIAS, plain)
        if match:
            return match
    return None


def _clean_value(value: str) -> str:
    value = re.sub(r"^\s*\([^)]{1,40}\)\s*[:：-]?\s*", "", value)
    return value.strip(" :：-\t()")


def _field(lines: list[str], labels: tuple[str, ...]) -> str:
    for index, line in enumerate(lines):
        match = _label_match(line, labels)
        if not match:
            continue
        value = _clean_value(line[match.end():])
        if value:
            return value
        if index + 1 < len(lines):
            candidate = _clean_value(lines[index + 1])
            if candidate and not _OTHER_LABEL.match(_plain(candidate)):
                return candidate
    return ""


def _date(text: str) -> str:
    patterns = (
        r"ngay(?:\s*\([^)]*\))?\s*(\d{1,2})\s+thang(?:\s*\([^)]*\))?\s*(\d{1,2})\s+nam(?:\s*\([^)]*\))?\s*(\d{4})",
        r"ngay\s+(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s+(\d{4})",
        r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b",
    )
    plain = _plain(text)
    for pattern in patterns:
        match = re.search(pattern, plain)
        if match:
            try:
                return date(int(match[3]), int(match[2]), int(match[1])).isoformat()
            except ValueError:
                pass
    return ""


def _party(lines: list[str], start_labels: tuple[str, ...], stop_labels: tuple[str, ...]) -> dict:
    start = next((i for i, line in enumerate(lines) if any(label in _plain(line) for label in start_labels)), None)
    if start is None:
        return {}
    end = next((i for i in range(start + 1, min(start + 12, len(lines)))
                if any(label in _plain(lines[i]) for label in stop_labels)), min(start + 12, len(lines)))
    block = lines[start:end]
    is_buyer = any("mua" in label or "buyer" in label for label in start_labels)
    if is_buyer:
        name = _field(block, ("ten don vi", "company's name", "don vi mua hang")) or _field(block, ("ten nguoi mua", "nguoi mua", "buyer"))
    else:
        name = _field(block, ("don vi ban hang", "ten don vi", "ten nguoi ban", "nguoi ban", "seller"))
    if re.search(r"\(\s*(?:buyer|buver|seller)\s*\)?", _plain(name)):
        name = ""
    tax = _field(block, ("ma so thue", "ma sothue", "mst", "tax code", "taxcode", "tax id"))
    address = _field(block, ("dia chi", "address"))
    match = re.search(r"\b\d{10}(?:-?\d{3})?\b", tax)
    return {"name": name[:255], "tax_code": match.group() if match else "", "address": address[:1000]}


def _items(lines: list[str]) -> list[dict]:
    result = []
    seen = set()
    numeric = r"[0-9][0-9.,]*"
    # Keep only rows with a quantity, unit price and line amount. A total row is never an item.
    pattern = re.compile(rf"^\s*(?:\d+\s+)?(.+?)\s+({numeric})\s+({numeric})\s+({numeric})\s*$")
    for line in lines:
        if any(word in _plain(line) for word in ("cong tien", "tong tien", "tien thue", "thue suat", "thanh toan")):
            continue
        match = pattern.match(line)
        if not match:
            continue
        description = match[1].strip()
        if len(description) < 3 or len(description) > 500 or not re.search(r"[A-Za-zÀ-ỹ]", description):
            continue
        quantity, price, amount = (_number(match[i]) for i in (2, 3, 4))
        if not quantity or price is None or amount is None or quantity <= 0:
            continue
        if abs(quantity * price - amount) > max(Decimal("2"), amount * Decimal("0.02")):
            continue
        unit = ""
        parts = description.rsplit(" ", 1)
        units = {"qua", "cai", "chiec", "bo", "hop", "goi", "chai", "binh", "thung",
                 "cay", "cuon", "to", "doi", "lan", "chuyen", "dich vu", "lit",
                 "kg", "tan", "gam", "m", "m2", "m3", "km"}
        if (len(parts) == 2 and _plain(parts[1]) in units
                and not re.search(r"\b\d+(?:[.,]\d+)?\s*$", parts[0])):
            description, unit = parts
        key = (_plain(description), quantity, price)
        if key in seen:
            continue
        seen.add(key)
        result.append({"description": description, "unit": unit, "quantity": str(quantity), "unit_price": str(price)})
        if len(result) == 50:
            break
    return result


def _invoice_page_ocr(ocr, decoded) -> list:
    """Keep native layout, mapping a prepared fallback's boxes back to the page."""
    from backend.util.ocr_pipeline import assess_image, prepare_ocr_image

    quality = assess_image(decoded)
    logger.debug("Invoice page image quality: %s", quality)
    result, _ = ocr(decoded)
    if result:
        return result
    prepared = prepare_ocr_image(decoded, profile="document")
    result, _ = ocr(prepared)
    if not result:
        return []

    scale_x = decoded.shape[1] / prepared.shape[1]
    scale_y = decoded.shape[0] / prepared.shape[0]
    mapped = []
    for item in result:
        box = item[0]
        native_box = [[point[0] * scale_x, point[1] * scale_y] for point in box] if box else box
        mapped.append([native_box, *item[1:]])
    return mapped


def extract_invoice_text(content: bytes, content_type: str) -> str:
    import fitz

    if content_type == "application/pdf":
        with fitz.open(stream=content, filetype="pdf") as document:
            if document.page_count > 8:
                raise ValueError("Hóa đơn chỉ được tối đa 8 trang.")
            text = "\n".join(page.get_text(sort=True) for page in document)
            if len(text.strip()) >= 80:
                return text
            pages = [page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png") for page in document]
    else:
        pages = [content]
    from rapidocr_onnxruntime import RapidOCR
    import cv2
    import numpy as np
    from backend.util.ocr_pipeline import prepare_ocr_image

    ocr = RapidOCR()
    chunks = []
    for image in pages:
        decoded = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError("Không đọc được ảnh hóa đơn.")
        result = _invoice_page_ocr(ocr, decoded)
        chunks.extend(str(item[1]) for item in result)
        unit_header = next((item for item in result if re.sub(r"[^a-z]", "", _plain(str(item[1]))) == "dvt"), None)
        unit_center = None
        unit_header_y = None
        if unit_header:
            unit_center = sum(point[0] for point in unit_header[0]) / len(unit_header[0])
            unit_header_y = sum(point[1] for point in unit_header[0]) / len(unit_header[0])
        rows = []
        for item in result:
            box = item[0]
            if not box:
                continue
            center_y = sum(point[1] for point in box) / len(box)
            left_x = min(point[0] for point in box)
            height = max(point[1] for point in box) - min(point[1] for point in box)
            row = next((row for row in rows if abs(row[0] - center_y) <= max(8, min(row[2], height) * 0.85)), None)
            if row is None:
                row = [center_y, [], height]
                rows.append(row)
            row[1].append((left_x, str(item[1])))
        for row_y, cells, _ in sorted(rows, key=lambda row: row[0]):
            if len(cells) > 1:
                numeric_cells = sum(bool(re.fullmatch(r"[0-9][0-9.,]*", value.strip())) for _, value in cells)
                if (unit_center is not None and unit_header_y is not None
                        and row_y > unit_header_y + 35 and numeric_cells >= 3):
                    x1 = max(0, int(unit_center - 37))
                    x2 = min(decoded.shape[1], int(unit_center + 37))
                    y1 = max(0, int(row_y - 20))
                    y2 = min(decoded.shape[0], int(row_y + 14))
                    cell_image = decoded[y1:y2, x1:x2]
                    if cell_image.size:
                        enlarged = cv2.resize(cell_image, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
                        enlarged = prepare_ocr_image(enlarged, profile="document")
                        unit_result, _ = ocr(enlarged)
                        unit_map = {"qua": "Quả", "cai": "Cái", "chiec": "Chiếc", "bo": "Bộ",
                                    "hop": "Hộp", "goi": "Gói", "chai": "Chai", "binh": "Bình",
                                    "thung": "Thùng", "lit": "Lít", "kg": "kg", "tan": "Tấn"}
                        unit = next((unit_map[_plain(str(item[1]).strip())] for item in (unit_result or [])
                                     if _plain(str(item[1]).strip()) in unit_map), "")
                        if unit:
                            cells = [(x, value) for x, value in cells if not x1 <= x <= x2]
                            cells.append((unit_center, unit))
                    description_cells = [(x, value) for x, value in cells
                                         if x < x1 and re.search(r"[A-Za-zÀ-ỹ]", value)]
                    if description_cells:
                        original_description = " ".join(value for _, value in sorted(description_cells))
                        description_left = max(0, int(min(x for x, _ in description_cells) - 4))
                        description_image = decoded[y1:y2, description_left:x1]
                        predictor = _vietocr_predictor()
                        if predictor and description_image.size:
                            try:
                                from PIL import Image

                                prepared = prepare_ocr_image(description_image, profile="document")
                                rgb = cv2.cvtColor(prepared, cv2.COLOR_BGR2RGB)
                                accented = predictor.predict(Image.fromarray(rgb))
                                if _prefer_accented_description(original_description, accented):
                                    cells = [(x, value) for x, value in cells if (x, value) not in description_cells]
                                    cells.append((description_left, accented))
                            except Exception:
                                logger.exception("Could not refine invoice item description")
                chunks.append(" ".join(value for _, value in sorted(cells)))
    return "\n".join(chunks)


def parse_invoice_text(text: str) -> dict:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Không đọc được chữ trong tệp hóa đơn.")
    seller = _party(lines, ("don vi ban hang", "ten nguoi ban", "nguoi ban", "seller"),
                    ("don vi mua hang", "ten nguoi mua", "nguoi mua", "buyer"))
    buyer = _party(lines, ("don vi mua hang", "ten nguoi mua", "nguoi mua", "buyer"),
                   ("stt", "ten hang hoa", "hang hoa, dich vu", "noi dung"))
    serial = _field(lines[:20], ("mau so - ky hieu", "ky hieu", "serial"))
    number = _field(lines[:20], ("so hoa don", "invoice no", "invoice number")) or _field(lines[:20], ("so",))
    number_match = re.search(r"\b\d{1,12}\b", number)
    vat_match = re.search(r"(?:thue suat(?:\s+gtgt)?|vat rate)" + _ALIAS + r"(\d{1,3}(?:[.,]\d{1,2})?)\s*%", _plain(text))
    result = {
        "invoice_date": _date(text),
        "serial": serial[:50],
        "invoice_number": number_match.group() if number_match else "",
        "seller": seller,
        "buyer": buyer,
        "items": _items(lines),
    }
    if vat_match:
        result["vat_percent"] = vat_match[1].replace(",", ".")
    plain = _plain(text)
    if "chuyen khoan" in plain:
        result["payment_method"] = "Chuyển khoản"
    elif "tien mat" in plain:
        result["payment_method"] = "Tiền mặt"
    return result
