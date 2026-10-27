"""Supplier Management Router (Sổ Nhập Hàng & Quản Lý Công Nợ NCC)."""

from datetime import date, datetime
from decimal import Decimal
import io
import logging
from typing import Optional
import urllib.parse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
import openpyxl

from backend.services.database import db
from frontend.deps import get_menu_context, require_menu, templates, format_number, format_currency
from frontend.modules.invoices.companies import get_company
from frontend.modules.enterprise import supplier_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enterprise/suppliers", tags=["suppliers"])


def _get_active_company(request: Request, ctx: dict, company_id: Optional[int] = None) -> dict | None:
    target_cid = company_id or ctx.get("active_company_id")
    company = get_company(target_cid) if target_cid else None
    user_comps = ctx.get("user_companies") or []

    if not company and user_comps:
        company = get_company(user_comps[0]["id"])

    if not company:
        first_comp = db._execute_query(
            "SELECT id FROM public.invoice_companies ORDER BY id LIMIT 1",
            fetch=True, commit=False
        )
        if first_comp:
            company = get_company(first_comp["id"])

    if company and request.session.get("active_company_id") != company["id"]:
        request.session["active_company_id"] = company["id"]
        ctx["active_company_id"] = company["id"]
        ctx["active_company"] = company

    return company


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def suppliers_page(
    request: Request,
    company_id: Optional[int] = Query(None),
    view: Optional[str] = Query(None),
    tab: Optional[str] = Query(None),
    book: Optional[str] = Query(None),
    supplier: Optional[str] = Query(None),
    product: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    debt_only: int = Query(0),
    page: int = Query(1),
    per_page: int = Query(100),
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx, company_id if isinstance(company_id, int) else None)
    cid = company["id"] if company else 52

    # Sanitize Query defaults if called directly
    view_val = view if isinstance(view, str) else None
    tab_val = tab if isinstance(tab, str) else None
    book_val = book if isinstance(book, str) else None
    supplier_val = supplier if isinstance(supplier, str) else None
    product_val = product if isinstance(product, str) else None
    month_val = month if isinstance(month, str) else None
    date_from_val = date_from if isinstance(date_from, str) else None
    date_to_val = date_to if isinstance(date_to, str) else None
    q_val = q if isinstance(q, str) else None
    page_val = int(page) if not hasattr(page, 'default') else 1
    per_page_val = int(per_page) if not hasattr(per_page, 'default') else 100
    debt_only_val = int(debt_only) if not hasattr(debt_only, 'default') else 0

    # Map view to tab and book
    # 4 distinct views:
    # 1. purchases_bich: Sổ nhập hàng (bích)
    # 2. debts_bich: Công nợ (bích)
    # 3. purchases_trang: Sổ nhập hàng (trang)
    # 4. debts_trang: Công nợ (trang)
    if view_val:
        current_view = view_val
        if view_val == "purchases_bich":
            active_tab = "purchases"
            active_book = "bich"
        elif view_val == "debts_bich":
            active_tab = "debts"
            active_book = "bich"
        elif view_val == "purchases_trang":
            active_tab = "purchases"
            active_book = "trang_pa"
        elif view_val == "debts_trang":
            active_tab = "debts"
            active_book = "trang_pa"
        else:
            active_tab = "purchases"
            active_book = "bich"
            current_view = "purchases_bich"
    else:
        active_tab = tab_val or "purchases"
        active_book = book_val or "bich"
        if active_tab == "debts" and active_book == "trang_pa":
            current_view = "debts_trang"
        elif active_tab == "debts":
            current_view = "debts_bich"
        elif active_tab == "purchases" and active_book == "trang_pa":
            current_view = "purchases_trang"
        else:
            current_view = "purchases_bich"

    # Fetch purchases
    purchases_data = supplier_service.list_purchases(
        company_id=cid,
        book_type=active_book if active_book != "all" else None,
        supplier_name=supplier_val,
        product_name=product_val,
        month=month_val,
        date_from=date_from_val,
        date_to=date_to_val,
        q=q_val,
        page=page_val,
        per_page=per_page_val,
    )

    # Fetch debts summary (No cards - clean table)
    debts_data = supplier_service.list_supplier_debts(
        company_id=cid,
        book_type=active_book if active_book != "all" else None,
        month=month_val,
        q=q_val or supplier_val,
        debt_only=bool(debt_only_val),
    )

    # Autocomplete suggestions
    supplier_suggestions = supplier_service.get_supplier_suggestions(cid)
    product_suggestions = supplier_service.get_product_suggestions(cid)

    # Counts for 4 main tabs
    count_purchases_bich = supplier_service.list_purchases(company_id=cid, book_type="bich", page=1, per_page=1)["total_rows"]
    count_debts_bich = supplier_service.list_supplier_debts(company_id=cid, book_type="bich")["total_suppliers"]
    count_purchases_trang = supplier_service.list_purchases(company_id=cid, book_type="trang_pa", page=1, per_page=1)["total_rows"]
    count_debts_trang = supplier_service.list_supplier_debts(company_id=cid, book_type="trang_pa")["total_suppliers"]

    template_data = {
        "request": request,
        **ctx,
        "user": user,
        "current_menu": "suppliers",
        "active_company": company,
        "current_view": current_view,
        "active_tab": active_tab,
        "current_book": active_book,
        "supplier_filter": supplier_val or "",
        "product_filter": product_val or "",
        "month_filter": month_val or "",
        "date_from_filter": date_from_val or "",
        "date_to_filter": date_to_val or "",
        "q_filter": q_val or "",
        "debt_only": debt_only_val,
        "page": page_val,
        "purchases": purchases_data["items"],
        "total_rows": purchases_data["total_rows"],
        "total_pages": purchases_data["total_pages"],
        "total_pieces": purchases_data["total_pieces"],
        "total_kg": purchases_data["total_kg"],
        "total_amount": purchases_data["total_amount"],
        "debts": debts_data["items"],
        "debt_totals": {
            "total_suppliers": debts_data["total_suppliers"],
            "total_opening_balance": debts_data.get("total_opening_balance", 0),
            "total_purchased": debts_data["total_purchased"],
            "total_paid": debts_data["total_paid"],
            "total_balance_due": debts_data["total_balance_due"],
        },
        "count_purchases_bich": count_purchases_bich,
        "count_debts_bich": count_debts_bich,
        "count_purchases_trang": count_purchases_trang,
        "count_debts_trang": count_debts_trang,
        "count_all": count_purchases_bich + count_purchases_trang,
        "supplier_suggestions": supplier_suggestions,
        "product_suggestions": product_suggestions,
        "next_stt_bich": supplier_service.get_next_purchase_stt(cid, "bich"),
        "next_stt_trang": supplier_service.get_next_purchase_stt(cid, "trang_pa"),
        "current_next_stt": supplier_service.get_next_purchase_stt(cid, active_book),
    }

    return templates.TemplateResponse("pages/enterprise/suppliers.html", template_data)


