import json
import logging
import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Request, Depends, Form, Query
from fastapi.responses import HTMLResponse
from pydantic import PositiveInt
from psycopg2.errors import UniqueViolation
from starlette.responses import RedirectResponse
from typing import Optional, List
from frontend.deps import get_menu_context, require_menu, templates
from frontend.pagination import paginate_counts
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


def _parse_vehicle_profile(vehicle_type, purchase_price, depreciation_start_date, depreciation_years):
    vehicle_type = (vehicle_type or "").strip() or None
    if vehicle_type and len(vehicle_type) > 120:
        raise ValueError("Loại xe quá dài.")
    price = None
    if purchase_price and purchase_price.strip():
        try:
            price = Decimal(purchase_price.strip())
        except InvalidOperation as exc:
            raise ValueError("Nguyên giá xe không hợp lệ.") from exc
        if not price.is_finite() or price < 0 or price >= 10**14 or price != price.to_integral_value():
            raise ValueError("Nguyên giá xe không hợp lệ.")
    start = None
    if depreciation_start_date and depreciation_start_date.strip():
        try:
            start = date.fromisoformat(depreciation_start_date.strip())
        except ValueError as exc:
            raise ValueError("Ngày bắt đầu khấu hao không hợp lệ.") from exc
    years = None
    if depreciation_years and depreciation_years.strip():
        try:
            years = int(depreciation_years.strip())
        except ValueError as exc:
            raise ValueError("Số năm khấu hao không hợp lệ.") from exc
        if years < 1 or years > 50:
            raise ValueError("Số năm khấu hao phải từ 1 đến 50.")
    return vehicle_type, price, start, years


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
        where_parts.append("(w.company_id = %s OR w.company_id IS NULL)")
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


def count_whitelist_vehicles(site_id=None, company_id=None) -> int:
    where_parts = []
    params = []
    if site_id:
        where_parts.append("(site_ids @> %s::jsonb OR site_ids = '[]'::jsonb OR site_ids IS NULL)")
        params.append(json.dumps([site_id]))
    if company_id is not None:
        where_parts.append("(company_id = %s OR company_id IS NULL)")
        params.append(company_id)
    where = " WHERE " + " AND ".join(where_parts) if where_parts else ""
    row = db.execute_query(f"SELECT COUNT(*) AS total FROM plate_whitelist{where}", tuple(params) if params else None, fetch=True)
    return int((row or {}).get("total") or 0)


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


@router.get("")
async def vehicles_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    ctx.update(month=date.today().strftime("%Y-%m"), vehicle_id=None, vehicle_section="list")
    site_id = request.session.get("active_site_id", 0) or None
    owner_users = db.select("users", columns=["id", "username", "full_name"], order_by="full_name ASC")
    for owner in owner_users:
        owner["vehicle_owner_label"] = owner.get("full_name") or owner.get("username")

    active_cid = ctx.get("active_company_id")
    total_vehicles = count_whitelist_vehicles(site_id=site_id, company_id=active_cid)
    paginated = paginate_counts(total_vehicles, page=page, per_page=20, base_url="/admin/vehicles")
    vehicles = get_whitelist_vehicles(
        site_id=site_id, company_id=active_cid,
        limit=paginated["per_page"], offset=(paginated["page"] - 1) * paginated["per_page"],
    ) or []

    ctx["vehicles"] = vehicles
    ctx["page"] = paginated["page"]
    ctx["total_pages"] = paginated["total_pages"]
    ctx["total"] = paginated["total"]
    ctx["page_base_url"] = paginated["page_base_url"]
    ctx["owner_users"] = owner_users
    ctx["site_list"] = db.select("sites", conditions={"is_active": True}, order_by="site_name ASC")
    ctx["sites"] = ctx["site_list"]
    return templates.TemplateResponse("pages/vehicles/index.html", {"request": request, **ctx})


