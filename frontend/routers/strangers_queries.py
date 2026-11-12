"""Truy vấn cơ sở dữ liệu của frontend.routers.strangers_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def count_stranger_logs_select_stranger_logs(params, where):
    return db._execute_query(f"SELECT COUNT(*) AS total FROM stranger_logs {where}", tuple(params) if params else None, fetch=True, commit=False)


def delete_stranger_log_select_stranger_logs(stranger_id):
    return db.select("stranger_logs", columns="image_path", conditions={"id": stranger_id}, fetchall=False)


def delete_stranger_log_delete_stranger_logs(stranger_id):
    return db.delete("stranger_logs", {"id": stranger_id})


def get_stranger_logs(status: str | None = None, limit: int = 20, offset: int = 0, site_id: int | None = None) -> list:
    params: list = []
    where_clauses = []
    if status:
        where_clauses.append("LOWER(TRIM(sl.status)) = LOWER(%s)")
        params.append(status)
    if site_id:
        where_clauses.append("(sl.site_id = %s OR sl.site_id IS NULL)")
        params.append(site_id)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.extend((limit, offset))
    query = f"""
        SELECT sl.*, s.site_name, u.username AS converted_username
        FROM stranger_logs sl
        LEFT JOIN sites s ON sl.site_id = s.id
        LEFT JOIN users u ON sl.converted_user_id = u.id
        {where}
        ORDER BY COALESCE(sl.last_seen, sl.event_time) DESC
        LIMIT %s OFFSET %s
    """
    return db._execute_query(query, tuple(params), fetchall=True, commit=False)


def get_stranger_log(stranger_id: int):
    query = """
        SELECT sl.*, s.site_name, u.username as converted_username
        FROM stranger_logs sl
        LEFT JOIN sites s ON sl.site_id = s.id
        LEFT JOIN users u ON sl.converted_user_id = u.id
        WHERE sl.id = %s
    """
    return db._execute_query(query, (stranger_id,), fetch=True, commit=False)


def update_stranger_status(stranger_id: int, status: str,
                           converted_user_id: int | None = None, note: str | None = None) -> bool:
    query = """
        UPDATE stranger_logs
        SET status = %s,
            converted_user_id = COALESCE(%s, converted_user_id),
            note = COALESCE(%s, note)
        WHERE id = %s
    """
    return db._execute_query(query, (status, converted_user_id, note, stranger_id), commit=True)
