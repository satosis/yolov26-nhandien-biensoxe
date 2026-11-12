"""Cầu nối HTTP của frontend.routers.parking_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Depends, Query
from frontend.deps import require_menu
import frontend.routers.parking_controller as controller


async def dashboard(request: Request, user: dict = Depends(require_menu('parking_dashboard'))):
    return await controller.dashboard(request=request, user=user)


async def logs(
    request: Request,
    date: str = None,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('parking_logs')),
):
    return await controller.logs(request=request, date=date, page=page, user=user)


async def update_parking_history_plate(
    session_id: int,
    request: Request,
    user: dict = Depends(require_menu('parking_logs')),
):
    return await controller.update_parking_history_plate(session_id=session_id, request=request, user=user)


async def delete_parking_history(session_id: int, user: dict = Depends(require_menu('parking_logs'))):
    return await controller.delete_parking_history(session_id=session_id, user=user)


async def analytics(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('parking_analytics')),
):
    return await controller.analytics(request=request, page=page, user=user)
