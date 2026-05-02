"""Strangers router."""
from fastapi import APIRouter, Request, Depends, Form
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
    return RedirectResponse("/strangers", status_code=303)

@router.delete("/{stranger_id}")
async def delete_stranger(stranger_id: int, user: dict = Depends(require_menu("strangers"))):
    get_db().delete_stranger_log(stranger_id)
    return RedirectResponse("/strangers", status_code=303)

@router.post("/register")
async def register_stranger(
    stranger_id: int = Form(...),
    user_id: int = Form(...),
    user_auth: dict = Depends(require_menu("strangers"))
):
    try:
        logging.info(f"Registering stranger {stranger_id} as user {user_id}")
        get_db().update_stranger_status(int(stranger_id), "registered", converted_user_id=int(user_id))
        return RedirectResponse("/strangers", status_code=303)
    except Exception as e:
        logging.exception("Failed to register stranger")
        raise e
