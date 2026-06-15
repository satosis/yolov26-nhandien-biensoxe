"""Vehicles (whitelist) router."""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from pathlib import Path
from typing import Optional, List
from frontend.deps import get_db, get_menu_context, require_menu

router = APIRouter(prefix="/vehicles")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _parse_fuel(value: Optional[str]) -> Optional[float]:
    """Parse fuel-consumption input (L/100km) -> float, or None if empty/invalid."""
    if not value or not value.strip():
        return None
    try:
        fc = float(value.strip().replace(",", "."))
    except ValueError:
        return None
    return fc if fc >= 0 else None


@router.get("")
async def vehicles_page(request: Request, user: dict = Depends(require_menu("vehicles"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    site_id = request.session.get("active_site_id", 0) or None
    db = get_db()
    owner_users = db.get_all_users()
    for owner in owner_users:
        owner["vehicle_owner_label"] = owner.get("full_name") or owner.get("username")

    ctx["vehicles"] = db.get_whitelist_vehicles(site_id=site_id)
    ctx["owner_users"] = owner_users
    ctx["site_list"] = db.get_sites(active_only=True)
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
    get_db().upsert_vehicle_whitelist(
        plate_norm=plate_number.strip().upper(),
        label=owner_name or None,
        added_by=added_by,
        site_ids=site_ids_int,
        note=note,
        fuel_consumption=_parse_fuel(fuel_consumption),
    )
    return RedirectResponse(url="/camera/vehicles", status_code=303)


@router.delete("/{plate_id}")
async def delete_vehicle(
    plate_id: int,
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    get_db().delete("plate_whitelist", {"id": plate_id})
    return RedirectResponse(url="/camera/vehicles", status_code=303)


@router.post("/{plate_id}/delete")
async def delete_vehicle_post(
    plate_id: int,
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    get_db().delete("plate_whitelist", {"id": plate_id})
    return RedirectResponse(url="/camera/vehicles", status_code=303)


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
    get_db().update_vehicle_whitelist(
        vehicle_id=vehicle_id,
        plate_norm=plate_number.strip().upper(),
        label=owner_name or None,
        site_ids=site_ids_int,
        note=note,
        fuel_consumption=_parse_fuel(fuel_consumption),
    )
    return RedirectResponse(url="/camera/vehicles", status_code=303)
