"""Khai báo các route cho module Live Chatbot (Admin & Khách hàng).

Tuân thủ quy tắc kiến trúc: Tệp router CHỈ khai báo đường dẫn, tham số,
kiểm tra quyền phụ thuộc và chuyển tiếp trực tiếp sang service.py xử lý.
"""
from __future__ import annotations

from fastapi import Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from frontend.deps import get_optional_user
from frontend.modules.chat import service

# Router quản trị (gắn vào hệ thống Admin)

# Router công khai (dành cho Chatbot widget ngoài storefront)


# =========================================================================
# ROUTE QUẢN TRỊ VIÊN (ADMIN)
# =========================================================================

def admin_live_chat_page(
    request: Request,
    session_id: int | None,
    q: str,
    user: dict,
) -> HTMLResponse:
    """Trang giao diện Live Chat dành cho Quản trị viên."""
    return service.render_admin_chat_page(
        request=request,
        user=user,
        session_id=session_id,
        q=q,
    )


def admin_live_chat_session_page(
    request: Request,
    session_id: int,
    q: str,
    user: dict,
) -> HTMLResponse:
    """Mở trực tiếp 1 phiên trò chuyện cụ thể."""
    return service.render_admin_chat_page(
        request=request,
        user=user,
        session_id=session_id,
        q=q,
    )


def admin_list_sessions_json(
    q: str,
    limit: int,
    offset: int,
    user: dict,
) -> JSONResponse:
    """Trả về danh sách hội thoại phân trang, mới nhất trước."""
    res = service.query_admin_sessions(q=q, limit=limit, offset=offset)
    return JSONResponse({
        "ok": True,
        "sessions": res["sessions"],
        "has_more": res["has_more"],
        "limit": res["limit"],
        "offset": res["offset"],
    })


def admin_get_session_detail_json(session_id: int, user: dict) -> JSONResponse:
    """Lấy chi tiết tin nhắn của 1 phiên chat dưới dạng JSON."""
    detail = service.get_admin_session_detail(session_id=session_id, mark_read=True)
    if not detail:
        return JSONResponse({"ok": False, "error": "Không tìm thấy phiên chat."}, status_code=404)
    return JSONResponse({"ok": True, "data": detail})


async def admin_reply(session_id: int, message: str, files: list, user: dict) -> JSONResponse:
    """Quản trị viên gửi phản hồi đến khách hàng."""
    return await service.admin_send_reply(session_id=session_id, admin_user=user, message=message, files=files)


def admin_update_status(session_id: int, status: str, user: dict) -> JSONResponse:
    """Cập nhật trạng thái phiên chat (open, resolved, closed)."""
    return service.admin_update_session_status(session_id=session_id, status=status)


# =========================================================================
# ROUTE CÔNG KHAI (KHÁCH HÀNG / STOREFRONT WIDGET)
# =========================================================================

async def customer_send_message(
    request: Request,
    session_token: str = Form(""),
    message: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    customer_name: str = Form(""),
    customer_contact: str = Form(""),
    service_name: str = Form(""),
    service_group: str = Form(""),
    service_category: str = Form(""),
    current_url: str = Form(""),
    user: dict | None = Depends(get_optional_user),
) -> JSONResponse:
    """Khách hàng gửi tin nhắn vào hộp chat trực tuyến."""
    return await service.customer_send_message(
        session_token=session_token,
        message=message,
        customer_name=customer_name,
        customer_contact=customer_contact,
        service_name=service_name,
        service_group=service_group,
        service_category=service_category,
        current_url=current_url,
        user=user,
        files=files,
    )


def customer_get_messages(session_token: str) -> JSONResponse:
    """Lấy danh sách tin nhắn của phiên chat (kèm đánh dấu đã đọc phản hồi của admin)."""
    return service.get_session_messages(session_token=session_token, mark_read=True)


def sync_chat_session(
    session_token: str,
    customer_name: str,
    customer_contact: str,
    service_name: str,
    service_group: str,
    service_category: str,
    current_url: str,
    user: dict | None,
) -> JSONResponse:
    """Đồng bộ / khởi tạo phiên chat với dịch vụ khách hàng đang xem."""
    sess = service.get_or_create_session(
        session_token=session_token,
        customer_name=customer_name,
        customer_contact=customer_contact,
        service_name=service_name,
        service_group=service_group,
        service_category=service_category,
        current_url=current_url,
        user=user,
    )
    return JSONResponse({"ok": True, "session": service.serialize_row(sess)})



def chat_file(name: str):
    """Mở tệp đính kèm của khách / quản trị viên."""
    return service.chat_file_response(name)
