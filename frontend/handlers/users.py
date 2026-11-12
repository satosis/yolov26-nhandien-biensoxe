"""Cầu nối HTTP của frontend.routers.users_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form, UploadFile, File
from frontend.deps import require_menu
from typing import Optional, Literal
import frontend.routers.users_controller as controller


async def users_page(
    request: Request,
    page: int = Query(1, ge=1),
    error: Optional[str] = None,
    user: dict = Depends(require_menu('users')),
):
    return await controller.users_page(request=request, page=page, error=error, user=user)


async def add_user_page(
    request: Request,
    error: Optional[str] = None,
    user: dict = Depends(require_menu('users')),
):
    return await controller.add_user_page(request=request, error=error, user=user)


async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    full_name: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    nickname: str = Form(''),
    phone: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    bank_account: Optional[str] = Form(None),
    bank_name: Optional[str] = Form(None),
    role_name: str = Form(...),
    site_ids: Optional[str] = Form(None, pattern=controller._SITE_IDS_PATTERN),
    note: Optional[str] = Form(None),
    _: dict = Depends(require_menu('users')),
):
    return await controller.create_user(request=request, username=username, password=password, full_name=full_name, gender=gender, nickname=nickname, phone=phone, email=email, bank_account=bank_account, bank_name=bank_name, role_name=role_name, site_ids=site_ids, note=note, _=_)


async def edit_user_page(
    user_id: int,
    request: Request,
    error: Optional[str] = None,
    user: dict = Depends(require_menu('users')),
):
    return await controller.edit_user_page(user_id=user_id, request=request, error=error, user=user)


async def edit_user(
    user_id: int,
    request: Request,
    username: str = Form(...),
    password: Optional[str] = Form(None),
    full_name: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    nickname: str = Form(''),
    phone: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    bank_account: Optional[str] = Form(None),
    bank_name: Optional[str] = Form(None),
    role_name: str = Form(...),
    site_ids: Optional[str] = Form(None, pattern=controller._SITE_IDS_PATTERN),
    note: Optional[str] = Form(None),
    _: dict = Depends(require_menu('users')),
):
    return await controller.edit_user(user_id=user_id, request=request, username=username, password=password, full_name=full_name, gender=gender, nickname=nickname, phone=phone, email=email, bank_account=bank_account, bank_name=bank_name, role_name=role_name, site_ids=site_ids, note=note, _=_)


async def delete_user(user_id: int, _: dict = Depends(require_menu('users'))):
    return await controller.delete_user(user_id=user_id, _=_)


async def bulk_delete_users(request: Request, _: dict = Depends(require_menu('users'))):
    return await controller.bulk_delete_users(request=request, _=_)


async def upload_face(
    user_id: int,
    face_image: UploadFile = File(...),
    angle: Literal['front', 'left', 'right', 'up', 'down'] = Form('front'),
    _: dict = Depends(require_menu('users')),
):
    return await controller.upload_face(user_id=user_id, face_image=face_image, angle=angle, _=_)
