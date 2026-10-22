"""Monthly staff payroll, based on the company's October 2026 workbook."""

from decimal import Decimal, ROUND_HALF_UP

from backend.services.database import db


COMPANY_TAX_CODE = "2500774911"
EMPLOYEE_INSURANCE_RATE = Decimal("0.105")
EMPLOYER_INSURANCE_RATE = Decimal("0.215")




def staff_members(company_id: int | None = None) -> list[dict]:
    if not company_id:
        return []
    return db._execute_query("""
        SELECT u.id, u.username, u.full_name, u.nickname
          FROM public.users u
          JOIN public.invoice_companies c ON c.id = ANY(u.company_ids)
         WHERE c.id = %s AND u.full_name IS NOT NULL
         ORDER BY u.full_name
    """, (company_id,), fetchall=True, commit=False) or []


def payroll_rows(month_start, company_id: int | None = None) -> list[dict]:
    if not company_id:
        return []
    rows = db._execute_query("""
        SELECT u.id AS user_id, u.username, u.full_name, p.job_title, p.department,
               p.base_salary, p.responsibility_allowance, p.meal_allowance,
               p.phone_allowance, p.insurance_salary,
               COALESCE(m.work_days, 0) AS work_days,
               COALESCE(m.standard_days, 26) AS standard_days,
               COALESCE(m.advance_amount, 0) AS advance_amount,
               COALESCE(m.trip_wage, 0) AS trip_wage,
               COALESCE(m.bonus_amount, 0) AS bonus_amount,
               COALESCE(m.overtime_pay, 0) AS overtime_pay,
               COALESCE(m.tax_deduction, 0) AS tax_deduction,
               COALESCE(m.other_deduction, 0) AS other_deduction,
               COALESCE(m.union_fee, 0) AS union_fee,
               COALESCE(m.note, '') AS note,
               m.user_id IS NOT NULL AS has_month_entry
          FROM public.staff_salary_profiles p
          JOIN public.users u ON u.id = p.user_id
          JOIN public.invoice_companies c ON c.id = ANY(u.company_ids)
          LEFT JOIN public.staff_payroll_months m
            ON m.user_id = u.id AND m.month = %s
         WHERE c.id = %s
         ORDER BY CASE WHEN lower(p.job_title) LIKE '%%giám đốc%%' THEN 0 ELSE 1 END,
                  u.full_name
    """, (month_start, company_id), fetchall=True, commit=False) or []
    for row in rows:
        calculate_payroll_row(row)
    return rows


