"""Cầu nối HTTP của frontend.routers.door_providers_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
from typing import Optional
import frontend.routers.door_providers_controller as controller


async def door_providers_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('door_providers')),
):
    return await controller.door_providers_page(request=request, page=page, user=user)


async def upsert_door_provider_route(
    provider_key: str = Form(...),
    name: str = Form(...),
    connection_type: Optional[str] = Form(None),
    api_doc_url: Optional[str] = Form(None),
    config_template: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    logo_url: Optional[str] = Form(None),
    is_active: Optional[str] = Form('on'),
    _: dict = Depends(require_menu('door_providers')),
):
    return await controller.upsert_door_provider_route(provider_key=provider_key, name=name, connection_type=connection_type, api_doc_url=api_doc_url, config_template=config_template, notes=notes, logo_url=logo_url, is_active=is_active, _=_)


async def delete_door_provider_route(provider_key: str, _: dict = Depends(require_menu('door_providers'))):
    return await controller.delete_door_provider_route(provider_key=provider_key, _=_)


async def toggle_door_provider(provider_key: str, _: dict = Depends(require_menu('door_providers'))):
    return await controller.toggle_door_provider(provider_key=provider_key, _=_)
