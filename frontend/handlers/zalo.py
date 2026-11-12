"""Cầu nối HTTP của frontend.modules.zalo.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Header, Form, UploadFile, File
from frontend.deps import require_menu
import frontend.modules.zalo.controller as controller


async def inbox_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('zalo_inbox')),
):
    return await controller.inbox_page(request=request, page=page, user=user)


async def create_trip_draft(
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
    _: dict = Depends(require_menu('zalo_inbox')),
):
    return await controller.create_trip_draft(request=request, user=user, _=_)


async def connect_bridge(_: dict = Depends(require_menu('zalo_inbox'))):
    return await controller.connect_bridge(_=_)


async def disconnect_bridge(_: dict = Depends(require_menu('zalo_inbox'))):
    return await controller.disconnect_bridge(_=_)


async def list_companies_for_zalo_bridge(x_zalo_bridge_secret: str = Header('')):
    return await controller.list_companies_for_zalo_bridge(x_zalo_bridge_secret=x_zalo_bridge_secret)


async def ingest_message(
    event: str = Form(...),
    file: UploadFile | None = File(None),
    x_zalo_bridge_secret: str = Header(''),
):
    return await controller.ingest_message(event=event, file=file, x_zalo_bridge_secret=x_zalo_bridge_secret)


async def apply_saved_draft(
    draft_id: str,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
    _: dict = Depends(require_menu('zalo_inbox')),
):
    return await controller.apply_saved_draft(draft_id=draft_id, request=request, user=user, _=_)


async def finalize_session_now(
    group_id: str,
    request: Request,
    nonce: str = Form(...),
    user: dict = Depends(require_menu('vehicles')),
    _: dict = Depends(require_menu('zalo_inbox')),
):
    return await controller.finalize_session_now(group_id=group_id, request=request, nonce=nonce, user=user, _=_)


async def retry_salary_message(
    item_id: str,
    request: Request,
    nonce: str = Form(...),
    _: dict = Depends(require_menu('zalo_inbox')),
):
    return await controller.retry_salary_message(item_id=item_id, request=request, nonce=nonce, _=_)


async def download_file(item_id: str, _: dict = Depends(require_menu('zalo_inbox'))):
    return await controller.download_file(item_id=item_id, _=_)


async def login_qr(_: dict = Depends(require_menu('zalo_inbox'))):
    return await controller.login_qr(_=_)


async def review_message(item_id: str, _: dict = Depends(require_menu('zalo_inbox'))):
    return await controller.review_message(item_id=item_id, _=_)


async def analyze_file(item_id: str, _: dict = Depends(require_menu('zalo_inbox'))):
    return await controller.analyze_file(item_id=item_id, _=_)
