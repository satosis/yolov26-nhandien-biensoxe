"""Catalog routes for cargo services and goods types."""

from decimal import Decimal
import logging
import urllib.parse
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse

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
    back: str = Query("/admin/vehicles"),
    page: int = Query(1, ge=1),
    q: str = Query("", max_length=200),
    debt: bool = Query(False),
    message: str = Query(""),
    error: str = Query(""),
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    current_tab = tab if tab in (*CATALOG_KINDS, "companies") else "service"
    ctx["tab"] = current_tab
    ctx["back_url"] = _catalog_back_url(back)
    ctx["message"] = message
    ctx["error"] = error
    ctx["q"] = q
    ctx["debt_only"] = debt
    active_cid = ctx.get("active_company_id")
    if current_tab == "companies":
        paginated = paginate_counts(
            count_companies(q, debt_only=debt, seller_company_id=active_cid), page=page, per_page=20,
            base_url="/admin/transport/catalog",
            extra_params={"tab": "companies", "back": back, "q": q.strip(), "debt": "true" if debt else None},
        )
        companies = list_companies_page(
            q, limit=20, offset=(paginated["page"] - 1) * 20,
            debt_only=debt, seller_company_id=active_cid,
        )
        company_ids = [company["id"] for company in companies]
        trip_counts = db._execute_query(
            """SELECT company_id, COUNT(*) AS cnt FROM public.cargo_trips
               WHERE seller_company_id = %s AND company_id = ANY(%s::integer[])
               GROUP BY company_id""",
            (active_cid, company_ids), fetchall=True, commit=False
        ) or [] if active_cid and company_ids else []
        trip_map = {r["company_id"]: r["cnt"] for r in trip_counts}
        for company in companies:
            company["trip_count"] = trip_map.get(company["id"], 0)
            debt_val = Decimal(str(company.get("current_debt") or 0))
            company["total_debt"] = float(debt_val)
            company["total_debt_fmt"] = _fmt_currency(debt_val)
            company["debt_trip_count"] = 0

        ctx["companies"] = companies
        ctx["page"] = paginated["page"]
        ctx["total_pages"] = paginated["total_pages"]
        ctx["total"] = paginated["total"]
        ctx["page_base_url"] = paginated["page_base_url"]
    elif current_tab == "service":
        paginated = paginate_counts(
            count_catalog_items("service", company_id=active_cid), page=page, per_page=20,
            base_url="/admin/transport/catalog",
            extra_params={"tab": "service", "back": back},
        )
        ctx["services"] = list_catalog_items_page(
            "service", company_id=active_cid, limit=20, offset=(paginated["page"] - 1) * 20,
        )
        ctx["page"] = paginated["page"]
        ctx["total_pages"] = paginated["total_pages"]
        ctx["total"] = paginated["total"]
        ctx["page_base_url"] = paginated["page_base_url"]
    else:  # cargo_type
        paginated = paginate_counts(
            count_catalog_items("cargo_type", company_id=active_cid), page=page, per_page=20,
            base_url="/admin/transport/catalog",
            extra_params={"tab": "cargo_type", "back": back},
        )
        ctx["cargo_types"] = list_catalog_items_page(
            "cargo_type", company_id=active_cid, limit=20, offset=(paginated["page"] - 1) * 20,
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
        save_catalog_item(kind, name, company_id=request.session.get("active_company_id"))
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
        save_catalog_item(kind, name, item_id=item_id, company_id=request.session.get("active_company_id"))
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
