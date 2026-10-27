"""Quarterly tax declaration and cross-check router."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response

from backend.services.database import db
from frontend.deps import get_menu_context, require_menu, templates
from frontend.modules.invoices.companies import get_company, list_companies
from frontend.modules.transport.tax_report import (
    compute_tax_summary, export_quarterly_tax_excel,
    get_input_tax_invoices, get_output_tax_invoices, get_previous_quarter_deductible,
    quarter_bounds,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transport/tax-report")


def _current_quarter(target_date: date) -> int:
    return (target_date.month - 1) // 3 + 1


def _parse_deductible(val: str | None) -> Decimal | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        if "." in s and s.count(".") == 1 and "," not in s:
            left, right = s.split(".")
            if len(right) <= 2:
                return Decimal(left)
        cleaned = s.replace(".", "").replace(",", "")
        return Decimal(cleaned)
    except Exception:
        return None


@router.get("", include_in_schema=False)
@router.get("/")
async def tax_report_page(
    request: Request,
    year: int = Query(None),
    quarter: int = Query(None),
    company_id: int | None = Query(None),
    deductible_in: str | None = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    today = date.today()
    selected_year = int(year) if year and str(year).isdigit() and 2000 <= int(year) <= 2100 else today.year
    selected_quarter = int(quarter) if quarter is not None and str(quarter).isdigit() and int(quarter) in (1, 2, 3, 4, 0) else _current_quarter(today)

    all_companies = list_companies() or []
    # Chỉ giữ lại công ty Vận Tải Đại Lộ Xanh theo yêu cầu người dùng
    companies = [
        c for c in all_companies
        if c.get("id") == 33 or "ĐẠI LỘ XANH" in (c.get("name") or "").upper()
    ]
    if not companies and all_companies:
        companies = [c for c in all_companies if "ĐẠI LỘ" in (c.get("name") or "").upper()] or all_companies[:1]

    seller_company_id = int(company_id) if company_id is not None and str(company_id).isdigit() else None
    if seller_company_id is None and companies:
        seller_company_id = companies[0]["id"]
    elif seller_company_id is None:
        seller_company_id = 33

    current_company = get_company(seller_company_id) if seller_company_id else None

    start_date, end_date, period_label = quarter_bounds(selected_year, selected_quarter if selected_quarter != 0 else None)

    # Calculate or retrieve [22] Thuế GTGT còn được khấu trừ kỳ trước chuyển sang
    prev_deductible = _parse_deductible(deductible_in)
    if prev_deductible is None:
        prev_deductible = get_previous_quarter_deductible(
            selected_year, selected_quarter if selected_quarter != 0 else None,
            seller_company_id=seller_company_id,
        )

    output_invoices = get_output_tax_invoices(start_date, end_date, seller_company_id=seller_company_id)
    input_invoices = get_input_tax_invoices(start_date, end_date, buyer_company_id=seller_company_id)
    summary = compute_tax_summary(output_invoices, input_invoices, previous_deductible=prev_deductible)

    available_years = [selected_year, selected_year - 1, selected_year - 2, 2026, 2025, 2024]
    available_years = sorted(list(set(available_years)), reverse=True)

    def _fmt_money(val: Decimal | float | int | None) -> str:
        if val is None:
            return "0 ₫"
        n = round(float(val))
        return f"{n:,.0f} ₫".replace(",", ".")

    def _fmt_date(val: date | str | None) -> str:
        if not val:
            return "—"
        if isinstance(val, date):
            return val.strftime("%d/%m/%Y")
        return str(val)

    # Enhance view items with preformatted strings
    for inv in output_invoices:
        inv["subtotal_fmt"] = _fmt_money(inv["subtotal"])
        inv["vat_amount_fmt"] = _fmt_money(inv["vat_amount"])
        inv["gross_fmt"] = _fmt_money(inv["gross"])
        inv["date_fmt"] = _fmt_date(inv["invoice_date"])

    for inv in input_invoices:
        inv["subtotal_fmt"] = _fmt_money(inv["subtotal"])
        inv["vat_amount_fmt"] = _fmt_money(inv["vat_amount"])
        inv["total_fmt"] = _fmt_money(inv["total"])
        inv["date_fmt"] = _fmt_date(inv["invoice_date"])

    ctx.update(
        current_menu="vehicles",
        transport_section="tax_report",
        year=selected_year,
        quarter=selected_quarter,
        period_label=period_label,
        available_years=available_years,
        company_id=seller_company_id,
        current_company=current_company,
        companies=companies,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        output_invoices=output_invoices,
        input_invoices=input_invoices,
        summary=summary,
        total_out_subtotal_fmt=_fmt_money(summary["total_out_subtotal"]),
        taxable_out_subtotal_fmt=_fmt_money(summary["taxable_out_subtotal"]),
        total_out_vat_fmt=_fmt_money(summary["total_out_vat"]),
        total_out_gross_fmt=_fmt_money(summary["total_out_gross"]),
        total_in_subtotal_fmt=_fmt_money(summary["total_in_subtotal"]),
        total_in_kkknt_subtotal_fmt=_fmt_money(summary["in_by_rate"]["kkknt"]["subtotal"]),
        total_in_vat_fmt=_fmt_money(summary["total_in_vat"]),
        total_in_total_fmt=_fmt_money(summary["total_in_total"]),
        vat_carry_in_fmt=_fmt_money(summary["vat_carry_in"]),
        deductible_in_val=int(summary["vat_carry_in"]),
        net_vat_fmt=_fmt_money(summary["net_vat"]),
        vat_payable_fmt=_fmt_money(summary["vat_payable"]),
        vat_deductible_fmt=_fmt_money(summary["vat_deductible"]),
    )

    return templates.TemplateResponse("pages/tax_report/index.html", {"request": request, **ctx})


@router.get("/export")
async def export_tax_report(
    request: Request,
    year: int = Query(None),
    quarter: int = Query(None),
    company_id: int | None = Query(None),
    deductible_in: str | None = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    today = date.today()
    selected_year = int(year) if year and str(year).isdigit() and 2000 <= int(year) <= 2100 else today.year
    selected_quarter = int(quarter) if quarter is not None and str(quarter).isdigit() and int(quarter) in (1, 2, 3, 4, 0) else _current_quarter(today)

    ctx = get_menu_context(request, user)
    seller_company_id = int(company_id) if company_id is not None and str(company_id).isdigit() else (ctx.get("active_company_id") or 33)
    company = get_company(seller_company_id) if seller_company_id else None

    start_date, end_date, period_label = quarter_bounds(selected_year, selected_quarter if selected_quarter != 0 else None)

    prev_deductible = _parse_deductible(deductible_in)
    if prev_deductible is None:
        prev_deductible = get_previous_quarter_deductible(
            selected_year, selected_quarter if selected_quarter != 0 else None,
            seller_company_id=seller_company_id,
        )

    output_invoices = get_output_tax_invoices(start_date, end_date, seller_company_id=seller_company_id)
    input_invoices = get_input_tax_invoices(start_date, end_date, buyer_company_id=seller_company_id)
    summary = compute_tax_summary(output_invoices, input_invoices, previous_deductible=prev_deductible)

    excel_bytes = export_quarterly_tax_excel(period_label, company, output_invoices, input_invoices, summary)

    filename = f"Bang_ke_thue_{selected_year}_Q{selected_quarter}.xlsx" if selected_quarter else f"Bang_ke_thue_{selected_year}_CaNam.xlsx"
    quoted_filename = quote(filename)

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted_filename}",
            "Cache-Control": "no-cache",
        },
    )
