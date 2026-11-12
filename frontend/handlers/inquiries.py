"""Cầu nối HTTP của frontend.routers.inquiries_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.routers.inquiries_controller as controller


async def inquiries_page(
    request: Request,
    page: int = Query(1, ge=1),
    service: str = Query(''),
    user: dict = Depends(require_menu('inquiries')),
):
    return await controller.inquiries_page(request=request, page=page, user=user, service=service)


async def mark_all_read(__user: dict = Depends(require_menu('inquiries'))):
    return await controller.mark_all_read(__user=__user)


async def mark_read(inquiry_id: int, _user: dict = Depends(require_menu('inquiries'))):
    return await controller.mark_read(inquiry_id=inquiry_id, _user=_user)


async def save_note(inquiry_id: int, note: str = Form(''), _user: dict = Depends(require_menu('inquiries'))):
    return await controller.save_note(inquiry_id=inquiry_id, note=note, _user=_user)


async def delete_inquiry_endpoint(inquiry_id: int, _user: dict = Depends(require_menu('inquiries'))):
    return await controller.delete_inquiry_endpoint(inquiry_id=inquiry_id, _user=_user)
