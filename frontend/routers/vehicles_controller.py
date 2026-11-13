import json
import logging
import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import PositiveInt
from psycopg2.errors import ForeignKeyViolation, UniqueViolation
from starlette.responses import RedirectResponse
from typing import Optional, List
from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate_counts
from backend.services.config import normalize_plate, trigger_ai_reload
import frontend.routers.vehicles_queries as queries

logger = logging.getLogger(__name__)
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
        if years < 1:
            raise ValueError("Số năm khấu hao phải lớn hơn 0.")
    return vehicle_type, price, start, years


def count_whitelist_vehicles(site_id=None, company_id=None) -> int:
    where_parts = []
    params = []
    if site_id:
        where_parts.append("(site_ids @> %s::jsonb OR site_ids = '[]'::jsonb OR site_ids IS NULL)")
        params.append(json.dumps([site_id]))
    if company_id is not None:
        where_parts.append("company_id = %s")
        params.append(company_id)
    where = " WHERE " + " AND ".join(where_parts) if where_parts else ""
    row = queries.count_whitelist_vehicles_select_plate_whitelist(params, where)
    return int((row or {}).get("total") or 0)


async def vehicles_page(request: Request, page: int, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    ctx.update(month=date.today().strftime("%Y-%m"), vehicle_id=None, vehicle_section="list")
    site_id = request.session.get("active_site_id", 0) or None
    owner_users = queries.select_users()
    for owner in owner_users:
        owner["vehicle_owner_label"] = owner.get("full_name") or owner.get("username")

    active_cid = ctx.get("active_company_id")
    total_vehicles = count_whitelist_vehicles(site_id=site_id, company_id=active_cid)
    paginated = paginate_counts(total_vehicles, page=page, per_page=100, base_url="/admin/vehicles")
    vehicles = queries.get_whitelist_vehicles(
        site_id=site_id, company_id=active_cid,
        limit=paginated["per_page"], offset=(paginated["page"] - 1) * paginated["per_page"],
    ) or []

    ctx["vehicles"] = vehicles
    ctx["page"] = paginated["page"]
    ctx["total_pages"] = paginated["total_pages"]
    ctx["total"] = paginated["total"]
    ctx["page_base_url"] = paginated["page_base_url"]
    ctx["owner_users"] = owner_users
    ctx["site_list"] = queries.select_active_sites()
    ctx["sites"] = ctx["site_list"]
    return templates.TemplateResponse("pages/vehicles/index.html", {"request": request, **ctx})


async def add_vehicle_page(request: Request, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    owner_users = queries.select_users()
    for owner in owner_users:
        owner["vehicle_owner_label"] = owner.get("full_name") or owner.get("username")
    ctx["owner_users"] = owner_users
    ctx["sites"] = queries.select_active_sites()
    return templates.TemplateResponse("pages/vehicles/add.html", {"request": request, **ctx})


async def edit_vehicle_page(vehicle_id: int, request: Request, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    vehicle = queries.edit_vehicle_page_select_plate_whitelist(vehicle_id)
    if not vehicle:
        return HTMLResponse("Không tìm thấy xe.", status_code=404)
    if isinstance(vehicle.get("site_ids"), str):
        try:
            vehicle["site_ids"] = json.loads(vehicle["site_ids"])
        except Exception:
            vehicle["site_ids"] = []
    elif vehicle.get("site_ids") is None:
        vehicle["site_ids"] = []

    owner_users = queries.select_users()
    for owner in owner_users:
        owner["vehicle_owner_label"] = owner.get("full_name") or owner.get("username")
    ctx["vehicle"] = vehicle
    ctx["owner_users"] = owner_users
    ctx["sites"] = queries.select_active_sites()
    return templates.TemplateResponse("pages/vehicles/edit.html", {"request": request, **ctx})


async def add_vehicle(
    request: Request,
    plate_number: str,
    site_ids: List[PositiveInt],
    owner_name: Optional[str],
    note: Optional[str],
    fuel_consumption: Optional[str],
    vehicle_type: Optional[str],
    purchase_price: Optional[str],
    depreciation_start_date: Optional[str],
    depreciation_years: Optional[str],
    user: dict,
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
    if not queries.upsert_vehicle_whitelist(
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


async def delete_vehicle_post(plate_id: int, request: Request, _: dict):
    try:
        deleted = queries.delete_vehicle_post_delete_plate_whitelist(plate_id)
    except ForeignKeyViolation:
        # Xe đã gắn với hóa đơn/chuyến hàng: giữ nguyên dữ liệu kế toán, không xóa lan.
        message = "Xe đã có hóa đơn/chuyến hàng nên không xóa được. Có thể ngưng sử dụng xe thay vì xóa."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JSONResponse({"ok": False, "message": message})
        return HTMLResponse(message, status_code=409)
    if not deleted:
        return HTMLResponse("Không tìm thấy xe.", status_code=404)
    trigger_ai_reload()
    if (request.headers.get("X-HTMX-Action") == "true"
            or request.headers.get("HX-Request")
            or request.query_params.get("hx")):
        return HTMLResponse("", status_code=200)
    return RedirectResponse(url="/admin/vehicles", status_code=303)


async def edit_vehicle(
    vehicle_id: int,
    plate_number: str,
    site_ids: List[PositiveInt],
    owner_name: Optional[str],
    note: Optional[str],
    fuel_consumption: Optional[str],
    vehicle_type: Optional[str],
    purchase_price: Optional[str],
    depreciation_start_date: Optional[str],
    depreciation_years: Optional[str],
    _: dict,
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
        updated = queries.update_vehicle_whitelist(
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
