"""Vehicles (whitelist) router."""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from pathlib import Path
from typing import Optional, List
from frontend.deps import get_db, get_menu_context, require_menu

router = APIRouter(prefix="/vehicles")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("")
async def vehicles_page(request: Request, user: dict = Depends(require_menu("vehicles"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    site_id = request.session.get("active_site_id", 0) or None
    ctx["vehicles"] = get_db().get_whitelist_vehicles(site_id=site_id)
    ctx["site_list"] = get_db().get_sites(active_only=True)
    ctx["sites"] = ctx["site_list"]
    return templates.TemplateResponse("pages/vehicles.html", {"request": request, **ctx})


@router.post("")
async def add_vehicle(
    request: Request,
    plate_number: str = Form(...),
    site_ids: List[str] = Form(default=[]),
    owner_name: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    added_by = user.get("username", "admin")
    site_ids_int = [int(s) for s in site_ids if s and s.strip()]
    get_db().upsert_vehicle_whitelist(
        plate_norm=plate_number.strip().upper(),
        label=owner_name,
        added_by=added_by,
        site_ids=site_ids_int,
        note=note,
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
    user: dict = Depends(require_menu("vehicles")),
):
    site_ids_int = [int(s) for s in site_ids if s and s.strip()]
    get_db().update_vehicle_whitelist(
        vehicle_id=vehicle_id,
        plate_norm=plate_number.strip().upper(),
        label=owner_name,
        site_ids=site_ids_int,
        note=note,
    )
    return RedirectResponse(url="/camera/vehicles", status_code=303)
