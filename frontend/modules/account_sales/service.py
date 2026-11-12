"""Nghiệp vụ (Business logic) và chuẩn bị dữ liệu cho phân hệ bán tài khoản."""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

import bcrypt
from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from backend.repositories.user_repo import (
    get_password_hash,
    get_user_by_id,
    set_user_password,
    update_basic_profile_record,
)
from backend.services import account_sales as sales
from backend.services import config as cfg
from backend.services.telegram_service import notify_telegram
from frontend.deps import get_menu_context, templates
from frontend.modules.auth.profile_controller import save_avatar
from frontend.modules.auth.service import verify_password
from frontend.modules.contact_validation import clean_email, clean_phone
from frontend.pagination import paginate_counts
from frontend.redirects import redirect_with_query

logger = logging.getLogger(__name__)

PRODUCTS_URL = "/admin/account-sales/products"
ORDERS_URL = "/admin/account-sales/orders"
PER_PAGE = 20
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
IMAGE_DIR = Path(cfg.BASE_DIR) / "upload" / "accounts"


async def save_product_image(upload: UploadFile | None) -> str:
    """Lưu ảnh sản phẩm vào upload/accounts; trả tên tệp, hoặc chuỗi rỗng khi không hợp lệ."""
    suffix = Path(upload.filename or "").suffix.lower() if upload else ""
    if suffix not in IMAGE_TYPES:
        return ""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{suffix}"
    (IMAGE_DIR / name).write_bytes(await upload.read())
    return name


def build_admin_context(request: Request, user: dict, menu: str, **extra) -> dict:
    """Xây dựng context tiêu chuẩn cho các trang quản trị account_sales."""
    return {
        **get_menu_context(request, user),
        "request": request,
        "msg": request.query_params.get("msg", ""),
        "error": request.query_params.get("error", ""),
        "current_menu": menu,
        **extra,
    }


def render_products_page(request: Request, user: dict, q: str = "", page: int = 1, service: str = "") -> HTMLResponse:
    """Danh mục sản phẩm chung của mọi trang bán, lọc theo dịch vụ (Bán tài khoản, Container, Quả cân, Điện mặt trời)."""
    q = q.strip()
    service = service if service in sales.SERVICES else ""
    extra = {key: value for key, value in (("q", q), ("service", service)) if value}
    pagination = paginate_counts(
        total=sales.count_products(q, service),
        page=page,
        per_page=PER_PAGE,
        base_url=PRODUCTS_URL,
        extra_params=extra or None,
    )
    products = sales.list_products(q=q, service=service, limit=PER_PAGE, offset=(pagination.page - 1) * PER_PAGE)
    counts = sales.service_counts()
    ctx = build_admin_context(
        request, user, "account_products",
        products=products,
        pagination=pagination,
        q=q,
        service=service,
        services=sales.SERVICES,
        service_counts=counts,
        total_all=sum(counts.values()),
        group_names=sales.group_names(),
    )
    return templates.TemplateResponse("pages/account_sales/products.html", ctx)


def _back_url(service: str) -> str:
    return f"{PRODUCTS_URL}?service={service}" if service in sales.SERVICES else PRODUCTS_URL


async def create_product(
    name: str,
    category_group: str,
    category: str,
    price: int,
    stock: int,
    instant: bool,
    active: bool,
    image_file: UploadFile | None,
    service: str = sales.ACCOUNT_SERVICE,
    description: str = "",
    specs: str = "",
    icon: str = "",
) -> RedirectResponse:
    """Thêm một sản phẩm vào danh mục chung."""
    image_name = await save_product_image(image_file)
    sales.save_product(
        None,
        name=name.strip(),
        category_group=category_group.strip() or sales.SERVICES.get(service, "Khác"),
        category=category.strip(),
        price=price,
        stock=stock,
        instant=instant,
        active=active,
        image=image_name,
        service=service,
        description=description.strip(),
        specs=specs.strip(),
        icon=icon.strip(),
    )
    return redirect_with_query(_back_url(service), msg="Đã lưu.")


async def update_product(
    product_id: int,
    name: str,
    category_group: str,
    category: str,
    price: int,
    stock: int,
    instant: bool,
    active: bool,
    image_file: UploadFile | None,
    service: str = sales.ACCOUNT_SERVICE,
    description: str = "",
    specs: str = "",
    icon: str = "",
) -> RedirectResponse:
    """Sửa một sản phẩm trong danh mục chung."""
    image_name = await save_product_image(image_file)
    sales.save_product(
        product_id,
        name=name.strip(),
        category_group=category_group.strip() or sales.SERVICES.get(service, "Khác"),
        category=category.strip(),
        price=price,
        stock=stock,
        instant=instant,
        active=active,
        image=image_name,
        service=service,
        description=description.strip(),
        specs=specs.strip(),
        icon=icon.strip(),
    )
    return redirect_with_query(_back_url(service), msg="Đã lưu.")


