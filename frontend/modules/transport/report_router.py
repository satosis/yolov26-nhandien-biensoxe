"""Monthly vehicle revenue and expense report based on the transport workbook."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from backend.services.database import db
from frontend.deps import get_menu_context, require_menu, templates
from frontend.pagination import paginate, paginate_counts
from frontend.modules.transport.report_export import build_monthly_transport_workbook
from frontend.modules.payroll.vehicle_payroll import monthly_payroll_rows
from frontend.modules.transport.settlements import company_settlements, monthly_trip_settlements


router = APIRouter(prefix="/vehicles")
from frontend.modules.transport.date_utils import month_bounds, MONTH_RE
EXPENSE_TYPES = {"fuel": "Đổ dầu", "repair": "Sửa chữa", "toll": "Vé cầu đường", "other": "Chi phí khác"}


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
    return db._execute_query(
        """SELECT id, plate_number FROM public.plate_whitelist
           WHERE list_type = 'white'
             AND (%s::int IS NULL OR site_ids @> jsonb_build_array(%s::int)
                  OR site_ids = '[]'::jsonb OR site_ids IS NULL)
           ORDER BY plate_number""",
        (site_id, site_id), fetchall=True, commit=False,
    ) or []


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
    rows = db._execute_query(
        """WITH trips AS (
             SELECT vehicle_id, COUNT(*) AS trip_count,
                    COALESCE(SUM(freight_revenue), 0) AS revenue_net,
                    COALESCE(SUM(ROUND(freight_revenue * (1 + COALESCE(vat_rate, 0)), 0)), 0) AS revenue,
                    COALESCE(SUM(fuel_cost_total), 0) AS estimated_fuel,
                    COALESCE(SUM(distance_km), 0) AS distance_km,
                    COALESCE(SUM(trip_wage), 0) AS trip_wage,
                    COALESCE(SUM(trip_other_cost), 0) AS trip_other_cost,
                    COALESCE(SUM(outsourced_freight), 0) AS outsourced_freight
               FROM (
                   SELECT vehicle_id, freight_revenue, vat_rate, fuel_cost_total,
                          distance_km, trip_wage, trip_other_cost, outsourced_freight,
                          invoice_date
                   FROM public.cargo_trips
                   UNION ALL
                   SELECT line.vehicle_id, line.amount AS freight_revenue,
                          COALESCE(line.tax_amount / NULLIF(line.amount, 0), invoice.vat_rate) AS vat_rate,
                          line.fuel_cost_total, line.distance_km,
                          NULL::numeric AS trip_wage, NULL::numeric AS trip_other_cost,
                          NULL::numeric AS outsourced_freight, invoice.invoice_date
                   FROM public.bkav_invoice_lines line
                   JOIN public.bkav_invoices invoice ON invoice.invoice_guid = line.invoice_guid
               ) activity
              WHERE invoice_date >= %s AND invoice_date < %s
              GROUP BY vehicle_id
           ), expenses AS (
             SELECT vehicle_id,
                    COUNT(*) AS expense_entries,
                    COALESCE(SUM(amount) FILTER (WHERE expense_type = 'fuel'), 0) AS fuel,
                    COUNT(*) FILTER (WHERE expense_type = 'fuel' AND amount IS NOT NULL) AS fuel_entries,
                    COUNT(*) FILTER (WHERE expense_type = 'repair' AND amount IS NOT NULL) AS repair_entries,
                    COUNT(*) FILTER (WHERE expense_type = 'toll' AND amount IS NOT NULL) AS toll_entries,
                    COUNT(*) FILTER (WHERE amount IS NULL) AS missing_expense_amounts,
                    COALESCE(SUM(amount) FILTER (WHERE expense_type = 'repair'), 0) AS repair,
                    COALESCE(SUM(amount) FILTER (WHERE expense_type = 'toll'), 0) AS toll,
                    COALESCE(SUM(amount) FILTER (WHERE expense_type = 'other'), 0) AS other
               FROM public.vehicle_expenses
              WHERE expense_date >= %s AND expense_date < %s
              GROUP BY vehicle_id
           )
           SELECT v.id, v.plate_number, v.owner_name, v.vehicle_type, v.purchase_price,
                  v.depreciation_start_date, v.depreciation_years,
                  COALESCE(t.trip_count, 0) AS trip_count,
                  COALESCE(t.revenue, 0) AS revenue,
                  COALESCE(t.revenue_net, 0) AS revenue_net,
                  COALESCE(t.estimated_fuel, 0) AS estimated_fuel,
                  COALESCE(t.distance_km, 0) AS distance_km,
                  COALESCE(t.trip_wage, 0) AS trip_wage,
                  COALESCE(t.trip_other_cost, 0) AS trip_other_cost,
                  COALESCE(t.outsourced_freight, 0) AS outsourced_freight,
                  COALESCE(e.fuel, 0) AS purchased_fuel,
                  COALESCE(e.expense_entries, 0) AS expense_entries,
                  COALESCE(e.fuel_entries, 0) AS fuel_entries,
                  COALESCE(e.repair_entries, 0) AS repair_entries,
                  COALESCE(e.toll_entries, 0) AS toll_entries,
                  COALESCE(e.missing_expense_amounts, 0) AS missing_expense_amounts,
                  COALESCE(e.repair, 0) AS repair,
                  COALESCE(e.toll, 0) AS toll,
                  COALESCE(e.other, 0) AS other,
                  0 AS payroll_entries,
                  0::numeric AS base_salary,
                  0::numeric AS meal_allowance,
                  0::numeric AS bonus_pay,
                  0::numeric AS salary_gross
             FROM public.plate_whitelist v
             LEFT JOIN trips t ON t.vehicle_id = v.id
             LEFT JOIN expenses e ON e.vehicle_id = v.id
            WHERE v.list_type = 'white'
              AND (%s::int IS NULL OR v.site_ids @> jsonb_build_array(%s::int)
                   OR v.site_ids = '[]'::jsonb OR v.site_ids IS NULL)
            ORDER BY v.plate_number""",
        (start, end, start, end, site_id, site_id), fetchall=True, commit=False,
    ) or []
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


@router.get("/monthly-report")
async def monthly_report(request: Request, month: str | None = None,
                         vehicle_id: str | None = Query(None),
                         report_page: int = Query(1, ge=1),
                         trip_page: int = Query(1, ge=1),
                         receivable_page: int = Query(1, ge=1),
                         cash_page: int = Query(1, ge=1),
                         user: dict = Depends(require_menu("vehicles"))):
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
    all_trip_rows = db._execute_query(
        """SELECT t.id::text AS id, 'trip' AS record_type, t.invoice_date, t.document_number, t.origin, t.destination,
                  t.cargo_description, t.distance_km, t.freight_revenue, t.vat_rate,
                  ROUND(t.freight_revenue * (1 + COALESCE(t.vat_rate, 0)), 0) AS invoice_total,
                  t.trip_wage, t.trip_other_cost, t.payment_status, t.debt_amount,
                  c.name AS company_name
             FROM public.cargo_trips t
             LEFT JOIN public.invoice_companies c ON c.id = t.company_id
            WHERE t.vehicle_id = %s AND t.invoice_date >= %s AND t.invoice_date < %s
            UNION ALL
            SELECT i.invoice_guid::text AS id, 'invoice' AS record_type, i.invoice_date,
                   lpad(i.invoice_number::text, 8, '0') AS document_number,
                   l.origin, l.destination, l.cargo_description, l.distance_km,
                   l.amount AS freight_revenue, i.vat_rate,
                   l.amount + COALESCE(l.tax_amount, ROUND(l.amount * COALESCE(i.vat_rate, 0), 0)) AS invoice_total,
                   NULL::numeric AS trip_wage, NULL::numeric AS trip_other_cost,
                   i.payment_status, i.debt_amount, c.name AS company_name
              FROM public.bkav_invoice_lines l
              JOIN public.bkav_invoices i ON i.invoice_guid = l.invoice_guid
              LEFT JOIN public.invoice_companies c ON c.id = i.buyer_company_id
             WHERE l.vehicle_id = %s AND i.invoice_date >= %s AND i.invoice_date < %s
            ORDER BY invoice_date, id""",
        (vehicle_id, start, end, vehicle_id, start, end), fetchall=True, commit=False,
    ) or [] if vehicle_id is not None else []
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


@router.get("/payroll")
async def vehicle_payroll(request: Request, month: str | None = None,
                          vehicle_id: str | None = Query(None),
                          user: dict = Depends(require_menu("vehicles"))):
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


@router.get("/debt-trips")
async def vehicle_debt_trips(request: Request,
                           vehicle_id: str | None = Query(None),
                           user: dict = Depends(require_menu("vehicles"))):
    selected_id = optional_vehicle_id(vehicle_id)
    if selected_id:
        return RedirectResponse(f"/admin/vehicles/cargo?vehicle_id={selected_id}&status=debt", status_code=303)
    return RedirectResponse("/admin/vehicles/cargo?status=debt", status_code=303)


@router.get("/expenses")
async def vehicle_expenses(request: Request, month: str | None = None,
                           vehicle_id: str | None = Query(None),
                           page: int = Query(1, ge=1),
                           user: dict = Depends(require_menu("vehicles"))):
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
    count_row = db._execute_query(
        f"SELECT COUNT(*) AS total {expense_filters}", expense_params,
        fetch=True, commit=False,
    ) or {}
    total_expenses = int(count_row.get("total") or 0)
    extra_params = {"month": month}
    if vehicle_id is not None:
        extra_params["vehicle_id"] = vehicle_id
    page_data = paginate_counts(
        total_expenses, page=page, per_page=100,
        base_url="/admin/vehicles/expenses", extra_params=extra_params,
    )
    ctx["expense_rows"] = db._execute_query(
        f"""SELECT e.*, v.plate_number {expense_filters}
            ORDER BY e.expense_date DESC, e.id DESC LIMIT %s OFFSET %s""",
        (*expense_params, page_data["per_page"], (page_data["page"] - 1) * page_data["per_page"]),
        fetchall=True, commit=False,
    ) or []
    ctx["pagination"] = page_data
    ctx["total_expenses"] = total_expenses
    return templates.TemplateResponse("pages/vehicles/expenses.html", {"request": request, **ctx})


@router.get("/monthly-report/export")
async def export_monthly_report(request: Request, month: str,
                                vehicle_id: str | None = Query(None),
                                _: dict = Depends(require_menu("vehicles"))):
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


@router.post("/expenses")
async def add_vehicle_expense(
    vehicle_id: int = Form(...), expense_date: str = Form(...), expense_type: str = Form(...),
    amount: str = Form(...), quantity: str = Form(""), unit_price: str = Form(""),
    vendor: str = Form(""), invoice_number: str = Form(""), note: str = Form(""),
    return_vehicle_id: str | None = Form(None),
    _: dict = Depends(require_menu("vehicles")),
):
    try:
        day = date.fromisoformat(expense_date)
        if expense_type not in EXPENSE_TYPES:
            raise ValueError("Loại chi phí không hợp lệ.")
        value = _nonnegative_decimal(amount, "Số tiền")
        liters = _nonnegative_decimal(quantity, "Số lượng", 10**11) if quantity.strip() else None
        price = _nonnegative_decimal(unit_price, "Đơn giá") if unit_price.strip() else None
        if any(len(x) > 500 for x in (vendor, invoice_number, note)):
            raise ValueError("Nội dung quá dài.")
        selected_vehicle_id = optional_vehicle_id(return_vehicle_id)
        if selected_vehicle_id is not None and selected_vehicle_id != vehicle_id:
            raise ValueError("Xe không hợp lệ.")
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    vehicle = db._execute_query("SELECT id FROM public.plate_whitelist WHERE id = %s", (vehicle_id,),
                                fetch=True, commit=False)
    if not vehicle:
        return HTMLResponse("Không tìm thấy xe.", status_code=404)
    db._execute_query(
        """INSERT INTO public.vehicle_expenses
             (vehicle_id, expense_date, expense_type, amount, quantity, unit_price, vendor, invoice_number, note)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (vehicle_id, day, expense_type, value, liters, price,
         vendor.strip() or None, invoice_number.strip() or None, note.strip() or None),
        commit=True,
    )
    return RedirectResponse(expenses_url(f"{day:%Y-%m}", selected_vehicle_id), status_code=303)


