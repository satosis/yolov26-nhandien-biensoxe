"""Cầu nối HTTP của frontend.modules.transport.prepaid_expenses_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends
from frontend.deps import require_menu
import frontend.modules.transport.prepaid_expenses_controller as controller


async def prepaid_expenses_page(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    tab: str = Query('allocation'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    kind: str = Query('all'),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.prepaid_expenses_page(request=request, year=year, period=period, tab=tab, date_from=date_from, date_to=date_to, kind=kind, user=user)


async def prepaid_expenses_export(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    kind: str = Query('all'),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.prepaid_expenses_export(request=request, year=year, period=period, date_from=date_from, date_to=date_to, kind=kind, user=user)


async def api_add_ccdc(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.api_add_ccdc(request=request, user=user)


async def api_update_ccdc(request: Request, item_id: int, user: dict = Depends(require_menu('vehicles'))):
    return await controller.api_update_ccdc(request=request, item_id=item_id, user=user)


async def api_delete_ccdc(request: Request, item_id: int, user: dict = Depends(require_menu('vehicles'))):
    return await controller.api_delete_ccdc(request=request, item_id=item_id, user=user)


async def api_add_voucher(request: Request, kind: str, user: dict = Depends(require_menu('vehicles'))):
    return await controller.api_add_voucher(request=request, kind=kind, user=user)


async def api_update_voucher(
    request: Request,
    voucher_id: int,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_update_voucher(request=request, voucher_id=voucher_id, user=user)


async def api_delete_voucher(
    request: Request,
    voucher_id: int,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_delete_voucher(request=request, voucher_id=voucher_id, user=user)