def delete_product(product_id: int) -> RedirectResponse:
    """Xóa một gói sản phẩm khỏi hệ thống."""
    sales.delete_product(product_id)
    return redirect_with_query(PRODUCTS_URL, msg="Đã xóa.")


def _orders_back_url(service: str = "", status: str = "", q: str = "") -> str:
    params = {}
    if service in sales.SERVICES:
        params["service"] = service
    if status in sales.ADMIN_ORDER_STATUSES:
        params["status"] = status
    if (q or "").strip():
        params["q"] = q.strip()
    if not params:
        return ORDERS_URL
    import urllib.parse
    return f"{ORDERS_URL}?{urllib.parse.urlencode(params)}"


def render_orders_page(
    request: Request,
    user: dict,
    status: str = "",
    page: int = 1,
    service: str = "",
    q: str = "",
) -> HTMLResponse:
    """Lấy danh sách đơn hàng đã thanh toán theo từng dịch vụ, phân trang và render trang quản trị đơn hàng."""
    q = (q or "").strip()
    service = service if service in sales.SERVICES else ""
    status = status if status in sales.ADMIN_ORDER_STATUSES else ""
    extra = {key: value for key, value in (("service", service), ("status", status), ("q", q)) if value}
    pagination = paginate_counts(
        total=sales.count_orders(status=status, service=service, q=q),
        page=page,
        per_page=PER_PAGE,
        base_url=ORDERS_URL,
        extra_params=extra or None,
    )
    orders = sales.list_orders(
        status=status,
        service=service,
        q=q,
        limit=PER_PAGE,
        offset=(pagination.page - 1) * PER_PAGE,
    )
    counts = sales.order_service_counts(status=status, q=q)
    catalog_options = [
        {
            "id": p["id"],
            "name": p["name"],
            "price": int(p.get("price") or 0),
            "service": p.get("service") or sales.ACCOUNT_SERVICE,
        }
        for p in sales.list_products()
        if p.get("active", True)
    ]
    ctx = build_admin_context(
        request, user, "account_orders",
        orders=orders,
        pagination=pagination,
        status=status,
        statuses=sales.ADMIN_ORDER_STATUSES,
        service=service,
        services=sales.SERVICES,
        service_counts=counts,
        total_all=sum(counts.values()),
        q=q,
        catalog_options=catalog_options,
    )
    return templates.TemplateResponse("pages/account_sales/orders.html", ctx)


def create_admin_order(
    *,
    service_name: str,
    product_id: int | None,
    product_name: str,
    quantity: int,
    unit_price: int,
    buyer_name: str,
    buyer_contact: str,
    status: str = "paid",
    filter_status: str = "",
    q: str = "",
) -> RedirectResponse:
    """Tạo đơn hàng đã thanh toán thủ công từ màn hình quản trị, gắn đúng dịch vụ."""
    svc = service_name if service_name in sales.SERVICES else sales.ACCOUNT_SERVICE
    sales.create_manual_order(
        service=svc,
        product_id=product_id,
        product_name=product_name,
        quantity=quantity,
        unit_price=unit_price,
        buyer_name=buyer_name,
        buyer_contact=buyer_contact,
        status=status,
    )
    return redirect_with_query(_orders_back_url(service=svc, status=filter_status, q=q), msg="Đã tạo đơn hàng.")


def update_order_status(
    order_id: int,
    status: str,
    service_name: str = "",
    filter_status: str = "",
    q: str = "",
) -> RedirectResponse:
    """Cập nhật trạng thái đơn hàng đã thanh toán (Đã thanh toán / Đã giao), giữ nguyên bộ lọc hiện tại."""
    sales.set_order_status(order_id, status)
    return redirect_with_query(_orders_back_url(service=service_name, status=filter_status, q=q), msg="Đã lưu.")


def _build_storefront_user(request: Request, session_user: dict | None) -> dict | None:
    if not session_user:
        return None
    merged = {
        "id": session_user.get("id"),
        "username": session_user.get("username") or "",
        "role": session_user.get("role") or "Guest",
        "full_name": session_user.get("full_name") or "",
        "avatar": session_user.get("avatar") or "",
        "phone": "",
        "email": "",
    }
    uid = session_user.get("id")
    if uid:
        try:
            row = get_user_by_id(int(uid))
        except Exception:
            row = None
        if row:
            merged["username"] = row.get("username") or merged["username"]
            merged["role"] = row.get("role") or merged["role"]
            merged["full_name"] = row.get("full_name") or ""
            merged["avatar"] = row.get("avatar") or ""
            merged["phone"] = row.get("phone") or ""
            merged["email"] = row.get("email") or ""
            if "session" in getattr(request, "scope", {}) and (
                session_user.get("full_name") != merged["full_name"]
                or session_user.get("avatar") != merged["avatar"]
            ):
                request.session["user"] = {
                    **session_user,
                    "full_name": merged["full_name"],
                    "avatar": merged["avatar"],
                }
    return merged