@router.post("/expenses/{expense_id}/delete")
async def delete_vehicle_expense(expense_id: int, month: str = Form(...),
                                 return_vehicle_id: str | None = Form(None),
                                 _: dict = Depends(require_menu("vehicles"))):
    try:
        month_bounds(month)
        selected_vehicle_id = optional_vehicle_id(return_vehicle_id)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    deleted = db._execute_query("DELETE FROM public.vehicle_expenses WHERE id = %s", (expense_id,), commit=True)
    if not deleted:
        return HTMLResponse("Không tìm thấy khoản chi.", status_code=404)
    return RedirectResponse(expenses_url(month, selected_vehicle_id), status_code=303)


@router.post("/expenses/{expense_id}/amount")
async def update_vehicle_expense_amount(expense_id: int, amount: str = Form(...),
                                        month: str = Form(...),
                                        return_vehicle_id: str | None = Form(None),
                                        _: dict = Depends(require_menu("vehicles"))):
    try:
        month_bounds(month)
        value = _nonnegative_decimal(amount, "Số tiền")
        selected_vehicle_id = optional_vehicle_id(return_vehicle_id)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    updated = db._execute_query(
        "UPDATE public.vehicle_expenses SET amount = %s WHERE id = %s",
        (value, expense_id), commit=True,
    )
    if not updated:
        return HTMLResponse("Không tìm thấy khoản chi.", status_code=404)
    return RedirectResponse(expenses_url(month, selected_vehicle_id), status_code=303)
