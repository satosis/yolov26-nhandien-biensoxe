"""Truy vấn cơ sở dữ liệu của frontend.routers.recordings_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def delete_recording_select_video_recordings(recording_id):
    return db.select("video_recordings", conditions={"id": recording_id}, fetchall=False)


def delete_recording_delete_video_recordings(recording_id):
    return db.delete("video_recordings", {"id": recording_id})


def get_recordings(site_id=None, limit=50, offset=0):
    query = "SELECT * FROM video_recordings"
    params = []
    if site_id:
        query += " WHERE site_id = %s"
        params.append(site_id)
    query += " ORDER BY start_time DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    return db._execute_query(query, tuple(params), fetchall=True, commit=False)


def count_recordings(site_id=None):
    query = "SELECT COUNT(*) FROM video_recordings"
    params = []
    if site_id:
        query += " WHERE site_id = %s"
        params.append(site_id)
    row = db._execute_query(query, tuple(params) if params else None, fetch=True, commit=False)
    return row["count"]
