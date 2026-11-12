"""Truy vấn cơ sở dữ liệu của frontend.routers.rbac_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def rbac_page_select_roles():
    return db.select("roles", order_by="id DESC")


def create_role_select_roles(role_name):
    return db.select("roles", conditions={"role_name": role_name}, fetchall=False)


def create_role_insert_roles(role_name, menus_str):
    return db.insert("roles", {"role_name": role_name, "allowed_menus": menus_str}, returning="id")


def update_role_update_roles(menus_str, role_id):
    return db.update("roles", {"allowed_menus": menus_str}, {"id": role_id})


def delete_role_select_roles():
    return db.select("roles", order_by="id ASC")


def delete_role_delete_roles(role_id):
    return db.delete("roles", {"id": role_id})
