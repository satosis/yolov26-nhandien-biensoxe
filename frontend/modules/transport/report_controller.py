"""Monthly vehicle revenue and expense report based on the transport workbook."""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate, paginate_counts
from frontend.modules.transport.report_export import build_monthly_transport_workbook
from frontend.modules.payroll.vehicle_payroll import monthly_payroll_rows
from frontend.modules.transport.settlements import company_settlements, monthly_trip_settlements


logger = logging.getLogger(__name__)
from frontend.modules.transport.date_utils import month_bounds
import frontend.modules.transport.report_queries as queries
EXPENSE_TYPES = {"fuel": "Đổ dầu", "repair": "Sửa chữa", "toll": "Vé cầu đường", "inspection": "Đăng kiểm", "insurance": "Bảo hiểm", "other": "Chi phí khác"}


def optional_vehicle_id(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    if not value.isdecimal() or int(value) <= 0:
        raise ValueError("Xe không hợp lệ.")
    return int(value)


def expenses_url(month: str, vehicle_id: int | None = None) -> str:
    url = f"/admin/vehicles/expenses?month={quote(month)}"
    return f"{url}&vehicle_id={vehicle_id}" if vehicle_id is not None else url


def vehicle_module_options(site_id: int | None) -> list[dict]:
    return queries.vehicle_module_options_select_plate_whitelist(site_id) or []


def vehicle_module_context(request: Request, user: dict, month: str,
                           vehicle_id: int | None) -> dict | None:
    site_id = request.session.get("active_site_id", 0) or None
    options = vehicle_module_options(site_id)
    if vehicle_id is not None and not any(row["id"] == vehicle_id for row in options):
        return None
    ctx = get_menu_context(request, user)
    ctx.update(current_menu="vehicles", month=month, vehicle_id=vehicle_id,
               vehicle_options=options, site_id=site_id,
               selected_vehicle=next((row for row in options if row["id"] == vehicle_id), None))
    return ctx


def monthly_depreciation(vehicle: dict, month_start: date) -> Decimal | None:
    price = vehicle.get("purchase_price")
    start = vehicle.get("depreciation_start_date")
    years = vehicle.get("depreciation_years")
    if price is None or not start or not years:
        return None
    if isinstance(start, str):
        start = date.fromisoformat(start)
    start_month = start.year * 12 + start.month
    report_month = month_start.year * 12 + month_start.month
    if not start_month <= report_month < start_month + int(years) * 12:
        return Decimal("0")
    return (Decimal(str(price)) / (int(years) * 12)).quantize(Decimal("0.01"))


def monthly_vehicle_rows(start: date, end: date, site_id: int | None = None) -> list[dict]:
    rows = queries.monthly_vehicle_rows_select_cargo_trips(start, end, site_id) or []
    pay_by_vehicle = {p["vehicle_id"]: p for p in monthly_payroll_rows(
        start, end, {row["id"] for row in rows})}
    for row in rows:
        payroll = pay_by_vehicle.get(row["id"])
        if payroll:
            row["payroll_entries"] = 1 if payroll["has_month_entry"] else 0
            row["base_salary"] = payroll["base_salary"]
            row["meal_allowance"] = payroll["meal_allowance"]
            row["bonus_pay"] = payroll["bonus_pay"]
            row["salary_gross"] = payroll["total_salary"] if payroll["salary_source"] == "staff" else (
                payroll["base_salary"] + payroll["meal_allowance"] + payroll["trip_wage"])
            if payroll["salary_source"] == "staff":
                row["trip_wage"] = payroll["trip_wage"]
        # Base salary is due each month, even when the vehicle has no trips or expenses.
        row["has_month_data"] = bool(row["trip_count"] or row["expense_entries"]
                                     or row["payroll_entries"])
        row["depreciation"] = monthly_depreciation(row, start)
        row["fuel_cost"] = row["purchased_fuel"] if row["fuel_entries"] else row["estimated_fuel"]
        row["fuel_is_estimate"] = not bool(row["fuel_entries"])
        # The original summary reads fuel from DODAU, which contains actual purchases only.
        row["template_fuel_cost"] = row["purchased_fuel"] if row["fuel_entries"] else None
        row["template_cost"] = (row["purchased_fuel"] + row["repair"] + row["toll"]
                                + (row["depreciation"] or 0))
        row["template_profit"] = row["revenue"] - row["template_cost"]
        row["template_margin"] = row["template_profit"] / row["revenue"] if row["revenue"] else None
        row["total_cost"] = (row["fuel_cost"] + row["repair"] + row["toll"] + row["other"]
                             + row["salary_gross"] + row["trip_other_cost"] + row["outsourced_freight"]
                             + (row["depreciation"] or 0))
        row["profit"] = row["revenue_net"] - row["total_cost"]
        row["profit_margin"] = row["profit"] / row["revenue_net"] if row["revenue_net"] else None
        row["incomplete"] = (row["fuel_is_estimate"] or row["depreciation"] is None
                             or row["missing_expense_amounts"] > 0 or row["payroll_entries"] == 0)
    return rows


async def monthly_report(
    request: Request,
    month: str | None,
    vehicle_id: str | None,
    report_page: int,
    trip_page: int,
    receivable_page: int,
    cash_page: int,
    user: dict,
):
    month = month or date.today().strftime("%Y-%m")
    try:
        start, end = month_bounds(month)
        vehicle_id = optional_vehicle_id(vehicle_id)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    ctx = get_menu_context(request, user)
    site_id = request.session.get("active_site_id", 0) or None
    vehicle_options = monthly_vehicle_rows(start, end, site_id)
    selected_vehicle = next((row for row in vehicle_options if row["id"] == vehicle_id), None)
    if vehicle_id is not None and selected_vehicle is None:
        return HTMLResponse("Không tìm thấy xe trong phạm vi đang xem.", status_code=404)
    report_rows = [row for row in vehicle_options
                   if row["has_month_data"] and (vehicle_id is None or row["id"] == vehicle_id)]
    report_pagination = paginate(
        report_rows, page=report_page, per_page=100,
        base_url="/admin/vehicles/monthly-report",
        extra_params={"month": month, "vehicle_id": vehicle_id},
    )
    report_pagination["page_param"] = "report_page"
    ctx.update(current_menu="vehicles", month=month, report_rows=report_rows,
               vehicle_options=vehicle_options, vehicle_id=vehicle_id,
               selected_vehicle=selected_vehicle,
               vehicle_section="report")
    all_trip_rows = queries.monthly_report_select_cargo_trips(vehicle_id, start, end) or [] if vehicle_id is not None else []
    trip_pagination = paginate(
        all_trip_rows, page=trip_page, per_page=100,
        base_url="/admin/vehicles/monthly-report",
        extra_params={"month": month, "vehicle_id": vehicle_id},
    )
    trip_pagination["page_param"] = "trip_page"
    settlement_rows = monthly_trip_settlements(start, end, {row["id"] for row in vehicle_options}) if vehicle_id is None else []
    all_receivable_rows = company_settlements(settlement_rows)
    receivable_pagination = paginate(
        all_receivable_rows, page=receivable_page, per_page=100,
        base_url="/admin/vehicles/monthly-report", extra_params={"month": month},
    )
    receivable_pagination["page_param"] = "receivable_page"
    all_cash_rows = [row for row in settlement_rows if row["paid_amount"]]
    running_cash = Decimal("0")
    for cash_row in all_cash_rows:
        running_cash += Decimal(str(cash_row["paid_amount"] or 0))
        cash_row["running_paid_total"] = running_cash
    cash_pagination = paginate(
        all_cash_rows, page=cash_page, per_page=100,
        base_url="/admin/vehicles/monthly-report", extra_params={"month": month},
    )
    cash_pagination["page_param"] = "cash_page"
    ctx.update(
        report_rows=report_pagination["items"], report_pagination=report_pagination,
        trip_rows=trip_pagination["items"], trip_pagination=trip_pagination,
        receivable_rows=receivable_pagination["items"], receivable_pagination=receivable_pagination,
        cash_rows=cash_pagination["items"], cash_pagination=cash_pagination,
        cash_in=sum((row["paid_amount"] for row in all_cash_rows), Decimal("0")),
    )
    ctx["total_debt"] = sum((row["outstanding"] for row in settlement_rows), Decimal("0"))
    return templates.TemplateResponse("pages/vehicles/monthly_report.html", {"request": request, **ctx})


async def vehicle_payroll(request: Request, month: str | None, vehicle_id: str | None, user: dict):
    month = month or date.today().strftime("%Y-%m")
    try:
        start, _ = month_bounds(month)
        selected_id = optional_vehicle_id(vehicle_id)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    if selected_id is None:
        return RedirectResponse("/admin/vehicles", status_code=303)
    ctx = vehicle_module_context(request, user, month, selected_id)
    if ctx is None:
        return HTMLResponse("Không tìm thấy xe trong phạm vi đang xem.", status_code=404)
    ctx.update(vehicle_section="payroll", filter_action="/admin/vehicles/payroll")
    ctx["payroll_rows"] = monthly_payroll_rows(start, month_bounds(month)[1], {selected_id})
    return templates.TemplateResponse("pages/vehicles/payroll.html", {"request": request, **ctx})


async def vehicle_expenses(
    request: Request,
    month: str | None,
    vehicle_id: str | None,
    page: int,
    user: dict,
):
    month = month or date.today().strftime("%Y-%m")
    try:
        start, end = month_bounds(month)
        selected_id = optional_vehicle_id(vehicle_id)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    if selected_id is None:
        return RedirectResponse("/admin/vehicles", status_code=303)
    ctx = vehicle_module_context(request, user, month, selected_id)
    if ctx is None:
        return HTMLResponse("Không tìm thấy xe trong phạm vi đang xem.", status_code=404)
    ctx.update(vehicle_section="expenses", filter_action="/admin/vehicles/expenses",
               expense_types=EXPENSE_TYPES)
    expense_filters = """FROM public.vehicle_expenses e
           JOIN public.plate_whitelist v ON v.id = e.vehicle_id
          WHERE e.expense_date >= %s AND e.expense_date < %s
            AND (%s::int IS NULL OR e.vehicle_id = %s::int)
            AND (%s::int IS NULL OR v.site_ids @> jsonb_build_array(%s::int)
                 OR v.site_ids = '[]'::jsonb OR v.site_ids IS NULL)"""
    expense_params = (start, end, selected_id, selected_id, ctx["site_id"], ctx["site_id"])
    summary_select = """SELECT e.expense_type,
               COUNT(*) AS entries,
               COALESCE(SUM(e.quantity) FILTER (WHERE e.expense_type = 'fuel'), 0) AS liters,
               COALESCE(SUM(e.amount), 0) AS total,
               COALESCE(SUM(e.amount) FILTER (WHERE e.expense_type = 'fuel' AND e.amount IS NOT NULL AND e.quantity > 0), 0) AS fuel_amount_priced,
               COALESCE(SUM(e.quantity) FILTER (WHERE e.expense_type = 'fuel' AND e.amount IS NOT NULL AND e.quantity > 0), 0) AS fuel_liters_priced"""
    month_summary_rows = queries.vehicle_expenses_run_sql(expense_params, summary_select, expense_filters) or []

    year_start = date(start.year, 1, 1)
    year_end = date(start.year + 1, 1, 1)
    year_params = (year_start, year_end, selected_id, selected_id, ctx["site_id"], ctx["site_id"])
    year_summary_rows = queries.vehicle_expenses_run_sql(year_params, summary_select, expense_filters) or []

    month_data = {r["expense_type"]: r for r in month_summary_rows}
    year_data = {r["expense_type"]: r for r in year_summary_rows}

    expense_summary = []
    month_total_entries = 0
    month_total_amount = Decimal(0)
    year_total_entries = 0
    year_total_amount = Decimal(0)

    for etype, elabel in EXPENSE_TYPES.items():
        m = month_data.get(etype) or {}
        y = year_data.get(etype) or {}
        m_entries = int(m.get("entries") or 0)
        m_liters = Decimal(str(m.get("liters") or 0))
        m_total = Decimal(str(m.get("total") or 0))
        y_entries = int(y.get("entries") or 0)
        y_liters = Decimal(str(y.get("liters") or 0))
        y_total = Decimal(str(y.get("total") or 0))

        expense_summary.append({
            "type": etype,
            "label": elabel,
            "month": {"entries": m_entries, "liters": m_liters, "total": m_total},
            "year": {"entries": y_entries, "liters": y_liters, "total": y_total},
        })

        month_total_entries += m_entries
        month_total_amount += m_total
        year_total_entries += y_entries
        year_total_amount += y_total

    summary_total = {
        "month": {"entries": month_total_entries, "total": month_total_amount},
        "year": {"entries": year_total_entries, "total": year_total_amount},
    }

    fuel_m = month_data.get("fuel") or {}
    fuel_y = year_data.get("fuel") or {}
    m_f_amt = Decimal(str(fuel_m.get("fuel_amount_priced") or 0))
    m_f_lit = Decimal(str(fuel_m.get("fuel_liters_priced") or 0))
    y_f_amt = Decimal(str(fuel_y.get("fuel_amount_priced") or 0))
    y_f_lit = Decimal(str(fuel_y.get("fuel_liters_priced") or 0))

    fuel_avg_month = round(m_f_amt / m_f_lit, 2) if m_f_lit > 0 else None
    fuel_avg_year = round(y_f_amt / y_f_lit, 2) if y_f_lit > 0 else None

    ctx["expense_summary"] = expense_summary
    ctx["summary_total"] = summary_total
    ctx["fuel_avg_month"] = fuel_avg_month
    ctx["fuel_avg_year"] = fuel_avg_year

    count_row = queries.vehicle_expenses_select_sql(expense_params, expense_filters) or {}
    total_expenses = int(count_row.get("total") or 0)
    extra_params = {"month": month}
    if vehicle_id is not None:
        extra_params["vehicle_id"] = vehicle_id
    page_data = paginate_counts(
        total_expenses, page=page, per_page=100,
        base_url="/admin/vehicles/expenses", extra_params=extra_params,
    )
    ctx["expense_rows"] = queries.vehicle_expenses_select_sql_2(expense_filters, expense_params, page_data) or []
    ctx["pagination"] = page_data
    ctx["total_expenses"] = total_expenses
    return templates.TemplateResponse("pages/vehicles/expenses.html", {"request": request, **ctx})


async def export_monthly_report(request: Request, month: str, vehicle_id: str | None, _: dict):
    try:
        start, end = month_bounds(month)
        vehicle_id = optional_vehicle_id(vehicle_id)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    site_id = request.session.get("active_site_id", 0) or None
    rows = monthly_vehicle_rows(start, end, site_id)
    if vehicle_id is not None:
        rows = [row for row in rows if row["id"] == vehicle_id]
        if not rows:
            return HTMLResponse("Không tìm thấy xe trong phạm vi đang xem.", status_code=404)
    plate_display = rows[0]["plate_number"] if vehicle_id is not None and rows else ""
    plate_part = re.sub(r"[^A-Za-z0-9.-]", "", plate_display) if plate_display else ""
    visible_vehicle_ids = {row["id"] for row in rows}
    rows = [row for row in rows if row["has_month_data"]]
    content = build_monthly_transport_workbook(
        start, end, rows, include_global=vehicle_id is None,
        visible_vehicle_ids=visible_vehicle_ids)
    if start.year == end.year - 1 and start.month == 1 and end.month == 1:
        period_str = f"Năm {start.year}"
        ascii_period = f"Nam_{start.year}"
    else:
        period_str = f"T{start.month}.{start.year}"
        ascii_period = f"T{start.month}.{start.year}"
    active_cid = request.session.get("active_company_id")
    company_name = ""
    if active_cid:
        from frontend.modules.invoices.companies import get_company
        comp = get_company(int(active_cid))
        if comp and comp.get("name"):
            company_name = comp["name"]
    from frontend.modules.bank_transfer import remove_accents
    name_label = f" {company_name}" if company_name else ""
    ascii_label = f"_{re.sub(r'[^A-Za-z0-9]', '_', remove_accents(company_name))}" if company_name else ""

    sample_name = f"5. Quản lý vận tải{name_label} _{period_str}"
    ascii_name = f"5_Quan_ly_van_tai{ascii_label}_{ascii_period}"
    vehicle_suffix = f" - {plate_part}" if plate_part else ""
    filename_ascii = f"{ascii_name}{vehicle_suffix}.xlsx"
    filename_utf8 = f"{sample_name}{vehicle_suffix}.xlsx"
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename_ascii}"; filename*=UTF-8\'\'{quote(filename_utf8, safe="")}',
                 "Cache-Control": "no-store"},
    )


