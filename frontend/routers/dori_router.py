"""DORI calculator router."""
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from pathlib import Path
from frontend.deps import get_menu_context, require_menu

router = APIRouter(prefix="/dori")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

@router.get("")
async def dori_page(request: Request, user: dict = Depends(require_menu("dori"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "dori"
    return templates.TemplateResponse("pages/dori.html", {"request": request, **ctx})
