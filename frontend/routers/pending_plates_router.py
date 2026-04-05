from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from pathlib import Path
from typing import List, Optional
from frontend.deps import get_db, get_menu_context, require_menu
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pending_plates")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

@router.get("/{pending_id}/register")
async def register_page(pending_id: str, request: Request, user: dict = Depends(require_menu("pending_plates"))):
    try:
        db = get_db()
        plate = db.get_pending_plate_by_id(pending_id)
        if not plate:
            logger.warning(f"Registration attempt for non-existent pending_id: {pending_id}")
            return RedirectResponse("/pending_plates", status_code=303)
            
        ctx = get_menu_context(request, user)
        ctx["current_menu"] = "pending_plates"
        ctx["plate"] = plate
        
        # Fetch users for the owner combobox
        users_list = db.select('users', columns=['id', 'full_name', 'username'], order_by='full_name ASC')
        ctx["users"] = users_list
        
        return templates.TemplateResponse("pages/pending_plate_register.html", {"request": request, **ctx})
    except Exception as e:
        logger.exception(f"Error rendering registration page for {pending_id}: {e}")
        raise

@router.post("/{pending_id}/register")
async def register_plate(
    pending_id: str,
    request: Request,
    plate_number: str = Form(...),
    owner_name: Optional[str] = Form(None),
    site_ids: List[str] = Form(default=[]),
    note: Optional[str] = Form(None),
    user: dict = Depends(require_menu("pending_plates")),
):
    db = get_db()
    username = user.get("username", "admin")
    
    # 1. Promote to whitelist
    site_ids_int = [int(s) for s in site_ids if s and s.strip()]
    db.upsert_vehicle_whitelist(
        plate_norm=plate_number.strip().upper(),
        label=owner_name,
        added_by=username,
        site_ids=site_ids_int,
        note=note,
    )
    
    # 2. Delete ALL pending records for this plate
    # We use the pending_id to identify the plate and then delete all associated pending records
    db.delete_pending_plate_by_id(pending_id)
    
    return RedirectResponse("/pending_plates", status_code=303)

@router.get("")
async def pending_page(request: Request, user: dict = Depends(require_menu("pending_plates"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "pending_plates"
    active_site_id = ctx.get("active_site_id", 0)
    active_site_id = int(active_site_id)
    
    raw_plates = get_db().get_pending_plates(site_id=active_site_id if active_site_id > 0 else None)
    
    # Proccess plates: handle both RealDictRow (from psycopg2) and fallback tuples
    plates = []
    for p in raw_plates:
        # Check if it behaves like a dict (has 'get' or is a dict-like object)
        if hasattr(p, 'get') or isinstance(p, dict):
            # It's already dict-like, just use it
            plates.append(p)
        else:
            # Fallback for old tuples: (id, event_id, plate_raw, ...)
            plates.append({
                "pending_id": p[0],
                "plate_raw": p[2],
                "plate_norm": p[3],
                "image_path": p[4],
                "first_seen": p[6]
            })
    
    ctx["plates"] = plates
    return templates.TemplateResponse("pages/pending_plates.html", {"request": request, **ctx})

@router.post("/{pending_id}/approve")
async def approve_plate(pending_id: str, request: Request, user: dict = Depends(require_menu("pending_plates"))):
    username = user.get("username", "admin")
    logger.info(f"HTMX-Action: Approving {pending_id} by {username}")
    success = get_db().delete_pending_plate_by_id(pending_id)
    
    # Ultra-reliable detection: Header OR Query Param
    if request.headers.get("X-HTMX-Action") == "true" or request.headers.get("HX-Request") or request.query_params.get("hx"):
        return Response("", status_code=200)
    return RedirectResponse("/pending_plates", status_code=303)

@router.post("/{pending_id}/reject")
async def reject_plate(pending_id: str, request: Request, user: dict = Depends(require_menu("pending_plates"))):
    username = user.get("username", "admin")
    logger.info(f"HTMX-Action: Deleting {pending_id} (Refused) by {username}")
    success = get_db().delete_pending_plate_by_id(pending_id)
    
    # Ultra-reliable detection: Header OR Query Param
    if request.headers.get("X-HTMX-Action") == "true" or request.headers.get("HX-Request") or request.query_params.get("hx"):
        return Response("", status_code=200)
    return RedirectResponse("/pending_plates", status_code=303)
