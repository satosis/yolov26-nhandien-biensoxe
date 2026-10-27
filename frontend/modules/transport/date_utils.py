import re
from datetime import date

MONTH_RE = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\Z")


def month_bounds(month: str) -> tuple[date, date]:
    if not month:
        raise ValueError("Tháng không hợp lệ.")
    month = month.strip()
    if MONTH_RE.fullmatch(month):
        year, number = map(int, month.split("-"))
        start = date(year, number, 1)
        end = date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)
        return start, end
    y_match = re.match(r"^(\d{4})(-all)?$", month)
    if y_match:
        year = int(y_match.group(1))
        return date(year, 1, 1), date(year + 1, 1, 1)
    if month == "all":
        return date(2000, 1, 1), date(2100, 1, 1)
    raise ValueError("Tháng không hợp lệ.")
