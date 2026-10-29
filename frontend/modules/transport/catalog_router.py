"""Catalog routes for cargo services and goods types."""

from decimal import Decimal
import logging
import urllib.parse
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse, JSONResponse

from frontend.deps import get_menu_context, require_menu, templates
from frontend.modules.transport.catalog import (
    KINDS as CATALOG_KINDS,
    count_items as count_catalog_items,
    list_items_page as list_catalog_items_page,
    save_item as save_catalog_item,
    delete_item as delete_catalog_item,
)
from frontend.modules.invoices.companies import count_companies, list_companies_page
from frontend.pagination import paginate_counts
from backend.services.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transport")
catalog_router = APIRouter()


@router.get("/cargo-trips")
async def redirect_cargo_trips():
    return RedirectResponse(url="/admin/vehicles/cargo", status_code=303)


def _fmt_currency(value) -> str:
    try:
        return f"{int(round(Decimal(str(value or 0)))):,}".replace(",", ".") + " đ"
    except Exception:
        return "0 đ"


def _catalog_back_url(raw: str = "") -> str:
    back = (raw or "").strip()
    if back and back.startswith("/"):
        return back
    return "/admin/vehicles"


def _catalog_redirect(tab: str, back: str = "", message: str = "", error: str = "") -> RedirectResponse:
    target = "/admin/transport/catalog"
    params = {}
    if tab:
        params["tab"] = tab
    if back:
        params["back"] = back
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    query = urllib.parse.urlencode(params)
    url = f"{target}?{query}" if query else target
    return RedirectResponse(url=url, status_code=303)


@catalog_router.get("/catalog")
@router.get("/catalog")
async def cargo_catalog_page(
    request: Request,
    tab: str = Query("service"),
    subtab: str = Query("list"),
    partner_type: str = Query("all"),
    role_filter: str = Query("all"),
    back: str = Query("/admin/vehicles"),
    page: int = Query(1, ge=1),
    q: str = Query("", max_length=200),
    debt: bool = Query(False),
    message: str = Query(""),
    error: str = Query(""),
    user: dict = Depends(require_menu("vehicles")),
):
    from frontend.modules.transport.partner_catalog_service import (
        list_partners_sheet1,
        get_ledger_131_sheet2,
        get_ledger_331_sheet3,
    )

    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    tab_str = tab if isinstance(tab, str) else "service"
    current_tab = tab_str if tab_str in (*CATALOG_KINDS, "companies") else "service"
    ctx["tab"] = current_tab
    subtab_str = subtab if isinstance(subtab, str) else "list"
    ctx["subtab"] = subtab_str if subtab_str in ("list", "131", "331") else "list"
    ctx["partner_type"] = partner_type if isinstance(partner_type, str) else "all"
    ctx["role_filter"] = role_filter if isinstance(role_filter, str) else "all"
    back_str = back if isinstance(back, str) else "/admin/vehicles"
    ctx["back_url"] = _catalog_back_url(back_str)
    ctx["message"] = message if isinstance(message, str) else ""
    ctx["error"] = error if isinstance(error, str) else ""
    clean_q = q if isinstance(q, str) else ""
    ctx["q"] = clean_q
    ctx["debt_only"] = bool(debt) if not hasattr(debt, "default") else False
    page_num = page if isinstance(page, int) else 1
    if current_tab == "companies":
        if ctx["subtab"] == "131":
            ctx["ledger_131"] = get_ledger_131_sheet2(year=2026, q=clean_q)
            ctx["companies"] = []
            ctx["page"] = 1
            ctx["total_pages"] = 1
            ctx["total"] = ctx["ledger_131"]["total_count"]
            ctx["page_base_url"] = "/admin/transport/catalog"
        elif ctx["subtab"] == "331":
            ctx["ledger_331"] = get_ledger_331_sheet3(year=2026, q=clean_q)
            ctx["companies"] = []
            ctx["page"] = 1
            ctx["total_pages"] = 1
            ctx["total"] = ctx["ledger_331"]["total_count"]
            ctx["page_base_url"] = "/admin/transport/catalog"
        else:
            sheet1 = list_partners_sheet1(
                q=clean_q,
                partner_type=ctx["partner_type"],
                role_filter=ctx["role_filter"],
                page=page_num,
                per_page=100,
            )
            ctx["companies"] = sheet1["items"]
            ctx["page"] = sheet1["page"]
            ctx["total_pages"] = sheet1["total_pages"]
            ctx["total"] = sheet1["total"]
            ctx["page_base_url"] = f"/admin/transport/catalog?tab=companies&subtab={subtab_str}&partner_type={ctx['partner_type']}&role_filter={ctx['role_filter']}"
    elif current_tab == "service":
        paginated = paginate_counts(
            count_catalog_items("service"), page=page, per_page=100,
            base_url="/admin/transport/catalog",
            extra_params={"tab": "service", "back": back},
        )
        ctx["services"] = list_catalog_items_page(
            "service", limit=100, offset=(paginated["page"] - 1) * 100,
        )
        ctx["page"] = paginated["page"]
        ctx["total_pages"] = paginated["total_pages"]
        ctx["total"] = paginated["total"]
        ctx["page_base_url"] = paginated["page_base_url"]
    else:  # cargo_type
        paginated = paginate_counts(
            count_catalog_items("cargo_type"), page=page, per_page=100,
            base_url="/admin/transport/catalog",
            extra_params={"tab": "cargo_type", "back": back},
        )
        ctx["cargo_types"] = list_catalog_items_page(
            "cargo_type", limit=100, offset=(paginated["page"] - 1) * 100,
        )
        ctx["page"] = paginated["page"]
        ctx["total_pages"] = paginated["total_pages"]
        ctx["total"] = paginated["total"]
        ctx["page_base_url"] = paginated["page_base_url"]

    return templates.TemplateResponse("pages/catalog/index.html", {"request": request, **ctx})


