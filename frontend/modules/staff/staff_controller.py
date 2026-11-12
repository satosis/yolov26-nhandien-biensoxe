"""Staff / Human Resources management router."""

from datetime import date
from decimal import Decimal
import logging
import re
import unicodedata
import urllib.parse
import bcrypt
import psycopg2
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from frontend.deps import get_menu_context, templates
from frontend.modules.invoices.companies import list_bank_names
from frontend.modules.gender_utils import predict_gender
from frontend.modules.contact_validation import clean_email, clean_phone
from frontend.formatting.money import format_vnd_rounded as _fmt_currency
from frontend.pagination import paginate_counts

logger = logging.getLogger(__name__)


def _slugify_name(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = re.sub(r"[\u0300-\u036f]", "", text)
    text = text.replace("đ", "d").replace("Đ", "d")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return slug or "staff"


def _staff_missing(user_id: int) -> JSONResponse | None:
    """Return a 404 response when the staff member does not exist (avoids a foreign-key 500)."""
    if queries.staff_missing_select_users(user_id):
        return None
    return JSONResponse({"ok": False, "message": "Không tìm thấy nhân sự."}, status_code=404)


from frontend.formatting.money import parse_vnd
import frontend.modules.staff.staff_queries as queries

COMMON_DEPARTMENTS = {
    "ban giám đốc", "bộ phận lái xe", "đội xe vận tải", "phòng kế toán",
    "phòng điều hành & vận tải", "kho bãi", "phòng kỹ thuật", "phòng hành chính nhân sự",
    "vận hành", "vận chuyển", "default"
}


def _validate_dept_name(dept: str) -> bool:
    if not dept:
        return True
    clean = dept.strip().lower()
    if clean in COMMON_DEPARTMENTS:
        return True
    row = queries.validate_dept_name_select_enterprise_departments(clean)
    return bool(row)


def _clean_numeric(value) -> Decimal:
    if value is None or str(value).strip() == "":
        return Decimal("0")
    try:
        return parse_vnd(value)
    except ValueError:
        return Decimal("0")


def _get_unique_username(base_name: str, current_user_id: int | None = None) -> str:
    base_slug = _slugify_name(base_name)
    username = base_slug
    idx = 1
    while True:
        exists = queries.username_taken(username, current_user_id)
        if not exists:
            return username
        username = f"{base_slug}_{idx}"
        idx += 1


def _get_default_staff_role_id() -> int:
    admin_role = queries.get_default_staff_role_id_select_roles()
    return admin_role["id"] if admin_role else 1


async def staff_index(
    request: Request,
    page: int,
    department: str,
    q: str,
    message: str,
    error: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "staff"
    ctx["message"] = message
    ctx["error"] = error
    ctx["q"] = q
    ctx["selected_department"] = department

    # Determine company filter
    all_companies = queries.staff_index_select_invoice_companies() or []
    companies_map = {c["id"]: c for c in all_companies}
    ctx["all_companies"] = all_companies
    ctx["companies_map"] = companies_map

    # Staff list follows the company selected in the global header.
    selected_company_id = ctx.get("active_company_id")
    ctx["selected_company_id"] = selected_company_id

    # Filter conditions
    search_term = None
    private_ids = []
    if q.strip():
        search_term = f"%{q.strip()}%"
        needle = q.strip().lower()
        private_rows = queries.staff_index_select_users() or []
        private_ids = [r["id"] for r in private_rows
                       if needle in (r["phone"] or "").lower() or needle in (r["id_number"] or "").lower()]
    filters = (selected_company_id, department, search_term, private_ids)

    # Count
    count_row = queries.count_staff(*filters)
    total_count = count_row["total"] if count_row else 0

    per_page = 100
    paginated = paginate_counts(
        total_count, page=page, per_page=per_page,
        base_url="/admin/staff",
        extra_params={
            "department": department or None,
            "q": q.strip() or None,
        },
    )

    # Query rows
    offset = (paginated["page"] - 1) * per_page
    staff_rows = queries.list_staff(*filters, per_page, offset) or []

    for s in staff_rows:
        base = Decimal(str(s["base_salary"]))
        resp = Decimal(str(s["responsibility_allowance"]))
        meal = Decimal(str(s["meal_allowance"]))
        phone = Decimal(str(s["phone_allowance"]))
        insurance = Decimal(str(s["insurance_salary"]))
        fixed_sum = base + resp + meal + phone
        s["fixed_pay"] = fixed_sum
        s["fixed_pay_fmt"] = _fmt_currency(fixed_sum)
        s["base_salary_fmt"] = _fmt_currency(base)
        s["insurance_salary_fmt"] = _fmt_currency(insurance)
        s["allowances_fmt"] = _fmt_currency(resp + meal + phone)

        cids = s.get("company_ids") or []
        s["company_names"] = [companies_map[cid]["name"] for cid in cids if cid in companies_map]

    # Preload contracts and licenses for alerts
    uids = [s["id"] for s in staff_rows]
    contracts_by_user = {}
    licenses_by_user = {}
    if uids:
        c_rows = queries.staff_index_select_staff_contracts(uids) or []
        for cr in c_rows:
            contracts_by_user.setdefault(cr["user_id"], []).append(cr)

        l_rows = queries.staff_index_select_staff_licenses(uids) or []
        for lr in l_rows:
            licenses_by_user.setdefault(lr["user_id"], []).append(lr)

    today = date.today()
    for s in staff_rows:
        u_contracts = contracts_by_user.get(s["id"]) or []
        u_licenses = licenses_by_user.get(s["id"]) or []
        s["contracts_count"] = len(u_contracts)
        s["licenses_count"] = len(u_licenses)
        s["active_contract"] = u_contracts[0] if u_contracts else None

        s["license_warning"] = None
        for lic in u_licenses:
            if lic.get("expiry_date"):
                delta = (lic["expiry_date"] - today).days
                if delta < 0:
                    s["license_warning"] = f"Hết hạn bằng {lic['license_type']} ({abs(delta)} ngày trước)"
                    break
                elif delta <= 30:
                    s["license_warning"] = f"Bằng {lic['license_type']} sắp hết hạn ({delta} ngày)"
                    break

        s["contract_warning"] = None
        if s["active_contract"] and s["active_contract"].get("end_date"):
            delta = (s["active_contract"]["end_date"] - today).days
            if delta < 0:
                s["contract_warning"] = f"HĐLĐ đã hết hạn ({abs(delta)} ngày)"
            elif delta <= 30:
                s["contract_warning"] = f"HĐLĐ sắp hết hạn ({delta} ngày)"

    # Department list for filter
    dept_rows = queries.staff_index_select_staff_salary_profiles() or []
    ctx["department_list"] = [d["dept"] for d in dept_rows]

    ctx["bank_names"] = list_bank_names()
    ctx["staff_list"] = staff_rows
    ctx["page"] = paginated["page"]
    ctx["total_pages"] = paginated["total_pages"]
    ctx["total"] = paginated["total"]

    return templates.TemplateResponse("pages/staff/index.html", {"request": request, **ctx})


async def get_staff_json(user_id: int, _: dict):
    user_row = queries.get_staff_json_run_sql(user_id)
    if not user_row:
        return JSONResponse({"ok": False, "message": "Không tìm thấy nhân sự."}, status_code=404)

    # Convert datatypes
    data = dict(user_row)
    if data.get("date_of_birth"):
        data["date_of_birth"] = str(data["date_of_birth"])
    for num_key in ("base_salary", "responsibility_allowance", "meal_allowance", "phone_allowance", "insurance_salary"):
        data[num_key] = float(data.get(num_key) or 0)
    data["company_ids"] = list(data.get("company_ids") or [])
    return JSONResponse({"ok": True, "staff": data})


async def create_staff(
    request: Request,
    full_name: str,
    username: str,
    password: str,
    gender: str,
    date_of_birth: str,
    id_number: str,
    phone: str,
    email: str,
    nickname: str,
    note: str,
    bank_account: str,
    bank_name: str,
    job_title: str,
    department: str,
    base_salary: str,
    insurance_salary: str,
    responsibility_allowance: str,
    meal_allowance: str,
    phone_allowance: str,
    back: str,
    _: dict,
):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    form = await request.form()
    company_ids_raw = form.getlist("company_ids")
    company_ids = [int(cid) for cid in company_ids_raw if str(cid).isdigit()]

    full_name = full_name.strip()
    if not full_name:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Họ và tên không được để trống."}, status_code=400)
        return RedirectResponse(f"{back}?error={urllib.parse.quote('Họ và tên không được để trống.')}", status_code=303)
    try:
        phone = clean_phone(phone)
        email = clean_email(email)
    except ValueError as exc:
        if is_ajax:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        return RedirectResponse(f"{back}?error={urllib.parse.quote(str(exc))}", status_code=303)

    if not username.strip():
        username = _get_unique_username(full_name)
    else:
        username = username.strip().lower()
        exists = queries.create_staff_select_users(username)
        if exists:
            msg = f"Tên đăng nhập '{username}' đã được sử dụng."
            if is_ajax:
                return JSONResponse({"ok": False, "message": msg}, status_code=400)
            return RedirectResponse(f"{back}?error={urllib.parse.quote(msg)}", status_code=303)

    if department and not _validate_dept_name(department):
        msg = f"Phòng ban '{department}' không hợp lệ."
        if is_ajax:
            return JSONResponse({"ok": False, "message": msg}, status_code=400)
        return RedirectResponse(f"{back}?error={urllib.parse.quote(msg)}", status_code=303)

    salary_checks = {
        "base_salary": ("Lương cơ bản", base_salary),
        "insurance_salary": ("Lương đóng bảo hiểm", insurance_salary),
        "responsibility_allowance": ("Phụ cấp trách nhiệm", responsibility_allowance),
        "meal_allowance": ("Phụ cấp tiền ăn", meal_allowance),
        "phone_allowance": ("Phụ cấp điện thoại", phone_allowance),
    }
    for sk, (slabel, sval) in salary_checks.items():
        if sval is not None and str(sval).strip() != "":
            try:
                parse_vnd(sval)
            except ValueError:
                msg = f"{slabel} không hợp lệ."
                if is_ajax:
                    return JSONResponse({"ok": False, "message": msg}, status_code=400)
                return RedirectResponse(f"{back}?error={urllib.parse.quote(msg)}", status_code=303)

    if not gender or gender not in ("nam", "nữ"):
        gender = predict_gender(full_name)

    dob = None
    if date_of_birth and str(date_of_birth).strip():
        try:
            from frontend.modules.transport.cash_bank_service import parse_flexible_date
            dob = parse_flexible_date(date_of_birth)
        except ValueError:
            msg = "Ngày sinh không hợp lệ."
            if is_ajax:
                return JSONResponse({"ok": False, "message": msg}, status_code=400)
            return RedirectResponse(f"{back}?error={urllib.parse.quote(msg)}", status_code=303)
    if not password.strip():
        msg = "Vui lòng nhập mật khẩu."
        if is_ajax:
            return JSONResponse({"ok": False, "message": msg}, status_code=400)
        return RedirectResponse(f"{back}?error={urllib.parse.quote(msg)}", status_code=303)
    hashed_pwd = bcrypt.hashpw(password.strip().encode(), bcrypt.gensalt()).decode()
    role_id = _get_default_staff_role_id()

    # Insert user
    new_user_id = queries.create_staff_run_sql(username, role_id, full_name, gender, hashed_pwd, nickname, dob, company_ids, bank_name, note, phone, email, id_number, bank_account)

    if not new_user_id:
        msg = "Không thể tạo tài khoản nhân sự mới trong cơ sở dữ liệu."
        if is_ajax:
            return JSONResponse({"ok": False, "message": msg}, status_code=500)
        return RedirectResponse(f"{back}?error={urllib.parse.quote(msg)}", status_code=303)

    # Insert salary profile
    queries.create_staff_insert_staff_salary_profiles(
        new_user_id, job_title.strip() or None, department.strip() or None,
        _clean_numeric(base_salary), _clean_numeric(insurance_salary),
        _clean_numeric(responsibility_allowance), _clean_numeric(meal_allowance),
        _clean_numeric(phone_allowance),
    )

    msg = f"Đã thêm mới nhân sự '{full_name}' thành công."
    if is_ajax:
        return JSONResponse({"ok": True, "message": msg, "user_id": new_user_id})
    return RedirectResponse(f"{back}?message={urllib.parse.quote(msg)}", status_code=303)


async def update_staff(
    user_id: int,
    request: Request,
    full_name: str,
    gender: str,
    date_of_birth: str,
    id_number: str,
    phone: str,
    email: str,
    nickname: str,
    note: str,
    bank_account: str,
    bank_name: str,
    new_password: str,
    job_title: str,
    department: str,
    base_salary: str,
    insurance_salary: str,
    responsibility_allowance: str,
    meal_allowance: str,
    phone_allowance: str,
    back: str,
    _: dict,
):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    form = await request.form()
    company_ids_raw = form.getlist("company_ids")
    company_ids = [int(cid) for cid in company_ids_raw if str(cid).isdigit()]

    existing = queries.update_staff_select_users(user_id)
    if not existing:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Không tìm thấy nhân sự."}, status_code=404)
        return RedirectResponse(f"{back}?error={urllib.parse.quote('Không tìm thấy nhân sự.')}", status_code=303)

    full_name = full_name.strip()
    if not full_name:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Họ và tên không được để trống."}, status_code=400)
        return RedirectResponse(f"{back}?error={urllib.parse.quote('Họ và tên không được để trống.')}", status_code=303)
    try:
        phone = clean_phone(phone)
        email = clean_email(email)
    except ValueError as exc:
        if is_ajax:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        return RedirectResponse(f"{back}?error={urllib.parse.quote(str(exc))}", status_code=303)

    if not gender or gender not in ("nam", "nữ"):
        gender = predict_gender(full_name)

    dob = None
    if date_of_birth and str(date_of_birth).strip():
        try:
            from frontend.modules.transport.cash_bank_service import parse_flexible_date
            dob = parse_flexible_date(date_of_birth)
        except ValueError:
            msg = "Ngày sinh không hợp lệ."
            if is_ajax:
                return JSONResponse({"ok": False, "message": msg}, status_code=400)
            return RedirectResponse(f"{back}?error={urllib.parse.quote(msg)}", status_code=303)

    # Update users table (leave role_id untouched, roles are system permissions not company job titles)
    if new_password.strip():
        hashed_pwd = bcrypt.hashpw(new_password.strip().encode(), bcrypt.gensalt()).decode()
        queries.update_staff_users_with_password(full_name, gender, user_id, nickname, dob, company_ids, hashed_pwd, bank_name, note, phone, email, id_number, bank_account)
    else:
        queries.update_staff_users(full_name, gender, user_id, nickname, dob, company_ids, bank_name, note, phone, email, id_number, bank_account)

    # Upsert staff_salary_profiles
    queries.update_staff_upsert_staff_salary_profiles(
        user_id, job_title.strip() or None, department.strip() or None,
        _clean_numeric(base_salary), _clean_numeric(insurance_salary),
        _clean_numeric(responsibility_allowance), _clean_numeric(meal_allowance),
        _clean_numeric(phone_allowance),
    )

    msg = f"Đã cập nhật hồ sơ nhân sự '{full_name}'."
    if is_ajax:
        return JSONResponse({"ok": True, "message": msg})
    return RedirectResponse(f"{back}?message={urllib.parse.quote(msg)}", status_code=303)


async def delete_staff(user_id: int, request: Request, back: str, _: dict):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")

    user_row = queries.delete_staff_select_users(user_id)
    if not user_row:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Không tìm thấy nhân sự."}, status_code=404)
        return RedirectResponse(f"{back}?error={urllib.parse.quote('Không tìm thấy nhân sự.')}", status_code=303)

    if user_row.get("username") == "admin":
        msg = "Không thể xóa tài khoản Administrator chính của hệ thống."
        if is_ajax:
            return JSONResponse({"ok": False, "message": msg}, status_code=400)
        return RedirectResponse(f"{back}?error={urllib.parse.quote(msg)}", status_code=303)

    # Check if director of any company
    director_companies = queries.delete_staff_select_invoice_companies(user_id) or []
    if director_companies:
        names = ", ".join([c["name"] for c in director_companies[:2]])
        msg = f"Nhân sự đang là Giám đốc/Đại diện pháp luật của ({names}). Vui lòng chỉ định người đại diện khác trước khi xóa."
        if is_ajax:
            return JSONResponse({"ok": False, "message": msg}, status_code=400)
        return RedirectResponse(f"{back}?error={urllib.parse.quote(msg)}", status_code=303)

    # Delete staff salary profile & payroll data
    queries.delete_staff_delete_staff_payroll_months(user_id)
    queries.delete_staff_delete_staff_salary_profiles(user_id)

    # Delete or unassign user
    queries.delete_staff_delete_users(user_id)

    name = user_row.get("full_name") or user_row.get("username")
    msg = f"Đã xóa hồ sơ nhân sự '{name}'."
    if is_ajax:
        return JSONResponse({"ok": True, "message": msg})
    return RedirectResponse(f"{back}?message={urllib.parse.quote(msg)}", status_code=303)


# ==============================================================================
# HỢP ĐỒNG LAO ĐỘNG, BẰNG LÁI & QUÁ TRÌNH CÔNG TÁC
# ==============================================================================

async def get_staff_records(user_id: int, _: dict):
    contracts = queries.get_staff_records_select_staff_contracts(user_id) or []
    for c in contracts:
        c["salary_rate_fmt"] = _fmt_currency(c.get("salary_rate"))
        c["insurance_rate_fmt"] = _fmt_currency(c.get("insurance_rate"))
        c["salary_rate"] = float(c["salary_rate"]) if c.get("salary_rate") is not None else 0
        c["insurance_rate"] = float(c["insurance_rate"]) if c.get("insurance_rate") is not None else 0
        if c.get("sign_date"): c["sign_date"] = c["sign_date"].strftime("%d/%m/%Y")
        if c.get("start_date"): c["start_date"] = c["start_date"].strftime("%d/%m/%Y")
        if c.get("end_date"): c["end_date"] = c["end_date"].strftime("%d/%m/%Y")
        if c.get("created_at"): c["created_at"] = c["created_at"].strftime("%d/%m/%Y %H:%M")

    licenses = queries.get_staff_records_select_staff_licenses(user_id) or []
    for l in licenses:
        if l.get("issued_date"): l["issued_date"] = l["issued_date"].strftime("%d/%m/%Y")
        if l.get("expiry_date"): l["expiry_date"] = l["expiry_date"].strftime("%d/%m/%Y")
        if l.get("created_at"): l["created_at"] = l["created_at"].strftime("%d/%m/%Y %H:%M")

    work_history = queries.get_staff_records_select_staff_work_history(user_id) or []
    for w in work_history:
        if w.get("event_date"): w["event_date"] = w["event_date"].strftime("%d/%m/%Y")
        if w.get("created_at"): w["created_at"] = w["created_at"].strftime("%d/%m/%Y %H:%M")

    return JSONResponse({
        "ok": True,
        "contracts": contracts,
        "licenses": licenses,
        "work_history": work_history,
    })


async def create_staff_contract(
    user_id: int,
    contract_number: str,
    contract_type: str,
    sign_date: str,
    start_date: str,
    end_date: str,
    salary_rate: str,
    insurance_rate: str,
    note: str,
    _: dict,
):
    if missing := _staff_missing(user_id):
        return missing
    try:
        queries.create_staff_contract_insert_staff_contracts(
            user_id, contract_number.strip() or None, contract_type,
            sign_date or None, start_date or None, end_date or None,
            _clean_numeric(salary_rate), _clean_numeric(insurance_rate),
            note.strip() or None,
        )
    except psycopg2.DataError:  # ngày/số sai định dạng
        return JSONResponse({"ok": False, "message": "Ngày hoặc số tiền không hợp lệ."}, status_code=400)
    return JSONResponse({"ok": True, "message": "Đã lưu hợp đồng lao động."})


async def delete_staff_contract(contract_id: int, _: dict):
    queries.delete_staff_contract_delete_staff_contracts(contract_id)
    return JSONResponse({"ok": True, "message": "Đã xóa hợp đồng."})


async def create_staff_license(
    user_id: int,
    license_type: str,
    license_number: str,
    issued_date: str,
    expiry_date: str,
    issued_by: str,
    note: str,
    _: dict,
):
    if missing := _staff_missing(user_id):
        return missing
    try:
        queries.create_staff_license_insert_staff_licenses(user_id, license_type, issued_date, expiry_date, license_number, issued_by, note)
    except psycopg2.DataError:  # ngày sai định dạng
        return JSONResponse({"ok": False, "message": "Ngày cấp hoặc ngày hết hạn không hợp lệ."}, status_code=400)
    return JSONResponse({"ok": True, "message": "Đã lưu bằng lái/chứng chỉ."})


async def delete_staff_license(license_id: int, _: dict):
    queries.delete_staff_license_delete_staff_licenses(license_id)
    return JSONResponse({"ok": True, "message": "Đã xóa bằng lái/chứng chỉ."})


async def create_staff_work_history(
    user_id: int,
    event_type: str,
    event_date: str,
    old_value: str,
    new_value: str,
    decision_number: str,
    note: str,
    user: dict,
):
    queries.create_staff_work_history_insert_staff_work_history(user_id, event_type, event_date, user, old_value, new_value, decision_number, note)
    return JSONResponse({"ok": True, "message": "Đã lưu thông tin quá trình công tác."})


async def delete_staff_work_history(hist_id: int, _: dict):
    queries.delete_staff_work_history_delete_staff_work_history(hist_id)
    return JSONResponse({"ok": True, "message": "Đã xóa lịch sử công tác."})