def _nonnegative_decimal(value: str, label: str, upper_limit: int = 10**14) -> Decimal:
    try:
        number = Decimal(value.strip().replace(",", "."))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"{label} không hợp lệ.") from exc
    if not number.is_finite() or number < 0 or number >= upper_limit:
        raise ValueError(f"{label} không hợp lệ.")
    return number


async def add_vehicle_expense(
    vehicle_id: int,
    expense_date: str,
    expense_type: str,
    amount: str,
    quantity: str,
    unit_price: str,
    vendor: str,
    invoice_number: str,
    note: str,
    return_vehicle_id: str | None,
    _: dict,
):
    try:
        day = date.fromisoformat(expense_date)
        if expense_type not in EXPENSE_TYPES:
            raise ValueError("Loại chi phí không hợp lệ.")
        value = _nonnegative_decimal(amount, "Số tiền")
        liters = _nonnegative_decimal(quantity, "Số lượng", 10**11) if quantity.strip() else None
        price = _nonnegative_decimal(unit_price, "Đơn giá") if unit_price.strip() else None
        selected_vehicle_id = optional_vehicle_id(return_vehicle_id)
        if selected_vehicle_id is not None and selected_vehicle_id != vehicle_id:
            raise ValueError("Xe không hợp lệ.")
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    vehicle = queries.add_vehicle_expense_select_plate_whitelist(vehicle_id)
    if not vehicle:
        return HTMLResponse("Không tìm thấy xe.", status_code=404)
    queries.add_vehicle_expense_insert_vehicle_expenses(vehicle_id, day, expense_type, value, liters, price, vendor, invoice_number, note)
    return RedirectResponse(expenses_url(f"{day:%Y-%m}", selected_vehicle_id), status_code=303)


async def delete_vehicle_expense(expense_id: int, month: str, return_vehicle_id: str | None, _: dict):
    try:
        month_bounds(month)
        selected_vehicle_id = optional_vehicle_id(return_vehicle_id)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    deleted = queries.delete_vehicle_expense_delete_vehicle_expenses(expense_id)
    if not deleted:
        return HTMLResponse("Không tìm thấy khoản chi.", status_code=404)
    return RedirectResponse(expenses_url(month, selected_vehicle_id), status_code=303)


async def update_vehicle_expense_amount(
    expense_id: int,
    amount: str,
    month: str,
    return_vehicle_id: str | None,
    _: dict,
):
    try:
        month_bounds(month)
        value = _nonnegative_decimal(amount, "Số tiền")
        selected_vehicle_id = optional_vehicle_id(return_vehicle_id)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    updated = queries.update_vehicle_expense_amount_update_vehicle_expenses(value, expense_id)
    if not updated:
        return HTMLResponse("Không tìm thấy khoản chi.", status_code=404)
    return RedirectResponse(expenses_url(month, selected_vehicle_id), status_code=303)