@catalog_router.post("/catalog/items")
@router.post("/catalog/items")
async def create_cargo_catalog_item(
    request: Request,
    kind: str = Form(...), name: str = Form(...), back: str = Form(""),
    _: dict = Depends(require_menu("vehicles")),
):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    if kind not in CATALOG_KINDS:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Danh mục không hợp lệ."}, status_code=400)
        return _catalog_redirect("service", back, error="Danh mục không hợp lệ.")
    try:
        save_catalog_item(kind, name)
    except ValueError as exc:
        if is_ajax:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        return _catalog_redirect(kind, back, error=str(exc))
    if is_ajax:
        return JSONResponse({"ok": True, "message": "Đã thêm mục danh mục."})
    return _catalog_redirect(kind, back, message="Đã thêm mục danh mục.")


@catalog_router.post("/catalog/items/{item_id}/edit")
@router.post("/catalog/items/{item_id}/edit")
async def edit_cargo_catalog_item(
    request: Request,
    item_id: int, kind: str = Form(...), name: str = Form(...), back: str = Form(""),
    _: dict = Depends(require_menu("vehicles")),
):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    if kind not in CATALOG_KINDS:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Danh mục không hợp lệ."}, status_code=400)
        return _catalog_redirect("service", back, error="Danh mục không hợp lệ.")
    try:
        save_catalog_item(kind, name, item_id=item_id)
    except ValueError as exc:
        if is_ajax:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        return _catalog_redirect(kind, back, error=str(exc))
    if is_ajax:
        return JSONResponse({"ok": True, "message": "Đã cập nhật mục danh mục."})
    return _catalog_redirect(kind, back, message="Đã cập nhật mục danh mục.")


@catalog_router.post("/catalog/items/{item_id}/delete")
@router.post("/catalog/items/{item_id}/delete")
async def remove_cargo_catalog_item(
    request: Request,
    item_id: int, kind: str = Form(...), back: str = Form(""),
    _: dict = Depends(require_menu("vehicles")),
):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    if kind not in CATALOG_KINDS:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Danh mục không hợp lệ."}, status_code=400)
        return _catalog_redirect("service", back, error="Danh mục không hợp lệ.")
    if not delete_catalog_item(kind, item_id):
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Không tìm thấy mục danh mục."}, status_code=404)
        return _catalog_redirect(kind, back, error="Không tìm thấy mục danh mục.")
    if is_ajax:
        return JSONResponse({"ok": True, "message": "Đã xóa mục danh mục."})
    return _catalog_redirect(kind, back, message="Đã xóa mục danh mục.")


@router.post("/catalog/companies/{company_id}/toggle-role")
@catalog_router.post("/catalog/companies/{company_id}/toggle-role")
async def toggle_partner_role_endpoint(
    request: Request,
    company_id: int,
    user: dict = Depends(require_menu("vehicles")),
):
    """Toggle KH / NCC status and partner type for Sheet 1."""
    from frontend.modules.transport.partner_catalog_service import update_partner_flags
    form = await request.form()
    is_customer = form.get("is_customer") in ("true", "1", "on", True)
    is_vendor = form.get("is_vendor") in ("true", "1", "on", True)
    partner_type = str(form.get("partner_type") or "").strip() or None
    update_partner_flags(company_id, is_customer, is_vendor, partner_type=partner_type)
    return JSONResponse({"ok": True, "message": "Đã cập nhật vai trò đối tác."})


