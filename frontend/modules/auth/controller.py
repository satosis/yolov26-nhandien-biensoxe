import logging
import os
import secrets
import urllib.parse
import bcrypt
import requests
from fastapi import Request
from starlette.responses import RedirectResponse, HTMLResponse

from backend.services.settings_service import get_app_setting
from frontend.deps import templates
from frontend.modules.auth.service import (
    verify_password, check_rate_limit, record_failed,
    clear_attempts, remaining_lockout, login_user, logout_user,
)
import frontend.modules.auth.queries as queries

logger = logging.getLogger(__name__)


def _get_public_base_url(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc or ""
    if "localhost" in host or "127.0.0.1" in host:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
        return f"{proto}://{host}/"
    app_url = os.environ.get("APP_URL") or get_app_setting("APP_URL")
    if app_url:
        return app_url.rstrip("/") + "/"
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    return f"{proto}://{host}/"


def _get_google_client():
    client_id = os.environ.get("GOOGLE_CLIENT_ID") or get_app_setting("google_client_id") or get_app_setting("GOOGLE_CLIENT_ID") or ""
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET") or get_app_setting("google_client_secret") or get_app_setting("GOOGLE_CLIENT_SECRET") or ""
    return client_id.strip(), client_secret.strip()


# ---- LOGIN ----
async def login_page(request: Request):
    user = request.session.get("user")
    if user:
        dest = "/admin/detection" if user.get("role") == "Admin" else "/tai-khoan"
        return RedirectResponse(dest, status_code=303)
    error = request.query_params.get("error")
    success = request.query_params.get("success")
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": error,
        "success": success,
        "view": "login",
    })


async def login_submit(request: Request, username: str, password: str):
    key = f"{request.client.host}:{username}"
    if not check_rate_limit(key):
        secs = remaining_lockout(key)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Tài khoản bị khóa tạm. Thử lại sau {secs}s.",
            "last_username": username,
            "view": "login",
        }, status_code=429)

    user = queries.get_user_by_username(username)
    if user and verify_password(password, user["hashed_password"]):
        clear_attempts(key)
        dest = "/admin/detection" if user.get("role") == "Admin" else "/tai-khoan"
        response = RedirectResponse(dest, status_code=303)
        login_user(request, response, user)
        return response

    record_failed(key)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Sai tài khoản hoặc mật khẩu.",
        "last_username": username,
        "view": "login",
    }, status_code=401)


# ---- REGISTER ----
async def register_page(request: Request):
    user = request.session.get("user")
    if user:
        dest = "/admin/detection" if user.get("role") == "Admin" else "/tai-khoan"
        return RedirectResponse(dest, status_code=303)
    error = request.query_params.get("error")
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": error,
        "view": "register",
    })


async def register_submit(
    request: Request,
    username: str,
    password: str,
    confirm_password: str,
    full_name: str,
    email: str,
):
    username = username.strip()
    if len(username) < 3:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Tên đăng nhập phải có ít nhất 3 ký tự.",
            "view": "register",
            "last_username": username,
        }, status_code=400)

    if password != confirm_password:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Mật khẩu xác nhận không trùng khớp.",
            "view": "register",
            "last_username": username,
        }, status_code=400)

    if len(password) < 6:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Mật khẩu phải có độ dài tối thiểu 6 ký tự.",
            "view": "register",
            "last_username": username,
        }, status_code=400)

    existing = queries.get_user_by_username(username)
    if existing:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Tài khoản '{username}' đã tồn tại.",
            "view": "register",
            "last_username": username,
        }, status_code=400)

    from backend.repositories.user_repo import create_user_record
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    ok, msg = create_user_record(
        username=username,
        hashed_password=hashed,
        role_name="Guest",
        full_name=full_name.strip() or username,
        email=email.strip() or None,
    )
    if not ok:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Không thể tạo tài khoản: {msg}",
            "view": "register",
            "last_username": username,
        }, status_code=400)

    user = queries.get_user_by_username(username)
    dest = "/admin/detection" if user.get("role") == "Admin" else "/tai-khoan"
    response = RedirectResponse(dest, status_code=303)
    login_user(request, response, user)
    return response


