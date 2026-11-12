"""Cầu nối HTTP của frontend.modules.transport.other_vouchers_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.modules.transport.other_vouchers_controller as controller


async def other_vouchers_page(
    request: Request,
    page: int = Query(1, ge=1),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.other_vouchers_page(request=request, page=page, message=message, error=error, user=user)


async def create_other_voucher_action(
    request: Request,
    voucher_date: str = Form(''),
    debit_account: str = Form(''),
    credit_account: str = Form(''),
    debit_partner_id: str = Form(''),
    credit_partner_id: str = Form(''),
    amount: str = Form(''),
    description: str = Form(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.create_other_voucher_action(request=request, voucher_date=voucher_date, debit_account=debit_account, credit_account=credit_account, debit_partner_id=debit_partner_id, credit_partner_id=credit_partner_id, amount=amount, description=description, user=user)


async def edit_other_voucher_action(
    request: Request,
    voucher_id: int,
    page: int = Form(1),
    voucher_date: str = Form(''),
    debit_account: str = Form(''),
    credit_account: str = Form(''),
    debit_partner_id: str = Form(''),
    credit_partner_id: str = Form(''),
    amount: str = Form(''),
    description: str = Form(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.edit_other_voucher_action(request=request, voucher_id=voucher_id, page=page, voucher_date=voucher_date, debit_account=debit_account, credit_account=credit_account, debit_partner_id=debit_partner_id, credit_partner_id=credit_partner_id, amount=amount, description=description, user=user)


async def delete_other_voucher_action(
    request: Request,
    voucher_id: int,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.delete_other_voucher_action(request=request, voucher_id=voucher_id, user=user)