@router.get("/add")
async def add_vehicle_page(request: Request, user: dict = Depends(require_menu("vehicles"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    owner_users = db.select("users", columns=["id", "username", "full_name"], order_by="full_name ASC")
    for owner in owner_users:
        owner["vehicle_owner_label"] = owner.get("full_name") or owner.get("username")
    ctx["owner_users"] = owner_users
    ctx["sites"] = db.select("sites", conditions={"is_active": True}, order_by="site_name ASC")
    return templates.TemplateResponse("pages/vehicles/add.html", {"request": request, **ctx})


@router.get("/{vehicle_id}/edit")
async def edit_vehicle_page(vehicle_id: int, request: Request, user: dict = Depends(require_menu("vehicles"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return HTMLResponse("Không tìm thấy xe.", status_code=404)
    if isinstance(vehicle.get("site_ids"), str):
        try:
            vehicle["site_ids"] = json.loads(vehicle["site_ids"])
        except Exception:
            vehicle["site_ids"] = []
    elif vehicle.get("site_ids") is None:
        vehicle["site_ids"] = []

    owner_users = db.select("users", columns=["id", "username", "full_name"], order_by="full_name ASC")
    for owner in owner_users:
        owner["vehicle_owner_label"] = owner.get("full_name") or owner.get("username")
    ctx["vehicle"] = vehicle
    ctx["owner_users"] = owner_users
    ctx["sites"] = db.select("sites", conditions={"is_active": True}, order_by="site_name ASC")
    return templates.TemplateResponse("pages/vehicles/edit.html", {"request": request, **ctx})


@router.post("")
async def add_vehicle(
    request: Request,
    plate_number: str = Form(...),
    site_ids: List[PositiveInt] = Form(default=[]),
    owner_name: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    fuel_consumption: Optional[str] = Form(None),
    vehicle_type: Optional[str] = Form(None),
    purchase_price: Optional[str] = Form(None),
    depreciation_start_date: Optional[str] = Form(None),
    depreciation_years: Optional[str] = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    added_by = user.get("username", "admin")
    plate_display = plate_number.strip().upper()
    plate_norm = normalize_plate(plate_display)
    fuel_value = _parse_fuel(fuel_consumption)
    try:
        vehicle_profile = _parse_vehicle_profile(vehicle_type, purchase_price, depreciation_start_date, depreciation_years)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    if not re.fullmatch(r"[A-Z0-9]+", plate_norm) or (fuel_consumption and fuel_consumption.strip() and fuel_value is None):
        return HTMLResponse("Biển số hoặc mức tiêu hao nhiên liệu không hợp lệ.", status_code=422)
    active_cid = request.session.get("active_company_id")
    if not upsert_vehicle_whitelist(
        plate_number=plate_display,
        label=owner_name or None,
        added_by=added_by,
        site_ids=site_ids,
        note=note,
        fuel_consumption=fuel_value,
        vehicle_profile=vehicle_profile,
        company_id=active_cid,
    ):
        return HTMLResponse("Không thể lưu xe.", status_code=500)
    return RedirectResponse(url="/admin/vehicles", status_code=303)




@router.post("/{plate_id}/delete")
async def delete_vehicle_post(
    plate_id: int,
    request: Request,
    _: dict = Depends(require_menu("vehicles")),
):
    if not db.delete("plate_whitelist", {"id": plate_id}):
        return HTMLResponse("Không tìm thấy xe.", status_code=404)
    trigger_ai_reload()
    if (request.headers.get("X-HTMX-Action") == "true"
            or request.headers.get("HX-Request")
            or request.query_params.get("hx")):
        return HTMLResponse("", status_code=200)
    return RedirectResponse(url="/admin/vehicles", status_code=303)


@router.post("/{vehicle_id}/edit")
async def edit_vehicle(
    vehicle_id: int,
    plate_number: str = Form(...),
    site_ids: List[PositiveInt] = Form(default=[]),
    owner_name: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    fuel_consumption: Optional[str] = Form(None),
    vehicle_type: Optional[str] = Form(None),
    purchase_price: Optional[str] = Form(None),
    depreciation_start_date: Optional[str] = Form(None),
    depreciation_years: Optional[str] = Form(None),
    _: dict = Depends(require_menu("vehicles")),
):
    plate_display = plate_number.strip().upper()
    plate_norm = normalize_plate(plate_display)
    fuel_value = _parse_fuel(fuel_consumption)
    try:
        vehicle_profile = _parse_vehicle_profile(vehicle_type, purchase_price, depreciation_start_date, depreciation_years)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    if not re.fullmatch(r"[A-Z0-9]+", plate_norm) or (fuel_consumption and fuel_consumption.strip() and fuel_value is None):
        return HTMLResponse("Biển số hoặc mức tiêu hao nhiên liệu không hợp lệ.", status_code=422)
    try:
        updated = update_vehicle_whitelist(
            vehicle_id=vehicle_id,
            plate_number=plate_display,
            label=owner_name or None,
            site_ids=site_ids,
            note=note,
            fuel_consumption=fuel_value,
            vehicle_profile=vehicle_profile,
        )
    except UniqueViolation as exc:
        if exc.diag.constraint_name not in {
            "plate_whitelist_plate_number_key",
            "plate_whitelist_plate_number_normalized_key",
        }:
            raise
        return HTMLResponse("Biển số đã tồn tại.", status_code=409)
    if not updated:
        return HTMLResponse("Không tìm thấy xe.", status_code=404)
    return RedirectResponse(url="/admin/vehicles", status_code=303)
