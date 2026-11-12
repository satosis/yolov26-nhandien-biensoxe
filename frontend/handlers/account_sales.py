"""Cầu nối HTTP của frontend.modules.account_sales.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from frontend.deps import get_current_user, require_menu
import frontend.modules.account_sales.controller as controller


def products_page(
    request: Request,
    q: str = Query(''),
    page: int = Query(1, ge=1),
    service: str = Query(''),
    user: dict = Depends(require_menu('account_products')),
) -> HTMLResponse:
    return controller.products_page(request=request, q=q, page=page, user=user, service_key=service)


async def create_product(
    name: str = Form(...),
    category_group: str = Form(''),
    category: str = Form(''),
    price: int = Form(0),
    stock: int = Form(0),
    instant: str = Form(''),
    active: str = Form(''),
    image: UploadFile | None = File(None),
    service: str = Form('tai-khoan'),
    description: str = Form(''),
    specs: str = Form(''),
    icon: str = Form(''),
    user: dict = Depends(require_menu('account_products')),
) -> RedirectResponse:
    return await controller.create_product(name=name, category_group=category_group, category=category, price=price, stock=stock, instant=instant, active=active, image=image, user=user, service_key=service, description=description, specs=specs, icon=icon)


async def update_product(
    product_id: int,
    name: str = Form(...),
    category_group: str = Form(''),
    category: str = Form(''),
    price: int = Form(0),
    stock: int = Form(0),
    instant: str = Form(''),
    active: str = Form(''),
    image: UploadFile | None = File(None),
    service: str = Form('tai-khoan'),
    description: str = Form(''),
    specs: str = Form(''),
    icon: str = Form(''),
    user: dict = Depends(require_menu('account_products')),
) -> RedirectResponse:
    return await controller.update_product(product_id=product_id, name=name, category_group=category_group, category=category, price=price, stock=stock, instant=instant, active=active, image=image, user=user, service_key=service, description=description, specs=specs, icon=icon)


def delete_product(
    product_id: int,
    user: dict = Depends(require_menu('account_products')),
) -> RedirectResponse:
    return controller.delete_product(product_id=product_id, user=user)


def orders_page(
    request: Request,
    status: str = Query(''),
    service: str = Query(''),
    q: str = Query(''),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('account_orders')),
) -> HTMLResponse:
    return controller.orders_page(request=request, status=status, page=page, user=user, service_key=service, q=q)


def create_order(
    service: str = Form('tai-khoan'),
    product_id: str = Form(''),
    product_name: str = Form(''),
    quantity: int = Form(1),
    unit_price: int = Form(0),
    buyer_name: str = Form(...),
    buyer_contact: str = Form(...),
    status: str = Form('paid'),
    filter_status: str = Form(''),
    q: str = Form(''),
    user: dict = Depends(require_menu('account_orders')),
) -> RedirectResponse:
    pid = int(product_id) if product_id and product_id.strip().isdigit() else None
    return controller.create_order(
        service_key=service,
        product_id=pid,
        product_name=product_name,
        quantity=quantity,
        unit_price=unit_price,
        buyer_name=buyer_name,
        buyer_contact=buyer_contact,
        status=status,
        filter_status=filter_status,
        q=q,
        user=user,
    )


def update_order_status(
    order_id: int,
    status: str = Form(...),
    service: str = Form(''),
    filter_status: str = Form(''),
    q: str = Form(''),
    user: dict = Depends(require_menu('account_orders')),
) -> RedirectResponse:
    return controller.update_order_status(order_id=order_id, status=status, service_key=service, filter_status=filter_status, q=q, user=user)


def storefront(
    request: Request,
    q: str = Query(''),
    group: str = Query(''),
    category: str = Query(''),
) -> HTMLResponse:
    return controller.storefront(request=request, q=q, group=group, category=category)


async def update_storefront_profile(
    request: Request,
    full_name: str = Form(''),
    phone: str = Form(''),
    email: str = Form(''),
    avatar: UploadFile | None = File(None),
    user: dict = Depends(get_current_user),
) -> JSONResponse:
    return await controller.update_storefront_profile(
        request=request,
        full_name=full_name,
        phone=phone,
        email=email,
        avatar=avatar,
        user=user,
    )


async def change_storefront_password(
    current_password: str = Form(''),
    new_password: str = Form(''),
    confirm_password: str = Form(''),
    user: dict = Depends(get_current_user),
) -> JSONResponse:
    return await controller.change_storefront_password(
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
    consent: str = Form(''),
    chat_token: str = Form(''),
) -> JSONResponse:
    return controller.place_order(product_id=product_id, quantity=quantity, buyer_name=buyer_name, buyer_contact=buyer_contact, consent=consent, chat_token=chat_token)


async def order_paid(code: str, request: Request) -> JSONResponse:
    token = request.query_params.get('token', '')
    if request.method == 'POST':
        try:
            ctype = (request.headers.get('content-type') or '').lower()
            if 'application/json' in ctype:
                body = await request.json()
                if isinstance(body, dict):
                    token = str(body.get('token') or token or '')
            else:
                form = await request.form()
                token = str(form.get('token') or token or '')
        except Exception:
            pass
    return await controller.order_paid(code=code, token=token)


async def cart_orders(request: Request, codes: str = Query('')) -> JSONResponse:
    items: list[dict] = []
    legacy_codes = [c.strip() for c in codes.split(',') if c.strip()]
    if request.method == 'POST':
        try:
            body = await request.json()
            if isinstance(body, dict):
                raw_items = body.get('items')
                if isinstance(raw_items, list):
                    items = [x for x in raw_items if isinstance(x, dict)][:50]
                raw_legacy = body.get('legacy_codes')
                if isinstance(raw_legacy, list):
                    legacy_codes = [str(c).strip() for c in raw_legacy if str(c).strip()][:50]
        except Exception:
            pass
    return await controller.cart_orders(items=items, legacy_codes=legacy_codes)


async def chatbot_send_message(
    request: Request,
    background_tasks: BackgroundTasks,
    message: str = Form(...),
    full_name: str = Form(''),
    contact: str = Form(''),
    service_name: str = Form(''),
    current_url: str = Form(''),
) -> JSONResponse:
    return await controller.chatbot_send_message(request=request, background_tasks=background_tasks, message=message, full_name=full_name, contact=contact, service_name=service_name, current_url=current_url)


async def sepay_webhook(request: Request) -> JSONResponse:
    return await controller.sepay_webhook(request=request)
