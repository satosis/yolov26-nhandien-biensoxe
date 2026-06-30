"""Auth router: login, logout, set-site."""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse, HTMLResponse
from pathlib import Path

from frontend.deps import get_db, get_current_user
from frontend.auth import (
    verify_password, check_rate_limit, record_failed,
    clear_attempts, remaining_lockout, login_user, logout_user,
)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/login")
async def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse("/admin/detection", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    key = f"{request.client.host}:{username}"
    if not check_rate_limit(key):
        secs = remaining_lockout(key)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Tài khoản bị khóa tạm. Thử lại sau {secs}s.",
            "last_username": username,
        })

    db = get_db()
    user = db.get_user(username)
    if user and verify_password(password, user["hashed_password"]):
        clear_attempts(key)

        response = RedirectResponse("/admin/detection", status_code=303)
        login_user(request, response, user)
        return response

    record_failed(key)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Sai tài khoản hoặc mật khẩu.",
        "last_username": username,
    })


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    response = RedirectResponse("/login", status_code=303)
    logout_user(request, response)
    return response


@router.post("/set-site")
async def set_site(request: Request, site_id: int = Form(0),
                   user: dict = Depends(get_current_user)):
    request.session["active_site_id"] = site_id if site_id else 0
    return HTMLResponse("")
