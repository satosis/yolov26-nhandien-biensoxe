"""Cầu nối HTTP của frontend.routers.legal_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Form
from typing import Optional
import frontend.routers.legal_controller as controller


async def privacy_policy(request: Request):
    'Public Privacy Policy Page for Facebook App Review & general compliance.'
    return await controller.privacy_policy(request=request)


async def data_deletion_page(request: Request, success_code: Optional[str] = None):
    'User Data Deletion Instructions URL required by Meta App Dashboard.'
    return await controller.data_deletion_page(request=request, success_code=success_code)


async def submit_deletion_request(
    request: Request,
    contact_name: str = Form(...),
    contact_email: str = Form(...),
    user_id: Optional[str] = Form(''),
    reason: Optional[str] = Form(''),
):
    'Handle direct user data deletion requests submitted from the form.'
    return await controller.submit_deletion_request(request=request, contact_name=contact_name, contact_email=contact_email, user_id=user_id, reason=reason)


async def data_deletion_status(request: Request, code: Optional[str] = None):
    'Check data deletion request status by confirmation code (Meta requirement).'
    return await controller.data_deletion_status(request=request, code=code)


async def facebook_data_deletion_callback(request: Request):
    'Facebook User Data Deletion Callback endpoint.\n\n    When a user removes the app on Facebook, Meta calls this endpoint with\n    `signed_request`. We respond with JSON containing `url` and `confirmation_code`.\n    '
    return await controller.facebook_data_deletion_callback(request=request)


async def facebook_data_deletion_get():
    'Redirect browser GET requests directly to the Data Deletion instructions page.'
    return await controller.facebook_data_deletion_get()


async def google_site_verification(code: str):
    'Serve a Search Console ownership file such as /google55b00cbec18ca0e8.html from frontend/site_verification/.'
    return await controller.google_site_verification(code=code)
