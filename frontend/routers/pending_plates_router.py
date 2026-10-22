import logging
import re
from fastapi import APIRouter, Request, Depends, Form, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints
from starlette.responses import RedirectResponse
from typing import Annotated, Optional
from backend.services.database import db
from frontend.deps import get_menu_context, require_menu, templates
from frontend.pagination import paginate_counts
from backend.services.config import normalize_plate
from frontend.routers.vehicles_router import upsert_vehicle_whitelist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pending_plates")


class BulkRejectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]] = Field(min_length=1)


def get_pending_plates(site_id=None, limit=None, offset=None) -> list[dict]:
    query = "SELECT p.*, p.first_seen_utc AS first_seen, s.site_name FROM pending_plates p LEFT JOIN sites s ON p.site_id = s.id WHERE LOWER(TRIM(p.status)) = 'pending'"
    params = []
    if site_id:
        query += " AND (p.site_id = %s OR p.site_id IS NULL)"
        params.append(site_id)
    query += " ORDER BY p.first_seen_utc DESC"
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)
    return db.execute_query(query, tuple(params) if params else None, fetchall=True)


def count_pending_plates(site_id=None) -> int:
    query = "SELECT COUNT(*) AS total FROM pending_plates p WHERE LOWER(TRIM(p.status)) = 'pending'"
    params = []
    if site_id:
        query += " AND (p.site_id = %s OR p.site_id IS NULL)"
        params.append(site_id)
    row = db.execute_query(query, tuple(params) if params else None, fetch=True)
    return int((row or {}).get("total") or 0)


def get_pending_plate_by_id(pending_id: str) -> dict | None:
    return db.execute_query(
        "SELECT pending_id, plate_raw, plate_norm, image_path, plate_type, site_id, first_seen_utc "
        "FROM pending_plates WHERE pending_id = %s AND LOWER(TRIM(status)) = 'pending'",
        (pending_id,),
        fetch=True
    )


@router.put("/{pending_id}")
async def update_pending_plate(
    pending_id: str,
    request: Request,
    user: dict = Depends(require_menu("pending_plates")),
):
    data = await request.json()
    plate_raw = str(data.get("plate") or "").strip().upper()
    plate_norm = normalize_plate(plate_raw)
    if not re.fullmatch(r"[A-Z0-9]{4,16}", plate_norm):
        return JSONResponse({"ok": False, "message": "Biển số không hợp lệ."}, status_code=400)
    updated = db.execute_query(
        """UPDATE pending_plates
              SET plate_raw = %s, plate_norm = %s
            WHERE pending_id = %s AND LOWER(TRIM(status)) = 'pending'
            RETURNING pending_id""",
        (plate_raw, plate_norm, pending_id), fetch=True, commit=True,
    )
    if not updated:
        return JSONResponse({"ok": False, "message": "Không tìm thấy biển số chờ duyệt."}, status_code=404)
    return JSONResponse({"ok": True, "plate": plate_norm})


def delete_pending_plate_by_id(pending_id: str) -> bool:
    result = db.execute_query(
        """WITH deleted_plates AS (
               DELETE FROM pending_plates AS plate
               USING pending_plates AS target
               WHERE target.pending_id = %s
                 AND LOWER(TRIM(target.status)) = 'pending'
                 AND LOWER(TRIM(plate.status)) = 'pending'
                 AND (
                     plate.pending_id = target.pending_id
                     OR (
                         target.plate_norm IS NOT NULL
                         AND target.plate_norm <> ''
                         AND UPPER(regexp_replace(plate.plate_norm, '[^a-zA-Z0-9]', '', 'g')) =
                             UPPER(regexp_replace(target.plate_norm, '[^a-zA-Z0-9]', '', 'g'))
                     )
                 )
               RETURNING plate.pending_id, plate.image_path
           ), deleted_logs AS (
               DELETE FROM stranger_logs
               WHERE image_path IN (
                   SELECT image_path FROM deleted_plates
                   WHERE pending_id = %s AND image_path IS NOT NULL
               )
               RETURNING id
           )
           SELECT EXISTS(
               SELECT 1 FROM deleted_plates WHERE pending_id = %s
           ) AS deleted""",
        (pending_id, pending_id, pending_id), fetch=True, commit=True,
    )
    return bool(result["deleted"])


