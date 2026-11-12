"""Truy vấn cơ sở dữ liệu của frontend.routers.cameras_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def persist_derived_rtsp_update_cameras(rtsp_url, camera_id):
    return db._execute_query(
        "UPDATE cameras SET rtsp_url = %s WHERE id = %s", (rtsp_url, camera_id), commit=True
    )