# ---- FORGOT PASSWORD ----
async def forgot_password_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
        "view": "forgot",
    })


async def forgot_password_submit(request: Request, account: str):
    account = account.strip()
    return templates.TemplateResponse("login.html", {
        "request": request,
        "success": "Nếu tài khoản tồn tại trong hệ thống, yêu cầu đã được tiếp nhận. Vui lòng liên hệ quản trị viên để hoàn tất thiết lập lại.",
        "view": "forgot",
    })


# ---- GOOGLE OAUTH ----
async def auth_google(request: Request):
    client_id, _ = _get_google_client()
    if not client_id:
        return RedirectResponse("/login?error=" + urllib.parse.quote("Chưa cấu hình Google Client ID."), status_code=303)

    state = secrets.token_urlsafe(16)
    request.session["google_oauth_state"] = state
    base_url = _get_public_base_url(request)
    redirect_uri = urllib.parse.urljoin(base_url, "auth/google/callback")

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    google_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(google_url, status_code=303)


async def auth_google_callback(request: Request, code: str, state: str, error: str):
    if error or not code:
        err_msg = error or "Đăng nhập Google bị hủy hoặc thất bại."
        return RedirectResponse(f"/login?error={urllib.parse.quote(err_msg)}", status_code=303)

    saved_state = request.session.pop("google_oauth_state", None)
    if not saved_state or saved_state != state:
        return RedirectResponse("/login?error=" + urllib.parse.quote("Phiên xác thực Google không hợp lệ (sai state)."), status_code=303)

    client_id, client_secret = _get_google_client()
    if not client_id or not client_secret:
        return RedirectResponse("/login?error=" + urllib.parse.quote("Thiếu cấu hình Google Client Secret."), status_code=303)

    base_url = _get_public_base_url(request)
    redirect_uri = urllib.parse.urljoin(base_url, "auth/google/callback")

    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        err = token_data.get("error_description") or token_data.get("error") or "Không lấy được access token từ Google."
        return RedirectResponse(f"/login?error={urllib.parse.quote(str(err))}", status_code=303)

    profile_resp = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    profile = profile_resp.json()
    sub = profile.get("sub")
    email = profile.get("email")
    name = profile.get("name") or profile.get("given_name") or "Người dùng Google"
    picture = profile.get("picture") or ""

    if not sub and not email:
        return RedirectResponse("/login?error=" + urllib.parse.quote("Không lấy được thông tin tài khoản Google."), status_code=303)

    user = None
    if email:
        user = queries.select_user_by_email(email)

    username = f"gg_{sub[:12]}" if sub else (email.split("@")[0] if email else f"gg_{secrets.token_hex(4)}")
    if not user:
        user = queries.get_user_by_username(username)

    if not user:
        from backend.repositories.user_repo import create_user_record
        random_pwd = bcrypt.hashpw(secrets.token_bytes(24), bcrypt.gensalt()).decode()
        ok, msg = create_user_record(
            username=username,
            hashed_password=random_pwd,
            role_name="Guest",
            full_name=name,
            email=email,
            avatar=picture,
        )
        if not ok:
            return RedirectResponse(f"/login?error={urllib.parse.quote('Không thể khởi tạo tài khoản: ' + msg)}", status_code=303)
        user = queries.get_user_by_username(username)
    else:
        queries.update_user_name_and_avatar(name, picture, user)
        user = queries.get_user_by_username(user["username"])

    dest = "/admin/detection" if user.get("role") == "Admin" else "/tai-khoan"
    response = RedirectResponse(dest, status_code=303)
    login_user(request, response, user)
    return response


