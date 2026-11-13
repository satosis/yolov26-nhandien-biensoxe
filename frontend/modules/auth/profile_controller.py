"""Trang thông tin cá nhân: người dùng đang đăng nhập tự sửa hồ sơ, ảnh đại diện và mật khẩu của mình."""
import uuid
from pathlib import Path

import bcrypt
from fastapi import Request, UploadFile

from backend.repositories.user_repo import (
    get_password_hash,
    get_user_by_id,
    set_user_password,
    update_profile_record,
)
from backend.services import config as cfg
from frontend.deps import get_menu_context, templates
from frontend.modules.auth.service import verify_password
from frontend.modules.contact_validation import clean_email, clean_phone
from frontend.redirects import redirect_with_query


PROFILE_URL = "/profile"
AVATAR_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
AVATAR_DIR = Path(cfg.BASE_DIR) / "upload" / "avatars"


async def save_avatar(upload: UploadFile | None) -> str:
    """Lưu ảnh đại diện vào upload/avatars; trả đường dẫn công khai, hoặc chuỗi rỗng khi không chọn ảnh."""
    filename = (upload.filename or "").strip() if upload else ""
    if not filename:
        return ""
    suffix = Path(filename).suffix.lower()
    if suffix not in AVATAR_TYPES:
        raise ValueError("Chỉ hỗ trợ ảnh đại diện định dạng JPG, PNG, WEBP hoặc GIF.")
    data = await upload.read()
    if not data:
        return ""
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{suffix}"
    (AVATAR_DIR / name).write_bytes(data)
    return f"/upload/avatars/{name}"


async def profile_page(request: Request, user: dict):
    ctx = get_menu_context(request, user)
    ctx.update(
        current_menu="profile",
        me=get_user_by_id(user["id"]),
    )
    return templates.TemplateResponse("pages/profile.html", {"request": request, **ctx})


async def save_profile(
    request: Request,
    full_name: str,
    gender: str,
    nickname: str,
    phone: str,
    email: str,
    bank_account: str,
    bank_name: str,
    avatar: UploadFile | None,
    user: dict,
):
    try:
        phone = clean_phone(phone)
        email = clean_email(email)
        update_profile_record(
            user["id"],
            full_name=full_name.strip() or None,
            gender=gender.strip().lower(),
            nickname=nickname,
            phone=phone or None,
            email=email or None,
            bank_account=bank_account.strip() or None,
            bank_name=bank_name.strip() or None,
            avatar=await save_avatar(avatar),
        )
    except ValueError as exc:
        return redirect_with_query(PROFILE_URL, error=str(exc))
    saved = get_user_by_id(user["id"])
    request.session["user"] = {**user, "full_name": saved.get("full_name") or "", "avatar": saved.get("avatar") or ""}
    return redirect_with_query(PROFILE_URL, msg="Đã lưu.")


async def change_password(current_password: str, new_password: str, confirm_password: str, user: dict):
    stored = get_password_hash(user["id"])
    if not stored or not verify_password(current_password, stored):
        return redirect_with_query(PROFILE_URL, error="Mật khẩu hiện tại không đúng.")
    if not new_password.strip() or new_password != confirm_password:
        return redirect_with_query(PROFILE_URL, error="Mật khẩu mới nhập lại không khớp.")
    set_user_password(user["id"], bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode())
    return redirect_with_query(PROFILE_URL, msg="Đã đổi mật khẩu.")
