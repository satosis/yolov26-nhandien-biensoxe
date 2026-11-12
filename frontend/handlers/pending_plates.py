"""Cầu nối HTTP của frontend.routers.pending_plates_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Depends, Form, Query
from frontend.deps import require_menu
from pydantic import PositiveInt
from typing import Optional
import frontend.routers.pending_plates_controller as controller


async def update_pending_plate(
    pending_id: str,
    request: Request,
    user: dict = Depends(require_menu('pending_plates')),
):
    return await controller.update_pending_plate(pending_id=pending_id, request=request, user=user)


async def register_page(
    pending_id: str,
    request: Request,
    user: dict = Depends(require_menu('pending_plates')),
):
    return await controller.register_page(pending_id=pending_id, request=request, user=user)


async def register_plate(
    pending_id: str,
    plate_number: str = Form(...),
    owner_name: Optional[str] = Form(None),
    site_ids: list[PositiveInt] = Form(default=[]),
    note: Optional[str] = Form(None),
    user: dict = Depends(require_menu('pending_plates')),
):
    return await controller.register_plate(pending_id=pending_id, plate_number=plate_number, owner_name=owner_name, site_ids=site_ids, note=note, user=user)


async def pending_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('pending_plates')),
):
    return await controller.pending_page(request=request, page=page, user=user)


async def bulk_reject_plates(
    payload: controller.BulkRejectInput,
    _: dict = Depends(require_menu('pending_plates')),
):
    return await controller.bulk_reject_plates(payload=payload, _=_)


async def resolve_plate(
    pending_id: str,
    request: Request,
    user: dict = Depends(require_menu('pending_plates')),
):
    return await controller.resolve_plate(pending_id=pending_id, request=request, user=user)
