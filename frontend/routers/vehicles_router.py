import json
import logging
import math
import re
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse
from pydantic import PositiveInt
from starlette.responses import RedirectResponse
from typing import Optional, List
from frontend.deps import get_menu_context, require_menu, templates
from backend.services.database import db
from backend.services.config import normalize_plate, trigger_ai_reload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vehicles")
_FUEL_RE = re.compile(r"^\d+(?:[.,]\d+)?$")


def _parse_fuel(value: Optional[str]) -> Optional[float]:
    if not value or not value.strip():
        return None
    normalized = value.strip()
    if len(normalized) > 40 or not _FUEL_RE.fullmatch(normalized):
        return None
    fc = float(normalized.replace(",", "."))
    return fc if math.isfinite(fc) and fc >= 0 else None


def get_whitelist_vehicles(site_id=None, limit=None, offset=None) -> list[dict]:
    query = """
        SELECT w.*,
               (SELECT array_agg(s.site_name)
                FROM sites s WHERE s.id IN (SELECT value::int FROM jsonb_array_elements_text(COALESCE(w.site_ids, '[]'::jsonb)))) AS site_names
        FROM plate_whitelist w
    """
    params = []
    if site_id:
        query += " WHERE (w.site_ids @> %s::jsonb OR w.site_ids = '[]'::jsonb OR w.site_ids IS NULL)"
        params.append(json.dumps([site_id]))
    query += " ORDER BY w.created_at DESC"
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)
    return db.execute_query(query, tuple(params) if params else None, fetchall=True)


def upsert_vehicle_whitelist(plate_norm: str, label: Optional[str], added_by: str, site_ids: Optional[list] = None, note: Optional[str] = None, fuel_consumption: Optional[float] = None) -> bool:
    query = """
        INSERT INTO plate_whitelist (plate_number, list_type, owner_name, added_by, site_ids, note, fuel_consumption, is_active)
        VALUES (%s, 'white', %s, %s, %s, %s, %s, TRUE)
        ON CONFLICT(plate_number) DO UPDATE SET
        owner_name=EXCLUDED.owner_name,
        added_by=EXCLUDED.added_by,
        site_ids=EXCLUDED.site_ids,
        note=EXCLUDED.note,
        fuel_consumption=EXCLUDED.fuel_consumption,
        updated_at=NOW()
    """
    saved = db.execute_query(
        query,
        (plate_norm, label, added_by, json.dumps(site_ids) if site_ids else '[]', note, fuel_consumption),
        commit=True
    )
    if saved:
        trigger_ai_reload()
    else:
        logger.error("Vehicle whitelist upsert affected no rows")
    return bool(saved)


def update_vehicle_whitelist(vehicle_id: int, plate_norm: str, label: Optional[str], site_ids: Optional[list] = None, note: Optional[str] = None, fuel_consumption: Optional[float] = None) -> bool:
    query = """
        UPDATE plate_whitelist
        SET plate_number = %s, owner_name = %s, site_ids = %s, note = %s, fuel_consumption = %s, updated_at = NOW()
        WHERE id = %s
    """
    updated = db.execute_query(
        query,
        (plate_norm, label, json.dumps(site_ids) if site_ids else '[]', note, fuel_consumption, vehicle_id),
        commit=True
    )
    if updated:
        trigger_ai_reload()
    return bool(updated)


@router.get("")
async def vehicles_page(request: Request, user: dict = Depends(require_menu("vehicles"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    site_id = request.session.get("active_site_id", 0) or None
    owner_users = db.select("users", columns=["id", "username", "full_name"], order_by="full_name ASC")
    for owner in owner_users:
        owner["vehicle_owner_label"] = owner.get("full_name") or owner.get("username")

    ctx["vehicles"] = get_whitelist_vehicles(site_id=site_id)
    ctx["owner_users"] = owner_users
    ctx["site_list"] = db.select("sites", conditions={"is_active": True}, order_by="site_name ASC")
    ctx["sites"] = ctx["site_list"]
    return templates.TemplateResponse("pages/vehicles.html", {"request": request, **ctx})


@router.post("")
async def add_vehicle(
    plate_number: str = Form(...),
    site_ids: List[PositiveInt] = Form(default=[]),
    owner_name: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    fuel_consumption: Optional[str] = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    added_by = user.get("username", "admin")
    plate_norm = normalize_plate(plate_number)
    fuel_value = _parse_fuel(fuel_consumption)
    if not plate_norm or (fuel_consumption and fuel_consumption.strip() and fuel_value is None):
        return HTMLResponse("Biển số hoặc mức tiêu hao nhiên liệu không hợp lệ.", status_code=422)
    if not upsert_vehicle_whitelist(
        plate_norm=plate_norm,
        label=owner_name or None,
        added_by=added_by,
        site_ids=site_ids,
        note=note,
        fuel_consumption=fuel_value,
    ):
        return HTMLResponse("Không thể lưu xe.", status_code=500)
    return RedirectResponse(url="/admin/vehicles", status_code=303)




@router.post("/{plate_id}/delete")
async def delete_vehicle_post(
    plate_id: int,
    _: dict = Depends(require_menu("vehicles")),
):
    if not db.delete("plate_whitelist", {"id": plate_id}):
        return HTMLResponse("Không tìm thấy xe.", status_code=404)
    trigger_ai_reload()
    return RedirectResponse(url="/admin/vehicles", status_code=303)


@router.post("/{vehicle_id}/edit")
async def edit_vehicle(
    vehicle_id: int,
    plate_number: str = Form(...),
    site_ids: List[PositiveInt] = Form(default=[]),
    owner_name: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    fuel_consumption: Optional[str] = Form(None),
    _: dict = Depends(require_menu("vehicles")),
):
    plate_norm = normalize_plate(plate_number)
    fuel_value = _parse_fuel(fuel_consumption)
    if not plate_norm or (fuel_consumption and fuel_consumption.strip() and fuel_value is None):
        return HTMLResponse("Biển số hoặc mức tiêu hao nhiên liệu không hợp lệ.", status_code=422)
    if not update_vehicle_whitelist(
        vehicle_id=vehicle_id,
        plate_norm=plate_norm,
        label=owner_name or None,
        site_ids=site_ids,
        note=note,
        fuel_consumption=fuel_value,
    ):
        return HTMLResponse("Không tìm thấy xe.", status_code=404)
    return RedirectResponse(url="/admin/vehicles", status_code=303)
