"""Cầu nối HTTP của frontend.routers.api_keys_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Depends, Form
from frontend.deps import require_menu
import frontend.routers.api_keys_controller as controller


async def api_keys_page(request: Request, user: dict = Depends(require_menu('api_keys'))):
    return await controller.api_keys_page(request=request, user=user)


async def save_api_keys(
    keys: list[str] = Form([]),
    values: list[str] = Form([]),
    user: dict = Depends(require_menu('api_keys')),
):
    return await controller.save_api_keys(keys=keys, values=values, user=user)


async def alias_api_keys():
    return await controller.alias_api_keys()