# ---- FACEBOOK OAUTH ----
async def auth_facebook(request: Request):
    app_id = get_app_setting("FB_APP_ID") or os.environ.get("FB_APP_ID", "")
    if not app_id:
        return RedirectResponse("/login?error=" + urllib.parse.quote("Chưa cấu hình Facebook App ID."), status_code=303)

    state = secrets.token_urlsafe(16)
    request.session["fb_oauth_state"] = state
    base_url = _get_public_base_url(request)
    redirect_uri = urllib.parse.urljoin(base_url, "auth/facebook/callback")

    fb_url = (
        "https://www.facebook.com/v26.0/dialog/oauth?"
        + urllib.parse.urlencode({
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": "email,public_profile",
        })
    )
    return RedirectResponse(fb_url, status_code=303)


async def auth_facebook_callback(request: Request, code: str, state: str, error: str, error_description: str):
    if error or not code:
        err_msg = error_description or "Đăng nhập Facebook bị hủy hoặc thất bại."
        return RedirectResponse(f"/login?error={urllib.parse.quote(err_msg)}", status_code=303)

    saved_state = request.session.pop("fb_oauth_state", None)
    if not saved_state or saved_state != state:
        return RedirectResponse("/login?error=" + urllib.parse.quote("Phiên xác thực Facebook không hợp lệ (sai state)."), status_code=303)

    app_id = get_app_setting("FB_APP_ID") or os.environ.get("FB_APP_ID", "")
    app_secret = get_app_setting("FB_APP_SECRET") or os.environ.get("FB_APP_SECRET", "")
    if not app_id or not app_secret:
        return RedirectResponse("/login?error=" + urllib.parse.quote("Thiếu cấu hình Facebook App Secret."), status_code=303)

    base_url = _get_public_base_url(request)
    redirect_uri = urllib.parse.urljoin(base_url, "auth/facebook/callback")

    token_resp = requests.get(
        "https://graph.facebook.com/v26.0/oauth/access_token",
        params={
            "client_id": app_id,
            "client_secret": app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=10,
    )
    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        err = token_data.get("error", {}).get("message", "Không lấy được access token từ Facebook.")
        return RedirectResponse(f"/login?error={urllib.parse.quote(err)}", status_code=303)

    profile_resp = requests.get(
        "https://graph.facebook.com/me",
        params={"fields": "id,name,email,picture.type(large)", "access_token": access_token},
        timeout=10,
    )
    profile = profile_resp.json()
    fb_id = profile.get("id")
    if not fb_id:
        return RedirectResponse("/login?error=" + urllib.parse.quote("Không lấy được thông tin tài khoản Facebook."), status_code=303)

    fb_name = profile.get("name") or "Người dùng Facebook"
    fb_email = profile.get("email")
    fb_pic = profile.get("picture", {}).get("data", {}).get("url") or ""
    username = f"fb_{fb_id}"

    user = queries.get_user_by_username(username)
    if not user and fb_email:
        user = queries.select_user_by_email(fb_email)

    if not user:
        from backend.repositories.user_repo import create_user_record
        random_pwd = bcrypt.hashpw(secrets.token_bytes(24), bcrypt.gensalt()).decode()
        ok, msg = create_user_record(
            username=username,
            hashed_password=random_pwd,
            role_name="Guest",
            full_name=fb_name,
            email=fb_email,
            avatar=fb_pic,
        )
        if not ok:
            return RedirectResponse(f"/login?error={urllib.parse.quote('Không thể khởi tạo tài khoản: ' + msg)}", status_code=303)
        user = queries.get_user_by_username(username)
    else:
        queries.update_user_name_and_avatar(fb_name, fb_pic, user)
        user = queries.get_user_by_username(user["username"])

    dest = "/admin/detection" if user.get("role") == "Admin" else "/tai-khoan"
    response = RedirectResponse(dest, status_code=303)
    login_user(request, response, user)
    return response


async def logout(request: Request):
    response = RedirectResponse("/tai-khoan", status_code=303)
    logout_user(request, response)
    return response


async def set_site(request: Request, site_id: int, user: dict):
    request.session["active_site_id"] = site_id
    return HTMLResponse("")


async def set_company(request: Request, company_id: int, user: dict):
    request.session["active_company_id"] = company_id if company_id > 0 else None
    return HTMLResponse("")
