"""Nickname storage shared by account management and Zalo payroll matching."""

from functools import lru_cache

from backend.services.database import db




def clean_nickname(value: str | None) -> str:
    nickname = " ".join(str(value or "").strip().split())
    if len(nickname) > 120:
        raise ValueError("Nickname không được dài quá 120 ký tự.")
    return nickname
