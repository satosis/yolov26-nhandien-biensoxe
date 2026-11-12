"""Khai báo các route cho phân hệ bán tài khoản (Quản trị & Storefront công khai)."""
from __future__ import annotations

from fastapi import BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from frontend.deps import require_menu
from frontend.modules.account_sales import service


# =========================================================================
# QUẢN TRỊ: SẢN PHẨM TÀI KHOẢN
# =========================================================================

def products_page(request: Request, q: str, page: int, user: dict, service_key: str = "") -> HTMLResponse:
    return service.render_products_page(request, user, q=q, page=page, service=service_key)


async def create_product(
    name: str = Form(...),
    category_group: str = Form(""),
    category: str = Form(""),
    price: int = Form(0),
    stock: int = Form(0),
    instant: str = Form(""),
    active: str = Form(""),
    image: UploadFile | None = File(None),
    user: dict = Depends(require_menu("account_products")),
    service_key: str = "tai-khoan",
    description: str = "",
    specs: str = "",
    icon: str = "",
) -> RedirectResponse:
    return await service.create_product(
        name=name,
        category_group=category_group,
        category=category,
        price=price,
        stock=stock,
        instant=bool(instant),
        active=bool(active),
        image_file=image,
        service=service_key,
        description=description,
        specs=specs,
        icon=icon,
    )


async def update_product(
    product_id: int,
    name: str = Form(...),
    category_group: str = Form(""),
    category: str = Form(""),
    price: int = Form(0),
    stock: int = Form(0),
    instant: str = Form(""),
    active: str = Form(""),
    image: UploadFile | None = File(None),
    user: dict = Depends(require_menu("account_products")),
    service_key: str = "tai-khoan",
    description: str = "",
    specs: str = "",
    icon: str = "",
) -> RedirectResponse:
    return await service.update_product(
        product_id=product_id,
        name=name,
        category_group=category_group,
        category=category,
        price=price,
        stock=stock,
        instant=bool(instant),
        active=bool(active),
        image_file=image,
        service=service_key,
        description=description,
        specs=specs,
        icon=icon,
    )


def delete_product(
    product_id: int,
    user: dict = Depends(require_menu("account_products")),
) -> RedirectResponse:
    return service.delete_product(product_id)


# =========================================================================
# QUẢN TRỊ: ĐƠN ĐẶT MUA THEO DỊCH VỤ
# =========================================================================

def orders_page(request: Request, status: str, page: int, user: dict, service_key: str = "", q: str = "") -> HTMLResponse:
    return service.render_orders_page(request, user, status=status, page=page, service=service_key, q=q)


def create_order(
    service_key: str = "tai-khoan",
    product_id: int | None = None,
    product_name: str = "",
    quantity: int = 1,
    unit_price: int = 0,
    buyer_name: str = "",
    buyer_contact: str = "",
    status: str = "paid",
    filter_status: str = "",
    q: str = "",
    user: dict = Depends(require_menu("account_orders")),
) -> RedirectResponse:
    return service.create_admin_order(
        service_name=service_key,
        product_id=product_id,
        product_name=product_name,
        quantity=quantity,
        unit_price=unit_price,
        buyer_name=buyer_name,
        buyer_contact=buyer_contact,
        status=status,
        filter_status=filter_status,
        q=q,
    )


def update_order_status(
    order_id: int,
    status: str = Form(...),
    service_key: str = "",
    filter_status: str = "",
    q: str = "",
    user: dict = Depends(require_menu("account_orders")),
) -> RedirectResponse:
    return service.update_order_status(order_id, status, service_name=service_key, filter_status=filter_status, q=q)


# =========================================================================
# CÔNG KHAI: STOREFRONT & ĐẶT MUA
# =========================================================================

def storefront(request: Request, q: str, group: str, category: str) -> HTMLResponse:
    return service.render_storefront(request, q=q, group=group, category=category)


async def update_storefront_profile(
    request: Request,
    full_name: str,
    phone: str,
    email: str,
    avatar: UploadFile | None,
    user: dict,
) -> JSONResponse:
    return await service.update_storefront_profile(
        request=request,
        full_name=full_name,
        phone=phone,
        email=email,
        avatar=avatar,
        user=user,
    )


async def change_storefront_password(
    current_password: str,
    new_password: str,
    confirm_password: str,
    user: dict,
) -> JSONResponse:
    return await service.change_storefront_password(
        current_password=current_password,
        new_password=new_password,
        confirm_password=confirm_password,
        user=user,
    )


def place_order(
    product_id: int = Form(...),
    quantity: int = Form(1),
    buyer_name: str = Form(...),
    buyer_contact: str = Form(...),
    consent: str = Form(""),
    chat_token: str = Form(""),
) -> JSONResponse:
    return service.place_order(product_id, quantity, buyer_name, buyer_contact, consent, chat_token)


async def order_paid(code: str, token: str = "") -> JSONResponse:
    return await service.check_order_paid(code, token=token)


async def cart_orders(items: list[dict] | None = None, legacy_codes: list[str] | None = None) -> JSONResponse:
    return await service.cart_orders(items=items, legacy_codes=legacy_codes)


async def chatbot_send_message(
    request: Request,
    background_tasks: BackgroundTasks,
    message: str,
    full_name: str,
    contact: str,
    service_name: str,
    current_url: str,
) -> JSONResponse:
    return await service.handle_chatbot_message(
        request=request,
        background_tasks=background_tasks,
        message=message,
        full_name=full_name,
        contact=contact,
        service_name=service_name,
        current_url=current_url,
    )


async def sepay_webhook(request: Request) -> JSONResponse:
    return await service.process_sepay_webhook(request)
