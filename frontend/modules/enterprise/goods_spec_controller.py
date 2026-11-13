"""Controller cho Sổ quy cách hàng hóa."""

from __future__ import annotations

from typing import Optional

from fastapi import Request, UploadFile
from starlette.concurrency import run_in_threadpool

from frontend.deps import get_menu_context, templates
from frontend.modules.enterprise import goods_spec_service
from frontend.modules.enterprise.active_company import get_active_company
from frontend.modules.enterprise.inventory_controller import (
    _get_payload,
    _json_error,
    _json_ok,
)


def _resolve_company(request: Request, user: dict, company_id: Optional[int] = None) -> tuple[dict, dict | None, int]:
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx, company_id)
    cid = company["id"] if company else 52
    return ctx, company, cid


async def specs_page(
    request: Request,
    company_id: Optional[int],
    user: dict,
):
    ctx, company, cid = _resolve_company(request, user, company_id)
    ctx.update({
        "request": request,
        "company": company,
        "active_company": company,
        "current_menu": "inventory",
        "tab": "specs",
        "book": goods_spec_service.spec_book(cid),
        "sections": goods_spec_service.SECTIONS,
    })
    return templates.TemplateResponse("pages/enterprise/inventory_specs.html", ctx)


async def api_create_spec(request: Request, user: dict):
    _, _, cid = _resolve_company(request, user)
    data = await _get_payload(request)
    try:
        res = goods_spec_service.save_spec(cid, data)
        return _json_ok("Đã lưu.", res)
    except ValueError as exc:
        return _json_error(str(exc), status_code=400)


async def api_update_spec(spec_id: int, request: Request, user: dict):
    _, _, cid = _resolve_company(request, user)
    data = await _get_payload(request)
    try:
        res = goods_spec_service.save_spec(cid, data, spec_id=spec_id)
        return _json_ok("Đã lưu.", res)
    except ValueError as exc:
        return _json_error(str(exc), status_code=400)


async def api_delete_spec(spec_id: int, request: Request, user: dict):
    _, _, cid = _resolve_company(request, user)
    ok = goods_spec_service.delete_spec(cid, spec_id)
    if not ok:
        return _json_error("Không tìm thấy quy cách.", status_code=404)
    return _json_ok("Đã xóa.")


async def api_add_photo(request: Request, file: UploadFile, caption: str, user: dict):
    _, _, cid = _resolve_company(request, user)
    content = await file.read()
    if not content:
        return _json_error("Vui lòng chọn ảnh.", status_code=400)
    try:
        res = await run_in_threadpool(goods_spec_service.add_photo, cid, (caption or "").strip(), content)
        return _json_ok("Đã thêm ảnh.", res)
    except ValueError as exc:
        return _json_error(str(exc), status_code=400)


async def api_update_photo(photo_id: int, request: Request, user: dict):
    _, _, cid = _resolve_company(request, user)
    data = await _get_payload(request)
    caption = str(data.get("caption") or "").strip()
    ok = goods_spec_service.update_photo(cid, photo_id, caption)
    if not ok:
        return _json_error("Không tìm thấy ảnh.", status_code=404)
    return _json_ok("Đã cập nhật chú thích.")


async def api_delete_photo(photo_id: int, request: Request, user: dict):
    _, _, cid = _resolve_company(request, user)
    ok = goods_spec_service.delete_photo(cid, photo_id)
    if not ok:
        return _json_error("Không tìm thấy ảnh.", status_code=404)
    return _json_ok("Đã xóa ảnh.")


async def api_import_excel(request: Request, file: UploadFile, user: dict):
    _, _, cid = _resolve_company(request, user)
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        return _json_error("Vui lòng tải lên file Excel (.xlsx).", status_code=400)
    content = await file.read()
    try:
        res = await run_in_threadpool(goods_spec_service.import_excel, cid, content)
        return _json_ok("Đã nhập sổ quy cách.", res)
    except ValueError as exc:
        return _json_error(str(exc), status_code=400)
