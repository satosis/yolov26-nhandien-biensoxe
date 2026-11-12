"""Truy vấn cơ sở dữ liệu của frontend.routers.door_providers_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def count_door_providers_select_door_providers():
    return db.execute_query("SELECT COUNT(*) AS total FROM door_providers", fetch=True)


def get_door_provider_select_door_providers(provider_key):
    return db.select("door_providers", conditions={"provider_key": provider_key}, fetchall=False)


def delete_door_provider_delete_door_providers(provider_key):
    return db.delete("door_providers", {"provider_key": provider_key})


def get_all_door_providers(limit=None, offset=None) -> list:
    query = "SELECT * FROM door_providers ORDER BY name ASC"
    params = []
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)
    return db.execute_query(query, tuple(params) if params else None, fetchall=True)


def upsert_door_provider(provider_key, name, connection_type=None, api_doc_url=None,
                         config_template=None, notes=None, logo_url=None, is_active=True) -> bool:
    query = """
        INSERT INTO door_providers
            (provider_key, name, connection_type, api_doc_url, config_template, notes, logo_url, is_active, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (provider_key) DO UPDATE SET
            name=EXCLUDED.name, connection_type=EXCLUDED.connection_type,
            api_doc_url=EXCLUDED.api_doc_url, config_template=EXCLUDED.config_template,
            notes=EXCLUDED.notes, logo_url=EXCLUDED.logo_url, is_active=EXCLUDED.is_active, updated_at=NOW()
    """
    return db.execute_query(
        query,
        (provider_key, name, connection_type, api_doc_url, config_template, notes, logo_url, is_active),
        commit=True
    )
