"""Enterprise management router for company profiles, staff operations, debts, and assets."""

from datetime import date, datetime, timedelta
from decimal import Decimal
import logging
import re
from typing import Optional
import unicodedata
import urllib.parse
from fastapi import Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response

from frontend.deps import get_menu_context, require_menu, templates, format_currency
from frontend.formatting.money import format_vnd_rounded as _fmt_currency
from frontend.pagination import paginate_counts
from frontend.modules.invoices.companies import (
    TaxLookupError,
    get_company,
    get_company_bank_accounts,
    list_bank_names,
    list_business_activities,
    refresh_company_from_tax,
    replace_company_bank_accounts,
    update_company,
    validate_company_bank_accounts,
)
from frontend.modules.invoices.companies_controller import (
    _bank_accounts_from_form,
    _remove_unused_company_image,
    _save_company_image,
)
from frontend.modules.invoices.debt_service import (
    adjust_company_debt,
    get_company_debt_logs,
    delete_company_debt_log,
    get_company_debt,
    update_company_debt_log,
)
from frontend.modules.invoices import personal_debt_service
from frontend.modules.enterprise import assets_service
from frontend.modules.enterprise import sales_service
from frontend.modules.enterprise import sales_import
from frontend.modules.enterprise import goods_spec_service
from frontend.context.company_context import get_current_company_id
from frontend.formatting.money import parse_vnd
from frontend.formatting.http_headers import content_disposition
from frontend.modules.enterprise.active_company import get_active_company
import frontend.modules.enterprise.queries as queries

logger = logging.getLogger(__name__)


def _clean_numeric(value) -> Decimal:
    if value is None:
        return Decimal("0")
    cleaned = re.sub(r"[^\d.]", "", str(value).replace(",", "."))
    try:
        return Decimal(cleaned) if cleaned else Decimal("0")
    except Exception:
        return Decimal("0")


def _personal_month_range(value: str | None = None) -> tuple[str, date, date]:
    cleaned = (value or "").strip().lower()
    month_start = None
    if not cleaned:
        cur_y = date.today().year
        return f"{cur_y}-all", date(cur_y, 1, 1), date(cur_y + 1, 1, 1)
    if cleaned in ("all", "all_year", "tat_ca"):
        cur_y = date.today().year
        return "all", date(cur_y, 1, 1), date(cur_y + 1, 1, 1)
    m_year_all = re.match(r"^(\d{4})-(all|00)$", cleaned)
    if m_year_all:
        y = int(m_year_all.group(1))
        return f"{y}-all", date(y, 1, 1), date(y + 1, 1, 1)
    m = re.match(r"^(\d{4})[-/](\d{1,2})$", cleaned)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            try:
                month_start = date(y, mo, 1)
                if mo == 12:
                    month_end = date(y + 1, 1, 1)
                else:
                    month_end = date(y, mo + 1, 1)
                return month_start.strftime("%Y-%m"), month_start, month_end
            except Exception:
                pass
    elif cleaned.rstrip("-").isdigit() and len(cleaned.rstrip("-")) == 4:
        y = int(cleaned.rstrip("-"))
        return f"{y}-all", date(y, 1, 1), date(y + 1, 1, 1)

    if not month_start:
        month_start = date.today().replace(day=1)

    if month_start.month == 12:
        month_end = date(month_start.year + 1, 1, 1)
    else:
        month_end = date(month_start.year, month_start.month + 1, 1)
    return month_start.strftime("%Y-%m"), month_start, month_end


def _parse_opening_balance(val: str) -> Decimal:
    if not val:
        return Decimal("0")
    try:
        return parse_vnd(val)
    except ValueError:
        return Decimal("0")


def _parse_date_optional(val: str) -> date | None:
    if not val:
        return None
    s = str(val).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%y-%m-%d"):
        try:
            d = datetime.strptime(s, fmt).date()
            if d.year < 100:
                d = d.replace(year=2000 + d.year)
            elif d.year < 1900:
                d = d.replace(year=2000 + (d.year % 100))
            return d
        except ValueError:
            pass
    return None


def _personal_row_json(row: dict) -> dict:
    result = dict(row)
    for key in ("opening_balance", "month_due", "month_paid", "current_balance", "amount_due", "amount_paid"):
        if key in result and result[key] is not None:
            result[key] = float(Decimal(str(result[key])))
    for key in ("last_transaction_date", "transaction_date", "created_at", "opening_balance_date"):
        value = result.get(key)
        if value and hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    return result


# ==============================================================================
# 1. THÔNG TIN DOANH NGHIỆP
# ==============================================================================

