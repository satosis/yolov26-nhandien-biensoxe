"""Truy vấn cơ sở dữ liệu của frontend.routers.sites_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def sites_list_html_select_sites(offset):
    return db.execute_query(
        "SELECT * FROM sites ORDER BY site_name LIMIT %s OFFSET %s",
        (20, offset), fetchall=True,
    )


def sites_page_select_sites():
    return db.execute_query("SELECT COUNT(*) AS total FROM sites", fetch=True)


def sites_page_select_sites_2(page_data):
    return db.execute_query(
        "SELECT * FROM sites ORDER BY site_name LIMIT %s OFFSET %s",
        (page_data["per_page"], (page_data["page"] - 1) * page_data["per_page"]),
        fetchall=True,
    )


def create_site_insert_sites(site_name, total_slots, active, address, description):
    return db.insert("sites", {
        "site_name": site_name,
        "address": address or "",
        "description": description or "",
        "total_slots": total_slots,
        "is_active": active
    }, returning="id")


def select_site_by_id(site_id):
    return db.select("sites", conditions={"id": site_id}, fetchall=False)


def get_site_row_select_sites_2(offset):
    return db.execute_query("SELECT id FROM sites ORDER BY site_name LIMIT %s OFFSET %s", (20, offset), fetchall=True)


def update_site_update_sites(site_name, active, total_slots, site_id, address, description):
    return db.update("sites", {
        "site_name": site_name,
        "address": address or "",
        "description": description or "",
        "is_active": active,
        "total_slots": total_slots
    }, {"id": site_id})


def delete_site_delete_sites(site_id):
    return db.delete("sites", {"id": site_id})


def toggle_site_status_update_sites(new_status, site_id):
    return db.update("sites", {"is_active": new_status}, {"id": site_id})
