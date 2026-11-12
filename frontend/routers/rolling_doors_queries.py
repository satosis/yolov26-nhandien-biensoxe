"""Truy vấn cơ sở dữ liệu của frontend.routers.rolling_doors_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def count_rolling_doors_select_rolling_doors():
    return db.execute_query("SELECT COUNT(*) AS total FROM rolling_doors", fetch=True, commit=False)


def get_rolling_door_select_rolling_doors(door_id):
    return db.select('rolling_doors', conditions={'id': door_id}, fetchall=False)


def upsert_rolling_door_record_insert_rolling_doors(data):
    return db.insert('rolling_doors', data)


def upsert_rolling_door_record_update_rolling_doors(data, door_id):
    return db.update('rolling_doors', data, {'id': door_id})


def delete_rolling_door_record_delete_rolling_doors(door_id):
    return db.delete('rolling_doors', {'id': door_id})


def select_sites():
    return db.select('sites', order_by='site_name ASC')


def select_cameras():
    return db.select('cameras', order_by='camera_name ASC')


def get_all_rolling_doors(limit=None, offset=None) -> list:
    query = """
        SELECT rd.*, dp.name as supplier_name, s.site_name 
        FROM rolling_doors rd
        LEFT JOIN door_providers dp ON rd.supplier_key = dp.provider_key
        LEFT JOIN sites s ON rd.site_id = s.id
        ORDER BY dp.name ASC, rd.name ASC
    """
    params = []
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)
    return db.execute_query(query, tuple(params) if params else None, fetchall=True, commit=False)
