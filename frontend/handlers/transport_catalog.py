"""Cầu nối HTTP của frontend.modules.transport.catalog_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.modules.transport.catalog_controller as controller


async def cargo_catalog_page(
    request: Request,
    tab: str = Query('service'),
    subtab: str = Query('list'),
    partner_type: str = Query('all'),
    role_filter: str = Query('all'),
    back: str = Query('/admin/vehicles'),
    page: int = Query(1, ge=1),
    q: str = Query('', max_length=200),
    debt: str = Query(''),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.cargo_catalog_page(request=request, tab=tab, subtab=subtab, partner_type=partner_type, role_filter=role_filter, back=back, page=page, q=q, debt=debt, message=message, error=error, user=user)


async def create_cargo_catalog_item(
    request: Request,
    kind: str = Form(...),
    name: str = Form(...),
    back: str = Form(''),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.create_cargo_catalog_item(request=request, kind=kind, name=name, back=back, _=_)


async def edit_cargo_catalog_item(
    request: Request,
    item_id: int,
    kind: str = Form(...),
    name: str = Form(...),
    back: str = Form(''),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.edit_cargo_catalog_item(request=request, item_id=item_id, kind=kind, name=name, back=back, _=_)


async def remove_cargo_catalog_item(
    request: Request,
    item_id: int,
    kind: str = Form(...),
    back: str = Form(''),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.remove_cargo_catalog_item(request=request, item_id=item_id, kind=kind, back=back, _=_)


async def toggle_partner_role_endpoint(
    request: Request,
    company_id: int,
    user: dict = Depends(require_menu('vehicles')),
):
    'Toggle KH / NCC status and partner type for Sheet 1.'
    return await controller.toggle_partner_role_endpoint(request=request, company_id=company_id, user=user)


async def update_opening_debts_endpoint(
    request: Request,
    company_id: int,
    user: dict = Depends(require_menu('vehicles')),
):
    'Update opening balances for 131 or 331.'
    return await controller.update_opening_debts_endpoint(request=request, company_id=company_id, user=user)


async def partner_subledger_page(
    request: Request,
    company_id: int,
    account: str = Query('331'),
    year: int = Query(2026),
    user: dict = Depends(require_menu('vehicles')),
):
    'View Sổ chi tiết công nợ (Mẫu số S31-DNN hoặc S32-DNN) for partner.'
    return await controller.partner_subledger_page(request=request, company_id=company_id, account=account, year=year, user=user)


async def partner_subledger_data_api(
    request: Request,
    company_id: int,
    account: str = Query('331'),
    year: int = Query(2026),
    user: dict = Depends(require_menu('vehicles')),
):
    'JSON API returning partner subledger data for AJAX modal.'
    return await controller.partner_subledger_data_api(request=request, company_id=company_id, account=account, year=year, user=user)
