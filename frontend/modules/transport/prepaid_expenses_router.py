"""Router for Prepaid Expenses & CCDC Allocation (TK 242)."""

from __future__ import annotations

from datetime import date, datetime
import logging

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from frontend.deps import get_menu_context, require_menu, templates
from frontend.modules.transport.cash_bank_service import get_company_info
from frontend.modules.transport.prepaid_expense_service import (
    add_prepaid_expense,
    delete_prepaid_expense,
    export_prepaid_expenses_excel,
    get_prepaid_expenses_data,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transport/prepaid-expenses")


def _resolve_period_dates(year: int | None, period: str | None, date_from_str: str | None, date_to_str: str | None) -> tuple[int, str, date, date]:
    today = date.today()
    selected_year = int(year) if year and str(year).isdigit() and 2000 <= int(year) <= 2100 else today.year

    d_from: date | None = None
    d_to: date | None = None

    if date_from_str:
        try:
            d_from = datetime.strptime(date_from_str.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass
    if date_to_str:
        try:
            d_to = datetime.strptime(date_to_str.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass

    p = (period or "").lower().strip()

    if p and p != "custom":
        if p == "today":
            d_from = date(selected_year, 1, 1)
            d_to = min(today, date(selected_year, 12, 31))
            return selected_year, "today", d_from, d_to
        elif p == "q1":
            d_from = date(selected_year, 1, 1)
            d_to = date(selected_year, 3, 31)
            return selected_year, "q1", d_from, d_to
        elif p == "q2":
            d_from = date(selected_year, 4, 1)
            d_to = date(selected_year, 6, 30)
            return selected_year, "q2", d_from, d_to
        elif p == "q3":
            d_from = date(selected_year, 7, 1)
            d_to = date(selected_year, 9, 30)
            return selected_year, "q3", d_from, d_to
        elif p == "q4":
            d_from = date(selected_year, 10, 1)
            d_to = date(selected_year, 12, 31)
            return selected_year, "q4", d_from, d_to
        elif p.startswith("m") and p[1:].isdigit():
            m = int(p[1:])
            if 1 <= m <= 12:
                last_day = 31 if m in (1, 3, 5, 7, 8, 10, 12) else (30 if m in (4, 6, 9, 11) else 28)
                d_from = date(selected_year, m, 1)
                d_to = date(selected_year, m, last_day)
                return selected_year, p, d_from, d_to
        elif p in ("all", "year"):
            d_from = date(selected_year, 1, 1)
            d_to = date(selected_year, 12, 31)
            return selected_year, "all", d_from, d_to

    if d_from and d_to:
        return selected_year, "custom", d_from, d_to

    # default
    d_from = date(selected_year, 1, 1)
    d_to = date(selected_year, 12, 31)
    return selected_year, "all", d_from, d_to


@router.get("", include_in_schema=False)
@router.get("/")
async def prepaid_expenses_page(
    request: Request,
    year: int = Query(None),
    period: str = Query("all"),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    today = date.today()
    selected_year, active_period, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)

    pe_data = get_prepaid_expenses_data(
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
    )

    available_years = [selected_year, selected_year - 1, 2026, 2025, 2024, 2023, 2022, 2021, 2020]
    available_years = sorted(list(set(available_years)), reverse=True)

    ctx.update({
        "current_menu": "prepaid_expenses",
        "transport_active": "prepaid_expenses",
        "pe_data": pe_data,
        "selected_year": selected_year,
        "available_years": available_years,
        "active_period": active_period,
        "date_from": d_from.strftime("%Y-%m-%d"),
        "date_to": d_to.strftime("%Y-%m-%d"),
        "date_from_display": d_from.strftime("%d/%m/%Y"),
        "date_to_display": d_to.strftime("%d/%m/%Y"),
        "today": today,
    })
    return templates.TemplateResponse("pages/transport/prepaid_expenses.html", {"request": request, **ctx})


@router.get("/export")
async def prepaid_expenses_export(
    request: Request,
    year: int = Query(None),
    period: str = Query("all"),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    selected_year, _, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)

    excel_bytes = export_prepaid_expenses_excel(
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
    )
    comp_info = get_company_info()
    safe_name = "".join(c for c in (comp_info.get("name") or "CongTy") if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
    filename = f"Bang_phan_bo_CCDC_242_Nam_{selected_year}_{safe_name}_{d_from.strftime('%Y%m%d')}_{d_to.strftime('%Y%m%d')}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/api/add")
async def api_add_ccdc(
    request: Request,
    item_code: str = Form(""),
    name: str = Form(...),
    unit: str = Form("Chiếc"),
    quantity: float = Form(1),
    start_date: str = Form(...),
    total_months: int = Form(36),
    original_cost: str = Form(...),
    accumulated_prev: str = Form("0"),
    offset_account: str = Form("642"),
    category: str = Form("CCDC"),
    note: str = Form(""),
    user: dict = Depends(require_menu("vehicles")),
):
    cleaned_orig = original_cost.replace(".", "").replace(",", "").replace(" ", "").strip()
    cleaned_acc = accumulated_prev.replace(".", "").replace(",", "").replace(" ", "").strip() or "0"

    ok, msg, ins_id = add_prepaid_expense(
        item_code=item_code,
        name=name,
        unit=unit,
        quantity=quantity,
        start_date=start_date,
        total_months=total_months,
        original_cost=cleaned_orig,
        accumulated_prev=cleaned_acc,
        offset_account=offset_account,
        category=category,
        note=note,
    )
    return JSONResponse({"ok": ok, "message": msg, "id": ins_id})


@router.post("/api/{item_id}/delete")
async def api_delete_ccdc(
    request: Request,
    item_id: int,
    user: dict = Depends(require_menu("vehicles")),
):
    ok, msg = delete_prepaid_expense(item_id=item_id)
    return JSONResponse({"ok": ok, "message": msg})
