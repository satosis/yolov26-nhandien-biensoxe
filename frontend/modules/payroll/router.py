"""Staff salary setup and monthly payroll."""

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from frontend.deps import get_menu_context, require_menu, templates
from frontend.pagination import paginate
from frontend.modules.payroll.staff_payroll import (
    get_staff_payroll_item,
    get_timesheet_matrix,
    payroll_rows,
    save_month,
    save_profile,
    save_timesheet_matrix,
    staff_members,
    update_staff_job_title,
)
from frontend.modules.payroll.export import build_staff_payroll_workbook
from frontend.modules.payroll.advances import create_staff_salary_advance, get_staff_salary_advance
from frontend.modules.transport.date_utils import month_bounds


router = APIRouter(prefix="/transport/payroll")


def _money(form, key: str) -> Decimal:
    raw = str(form.get(key, "0")).strip().replace(" ", "") or "0"
    # Forms use plain decimal values. Accept grouping dots from pasted Vietnamese amounts.
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif raw.count(".") > 1 or ("." in raw and len(raw.rsplit(".", 1)[1]) == 3):
        raw = raw.replace(".", "")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{key}: số không hợp lệ.") from exc
    if not number.is_finite() or number < 0 or number > Decimal("999999999999"):
        raise ValueError(f"{key}: số phải từ 0 trở lên.")
    return number


def _whole_money(form, key: str) -> Decimal:
    number = _money(form, key)
    if number != number.to_integral_value():
        raise ValueError(f"{key}: số tiền phải là số nguyên đồng.")
    return number


@router.get("")
async def staff_payroll_page(
    request: Request,
    month: str | None = None,
    tab: str = Query("payroll"),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu("vehicles")),
):
    month = month or date.today().strftime("%Y-%m")
    try:
        start, _ = month_bounds(month)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    ctx = get_menu_context(request, user)
    active_cid = ctx.get("active_company_id")
    rows = payroll_rows(start, active_cid)
    paginated = paginate(
        rows,
        page=page,
        per_page=20,
        base_url="/admin/transport/payroll",
        extra_params={"month": month, "tab": tab},
    )
    total_gross = sum((Decimal(r["gross_pay"]) for r in rows), Decimal(0))
    total_net = sum((Decimal(r["net_pay"]) for r in rows), Decimal(0))
    total_days = sum((Decimal(r["work_days"]) for r in rows), Decimal(0))
    total_insurance = sum((Decimal(r["employee_insurance"]) for r in rows), Decimal(0))
    total_advance = sum((Decimal(r["advance_amount"]) for r in rows), Decimal(0))
    total_fixed = sum((Decimal(r["fixed_pay"]) for r in rows), Decimal(0))
    total_trip_wage = sum((Decimal(r["trip_wage"]) for r in rows), Decimal(0))
    timesheet_matrix = get_timesheet_matrix(month, active_cid)
    ctx.update(
        current_menu="payroll",
        transport_section="staff_payroll",
        month=month,
        active_tab=tab,
        staff=staff_members(active_cid),
        rows=paginated["items"],
        timesheet=timesheet_matrix,
        page=paginated["page"],
        total_pages=paginated["total_pages"],
        total=paginated["total"],
        page_base_url=paginated["page_base_url"],
        total_gross=total_gross,
        total_net=total_net,
        total_days=total_days,
        total_insurance=total_insurance,
        total_advance=total_advance,
        total_fixed=total_fixed,
        total_trip_wage=total_trip_wage,
        saved=request.query_params.get("saved") == "1",
        saved_job=request.query_params.get("saved_job") == "1",
    )
    return templates.TemplateResponse("pages/staff_payroll.html", {"request": request, **ctx})


