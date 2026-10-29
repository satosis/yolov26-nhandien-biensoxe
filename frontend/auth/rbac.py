from fastapi import Request, HTTPException
from backend.services.database import db

MENU_PERMISSION_OVERRIDES = {
    "service_accounts": ("vehicles", "users", "rbac"),
    "api_keys": ("vehicles", "users", "rbac"),
    "companies": ("vehicles", "users"),
    "staff": ("vehicles", "users"),
    "enterprise": ("vehicles", "users"),
    "payroll": ("vehicles", "users"),
    "departments": ("vehicles", "users"),
    "attendance": ("vehicles", "users"),
    "advances": ("vehicles", "users"),
    "assets": ("vehicles", "users"),
    "debts": ("vehicles", "users"),
    "tax_checklist": ("vehicles", "users"),
    "sales": ("vehicles", "users"),
    "suppliers": ("vehicles", "users"),
    "advertising": ("vehicles", "users"),
    "advertising_keywords": ("vehicles", "users"),
    "self_test": ("vehicles", "users"),
}


from typing import Optional
from backend.app_types import SessionUser


def get_current_user(request: Request) -> SessionUser:
    user = request.session.get("user")
    if not user:
        p = request.url.path
        if "/api/" in p or p.endswith("/api") or request.headers.get("accept", "").startswith("application/json") or request.headers.get("x-requested-with") == "XMLHttpRequest":
            raise HTTPException(status_code=401, detail="Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.")
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def get_optional_user(request: Request) -> Optional[SessionUser]:
    return request.session.get("user")


from backend.repositories.role_repo import get_role_permissions


def require_menu(menu_key: str):
    def _check(request: Request) -> dict:
        user = get_current_user(request)
        allowed_str = get_role_permissions(user.get("role"))
        if allowed_str != user.get("allowed_menus", ""):
            user["allowed_menus"] = allowed_str
            request.session["user"] = user
        allowed = allowed_str
        if allowed != "*":
            allowed_list = [t.strip() for t in allowed.split(",") if t.strip()]
            if menu_key not in allowed_list:
                overrides = MENU_PERMISSION_OVERRIDES.get(menu_key)
                if overrides and any(req in allowed_list for req in overrides):
                    return user
                raise HTTPException(status_code=403, detail="Không có quyền truy cập")
        return user
    return _check
