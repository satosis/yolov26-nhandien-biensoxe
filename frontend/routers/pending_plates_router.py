"""Pending plates router."""
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from pathlib import Path
from frontend.deps import get_db, get_menu_context, require_menu

router = APIRouter(prefix="/pending_plates")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

@router.get("")
async def pending_page(request: Request, user: dict = Depends(require_menu("pending_plates"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "pending_plates"
    active_site_id = ctx.get("active_site_id")
    ctx["plates"] = get_db().get_pending_plates(site_id=active_site_id if active_site_id and active_site_id > 0 else None)
    return templates.TemplateResponse("pages/pending_plates.html", {"request": request, **ctx})

@router.post("/{plate_norm}/approve")
async def approve_plate(plate_norm: str, request: Request, user: dict = Depends(require_menu("pending_plates"))):
    username = user.get("username", "admin")
    get_db().update_pending_status(plate_norm, "approved", username)
    return RedirectResponse("/pending_plates", status_code=303)

@router.post("/{plate_norm}/reject")
async def reject_plate(plate_norm: str, request: Request, user: dict = Depends(require_menu("pending_plates"))):
    username = user.get("username", "admin")
    get_db().update_pending_status(plate_norm, "rejected", username)
    return RedirectResponse("/pending_plates", status_code=303)
