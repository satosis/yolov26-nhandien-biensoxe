"""Cầu nối HTTP của frontend.modules.enterprise.inventory_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, UploadFile, File
from frontend.deps import require_menu
from typing import Optional
import frontend.modules.enterprise.inventory_controller as controller


async def inventory_page(
    request: Request,
    company_id: Optional[int] = Query(None),
    tab: Optional[str] = Query('journal'),
    q: Optional[str] = Query(None),
    warehouse: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1),
    per_page: int = Query(100),
    user: dict = Depends(require_menu('inventory')),
):
    return await controller.inventory_page(request=request, company_id=company_id, tab=tab, q=q, warehouse=warehouse, kind=kind, date_from=date_from, date_to=date_to, page=page, per_page=per_page, user=user)


async def api_create_item(request: Request, user: dict = Depends(require_menu('inventory'))):
    return await controller.api_create_item(request=request, user=user)


async def api_update_item(item_id: int, request: Request, user: dict = Depends(require_menu('inventory'))):
    return await controller.api_update_item(item_id=item_id, request=request, user=user)


async def api_delete_item(item_id: int, request: Request, user: dict = Depends(require_menu('inventory'))):
    return await controller.api_delete_item(item_id=item_id, request=request, user=user)


async def api_merge_item(item_id: int, request: Request, user: dict = Depends(require_menu('inventory'))):
    return await controller.api_merge_item(item_id=item_id, request=request, user=user)


async def api_create_movement(request: Request, user: dict = Depends(require_menu('inventory'))):
    return await controller.api_create_movement(request=request, user=user)


async def api_update_movement(
    movement_id: int,
    request: Request,
    user: dict = Depends(require_menu('inventory')),
):
    return await controller.api_update_movement(movement_id=movement_id, request=request, user=user)


async def api_delete_movement(
    movement_id: int,
    request: Request,
    user: dict = Depends(require_menu('inventory')),
):
    return await controller.api_delete_movement(movement_id=movement_id, request=request, user=user)


async def api_import_excel(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_menu('inventory')),
):
    return await controller.api_import_excel(request=request, file=file, user=user)


async def api_check_stock(
    request: Request,
    warehouse: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    qty: Optional[str] = Query(None),
    sale_id: Optional[str] = Query(None),
    user: dict = Depends(require_menu('sales')),
):
    return await controller.api_check_stock(request=request, warehouse=warehouse, name=name, qty=qty, sale_id=sale_id, user=user)


async def api_suggest_items(
    request: Request,
    q: Optional[str] = Query(None),
    user: dict = Depends(require_menu('sales')),
):
    "Stock items whose name matches every word typed, for the sale form's item name box."
    return await controller.api_suggest_items(request=request, q=q, user=user)


async def download_import_template(user: dict = Depends(require_menu('inventory'))):
    return await controller.download_import_template(user=user)