def render_storefront(request: Request, q: str = "", group: str = "", category: str = "") -> HTMLResponse:
    """Chuẩn bị dữ liệu hiển thị gian hàng công khai /tai-khoan."""
    q = q.strip()
    tree = sales.category_tree()
    categories_list = [g["name"] for g in tree]
    sf_user = _build_storefront_user(request, request.session.get("user"))
    return templates.TemplateResponse("pages/account_sales/storefront.html", {
        "request": request,
        "products": sales.list_products(q=q, available_only=True, group=group, category=category,
                                        service=sales.ACCOUNT_SERVICE),
        "categories": categories_list,
        "tree": tree,
        "total_all": sum(g["count"] for g in tree) if tree else 0,
        "q": q,
        "group": group,
        "category": category,
        "user": sf_user,
    })


async def update_storefront_profile(
    request: Request,
    full_name: str,
    phone: str,
    email: str,
    avatar: UploadFile | None,
    user: dict,
) -> JSONResponse:
    """Cập nhật thông tin hồ sơ người dùng trực tiếp từ cửa hàng /tai-khoan."""
    clean_name = (full_name or "").strip()
    if not clean_name:
        return JSONResponse({"ok": False, "error": "Vui lòng nhập họ và tên."}, status_code=400)
    try:
        cleaned_phone = clean_phone(phone)
        cleaned_email = clean_email(email)
        new_avatar = await save_avatar(avatar)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    update_basic_profile_record(
        int(user["id"]),
        full_name=clean_name,
        phone=cleaned_phone or None,
        email=cleaned_email or None,
        avatar=new_avatar or None,
    )
    saved = get_user_by_id(int(user["id"])) or {}
    next_name = saved.get("full_name") or clean_name
    next_avatar = saved.get("avatar") or new_avatar or user.get("avatar") or ""
    request.session["user"] = {
        **user,
        "full_name": next_name,
        "avatar": next_avatar,
    }
    return JSONResponse({
        "ok": True,
        "msg": "Đã lưu thông tin hồ sơ.",
        "user": {
            "username": saved.get("username") or user.get("username") or "",
            "full_name": next_name,
            "avatar": next_avatar,
            "phone": saved.get("phone") or cleaned_phone or "",
            "email": saved.get("email") or cleaned_email or "",
        },
    })


async def change_storefront_password(
    current_password: str,
    new_password: str,
    confirm_password: str,
    user: dict,
) -> JSONResponse:
    """Đổi mật khẩu người dùng trực tiếp từ cửa hàng /tai-khoan."""
    stored = get_password_hash(int(user["id"]))
    valid_current = False
    if stored and current_password:
        try:
            valid_current = verify_password(current_password, stored)
        except ValueError:
            valid_current = False
    if not valid_current:
        return JSONResponse({"ok": False, "error": "Mật khẩu hiện tại không đúng."}, status_code=400)
    if len((new_password or "").strip()) < 6:
        return JSONResponse({"ok": False, "error": "Mật khẩu mới phải có ít nhất 6 ký tự."}, status_code=400)
    if new_password != confirm_password:
        return JSONResponse({"ok": False, "error": "Mật khẩu mới nhập lại không khớp."}, status_code=400)

    set_user_password(int(user["id"]), bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode())
    return JSONResponse({"ok": True, "msg": "Đã đổi mật khẩu thành công."})


def place_order(product_id: int, quantity: int, buyer_name: str, buyer_contact: str, consent: str, chat_token: str = "") -> JSONResponse:
    """Khởi tạo thanh toán QR cho khách hàng (chưa lưu DB khi chưa thanh toán)."""
    if consent != "1":
        raise HTTPException(status_code=400, detail="Vui lòng xác nhận đồng ý điều khoản.")
    order = sales.create_order(product_id, quantity, buyer_name.strip(), buyer_contact.strip(), chat_token.strip())
    return JSONResponse({
        "ok": True,
        **_payment_view(order["payment_code"], order["total"], order["product_name"], order["quantity"], order["qr_url"]),
        "token": order["token"],
    })


def _payment_view(code: str, amount: int, product_name: str, quantity: int, qr_url: str) -> dict[str, Any]:
    """Thông tin hộp thanh toán của một đơn: mã QR, số tiền, tài khoản nhận và nội dung chuyển khoản."""
    return {"code": code, "amount": amount, "product_name": product_name, "quantity": quantity, "qr_url": qr_url,
            "bank": sales.PAY_BANK_NAME, "account": sales.PAY_ACCOUNT, "account_name": sales.PAY_ACCOUNT_NAME}