@router.get("/{pending_id}/register")
async def register_page(pending_id: str, request: Request, user: dict = Depends(require_menu("pending_plates"))):
    plate = get_pending_plate_by_id(pending_id)
    if not plate:
        return HTMLResponse("Không tìm thấy biển số chờ duyệt.", status_code=404)

    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "pending_plates"
    ctx["plate"] = plate
    ctx["users"] = db.select('users', columns=['id', 'full_name', 'username'], order_by='full_name ASC')
    return templates.TemplateResponse("pages/pending_plate_register.html", {"request": request, **ctx})


@router.post("/{pending_id}/register")
async def register_plate(
    pending_id: str,
    plate_number: str = Form(...),
    owner_name: Optional[str] = Form(None),
    site_ids: list[PositiveInt] = Form(default=[]),
    note: Optional[str] = Form(None),
    user: dict = Depends(require_menu("pending_plates")),
):
    if not get_pending_plate_by_id(pending_id):
        return HTMLResponse("Không tìm thấy biển số chờ duyệt.", status_code=404)
    plate_norm = normalize_plate(plate_number)
    if not re.fullmatch(r"[A-Z0-9]+", plate_norm):
        return HTMLResponse("Biển số không hợp lệ.", status_code=422)
    username = user.get("username", "admin")
    if not upsert_vehicle_whitelist(
        plate_number=plate_number.strip().upper(),
        label=owner_name,
        added_by=username,
        site_ids=site_ids,
        note=note,
    ):
        return HTMLResponse("Không thể đăng ký biển số.", status_code=500)
    
    delete_pending_plate_by_id(pending_id)
    return RedirectResponse("/admin/pending_plates", status_code=303)


@router.get("")
async def pending_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu("pending_plates")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "pending_plates"
    active_site_id = ctx.get("active_site_id", 0)
    total_plates = count_pending_plates(site_id=active_site_id or None)
    paginated = paginate_counts(total_plates, page=page, per_page=20, base_url="/admin/pending_plates")
    plates = get_pending_plates(
        site_id=active_site_id or None,
        limit=paginated["per_page"],
        offset=(paginated["page"] - 1) * paginated["per_page"],
    ) or []

    ctx["plates"] = plates
    ctx["page"] = paginated["page"]
    ctx["total_pages"] = paginated["total_pages"]
    ctx["total"] = paginated["total"]
    ctx["page_base_url"] = paginated["page_base_url"]
    return templates.TemplateResponse("pages/pending_plates.html", {"request": request, **ctx})


@router.post("/bulk-reject")
async def bulk_reject_plates(payload: BulkRejectInput, _: dict = Depends(require_menu("pending_plates"))):
    deleted = 0
    missing = []
    for pending_id in dict.fromkeys(payload.ids):
        if delete_pending_plate_by_id(pending_id):
            deleted += 1
        else:
            missing.append(pending_id)
    if deleted:
        logger.info("Bulk rejected %s pending plates", deleted)
    if missing:
        return JSONResponse(
            {"ok": False, "message": "Một số biển số không tồn tại.", "count": deleted, "missing": missing},
            status_code=404,
        )
    return {"ok": True, "count": deleted}


@router.post("/{pending_id}/approve")
@router.post("/{pending_id}/reject")
async def resolve_plate(pending_id: str, request: Request, user: dict = Depends(require_menu("pending_plates"))):
    if not delete_pending_plate_by_id(pending_id):
        return HTMLResponse("Không tìm thấy biển số chờ duyệt.", status_code=404)
    logger.info("Resolved pending plate %s by %s", pending_id, user.get("username", "admin"))

    if request.headers.get("X-HTMX-Action") == "true" or request.headers.get("HX-Request") or request.query_params.get("hx"):
        return Response("", status_code=200)
    return RedirectResponse("/admin/pending_plates", status_code=303)
