"""Cầu nối HTTP của frontend.routers.providers_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
from typing import Optional
import frontend.routers.providers_controller as controller


async def providers_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('providers')),
):
    return await controller.providers_page(request=request, page=page, user=user)


async def edit_api_keys_page(
    provider_key: str,
    request: Request,
    user: dict = Depends(require_menu('providers')),
):
    return await controller.edit_api_keys_page(provider_key=provider_key, request=request, user=user)


async def save_api_keys(provider_key: str, request: Request, _: dict = Depends(require_menu('providers'))):
    return await controller.save_api_keys(provider_key=provider_key, request=request, _=_)


async def upsert_provider(
    provider_key: str = Form(...),
    name: str = Form(...),
    api_base_url: Optional[str] = Form(None),
    api_doc_url: Optional[str] = Form(None),
    rtsp_template: Optional[str] = Form(None),
    onvif_port: int = Form(80),
    default_rtsp_port: int = Form(554),
    default_http_port: int = Form(80),
    notes: Optional[str] = Form(None),
    logo_url: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    _: dict = Depends(require_menu('providers')),
):
    return await controller.upsert_provider(provider_key=provider_key, name=name, api_base_url=api_base_url, api_doc_url=api_doc_url, rtsp_template=rtsp_template, onvif_port=onvif_port, default_rtsp_port=default_rtsp_port, default_http_port=default_http_port, notes=notes, logo_url=logo_url, is_active=is_active, _=_)


async def delete_provider(provider_key: str, _: dict = Depends(require_menu('providers'))):
    return await controller.delete_provider(provider_key=provider_key, _=_)


async def toggle_provider(provider_key: str, _: dict = Depends(require_menu('providers'))):
    return await controller.toggle_provider(provider_key=provider_key, _=_)
