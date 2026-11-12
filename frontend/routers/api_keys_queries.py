"""Truy vấn cơ sở dữ liệu của frontend.routers.api_keys_controller: ghi các khóa API vào bảng app_settings."""
import json

from backend.services.crypto_service import encrypt_value, is_sensitive_key
from backend.services.database import db


def _put_setting(cursor, key: str, value: str) -> None:
    cursor.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
        (key, value),
    )


def save_entries(removed, entries: dict, managed_names_key: str) -> None:
    """Xóa các khóa bị bỏ, ghi các khóa còn lại (khóa nhạy cảm được mã hóa) và danh sách tên đang quản lý trong một giao dịch."""
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            if removed:
                cursor.execute("DELETE FROM app_settings WHERE key = ANY(%s)", (list(removed),))
            for key, value in entries.items():
                val_to_save = encrypt_value(value) if is_sensitive_key(key) else value
                _put_setting(cursor, key, val_to_save)
            _put_setting(cursor, managed_names_key, json.dumps(sorted(entries)))
            conn.commit()