def calculate_payroll_row(row: dict) -> dict:
    """Use the same salary calculation for display and locked advance checks."""
    work = Decimal(row["work_days"])
    standard = Decimal(row["standard_days"])
    factor = min(work / standard, Decimal(1)) if standard > 0 else Decimal(0)
    fixed = sum((Decimal(row[key]) for key in
                 ("base_salary", "responsibility_allowance", "meal_allowance", "phone_allowance")), Decimal(0))
    row["work_factor"] = factor
    prorated_fixed = fixed * factor
    row["fixed_pay"] = prorated_fixed.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    row["gross_pay"] = (prorated_fixed + sum((Decimal(row[key]) for key in
        ("trip_wage", "bonus_amount", "overtime_pay")), Decimal(0))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    insurance_salary = Decimal(row["insurance_salary"]) if row["has_month_entry"] else Decimal(0)
    row["employee_insurance"] = (insurance_salary * EMPLOYEE_INSURANCE_RATE).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    row["employer_insurance"] = (insurance_salary * EMPLOYER_INSURANCE_RATE).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    row["net_pay"] = row["gross_pay"] - row["employee_insurance"] - sum((Decimal(row[key]) for key in
        ("advance_amount", "tax_deduction", "other_deduction", "union_fee")), Decimal(0))
    return row


def get_staff_payroll_item(staff_id: int, month_start, company_id: int | None = None) -> dict | None:
    rows = payroll_rows(month_start, company_id)
    for r in rows:
        if r["user_id"] == staff_id:
            return r
    user_row = db._execute_query(
        "SELECT id AS user_id, username, full_name FROM public.users WHERE id = %s",
        (staff_id,), fetch=True, commit=False
    )
    if not user_row:
        return None
    return {
        "user_id": user_row["user_id"],
        "username": user_row["username"],
        "full_name": user_row["full_name"],
        "job_title": "",
        "department": "",
        "base_salary": Decimal("0"),
        "responsibility_allowance": Decimal("0"),
        "meal_allowance": Decimal("0"),
        "phone_allowance": Decimal("0"),
        "insurance_salary": Decimal("0"),
        "work_days": Decimal("0"),
        "standard_days": Decimal("26"),
        "advance_amount": Decimal("0"),
        "trip_wage": Decimal("0"),
        "bonus_amount": Decimal("0"),
        "overtime_pay": Decimal("0"),
        "tax_deduction": Decimal("0"),
        "other_deduction": Decimal("0"),
        "union_fee": Decimal("0"),
        "note": "",
        "has_month_entry": False,
        "has_profile": False,
        "work_factor": Decimal("0"),
        "fixed_pay": Decimal("0"),
        "gross_pay": Decimal("0"),
        "employee_insurance": Decimal("0"),
        "employer_insurance": Decimal("0"),
        "net_pay": Decimal("0"),
    }


def save_profile(user_id: int, values: dict) -> None:
    columns = ("job_title", "department", "base_salary", "responsibility_allowance",
               "meal_allowance", "phone_allowance", "insurance_salary")
    db._execute_query("""
        INSERT INTO public.staff_salary_profiles
            (user_id, job_title, department, base_salary, responsibility_allowance,
             meal_allowance, phone_allowance, insurance_salary)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            job_title = EXCLUDED.job_title, department = EXCLUDED.department,
            base_salary = EXCLUDED.base_salary,
            responsibility_allowance = EXCLUDED.responsibility_allowance,
            meal_allowance = EXCLUDED.meal_allowance,
            phone_allowance = EXCLUDED.phone_allowance,
            insurance_salary = EXCLUDED.insurance_salary, updated_at = now()
    """, (user_id, *(values[key] for key in columns)))


def update_staff_job_title(user_id: int, job_title: str, department: str = "") -> None:
    job_title = (job_title or "").strip()[:120]
    department = (department or "").strip()[:120]
    if not job_title:
        raise ValueError("Chức vụ không được để trống.")
    db._execute_query("""
        INSERT INTO public.staff_salary_profiles
            (user_id, job_title, department, base_salary, responsibility_allowance,
             meal_allowance, phone_allowance, insurance_salary)
        VALUES (%s, %s, %s, 0, 0, 0, 0, 0)
        ON CONFLICT (user_id) DO UPDATE SET
            job_title = EXCLUDED.job_title,
            department = EXCLUDED.department,
            updated_at = now()
    """, (user_id, job_title, department))


def save_month(user_id: int, month_start, values: dict) -> None:
    columns = ("work_days", "standard_days", "advance_amount", "trip_wage",
               "bonus_amount", "overtime_pay", "tax_deduction", "other_deduction",
               "union_fee", "note")
    db._execute_query("""
        INSERT INTO public.staff_payroll_months
            (user_id, month, work_days, standard_days, advance_amount, trip_wage,
             bonus_amount, overtime_pay, tax_deduction, other_deduction, union_fee, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, month) DO UPDATE SET
            work_days = EXCLUDED.work_days, standard_days = EXCLUDED.standard_days,
            advance_amount = EXCLUDED.advance_amount, trip_wage = EXCLUDED.trip_wage,
            bonus_amount = EXCLUDED.bonus_amount, overtime_pay = EXCLUDED.overtime_pay,
            tax_deduction = EXCLUDED.tax_deduction,
            other_deduction = EXCLUDED.other_deduction,
            union_fee = EXCLUDED.union_fee, note = EXCLUDED.note, updated_at = now()
    """, (user_id, month_start, *(values[key] for key in columns)))
