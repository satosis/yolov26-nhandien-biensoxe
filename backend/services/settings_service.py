from backend.services.database import db

def get_app_setting(key: str, default: str = "") -> str:
    row = db.execute_query("SELECT value FROM app_settings WHERE key = %s", (key,), fetch=True)
    return row["value"] if row and row["value"] is not None else default

def get_all_app_settings() -> dict:
    rows = db.execute_query("SELECT key, value FROM app_settings ORDER BY key", fetchall=True)
    return {r["key"]: r["value"] for r in rows}

def set_app_setting(key: str, value: str) -> bool:
    return set_many_app_settings({key: value})

def set_many_app_settings(settings: dict) -> bool:
    for k, v in settings.items():
        db.execute_query(
            "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
            (k, v), commit=True
        )
    return True
