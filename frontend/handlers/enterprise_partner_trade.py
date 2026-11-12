"""Cầu nối HTTP của frontend.modules.enterprise.partner_trade_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
from typing import Optional
import frontend.modules.enterprise.partner_trade_controller as controller


async def partner_trades_page(
    request: Request,
    partner: Optional[int] = Query(None),
    q: str = Query(''),
    page: Optional[int] = Query(None),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('sales')),
):
    return await controller.partner_trades_page(request=request, partner=partner, q=q, page=page, message=message, error=error, user=user)


async def save_partner(
    request: Request,
    partner_id: str = Form(''),
    name: str = Form(''),
    opening_balance: str = Form(''),
    title: str = Form(''),
    owe_label: str = Form(''),
    is_default: str = Form(''),
    user: dict = Depends(require_menu('sales')),
):
    return await controller.save_partner(request=request, partner_id=partner_id, name=name, opening_balance=opening_balance, title=title, owe_label=owe_label, is_default=is_default, user=user)


async def delete_partner(partner_id: int, request: Request, user: dict = Depends(require_menu('sales'))):
    return await controller.delete_partner(partner_id=partner_id, request=request, user=user)


async def get_or_create_share_link(
    partner_id: int,
    request: Request,
    renew: str = Form(''),
    user: dict = Depends(require_menu('sales')),
):
    return await controller.get_or_create_share_link(partner_id=partner_id, request=request, renew=renew, user=user)


async def save_entry(
    request: Request,
    entry_id: str = Form(''),
    partner_id: int = Form(...),
    entry_date: str = Form(...),
    side: str = Form('take'),
    item_name: str = Form(''),
    buyer: str = Form(''),
    quantity: str = Form(''),
    unit_price: str = Form(''),
    amount: str = Form(''),
    note: str = Form(''),
    page: str = Form(''),
    q: str = Form(''),
    user: dict = Depends(require_menu('sales')),
):
    return await controller.save_entry(request=request, entry_id=entry_id, partner_id=partner_id, entry_date=entry_date, side=side, item_name=item_name, buyer=buyer, quantity=quantity, unit_price=unit_price, amount=amount, note=note, page=page, q=q, user=user)


async def delete_entry(
    entry_id: int,
    request: Request,
    partner_id: int = Form(...),
    page: str = Form(''),
    q: str = Form(''),
    user: dict = Depends(require_menu('sales')),
):
    return await controller.delete_entry(entry_id=entry_id, request=request, partner_id=partner_id, page=page, q=q, user=user)


async def entry_detail_page(entry_id: int, request: Request, user: dict = Depends(require_menu('sales'))):
    return await controller.entry_detail_page(entry_id=entry_id, request=request, user=user)


async def save_entry_details(entry_id: int, request: Request, user: dict = Depends(require_menu('sales'))):
    return await controller.save_entry_details(entry_id=entry_id, request=request, user=user)


async def public_partner_ledger(token: str, request: Request):
    return await controller.public_partner_ledger(token=token, request=request)


async def public_partner_entry_detail(token: str, entry_id: int, request: Request):
    return await controller.public_partner_entry_detail(token=token, entry_id=entry_id, request=request)
