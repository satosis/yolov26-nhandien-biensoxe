import logging

from backend.services.crypto_service import (
    decrypt_value,
    encrypt_value,
    is_encrypted,
    is_sensitive_key,
)
from backend.services.database import db

logger = logging.getLogger(__name__)


def get_app_setting(key: str, default: str = "") -> str:
    """Retrieve an application setting, decrypting if it is encrypted or sensitive."""
    row = db.execute_query("SELECT value FROM app_settings WHERE key = %s", (key,), fetch=True)
    if not row or row.get("value") is None:
        return default
    val = row["value"]
    if is_encrypted(val) or is_sensitive_key(key):
        return decrypt_value(val)
    return val


def get_all_app_settings() -> dict:
    """Retrieve all application settings, decrypting any sensitive/encrypted values."""
    rows = db.execute_query("SELECT key, value FROM app_settings ORDER BY key", fetchall=True)
    result = {}
    for r in (rows or []):
        k = r["key"]
        v = r["value"]
        if is_encrypted(v) or is_sensitive_key(k):
            result[k] = decrypt_value(v)
        else:
            result[k] = v
    return result


def set_app_setting(key: str, value: str) -> bool:
    """Save a single application setting, automatically encrypting if sensitive."""
    return set_many_app_settings({key: value})


def set_many_app_settings(settings: dict) -> bool:
    """Save multiple application settings, automatically encrypting sensitive credentials."""
    if not settings:
        return True

    processed: dict = {}
    for k, v in settings.items():
        if v is None:
            processed[k] = ""
        else:
            str_v = str(v)
            if is_sensitive_key(k) and str_v:
                processed[k] = encrypt_value(str_v)
            else:
                processed[k] = str_v

    placeholders = ", ".join(["(%s, %s)"] * len(processed))
    params = tuple(value for pair in processed.items() for value in pair)
    saved = bool(db.execute_query(
        f"INSERT INTO app_settings (key, value) VALUES {placeholders} "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
        params,
        commit=True,
    ))
    if not saved:
        logger.error("App settings write affected no row (%d keys)", len(settings))
    return saved
