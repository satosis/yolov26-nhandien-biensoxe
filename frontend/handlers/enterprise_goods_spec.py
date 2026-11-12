"""Cầu nối HTTP của frontend.modules.enterprise.goods_spec_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from typing import Optional

from fastapi import Depends, File, Form, Query, Request, UploadFile

from frontend.deps import require_menu
import frontend.modules.enterprise.goods_spec_controller as controller


async def specs_page(
    request: Request,
    company_id: Optional[int] = Query(None),
    user: dict = Depends(require_menu("inventory")),
):
    return await controller.specs_page(request=request, company_id=company_id, user=user)


async def api_create_spec(request: Request, user: dict = Depends(require_menu("inventory"))):
    return await controller.api_create_spec(request=request, user=user)


async def api_update_spec(spec_id: int, request: Request, user: dict = Depends(require_menu("inventory"))):
    return await controller.api_update_spec(spec_id=spec_id, request=request, user=user)


async def api_delete_spec(spec_id: int, request: Request, user: dict = Depends(require_menu("inventory"))):
    return await controller.api_delete_spec(spec_id=spec_id, request=request, user=user)


async def api_add_photo(
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(""),
    user: dict = Depends(require_menu("inventory")),
):
    return await controller.api_add_photo(request=request, file=file, caption=caption, user=user)


async def api_update_photo(photo_id: int, request: Request, user: dict = Depends(require_menu("inventory"))):
    return await controller.api_update_photo(photo_id=photo_id, request=request, user=user)


async def api_delete_photo(photo_id: int, request: Request, user: dict = Depends(require_menu("inventory"))):
    return await controller.api_delete_photo(photo_id=photo_id, request=request, user=user)


async def api_import_excel(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_menu("inventory")),
):
    return await controller.api_import_excel(request=request, file=file, user=user)
