"""Truy vấn cơ sở dữ liệu của frontend.routers.legal_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def save_deletion_request_run_sql(confirmation_code, user_id, contact_info, details):
    insert_sql = """
        INSERT INTO data_deletion_requests (confirmation_code, user_id, contact_info, status, details)
        VALUES (%s, %s, %s, 'completed', %s)
        ON CONFLICT (confirmation_code) DO NOTHING;
        """
    return db.query(insert_sql, (confirmation_code, user_id, contact_info, details), commit=True)


def get_deletion_request_run_sql(confirmation_code):
    sql = """
        SELECT confirmation_code, user_id, contact_info, status, details, created_at
        FROM data_deletion_requests
        WHERE confirmation_code = %s
        LIMIT 1;
        """
    return db.query(sql, (confirmation_code,), fetch=True)
