import json
from fastapi import APIRouter, Request, Depends, Form
from starlette.responses import RedirectResponse
from typing import Optional, List
from frontend.deps import get_menu_context, require_menu, templates
from backend.services.database import db
from backend.services.config import normalize_plate, trigger_ai_reload

router = APIRouter(prefix="/vehicles")


def _parse_fuel(value: Optional[str]) -> Optional[float]:
    if not value or not value.strip():
        return None
    try:
        fc = float(value.strip().replace(",", "."))
    except ValueError:
        return None
    return fc if fc >= 0 else None


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


def upsert_vehicle_whitelist(plate_norm: str, label: Optional[str], added_by: str, site_ids: Optional[list] = None, note: Optional[str] = None, fuel_consumption: Optional[float] = None):
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
    db.execute_query(
        query,
        (plate_norm, label, added_by, json.dumps(site_ids) if site_ids else '[]', note, fuel_consumption),
        commit=True
    )
    trigger_ai_reload()


def update_vehicle_whitelist(vehicle_id: int, plate_norm: str, label: Optional[str], site_ids: Optional[list] = None, note: Optional[str] = None, fuel_consumption: Optional[float] = None):
    query = """
        UPDATE plate_whitelist
        SET plate_number = %s, owner_name = %s, site_ids = %s, note = %s, fuel_consumption = %s, updated_at = NOW()
        WHERE id = %s
    """
    db.execute_query(
        query,
        (plate_norm, label, json.dumps(site_ids) if site_ids else '[]', note, fuel_consumption, vehicle_id),
        commit=True
    )
    trigger_ai_reload()


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
    request: Request,
    plate_number: str = Form(...),
    site_ids: List[str] = Form(default=[]),
    owner_name: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    fuel_consumption: Optional[str] = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    added_by = user.get("username", "admin")
    site_ids_int = [int(s) for s in site_ids if s and s.strip()]
    upsert_vehicle_whitelist(
        plate_norm=normalize_plate(plate_number),
        label=owner_name or None,
        added_by=added_by,
        site_ids=site_ids_int,
        note=note,
        fuel_consumption=_parse_fuel(fuel_consumption),
    )
    return RedirectResponse(url="/admin/vehicles", status_code=303)




@router.post("/{plate_id}/delete")
async def delete_vehicle_post(
    plate_id: int,
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    db.delete("plate_whitelist", {"id": plate_id})
    trigger_ai_reload()
    return RedirectResponse(url="/admin/vehicles", status_code=303)


@router.post("/{vehicle_id}/edit")
async def edit_vehicle(
    vehicle_id: int,
    request: Request,
    plate_number: str = Form(...),
    site_ids: List[str] = Form(default=[]),
    owner_name: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    fuel_consumption: Optional[str] = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    site_ids_int = [int(s) for s in site_ids if s and s.strip()]
    update_vehicle_whitelist(
        vehicle_id=vehicle_id,
        plate_norm=normalize_plate(plate_number),
        label=owner_name or None,
        site_ids=site_ids_int,
        note=note,
        fuel_consumption=_parse_fuel(fuel_consumption),
    )
    return RedirectResponse(url="/admin/vehicles", status_code=303)
