"""Staff / Human Resources management router."""

from datetime import date
from decimal import Decimal
import logging
import re
import unicodedata
import urllib.parse
import bcrypt
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from backend.services.database import db
from frontend.deps import get_menu_context, require_menu, templates
from frontend.modules.invoices.companies import list_bank_names
from frontend.modules.gender_utils import predict_gender
from frontend.modules.user_nicknames import clean_nickname
from frontend.pagination import paginate_counts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/staff")


def _slugify_name(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = re.sub(r"[\u0300-\u036f]", "", text)
    text = text.replace("đ", "d").replace("Đ", "d")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return slug or "staff"


def _fmt_currency(value) -> str:
    try:
        val = int(round(Decimal(str(value or 0))))
        return f"{val:,}".replace(",", ".") + " đ"
    except Exception:
        return "0 đ"


def _clean_numeric(value) -> Decimal:
    if value is None:
        return Decimal("0")
    cleaned = re.sub(r"[^\d.]", "", str(value).replace(",", "."))
    try:
        return Decimal(cleaned) if cleaned else Decimal("0")
    except Exception:
        return Decimal("0")


def _get_unique_username(base_name: str, current_user_id: int | None = None) -> str:
    base_slug = _slugify_name(base_name)
    username = base_slug
    idx = 1
    while True:
        query = "SELECT id FROM public.users WHERE username = %s"
        params = [username]
        if current_user_id:
            query += " AND id != %s"
            params.append(current_user_id)
        exists = db._execute_query(query, tuple(params), fetch=True, commit=False)
        if not exists:
            return username
        username = f"{base_slug}_{idx}"
        idx += 1


def _get_default_staff_role_id() -> int:
    admin_role = db._execute_query(
        "SELECT id FROM public.roles WHERE LOWER(role_name) = 'admin' ORDER BY id LIMIT 1",
        fetch=True, commit=False
    )
    return admin_role["id"] if admin_role else 1



@router.get("")
@router.get("/")
async def staff_index(
    request: Request,
    page: int = Query(1, ge=1),
    department: str = Query(""),
    q: str = Query("", max_length=200),
    message: str = Query(""),
    error: str = Query(""),
    user: dict = Depends(require_menu("staff")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "staff"
    ctx["message"] = message
    ctx["error"] = error
    ctx["q"] = q
    ctx["selected_department"] = department

    # Determine company filter
    all_companies = db._execute_query(
        "SELECT id, name, tax_code, address FROM public.invoice_companies ORDER BY name",
        fetchall=True, commit=False,
    ) or []
    companies_map = {c["id"]: c for c in all_companies}
    ctx["all_companies"] = all_companies
    ctx["companies_map"] = companies_map

    # Staff list follows the company selected in the global header.
    selected_company_id = ctx.get("active_company_id")
    ctx["selected_company_id"] = selected_company_id

    # Filter conditions
    where_clauses = ["1=1"]
    params = []

    if selected_company_id is not None:
        where_clauses.append("%s = ANY(u.company_ids)")
        params.append(selected_company_id)

    if department:
        where_clauses.append("LOWER(TRIM(p.department)) = LOWER(TRIM(%s))")
        params.append(department)

    if q.strip():
        search_term = f"%{q.strip()}%"
        where_clauses.append(
            "(u.full_name ILIKE %s OR u.username ILIKE %s OR u.phone ILIKE %s "
            "OR u.id_number ILIKE %s OR p.job_title ILIKE %s)"
        )
        params.extend([search_term, search_term, search_term, search_term, search_term])

    where_sql = " AND ".join(where_clauses)

    # Count
    count_sql = f"""
        SELECT COUNT(*) as total
        FROM public.users u
        LEFT JOIN public.staff_salary_profiles p ON p.user_id = u.id
        WHERE {where_sql}
    """
    count_row = db._execute_query(count_sql, tuple(params) if params else None, fetch=True, commit=False)
    total_count = count_row["total"] if count_row else 0

    per_page = 20
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
    query_sql = f"""
        SELECT u.id, u.username, u.full_name, u.gender, u.nickname, u.phone, u.email,
               u.date_of_birth, u.id_number, u.company_ids, u.bank_account, u.bank_name, u.note,
               u.created_at, r.role_name as role,
               p.job_title, p.department,
               COALESCE(p.base_salary, 0) as base_salary,
               COALESCE(p.responsibility_allowance, 0) as responsibility_allowance,
               COALESCE(p.meal_allowance, 0) as meal_allowance,
               COALESCE(p.phone_allowance, 0) as phone_allowance,
               COALESCE(p.insurance_salary, 0) as insurance_salary,
               p.updated_at as profile_updated_at,
               p.user_id IS NOT NULL as has_salary_profile
        FROM public.users u
        LEFT JOIN public.roles r ON u.role_id = r.id
        LEFT JOIN public.staff_salary_profiles p ON p.user_id = u.id
        WHERE {where_sql}
        ORDER BY CASE WHEN lower(COALESCE(p.job_title, '')) LIKE '%%giám đốc%%' THEN 0 ELSE 1 END,
                 COALESCE(u.full_name, u.username) ASC
        LIMIT %s OFFSET %s
    """
    query_params = list(params) + [per_page, offset]
    staff_rows = db._execute_query(query_sql, tuple(query_params), fetchall=True, commit=False) or []

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
        c_rows = db._execute_query(
            "SELECT * FROM public.staff_contracts WHERE user_id = ANY(%s) ORDER BY end_date DESC NULLS LAST",
            (uids,), fetchall=True, commit=False,
        ) or []
        for cr in c_rows:
            contracts_by_user.setdefault(cr["user_id"], []).append(cr)

        l_rows = db._execute_query(
            "SELECT * FROM public.staff_licenses WHERE user_id = ANY(%s) ORDER BY expiry_date ASC NULLS LAST",
            (uids,), fetchall=True, commit=False,
        ) or []
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
    dept_rows = db._execute_query(
        "SELECT DISTINCT TRIM(department) as dept FROM public.staff_salary_profiles WHERE department IS NOT NULL AND TRIM(department) != '' ORDER BY dept",
        fetchall=True, commit=False,
    ) or []
    ctx["department_list"] = [d["dept"] for d in dept_rows]

    ctx["bank_names"] = list_bank_names()
    ctx["staff_list"] = staff_rows
    ctx["page"] = paginated["page"]
    ctx["total_pages"] = paginated["total_pages"]
    ctx["total"] = paginated["total"]

    return templates.TemplateResponse("pages/staff/index.html", {"request": request, **ctx})


@router.get("/{user_id}/json")
async def get_staff_json(user_id: int, _: dict = Depends(require_menu("staff"))):
    query = """
        SELECT u.id, u.username, u.full_name, u.gender, u.nickname, u.phone, u.email,
               u.date_of_birth, u.id_number, u.company_ids, u.bank_account, u.bank_name, u.note,
               p.job_title, p.department,
               COALESCE(p.base_salary, 0) as base_salary,
               COALESCE(p.responsibility_allowance, 0) as responsibility_allowance,
               COALESCE(p.meal_allowance, 0) as meal_allowance,
               COALESCE(p.phone_allowance, 0) as phone_allowance,
               COALESCE(p.insurance_salary, 0) as insurance_salary
        FROM public.users u
        LEFT JOIN public.staff_salary_profiles p ON p.user_id = u.id
        WHERE u.id = %s
    """
    user_row = db._execute_query(query, (user_id,), fetch=True, commit=False)
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


@router.post("/create")
async def create_staff(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(""),
    password: str = Form("123456"),
    gender: str = Form(""),
    date_of_birth: str = Form(""),
    id_number: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    nickname: str = Form(""),
    note: str = Form(""),
    bank_account: str = Form(""),
    bank_name: str = Form(""),
    job_title: str = Form(""),
    department: str = Form(""),
    base_salary: str = Form("0"),
    insurance_salary: str = Form("0"),
    responsibility_allowance: str = Form("0"),
    meal_allowance: str = Form("0"),
    phone_allowance: str = Form("0"),
    back: str = Form("/admin/staff"),
    _: dict = Depends(require_menu("staff")),
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

    if not username.strip():
        username = _get_unique_username(full_name)
    else:
        username = username.strip().lower()
        exists = db._execute_query("SELECT 1 FROM public.users WHERE username = %s", (username,), fetch=True, commit=False)
        if exists:
            msg = f"Tên đăng nhập '{username}' đã được sử dụng."
            if is_ajax:
                return JSONResponse({"ok": False, "message": msg}, status_code=400)
            return RedirectResponse(f"{back}?error={urllib.parse.quote(msg)}", status_code=303)

    if not gender or gender not in ("nam", "nữ"):
        gender = predict_gender(full_name)

    dob = date_of_birth.strip() if date_of_birth and date_of_birth.strip() else None
    hashed_pwd = bcrypt.hashpw((password.strip() or "123456").encode(), bcrypt.gensalt()).decode()
    role_id = _get_default_staff_role_id()

    # Insert user
    insert_user_sql = """
        INSERT INTO public.users (
            username, hashed_password, role_id, full_name, gender, nickname,
            phone, email, date_of_birth, id_number, company_ids, bank_account, bank_name, note
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        ) RETURNING id
    """
    new_user_id = db.execute_query(
        insert_user_sql,
        (
            username, hashed_pwd, role_id,
            full_name, gender, clean_nickname(nickname),
            phone.strip() or None, email.strip() or None, dob,
            id_number.strip() or None, company_ids or None,
            bank_account.strip() or None, bank_name.strip() or None, note.strip() or None
        ),
        commit=True, return_id=True,
    )

    if not new_user_id:
        msg = "Không thể tạo tài khoản nhân sự mới trong cơ sở dữ liệu."
        if is_ajax:
            return JSONResponse({"ok": False, "message": msg}, status_code=500)
        return RedirectResponse(f"{back}?error={urllib.parse.quote(msg)}", status_code=303)

    # Insert salary profile
    insert_profile_sql = """
        INSERT INTO public.staff_salary_profiles (
            user_id, job_title, department, base_salary, insurance_salary,
            responsibility_allowance, meal_allowance, phone_allowance, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, now()
        )
    """
    db._execute_query(
        insert_profile_sql,
        (
            new_user_id, job_title.strip() or None, department.strip() or None,
            _clean_numeric(base_salary), _clean_numeric(insurance_salary),
            _clean_numeric(responsibility_allowance), _clean_numeric(meal_allowance),
            _clean_numeric(phone_allowance)
        ),
        commit=True,
    )

    msg = f"Đã thêm mới nhân sự '{full_name}' thành công."
    if is_ajax:
        return JSONResponse({"ok": True, "message": msg, "user_id": new_user_id})
    return RedirectResponse(f"{back}?message={urllib.parse.quote(msg)}", status_code=303)


@router.post("/{user_id}/edit")
async def update_staff(
    user_id: int,
    request: Request,
    full_name: str = Form(...),
    gender: str = Form(""),
    date_of_birth: str = Form(""),
    id_number: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    nickname: str = Form(""),
    note: str = Form(""),
    bank_account: str = Form(""),
    bank_name: str = Form(""),
    new_password: str = Form(""),
    job_title: str = Form(""),
    department: str = Form(""),
    base_salary: str = Form("0"),
    insurance_salary: str = Form("0"),
    responsibility_allowance: str = Form("0"),
    meal_allowance: str = Form("0"),
    phone_allowance: str = Form("0"),
    back: str = Form("/admin/staff"),
    _: dict = Depends(require_menu("staff")),
):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    form = await request.form()
    company_ids_raw = form.getlist("company_ids")
    company_ids = [int(cid) for cid in company_ids_raw if str(cid).isdigit()]

    existing = db._execute_query("SELECT id FROM public.users WHERE id = %s", (user_id,), fetch=True, commit=False)
    if not existing:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Không tìm thấy nhân sự."}, status_code=404)
        return RedirectResponse(f"{back}?error={urllib.parse.quote('Không tìm thấy nhân sự.')}", status_code=303)

    full_name = full_name.strip()
    if not full_name:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Họ và tên không được để trống."}, status_code=400)
        return RedirectResponse(f"{back}?error={urllib.parse.quote('Họ và tên không được để trống.')}", status_code=303)

    if not gender or gender not in ("nam", "nữ"):
        gender = predict_gender(full_name)

    dob = date_of_birth.strip() if date_of_birth and date_of_birth.strip() else None

    # Update users table (leave role_id untouched, roles are system permissions not company job titles)
    if new_password.strip():
        hashed_pwd = bcrypt.hashpw(new_password.strip().encode(), bcrypt.gensalt()).decode()
        update_user_sql = """
            UPDATE public.users SET
                full_name = %s, gender = %s, nickname = %s, phone = %s, email = %s,
                date_of_birth = %s, id_number = %s, company_ids = %s,
                bank_account = %s, bank_name = %s, note = %s, hashed_password = %s,
                updated_at = now()
            WHERE id = %s
        """
        db._execute_query(
            update_user_sql,
            (
                full_name, gender, clean_nickname(nickname), phone.strip() or None,
                email.strip() or None, dob, id_number.strip() or None,
                company_ids or None, bank_account.strip() or None,
                bank_name.strip() or None, note.strip() or None, hashed_pwd,
                user_id
            ),
            commit=True,
        )
    else:
        update_user_sql = """
            UPDATE public.users SET
                full_name = %s, gender = %s, nickname = %s, phone = %s, email = %s,
                date_of_birth = %s, id_number = %s, company_ids = %s,
                bank_account = %s, bank_name = %s, note = %s,
                updated_at = now()
            WHERE id = %s
        """
        db._execute_query(
            update_user_sql,
            (
                full_name, gender, clean_nickname(nickname), phone.strip() or None,
                email.strip() or None, dob, id_number.strip() or None,
                company_ids or None, bank_account.strip() or None,
                bank_name.strip() or None, note.strip() or None,
                user_id
            ),
            commit=True,
        )

    # Upsert staff_salary_profiles
    upsert_profile_sql = """
        INSERT INTO public.staff_salary_profiles (
            user_id, job_title, department, base_salary, insurance_salary,
            responsibility_allowance, meal_allowance, phone_allowance, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, now()
        )
        ON CONFLICT (user_id) DO UPDATE SET
            job_title = EXCLUDED.job_title,
            department = EXCLUDED.department,
            base_salary = EXCLUDED.base_salary,
            insurance_salary = EXCLUDED.insurance_salary,
            responsibility_allowance = EXCLUDED.responsibility_allowance,
            meal_allowance = EXCLUDED.meal_allowance,
            phone_allowance = EXCLUDED.phone_allowance,
            updated_at = now()
    """
    db._execute_query(
        upsert_profile_sql,
        (
            user_id, job_title.strip() or None, department.strip() or None,
            _clean_numeric(base_salary), _clean_numeric(insurance_salary),
            _clean_numeric(responsibility_allowance), _clean_numeric(meal_allowance),
            _clean_numeric(phone_allowance)
        ),
        commit=True,
    )

    msg = f"Đã cập nhật hồ sơ nhân sự '{full_name}'."
    if is_ajax:
        return JSONResponse({"ok": True, "message": msg})
    return RedirectResponse(f"{back}?message={urllib.parse.quote(msg)}", status_code=303)


@router.post("/{user_id}/delete")
async def delete_staff(
    user_id: int,
    request: Request,
    back: str = Form("/admin/staff"),
    _: dict = Depends(require_menu("staff")),
):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")

    user_row = db._execute_query("SELECT id, username, full_name FROM public.users WHERE id = %s", (user_id,), fetch=True, commit=False)
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
    director_companies = db._execute_query(
        "SELECT id, name FROM public.invoice_companies WHERE director_user_id = %s",
        (user_id,), fetchall=True, commit=False,
    ) or []
    if director_companies:
        names = ", ".join([c["name"] for c in director_companies[:2]])
        msg = f"Nhân sự đang là Giám đốc/Đại diện pháp luật của ({names}). Vui lòng chỉ định người đại diện khác trước khi xóa."
        if is_ajax:
            return JSONResponse({"ok": False, "message": msg}, status_code=400)
        return RedirectResponse(f"{back}?error={urllib.parse.quote(msg)}", status_code=303)

    # Delete staff salary profile & payroll data
    db._execute_query("DELETE FROM public.staff_payroll_months WHERE user_id = %s", (user_id,), commit=True)
    db._execute_query("DELETE FROM public.staff_salary_profiles WHERE user_id = %s", (user_id,), commit=True)

    # Delete or unassign user
    db._execute_query("DELETE FROM public.users WHERE id = %s", (user_id,), commit=True)

    name = user_row.get("full_name") or user_row.get("username")
    msg = f"Đã xóa hồ sơ nhân sự '{name}'."
    if is_ajax:
        return JSONResponse({"ok": True, "message": msg})
    return RedirectResponse(f"{back}?message={urllib.parse.quote(msg)}", status_code=303)


# ==============================================================================
# HỢP ĐỒNG LAO ĐỘNG, BẰNG LÁI & QUÁ TRÌNH CÔNG TÁC
# ==============================================================================

@router.get("/{user_id}/records")
async def get_staff_records(
    user_id: int,
    _: dict = Depends(require_menu("staff")),
):
    contracts = db._execute_query(
        "SELECT * FROM public.staff_contracts WHERE user_id = %s ORDER BY start_date DESC NULLS LAST",
        (user_id,), fetchall=True, commit=False,
    ) or []
    for c in contracts:
        c["salary_rate_fmt"] = _fmt_currency(c.get("salary_rate"))
        c["insurance_rate_fmt"] = _fmt_currency(c.get("insurance_rate"))
        c["salary_rate"] = float(c["salary_rate"]) if c.get("salary_rate") is not None else 0
        c["insurance_rate"] = float(c["insurance_rate"]) if c.get("insurance_rate") is not None else 0
        if c.get("sign_date"): c["sign_date"] = c["sign_date"].strftime("%d/%m/%Y")
        if c.get("start_date"): c["start_date"] = c["start_date"].strftime("%d/%m/%Y")
        if c.get("end_date"): c["end_date"] = c["end_date"].strftime("%d/%m/%Y")
        if c.get("created_at"): c["created_at"] = c["created_at"].strftime("%d/%m/%Y %H:%M")

    licenses = db._execute_query(
        "SELECT * FROM public.staff_licenses WHERE user_id = %s ORDER BY expiry_date ASC NULLS LAST",
        (user_id,), fetchall=True, commit=False,
    ) or []
    for l in licenses:
        if l.get("issued_date"): l["issued_date"] = l["issued_date"].strftime("%d/%m/%Y")
        if l.get("expiry_date"): l["expiry_date"] = l["expiry_date"].strftime("%d/%m/%Y")
        if l.get("created_at"): l["created_at"] = l["created_at"].strftime("%d/%m/%Y %H:%M")

    work_history = db._execute_query(
        "SELECT * FROM public.staff_work_history WHERE user_id = %s ORDER BY event_date DESC",
        (user_id,), fetchall=True, commit=False,
    ) or []
    for w in work_history:
        if w.get("event_date"): w["event_date"] = w["event_date"].strftime("%d/%m/%Y")
        if w.get("created_at"): w["created_at"] = w["created_at"].strftime("%d/%m/%Y %H:%M")

    return JSONResponse({
        "ok": True,
        "contracts": contracts,
        "licenses": licenses,
        "work_history": work_history,
    })


@router.post("/{user_id}/contracts/create")
async def create_staff_contract(
    user_id: int,
    contract_number: str = Form(""),
    contract_type: str = Form("xác định thời hạn"),
    sign_date: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    salary_rate: str = Form("0"),
    insurance_rate: str = Form("0"),
    note: str = Form(""),
    _: dict = Depends(require_menu("staff")),
):
    db._execute_query(
        """
        INSERT INTO public.staff_contracts (
            user_id, contract_number, contract_type, sign_date, start_date, 
            end_date, salary_rate, insurance_rate, note, status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
        """,
        (
            user_id, contract_number.strip() or None, contract_type,
            sign_date or None, start_date or None, end_date or None,
            _clean_numeric(salary_rate), _clean_numeric(insurance_rate),
            note.strip() or None
        ),
        commit=True,
    )
    return JSONResponse({"ok": True, "message": "Đã lưu hợp đồng lao động."})


@router.post("/contracts/{contract_id}/delete")
async def delete_staff_contract(
    contract_id: int,
    _: dict = Depends(require_menu("staff")),
):
    db._execute_query("DELETE FROM public.staff_contracts WHERE id = %s", (contract_id,), commit=True)
    return JSONResponse({"ok": True, "message": "Đã xóa hợp đồng."})


@router.post("/{user_id}/licenses/create")
async def create_staff_license(
    user_id: int,
    license_type: str = Form("GPLX B2"),
    license_number: str = Form(""),
    issued_date: str = Form(""),
    expiry_date: str = Form(""),
    issued_by: str = Form(""),
    note: str = Form(""),
    _: dict = Depends(require_menu("staff")),
):
    db._execute_query(
        """
        INSERT INTO public.staff_licenses (
            user_id, license_type, license_number, issued_date, expiry_date, issued_by, note
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id, license_type, license_number.strip() or None,
            issued_date or None, expiry_date or None,
            issued_by.strip() or None, note.strip() or None
        ),
        commit=True,
    )
    return JSONResponse({"ok": True, "message": "Đã lưu bằng lái/chứng chỉ."})


@router.post("/licenses/{license_id}/delete")
async def delete_staff_license(
    license_id: int,
    _: dict = Depends(require_menu("staff")),
):
    db._execute_query("DELETE FROM public.staff_licenses WHERE id = %s", (license_id,), commit=True)
    return JSONResponse({"ok": True, "message": "Đã xóa bằng lái/chứng chỉ."})


@router.post("/{user_id}/work-history/create")
async def create_staff_work_history(
    user_id: int,
    event_type: str = Form("bổ nhiệm"),
    event_date: str = Form(...),
    old_value: str = Form(""),
    new_value: str = Form(""),
    decision_number: str = Form(""),
    note: str = Form(""),
    user: dict = Depends(require_menu("staff")),
):
    db._execute_query(
        """
        INSERT INTO public.staff_work_history (
            user_id, event_type, event_date, old_value, new_value, decision_number, note, created_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id, event_type, event_date, old_value.strip() or None,
            new_value.strip() or None, decision_number.strip() or None,
            note.strip() or None, user.get("id")
        ),
        commit=True,
    )
    return JSONResponse({"ok": True, "message": "Đã lưu thông tin quá trình công tác."})


@router.post("/work-history/{hist_id}/delete")
async def delete_staff_work_history(
    hist_id: int,
    _: dict = Depends(require_menu("staff")),
):
    db._execute_query("DELETE FROM public.staff_work_history WHERE id = %s", (hist_id,), commit=True)
    return JSONResponse({"ok": True, "message": "Đã xóa lịch sử công tác."})
