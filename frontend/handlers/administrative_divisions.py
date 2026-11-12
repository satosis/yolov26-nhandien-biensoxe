"""Cầu nối HTTP của frontend.routers.administrative_divisions_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends
from frontend.deps import require_menu
import frontend.routers.administrative_divisions_controller as controller


async def administrative_divisions_page(
    request: Request,
    province: str = Query('', max_length=2),
    q: str = Query('', max_length=100),
    page: int = Query(1, ge=1),
    synced: str = Query(''),
    user: dict = Depends(require_menu('administrative_divisions')),
):
    return await controller.administrative_divisions_page(request=request, province=province, q=q, page=page, synced=synced, user=user)


async def sync_administrative_divisions(user: dict = Depends(require_menu('administrative_divisions'))):
    return await controller.sync_administrative_divisions(user=user)
