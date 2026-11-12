"""Cầu nối HTTP của frontend.routers.sites_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.routers.sites_controller as controller


async def sites_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('sites')),
):
    return await controller.sites_page(request=request, page=page, user=user)


async def create_site(
    request: Request,
    user: dict = Depends(require_menu('sites')),
    site_name: str = Form(...),
    address: str = Form(''),
    description: str = Form(''),
    total_slots: int = Form(0),
    is_active: str = Form('on'),
    page: int = Query(1, ge=1),
):
    return await controller.create_site(request=request, user=user, site_name=site_name, address=address, description=description, total_slots=total_slots, is_active=is_active, page=page)


async def edit_site_form(
    site_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('sites')),
):
    return await controller.edit_site_form(site_id=site_id, request=request, page=page, user=user)


async def get_site_row(
    site_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('sites')),
):
    return await controller.get_site_row(site_id=site_id, request=request, page=page, user=user)


async def update_site(
    site_id: int,
    request: Request,
    user: dict = Depends(require_menu('sites')),
    site_name: str = Form(...),
    address: str = Form(''),
    description: str = Form(''),
    total_slots: int = Form(0),
    is_active: str = Form(None),
    page: int = Query(1, ge=1),
):
    return await controller.update_site(site_id=site_id, request=request, user=user, site_name=site_name, address=address, description=description, total_slots=total_slots, is_active=is_active, page=page)


async def delete_site(
    site_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('sites')),
):
    return await controller.delete_site(site_id=site_id, request=request, page=page, user=user)


async def toggle_site_status(
    site_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('sites')),
):
    return await controller.toggle_site_status(site_id=site_id, request=request, page=page, user=user)
