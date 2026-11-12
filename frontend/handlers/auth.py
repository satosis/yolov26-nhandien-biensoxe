"""Cầu nối HTTP của frontend.modules.auth.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Form, Depends
from frontend.deps import get_current_user
import frontend.modules.auth.controller as controller


async def login_page(request: Request):
    return await controller.login_page(request=request)


async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    return await controller.login_submit(request=request, username=username, password=password)


async def register_page(request: Request):
    return await controller.register_page(request=request)


async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    full_name: str = Form(''),
    email: str = Form(''),
):
    return await controller.register_submit(request=request, username=username, password=password, confirm_password=confirm_password, full_name=full_name, email=email)


async def forgot_password_page(request: Request):
    return await controller.forgot_password_page(request=request)


async def forgot_password_submit(request: Request, account: str = Form(...)):
    return await controller.forgot_password_submit(request=request, account=account)


async def auth_google(request: Request):
    return await controller.auth_google(request=request)


async def auth_google_callback(request: Request, code: str = None, state: str = None, error: str = None):
    return await controller.auth_google_callback(request=request, code=code, state=state, error=error)


async def auth_facebook(request: Request):
    return await controller.auth_facebook(request=request)


async def auth_facebook_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None,
):
    return await controller.auth_facebook_callback(request=request, code=code, state=state, error=error, error_description=error_description)


async def logout(request: Request):
    return await controller.logout(request=request)


async def set_site(
    request: Request,
    site_id: int = Form(0, ge=0, le=2147483647),
    user: dict = Depends(get_current_user),
):
    return await controller.set_site(request=request, site_id=site_id, user=user)


async def set_company(
    request: Request,
    company_id: int = Form(0, ge=0),
    user: dict = Depends(get_current_user),
):
    return await controller.set_company(request=request, company_id=company_id, user=user)
