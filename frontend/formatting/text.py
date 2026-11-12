"""Chuẩn hóa chữ tiếng Việt để so khớp: chữ thường, bỏ dấu, đ→d, gộp khoảng trắng."""
import re
import unicodedata


def strip_marks(value) -> str:
    """Lower case without the accent marks (NFKD); unlike fold_text, đ and spacing are left as written."""
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def fold_text(value) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn").replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


def normalize_invoice_number(value) -> str:
    """Invoice numbers compare without leading zeros ("00000084" = "84"); lettered ones compare case-insensitively."""
    s = str(value or "").strip()
    if not s:
        return ""
    if s.isdigit():
        return s.lstrip("0") or "0"
    return s.casefold()
