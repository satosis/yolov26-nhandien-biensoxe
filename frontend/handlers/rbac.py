"""Cầu nối HTTP của frontend.routers.rbac_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
from typing import List
import frontend.routers.rbac_controller as controller


async def rbac_page(request: Request, page: int = Query(1, ge=1), user: dict = Depends(require_menu('rbac'))):
    return await controller.rbac_page(request=request, page=page, user=user)


async def create_role(
    role_name: str = Form(...),
    allowed_menus: List[str] = Form([]),
    _: dict = Depends(require_menu('rbac')),
):
    return await controller.create_role(role_name=role_name, allowed_menus=allowed_menus, _=_)


async def update_role(
    role_id: int,
    allowed_menus: List[str] = Form([]),
    _: dict = Depends(require_menu('rbac')),
):
    return await controller.update_role(role_id=role_id, allowed_menus=allowed_menus, _=_)


async def delete_role(role_id: int, _: dict = Depends(require_menu('rbac'))):
    return await controller.delete_role(role_id=role_id, _=_)