@router.get("/api/next-stt")
async def api_next_purchase_stt(
    request: Request,
    book: str = Query("bich"),
    company_id: Optional[int] = Query(None),
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx, company_id)
    cid = company["id"] if company else 52
    next_stt = supplier_service.get_next_purchase_stt(company_id=cid, book_type=book)
    return JSONResponse({"status": "success", "next_stt": next_stt})


@router.get("/api/purchases")
async def api_list_purchases(
    request: Request,
    company_id: Optional[int] = Query(None),
    book: str = Query("all"),
    supplier: Optional[str] = Query(None),
    product: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1),
    per_page: int = Query(100),
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx, company_id)
    cid = company["id"] if company else 52

    data = supplier_service.list_purchases(
        company_id=cid,
        book_type=book if book != "all" else None,
        supplier_name=supplier,
        product_name=product,
        month=month,
        date_from=date_from,
        date_to=date_to,
        q=q,
        page=page,
        per_page=per_page,
    )
    # Convert dates and decimals to serializable
    items = []
    for row in data["items"]:
        item = dict(row)
        for k, v in item.items():
            if isinstance(v, (date, datetime)):
                item[k] = v.isoformat()
            elif isinstance(v, Decimal):
                item[k] = float(v)
        items.append(item)

    data["items"] = items
    return JSONResponse(data)


