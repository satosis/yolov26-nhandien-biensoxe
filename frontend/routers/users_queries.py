"""Truy vấn cơ sở dữ liệu của frontend.routers.users_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def select_roles():
    return db.select("roles", order_by="id ASC")


def users_page_select_app_settings():
    return db.select("app_settings", conditions={"key": "REQUIRE_KYC"}, fetchall=False)


def select_sites():
    return db.select("sites", order_by="id ASC")


def select_role_by_name(role_name):
    return db._execute_query("SELECT 1 FROM roles WHERE role_name = %s", (role_name,), fetch=True, commit=False)
