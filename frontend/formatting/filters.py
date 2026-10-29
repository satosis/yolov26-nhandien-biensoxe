import datetime
from decimal import Decimal


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)) or hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def format_number(value, default: str = "0") -> str:
    """Format numeric value into Vietnamese standard format with dot thousand separators (e.g., 1.234.567)."""
    if value is None or value == "":
        return default
    try:
        clean = str(value).strip().replace(" ", "").replace(",", "")
        d = Decimal(clean)
        if d == d.to_integral():
            val_int = int(d)
            return f"{val_int:,}".replace(",", ".")
        else:
            parts = f"{d:f}".split(".")
            int_part = f"{int(parts[0]):,}".replace(",", ".")
            dec_part = parts[1].rstrip("0")
            return f"{int_part},{dec_part}" if dec_part else int_part
    except Exception:
        return str(value)


def format_currency(value, symbol: str = "đ", default: str = "0 đ") -> str:
    """Format numeric value as currency (e.g., 1.234.567 đ)."""
    if value is None or value == "":
        return default
    num_str = format_number(value, default="0")
    if num_str == "0":
        return f"0 {symbol}".strip()
    return f"{num_str} {symbol}".strip()


def setup_jinja2_filters(env):
    """Register custom formatting filters and globals to a Jinja2 Environment."""
    env.filters["format_number"] = format_number
    env.filters["format_currency"] = format_currency
    env.filters["format_vnd"] = format_currency
    env.filters["money"] = format_currency
    env.globals["format_number"] = format_number
    env.globals["format_currency"] = format_currency
    env.globals["format_vnd"] = format_currency
    env.globals["money"] = format_currency
