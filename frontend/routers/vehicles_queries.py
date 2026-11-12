"""Truy vấn cơ sở dữ liệu của frontend.routers.vehicles_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db
import json
from typing import Optional
from backend.services.config import trigger_ai_reload
import logging

logger = logging.getLogger(__name__)


def count_whitelist_vehicles_select_plate_whitelist(params, where):
    return db.execute_query(f"SELECT COUNT(*) AS total FROM plate_whitelist{where}", tuple(params) if params else None, fetch=True)


def select_users():
    return db.select("users", columns=["id", "username", "full_name"], order_by="full_name ASC")


def select_active_sites():
    return db.select("sites", conditions={"is_active": True}, order_by="site_name ASC")


def edit_vehicle_page_select_plate_whitelist(vehicle_id):
    return db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)


def delete_vehicle_post_delete_plate_whitelist(plate_id):
    return db.delete("plate_whitelist", {"id": plate_id})


def get_whitelist_vehicles(site_id=None, limit=None, offset=None, company_id=None) -> list[dict]:
    query = """
        SELECT w.*,
               (SELECT array_agg(s.site_name)
                FROM sites s WHERE s.id IN (SELECT value::int FROM jsonb_array_elements_text(COALESCE(w.site_ids, '[]'::jsonb)))) AS site_names
        FROM plate_whitelist w
    """
    where_parts = []
    params = []
    if site_id:
        where_parts.append("(w.site_ids @> %s::jsonb OR w.site_ids = '[]'::jsonb OR w.site_ids IS NULL)")
        params.append(json.dumps([site_id]))
    if company_id is not None:
        where_parts.append("w.company_id = %s")
        params.append(company_id)
    if where_parts:
        query += " WHERE " + " AND ".join(where_parts)
    query += " ORDER BY w.created_at DESC"
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)
    return db.execute_query(query, tuple(params) if params else None, fetchall=True)


def upsert_vehicle_whitelist(plate_number: str, label: Optional[str], added_by: str, site_ids: Optional[list] = None, note: Optional[str] = None, fuel_consumption: Optional[float] = None, vehicle_profile=(None, None, None, None), company_id: Optional[int] = None) -> bool:
    query = """
        INSERT INTO plate_whitelist (plate_number, list_type, owner_name, added_by, site_ids, note, fuel_consumption,
                                     vehicle_type, purchase_price, depreciation_start_date, depreciation_years, is_active, company_id)
        VALUES (%s, 'white', %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
        ON CONFLICT (UPPER(REGEXP_REPLACE(plate_number, '[^A-Za-z0-9]', '', 'g'))) DO UPDATE SET
        plate_number=EXCLUDED.plate_number,
        owner_name=EXCLUDED.owner_name,
        added_by=EXCLUDED.added_by,
        site_ids=EXCLUDED.site_ids,
        note=EXCLUDED.note,
        fuel_consumption=EXCLUDED.fuel_consumption,
        vehicle_type=COALESCE(EXCLUDED.vehicle_type, plate_whitelist.vehicle_type),
        purchase_price=COALESCE(EXCLUDED.purchase_price, plate_whitelist.purchase_price),
        depreciation_start_date=COALESCE(EXCLUDED.depreciation_start_date, plate_whitelist.depreciation_start_date),
        depreciation_years=COALESCE(EXCLUDED.depreciation_years, plate_whitelist.depreciation_years),
        company_id=COALESCE(EXCLUDED.company_id, plate_whitelist.company_id),
        updated_at=NOW()
    """
    saved = db.execute_query(
        query,
        (plate_number, label, added_by, json.dumps(site_ids) if site_ids else '[]', note, fuel_consumption, *vehicle_profile, company_id),
        commit=True
    )
    if saved:
        trigger_ai_reload()
    else:
        logger.error("Vehicle whitelist upsert affected no rows")
    return bool(saved)


def update_vehicle_whitelist(vehicle_id: int, plate_number: str, label: Optional[str], site_ids: Optional[list] = None, note: Optional[str] = None, fuel_consumption: Optional[float] = None, vehicle_profile=(None, None, None, None)) -> bool:
    query = """
        UPDATE plate_whitelist
        SET plate_number = %s, owner_name = %s, site_ids = %s, note = %s, fuel_consumption = %s,
            vehicle_type = %s, purchase_price = %s, depreciation_start_date = %s, depreciation_years = %s,
            updated_at = NOW()
        WHERE id = %s
    """
    updated = db.execute_query(
        query,
        (plate_number, label, json.dumps(site_ids) if site_ids else '[]', note, fuel_consumption, *vehicle_profile, vehicle_id),
        commit=True
    )
    if updated:
        trigger_ai_reload()
    return bool(updated)
