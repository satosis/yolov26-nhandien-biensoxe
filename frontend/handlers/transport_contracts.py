"""Cầu nối HTTP của frontend.modules.transport.contracts_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends
from frontend.deps import require_menu
from typing import Optional
import frontend.modules.transport.contracts_controller as controller


async def contracts_page(
    request: Request,
    customer_id: Optional[int] = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.contracts_page(request=request, company_id=customer_id, user=user)


async def export_contract(
    request: Request,
    customer_id: Optional[int] = Query(None),
    date_param: Optional[str] = Query(None, alias='date'),
    trip_id: Optional[int] = Query(None),
    contract_no: Optional[str] = Query(None),
    kind: str = Query('hdvc'),
    download: Optional[str] = Query(None),
    format: Optional[str] = Query(None),
    view: Optional[str] = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    'Return a preview or download directly; never create a contract record.'
    return await controller.export_contract(request=request, company_id=customer_id, date_param=date_param, trip_id=trip_id, contract_no=contract_no, kind=kind, download=download, format=format, view=view, user=user)


async def contract_zalo_groups(
    request: Request,
    customer_id: Optional[int] = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.contract_zalo_groups(request=request, company_id=customer_id, user=user)


async def send_contract_zalo(
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.send_contract_zalo(request=request, user=user)
