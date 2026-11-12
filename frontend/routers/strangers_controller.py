import logging
import os
from pathlib import Path

import math
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, PositiveInt
from starlette.responses import RedirectResponse

from frontend.deps import get_menu_context, templates
from backend.ai.stranger_manager import get_stranger_manager, merge_duplicate_strangers
from frontend.routers.users_controller import get_all_users, get_user_by_id
import frontend.routers.strangers_queries as queries

logger = logging.getLogger(__name__)


class BulkRegisterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[PositiveInt] = Field(min_length=1)
    user_id: PositiveInt


class BulkDeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[PositiveInt] = Field(min_length=1)


def count_stranger_logs(status: str | None = None, site_id: int | None = None) -> int:
    params: list = []
    where_clauses = []
    if status:
        where_clauses.append("LOWER(TRIM(status)) = LOWER(%s)")
        params.append(status)
    if site_id:
        where_clauses.append("(site_id = %s OR site_id IS NULL)")
        params.append(site_id)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    row = queries.count_stranger_logs_select_stranger_logs(params, where)
    return row["total"] if row else 0


def delete_stranger_log(stranger_id: int) -> bool:
    row = queries.delete_stranger_log_select_stranger_logs(stranger_id)
    if not row:
        return False
    path = row.get("image_path")
    if path:
        existed = os.path.lexists(path)
        Path(path).unlink(missing_ok=True)
        if not existed:
            logger.error("Stranger record %s references a missing image file", stranger_id)
    return queries.delete_stranger_log_delete_stranger_logs(stranger_id)


async def strangers_page(request: Request, page: int, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "strangers"
    active_site_id = ctx.get("active_site_id")
    merge_duplicate_strangers()

    total = count_stranger_logs(status="new", site_id=active_site_id or None)
    total_pages = max(1, math.ceil(total / 20))
    page_num = max(1, min(page, total_pages))
    offset = (page_num - 1) * 20

    ctx["strangers"] = queries.get_stranger_logs(status="new", limit=20, offset=offset, site_id=active_site_id or None)
    ctx["page"] = page_num
    ctx["total_pages"] = total_pages
    ctx["total"] = total
    ctx["page_base_url"] = "/admin/strangers?"
    ctx["all_users"] = get_all_users()
    return templates.TemplateResponse("pages/strangers.html", {"request": request, **ctx})


async def review_stranger(stranger_id: int, _: dict):
    if not queries.update_stranger_status(stranger_id, "reviewed"):
        return HTMLResponse("Không tìm thấy người lạ.", status_code=404)
    return RedirectResponse("/admin/strangers", status_code=303)


async def delete_stranger(stranger_id: int, _: dict):
    if not delete_stranger_log(stranger_id):
        return JSONResponse({"ok": False, "message": "Không tìm thấy người lạ."}, status_code=404)
    get_stranger_manager().remove(stranger_id)
    return {"ok": True}


async def register_stranger(stranger_id: int, user_id: int, _: dict):
    if not get_user_by_id(user_id):
        return HTMLResponse("Không tìm thấy người dùng.", status_code=404)
    if not queries.update_stranger_status(stranger_id, "registered", converted_user_id=user_id):
        return HTMLResponse("Không tìm thấy người lạ.", status_code=404)
    logger.info("Registered stranger %s as user %s", stranger_id, user_id)
    get_stranger_manager().remove(stranger_id)
    return RedirectResponse("/admin/strangers", status_code=303)


async def bulk_register_strangers(payload: BulkRegisterInput, _: dict):
    if not get_user_by_id(payload.user_id):
        return JSONResponse({"ok": False, "message": "Không tìm thấy người dùng."}, status_code=404)
    updated = 0
    missing = []
    for sid in dict.fromkeys(payload.ids):
        if queries.update_stranger_status(sid, "registered", converted_user_id=payload.user_id):
            updated += 1
            get_stranger_manager().remove(sid)
        else:
            missing.append(sid)
    if updated:
        logger.info("Bulk registered %s strangers as user %s", updated, payload.user_id)
    if missing:
        return JSONResponse(
            {"ok": False, "message": "Một số người lạ không tồn tại.", "count": updated, "missing": missing},
            status_code=404,
        )
    return {"ok": True, "count": updated}


async def bulk_delete_strangers(payload: BulkDeleteInput, _: dict):
    deleted = 0
    missing = []
    for sid in dict.fromkeys(payload.ids):
        if delete_stranger_log(sid):
            deleted += 1
            get_stranger_manager().remove(sid)
        else:
            missing.append(sid)
    if deleted:
        logger.info("Bulk deleted %s strangers", deleted)
    if missing:
        return JSONResponse(
            {"ok": False, "message": "Một số người lạ không tồn tại.", "count": deleted, "missing": missing},
            status_code=404,
        )
    return {"ok": True, "count": deleted}
