"""Cầu nối HTTP của frontend.modules.service_accounts.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
from typing import Optional
import frontend.modules.service_accounts.controller as controller


async def service_accounts_page(
    request: Request,
    category: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    edit_id: Optional[int] = None,
    user: dict = Depends(require_menu('service_accounts')),
):
    return await controller.service_accounts_page(request=request, category=category, search=search, status=status, page=page, edit_id=edit_id, user=user)


async def save_account_endpoint(
    request: Request,
    portal_url: str = Form(...),
    account_username: str = Form(...),
    account_password: str = Form(...),
    account_id: Optional[int] = Form(None),
    service_name: Optional[str] = Form(''),
    category: Optional[str] = Form('other'),
    note: Optional[str] = Form(''),
    user: dict = Depends(require_menu('service_accounts')),
):
    return await controller.save_account_endpoint(request=request, portal_url=portal_url, account_username=account_username, account_password=account_password, account_id=account_id, service_name=service_name, category=category, note=note, user=user)


async def delete_account_endpoint(
    account_id: int,
    request: Request,
    user: dict = Depends(require_menu('service_accounts')),
):
    return await controller.delete_account_endpoint(account_id=account_id, request=request, user=user)


async def quick_balance_endpoint(
    account_id: int,
    balance: str = Form(...),
    _: dict = Depends(require_menu('service_accounts')),
):
    return await controller.quick_balance_endpoint(account_id=account_id, balance=balance, _=_)


async def alias_service_accounts_page():
    return await controller.alias_service_accounts_page()


async def alias_save_account(request: Request):
    return await controller.alias_save_account(request=request)
