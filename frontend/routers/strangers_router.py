"""Strangers router."""
from fastapi import APIRouter, Request, Depends, Form, Body
from fastapi.templating import Jinja2Templates
import logging
from starlette.responses import RedirectResponse
from pathlib import Path
from frontend.deps import get_db, get_menu_context, require_menu

router = APIRouter(prefix="/strangers")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

@router.get("")
async def strangers_page(request: Request, user: dict = Depends(require_menu("strangers"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "strangers"
    active_site_id = ctx.get("active_site_id")
    ctx["strangers"] = get_db().get_stranger_logs(status="new", limit=50, site_id=active_site_id if active_site_id and active_site_id > 0 else None)
    ctx["all_users"] = get_db().get_all_users()
    return templates.TemplateResponse("pages/strangers.html", {"request": request, **ctx})

@router.post("/{stranger_id}/review")
async def review_stranger(stranger_id: int, user: dict = Depends(require_menu("strangers"))):
    get_db().update_stranger_status(stranger_id, "reviewed")
    return RedirectResponse("/admin/strangers", status_code=303)

@router.delete("/{stranger_id}")
async def delete_stranger(stranger_id: int, user: dict = Depends(require_menu("strangers"))):
    get_db().delete_stranger_log(stranger_id)
    try:
        from backend.core.stranger_manager import get_stranger_manager
        get_stranger_manager().remove(stranger_id)
    except Exception:
        pass
    return RedirectResponse("/admin/strangers", status_code=303)

@router.post("/register")
async def register_stranger(
    stranger_id: int = Form(...),
    user_id: int = Form(...),
    user_auth: dict = Depends(require_menu("strangers"))
):
    try:
        logging.info(f"Registering stranger {stranger_id} as user {user_id}")
        get_db().update_stranger_status(int(stranger_id), "registered", converted_user_id=int(user_id))
        try:
            from backend.core.stranger_manager import get_stranger_manager
            get_stranger_manager().remove(int(stranger_id))
        except Exception:
            pass
        return RedirectResponse("/admin/strangers", status_code=303)
    except Exception as e:
        logging.exception("Failed to register stranger")
        raise e

@router.post("/bulk-register")
async def bulk_register_strangers(
    payload: dict = Body(...),
    user: dict = Depends(require_menu("strangers"))
):
    ids = [int(i) for i in payload.get("ids", [])]
    user_id = int(payload.get("user_id"))
    db = get_db()
    for sid in ids:
        db.update_stranger_status(sid, "registered", converted_user_id=user_id)
        try:
            from backend.core.stranger_manager import get_stranger_manager
            get_stranger_manager().remove(sid)
        except Exception:
            pass
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
        db.delete_stranger_log(sid)
        try:
            from backend.core.stranger_manager import get_stranger_manager
            get_stranger_manager().remove(sid)
        except Exception:
            pass
    logging.info(f"Bulk deleted {len(ids)} strangers")
    return {"ok": True, "count": len(ids)}