async def _notify_order_paid(order: dict[str, Any]) -> None:
    """Gửi thông báo Telegram và Live Chat khi đơn hàng vừa thanh toán đủ."""
    paid = f"{int(order['paid_amount']):,}".replace(",", ".")
    total = f"{int(order['unit_price']) * int(order['quantity']):,}".replace(",", ".")
    svc = order.get("service") or sales.ACCOUNT_SERVICE
    svc_label = sales.SERVICES.get(svc, "dịch vụ")
    svc_url = sales.SERVICE_PATHS.get(svc, "/tai-khoan")
    try:
        notify_telegram(
            f"💰 Đã nhận {paid}đ · đơn {svc_label.lower()} #{order['id']}: {order['buyer_name']} · {order['buyer_contact']}\n"
            f"{order['product_name']} × {order['quantity']} = {total}đ · mã {order['payment_code']}",
            important=True,
            prefix="",
        )
    except Exception:
        logger.exception("Lỗi gửi thông báo Telegram cho đơn #%s", order.get("id"))
    try:
        from frontend.modules.chat import service as chat_service
        chat_svc_name = (
            "Siêu thị tài khoản số (ACCSHOP)" if svc == sales.ACCOUNT_SERVICE else svc_label
        )
        await chat_service.post_system_notice(
            session_token=order.get("chat_session_token") or "",
            customer_name=order.get("buyer_name") or "",
            customer_contact=order.get("buyer_contact") or "",
            service_name=chat_svc_name,
            current_url=svc_url,
            text=(f"✅ Đã nhận thanh toán đơn #{order['id']} ({svc_label})\n"
                  f"{order['product_name']} × {order['quantity']} = {total}đ\n"
                  f"Mã đơn: {order['payment_code']} · Liên hệ: {order['buyer_contact']}"),
        )
    except Exception:
        logger.exception("Lỗi gửi thông báo Live Chat cho đơn #%s", order.get("id"))


async def cart_orders(items: list[dict[str, Any]] | None = None, legacy_codes: list[str] | None = None) -> JSONResponse:
    """Đồng bộ giỏ hàng phía trình duyệt: lọc mã đã thanh toán (chốt đơn nếu tiền đã về) và kiểm tra mã cũ nếu có."""
    paid_codes: list[str] = []
    seen: set[str] = set()
    for item in (items or []):
        code = str(item.get("code") or "").strip().upper()
        token = str(item.get("token") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        paid, created = sales.resolve_order_payment(code, token)
        if created:
            await _notify_order_paid(created)
        if paid:
            paid_codes.append(code)

    remaining_legacy = [c.strip() for c in (legacy_codes or []) if c and c.strip().upper() not in seen]
    legacy_rows = sales.unpaid_orders(remaining_legacy) if remaining_legacy else []
    legacy_unpaid = {str(o["payment_code"]).upper() for o in legacy_rows}
    for lc in remaining_legacy:
        up = lc.upper()
        if up not in legacy_unpaid and sales.is_paid(up):
            paid_codes.append(up)

    return JSONResponse({
        "paid_codes": paid_codes,
        "orders": [_payment_view(o["payment_code"], o["due"], o["product_name"], o["quantity"], o["qr_url"])
                   for o in legacy_rows],
    })


async def check_order_paid(code: str, token: str = "") -> JSONResponse:
    """Kiểm tra đơn hàng đã thanh toán hay chưa; tự chốt đơn sang `paid` nếu tiền đã vào account_payments."""
    paid, created = sales.resolve_order_payment(code, token)
    if created:
        await _notify_order_paid(created)
    return JSONResponse({"paid": paid})


async def handle_chatbot_message(
    request: Request,
    background_tasks: Any,
    message: str,
    full_name: str = "",
    contact: str = "",
    service_name: str = "",
    current_url: str = "",
) -> JSONResponse:
    """Xử lý tin nhắn Chatbot trực tiếp trên hệ thống (không qua Telegram)."""
    from frontend.modules.chat import service as chat_service
    user = request.session.get("user") or {}
    session_token = request.cookies.get("chat_session_token") or ""
    return await chat_service.customer_send_message(
        session_token=session_token,
        message=message,
        customer_name=full_name,
        customer_contact=contact,
        service_name=service_name,
        current_url=current_url,
        user=user,
    )


async def process_sepay_webhook(request: Request) -> JSONResponse:
    """Xác thực và xử lý webhook thanh toán ngân hàng tự động từ SePay."""
    auth_header = request.headers.get("authorization", "")
    expected = f"Apikey {os.environ['SESSION_SECRET']}"
    if auth_header != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
    payload = await request.json()
    order = sales.record_payment(payload)
    if order:
        await _notify_order_paid(order)
    return JSONResponse({"success": True})

