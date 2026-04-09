"""Authentication helpers for webapp."""
import time
import bcrypt
from fastapi import Request, Response
from frontend.deps import get_db

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 900
_attempts: dict[str, dict] = {}


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def check_rate_limit(key: str) -> bool:
    info = _attempts.get(key)
    if not info:
        return True
    if info["count"] >= MAX_LOGIN_ATTEMPTS:
        if time.time() - info["last"] < LOCKOUT_SECONDS:
            return False
        _attempts.pop(key, None)
    return True


def record_failed(key: str):
    info = _attempts.get(key, {"count": 0, "last": 0})
    info["count"] += 1
    info["last"] = time.time()
    _attempts[key] = info


def clear_attempts(key: str):
    _attempts.pop(key, None)


def remaining_lockout(key: str) -> int:
    info = _attempts.get(key)
    if not info:
        return 0
    return max(0, int(LOCKOUT_SECONDS - (time.time() - info["last"])))


def login_user(request: Request, response: Response, user: dict):
    """Store user in session and create remember token."""
    db = get_db()
    roles = db.get_roles()
    role_info = next((r for r in roles if r["role_name"] == user.get("role")), None)
    allowed_menus = role_info.get("allowed_menus", "") if role_info else ""

    session_user = {
        "id": user["id"],
        "username": user["username"],
        "role": user.get("role", "Guest"),
        "full_name": user.get("full_name", ""),
        "allowed_menus": allowed_menus,
    }
    request.session["user"] = session_user

    # DB method generates token internally, returns it
    token = db.create_remember_token(user["id"])
    if token:
        response.set_cookie(
            "remember_token", token,
            max_age=60 * 60 * 24 * 7,
            httponly=True, samesite="lax",
        )


def try_remember_token(request: Request) -> dict | None:
    """Try to restore session from remember_token cookie."""
    token = request.cookies.get("remember_token")
    if not token:
        return None
    db = get_db()
    user = db.get_user_by_remember_token(token)
    if not user:
        return None
    roles = db.get_roles()
    role_info = next((r for r in roles if r["role_name"] == user.get("role")), None)
    allowed_menus = role_info.get("allowed_menus", "") if role_info else ""
    session_user = {
        "id": user["id"],
        "username": user["username"],
        "role": user.get("role", "Guest"),
        "full_name": user.get("full_name", ""),
        "allowed_menus": allowed_menus,
    }
    request.session["user"] = session_user
    return session_user


def logout_user(request: Request, response: Response):
    """Clear session and cookies. Save username for login form prefill."""
    user = request.session.get("user")
    if user:
        if "id" in user:
            get_db().delete_remember_tokens(user["id"])
        # Save last username so login form can prefill it
        if user.get("username"):
            response.set_cookie("last_username", user["username"], max_age=60*60*24*30, httponly=False, samesite="lax")
    request.session.clear()
    response.delete_cookie("remember_token")
