"""Cầu nối HTTP của frontend.modules.transport.depreciation_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends
from frontend.deps import require_menu
import frontend.modules.transport.depreciation_controller as controller


async def depreciation_page(
    request: Request,
    year: int = Query(None),
    mode: str = Query('ytd'),
    period: str = Query(None),
    tab: str = Query('depreciation'),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.depreciation_page(request=request, year=year, mode=mode, period=period, tab=tab, user=user)


async def export_depreciation(
    request: Request,
    year: int = Query(None),
    mode: str = Query('ytd'),
    period: str = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_depreciation(request=request, year=year, mode=mode, period=period, user=user)


async def api_add_asset(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.api_add_asset(request=request, user=user)


async def api_update_asset(
    request: Request,
    source: str,
    source_id: int,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_update_asset(request=request, source=source, source_id=source_id, user=user)


async def api_delete_asset(
    request: Request,
    source: str,
    source_id: int,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_delete_asset(request=request, source=source, source_id=source_id, user=user)


async def api_restore_vehicle(
    request: Request,
    vehicle_id: int,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_restore_vehicle(request=request, vehicle_id=vehicle_id, user=user)


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
