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


# Money as people say it: a number and its unit ("2,5 triệu", "3tr", "800 nghìn", "500k", "3.500.000đ"), parts
# added up ("2 triệu 800 ngàn"), a bare number after a unit giving that unit's next digits ("3 triệu 5", "3tr5"),
# "rưỡi" half the unit. With no unit, only an amount written with thousand separators ("3.500.000").
_MONEY_UNITS = {"tỷ": 10 ** 9, "tỉ": 10 ** 9, "triệu": 10 ** 6, "trieu": 10 ** 6, "tr": 10 ** 6,
                "nghìn": 10 ** 3, "ngàn": 10 ** 3, "nghin": 10 ** 3, "ngan": 10 ** 3, "k": 10 ** 3,
                "đồng": 1, "dong": 1, "vnđ": 1, "vnd": 1, "đ": 1}
# A number before one of these words counts things or measures, it is not money ("1.500 kg", "5 triệu 2 xe")
_COUNTED = ("kg", "tấn", "tan", "km", "m2", "m3", "lít", "lit", "cái", "cai", "chiếc", "chiec",
            "xe", "chuyến", "chuyen", "cont", "container", "người", "nguoi", "ngày", "ngay")
_UNIT_WORDS = "|".join(sorted(map(re.escape, _MONEY_UNITS), key=len, reverse=True))
_MONEY_PART = re.compile(rf"(?<![\w.,])(\d+(?:[.,]\d+)*)(?:\s*({_UNIT_WORDS}))?(?![^\W\d_])", re.I)
# the next part of the same amount, right after the previous one ("3 triệu| 800 ngàn", "3tr|5")
_NEXT_PART = re.compile(rf"\s*(\d+(?:[.,]\d+)*)(?:\s*({_UNIT_WORDS}))?(?![^\W\d_])", re.I)
_COUNTED_AFTER = re.compile(r"\s*(?:" + "|".join(_COUNTED) + r")\b", re.I)
_HALF_AFTER = re.compile(r"\s*(?:rưỡi|ruoi)\b", re.I)


class _Money:
    """One amount found in a text: where it is written (start/end/group, like a regex match) and its đồng value."""

    def __init__(self, text: str, start: int, end: int, amount: int):
        self._text, self._start, self._end, self.amount = text, start, end, amount

    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end

    def group(self, *_args) -> str:
        return self._text[self._start:self._end]


def _unit_of(part: re.Match) -> int | None:
    return _MONEY_UNITS.get((part.group(2) or "").lower())


def _amounts(text: str):
    position = 0
    while part := _MONEY_PART.search(text, position):
        position = part.end()
        unit = _unit_of(part)
        if unit is None:
            if _GROUPED.fullmatch(part.group(1)) and not _COUNTED_AFTER.match(text, part.end()):
                yield _Money(text, part.start(), part.end(), int(parse_vnd(part.group(1))))
            continue
        amount, end, last_unit = parse_vnd(part.group(1)) * unit, part.end(), unit
        while following := _NEXT_PART.match(text, end):
            following_unit = _unit_of(following)
            if following_unit is None:
                digits = following.group(1)
                if digits.isdigit() and len(digits) <= 3 and last_unit >= 1000 \
                        and not _COUNTED_AFTER.match(text, following.end()):
                    amount += int(digits.ljust(3, "0")) * last_unit // 1000
                    end = following.end()
                break
            if following_unit >= last_unit:
                break
            amount += parse_vnd(following.group(1)) * following_unit
            end, last_unit = following.end(), following_unit
        half = _HALF_AFTER.match(text, end)
        if half and last_unit >= 1000:
            amount += last_unit // 2
            end = half.end()
        position = end
        yield _Money(text, part.start(), end, int(round(amount)))


class _MoneyReader:
    """search/finditer like a compiled pattern, so callers read every way of writing an amount the same way."""

    def search(self, text: str):
        return next(_amounts(str(text or "")), None)

    def finditer(self, text: str):
        return _amounts(str(text or ""))


MILLION_PRICE = _MoneyReader()


def million_price(found: _Money) -> int:
    """The đồng amount of a MILLION_PRICE result."""
    return found.amount

