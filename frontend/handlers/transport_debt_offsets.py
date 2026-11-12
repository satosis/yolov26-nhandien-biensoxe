"""Cầu nối HTTP của frontend.modules.transport.debt_offsets_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.modules.transport.debt_offsets_controller as controller


async def debt_offsets_page(
    request: Request,
    page: int = Query(1, ge=1),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.debt_offsets_page(request=request, page=page, message=message, error=error, user=user)


async def create_debt_offset(
    request: Request,
    offset_date: str = Form(''),
    debit_partner_id: str = Form(''),
    credit_partner_id: str = Form(''),
    amount: str = Form(''),
    description: str = Form(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.create_debt_offset(request=request, offset_date=offset_date, debit_partner_id=debit_partner_id, credit_partner_id=credit_partner_id, amount=amount, description=description, user=user)


async def edit_debt_offset(
    request: Request,
    offset_id: int,
    page: int = Form(1),
    offset_date: str = Form(''),
    debit_partner_id: str = Form(''),
    credit_partner_id: str = Form(''),
    amount: str = Form(''),
    description: str = Form(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.edit_debt_offset(request=request, offset_id=offset_id, page=page, offset_date=offset_date, debit_partner_id=debit_partner_id, credit_partner_id=credit_partner_id, amount=amount, description=description, user=user)


async def delete_debt_offset(
    request: Request,
    offset_id: int,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.delete_debt_offset(request=request, offset_id=offset_id, user=user)
