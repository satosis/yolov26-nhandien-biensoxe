from fastapi import APIRouter, Request, Form, Depends
from starlette.responses import RedirectResponse, HTMLResponse

from backend.services.database import db
from frontend.deps import get_current_user, templates
from frontend.auth import (
    verify_password, check_rate_limit, record_failed,
    clear_attempts, remaining_lockout, login_user, logout_user,
)

router = APIRouter()


@router.get("/login")
async def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse("/admin/detection", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


def get_user_by_username(username: str) -> dict | None:
    query = """
        SELECT u.*, r.role_name as role 
        FROM users u 
        JOIN roles r ON u.role_id = r.id 
        WHERE u.username = %s
    """
    return db.execute_query(query, (username,), fetch=True)


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

    user = get_user_by_username(username)
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
