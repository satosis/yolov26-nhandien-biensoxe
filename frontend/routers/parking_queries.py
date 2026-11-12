"""Truy vấn cơ sở dữ liệu của frontend.routers.parking_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def count_parking_history_select_vehicle_sessions(params, where):
    return db.execute_query(f"SELECT COUNT(*) AS total FROM vehicle_sessions vs {where}", tuple(params) if params else None, fetch=True)


def update_parking_history_plate_update_vehicle_sessions(plate, session_id):
    return db._execute_query(
        "UPDATE vehicle_sessions SET plate_norm = %s WHERE id = %s RETURNING id",
        (plate, session_id), fetch=True, commit=True,
    )


def delete_parking_history_delete_vehicle_sessions(session_id):
    return db._execute_query(
        "DELETE FROM vehicle_sessions WHERE id = %s RETURNING id",
        (session_id,), fetch=True, commit=True,
    )


def get_occupancy_stats(site_id=None) -> list[dict]:
    query = """
        SELECT
            s.id,
            s.site_name,
            s.total_slots,
            COUNT(vs.id) FILTER (WHERE vs.status = 'parking') AS occupied_slots
        FROM sites s
        LEFT JOIN vehicle_sessions vs ON s.id = vs.site_id
    """
    params = []
    if site_id is not None:
        query += " WHERE s.id = %s"
        params.append(site_id)
    query += " GROUP BY s.id, s.site_name, s.total_slots ORDER BY s.site_name"
    return db.execute_query(query, tuple(params) if params else None, fetchall=True)


def get_parking_history(limit=20, offset=0, date_filter=None) -> list[dict]:
    where = ""
    params = []
    if date_filter:
        where = "WHERE vs.entry_time::date = %s"
        params.append(date_filter)
    params.extend((limit, offset))
    query = f"""
        SELECT
            vs.*,
            vs.plate_norm AS plate_number,
            s.site_name,
            c_in.camera_name AS cam_in,
            c_out.camera_name AS cam_out,
            COALESCE(c_in.camera_name, c_out.camera_name) AS camera_name
        FROM vehicle_sessions vs
        LEFT JOIN sites s ON vs.site_id = s.id
        LEFT JOIN cameras c_in ON c_in.id::text = vs.camera_id_in
        LEFT JOIN cameras c_out ON c_out.id::text = vs.camera_id_out
        {where}
        ORDER BY vs.entry_time DESC
        LIMIT %s OFFSET %s
    """
    return db.execute_query(query, tuple(params), fetchall=True)


def _get_analytics_kpi(site_id=None) -> dict:
    where = ""
    params = []
    if site_id is not None:
        where = "WHERE site_id = %s"
        params.append(site_id)

    query = f"""
        SELECT
            COUNT(*) FILTER (WHERE entry_time >= CURRENT_DATE) AS today_count,
            COUNT(*) FILTER (WHERE entry_time >= date_trunc('week', CURRENT_DATE)) AS week_count,
            COUNT(*) FILTER (WHERE entry_time >= date_trunc('month', CURRENT_DATE)) AS month_count,
            AVG(stay_duration) FILTER (WHERE stay_duration IS NOT NULL AND stay_duration > 0) AS avg_duration_sec
        FROM vehicle_sessions
        {where}
    """
    row = db.execute_query(query, tuple(params) if params else None, fetch=True)
    if not row:
        return {"today_count": 0, "week_count": 0, "month_count": 0, "avg_duration": "—"}

    avg_sec = row.get("avg_duration_sec")
    if avg_sec:
        avg_min = int(avg_sec // 60)
        if avg_min < 60:
            avg_str = f"{avg_min} phút"
        else:
            avg_str = f"{avg_min // 60}h {avg_min % 60}p"
    else:
        avg_str = "—"

    return {
        "today_count": row.get("today_count") or 0,
        "week_count": row.get("week_count") or 0,
        "month_count": row.get("month_count") or 0,
        "avg_duration": avg_str,
    }
