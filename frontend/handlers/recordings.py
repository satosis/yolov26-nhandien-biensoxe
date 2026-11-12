"""Cầu nối HTTP của frontend.routers.recordings_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Depends, Query
from frontend.deps import require_menu
import frontend.routers.recordings_controller as controller


async def recordings_page(
    request: Request,
    user: dict = Depends(require_menu('recordings')),
    site_id: int = Query(None),
    page: int = Query(1, ge=1),
):
    return await controller.recordings_page(request=request, user=user, site_id=site_id, page=page)


async def delete_recording(recording_id: int, _: dict = Depends(require_menu('recordings'))):
    return await controller.delete_recording(recording_id=recording_id, _=_)
