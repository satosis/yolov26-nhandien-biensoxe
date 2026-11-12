"""Nút chat Claude sửa code trong trang quản trị: kiểm tra quyền và đổi kết quả của service thành JSON."""
from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from frontend.auth.rbac import get_current_user
from frontend.modules.claude_code import service


def require_owner(request: Request) -> dict:
    """Trả về user nếu là chủ dự án; dữ liệu trò chuyện chung theo tài khoản, không chia theo máy."""
    user = get_current_user(request)
    if not service.is_allowed(user):
        raise HTTPException(status_code=403, detail="Chỉ chủ dự án dùng được Claude sửa code.")
    return user


def require_owner_write(request: Request) -> dict:
    """Như require_owner, thêm header chỉ fetch cùng trang gửi được, chặn trang khác giả lệnh (CSRF)."""
    user = require_owner(request)
    if request.headers.get("x-requested-with") != "XMLHttpRequest":
        raise HTTPException(status_code=403, detail="Yêu cầu không hợp lệ.")
    return user


def _error(error: service.ChatError) -> JSONResponse:
    return JSONResponse({"ok": False, "message": str(error)}, status_code=error.status)


def conversation(user: dict) -> JSONResponse:
    return JSONResponse({"ok": True, **service.conversation_view(user)})


def run_events(run_id: str, after: int, user: dict) -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "run": service.run_view(user, run_id, after)})
    except service.ChatError as error:
        return _error(error)


def send(message: str, page_path: str, page_title: str, files: list, user: dict) -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "run": service.send(user, message, page_path, page_title, files)})
    except service.ChatError as error:
        return _error(error)


def stop(run_id: str, user: dict) -> JSONResponse:
    try:
        service.stop(user, run_id)
    except service.ChatError as error:
        return _error(error)
    return JSONResponse({"ok": True})


def new_conversation(user: dict) -> JSONResponse:
    try:
        return JSONResponse({"ok": True, **service.new_conversation(user)})
    except service.ChatError as error:
        return _error(error)