@router.post("/api/purchases")
async def api_create_purchase(
    request: Request,
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx)
    cid = company["id"] if company else 52

    try:
        body = await request.json()
    except Exception:
        body = dict(await request.form())

    res = supplier_service.create_purchase(
        company_id=cid,
        data=body,
        created_by=user.get("username", "") or user.get("email", ""),
    )
    if isinstance(res, dict) and "items" in res:
        serialized_items = [_serialize_dict(it) for it in res["items"]]
        return JSONResponse({"status": "success", "data": {"items": serialized_items, "count": res.get("count", len(serialized_items))}})
    return JSONResponse({"status": "success", "data": _serialize_dict(res)})


@router.put("/api/purchases/{purchase_id}")
async def api_update_purchase(
    purchase_id: int,
    request: Request,
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx)
    cid = company["id"] if company else 52

    try:
        body = await request.json()
    except Exception:
        body = dict(await request.form())

    res = supplier_service.update_purchase(
        purchase_id=purchase_id,
        company_id=cid,
        data=body,
    )
    if not res:
        raise HTTPException(status_code=404, detail="Không tìm thấy dòng nhập hàng")

    return JSONResponse({"status": "success", "data": _serialize_dict(res)})


@router.delete("/api/purchases/{purchase_id}")
async def api_delete_purchase(
    purchase_id: int,
    request: Request,
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx)
    cid = company["id"] if company else 52

    success = supplier_service.delete_purchase(purchase_id=purchase_id, company_id=cid)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy dòng nhập hàng")

    return JSONResponse({"status": "success", "message": "Đã xóa dòng nhập hàng thành công"})


@router.get("/api/debts")
async def api_list_debts(
    request: Request,
    company_id: Optional[int] = Query(None),
    book: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    debt_only: int = Query(0),
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx, company_id)
    cid = company["id"] if company else 52

    data = supplier_service.list_supplier_debts(
        company_id=cid,
        book_type=book,
        month=month,
        q=q,
        debt_only=bool(debt_only),
    )
    items = [_serialize_dict(row) for row in data["items"]]
    data["items"] = items
    return JSONResponse(data)


@router.post("/api/opening-debt")
async def api_save_opening_debt(
    request: Request,
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx)
    cid = company["id"] if company else 52

    try:
        body = await request.json()
    except Exception:
        body = dict(await request.form())

    supplier_name = body.get("supplier_name")
    opening_balance = body.get("opening_balance", 0)
    book_type = body.get("book_type", "bich")
    opening_date = body.get("opening_date")
    note = body.get("note", "")

    if not supplier_name:
        raise HTTPException(status_code=400, detail="Vui lòng nhập tên nhà cung cấp")

    try:
        res = supplier_service.save_supplier_opening_balance(
            company_id=cid,
            supplier_name=supplier_name,
            opening_balance=opening_balance,
            book_type=book_type,
            opening_date=opening_date,
            note=note,
        )
        return JSONResponse({
            "status": "success",
            "message": "Đã lưu số dư nợ đầu kỳ thành công",
            "data": _serialize_dict(res)
        })
    except Exception as e:
        logger.exception("Failed to save supplier opening debt")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/payments")
async def api_record_payment(
    request: Request,
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx)
    cid = company["id"] if company else 52

    try:
        body = await request.json()
    except Exception:
        body = dict(await request.form())

    supplier_name = body.get("supplier_name")
    amount = body.get("amount")
    payment_date = body.get("payment_date")
    invoice_number = body.get("invoice_number", "")
    payment_method = body.get("payment_method", "ck")
    note = body.get("note", "")
    book_type = body.get("book_type", "bich")

    if not supplier_name or not amount:
        raise HTTPException(status_code=400, detail="Vui lòng nhập tên nhà cung cấp và số tiền")

    res = supplier_service.record_supplier_payment(
        company_id=cid,
        supplier_name=supplier_name,
        amount=amount,
        book_type=book_type,
        payment_date=payment_date,
        invoice_number=invoice_number,
        payment_method=payment_method,
        note=note,
        created_by=user.get("username", "") or user.get("email", ""),
    )
    return JSONResponse({"status": "success", "data": _serialize_dict(res)})


