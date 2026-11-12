"""Parse VND amounts typed by users, pasted from Excel, or read back from the database."""

import re
from decimal import Decimal, InvalidOperation

_GROUPED = re.compile(r"-?\d{1,3}(?:([.,])\d{3})(?:\1\d{3})*")         # 1.500.000 | 1,500,000
_GROUPED_DEC = re.compile(r"-?\d{1,3}(?:([.,])\d{3})(?:\1\d{3})*[.,]\d{1,2}")  # 1.500.000,50 | 1,500,000.50
_PLAIN = re.compile(r"-?\d+(?:[.,]\d{1,2})?")                          # 1500000 | 1500000.00 | 1500000,5


def parse_vnd(value) -> Decimal:
    """Raise ValueError when the text is not a recognisable amount."""
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        number = Decimal(str(value))
    else:
        text = re.sub(r"\s+|đ$|vnđ$|vnd$", "", str(value or "").strip(), flags=re.I)
        if not text:
            raise ValueError("Chưa nhập số tiền.")
        if _GROUPED.fullmatch(text):
            text = re.sub(r"[.,]", "", text)
        elif _GROUPED_DEC.fullmatch(text):
            separator = _GROUPED_DEC.fullmatch(text).group(1)
            text = text.replace(separator, "").replace(",", ".")
        elif _PLAIN.fullmatch(text):
            text = text.replace(",", ".")
        else:
            raise ValueError(f"Số tiền không hợp lệ: {value}")
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"Số tiền không hợp lệ: {value}") from exc
    if not number.is_finite():
        raise ValueError(f"Số tiền không hợp lệ: {value}")
    return number


def format_vnd_rounded(value) -> str:
    """Số tiền làm tròn tới đồng, chấm ngăn nghìn: 1.234.567 đ; giá trị không đọc được → '0 đ'."""
    try:
        return f"{int(round(Decimal(str(value or 0)))):,}".replace(",", ".") + " đ"
    except Exception:
        return "0 đ"


# "2,5 triệu", "3tr", "2 triệu 800 ngàn": a price written with "triệu", optionally followed by thousands;
# "500k", "800 nghìn": a price under a million
MILLION_PRICE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:triệu|tr\b)(?:\s*(\d{1,3})\s*(?:ngàn|nghìn|ngan|nghin|k)\b)?"
    r"|\b(\d{2,4})\s*(?:ngàn|nghìn|ngan|nghin|k)\b", re.I)


def million_price(found: re.Match) -> int:
    """The đồng amount of a MILLION_PRICE match: "2 triệu 800 ngàn" -> 2_800_000, "500k" -> 500_000."""
    if found.group(3):
        return int(found.group(3)) * 1_000
    return round(float(found.group(1).replace(",", ".")) * 1_000_000) + int(found.group(2) or 0) * 1_000

