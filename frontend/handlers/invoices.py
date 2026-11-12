"""Cầu nối HTTP của frontend.modules.invoices.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Depends, Form
from frontend.deps import require_menu
import frontend.modules.invoices.controller as controller


async def input_invoices_page(
    request: Request,
    month: str | None = None,
    edit: int | None = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.input_invoices_page(request=request, month=month, edit=edit, user=user)


async def add_input_invoice_page(
    request: Request,
    month: str | None = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.add_input_invoice_page(request=request, month=month, user=user)


async def edit_input_invoice_page(
    request: Request,
    invoice_id: int,
    month: str | None = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.edit_input_invoice_page(request=request, invoice_id=invoice_id, month=month, user=user)


async def extract_input_invoice(request: Request, _: dict = Depends(require_menu('vehicles'))):
    return await controller.extract_input_invoice(request=request, _=_)


async def save_input_invoice(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.save_input_invoice(request=request, user=user)


async def download_input_invoice(invoice_id: int, _: dict = Depends(require_menu('vehicles'))):
    return await controller.download_input_invoice(invoice_id=invoice_id, _=_)


async def delete_input_invoice(
    request: Request,
    invoice_id: int,
    month: str = Form(...),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.delete_input_invoice(request=request, invoice_id=invoice_id, month=month, _=_)


async def set_input_invoice_vehicle(
    request: Request,
    invoice_id: int,
    month: str = Form(...),
    vehicle_id: str = Form(''),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.set_input_invoice_vehicle(request=request, invoice_id=invoice_id, month=month, vehicle_id=vehicle_id, _=_)


async def book_electricity_api(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.book_electricity_api(request=request, user=user)


async def gmail_status_api(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.gmail_status_api(request=request, user=user)


async def gmail_auth_redirect(request: Request, _: dict = Depends(require_menu('vehicles'))):
    return await controller.gmail_auth_redirect(request=request, _=_)


async def gmail_oauth_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.gmail_oauth_callback(request=request, code=code, error=error, user=user)


async def gmail_disconnect_api(_: dict = Depends(require_menu('vehicles'))):
    return await controller.gmail_disconnect_api(_=_)


async def gmail_sync_api(
    request: Request,
    max_messages: int = Form(40),
    keyword: str = Form(''),
    target_company_id: int | None = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.gmail_sync_api(request=request, max_messages=max_messages, keyword=keyword, target_company_id=target_company_id, user=user)


async def imap_sync_api(
    request: Request,
    email_address: str = Form(...),
    app_password: str = Form(...),
    max_messages: int = Form(30),
    keyword: str = Form(''),
    target_company_id: int | None = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.imap_sync_api(request=request, email_address=email_address, app_password=app_password, max_messages=max_messages, keyword=keyword, target_company_id=target_company_id, user=user)


async def saveinvoice_status_api(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.saveinvoice_status_api(request=request, user=user)


async def saveinvoice_preview_api(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.saveinvoice_preview_api(request=request, user=user)


async def saveinvoice_import_api(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.saveinvoice_import_api(request=request, user=user)


async def saveinvoice_repair_items_api(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.saveinvoice_repair_items_api(request=request, user=user)


async def saveinvoice_refresh_api(user: dict = Depends(require_menu('vehicles'))):
    return await controller.saveinvoice_refresh_api(user=user)