@router.post("/timesheet/save")
async def save_payroll_timesheet(
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    try:
        data = await request.json()
    except Exception:
        data = {}
    month = data.get("month") or date.today().strftime("%Y-%m")
    updates = data.get("updates") or []
    ctx = get_menu_context(request, user)
    active_cid = ctx.get("active_company_id")
    save_timesheet_matrix(month, active_cid, updates)
    return JSONResponse({"ok": True, "message": "Đã lưu bảng chấm công và tự động đồng bộ sang bảng lương."})


@router.post("/timesheet/quick-fill")
async def quick_fill_payroll_timesheet(
    request: Request,
    month: str = Form(...),
    mode: str = Form("weekdays"),
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    active_cid = ctx.get("active_company_id")
    ts = get_timesheet_matrix(month, active_cid)
    updates = []
    days_info = ts.get("days_info", [])
    for row in ts.get("rows", []):
        details = {}
        work_count = 0
        for d in days_info:
            day_num = d["day"]
            is_sun = d["is_sunday"]
            if mode == "weekdays":
                hc = "x" if not is_sun else ""
            elif mode == "26days":
                if not is_sun and work_count < 26:
                    hc = "x"
                    work_count += 1
                else:
                    hc = ""
            else:
                hc = "x"
            details[str(day_num)] = {"hc": hc, "ot": 0}
        updates.append({
            "user_id": row["user_id"],
            "standard_days": 26,
            "daily_details": details,
            "note": row.get("att_note") or "",
        })
    save_timesheet_matrix(month, active_cid, updates)
    return RedirectResponse(f"/admin/transport/payroll?tab=timesheet&month={quote(month)}&saved=1", status_code=303)


@router.get("/export")
async def export_staff_payroll(request: Request, month: str,
                               user: dict = Depends(require_menu("vehicles"))):
    try:
        start, end_date = month_bounds(month)
        ctx = get_menu_context(request, user)
        active_cid = ctx.get("active_company_id") or request.session.get("active_company_id")
        content = build_staff_payroll_workbook(start, payroll_rows(start, active_cid))
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    filename = f"6_Bang_luong_DLX_T{start.month}_{start.year}.xlsx"
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "no-store"},
    )


@router.get("/{staff_id}")
async def staff_payroll_detail_page(request: Request, staff_id: int, month: str | None = None,
                                    user: dict = Depends(require_menu("vehicles"))):
    month = month or date.today().strftime("%Y-%m")
    try:
        start, _ = month_bounds(month)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    ctx = get_menu_context(request, user)
    active_cid = ctx.get("active_company_id")
    members = staff_members(active_cid)
    if staff_id not in {row["id"] for row in members}:
        return HTMLResponse("Không tìm thấy nhân sự thuộc công ty.", status_code=404)
    item = get_staff_payroll_item(staff_id, start, active_cid)
    if not item:
        return HTMLResponse("Không tìm thấy hồ sơ lương nhân sự.", status_code=404)
    
    total_deductions = (
        Decimal(item["employee_insurance"])
        + Decimal(item["advance_amount"])
        + Decimal(item["tax_deduction"])
        + Decimal(item["other_deduction"])
        + Decimal(item["union_fee"])
    )
    default_amount = max(Decimal(item["net_pay"]), Decimal(0)).to_integral_value(rounding=ROUND_DOWN)
    advance_error = request.query_params.get("advance_error", "")[:300]
    request_id = request.query_params.get("advance_request_id", "")
    try:
        request_id = str(UUID(request_id)) if request_id else str(uuid4())
    except ValueError:
        request_id = str(uuid4())
    advance_payment = None
    if request.query_params.get("advance"):
        try:
            advance_payment = get_staff_salary_advance(staff_id, start, request.query_params["advance"], active_cid)
            if not advance_payment:
                advance_error = "Không tìm thấy khoản tạm ứng trong kỳ lương này."
        except ValueError as exc:
            advance_error = str(exc)
    ctx.update(
        current_menu="payroll",
        transport_section="staff_payroll",
        month=month,
        staff_id=staff_id,
        staff=item,
        all_staff=members,
        total_deductions=total_deductions,
        saved=request.query_params.get("saved") == "1",
        saved_job=request.query_params.get("saved_job") == "1",
        advance_request_id=request_id,
        advance_default_amount=default_amount,
        advance_amount_value=request.query_params.get("advance_amount", str(default_amount))[:32],
        advance_error=advance_error,
        advance_payment=advance_payment,
    )
    return templates.TemplateResponse("pages/staff_payroll_detail.html", {"request": request, **ctx})


