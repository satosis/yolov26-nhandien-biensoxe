"""Users router."""
import logging
import bcrypt
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Optional
from frontend.deps import get_db, get_menu_context, require_menu

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("")
async def users_page(request: Request, user: dict = Depends(require_menu("users"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "users"
    db = get_db()
    ctx["all_users"] = db.get_all_users()
    ctx["roles"] = db.get_roles()
    return templates.TemplateResponse("pages/users.html", {"request": request, **ctx})


@router.post("")
async def create_user(
    request: Request,
    user: dict = Depends(require_menu("users")),
    username: str = Form(...),
    password: str = Form(...),
    full_name: Optional[str] = Form(None),
    role_name: str = Form(...),
    site_ids: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
):
    parsed_site_ids = None
    if site_ids:
        parsed_site_ids = [int(s.strip()) for s in site_ids.split(",") if s.strip()]

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    ok, msg = get_db().create_user(
        username=username,
        hashed_password=hashed,
        role_name=role_name,
        full_name=full_name or None,
        note=note or None,
        site_ids=parsed_site_ids,
    )
    if not ok:
        logger.warning("create_user failed: %s", msg)
    return RedirectResponse(url="/users", status_code=303)


@router.post("/{user_id}/edit")
async def edit_user(
    user_id: int,
    request: Request,
    user: dict = Depends(require_menu("users")),
    username: str = Form(...),
    password: Optional[str] = Form(None),
    full_name: Optional[str] = Form(None),
    role_name: str = Form(...),
    site_ids: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
):
    parsed_site_ids = None
    if site_ids:
        parsed_site_ids = [int(s.strip()) for s in site_ids.split(",") if s.strip()]

    hashed = None
    if password:
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    ok = get_db().update_user(
        user_id=user_id,
        username=username,
        role_name=role_name,
        hashed_password=hashed,
        full_name=full_name or None,
        note=note or None,
        site_ids=parsed_site_ids,
    )
    if not ok:
        logger.warning("update_user failed for user_id=%s", user_id)
    return RedirectResponse(url="/users", status_code=303)


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    request: Request,
    user: dict = Depends(require_menu("users")),
):
    ok = get_db().delete_user(user_id)
    if not ok:
        logger.warning("delete_user failed for user_id=%s", user_id)
    return RedirectResponse(url="/users", status_code=303)
