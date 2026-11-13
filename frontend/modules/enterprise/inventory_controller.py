"""Inventory Management Router (Sổ theo dõi tồn kho & Nhật ký nhập xuất)."""

from datetime import date, datetime
from decimal import Decimal
import logging
from typing import Optional

from fastapi import Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from frontend.deps import get_menu_context, templates
from frontend.modules.enterprise import inventory_service
from frontend.modules.enterprise.active_company import get_active_company
from frontend.pagination import paginate_counts

logger = logging.getLogger(__name__)


def _json_ok(message: str, data: Optional[dict] = None) -> JSONResponse:
    payload = {"ok": True, "message": message}
    if data is not None:
        payload["data"] = jsonable_encoder(
            data,
            custom_encoder={Decimal: float, date: lambda d: d.isoformat(), datetime: lambda dt: dt.isoformat()},
        )
    return JSONResponse(payload)


def _json_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=status_code)


async def _get_payload(request: Request) -> dict:
    try:
        data = await request.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    try:
        form = await request.form()
        return dict(form)
    except Exception:
        return {}


async def inventory_page(
    request: Request,
    company_id: Optional[int],
    tab: Optional[str],
    q: Optional[str],
    warehouse: Optional[str],
    kind: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    page: int,
    per_page: int,
    user: dict,
):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx, company_id)
    cid = company["id"] if company else 52

    tab_val = "stock" if (tab or "").strip() == "stock" else "journal"
    q_val = (q or "").strip()
    warehouse_val = (warehouse or "").strip()
    kind_val = (kind or "").strip().lower()
    date_from_val = (date_from or "").strip()
    date_to_val = (date_to or "").strip()
    page_val = max(1, int(page or 1))
    per_page_val = max(1, min(200, int(per_page or 100)))

    warehouses = inventory_service.list_warehouses(cid)
    units = inventory_service.list_units(cid)
    all_items = inventory_service.list_all_items(cid)

    if tab_val == "journal":
        movements, total = inventory_service.journal(
            cid,
            q=q_val,
            warehouse=warehouse_val,
            kind=kind_val,
            date_from=date_from_val or None,
            date_to=date_to_val or None,
            page=page_val,
            per_page=per_page_val,
        )
        items = []
    else:
        items, total = inventory_service.stock_book(
            cid,
            q=q_val,
            warehouse=warehouse_val,
            date_from=date_from_val or None,
            date_to=date_to_val or None,
            page=page_val,
            per_page=per_page_val,
        )
        movements = []

    extra_params: dict[str, str] = {"tab": tab_val}
    if q_val:
        extra_params["q"] = q_val
    if warehouse_val:
        extra_params["warehouse"] = warehouse_val
    if kind_val and tab_val == "journal":
        extra_params["kind"] = kind_val
    if date_from_val:
        extra_params["date_from"] = date_from_val
    if date_to_val:
        extra_params["date_to"] = date_to_val

    pagination = paginate_counts(
        total,
        page=page_val,
        per_page=per_page_val,
        base_url="/admin/enterprise/inventory",
        extra_params=extra_params,
    )

    ctx.update(
        {
            "request": request,
            "company": company,
            "active_company": company,
            "current_menu": "inventory",
            "tab": tab_val,
            "q": q_val,
            "warehouse": warehouse_val,
            "kind": kind_val,
            "date_from": date_from_val,
            "date_to": date_to_val,
            "warehouses": warehouses,
            "units": units,
            "all_items": all_items,
            "items": items,
            "movements": movements,
            "pagination": pagination,
            "total": total,
        }
    )
    return templates.TemplateResponse("pages/enterprise/inventory.html", ctx)


async def api_create_item(request: Request, user: dict):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else 52

    data = await _get_payload(request)
    try:
        item = inventory_service.save_item(cid, data)
        return _json_ok("Đã thêm mặt hàng thành công.", item)
    except ValueError as exc:
        return _json_error(str(exc), status_code=400)


