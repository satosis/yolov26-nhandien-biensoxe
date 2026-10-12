import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, PositiveInt
from starlette.responses import RedirectResponse

from backend.services.database import db
from frontend.deps import get_menu_context, require_menu, templates
from backend.ai.stranger_manager import get_stranger_manager
from frontend.routers.users_router import get_all_users, get_user_by_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/strangers")


class BulkRegisterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[PositiveInt] = Field(min_length=1)
    user_id: PositiveInt


class BulkDeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[PositiveInt] = Field(min_length=1)


def get_stranger_logs(status: str | None = None, limit: int = 50, site_id: int | None = None) -> list:
    params: list = []
    where_clauses = []
    if status:
        where_clauses.append("LOWER(TRIM(sl.status)) = LOWER(%s)")
        params.append(status)
    if site_id:
        where_clauses.append("(sl.site_id = %s OR sl.site_id IS NULL)")
        params.append(site_id)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.append(limit)
    query = f"""
        SELECT sl.*, s.site_name, u.username AS converted_username
        FROM stranger_logs sl
        LEFT JOIN sites s ON sl.site_id = s.id
        LEFT JOIN users u ON sl.converted_user_id = u.id
        {where}
        ORDER BY COALESCE(sl.last_seen, sl.event_time) DESC
        LIMIT %s
    """
    return db._execute_query(query, tuple(params), fetchall=True, commit=False)


def get_stranger_log(stranger_id: int):
    query = """
        SELECT sl.*, s.site_name, u.username as converted_username
        FROM stranger_logs sl
        LEFT JOIN sites s ON sl.site_id = s.id
        LEFT JOIN users u ON sl.converted_user_id = u.id
        WHERE sl.id = %s
    """
    return db._execute_query(query, (stranger_id,), fetch=True, commit=False)


def update_stranger_status(stranger_id: int, status: str,
                           converted_user_id: int | None = None, note: str | None = None) -> bool:
    query = """
        UPDATE stranger_logs
        SET status = %s,
            converted_user_id = COALESCE(%s, converted_user_id),
            note = COALESCE(%s, note)
        WHERE id = %s
    """
    return db._execute_query(query, (status, converted_user_id, note, stranger_id), commit=True)


def delete_stranger_log(stranger_id: int) -> bool:
    row = db.select("stranger_logs", columns="image_path", conditions={"id": stranger_id}, fetchall=False)
    if not row:
        return False
    if row.get("image_path"):
        Path(row["image_path"]).unlink(missing_ok=True)
    return db.delete("stranger_logs", {"id": stranger_id})


@router.get("")
async def strangers_page(request: Request, user: dict = Depends(require_menu("strangers"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "strangers"
    active_site_id = ctx.get("active_site_id")
    ctx["strangers"] = get_stranger_logs(status="new", limit=50, site_id=active_site_id if active_site_id and active_site_id > 0 else None)
    ctx["all_users"] = get_all_users()
    return templates.TemplateResponse("pages/strangers.html", {"request": request, **ctx})


@router.post("/{stranger_id}/review")
async def review_stranger(stranger_id: int, user: dict = Depends(require_menu("strangers"))):
    if not update_stranger_status(stranger_id, "reviewed"):
        return HTMLResponse("Không tìm thấy người lạ.", status_code=404)
    return RedirectResponse("/admin/strangers", status_code=303)


@router.delete("/{stranger_id}")
async def delete_stranger(stranger_id: int, user: dict = Depends(require_menu("strangers"))):
    if not delete_stranger_log(stranger_id):
        return JSONResponse({"ok": False, "message": "Không tìm thấy người lạ."}, status_code=404)
    get_stranger_manager().remove(stranger_id)
    return {"ok": True}


@router.post("/register")
async def register_stranger(
    stranger_id: int = Form(..., gt=0),
    user_id: int = Form(..., gt=0),
    user_auth: dict = Depends(require_menu("strangers"))
):
    if not get_user_by_id(user_id):
        return HTMLResponse("Không tìm thấy người dùng.", status_code=404)
    if not update_stranger_status(stranger_id, "registered", converted_user_id=user_id):
        return HTMLResponse("Không tìm thấy người lạ.", status_code=404)
    logger.info("Registered stranger %s as user %s", stranger_id, user_id)
    get_stranger_manager().remove(stranger_id)
    return RedirectResponse("/admin/strangers", status_code=303)


@router.post("/bulk-register")
async def bulk_register_strangers(
    payload: BulkRegisterInput,
    user: dict = Depends(require_menu("strangers"))
):
    if not get_user_by_id(payload.user_id):
        return JSONResponse({"ok": False, "message": "Không tìm thấy người dùng."}, status_code=404)
    updated = 0
    missing = []
    for sid in dict.fromkeys(payload.ids):
        if update_stranger_status(sid, "registered", converted_user_id=payload.user_id):
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


@router.post("/bulk-delete")
async def bulk_delete_strangers(
    payload: BulkDeleteInput,
    user: dict = Depends(require_menu("strangers"))
):
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
