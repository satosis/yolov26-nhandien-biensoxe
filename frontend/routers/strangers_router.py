"""Strangers router."""
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
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
    ctx["strangers"] = get_db().get_stranger_logs(status=None, limit=50, site_id=active_site_id if active_site_id and active_site_id > 0 else None)
    return templates.TemplateResponse("pages/strangers.html", {"request": request, **ctx})

@router.post("/{stranger_id}/review")
async def review_stranger(stranger_id: int, user: dict = Depends(require_menu("strangers"))):
    get_db().update_stranger_status(stranger_id, "reviewed")
    return RedirectResponse("/strangers", status_code=303)

@router.delete("/{stranger_id}")
async def delete_stranger(stranger_id: int, user: dict = Depends(require_menu("strangers"))):
    get_db().delete_stranger_log(stranger_id)
    return RedirectResponse("/strangers", status_code=303)