@router.post("/{staff_id}/advances")
async def advance_staff_salary(request: Request, staff_id: int,
                               user: dict = Depends(require_menu("vehicles"))):
    form = await request.form()
    month = str(form.get("month", ""))
    try:
        start, _ = month_bounds(month)
        if month != start.strftime("%Y-%m"):
            raise ValueError("Chọn một tháng cụ thể để tạm ứng lương.")
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    ctx = get_menu_context(request, user)
    active_cid = ctx.get("active_company_id")
    if staff_id not in {row["id"] for row in staff_members(active_cid)}:
        return HTMLResponse("Không tìm thấy nhân sự thuộc công ty.", status_code=404)
    request_id = str(form.get("request_id", ""))
    try:
        payment = create_staff_salary_advance(
            staff_id, start, _money(form, "amount"), request_id, active_cid, int(user["id"]),
        )
    except ValueError as exc:
        query = urlencode({
            "month": month,
            "advance_error": str(exc),
            "advance_amount": str(form.get("amount", ""))[:32],
            "advance_request_id": request_id[:64],
        })
        return RedirectResponse(f"/admin/transport/payroll/{staff_id}?{query}", status_code=303)
    query = urlencode({"month": month, "advance": str(payment["id"])})
    return RedirectResponse(f"/admin/transport/payroll/{staff_id}?{query}", status_code=303)


@router.post("/{staff_id}")
async def save_staff_payroll(request: Request, staff_id: int,
                             _: dict = Depends(require_menu("vehicles"))):
    form = await request.form()
    month = str(form.get("month", ""))
    try:
        start, _ = month_bounds(month)
        active_cid = request.session.get("active_company_id")
        if staff_id not in {row["id"] for row in staff_members(active_cid)}:
            return HTMLResponse("Không tìm thấy nhân sự thuộc công ty.", status_code=404)
        mode = str(form.get("mode", "month"))
        if mode == "job_title":
            job_title = str(form.get("job_title", "")).strip()[:120]
            department = str(form.get("department", "")).strip()[:120]
            if not job_title:
                raise ValueError("Cần nhập chức vụ.")
            update_staff_job_title(staff_id, job_title, department)
            back_url = str(form.get("back_url", "")).strip()
            if back_url and back_url.startswith("/admin/transport/payroll"):
                separator = "&" if "?" in back_url else "?"
                return RedirectResponse(f"{back_url}{separator}saved_job=1", status_code=303)
            return RedirectResponse(f"/admin/transport/payroll/{staff_id}?month={quote(month)}&saved_job=1", status_code=303)
        elif mode == "profile":
            values = {key: str(form.get(key, "")).strip()[:120] for key in ("job_title", "department")}
            if not values["job_title"]:
                raise ValueError("Cần nhập chức vụ.")
            for key in ("base_salary", "responsibility_allowance", "meal_allowance",
                        "phone_allowance", "insurance_salary"):
                values[key] = _whole_money(form, key)
            save_profile(staff_id, values)
        elif mode == "month":
            keys = ("work_days", "standard_days", "advance_amount", "trip_wage",
                    "bonus_amount", "overtime_pay", "tax_deduction", "other_deduction", "union_fee")
            values = {key: (_money(form, key) if key in ("work_days", "standard_days")
                            else _whole_money(form, key)) for key in keys}
            if values["standard_days"] <= 0 or values["standard_days"] > 31 or values["work_days"] > 31:
                raise ValueError("Số công chuẩn và công thực tế phải trong khoảng 1–31 ngày.")
            values["note"] = str(form.get("note", "")).strip()[:500]
            save_month(staff_id, start, values)
        else:
            raise ValueError("Thao tác không hợp lệ.")
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    return RedirectResponse(f"/admin/transport/payroll/{staff_id}?month={quote(month)}&saved=1", status_code=303)

