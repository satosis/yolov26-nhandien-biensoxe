"""Pending plates router."""
import logging
import re
from fastapi import APIRouter, Request, Depends, Form, Response, Body
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from pathlib import Path
from typing import List, Optional
from frontend.deps import get_db, get_menu_context, require_menu
from backend.services.config import normalize_plate
from frontend.routers.vehicles_router import upsert_vehicle_whitelist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pending_plates")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def get_pending_plates(db, site_id=None) -> list[dict]:
    """Lấy danh sách biển số lạ chờ duyệt."""
    query = "SELECT p.*, p.first_seen_utc AS first_seen, s.site_name FROM pending_plates p LEFT JOIN sites s ON p.site_id = s.id WHERE LOWER(TRIM(p.status)) = 'pending'"
    params = []
    if site_id:
        query += " AND (p.site_id = %s OR p.site_id IS NULL)"
        params.append(site_id)
    query += " ORDER BY p.first_seen_utc DESC"
    return db.execute_query(query, tuple(params) if params else None, fetchall=True) or []


def get_pending_plate_by_id(db, pending_id: str) -> dict | None:
    """Lấy chi tiết một biển số chờ duyệt theo pending_id."""
    return db.execute_query(
        "SELECT pending_id, plate_raw, plate_norm, image_path, plate_type, site_id, first_seen_utc FROM pending_plates WHERE pending_id = %s",
        (pending_id,),
        fetch=True
    )


def delete_pending_plate_by_id(db, pending_id: str) -> bool:
    """Xóa biển số chờ duyệt và log lạ tương ứng."""
    row = db.execute_query(
        "SELECT plate_norm, image_path FROM pending_plates WHERE pending_id = %s",
        (pending_id,),
        fetch=True
    )
    if not row:
        return False
    plate_norm = row.get("plate_norm")
    img_path = row.get("image_path")
    if plate_norm:
        clean_plate = re.sub(r'[^A-Z0-9]', '', plate_norm.upper())
        db.execute_query("""
            DELETE FROM pending_plates 
            WHERE regexp_replace(plate_norm, '[^a-zA-Z0-9]', '', 'g') = %s 
            AND LOWER(TRIM(status)) = 'pending'
        """, (clean_plate,), commit=True)
    if img_path:
        db.execute_query("DELETE FROM stranger_logs WHERE image_path = %s", (img_path,), commit=True)
    return True


@router.get("/{pending_id}/register")
async def register_page(pending_id: str, request: Request, user: dict = Depends(require_menu("pending_plates"))):
    try:
        db = get_db()
        plate = get_pending_plate_by_id(db, pending_id)
        if not plate:
            logger.warning(f"Registration attempt for non-existent pending_id: {pending_id}")
            return RedirectResponse("/admin/pending_plates", status_code=303)
            
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
    upsert_vehicle_whitelist(
        db=db,
        plate_norm=normalize_plate(plate_number),
        label=owner_name,
        added_by=username,
        site_ids=site_ids_int,
        note=note,
    )
    
    # 2. Delete ALL pending records for this plate
    delete_pending_plate_by_id(db, pending_id)
    
    return RedirectResponse("/admin/pending_plates", status_code=303)


@router.get("")
async def pending_page(request: Request, user: dict = Depends(require_menu("pending_plates"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "pending_plates"
    active_site_id = ctx.get("active_site_id", 0)
    active_site_id = int(active_site_id)
    db = get_db()
    
    raw_plates = get_pending_plates(db, site_id=active_site_id if active_site_id > 0 else None)
    
    # Process plates: handle both RealDictRow (from psycopg2) and fallback tuples
    plates = []
    for p in raw_plates:
        if hasattr(p, 'get') or isinstance(p, dict):
            plates.append(p)
        else:
            plates.append({
                "pending_id": p[0],
                "plate_raw": p[2],
                "plate_norm": p[3],
                "image_path": p[4],
                "first_seen": p[6]
            })
    
    ctx["plates"] = plates
    return templates.TemplateResponse("pages/pending_plates.html", {"request": request, **ctx})


@router.post("/bulk-reject")
async def bulk_reject_plates(payload: dict = Body(...), user: dict = Depends(require_menu("pending_plates"))):
    ids = [str(i) for i in payload.get("ids", [])]
    db = get_db()
    for pid in ids:
        delete_pending_plate_by_id(db, pid)
    logger.info(f"Bulk rejected {len(ids)} pending plates")
    return {"ok": True, "count": len(ids)}


@router.post("/{pending_id}/approve")
async def approve_plate(pending_id: str, request: Request, user: dict = Depends(require_menu("pending_plates"))):
    username = user.get("username", "admin")
    logger.info(f"HTMX-Action: Approving {pending_id} by {username}")
    delete_pending_plate_by_id(get_db(), pending_id)
    
    if request.headers.get("X-HTMX-Action") == "true" or request.headers.get("HX-Request") or request.query_params.get("hx"):
        return Response("", status_code=200)
    return RedirectResponse("/admin/pending_plates", status_code=303)


@router.post("/{pending_id}/reject")
async def reject_plate(pending_id: str, request: Request, user: dict = Depends(require_menu("pending_plates"))):
    username = user.get("username", "admin")
    logger.info(f"HTMX-Action: Deleting {pending_id} (Refused) by {username}")
    delete_pending_plate_by_id(get_db(), pending_id)
    
    if request.headers.get("X-HTMX-Action") == "true" or request.headers.get("HX-Request") or request.query_params.get("hx"):
        return Response("", status_code=200)
    return RedirectResponse("/admin/pending_plates", status_code=303)
