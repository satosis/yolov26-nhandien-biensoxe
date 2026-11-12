"""Truy vấn cơ sở dữ liệu của frontend.modules.auth.controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def select_user_by_email(email):
    return db.execute_query(
        "SELECT u.*, COALESCE(r.role_name, 'Guest') as role FROM users u LEFT JOIN roles r ON u.role_id = r.id WHERE u.email = %s",
        (email,),
        fetch=True,
    )


def update_user_name_and_avatar(name, picture, user):
    return db.execute_query(
        """
                UPDATE users 
                SET full_name = COALESCE(NULLIF(%s, ''), full_name),
                    avatar = COALESCE(NULLIF(%s, ''), avatar),
                    updated_at = NOW()
                WHERE id = %s
                """,
        (name, picture, user["id"]),
    )


def get_user_by_username(username: str) -> dict | None:
    query = """
        SELECT u.*, COALESCE(r.role_name, 'Guest') as role 
        FROM users u 
        LEFT JOIN roles r ON u.role_id = r.id 
        WHERE u.username = %s
    """
    return db.execute_query(query, (username,), fetch=True)
