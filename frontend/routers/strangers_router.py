from fastapi import APIRouter, Request, Depends, Form, Body
import logging
from pathlib import Path
from starlette.responses import RedirectResponse
from frontend.deps import get_db, get_menu_context, require_menu, templates
from backend.ai.stranger_manager import get_stranger_manager

router = APIRouter(prefix="/strangers")

from frontend.routers.users_router import get_all_users


def get_stranger_logs(db, status: str | None = None, limit: int = 50, site_id: int | None = None) -> list:
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


def get_stranger_log(db, stranger_id: int):
    query = """
        SELECT sl.*, s.site_name, u.username as converted_username
        FROM stranger_logs sl
        LEFT JOIN sites s ON sl.site_id = s.id
        LEFT JOIN users u ON sl.converted_user_id = u.id
        WHERE sl.id = %s
    """
    return db._execute_query(query, (stranger_id,), fetch=True, commit=False)


def update_stranger_status(db, stranger_id: int, status: str,
                           converted_user_id: int | None = None, note: str | None = None) -> bool:
    query = """
        UPDATE stranger_logs
        SET status = %s,
            converted_user_id = COALESCE(%s, converted_user_id),
            note = COALESCE(%s, note)
        WHERE id = %s
    """
    return db._execute_query(query, (status, converted_user_id, note, stranger_id), commit=True)


def delete_stranger_log(db, stranger_id: int) -> bool:
    row = db._execute_query("SELECT image_path FROM stranger_logs WHERE id = %s", (stranger_id,), fetch=True, commit=False)
    if row and row.get("image_path"):
        Path(row["image_path"]).unlink(missing_ok=True)
    return db.delete("stranger_logs", {"id": stranger_id})


@router.get("")
async def strangers_page(request: Request, user: dict = Depends(require_menu("strangers"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "strangers"
    active_site_id = ctx.get("active_site_id")
    db = get_db()
    ctx["strangers"] = get_stranger_logs(db, status="new", limit=50, site_id=active_site_id if active_site_id and active_site_id > 0 else None)
    ctx["all_users"] = get_all_users(db)
    return templates.TemplateResponse("pages/strangers.html", {"request": request, **ctx})


@router.post("/{stranger_id}/review")
async def review_stranger(stranger_id: int, user: dict = Depends(require_menu("strangers"))):
    update_stranger_status(get_db(), stranger_id, "reviewed")
    return RedirectResponse("/admin/strangers", status_code=303)


@router.delete("/{stranger_id}")
async def delete_stranger(stranger_id: int, user: dict = Depends(require_menu("strangers"))):
    delete_stranger_log(get_db(), stranger_id)
    get_stranger_manager().remove(stranger_id)
    return RedirectResponse("/admin/strangers", status_code=303)


@router.post("/register")
async def register_stranger(
    stranger_id: int = Form(...),
    user_id: int = Form(...),
    user_auth: dict = Depends(require_menu("strangers"))
):
    logging.info("Registering stranger %s as user %s", stranger_id, user_id)
    update_stranger_status(get_db(), stranger_id, "registered", converted_user_id=user_id)
    get_stranger_manager().remove(stranger_id)
    return RedirectResponse("/admin/strangers", status_code=303)


@router.post("/bulk-register")
async def bulk_register_strangers(
    payload: dict = Body(...),
    user: dict = Depends(require_menu("strangers"))
):
    ids = [int(i) for i in payload.get("ids", [])]
    user_id = int(payload.get("user_id"))
    db = get_db()
    for sid in ids:
        update_stranger_status(db, sid, "registered", converted_user_id=user_id)
        get_stranger_manager().remove(sid)
    logging.info(f"Bulk registered {len(ids)} strangers as user {user_id}")
    return {"ok": True, "count": len(ids)}


@router.post("/bulk-delete")
async def bulk_delete_strangers(
    payload: dict = Body(...),
    user: dict = Depends(require_menu("strangers"))
):
    ids = [int(i) for i in payload.get("ids", [])]
    db = get_db()
    for sid in ids:
        delete_stranger_log(db, sid)
        get_stranger_manager().remove(sid)
    logging.info(f"Bulk deleted {len(ids)} strangers")
    return {"ok": True, "count": len(ids)}