@router.put("/api/payments/{payment_id}")
async def api_update_payment(
    payment_id: int,
    request: Request,
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx)
    cid = company["id"] if company else 52

    try:
        body = await request.json()
    except Exception:
        body = dict(await request.form())

    res = supplier_service.update_supplier_payment(
        payment_id=payment_id,
        company_id=cid,
        data=body,
    )
    if not res:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiếu thanh toán")

    return JSONResponse({"status": "success", "data": _serialize_dict(res)})


@router.delete("/api/payments/{payment_id}")
async def api_delete_payment(
    payment_id: int,
    request: Request,
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx)
    cid = company["id"] if company else 52

    success = supplier_service.delete_supplier_payment(payment_id=payment_id, company_id=cid)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiếu thanh toán")

    return JSONResponse({"status": "success", "message": "Đã xóa phiếu thanh toán"})


@router.get("/api/statement/{supplier_name}")
async def api_get_statement(
    supplier_name: str,
    request: Request,
    company_id: Optional[int] = Query(None),
    book_type: Optional[str] = Query(None),
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx, company_id)
    cid = company["id"] if company else 52

    data = supplier_service.get_supplier_statement(company_id=cid, supplier_name=supplier_name, book_type=book_type)
    data["ledger"] = [_serialize_dict(r) for r in data["ledger"]]
    data["profile"] = _serialize_dict(data["profile"])
    return JSONResponse(data)


@router.post("/api/import")
async def api_import_excel(
    request: Request,
    file: UploadFile = File(...),
    book_type: str = Form("bich"),
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx)
    cid = company["id"] if company else 52

    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        res = supplier_service.import_excel_book(
            file_path=tmp_path,
            company_id=cid,
            default_book=book_type,
            created_by=user.get("username", "") or user.get("email", ""),
        )
        return JSONResponse({"status": "success", "data": res})
    finally:
        import os
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@router.get("/export")
async def export_purchases(
    request: Request,
    company_id: Optional[int] = Query(None),
    book: str = Query("all"),
    supplier: Optional[str] = Query(None),
    product: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    user: dict = Depends(require_menu("suppliers")),
):
    ctx = get_menu_context(request, user)
    company = _get_active_company(request, ctx, company_id)
    cid = company["id"] if company else 52

    data = supplier_service.list_purchases(
        company_id=cid,
        book_type=book if book != "all" else None,
        supplier_name=supplier,
        product_name=product,
        month=month,
        page=1,
        per_page=5000,
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SoNhapHang"

    headers = [
        "STT", "Số HĐ", "Ngày", "Tên Nhà Cung Cấp", "Tên Hàng Hóa",
        "Số Cây/Tấm", "ĐVT", "Số Lượng (Kg)", "Công Thức", "Đơn Giá",
        "Thành Tiền", "Trọng Lượng (Kg/Cây)", "Ghi Chú"
    ]
    ws.append(headers)

    for idx, r in enumerate(data["items"], start=1):
        ws.append([
            idx,
            r.get("invoice_number") or "",
            str(r.get("purchase_date") or ""),
            r.get("supplier_name") or "",
            r.get("product_name") or "",
            float(r["pieces"]) if r.get("pieces") is not None else "",
            r.get("unit") or "Kg",
            float(r["quantity_kg"]) if r.get("quantity_kg") is not None else 0,
            r.get("qty_formula") or "",
            float(r["unit_price"]) if r.get("unit_price") is not None else 0,
            float(r["total_amount"]) if r.get("total_amount") is not None else 0,
            float(r["unit_weight"]) if r.get("unit_weight") is not None else "",
            r.get("note") or "",
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"So_nhap_hang_{company['name'] if company else 'NCC'}_{date.today().strftime('%Y%m%d')}.xlsx"
    quoted_filename = urllib.parse.quote(filename)

    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted_filename}"},
    )


def _serialize_dict(d: dict | None) -> dict:
    if not d:
        return {}
    res = dict(d)
    for k, v in res.items():
        if isinstance(v, (date, datetime)):
            res[k] = v.isoformat()
        elif isinstance(v, Decimal):
            res[k] = float(v)
    return res
