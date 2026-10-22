import time
import bcrypt
from fastapi import Request, Response
from frontend.deps import get_role_permissions
from backend.services.database import db

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
    session_user = {
        "id": user["id"],
        "username": user["username"],
        "role": user.get("role", "Guest"),
        "full_name": user.get("full_name", ""),
        "allowed_menus": get_role_permissions(user.get("role")),
    }
    request.session["user"] = session_user


def logout_user(request: Request, response: Response):
    user = request.session.get("user")
    if user:
        if "id" in user:
            db.update("users", {"remember_token": None, "remember_token_expires": None}, {"id": user["id"]})
        if user.get("username"):
            response.set_cookie("last_username", user["username"], max_age=60*60*24*30, httponly=False, samesite="lax")
    request.session.clear()
    response.delete_cookie("remember_token")
