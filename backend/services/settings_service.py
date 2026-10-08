"""App settings management service — key/value configuration stored in PostgreSQL."""
import logging

logger = logging.getLogger(__name__)


def get_app_setting(db, key: str, default: str = None) -> str:
    """Get a single setting value from app_settings."""
    row = db.execute_query("SELECT value FROM app_settings WHERE key = %s", (key,), fetch=True)
    return row["value"] if row and row.get("value") is not None else default


def get_all_app_settings(db) -> dict:
    """Get all key/value pairs from app_settings."""
    rows = db.execute_query("SELECT key, value FROM app_settings ORDER BY key", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}


def set_app_setting(db, key: str, value: str) -> bool:
    """Upsert a single setting into app_settings."""
    return set_many_app_settings(db, {key: value})


def set_many_app_settings(db, settings: dict) -> bool:
    """Upsert multiple settings into app_settings."""
    if not settings:
        return True
    for k, v in settings.items():
        db.execute_query(
            "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
            (k, v), commit=True
        )
    return True
