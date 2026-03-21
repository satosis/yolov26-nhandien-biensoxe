"""HPC Engine router."""
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from pathlib import Path
from frontend.deps import get_db, get_menu_context, require_menu

router = APIRouter(prefix="/hpc")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

@router.get("")
async def hpc_page(request: Request, user: dict = Depends(require_menu("hpc"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "hpc"
    ctx["cameras"] = get_db().get_all_cameras()
    return templates.TemplateResponse("pages/hpc.html", {"request": request, **ctx})
