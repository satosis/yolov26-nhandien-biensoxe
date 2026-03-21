"""Config router."""
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from pathlib import Path
from frontend.deps import get_db, get_menu_context, require_menu

router = APIRouter(prefix="/config")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

@router.get("")
async def config_page(request: Request, user: dict = Depends(require_menu("config"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "config"
    db = get_db()
    ctx["settings"] = db.get_all_app_settings()
    return templates.TemplateResponse("pages/config.html", {"request": request, **ctx})

@router.post("")
async def save_config(request: Request, user: dict = Depends(require_menu("config"))):
    form = await request.form()
    data = {k: v for k, v in form.items() if k != "_method"}
    get_db().set_many_app_settings(data)
    return RedirectResponse("/config", status_code=303)