@router.post("/catalog/companies/{company_id}/update-opening-debts")
@catalog_router.post("/catalog/companies/{company_id}/update-opening-debts")
async def update_opening_debts_endpoint(
    request: Request,
    company_id: int,
    user: dict = Depends(require_menu("vehicles")),
):
    """Update opening balances for 131 or 331."""
    from frontend.modules.transport.partner_catalog_service import update_partner_opening_debts
    form = await request.form()
    account = str(form.get("account") or "131").strip()
    open_dr_raw = str(form.get("open_dr") or "0").replace(".", "").replace(",", "").strip()
    open_cr_raw = str(form.get("open_cr") or "0").replace(".", "").replace(",", "").strip()
    try:
        open_dr = Decimal(open_dr_raw or 0)
        open_cr = Decimal(open_cr_raw or 0)
    except Exception:
        return JSONResponse({"ok": False, "message": "Số tiền không hợp lệ."}, status_code=400)
    update_partner_opening_debts(company_id, account, open_dr, open_cr)
    return JSONResponse({"ok": True, "message": f"Đã lưu số dư đầu kỳ sổ {account}."})


@router.get("/catalog/companies/{company_id}/subledger")
@catalog_router.get("/catalog/companies/{company_id}/subledger")
async def partner_subledger_page(
    request: Request,
    company_id: int,
    account: str = Query("331"),
    year: int = Query(2026),
    user: dict = Depends(require_menu("vehicles")),
):
    """View Sổ chi tiết công nợ (Mẫu số S31-DNN hoặc S32-DNN) for partner."""
    from frontend.modules.transport.partner_catalog_service import get_partner_subledger
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    acc_clean = "131" if str(account).strip().startswith("131") else "331"
    subledger_data = get_partner_subledger(
        partner_id=company_id,
        account=acc_clean,
        year=year,
    )
    if "error" in subledger_data:
        return _catalog_redirect("companies", back="", error=subledger_data["error"])
    ctx["subledger"] = subledger_data
    ctx["account"] = acc_clean
    ctx["year"] = year
    ctx["back_url"] = f"/admin/transport/catalog?tab=companies&subtab={acc_clean}"
    return templates.TemplateResponse("pages/catalog/partner_subledger.html", ctx)


@router.get("/catalog/companies/{company_id}/subledger-data")
@catalog_router.get("/catalog/companies/{company_id}/subledger-data")
async def partner_subledger_data_api(
    request: Request,
    company_id: int,
    account: str = Query("331"),
    year: int = Query(2026),
    user: dict = Depends(require_menu("vehicles")),
):
    """JSON API returning partner subledger data for AJAX modal."""
    from frontend.modules.transport.partner_catalog_service import get_partner_subledger
    ctx = get_menu_context(request, user)
    acc_clean = "131" if str(account).strip().startswith("131") else "331"
    data = get_partner_subledger(
        partner_id=company_id,
        account=acc_clean,
        year=year,
    )
    if "error" in data:
        return JSONResponse({"ok": False, "message": data["error"]}, status_code=404)

    serialized_entries = []
    for e in data.get("entries", []):
        serialized_entries.append({
            "date_str": e.get("date_str") or "",
            "voucher_no": e.get("voucher_no") or "",
            "voucher_date_str": e.get("voucher_date_str") or "",
            "serial": e.get("serial") or "",
            "description": e.get("description") or "",
            "offset_account": e.get("offset_account") or "",
            "ps_dr": float(e.get("ps_dr") or 0),
            "ps_cr": float(e.get("ps_cr") or 0),
            "ps_dr_fmt": e.get("ps_dr_fmt") or "—",
            "ps_cr_fmt": e.get("ps_cr_fmt") or "—",
            "bal_dr_fmt": e.get("bal_dr_fmt") or "—",
            "bal_cr_fmt": e.get("bal_cr_fmt") or "—",
            "doc_type": e.get("doc_type") or "",
            "doc_link": e.get("doc_link"),
        })

    return JSONResponse({
        "ok": True,
        "owner_name": data["owner_company"]["name"],
        "owner_tax": data["owner_company"]["tax_code"],
        "owner_address": data["owner_company"].get("address") or "",
        "partner_name": data["partner"]["name"],
        "partner_tax": data["partner"].get("tax_code") or "",
        "partner_address": data["partner"].get("address") or "",
        "partner_id": company_id,
        "account": data["account"],
        "account_title": data["account_title"],
        "form_code": data["form_code"],
        "circular": data["circular"],
        "year": data["year"],
        "open_dr_fmt": data["open_dr_fmt"],
        "open_cr_fmt": data["open_cr_fmt"],
        "tot_ps_dr_fmt": data["tot_ps_dr_fmt"],
        "tot_ps_cr_fmt": data["tot_ps_cr_fmt"],
        "close_dr_fmt": data["close_dr_fmt"],
        "close_cr_fmt": data["close_cr_fmt"],
        "entries": serialized_entries,
        "total_entries": len(serialized_entries),
        "detail_page_url": f"/admin/transport/catalog/companies/{company_id}/subledger?account={acc_clean}",
    })

