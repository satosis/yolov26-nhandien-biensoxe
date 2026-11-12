"""Cầu nối HTTP của frontend.routers.strangers_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.routers.strangers_controller as controller


async def strangers_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('strangers')),
):
    return await controller.strangers_page(request=request, page=page, user=user)


async def review_stranger(stranger_id: int, _: dict = Depends(require_menu('strangers'))):
    return await controller.review_stranger(stranger_id=stranger_id, _=_)


async def delete_stranger(stranger_id: int, _: dict = Depends(require_menu('strangers'))):
    return await controller.delete_stranger(stranger_id=stranger_id, _=_)


async def register_stranger(
    stranger_id: int = Form(..., gt=0),
    user_id: int = Form(..., gt=0),
    _: dict = Depends(require_menu('strangers')),
):
    return await controller.register_stranger(stranger_id=stranger_id, user_id=user_id, _=_)


async def bulk_register_strangers(
    payload: controller.BulkRegisterInput,
    _: dict = Depends(require_menu('strangers')),
):
    return await controller.bulk_register_strangers(payload=payload, _=_)


async def bulk_delete_strangers(
    payload: controller.BulkDeleteInput,
    _: dict = Depends(require_menu('strangers')),
):
    return await controller.bulk_delete_strangers(payload=payload, _=_)