async def api_update_item(item_id: int, request: Request, user: dict):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else 52

    data = await _get_payload(request)
    try:
        item = inventory_service.save_item(cid, data, item_id=item_id)
        return _json_ok("Đã cập nhật mặt hàng thành công.", item)
    except ValueError as exc:
        return _json_error(str(exc), status_code=400)


async def api_delete_item(item_id: int, request: Request, user: dict):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else 52

    ok = inventory_service.delete_item(cid, item_id)
    if not ok:
        return _json_error("Không tìm thấy mặt hàng để xóa.", status_code=404)
    return _json_ok("Đã xóa mặt hàng.")


async def api_merge_item(item_id: int, request: Request, user: dict):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else 52

    data = await _get_payload(request)
    raw_target_id = data.get("target_id")
    try:
        if raw_target_id is None or str(raw_target_id).strip() == "":
            raise ValueError("Vui lòng chọn mặt hàng đích.")
        try:
            target_id = int(raw_target_id)
        except (ValueError, TypeError):
            raise ValueError("Mặt hàng đích không hợp lệ.")
        res = inventory_service.merge_items(cid, item_id, target_id)
        return _json_ok("Đã gộp mặt hàng.", res)
    except ValueError as exc:
        return _json_error(str(exc), status_code=400)


async def api_create_movement(request: Request, user: dict):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else 52
    uid = user.get("id") or user.get("user_id")

    data = await _get_payload(request)
    try:
        m = inventory_service.save_movement(cid, data, user_id=uid)
        return _json_ok("Đã lưu phiếu thành công.", m)
    except ValueError as exc:
        return _json_error(str(exc), status_code=400)


async def api_update_movement(movement_id: int, request: Request, user: dict):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else 52
    uid = user.get("id") or user.get("user_id")

    data = await _get_payload(request)
    try:
        m = inventory_service.save_movement(cid, data, user_id=uid, movement_id=movement_id)
        return _json_ok("Đã cập nhật phiếu thành công.", m)
    except ValueError as exc:
        return _json_error(str(exc), status_code=400)


async def api_delete_movement(movement_id: int, request: Request, user: dict):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else 52

    ok = inventory_service.delete_movement(cid, movement_id)
    if not ok:
        return _json_error("Không tìm thấy phiếu để xóa.", status_code=404)
    return _json_ok("Đã xóa phiếu.")


async def api_import_excel(request: Request, file: UploadFile, user: dict):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else 52
    uid = user.get("id") or user.get("user_id")

    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        return _json_error("Vui lòng tải lên file Excel (.xlsx).", status_code=400)

    content = await file.read()
    res = inventory_service.import_excel(cid, content, user_id=uid)
    return _json_ok(f"Đã nhập {res['items']} mặt hàng, {res['movements']} phiếu.", res)


async def api_check_stock(
    request: Request,
    warehouse: Optional[str],
    name: Optional[str],
    qty: Optional[str],
    sale_id: Optional[str],
    user: dict,
):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else 52

    clean_sale_id = None
    if sale_id is not None and str(sale_id).strip() != "":
        try:
            clean_sale_id = int(str(sale_id).strip())
        except (ValueError, TypeError):
            clean_sale_id = None

    res = inventory_service.check_stock(
        cid,
        warehouse=warehouse or "",
        product_name=name or "",
        qty=qty if qty is not None else 0,
        exclude_sale_id=clean_sale_id,
    )
    return JSONResponse(res)


async def api_suggest_items(request: Request, q: Optional[str], user: dict):
    """Stock items whose name matches every word typed, for the sale form's item name box."""
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else 52
    term = (q or "").strip()
    if not term:
        return JSONResponse({"items": []})
    rows, _ = inventory_service.stock_book(cid, q=term, page=1, per_page=15)
    return JSONResponse({"items": [
        {"name": r["name"], "warehouse": r["warehouse"] or "", "unit": r["unit"] or "", "closing": float(r["closing"])}
        for r in rows
    ]})


async def download_import_template(user: dict):
    from fastapi.responses import Response
    from frontend.formatting.http_headers import content_disposition
    return Response(
        content=inventory_service.import_template_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition("Mau_nhap_ton_kho.xlsx")},
    )
