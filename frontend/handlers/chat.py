"""Cầu nối HTTP của frontend.modules.chat.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from frontend.deps import require_menu, get_optional_user
import frontend.modules.chat.controller as controller


def admin_live_chat_page(
    request: Request,
    session_id: int | None = Query(None),
    q: str = Query(''),
    user: dict = Depends(require_menu('account_live_chat')),
) -> HTMLResponse:
    'Trang giao diện Live Chat dành cho Quản trị viên.'
    return controller.admin_live_chat_page(request=request, session_id=session_id, q=q, user=user)


def admin_live_chat_session_page(
    request: Request,
    session_id: int,
    q: str = Query(''),
    user: dict = Depends(require_menu('account_live_chat')),
) -> HTMLResponse:
    'Mở trực tiếp 1 phiên trò chuyện cụ thể.'
    return controller.admin_live_chat_session_page(request=request, session_id=session_id, q=q, user=user)


def admin_list_sessions_json(
    q: str = Query(''),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_menu('account_live_chat')),
) -> JSONResponse:
    'Lấy danh sách hội thoại phân trang dưới dạng JSON.'
    return controller.admin_list_sessions_json(q=q, limit=limit, offset=offset, user=user)


def admin_get_session_detail_json(
    session_id: int,
    user: dict = Depends(require_menu('account_live_chat')),
) -> JSONResponse:
    'Lấy chi tiết tin nhắn của 1 phiên chat dưới dạng JSON.'
    return controller.admin_get_session_detail_json(session_id=session_id, user=user)


async def admin_reply(
    session_id: int,
    message: str = Form(''),
    files: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_menu('account_live_chat')),
) -> JSONResponse:
    'Quản trị viên gửi phản hồi (chữ, ảnh, tệp) đến khách hàng.'
    return await controller.admin_reply(session_id=session_id, message=message, files=files, user=user)


def admin_update_status(
    session_id: int,
    status: str = Form(...),
    user: dict = Depends(require_menu('account_live_chat')),
) -> JSONResponse:
    'Cập nhật trạng thái phiên chat (open, resolved, closed).'
    return controller.admin_update_status(session_id=session_id, status=status, user=user)


async def customer_send_message(
    request: Request,
    session_token: str = Form(''),
    message: str = Form(''),
    files: list[UploadFile] = File(default=[]),
    customer_name: str = Form(''),
    customer_contact: str = Form(''),
    service_name: str = Form(''),
    service_group: str = Form(''),
    service_category: str = Form(''),
    current_url: str = Form(''),
    user: dict | None = Depends(get_optional_user),
) -> JSONResponse:
    'Khách hàng gửi tin nhắn (chữ, ảnh, tệp) vào hộp chat trực tuyến.'
    return await controller.customer_send_message(request=request, session_token=session_token, message=message, files=files, customer_name=customer_name, customer_contact=customer_contact, service_name=service_name, service_group=service_group, service_category=service_category, current_url=current_url, user=user)


def customer_get_messages(session_token: str = Query(...)) -> JSONResponse:
    'Lấy danh sách tin nhắn của phiên chat (kèm đánh dấu đã đọc phản hồi của admin).'
    return controller.customer_get_messages(session_token=session_token)


def sync_chat_session(
    session_token: str = Form(''),
    customer_name: str = Form(''),
    customer_contact: str = Form(''),
    service_name: str = Form(''),
    service_group: str = Form(''),
    service_category: str = Form(''),
    current_url: str = Form(''),
    user: dict | None = Depends(get_optional_user),
) -> JSONResponse:
    'Đồng bộ / khởi tạo phiên chat với dịch vụ khách hàng đang xem.'
    return controller.sync_chat_session(session_token=session_token, customer_name=customer_name, customer_contact=customer_contact, service_name=service_name, service_group=service_group, service_category=service_category, current_url=current_url, user=user)


def chat_file(name: str):
    'Mở tệp đính kèm của khách / quản trị viên.'
    return controller.chat_file(name=name)
