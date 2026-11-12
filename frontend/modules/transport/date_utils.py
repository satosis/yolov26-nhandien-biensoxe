import calendar
import re
from datetime import date, datetime

MONTH_RE = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\Z")
QUARTER_RE = re.compile(r"^(\d{4})-[qQ]([1-4])$")


def month_bounds(month: str) -> tuple[date, date]:
    if not month:
        raise ValueError("Tháng không hợp lệ.")
    month = month.strip()
    if MONTH_RE.fullmatch(month):
        year, number = map(int, month.split("-"))
        start = date(year, number, 1)
        end = date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)
        return start, end
    q_match = QUARTER_RE.match(month)
    if q_match:
        year = int(q_match.group(1))
        q = int(q_match.group(2))
        start_month = 3 * q - 2
        start = date(year, start_month, 1)
        end = date(year + 1, 1, 1) if q == 4 else date(year, 3 * q + 1, 1)
        return start, end
    y_match = re.match(r"^(\d{4})(-all)?$", month)
    if y_match:
        year = int(y_match.group(1))
        return date(year, 1, 1), date(year + 1, 1, 1)
    if month == "all":
        return date(2000, 1, 1), date(2100, 1, 1)
    raise ValueError("Tháng không hợp lệ.")


def _iso_date(value) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date() if isinstance(value, str) and value.strip() else None
    except ValueError:
        return None


def resolve_period_dates(year, period, date_from, date_to, *, default: str = "all") -> tuple[int, str, date, date]:
    """Year, period name and date range of a report filter.

    `period` is today (year to date), q1..q4, m1..m12, all/year or custom (then date_from..date_to counts);
    anything else uses the dates when both are valid, otherwise `default` ("all" = whole year, "today" = year to date).
    """
    today = date.today()
    selected_year = int(year) if year and str(year).isdigit() and 2000 <= int(year) <= 2100 else today.year
    d_from, d_to = _iso_date(date_from), _iso_date(date_to)
    p = period.lower().strip() if isinstance(period, str) else ""

    if p and p != "custom":
        if p == "today":
            return selected_year, "today", date(selected_year, 1, 1), min(today, date(selected_year, 12, 31))
        if p in ("q1", "q2", "q3", "q4"):
            first = 3 * int(p[1]) - 2
            return selected_year, p, date(selected_year, first, 1), date(selected_year, first + 2, calendar.monthrange(selected_year, first + 2)[1])
        if p.startswith("m") and p[1:].isdigit() and 1 <= int(p[1:]) <= 12:
            month = int(p[1:])
            return selected_year, p, date(selected_year, month, 1), date(selected_year, month, calendar.monthrange(selected_year, month)[1])
        if p in ("all", "year"):
            return selected_year, "all", date(selected_year, 1, 1), date(selected_year, 12, 31)

    if d_from and d_to:
        return selected_year, "custom", d_from, d_to
    if default == "today":
        return selected_year, "today", date(selected_year, 1, 1), min(today, date(selected_year, 12, 31))
    return selected_year, "all", date(selected_year, 1, 1), date(selected_year, 12, 31)