async def enterprise_page(request: Request, company_id: Optional[int], message: str, error: str, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "enterprise"
    ctx["message"] = message
    ctx["error"] = error

    company = get_active_company(request, ctx, company_id)
    if not company:
        return RedirectResponse("/admin/companies?error=" + urllib.parse.quote("Chưa có thông tin doanh nghiệp."), status_code=303)

    ctx["company"] = company
    ctx["bank_accounts"] = get_company_bank_accounts(company["id"])
    ctx["bank_names"] = list_bank_names()
    ctx["business_activities"] = list_business_activities()
    ctx["users_list"] = queries.enterprise_page_select_users() or []

    staff_count_row = queries.enterprise_page_select_users_2(company) or {}
    ctx["staff_count"] = staff_count_row.get("cnt", 0)

    return templates.TemplateResponse("pages/enterprise/index.html", {"request": request, **ctx})


async def update_enterprise(
    request: Request,
    company_id: int,
    tax_code: str,
    name: str,
    address: str,
    email: str,
    phone: str,
    business_activity: str,
    director_user_id: str,
    signature_file: Optional[UploadFile],
    stamp_file: Optional[UploadFile],
    remove_signature: Optional[str],
    remove_stamp: Optional[str],
    _: dict,
):
    previous = get_company(company_id)
    if not previous:
        return RedirectResponse("/admin/enterprise?error=" + urllib.parse.quote("Không tìm thấy doanh nghiệp."), status_code=303)

    signature_url = stamp_url = None
    try:
        bank_accounts = await _bank_accounts_from_form(request)
        validate_company_bank_accounts(bank_accounts)
        signature_url = await _save_company_image(signature_file, "signature")
        stamp_url = await _save_company_image(stamp_file, "stamp")
        first = bank_accounts[0] if bank_accounts else {}
        update_company(
            company_id, tax_code, name, address, email, phone=phone,
            bank_account=first.get("account_number", ""),
            bank_name=first.get("bank_name", ""),
            director_user_id=director_user_id,
            signature_url=signature_url,
            stamp_url=stamp_url,
            remove_signature=bool(remove_signature),
            remove_stamp=bool(remove_stamp),
            business_activity=business_activity,
        )
        replace_company_bank_accounts(company_id, bank_accounts)
    except ValueError as exc:
        return RedirectResponse(
            f"/admin/enterprise?company_id={company_id}&error={urllib.parse.quote(str(exc))}",
            status_code=303,
        )
    finally:
        _remove_unused_company_image(signature_url)
        _remove_unused_company_image(stamp_url)
        if previous:
            _remove_unused_company_image(previous.get("signature_url"))
            _remove_unused_company_image(previous.get("stamp_url"))

    msg = f"Đã lưu cập nhật thông tin doanh nghiệp '{name}'."
    return RedirectResponse(f"/admin/enterprise?company_id={company_id}&message={urllib.parse.quote(msg)}", status_code=303)


async def refresh_enterprise(company_id: int, _: dict):
    try:
        updated = await refresh_company_from_tax(company_id)
    except (ValueError, TaxLookupError) as exc:
        return RedirectResponse(f"/admin/enterprise?company_id={company_id}&error={urllib.parse.quote(str(exc))}", status_code=303)
    msg = "Đã đồng bộ thông tin doanh nghiệp và giám đốc từ Tổng cục Thuế (MaSoThue)."
    if updated.get("director_user_created"):
        msg += " Đã tạo tài khoản người dùng cho giám đốc."
    return RedirectResponse(f"/admin/enterprise?company_id={company_id}&message={urllib.parse.quote(msg)}", status_code=303)


# ==============================================================================
# 2. CƠ CẤU PHÒNG BAN & ĐỘI XE
# ==============================================================================

async def departments_page(
    request: Request,
    page: int,
    company_id: Optional[int],
    message: str,
    error: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "departments"
    ctx["message"] = message
    ctx["error"] = error

    company = get_active_company(request, ctx, company_id)
    ctx["company"] = company
    cid = company["id"] if company else None

    count_row = queries.departments_page_select_enterprise_departments(cid) or {}
    page_data = paginate_counts(
        int(count_row.get("total") or 0), page=page, per_page=20,
        base_url="/admin/enterprise/departments",
    )

    # Load departments with manager info and member counts
    depts = queries.departments_page_select_staff_salary_profiles(cid, page_data) or []
    ctx["departments"] = depts
    ctx["pagination"] = page_data

    # List of all staff for manager selection
    ctx["staff_users"] = queries.departments_page_select_users() or []

    return templates.TemplateResponse("pages/enterprise/departments.html", {"request": request, **ctx})


async def department_create(
    request: Request,
    name: str,
    code: str,
    manager_user_id: str,
    description: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    cid = ctx.get("active_company_id")
    mgr_id = int(manager_user_id) if manager_user_id and manager_user_id.isdigit() else None

    name = name.strip()
    if not name:
        return RedirectResponse("/admin/enterprise/departments?error=" + urllib.parse.quote("Vui lòng nhập tên phòng ban/đội nhóm."), status_code=303)

    queries.department_create_insert_enterprise_departments(cid, name, mgr_id, code, description)
    return RedirectResponse("/admin/enterprise/departments?message=" + urllib.parse.quote(f"Đã thêm phòng ban '{name}'."), status_code=303)


async def department_edit(
    dept_id: int,
    name: str,
    code: str,
    manager_user_id: str,
    description: str,
    _: dict,
):
    name = name.strip()
    if not name:
        return RedirectResponse("/admin/enterprise/departments?error=" + urllib.parse.quote("Tên phòng ban không được để trống."), status_code=303)
    mgr_id = int(manager_user_id) if manager_user_id and manager_user_id.isdigit() else None

    queries.department_edit_update_enterprise_departments(name, mgr_id, dept_id, code, description)
    return RedirectResponse("/admin/enterprise/departments?message=" + urllib.parse.quote(f"Đã cập nhật phòng ban '{name}'."), status_code=303)


async def department_delete(dept_id: int, _: dict):
    queries.department_delete_delete_enterprise_departments(dept_id)
    return RedirectResponse("/admin/enterprise/departments?message=" + urllib.parse.quote("Đã xóa phòng ban."), status_code=303)


# ==============================================================================
# 3. CHẤM CÔNG & NGHỈ PHÉP
# ==============================================================================

async def attendance_page(
    request: Request,
    month: str = Query(""),
    tab: str = Query("sheet"),
    attendance_page: int = Query(1, ge=1),
    leave_page: int = Query(1, ge=1),
    company_id: Optional[int] = Query(None),
    message: str = Query(""),
    error: str = Query(""),
    user: dict = Depends(require_menu("attendance")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "attendance"
    ctx["active_tab"] = tab
    ctx["message"] = message
    ctx["error"] = error

    company = get_active_company(request, ctx, company_id)
    ctx["company"] = company
    cid = company["id"] if company else None

    # Determine selected month (defaults to current YYYY-MM)
    if not month or not re.match(r"^\d{4}-\d{2}$", month):
        month = datetime.now().strftime("%Y-%m")
    ctx["selected_month"] = month
    month_date = f"{month}-01"

    staff_count = queries.attendance_page_select_users(cid) or {}
    attendance_pagination = paginate_counts(
        int(staff_count.get("total") or 0), page=attendance_page, per_page=20,
        base_url="/admin/enterprise/attendance", extra_params={"month": month, "tab": "sheet"},
    )
    attendance_pagination["page_param"] = "attendance_page"

    # 1. Attendance Sheet: All active staff of this company + attendance record for this month
    staff_rows = queries.attendance_page_select_users_2(month_date, cid, attendance_pagination) or []
    ctx["staff_attendance_rows"] = staff_rows
    ctx["attendance_pagination"] = attendance_pagination

    # 2. Leave Requests
    leave_count = queries.attendance_page_select_staff_leave_requests(cid) or {}
    leave_pagination = paginate_counts(
        int(leave_count.get("total") or 0), page=leave_page, per_page=20,
        base_url="/admin/enterprise/attendance", extra_params={"month": month, "tab": "requests"},
    )
    leave_pagination["page_param"] = "leave_page"
    leave_requests = queries.attendance_page_select_staff_leave_requests_2(cid, leave_pagination) or []
    ctx["leave_requests"] = leave_requests
    ctx["leave_pagination"] = leave_pagination

    ctx["all_staff"] = queries.attendance_page_select_users_3() or []

    return templates.TemplateResponse("pages/enterprise/attendance.html", {"request": request, **ctx})


async def attendance_save_row(
    user_id: int,
    month: str,
    standard_days: float,
    actual_days: float,
    paid_leave_days: float,
    unpaid_leave_days: float,
    overtime_hours: float,
    note: str,
    user: dict,
    request: Request,
):
    ctx = get_menu_context(request, user)
    cid = ctx.get("active_company_id")
    month_date = f"{month}-01" if len(month) == 7 else month

    queries.attendance_save_row_insert_staff_attendance(user_id, cid, month_date, standard_days, actual_days, paid_leave_days, unpaid_leave_days, overtime_hours, note)
    queries.attendance_save_row_insert_staff_payroll_months(user_id, month_date, actual_days, standard_days)
    return JSONResponse({"ok": True, "message": "Đã lưu ngày công thành công."})


async def attendance_quick_fill(month: str, standard_days: float, user: dict, request: Request):
    ctx = get_menu_context(request, user)
    cid = ctx.get("active_company_id")
    month_date = f"{month}-01"

    # Fetch all staff
    staff = queries.attendance_quick_fill_select_users(cid) or []

    for s in staff:
        queries.attendance_quick_fill_insert_staff_attendance(cid, month_date, standard_days, s)

    return RedirectResponse(f"/admin/enterprise/attendance?month={month}&message=" + urllib.parse.quote(f"Đã điền nhanh công chuẩn {standard_days} ngày cho nhân sự tháng {month}."), status_code=303)


async def leave_request_create(
    request: Request,
    user_id: int,
    leave_type: str,
    start_date: str,
    end_date: str,
    total_days: float,
    reason: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    cid = ctx.get("active_company_id")

    queries.leave_request_create_insert_staff_leave_requests(cid, user_id, leave_type, start_date, end_date, total_days, reason)
    return RedirectResponse("/admin/enterprise/attendance?tab=requests&message=" + urllib.parse.quote("Đã tạo đơn xin nghỉ phép."), status_code=303)


async def leave_request_update_status(req_id: int, status: str, user: dict):
    approver_id = user.get("id")
    queries.leave_request_update_status_update_staff_leave_requests(status, approver_id, req_id)
    return RedirectResponse("/admin/enterprise/attendance?tab=requests&message=" + urllib.parse.quote("Đã cập nhật trạng thái đơn nghỉ phép."), status_code=303)


async def leave_request_delete(req_id: int, _: dict):
    queries.leave_request_delete_delete_staff_leave_requests(req_id)
    return RedirectResponse("/admin/enterprise/attendance?tab=requests&message=" + urllib.parse.quote("Đã xóa đơn xin nghỉ."), status_code=303)


# ==============================================================================
# 4. TẠM ỨNG & HOÀN ỨNG CHI PHÍ NỘI BỘ
# ==============================================================================

async def advances_page(
    request: Request,
    page: int,
    status: str,
    month: str,
    company_id: Optional[int],
    message: str,
    error: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "advances"
    ctx["selected_status"] = status
    ctx["message"] = message
    ctx["error"] = error

    company = get_active_company(request, ctx, company_id)
    ctx["company"] = company
    cid = company["id"] if company else None

    # Advances list
    where = "WHERE a.company_id = %s"
    params = [cid]
    if status != "all":
        where += " AND a.status = %s"
        params.append(status)

    totals = queries.advances_page_select_enterprise_internal_advances(params, where) or {}
    page_data = paginate_counts(
        int(totals.get("total") or 0), page=page, per_page=20,
        base_url="/admin/enterprise/advances", extra_params={"status": status, "month": month or None},
    )
    advances = queries.advances_page_select_enterprise_internal_advances_2(where, params, page_data) or []

    # Format numbers and remaining balance
    total_amount = Decimal(str(totals.get("total_amount") or 0))
    total_settled = Decimal(str(totals.get("total_settled") or 0))
    for adv in advances:
        amt = Decimal(str(adv.get("amount") or 0))
        stld = Decimal(str(adv.get("settled_amount") or 0))
        adv["amount_fmt"] = _fmt_currency(amt)
        adv["settled_amount_fmt"] = _fmt_currency(stld)
        adv["remaining_fmt"] = _fmt_currency(max(Decimal("0"), amt - stld))

    ctx["advances"] = advances
    ctx["pagination"] = page_data
    ctx["total_advance_fmt"] = _fmt_currency(total_amount)
    ctx["total_settled_fmt"] = _fmt_currency(total_settled)
    ctx["total_remaining_fmt"] = _fmt_currency(max(Decimal("0"), total_amount - total_settled))

    ctx["all_staff"] = queries.advances_page_select_users() or []

    return templates.TemplateResponse("pages/enterprise/advances.html", {"request": request, **ctx})


async def advance_create(
    request: Request,
    user_id: int,
    advance_type: str,
    amount: str,
    advance_date: str,
    reason: str,
    bank_account: str,
    bank_name: str,
    user: dict,
):
    from frontend.modules.transport.cash_bank_service import parse_flexible_date
    from frontend.formatting.money import parse_vnd

    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")

    def _err(msg: str, status_code: int = 400):
        if is_ajax:
            return JSONResponse({"ok": False, "message": msg}, status_code=status_code)
        return RedirectResponse("/admin/enterprise/advances?error=" + urllib.parse.quote(msg), status_code=303)

    user_row = queries.advance_create_select_users(user_id)
    if not user_row:
        return _err("Không tìm thấy nhân sự.", status_code=404)

    try:
        adv_date = parse_flexible_date(advance_date)
    except ValueError:
        return _err(f"Ngày tạm ứng không hợp lệ: {advance_date}")

    valid_adv_types = {"tiền dầu", "cầu đường", "công tác phí", "tạm ứng lương", "khác", "chi phí", "default", "cash"}
    clean_adv_type = (advance_type or "").strip().lower()
    if clean_adv_type not in valid_adv_types:
        return _err("Loại tạm ứng không hợp lệ.")
    if clean_adv_type in ("default", "cash", "chi phí"):
        clean_adv_type = "khác"

    ctx = get_menu_context(request, user)
    cid = ctx.get("active_company_id")
    try:
        num_amt = parse_vnd(amount)
    except ValueError:
        return _err("Số tiền tạm ứng không hợp lệ.")
    if num_amt <= 0:
        return _err("Số tiền tạm ứng phải lớn hơn 0.")

    queries.advance_create_insert_enterprise_internal_advances(cid, user_id, clean_adv_type, num_amt, adv_date, user, reason, bank_name, bank_account)
    if is_ajax:
        return JSONResponse({"ok": True, "message": "Đã ghi nhận yêu cầu tạm ứng."})
    return RedirectResponse("/admin/enterprise/advances?message=" + urllib.parse.quote("Đã ghi nhận yêu cầu tạm ứng."), status_code=303)


async def advance_settle(adv_id: int, settled_amount: str, settled_date: str, settle_note: str, _: dict):
    num_settled = _clean_numeric(settled_amount)
    queries.advance_settle_update_enterprise_internal_advances(num_settled, settled_date, adv_id)
    return RedirectResponse("/admin/enterprise/advances?message=" + urllib.parse.quote("Đã lưu quyết toán hoàn ứng chi phí."), status_code=303)


async def advance_update_status(adv_id: int, status: str, _: dict):
    queries.advance_update_status_update_enterprise_internal_advances(status, adv_id)
    return RedirectResponse("/admin/enterprise/advances?message=" + urllib.parse.quote("Đã cập nhật trạng thái phiếu tạm ứng."), status_code=303)


async def advance_delete(adv_id: int, _: dict):
    queries.advance_delete_delete_enterprise_internal_advances(adv_id)
    return RedirectResponse("/admin/enterprise/advances?message=" + urllib.parse.quote("Đã xóa phiếu tạm ứng."), status_code=303)


# ==============================================================================
# 5. QUẢN LÝ TÀI SẢN DOANH NGHIỆP
# ==============================================================================

async def assets_page(request: Request, page: int, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "assets"
    # get_menu_context validates this company against the signed-in user's company list.
    company = ctx.get("active_company")
    ctx["company"] = company
    total_assets = assets_service.count_assets(company["id"]) if company else 0
    page_data = paginate_counts(total_assets, page=page, per_page=20, base_url="/admin/enterprise/assets")
    ctx["assets"] = [_asset_row_json(item) for item in assets_service.list_assets(
        company["id"], limit=page_data["per_page"],
        offset=(page_data["page"] - 1) * page_data["per_page"],
    )] if company else []
    ctx.update(asset_page=page_data["page"], asset_total_pages=page_data["total_pages"],
               asset_total=page_data["total"])
    return templates.TemplateResponse("pages/enterprise/assets.html", {"request": request, **ctx})


async def asset_assignment_create(
    request: Request,
    user_id: int,
    asset_type: str,
    vehicle_id: str,
    asset_name: str,
    asset_code: str,
    handover_date: str,
    initial_odo: str,
    condition_note: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    cid = ctx.get("active_company_id")
    v_id = int(vehicle_id) if vehicle_id and vehicle_id.isdigit() else None
    odo = _clean_numeric(initial_odo) if initial_odo else None

    queries.asset_assignment_create_insert_enterprise_asset_assignments(cid, user_id, asset_type, v_id, handover_date, odo, asset_name, user, asset_code, condition_note)
    return RedirectResponse("/admin/enterprise/assets?message=" + urllib.parse.quote("Đã ghi nhận bàn giao tài sản/xe."), status_code=303)


async def asset_assignment_return(
    assign_id: int,
    return_date: str,
    return_odo: str,
    condition_note: str,
    _: dict,
):
    odo = _clean_numeric(return_odo) if return_odo else None
    queries.asset_assignment_return_update_enterprise_asset_assignments(return_date, odo, assign_id, condition_note)
    return RedirectResponse("/admin/enterprise/assets?message=" + urllib.parse.quote("Đã thu hồi tài sản/phương tiện."), status_code=303)


async def asset_assignment_delete(assign_id: int, _: dict):
    queries.asset_assignment_delete_delete_enterprise_asset_assignments(assign_id)
    return RedirectResponse("/admin/enterprise/assets?message=" + urllib.parse.quote("Đã xóa biên bản bàn giao."), status_code=303)


def _asset_company_id(*args, **kwargs) -> int | None:
    return get_current_company_id()


def _asset_row_json(row: dict) -> dict:
    result = dict(row)
    for key, value in result.items():
        if isinstance(value, (date, datetime)):
            result[key] = value.isoformat()
        elif isinstance(value, Decimal):
            result[key] = float(value)
    return result


async def enterprise_assets_api(request: Request, q: str, category: str, status: str, page: int, user: dict):
    company_id = _asset_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    total = assets_service.count_assets(company_id, q=q, category=category, status=status)
    page_data = paginate_counts(total, page=page, per_page=20)
    assets = assets_service.list_assets(
        company_id, q=q, category=category, status=status,
        limit=page_data["per_page"],
        offset=(page_data["page"] - 1) * page_data["per_page"],
    )
    return JSONResponse({"ok": True, "assets": [_asset_row_json(item) for item in assets],
                         "page": page_data["page"], "total_pages": page_data["total_pages"],
                         "total": page_data["total"]})


async def enterprise_asset_create_api(request: Request, user: dict):
    company_id = _asset_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    values, error = assets_service.parse_asset_values(payload)
    if error:
        return JSONResponse({"ok": False, "message": error}, status_code=400)
    row = assets_service.create_asset(company_id, values, user.get("id"))
    return JSONResponse({"ok": True, "message": "Đã thêm tài sản.", "asset": _asset_row_json(row)})


async def enterprise_asset_update_api(asset_id: int, request: Request, user: dict):
    company_id = _asset_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    values, error = assets_service.parse_asset_values(payload)
    if error:
        return JSONResponse({"ok": False, "message": error}, status_code=400)
    row = assets_service.update_asset(company_id, asset_id, values)
    if not row:
        return JSONResponse({"ok": False, "message": "Không tìm thấy tài sản."}, status_code=404)
    return JSONResponse({"ok": True, "message": "Đã cập nhật tài sản.", "asset": _asset_row_json(row)})


async def enterprise_asset_delete_api(asset_id: int, request: Request, user: dict):
    company_id = _asset_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    if not assets_service.delete_asset(company_id, asset_id):
        return JSONResponse({"ok": False, "message": "Không tìm thấy tài sản."}, status_code=404)
    return JSONResponse({"ok": True, "message": "Đã xóa tài sản."})


# ==============================================================================
# 6. QUẢN LÝ CÔNG NỢ DOANH NGHIỆP
# ==============================================================================

async def enterprise_debts_page(
    request: Request,
    page: int,
    log_page: int,
    q: str,
    debt_only: str,
    month: str,
    personal_month: str,
    personal_q: str,
    personal_debt_only: int,
    tab: str,
    due_only: int,
    message: str,
    error: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    owner_company_id = ctx.get("active_company_id")
    raw_debt_filter = str(debt_only if debt_only is not None else "0").strip().lower()
    if not raw_debt_filter:
        raw_debt_filter = "0"
    if raw_debt_filter == "due_7":
        debt_only = 1
        due_only = 1
    else:
        try:
            debt_only = 1 if int(raw_debt_filter) == 1 else 0
        except (TypeError, ValueError):
            debt_only = 0
    ctx["current_menu"] = "debts"
    ctx["active_tab"] = tab.strip()
    ctx["message"] = message
    ctx["error"] = error

    effective_month = (month or personal_month).strip()
    month_value, month_start, month_end = _personal_month_range(effective_month)
    effective_q = (q or personal_q).strip()

    # Common debt_only filter: if passed via debt_only and not personal_debt_only, use it for both
    if "personal_debt_only" not in request.query_params:
        personal_debt_only = debt_only

    today_d = date.today()
    personal_company_id = owner_company_id
    personal_duplicate_count = personal_debt_service.count_duplicate_name_groups(personal_company_id) if personal_company_id else 0
    personal_customers, personal_stats = ([], {})
    if personal_company_id:
        personal_customers, personal_stats = personal_debt_service.list_customers(
            personal_company_id,
            month_start,
            month_end,
            q=effective_q,
            debt_only=personal_debt_only == 1 or due_only == 1,
        )

    pers_due_count = 0
    month_start_str = month_start.strftime("%d/%m/%Y")
    for customer in personal_customers:
        op_bal = Decimal(str(customer.get("opening_balance") or 0))
        if customer.get("last_transaction_date"):
            value = customer["last_transaction_date"]
            customer["last_transaction_date_str"] = value.strftime("%d/%m/%Y") if hasattr(value, "strftime") else str(value)
            customer["is_opening_date"] = False
        else:
            if customer.get("opening_balance_date"):
                ob_d = customer["opening_balance_date"]
                customer["last_transaction_date_str"] = ob_d.strftime("%d/%m/%Y") if hasattr(ob_d, "strftime") else str(ob_d)
                customer["is_opening_date"] = True
            elif op_bal > 0:
                customer["last_transaction_date_str"] = month_start_str
                customer["is_opening_date"] = True
            else:
                customer["last_transaction_date_str"] = "--"
                customer["is_opening_date"] = False
        customer["month_start_str"] = month_start_str

        for key in ("opening_balance", "month_due", "month_paid", "current_balance"):
            customer[key + "_fmt"] = _fmt_currency(customer.get(key))

        cur_bal = Decimal(str(customer.get("current_balance") or 0))
        is_due = False
        overdue_days = 0
        due_reason = ""
        if cur_bal > 0:
            if customer.get("last_transaction_date"):
                last_dt = customer["last_transaction_date"]
                if hasattr(last_dt, "date"):
                    last_dt = last_dt.date()
                days_inactive = (today_d - last_dt).days
                if days_inactive >= 7:
                    is_due = True
                    overdue_days = days_inactive
                    due_reason = f"Đã {days_inactive} ngày chưa phát sinh giao dịch mới"
            else:
                c_created = customer.get("created_at")
                if c_created and hasattr(c_created, "date"):
                    days_c = (today_d - c_created.date()).days
                else:
                    days_c = 0
                days_m = (today_d - month_start).days
                effective_days = max(days_c, days_m, 7)
                if effective_days >= 7:
                    is_due = True
                    overdue_days = effective_days
                    due_reason = f"Số dư nợ đầu kỳ (từ {month_start_str}), đã {effective_days} ngày chưa có thanh toán"

        customer["is_due"] = is_due
        customer["overdue_days"] = overdue_days
        customer["due_reason"] = due_reason
        if is_due:
            pers_due_count += 1

    if due_only == 1:
        personal_customers = [c for c in personal_customers if c.get("is_due")]

    # Totals, balances and activity are all scoped to this company's ledger and selected month.
    debt_summary = queries.enterprise_debts_page_select_company_debt_accounts(owner_company_id, month_start, month_end) or {}

    where_clauses = []
    params = []
    if effective_q:
        term = f"%{effective_q}%"
        where_clauses.append("(c.name ILIKE %s OR c.tax_code ILIKE %s OR c.phone ILIKE %s)")
        params.extend([term, term, term])
    if debt_only == 1:
        where_clauses.append("COALESCE(a.current_debt, 0) > 0")
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    company_count = queries.enterprise_debts_page_select_invoice_companies(where_sql, params, owner_company_id) or {}
    companies_pagination = paginate_counts(
        int(company_count.get("total") or 0), page=page, per_page=100,
        base_url="/admin/enterprise/debts",
        extra_params={"tab": "companies", "q": effective_q, "debt_only": debt_only, "due_only": due_only, "month": month_value},
    )
    company_fetch_limit = (
        int(company_count.get("total") or 0)
        if due_only == 1 else companies_pagination["per_page"]
    )
    company_fetch_offset = (
        0 if due_only == 1
        else (companies_pagination["page"] - 1) * companies_pagination["per_page"]
    )

    companies = queries.enterprise_debts_page_select_company_debt_logs(where_sql, params, company_fetch_limit, company_fetch_offset, owner_company_id) or []

    all_companies = queries.enterprise_debts_page_select_invoice_companies_2(owner_company_id) or []

    log_where = ["l.owner_company_id = %s"]
    log_params = [owner_company_id]
    if month_start and month_end:
        log_where.append("l.recorded_date >= %s AND l.recorded_date < %s")
        log_params.extend([month_start, month_end])
    if effective_q:
        term = f"%{effective_q}%"
        log_where.append("(c.name ILIKE %s OR c.tax_code ILIKE %s OR l.note ILIKE %s OR l.reference_doc ILIKE %s)")
        log_params.extend([term, term, term, term])
    log_where_sql = "WHERE " + " AND ".join(log_where)

    log_count = queries.enterprise_debts_page_select_company_debt_logs_2(log_params, log_where_sql) or {}
    log_pagination = paginate_counts(
        int(log_count.get("total") or 0), page=log_page, per_page=100,
        base_url="/admin/enterprise/debts", extra_params={"tab": "logs", "q": effective_q, "month": month_value},
    )
    log_pagination["page_param"] = "log_page"
    recent_logs = queries.enterprise_debts_page_select_company_debt_logs_3(log_where_sql, log_params, log_pagination) or []

    type_labels = {
        "initial": "Số dư ban đầu",
        "manual_add": "Ghi nợ ngoài",
        "payment": "Thu / Trả nợ",
        "trip_debt": "Nợ chuyến hàng",
        "invoice_debt": "Nợ hóa đơn",
    }
    for log in recent_logs:
        val = Decimal(str(log["amount"]))
        log["amount_val"] = float(val)
        log["amount_fmt"] = format_currency(abs(val))
        log["is_increase"] = val > 0
        log["type_label"] = type_labels.get(log["debt_type"], log["debt_type"])
        if log.get("recorded_date"):
            if hasattr(log["recorded_date"], "strftime"):
                log["recorded_date_str"] = log["recorded_date"].strftime("%d/%m/%Y")
            else:
                log["recorded_date_str"] = str(log["recorded_date"])

    b2b_due_count = 0
    b2b_month_start_str = month_start.strftime("%d/%m/%Y")
    for comp in companies:
        d_val = Decimal(str(comp.get("current_debt") or 0))
        comp["current_debt_val"] = float(d_val)
        comp["current_debt_fmt"] = format_currency(d_val)
        if comp.get("last_log_date"):
            if hasattr(comp["last_log_date"], "strftime"):
                comp["last_log_date_str"] = comp["last_log_date"].strftime("%d/%m/%Y")
            else:
                comp["last_log_date_str"] = str(comp["last_log_date"])
            comp["is_opening_date"] = False
        else:
            if d_val > 0:
                comp["last_log_date_str"] = b2b_month_start_str
                comp["is_opening_date"] = True
            else:
                comp["last_log_date_str"] = "--"
                comp["is_opening_date"] = False

        is_due = False
        overdue_days = 0
        due_reason = ""
        if d_val > 0:
            if comp.get("last_log_date"):
                last_dt = comp["last_log_date"]
                if hasattr(last_dt, "date"):
                    last_dt = last_dt.date()
                days_inactive = (today_d - last_dt).days
                if days_inactive >= 7:
                    is_due = True
                    overdue_days = days_inactive
                    due_reason = f"Đã {days_inactive} ngày chưa phát sinh giao dịch mới"
            else:
                is_due = True
                overdue_days = max((today_d - month_start).days, 7)
                due_reason = f"Số dư nợ đầu kỳ (từ {b2b_month_start_str}), đã {overdue_days} ngày chưa có thanh toán"
        comp["is_due"] = is_due
        comp["overdue_days"] = overdue_days
        comp["due_reason"] = due_reason
        if is_due:
            b2b_due_count += 1

    if due_only == 1:
        companies = [c for c in companies if c.get("is_due")]
        companies_pagination = paginate_counts(
            len(companies), page=page, per_page=100,
            base_url="/admin/enterprise/debts",
            extra_params={"tab": "companies", "q": effective_q, "debt_only": debt_only, "due_only": 1, "month": month_value},
        )
        start = (companies_pagination["page"] - 1) * companies_pagination["per_page"]
        companies = companies[start:start + companies_pagination["per_page"]]

    for c in all_companies:
        cd = Decimal(str(c.get("current_debt") or 0))
        c["current_debt_val"] = float(cd)
        c["current_debt_fmt"] = format_currency(cd)

    b2b_debt = Decimal(str(debt_summary.get("total_debt_all") or 0))
    b2b_debtor_count = int(debt_summary.get("debt_company_count") or 0)
    b2b_total_count = int(debt_summary.get("total_companies") or 0)
    b2b_added = Decimal(str(debt_summary.get("total_debt_added") or 0))
    b2b_paid = Decimal(str(debt_summary.get("total_debt_paid") or 0))

    pers_debt = Decimal(str(personal_stats.get("total_debt") or 0))
    pers_debtor_count = int(personal_stats.get("debtor_count") or 0)
    pers_total_count = int(personal_stats.get("customer_count") or 0)
    pers_added = Decimal(str(personal_stats.get("month_due") or 0))
    pers_paid = Decimal(str(personal_stats.get("month_paid") or 0))

    total_combined_debt = b2b_debt + pers_debt
    total_combined_added = b2b_added + pers_added
    total_combined_paid = b2b_paid + pers_paid
    total_combined_debtors = b2b_debtor_count + pers_debtor_count
    total_combined_entities = b2b_total_count + pers_total_count

    ctx["total_debt_all"] = _fmt_currency(total_combined_debt)
    ctx["total_debt_b2b_fmt"] = _fmt_currency(b2b_debt)
    ctx["total_debt_personal_fmt"] = _fmt_currency(pers_debt)
    ctx["b2b_debt"] = b2b_debt
    ctx["pers_debt"] = pers_debt

    ctx["debt_company_count"] = total_combined_debtors
    ctx["total_companies_count"] = total_combined_entities
    ctx["b2b_debtor_count"] = b2b_debtor_count
    ctx["b2b_total_count"] = b2b_total_count
    ctx["pers_debtor_count"] = pers_debtor_count
    ctx["pers_total_count"] = pers_total_count

    ctx["total_debt_added"] = _fmt_currency(total_combined_added)
    ctx["total_b2b_added_fmt"] = _fmt_currency(b2b_added)
    ctx["total_pers_added_fmt"] = _fmt_currency(pers_added)
    ctx["total_debt_paid"] = _fmt_currency(total_combined_paid)
    ctx["total_b2b_paid_fmt"] = _fmt_currency(b2b_paid)
    ctx["total_pers_paid_fmt"] = _fmt_currency(pers_paid)
    ctx["companies"] = companies
    ctx["companies_pagination"] = companies_pagination
    # A company with no partner (B2B) ledger of its own, e.g. one selling only to individuals, has no "Sổ nợ đối tác" / "Nhật ký giao dịch" tabs.
    ctx["show_company_debts"] = bool(debt_summary.get("total_companies"))
    if not ctx["show_company_debts"]:
        ctx["current_menu"] = "sales"
        if ctx["active_tab"] in ("", "companies", "logs"):
            ctx["active_tab"] = "personal-debts"
    ctx["log_pagination"] = log_pagination
    ctx["all_companies"] = all_companies
    ctx["recent_logs"] = recent_logs
    ctx["today_str"] = date.today().strftime("%Y-%m-%d")
    ctx["personal_customers"] = personal_customers
    ctx["personal_duplicate_count"] = personal_duplicate_count
    ctx["month"] = month_value
    ctx["personal_month"] = month_value
    ctx["q"] = effective_q
    ctx["personal_q"] = effective_q
    ctx["debt_only"] = debt_only
    ctx["personal_debt_only"] = personal_debt_only
    ctx["pers_due_count"] = pers_due_count
    ctx["b2b_due_count"] = b2b_due_count
    ctx["total_due_count"] = pers_due_count + b2b_due_count
    ctx["due_only"] = due_only
    pers_total_opening = sum((Decimal(str(c.get("opening_balance") or 0)) for c in personal_customers), Decimal("0"))
    pers_total_due = sum((Decimal(str(c.get("month_due") or 0)) for c in personal_customers), Decimal("0"))
    pers_total_paid = sum((Decimal(str(c.get("month_paid") or 0)) for c in personal_customers), Decimal("0"))
    pers_total_balance = sum((Decimal(str(c.get("current_balance") or 0)) for c in personal_customers), Decimal("0"))

    b2b_total_current_debt = sum((Decimal(str(c.get("current_debt_val") or 0)) for c in companies), Decimal("0"))

    ctx["pers_total_opening_fmt"] = _fmt_currency(pers_total_opening)
    ctx["pers_total_due_fmt"] = _fmt_currency(pers_total_due)
    ctx["pers_total_paid_fmt"] = _fmt_currency(pers_total_paid)
    ctx["pers_total_balance_fmt"] = _fmt_currency(pers_total_balance)
    ctx["b2b_total_current_debt_fmt"] = _fmt_currency(b2b_total_current_debt)

    ctx["personal_summary"] = {
        "customer_count": int(personal_stats.get("customer_count") or 0),
        "debtor_count": int(personal_stats.get("debtor_count") or 0),
        "total_debt_fmt": _fmt_currency(personal_stats.get("total_debt")),
        "month_due_fmt": _fmt_currency(personal_stats.get("month_due")),
        "month_paid_fmt": _fmt_currency(personal_stats.get("month_paid")),
    }

    return templates.TemplateResponse("pages/enterprise/debts.html", {"request": request, **ctx})


async def enterprise_debt_add(
    request: Request,
    company_id: int,
    amount: str,
    recorded_date: str,
    reference_doc: str,
    note: str,
    user: dict,
):
    cleaned = re.sub(r"[^\d]", "", amount or "")
    if not cleaned:
        return RedirectResponse("/admin/enterprise/debts?error=" + urllib.parse.quote("Số tiền không hợp lệ."), status_code=303)
    val = Decimal(cleaned)
    if val <= 0:
        return RedirectResponse("/admin/enterprise/debts?error=" + urllib.parse.quote("Số tiền nợ phải lớn hơn 0."), status_code=303)

    ok, msg, new_balance = adjust_company_debt(
        company_id=company_id,
        amount=val,
        debt_type="manual_add",
        note=note.strip() or "Ghi nhận nợ ngoài",
        reference_doc=reference_doc.strip(),
        recorded_date=recorded_date or None,
        user_id=user.get("id"),
    )
    status_key = "message" if ok else "error"
    return RedirectResponse(f"/admin/enterprise/debts?{status_key}=" + urllib.parse.quote(msg), status_code=303)


async def enterprise_debt_pay(
    request: Request,
    company_id: int,
    amount: str,
    recorded_date: str,
    reference_doc: str,
    note: str,
    payment_method: str,
    user: dict,
):
    cleaned = re.sub(r"[^\d]", "", amount or "")
    if not cleaned:
        return RedirectResponse("/admin/enterprise/debts?error=" + urllib.parse.quote("Số tiền không hợp lệ."), status_code=303)
    val = Decimal(cleaned)
    if val <= 0:
        return RedirectResponse("/admin/enterprise/debts?error=" + urllib.parse.quote("Số tiền thanh toán phải lớn hơn 0."), status_code=303)

    ok, msg, new_balance = adjust_company_debt(
        company_id=company_id,
        amount=-val,
        debt_type="payment",
        note=note.strip() or "Thu / Trả nợ",
        reference_doc=reference_doc.strip(),
        recorded_date=recorded_date or None,
        user_id=user.get("id"),
    )

    if ok:
        from frontend.modules.transport.cash_bank_service import record_cash_or_bank_entry
        partner_row = queries.enterprise_debt_pay_select_invoice_companies(company_id)
        partner_name = partner_row.get("name") if partner_row else ""
        is_cash = "tiền mặt" in (payment_method or "").lower() or payment_method == "cash"
        b_type = "cash" if is_cash else "bank"
        record_cash_or_bank_entry(
            book_type=b_type,
            voucher_type="receipt",
            amount=val,
            voucher_date=recorded_date or None,
            voucher_no=reference_doc.strip(),
            description=note.strip() or f"Thu tiền công nợ đối tác {partner_name}",
            offset_account="131",
            partner_name=partner_name,
            bank_account_no="" if not is_cash else None,
            bank_name="" if not is_cash else None,
            source_type="company_debt_logs",
            note=f"Thu tiền công nợ {partner_name}",
        )

    status_key = "message" if ok else "error"
    return RedirectResponse(f"/admin/enterprise/debts?{status_key}=" + urllib.parse.quote(msg), status_code=303)


async def enterprise_debt_delete(log_id: int, request: Request, user: dict):
    ok, msg, new_balance = delete_company_debt_log(log_id)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    if is_ajax:
        return JSONResponse({"ok": ok, "message": msg, "new_balance": float(new_balance)})
    status_key = "message" if ok else "error"
    return RedirectResponse(f"/admin/enterprise/debts?{status_key}=" + urllib.parse.quote(msg), status_code=303)


async def enterprise_debt_update(log_id: int, request: Request, user: dict):
    try:
        data = await request.json()
        amount = Decimal(str(data.get("amount") or "0"))
        recorded_date = date.fromisoformat(str(data.get("recorded_date") or ""))
        if not amount.is_finite() or amount <= 0:
            raise ValueError
    except (ValueError, TypeError, ArithmeticError):
        return JSONResponse({"ok": False, "message": "Thông tin giao dịch không hợp lệ."}, status_code=400)
    ok, msg, new_balance = update_company_debt_log(
        log_id=log_id,
        amount=amount,
        note=str(data.get("note") or "").strip(),
        reference_doc=str(data.get("reference_doc") or "").strip(),
        recorded_date=recorded_date,
    )
    return JSONResponse({"ok": ok, "message": msg, "new_balance": float(new_balance)}, status_code=200 if ok else 404)


async def enterprise_debt_company_logs(company_id: int, request: Request, user: dict):
    comp = get_company(company_id)
    if not comp:
        return JSONResponse({"ok": False, "message": "Không tìm thấy đơn vị"}, status_code=404)
    logs = get_company_debt_logs(company_id)
    current_debt = get_company_debt(company_id)
    return JSONResponse({
        "ok": True,
        "company": {"id": comp["id"], "name": comp["name"], "tax_code": comp.get("tax_code", "")},
        "current_debt": float(current_debt),
        "current_debt_fmt": _fmt_currency(current_debt),
        "logs": logs,
    })


def _personal_debt_company_id(*args, **kwargs) -> int | None:
    return get_current_company_id()


async def personal_debt_import_preview_api(request: Request, file: UploadFile, user: dict):
    company_id = _personal_debt_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    raw = await file.read(sales_import.MAX_UPLOAD_BYTES + 1)
    try:
        plan = sales_import.parse_upload(file.filename or "", raw)
        summary = sales_service.sales_import_preview(company_id, plan)
    except sales_import.SalesImportError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "summary": summary})


async def personal_debt_import_api(request: Request, file: UploadFile, user: dict):
    company_id = _personal_debt_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    raw = await file.read(sales_import.MAX_UPLOAD_BYTES + 1)
    try:
        plan = sales_import.parse_upload(file.filename or "", raw)
        counts = sales_service.import_sales_workbooks(company_id, plan, user.get("id"))
    except sales_import.SalesImportError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return JSONResponse({
        "ok": True,
        "message": "Đã nhập dữ liệu công nợ khách hàng cá nhân.",
        "counts": counts,
    })


async def personal_debt_customers_api(
    request: Request,
    month: str,
    q: str,
    debt_only: int,
    due_only: int,
    user: dict,
):
    company_id = _personal_debt_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    month_value, month_start, month_end = _personal_month_range(month)
    customers, stats = personal_debt_service.list_customers(
        company_id, month_start, month_end, q=q, debt_only=debt_only == 1 or due_only == 1
    )
    today_d = date.today()
    month_start_str = month_start.strftime("%d/%m/%Y")
    due_count = 0
    for customer in customers:
        op_bal = Decimal(str(customer.get("opening_balance") or 0))
        if customer.get("last_transaction_date"):
            value = customer["last_transaction_date"]
            customer["last_transaction_date_str"] = value.strftime("%d/%m/%Y") if hasattr(value, "strftime") else str(value)
            customer["is_opening_date"] = False
        else:
            if customer.get("opening_balance_date"):
                ob_d = customer["opening_balance_date"]
                customer["last_transaction_date_str"] = ob_d.strftime("%d/%m/%Y") if hasattr(ob_d, "strftime") else str(ob_d)
                customer["is_opening_date"] = True
            elif op_bal > 0:
                customer["last_transaction_date_str"] = month_start_str
                customer["is_opening_date"] = True
            else:
                customer["last_transaction_date_str"] = "--"
                customer["is_opening_date"] = False
        customer["month_start_str"] = month_start_str

        cur_bal = Decimal(str(customer.get("current_balance") or 0))
        is_due = False
        overdue_days = 0
        due_reason = ""
        if cur_bal > 0:
            if customer.get("last_transaction_date"):
                last_dt = customer["last_transaction_date"]
                if hasattr(last_dt, "date"):
                    last_dt = last_dt.date()
                days_inactive = (today_d - last_dt).days
                if days_inactive >= 7:
                    is_due = True
                    overdue_days = days_inactive
                    due_reason = f"Đã {days_inactive} ngày chưa phát sinh giao dịch mới"
            else:
                c_created = customer.get("created_at")
                if c_created and hasattr(c_created, "date"):
                    days_c = (today_d - c_created.date()).days
                else:
                    days_c = 0
                days_m = (today_d - month_start).days
                effective_days = max(days_c, days_m, 7)
                if effective_days >= 7:
                    is_due = True
                    overdue_days = effective_days
                    due_reason = f"Số dư nợ đầu kỳ (từ {month_start_str}), đã {effective_days} ngày chưa có thanh toán"

        customer["is_due"] = is_due
        customer["overdue_days"] = overdue_days
        customer["due_reason"] = due_reason
        if is_due:
            due_count += 1

    if due_only == 1:
        customers = [c for c in customers if c.get("is_due")]

    summary = {key: float(Decimal(str(value or 0))) if key not in ("customer_count", "debtor_count") else int(value or 0)
               for key, value in stats.items()}
    summary["due_count"] = due_count

    return JSONResponse({
        "ok": True,
        "month": month_value,
        "due_count": due_count,
        "customers": [_personal_row_json(row) for row in customers],
        "summary": summary,
    })


async def personal_debt_customers_merge_duplicates(request: Request, user: dict):
    company_id = _personal_debt_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    result = personal_debt_service.merge_duplicate_customers(company_id)
    removed = result.get("removed", 0)
    message = f"Đã gộp {removed} khách trùng tên." if removed > 0 else "Không có khách trùng tên."
    return JSONResponse({"ok": True, "message": message, "removed": removed})


async def personal_debt_customer_create(
    request: Request,
    customer_code: str,
    full_name: str,
    phone: str,
    address: str,
    opening_balance: str,
    opening_balance_date: str,
    user: dict,
):
    company_id = _personal_debt_company_id(request, user)
    code, name = (customer_code or "").strip(), full_name.strip()
    if not company_id or not name:
        return JSONResponse({"ok": False, "message": "Vui lòng nhập tên khách hàng."}, status_code=400)
    if not code:
        s = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8')
        s = re.sub(r'[^a-zA-Z0-9]+', '', s).lower()
        base_code = s[:30] if s else "kh"
        code = base_code
        idx = 1
        while True:
            exists = queries.personal_debt_customer_create_select_personal_debt_customers(company_id, code)
            if not exists:
                break
            code = f"{base_code}_{idx}"
            idx += 1
    opening = _parse_opening_balance(opening_balance)
    ob_date = _parse_date_optional(opening_balance_date)
    customer = personal_debt_service.create_customer(
        company_id, code, name, phone.strip(), address.strip(), opening, user.get("id"), ob_date
    )
    message = "Đã nhập vào khách hàng cùng tên." if customer.get("merged") else "Đã thêm khách hàng cá nhân."
    return JSONResponse({"ok": True, "message": message, "customer": _personal_row_json(customer)})


async def personal_debt_customer_edit(
    customer_id: int,
    request: Request,
    customer_code: str,
    full_name: str,
    phone: str,
    address: str,
    opening_balance: str,
    opening_balance_date: str,
    user: dict,
):
    company_id = _personal_debt_company_id(request, user)
    code, name = customer_code.strip(), full_name.strip()
    if not company_id or not code or not name:
        return JSONResponse({"ok": False, "message": "Vui lòng nhập mã và tên khách hàng."}, status_code=400)
    opening = _parse_opening_balance(opening_balance)
    ob_date = _parse_date_optional(opening_balance_date)
    customer = personal_debt_service.update_customer(
        company_id, customer_id, code, name, phone.strip(), address.strip(), opening, ob_date
    )
    if not customer:
        return JSONResponse({"ok": False, "message": "Không tìm thấy khách hàng."}, status_code=404)
    message = "Đã gộp vào khách hàng cùng tên." if customer.get("merged") else "Đã cập nhật khách hàng."
    return JSONResponse({"ok": True, "message": message, "customer": _personal_row_json(customer)})


async def personal_debt_customer_delete(customer_id: int, request: Request, user: dict):
    company_id = _personal_debt_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    message = personal_debt_service.delete_customer(company_id, customer_id)
    if message:
        return JSONResponse({"ok": False, "message": message}, status_code=409)
    return JSONResponse({"ok": True, "message": "Đã xóa khách hàng."})


async def personal_debt_transaction_create(
    request: Request,
    customer_id: int,
    invoice_number: str,
    transaction_date: str,
    amount_due: str,
    amount_paid: str,
    received_from: str,
    note: str,
    user: dict,
):
    company_id = _personal_debt_company_id(request, user)
    due_value = re.sub(r"[^\d]", "", amount_due or "") or "0"
    paid_value = re.sub(r"[^\d]", "", amount_paid or "") or "0"
    try:
        due, paid = Decimal(due_value), Decimal(paid_value)
        record_date = datetime.strptime(transaction_date, "%Y-%m-%d").date() if transaction_date else date.today()
    except Exception:
        return JSONResponse({"ok": False, "message": "Ngày hoặc số tiền không hợp lệ."}, status_code=400)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    if due < 0 or paid < 0 or (due == 0 and paid == 0):
        return JSONResponse({"ok": False, "message": "Nhập số tiền hàng hoặc số tiền thanh toán lớn hơn 0."}, status_code=400)
    log = personal_debt_service.add_transaction(
        company_id, customer_id, invoice_number.strip(), record_date, due, paid,
        received_from.strip(), note.strip(), user.get("id")
    )
    if not log:
        return JSONResponse({"ok": False, "message": "Không tìm thấy khách hàng."}, status_code=404)
    return JSONResponse({"ok": True, "message": "Đã ghi nhận giao dịch công nợ.", "log": _personal_row_json(log)})


async def personal_debt_customer_logs(customer_id: int, request: Request, user: dict):
    company_id = _personal_debt_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    customer, logs = personal_debt_service.get_customer_logs(company_id, customer_id)
    if not customer:
        return JSONResponse({"ok": False, "message": "Không tìm thấy khách hàng."}, status_code=404)
    return JSONResponse({
        "ok": True,
        "customer": _personal_row_json(customer),
        "logs": [_personal_row_json(log) for log in logs],
    })


async def personal_debt_transaction_edit(log_id: int, request: Request, user: dict):
    company_id = _personal_debt_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    try:
        data = await request.json()
        due_value = re.sub(r"[^\d]", "", str(data.get("amount_due") or "0")) or "0"
        paid_value = re.sub(r"[^\d]", "", str(data.get("amount_paid") or "0")) or "0"
        due, paid = Decimal(due_value), Decimal(paid_value)
        record_date = date.fromisoformat(str(data.get("transaction_date") or ""))
        if not due.is_finite() or not paid.is_finite() or due < 0 or paid < 0 or (due == 0 and paid == 0):
            raise ValueError
    except (ValueError, TypeError, ArithmeticError):
        return JSONResponse({"ok": False, "message": "Thông tin giao dịch không hợp lệ."}, status_code=400)
    try:
        updated = personal_debt_service.update_transaction(company_id, log_id, {
            "invoice_number": str(data.get("invoice_number") or "").strip()[:100],
            "transaction_date": record_date,
            "amount_due": due,
            "amount_paid": paid,
            "received_from": str(data.get("received_from") or "").strip()[:255],
            "note": str(data.get("note") or "").strip(),
        })
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=409)
    if not updated:
        return JSONResponse({"ok": False, "message": "Không tìm thấy giao dịch."}, status_code=404)
    return JSONResponse({"ok": True, "message": "Đã cập nhật giao dịch công nợ."})


async def personal_debt_transaction_delete(log_id: int, request: Request, user: dict):
    company_id = _personal_debt_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    deleted = personal_debt_service.delete_transaction(company_id, log_id)
    if not deleted:
        return JSONResponse({"ok": False, "message": "Không tìm thấy giao dịch."}, status_code=404)
    return JSONResponse({"ok": True, "message": "Đã xóa giao dịch."})


# ==============================================================================
# 7. NHẬT KÝ BÁN HÀNG VÀ HÓA ĐƠN PDF
# ==============================================================================

def _sale_row_json(row: dict) -> dict:
    result = dict(row)
    for key in ("total_amount", "paid_on_sale", "paid_total"):
        if result.get(key) is not None:
            result[key] = float(Decimal(str(result[key])))
    value = result.get("sale_date")
    if value and hasattr(value, "isoformat"):
        result["sale_date"] = value.isoformat()
    result["remaining"] = max(0, result.get("total_amount", 0) - result.get("paid_total", 0))
    return result


def _sales_company_id(request: Request | None = None, user: dict | None = None, *args, **kwargs) -> int | None:
    cid = get_current_company_id()
    if cid and cid > 0:
        return cid
    if request:
        from frontend.context.menu_context import get_active_company_id
        return get_active_company_id(request, user)
    return None


async def enterprise_sales_page(request: Request, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "sales"
    company_id = ctx.get("active_company_id")
    customers = []
    if company_id:
        customers, _ = personal_debt_service.list_customers(
            company_id, date.today().replace(day=1), date.today(), q="", debt_only=False
        )
    ctx["sales_customers"] = [
        {"id": row["id"], "customer_code": row["customer_code"],
         "full_name": row["full_name"], "phone": row.get("phone", ""),
         "address": row.get("address", ""),
         "current_balance": int(row.get("current_balance") or 0)}
        for row in customers
    ]
    ctx["today_str"] = date.today().isoformat()
    ctx["next_stt"] = sales_service.get_next_sale_stt(company_id) if company_id else "1"
    ctx["spec_lookup"] = goods_spec_service.lookup_data(company_id) if company_id else {"box": [], "shape": []}
    return templates.TemplateResponse("pages/enterprise/sales.html", {"request": request, **ctx})


async def enterprise_sales_next_stt_api(request: Request, user: dict):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    next_stt = sales_service.get_next_sale_stt(company_id)
    return JSONResponse({"ok": True, "next_stt": next_stt})


async def enterprise_sales_list_api(
    request: Request,
    q: str,
    date_from: str,
    date_to: str,
    status: str,
    page: int,
    per_page: int,
    user: dict,
):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    try:
        start = date.fromisoformat(date_from) if date_from else None
        end = date.fromisoformat(date_to) if date_to else None
    except ValueError:
        return JSONResponse({"ok": False, "message": "Khoảng ngày không hợp lệ."}, status_code=400)
    stats = sales_service.count_sales(company_id, q=q, date_from=start, date_to=end, status=status)
    page_data = paginate_counts(
        int(stats.get("total") or 0), page=page, per_page=per_page,
    )
    rows = sales_service.list_sales(
        company_id, q=q, date_from=start, date_to=end, status=status,
        limit=page_data["per_page"],
        offset=(page_data["page"] - 1) * page_data["per_page"],
    )
    return JSONResponse({
        "ok": True,
        "sales": [_sale_row_json(row) for row in rows],
        "page": page_data["page"],
        "total_pages": page_data["total_pages"],
        "total": page_data["total"],
        "summary": {
            "total_amount": str(stats.get("total_amount") or 0),
            "paid_amount": str(stats.get("paid_amount") or 0),
        },
    })


async def enterprise_sales_items_api(
    request: Request,
    q: str,
    product_name: str,
    customer_id: str,
    date_from: str,
    date_to: str,
    page: int,
    per_page: int,
    user: dict,
):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    try:
        start = date.fromisoformat(date_from) if date_from else None
        end = date.fromisoformat(date_to) if date_to else None
    except ValueError:
        return JSONResponse({"ok": False, "message": "Khoảng ngày không hợp lệ."}, status_code=400)
    
    clean_cust_id = int(customer_id.strip()) if customer_id and customer_id.strip().isdigit() else None
    stats = sales_service.count_sale_items(
        company_id, q=q, product_name=product_name, customer_id=clean_cust_id, date_from=start, date_to=end
    )
    page_data = paginate_counts(
        int(stats.get("total_items") or 0), page=page, per_page=per_page,
    )
    items = sales_service.list_sale_items(
        company_id, q=q, product_name=product_name, customer_id=clean_cust_id, date_from=start, date_to=end,
        limit=page_data["per_page"],
        offset=(page_data["page"] - 1) * page_data["per_page"],
    )
    def _format_item(row):
        sale_dt = row.get("sale_date")
        sale_dt_str = sale_dt.strftime("%d/%m/%Y") if hasattr(sale_dt, "strftime") else str(sale_dt or "")
        return {
            "item_id": row["item_id"],
            "sale_id": row["sale_id"],
            "invoice_number": row.get("display_invoice_number") or "",
            "sale_date": sale_dt_str,
            "sale_date_raw": sale_dt.isoformat() if hasattr(sale_dt, "isoformat") else str(sale_dt or ""),
            "customer_id": row.get("customer_id"),
            "customer_code": row.get("customer_code") or "",
            "customer_name": row.get("customer_name") or "",
            "customer_phone": row.get("customer_phone") or "",
            "carrier": row.get("carrier") or "",
            "sale_note": row.get("sale_note") or "",
            "status": row.get("status") or "delivered",
            "product_name": row.get("product_name") or "",
            "unit": row.get("unit") or "kg",
            "gross_weight": _clean_dec(row.get("gross_weight")),
            "tare_weight": _clean_dec(row.get("tare_weight")),
            "quantity": _clean_dec(row.get("quantity")),
            "unit_price": _clean_dec(row.get("unit_price")),
            "line_total": _clean_dec(row.get("line_total")),
            "gross_formula": row.get("gross_formula") or "",
            "tare_formula": row.get("tare_formula") or "",
            "qty_formula": row.get("qty_formula") or "",
        }
    return JSONResponse({
        "ok": True,
        "items": [_format_item(r) for r in items],
        "page": page_data["page"],
        "total_pages": page_data["total_pages"],
        "total": page_data["total"],
        "summary": {
            "total_items": int(stats.get("total_items") or 0),
            "total_quantity": float(stats.get("total_quantity") or 0),
            "total_amount": float(stats.get("total_amount") or 0),
            "total_products": int(stats.get("total_products") or 0),
        }
    })


async def enterprise_sales_items_export_api(
    request: Request,
    q: str,
    product_name: str,
    customer_id: str,
    date_from: str,
    date_to: str,
    user: dict,
):
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    company_id = _sales_company_id(request, user)
    if not company_id:
        raise HTTPException(status_code=400, detail="Chưa chọn doanh nghiệp.")
    start = date.fromisoformat(date_from) if date_from else None
    end = date.fromisoformat(date_to) if date_to else None
    clean_cust_id = int(customer_id.strip()) if customer_id and customer_id.strip().isdigit() else None

    items = sales_service.list_sale_items(
        company_id, q=q, product_name=product_name, customer_id=clean_cust_id, date_from=start, date_to=end,
        limit=5000, offset=0
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sổ chi tiết bán hàng"

    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")
    thin_border = Border(
        left=Side(style="thin", color="CCCCCC"),
        right=Side(style="thin", color="CCCCCC"),
        top=Side(style="thin", color="CCCCCC"),
        bottom=Side(style="thin", color="CCCCCC"),
    )

    headers = [
        "STT", "Số HĐ", "Ngày bán", "Mã KH", "Tên khách hàng", "Tên hàng hoá",
        "ĐVT", "Cân tổng", "Bì", "Số lượng", "Đơn giá (đ)", "Thành tiền (đ)",
        "Xe / Giao hàng", "Ghi chú"
    ]
    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = align_center

    row_font = Font(name="Arial", size=10)
    num_format_currency = "#,##0"
    num_format_qty = "#,##0.000"

    for idx, r in enumerate(items, 1):
        sale_dt = r.get("sale_date")
        sale_dt_str = sale_dt.strftime("%d/%m/%Y") if hasattr(sale_dt, "strftime") else str(sale_dt or "")
        qty = float(r.get("quantity") or 0)
        price = float(r.get("unit_price") or 0)
        total = float(r.get("line_total") or 0)
        gross = float(r.get("gross_weight")) if r.get("gross_weight") is not None else None
        tare = float(r.get("tare_weight")) if r.get("tare_weight") is not None else None

        row_data = [
            idx,
            r.get("display_invoice_number") or "",
            sale_dt_str,
            r.get("customer_code") or "",
            r.get("customer_name") or "",
            r.get("product_name") or "",
            r.get("unit") or "kg",
            gross,
            tare,
            qty,
            price,
            total,
            r.get("carrier") or "",
            r.get("sale_note") or "",
        ]
        ws.append(row_data)
        current_row = ws.max_row
        for col_idx in range(1, len(row_data) + 1):
            cell = ws.cell(row=current_row, column=col_idx)
            cell.font = row_font
            cell.border = thin_border
            if col_idx in (1, 2, 3, 4, 7):
                cell.alignment = align_center
            elif col_idx in (8, 9, 10):
                cell.alignment = align_right
                cell.number_format = num_format_qty
            elif col_idx in (11, 12):
                cell.alignment = align_right
                cell.number_format = num_format_currency
            else:
                cell.alignment = align_left

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 11)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"So_chi_tiet_ban_hang_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(filename)}
    )


async def enterprise_sales_update_status_api(sale_id: int, request: Request, user: dict):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    try:
        data = await request.json()
        new_status = str(data.get("status") or "").strip()
        inv_num = data.get("invoice_number")
        sales_service.update_sale_status(company_id, sale_id, new_status, invoice_number=inv_num)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "message": "Dữ liệu trạng thái không hợp lệ."}, status_code=400)
    return JSONResponse({"ok": True, "message": "Đã cập nhật trạng thái đơn hàng."})


def _clean_dec(val) -> str:
    if val is None or val == "":
        return ""
    try:
        d = Decimal(str(val))
        if not d.is_finite():
            return ""
        if d == d.to_integral():
            return str(int(d))
        return f"{d.normalize():f}"
    except Exception:
        return str(val)


async def enterprise_sales_get_api(sale_id: int, request: Request, user: dict):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    sale, items = sales_service.get_sale(company_id, sale_id)
    if not sale:
        return JSONResponse({"ok": False, "message": "Không tìm thấy hóa đơn."}, status_code=404)
    sale_date = sale.get("sale_date")
    return JSONResponse({
        "ok": True,
        "sale": {
            "id": sale["id"],
            "invoice_number": sale.get("source_invoice_number") or sale["invoice_number"],
            "customer_id": sale["customer_id"],
            "customer_code": sale.get("customer_code"),
            "customer_name": sale.get("customer_name"),
            "status": sale.get("status") or "delivered",
            "sale_date": sale_date.isoformat() if sale_date else "",
            "customer_phone": sale.get("customer_phone") or "",
            "customer_address": sale.get("customer_address") or "",
            "carrier": sale.get("carrier") or "",
            "note": sale.get("note") or "",
            "paid_on_sale": str(sale.get("paid_on_sale") or 0),
            "payment_source": sale.get("payment_source") or "",
        },
        "items": [
            {
                "warehouse": item.get("warehouse") or "",
                "stock_qty": _clean_dec(item.get("stock_qty")),
                "stock_unit": item.get("stock_unit") or "",
                "product_name": item.get("product_name") or "",
                "unit": item.get("unit") or "",
                "weigh_code": item.get("weigh_code") or "",
                "gross_weight": _clean_dec(item.get("gross_weight")),
                "tare_weight": _clean_dec(item.get("tare_weight")),
                "quantity": _clean_dec(item.get("quantity")),
                "unit_price": _clean_dec(item.get("unit_price")),
                "line_total": _clean_dec(item.get("line_total")),
                "gross_formula": item.get("gross_formula") or "",
                "tare_formula": item.get("tare_formula") or "",
                "qty_formula": item.get("qty_formula") or "",
            }
            for item in items
        ],
    })


async def enterprise_sales_create_api(request: Request, user: dict):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    try:
        data = await request.json()
        raw_customer = data.get("customer_id")
        customer_name = str(data.get("customer_name") or "").strip()
        customer_phone = str(data.get("customer_phone") or "").strip()[:50]
        customer_address = str(data.get("customer_address") or data.get("address") or "").strip()[:500]

        customer_id = 0
        if raw_customer is not None and str(raw_customer).strip().isdigit():
            customer_id = int(str(raw_customer).strip())
        elif raw_customer and not customer_name:
            customer_name = str(raw_customer).strip()

        new_customer_obj = None
        if customer_id <= 0:
            if not customer_name:
                return JSONResponse({"ok": False, "message": "Vui lòng chọn hoặc nhập tên khách hàng."}, status_code=400)
            cust = sales_service.get_or_create_customer(
                company_id, customer_name, customer_phone, customer_address, user.get("id")
            )
            if not cust:
                return JSONResponse({"ok": False, "message": "Không thể tạo thông tin khách hàng mới."}, status_code=500)
            customer_id = cust["id"]
            new_customer_obj = {
                "id": cust["id"],
                "customer_code": cust.get("customer_code", ""),
                "full_name": cust.get("full_name", customer_name),
                "phone": cust.get("phone", customer_phone),
                "address": cust.get("address", customer_address),
            }

        sale_date = date.fromisoformat(str(data.get("sale_date") or date.today().isoformat()))
        invoice_number = str(data.get("invoice_number") or "").strip()
        status = str(data.get("status") or ("pending" if not invoice_number else "delivered")).strip()
        if status in ("pending", "delivering"):
            invoice_number = ""
        items, item_error = sales_service.normalize_items(
            data.get("items"), allow_unpriced=status in ("pending", "delivering")
        )
        paid_amount = Decimal(str(data.get("paid_amount") or "0"))
        if item_error:
            return JSONResponse({"ok": False, "message": item_error}, status_code=400)
        if customer_id <= 0 or not paid_amount.is_finite() or paid_amount < 0:
            raise ValueError
        total_amount = sum((item["line_total"] for item in items), Decimal("0"))
    except (ValueError, TypeError, ArithmeticError):
        return JSONResponse({"ok": False, "message": "Thông tin hóa đơn hoặc số tiền không hợp lệ."}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "message": "Dữ liệu hóa đơn không hợp lệ."}, status_code=400)

    if invoice_number:
        conflict = sales_service.invoice_number_conflict(company_id, invoice_number)
        if conflict:
            return JSONResponse({"ok": False, "message": conflict}, status_code=400)

    values = {
        "invoice_number": invoice_number,
        "status": status,
        "customer_id": customer_id,
        "sale_date": sale_date,
        "customer_phone": customer_phone,
        "customer_address": customer_address,
        "carrier": str(data.get("carrier") or "").strip()[:255],
        "note": str(data.get("note") or "").strip(),
        "items": items,
        "total_amount": total_amount,
        "paid_amount": paid_amount,
        "payment_source": str(data.get("payment_source") or ("Tiền mặt" if paid_amount > 0 else "")).strip()[:255],
    }
    sale = sales_service.create_sale(company_id, values, user.get("id"))
    if not sale:
        return JSONResponse({"ok": False, "message": "Không tìm thấy khách hàng thuộc doanh nghiệp đang chọn."}, status_code=404)
    res_data = {"ok": True, "message": "Đã lưu hóa đơn bán hàng.", "sale": _sale_row_json(sale)}
    if new_customer_obj:
        res_data["new_customer"] = new_customer_obj
    return JSONResponse(res_data)


async def enterprise_sales_update_api(sale_id: int, request: Request, user: dict):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    try:
        data = await request.json()
        sale_date = date.fromisoformat(str(data.get("sale_date") or ""))
        status = str(data.get("status") or "").strip()
        items, item_error = sales_service.normalize_items(
            data.get("items"), allow_unpriced=status in ("pending", "delivering")
        )
        if item_error:
            return JSONResponse({"ok": False, "message": item_error}, status_code=400)
        total_amount = sum((item["line_total"] for item in items), Decimal("0"))
        paid_amount = Decimal(str(data.get("paid_amount") or "0")) if "paid_amount" in data else None
        if paid_amount is not None and (not paid_amount.is_finite() or paid_amount < 0):
            paid_amount = Decimal("0")
    except (ValueError, TypeError, ArithmeticError):
        return JSONResponse({"ok": False, "message": "Thông tin hóa đơn không hợp lệ."}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "message": "Dữ liệu hóa đơn không hợp lệ."}, status_code=400)

    custom_inv_num = str(data.get("invoice_number") or "").strip()
    if status in ("pending", "delivering"):
        custom_inv_num = ""
    if custom_inv_num:
        conflict = sales_service.invoice_number_conflict(company_id, custom_inv_num, sale_id)
        if conflict:
            return JSONResponse({"ok": False, "message": conflict}, status_code=400)

    values = {
        "invoice_number": custom_inv_num,
        "status": status,
        "sale_date": sale_date,
        "customer_phone": str(data.get("customer_phone") or "").strip()[:50],
        "customer_address": str(data.get("customer_address") or "").strip()[:500],
        "carrier": str(data.get("carrier") or "").strip()[:255],
        "note": str(data.get("note") or "").strip(),
        "items": items,
        "total_amount": total_amount,
    }
    if paid_amount is not None:
        values["paid_amount"] = paid_amount
        values["payment_source"] = str(data.get("payment_source") or ("Tiền mặt" if paid_amount > 0 else "")).strip()[:255]
    try:
        updated = sales_service.update_sale(company_id, sale_id, values)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    if not updated:
        return JSONResponse({"ok": False, "message": "Không tìm thấy hóa đơn."}, status_code=404)
    return JSONResponse({"ok": True, "message": "Đã cập nhật hóa đơn bán hàng."})


async def enterprise_sales_delete_api(sale_id: int, request: Request, user: dict):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    if not sales_service.delete_sale(company_id, sale_id):
        return JSONResponse({"ok": False, "message": "Không tìm thấy hóa đơn."}, status_code=404)
    return JSONResponse({"ok": True, "message": "Đã xóa hóa đơn bán hàng."})


async def enterprise_sales_import_preview_api(request: Request, file: UploadFile, user: dict):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    raw = await file.read(sales_import.MAX_UPLOAD_BYTES + 1)
    try:
        plan = sales_import.parse_upload(file.filename or "", raw)
        summary = sales_service.sales_import_preview(company_id, plan)
    except sales_import.SalesImportError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "summary": summary})


async def enterprise_sales_import_api(request: Request, file: UploadFile, user: dict):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    raw = await file.read(sales_import.MAX_UPLOAD_BYTES + 1)
    try:
        plan = sales_import.parse_upload(file.filename or "", raw)
        counts = sales_service.import_sales_workbooks(company_id, plan, user.get("id"))
    except sales_import.SalesImportError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return JSONResponse({
        "ok": True,
        "message": "Đã nhập dữ liệu bán hàng.",
        "counts": counts,
        "customers": sales_service.list_import_customers(company_id),
        "warnings": plan["warnings"][:30],
    })


async def enterprise_sales_payment_api(sale_id: int, request: Request, user: dict):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    try:
        data = await request.json()
        amount = Decimal(str(data.get("amount") or "0"))
        if not amount.is_finite() or amount <= 0:
            raise ValueError
    except Exception:
        return JSONResponse({"ok": False, "message": "Số tiền thanh toán phải lớn hơn 0."}, status_code=400)
    recorded = sales_service.record_payment(
        company_id, sale_id, amount,
        str(data.get("source") or "Tiền mặt").strip()[:255],
        str(data.get("note") or "").strip(), user.get("id"),
    )
    if not recorded:
        return JSONResponse({"ok": False, "message": "Không tìm thấy hóa đơn."}, status_code=404)
    return JSONResponse({"ok": True, "message": "Đã ghi nhận thanh toán."})


def _fmt_money_num(value) -> str:
    try:
        amount = int(round(Decimal(str(value or 0))))
    except Exception:
        amount = 0
    return f"{amount:,}".replace(",", ".")


def _fmt_qty_num(value) -> str:
    if value in (None, ""):
        return ""
    try:
        dec = Decimal(str(value))
        if not dec or dec <= 0:
            return ""
        rounded = dec.quantize(Decimal("0.001")).normalize()
        if rounded == rounded.to_integral():
            return f"{int(rounded):,}".replace(",", ".")
        parts = f"{rounded:f}".split(".")
        int_part = f"{int(parts[0]):,}".replace(",", ".")
        return f"{int_part},{parts[1]}"
    except Exception:
        return ""


def _sale_amount_words(value: int, use_ngan: bool = True) -> str:
    digits = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")

    def read_group(number: int, full: bool = False) -> str:
        hundreds, remainder = divmod(number, 100)
        tens, units = divmod(remainder, 10)
        words = []
        if hundreds or full:
            words.extend([digits[hundreds], "trăm"])
        if tens > 1:
            words.extend([digits[tens], "mươi"])
            if units == 1:
                words.append("mốt")
            elif units == 5:
                words.append("lăm")
            elif units:
                words.append(digits[units])
        elif tens == 1:
            words.append("mười")
            if units == 5:
                words.append("lăm")
            elif units:
                words.append(digits[units])
        elif units:
            if hundreds or full:
                words.append("lẻ")
            words.append("năm" if units == 5 and (hundreds or full) else digits[units])
        return " ".join(words)

    val_int = int(value or 0)
    if not val_int:
        return "Không đồng"
    is_neg = val_int < 0
    number = abs(val_int)
    groups = []
    while number:
        number, group = divmod(number, 1000)
        groups.append(group)
    thousand_scale = "ngàn" if use_ngan else "nghìn"
    scales = ("", thousand_scale, "triệu", "tỷ", "ngàn tỷ" if use_ngan else "nghìn tỷ")
    parts = []
    for index in range(len(groups) - 1, -1, -1):
        if not groups[index]:
            continue
        group_words = read_group(groups[index], full=bool(parts) and groups[index] < 100)
        scale_label = scales[index] if index < len(scales) else ""
        if scale_label:
            parts.append(f"{group_words} {scale_label}")
        else:
            parts.append(group_words)
    words_str = ", ".join(parts).capitalize() + " đồng"
    if is_neg:
        return f"Âm {words_str[0].lower() + words_str[1:]} (dư tiền)"
    return words_str


def _sale_payment_label(source) -> str:
    """The payment line of a printed sale: TTTM / TTCK for a plain cash / transfer source, otherwise the source
    exactly as it was typed on the sale ("Cty DP Tiến Phong CK Phương Nam")."""
    text = str(source or "").strip()
    folded = text.lower()
    if folded in ("", "tm", "tttm", "tiền mặt", "tt tiền mặt"):
        return "TTTM"
    if folded in ("ck", "ttck", "chuyển khoản", "tt chuyển khoản"):
        return "TTCK"
    return text


async def enterprise_sales_invoice_pdf(sale_id: int, request: Request, user: dict):
    sale_row = queries.enterprise_sales_invoice_pdf_select_personal_sales(sale_id)
    if not sale_row:
        raise HTTPException(status_code=404, detail="Không tìm thấy hóa đơn bán hàng.")
    company_id = sale_row["company_id"]
    sale, items = sales_service.get_sale(company_id, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Không tìm thấy hóa đơn bán hàng.")

    sale_date = sale["sale_date"]
    sale["sale_date_display"] = sale_date.strftime("%d/%m/%Y")
    sale_date_day_month = sale_date.strftime("%d/%m")

    old_debt, last_date = sales_service.get_sale_previous_debt(
        company_id, sale.get("customer_id"), sale_date, sale["invoice_number"]
    )
    if last_date:
        if last_date.year < 100:
            last_date = last_date.replace(year=2000 + last_date.year)
        elif last_date.year < 1900:
            last_date = last_date.replace(year=2000 + (last_date.year % 100))
        if last_date.year != sale_date.year:
            prev_date_day_month = last_date.strftime("%d/%m/%Y")
        else:
            prev_date_day_month = last_date.strftime("%d/%m")
    else:
        prev_date = sale_date - timedelta(days=1)
        prev_date_day_month = prev_date.strftime("%d/%m")

    invoice_total = Decimal(str(sale["total_amount"]))
    grand_total = invoice_total + old_debt

    payments_raw = sales_service.get_sale_payments(
        company_id, sale.get("customer_id"), sale["invoice_number"]
    )
    payments = []
    total_paid = Decimal("0")
    if payments_raw:
        for p in payments_raw:
            amt = Decimal(str(p.get("amount_paid") or 0))
            if amt <= 0:
                continue
            total_paid += amt
            p_date = p["transaction_date"].strftime("%d/%m") if p.get("transaction_date") else sale_date_day_month
            method_code = _sale_payment_label(p.get("received_from"))
            payments.append({
                "label": f"{p_date} {method_code}",
                "amount": amt,
                "amount_fmt": _fmt_money_num(amt)
            })
    elif Decimal(str(sale.get("paid_on_sale") or 0)) > 0:
        amt = Decimal(str(sale["paid_on_sale"]))
        total_paid = amt
        method_code = _sale_payment_label(sale.get("payment_source"))
        payments.append({
            "label": f"{sale_date_day_month} {method_code}",
            "amount": amt,
            "amount_fmt": _fmt_money_num(amt)
        })

    remaining_debt = grand_total - total_paid

    sale["paid_total"] = total_paid
    sale["paid_total_fmt"] = _fmt_money_num(total_paid)
    sale["remaining_debt"] = remaining_debt

    if remaining_debt < 0:
        sale["remaining_debt_label"] = "Còn thừa"
        sale["remaining_debt_fmt"] = _fmt_money_num(abs(remaining_debt))
        words_amount = int(round(abs(remaining_debt)))
        sale["remaining_debt_words"] = _sale_amount_words(words_amount, use_ngan=False)
    else:
        sale["remaining_debt_label"] = "Còn nợ"
        sale["remaining_debt_fmt"] = _fmt_money_num(remaining_debt)
        words_amount = int(round(remaining_debt))
        sale["remaining_debt_words"] = _sale_amount_words(words_amount, use_ngan=False)

    sale["display_invoice_number"] = sale.get("source_invoice_number") or sale["invoice_number"]
    sale["invoice_total_fmt"] = _fmt_money_num(invoice_total)
    sale["old_debt_fmt"] = _fmt_money_num(old_debt)
    sale["grand_total_fmt"] = _fmt_money_num(grand_total)

    for item in items:
        qty_num = Decimal(str(item.get('quantity') or 0))
        price_num = Decimal(str(item.get('unit_price') or 0))
        item["quantity_display"] = _fmt_qty_num(qty_num)
        item["gross_weight_display"] = _fmt_qty_num(item.get("gross_weight"))
        item["tare_weight_display"] = _fmt_qty_num(item.get("tare_weight"))
        item["unit_price_fmt"] = _fmt_money_num(price_num) if price_num > 0 else ""
        item["line_total_fmt"] = _fmt_money_num(item["line_total"]) if Decimal(str(item.get("line_total") or 0)) > 0 else ""
        item["is_surcharge"] = bool(qty_num <= 0 and price_num <= 0 and Decimal(str(item.get("line_total") or 0)) > 0)

    pad_rows = []

    html = templates.env.get_template("pages/enterprise/sales_invoice_pdf.html").render(
        sale=sale,
        items=items,
        pad_rows=pad_rows,
        sale_date_day_month=sale_date_day_month,
        prev_date_day_month=prev_date_day_month,
        payments=payments,
        has_weigh_info=False,
        summary_colspan=5,
        old_debt=old_debt,
    )
    from frontend.modules.transport.contract import _find_browser_exe, render_pdf_from_html
    if not _find_browser_exe():
        raise HTTPException(status_code=503, detail="Không tìm thấy Chrome/Edge để tạo PDF.")
    pdf_bytes = render_pdf_from_html(html, f"HOÁ ĐƠN BÁN HÀNG {sale['display_invoice_number']}")
    try:
        if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
            raise ValueError("Không tạo được dữ liệu PDF hợp lệ")
        import fitz
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
            rendered_text = " ".join(page.get_text() for page in pdf)
        if "Vui lòng mở trên trình duyệt có Chrome/Edge" in rendered_text:
            raise ValueError("Không tìm thấy Chrome/Edge để tạo PDF.")
    except Exception as exc:
        logger.exception("Could not render sales invoice PDF")
        raise HTTPException(status_code=500, detail="Không thể tạo PDF hóa đơn.") from exc

    filename = re.sub(r"[^A-Za-z0-9_-]", "_", sale["display_invoice_number"])
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="Hoa_don_{filename}.pdf"'},
    )


async def enterprise_presence_heartbeat_api(request: Request, user: dict):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    try:
        data = await request.json()
        session_id = str(data.get("session_id") or "").strip()
        if not session_id:
            return JSONResponse({"ok": False, "message": "Thiếu session_id."}, status_code=400)
        scope = str(data.get("scope") or "sales").strip()
        resource_id = str(data.get("resource_id") or "").strip()
        customer_id = str(data.get("customer_id") or "").strip()
        customer_name = str(data.get("customer_name") or "").strip()
        invoice_number = str(data.get("invoice_number") or "").strip()
        user_id = user.get("id") or 0
        user_name = user.get("full_name") or user.get("username") or f"User #{user_id}"

        from frontend.modules.enterprise import presence_service
        res = presence_service.record_heartbeat(
            session_id=session_id,
            company_id=company_id,
            scope=scope,
            user_id=user_id,
            user_name=user_name,
            resource_id=resource_id,
            customer_id=customer_id,
            customer_name=customer_name,
            invoice_number=invoice_number,
        )
        return JSONResponse(res)
    except Exception as exc:
        logging.getLogger(__name__).warning("Lỗi ghi nhận presence heartbeat: %s", exc)
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)


async def enterprise_presence_release_api(request: Request, user: dict):
    try:
        data = await request.json()
        session_id = str(data.get("session_id") or "").strip()
        if session_id:
            from frontend.modules.enterprise import presence_service
            presence_service.release_session(session_id)
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)


async def enterprise_presence_active_api(request: Request, user: dict):
    company_id = _sales_company_id(request, user)
    if not company_id:
        return JSONResponse({"ok": False, "message": "Chưa chọn doanh nghiệp."}, status_code=400)
    scope = request.query_params.get("scope", "sales")
    from frontend.modules.enterprise import presence_service
    active = presence_service.get_active_sessions_for_company(company_id=company_id, scope=scope)
    return JSONResponse({"ok": True, "active": active})

