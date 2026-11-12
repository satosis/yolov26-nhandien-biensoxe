"""Cầu nối HTTP của frontend.modules.auth.profile_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Depends, Form, UploadFile, File
from frontend.deps import get_current_user
import frontend.modules.auth.profile_controller as controller


async def profile_page(request: Request, user: dict = Depends(get_current_user)):
    return await controller.profile_page(request=request, user=user)


async def save_profile(
    request: Request,
    full_name: str = Form(''),
    gender: str = Form(''),
    nickname: str = Form(''),
    phone: str = Form(''),
    email: str = Form(''),
    bank_account: str = Form(''),
    bank_name: str = Form(''),
    avatar: UploadFile | None = File(None),
    user: dict = Depends(get_current_user),
):
    return await controller.save_profile(request=request, full_name=full_name, gender=gender, nickname=nickname, phone=phone, email=email, bank_account=bank_account, bank_name=bank_name, avatar=avatar, user=user)


async def change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: dict = Depends(get_current_user),
):
    return await controller.change_password(current_password=current_password, new_password=new_password, confirm_password=confirm_password, user=user)
