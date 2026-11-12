"""Cầu nối HTTP của frontend.routers.kyc_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Depends, Request, Form, UploadFile, File
from frontend.deps import get_current_user, require_menu
from typing import Optional
import frontend.routers.kyc_controller as controller


async def clean_cache(user: dict = Depends(get_current_user)):
    return await controller.clean_cache(user=user)


async def kyc_page(request: Request, user: dict = Depends(get_current_user)):
    return await controller.kyc_page(request=request, user=user)


async def submit_basic_info(
    user: dict = Depends(get_current_user),
    full_name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(...),
    date_of_birth: str = Form(...),
    id_number: str = Form(...),
):
    return await controller.submit_basic_info(user=user, full_name=full_name, phone=phone, email=email, date_of_birth=date_of_birth, id_number=id_number)


async def upload_id_doc(
    user: dict = Depends(get_current_user),
    doc_type: str = Form(...),
    file: UploadFile = File(...),
):
    return await controller.upload_id_doc(user=user, doc_type=doc_type, file=file)


async def delete_id_doc(user: dict = Depends(get_current_user), doc_type: str = Form(...)):
    return await controller.delete_id_doc(user=user, doc_type=doc_type)


async def upload_face(
    user: dict = Depends(get_current_user),
    angle: str = Form(...),
    file: UploadFile = File(...),
    target_user_id: Optional[int] = Form(None),
):
    return await controller.upload_face(user=user, angle=angle, file=file, target_user_id=target_user_id)


async def delete_face(
    angle: str,
    user: dict = Depends(get_current_user),
    target_user_id: Optional[int] = None,
):
    return await controller.delete_face(angle=angle, user=user, target_user_id=target_user_id)


async def reset_kyc(user: dict = Depends(get_current_user)):
    return await controller.reset_kyc(user=user)


async def kyc_admin_page(request: Request, user: dict = Depends(require_menu('users'))):
    return await controller.kyc_admin_page(request=request, user=user)


async def kyc_admin_detail(user_id: int, request: Request, user: dict = Depends(require_menu('users'))):
    return await controller.kyc_admin_detail(user_id=user_id, request=request, user=user)


async def approve_kyc(user_id: int, user: dict = Depends(require_menu('users'))):
    return await controller.approve_kyc(user_id=user_id, user=user)


async def reject_kyc(
    user_id: int,
    user: dict = Depends(require_menu('users')),
    reason: str = Form('Không đạt yêu cầu'),
):
    return await controller.reject_kyc(user_id=user_id, user=user, reason=reason)


async def serve_kyc_upload(user_id: int, filename: str, user: dict = Depends(get_current_user)):
    return await controller.serve_kyc_upload(user_id=user_id, filename=filename, user=user)


async def register_stranger(
    user: dict = Depends(require_menu('strangers')),
    stranger_id: int = Form(...),
    user_id: int = Form(...),
):
    return await controller.register_stranger(user=user, stranger_id=stranger_id, user_id=user_id)
