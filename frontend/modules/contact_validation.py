"""Validate phone numbers and emails typed into staff/user forms."""

import re

_PHONE_RE = re.compile(r"(?:0\d{9,10}|(?:\+?84)\d{9,10})")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def clean_phone(value: str | None) -> str:
    """'' stays ''; spaces/dots/dashes are dropped; must be a VN number (0xxxxxxxxx(x) or +84/84...)."""
    raw = re.sub(r"[\s.\-()]", "", str(value or ""))
    if raw and not _PHONE_RE.fullmatch(raw):
        raise ValueError("Số điện thoại không hợp lệ (VD 0986565666 hoặc +84986565666).")
    return raw


def clean_email(value: str | None) -> str:
    email = str(value or "").strip()
    if email and not _EMAIL_RE.fullmatch(email):
        raise ValueError("Email không hợp lệ (VD ten@congty.vn).")
    return email
