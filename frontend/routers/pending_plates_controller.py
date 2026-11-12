import logging
import re
from fastapi import Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints
from starlette.responses import RedirectResponse
from typing import Annotated, Optional
from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate_counts
from backend.services.config import normalize_plate
from frontend.routers.vehicles_queries import upsert_vehicle_whitelist
import frontend.routers.pending_plates_queries as queries

logger = logging.getLogger(__name__)


class BulkRejectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]] = Field(min_length=1)


def get_pending_plate_by_id(pending_id: str) -> dict | None:
    return queries.get_pending_plate_by_id_select_pending_plates(pending_id)


async def update_pending_plate(pending_id: str, request: Request, user: dict):
    try:
        data = await request.json()
    except ValueError:  # body rỗng hoặc không phải JSON
        data = None
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "message": "Dữ liệu gửi lên không hợp lệ."}, status_code=400)
    plate_raw = str(data.get("plate") or "").strip().upper()
    plate_norm = normalize_plate(plate_raw)
    if not re.fullmatch(r"[A-Z0-9]{4,16}", plate_norm):
        return JSONResponse({"ok": False, "message": "Biển số không hợp lệ."}, status_code=400)
    updated = queries.update_pending_plate_update_pending_plates(plate_raw, plate_norm, pending_id)
    if not updated:
        return JSONResponse({"ok": False, "message": "Không tìm thấy biển số chờ duyệt."}, status_code=404)
    return JSONResponse({"ok": True, "plate": plate_norm})


def delete_pending_plate_by_id(pending_id: str) -> bool:
    result = queries.delete_pending_plate_by_id_delete_pending_plates(pending_id)
    return bool(result["deleted"])


async def register_page(pending_id: str, request: Request, user: dict):
    plate = get_pending_plate_by_id(pending_id)
    if not plate:
        return HTMLResponse("Không tìm thấy biển số chờ duyệt.", status_code=404)

    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "pending_plates"
    ctx["plate"] = plate
    ctx["users"] = queries.register_page_select_users()
    return templates.TemplateResponse("pages/pending_plate_register.html", {"request": request, **ctx})


async def register_plate(
    pending_id: str,
    plate_number: str,
    owner_name: Optional[str],
    site_ids: list[PositiveInt],
    note: Optional[str],
    user: dict,
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


async def pending_page(request: Request, page: int, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "pending_plates"
    active_site_id = ctx.get("active_site_id", 0)
    total_plates = queries.count_pending_plates(site_id=active_site_id or None)
    paginated = paginate_counts(total_plates, page=page, per_page=20, base_url="/admin/pending_plates")
    plates = queries.get_pending_plates(
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


async def bulk_reject_plates(payload: BulkRejectInput, _: dict):
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


async def resolve_plate(pending_id: str, request: Request, user: dict):
    if not delete_pending_plate_by_id(pending_id):
        return HTMLResponse("Không tìm thấy biển số chờ duyệt.", status_code=404)
    logger.info("Resolved pending plate %s by %s", pending_id, user.get("username", "admin"))

    if request.headers.get("X-HTMX-Action") == "true" or request.headers.get("HX-Request") or request.query_params.get("hx"):
        return Response("", status_code=200)
    return RedirectResponse("/admin/pending_plates", status_code=303)
