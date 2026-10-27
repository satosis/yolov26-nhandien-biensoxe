"""Monthly staff payroll, based on the company's October 2026 workbook."""

import calendar
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import json

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
               COALESCE(m.work_days, att.actual_days, 0) AS work_days,
               COALESCE(m.standard_days, att.standard_days, 26) AS standard_days,
               COALESCE(m.advance_amount, 0) AS advance_amount,
               COALESCE(m.trip_wage, 0) AS trip_wage,
               COALESCE(m.bonus_amount, 0) AS bonus_amount,
               COALESCE(m.overtime_pay, 0) AS overtime_pay,
               COALESCE(m.tax_deduction, 0) AS tax_deduction,
               COALESCE(m.other_deduction, 0) AS other_deduction,
               COALESCE(m.union_fee, 0) AS union_fee,
               COALESCE(m.note, att.note, '') AS note,
               COALESCE(att.daily_details, '{}'::jsonb) AS daily_details,
               m.user_id IS NOT NULL AS has_month_entry
          FROM public.staff_salary_profiles p
          JOIN public.users u ON u.id = p.user_id
          JOIN public.invoice_companies c ON c.id = ANY(u.company_ids)
          LEFT JOIN public.staff_payroll_months m
            ON m.user_id = u.id AND m.month = %s
          LEFT JOIN public.staff_attendance att
            ON att.user_id = u.id AND att.month = %s
         WHERE c.id = %s
         ORDER BY CASE WHEN lower(p.job_title) LIKE '%%giám đốc%%' THEN 0 ELSE 1 END,
                  u.full_name
    """, (month_start, month_start, company_id), fetchall=True, commit=False) or []
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


def parse_attendance_mark(mark: str) -> tuple[float, float, float]:
    """
    Returns (work_days, paid_leave, unpaid_leave) based on template symbols:
    - X, x, NL: 1.0 công
    - X/2, x/2, NL/2: 0.5 công
    - P: 1.0 phép năm
    - P/2: 0.5 phép năm
    - L: 1.0 ngày lễ
    - H: 1.0 hội nghị
    - NB: 1.0 nghỉ bù
    - T: 1.0 tai nạn
    - TN: 1.0 thâm niên
    - KL: 1.0 nghỉ không hưởng lương
    - OM: 1.0 ốm
    - CO: 1.0 con ốm
    - TS: 1.0 thai sản
    - LD, LĐ: 1.0 nghĩa vụ
    """
    m = (mark or "").strip().upper()
    if not m or m in ("-", "O", "0"):
        return (0.0, 0.0, 0.0)
    if m in ("X", "NL"):
        return (1.0, 0.0, 0.0)
    if m in ("X/2", "NL/2"):
        return (0.5, 0.0, 0.0)
    if m == "P":
        return (1.0, 1.0, 0.0)
    if m == "P/2":
        return (0.5, 0.5, 0.0)
    if m in ("L", "H", "NB", "T", "TN"):
        return (1.0, 1.0, 0.0)
    if m in ("KL", "OM", "CO", "TS", "LD", "LĐ"):
        return (0.0, 0.0, 1.0)
    try:
        val = float(m)
        return (val, 0.0, 0.0)
    except ValueError:
        return (0.0, 0.0, 0.0)


def get_timesheet_matrix(month_str: str, company_id: int | None = None) -> dict:
    if not month_str or len(month_str) < 7:
        month_str = date.today().strftime("%Y-%m")
    y, m = map(int, month_str[:7].split("-"))
    num_days = calendar.monthrange(y, m)[1]

    vn_weekdays = {0: "T2", 1: "T3", 2: "T4", 3: "T5", 4: "T6", 5: "T7", 6: "CN"}
    days_info = []
    for d in range(1, num_days + 1):
        dt = date(y, m, d)
        wd_num = dt.weekday()
        wd_name = vn_weekdays[wd_num]
        days_info.append({
            "day": d,
            "weekday": wd_name,
            "is_weekend": wd_num in (5, 6),
            "is_sunday": wd_num == 6,
            "is_saturday": wd_num == 5,
            "date_str": dt.strftime("%Y-%m-%d"),
        })

    month_start = f"{month_str[:7]}-01"
    staff_rows = db._execute_query("""
        SELECT u.id AS user_id, u.username, u.full_name,
               p.job_title, p.department,
               COALESCE(att.standard_days, 26) AS standard_days,
               COALESCE(att.actual_days, m.work_days, 0) AS actual_days,
               COALESCE(att.paid_leave_days, 0) AS paid_leave_days,
               COALESCE(att.unpaid_leave_days, 0) AS unpaid_leave_days,
               COALESCE(att.overtime_hours, 0) AS overtime_hours,
               COALESCE(att.note, '') AS att_note,
               COALESCE(att.daily_details, '{}'::jsonb) AS daily_details
          FROM public.users u
          JOIN public.invoice_companies c ON c.id = ANY(u.company_ids)
          LEFT JOIN public.staff_salary_profiles p ON p.user_id = u.id
          LEFT JOIN public.staff_attendance att ON att.user_id = u.id AND att.month = %s
          LEFT JOIN public.staff_payroll_months m ON m.user_id = u.id AND m.month = %s
         WHERE c.id = %s AND u.full_name IS NOT NULL
         ORDER BY CASE WHEN lower(COALESCE(p.job_title, '')) LIKE '%%giám đốc%%' THEN 0 ELSE 1 END,
                  u.full_name
    """, (month_start, month_start, company_id), fetchall=True, commit=False) or []

    processed_rows = []
    for s in staff_rows:
        details = s["daily_details"]
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except:
                details = {}
        elif not isinstance(details, dict):
            details = {}

        calc_actual = 0.0
        calc_paid_leave = 0.0
        calc_unpaid_leave = 0.0
        calc_ot = 0.0
        daily_cells = []

        for d in range(1, num_days + 1):
            day_str = str(d)
            day_data = details.get(day_str, {})
            hc_mark = str(day_data.get("hc", "") or "").strip()
            ot_hrs = day_data.get("ot", "")
            try:
                ot_num = float(ot_hrs) if ot_hrs not in ("", None) else 0.0
            except:
                ot_num = 0.0

            w, p, u = parse_attendance_mark(hc_mark)
            calc_actual += w
            calc_paid_leave += p
            calc_unpaid_leave += u
            calc_ot += ot_num

            daily_cells.append({
                "day": d,
                "hc": hc_mark,
                "ot": ot_hrs if ot_hrs not in ("", 0, "0", None) else "",
                "is_sunday": days_info[d - 1]["is_sunday"],
                "is_saturday": days_info[d - 1]["is_saturday"],
            })

        final_actual = calc_actual if details else float(s["actual_days"])
        final_paid = calc_paid_leave if details else float(s["paid_leave_days"])
        final_unpaid = calc_unpaid_leave if details else float(s["unpaid_leave_days"])
        final_ot = calc_ot if details else float(s["overtime_hours"])

        raw_job_title = (s["job_title"] or "").strip()
        cleaned_job_title = "" if raw_job_title in ("--", "-") else raw_job_title

        processed_rows.append({
            "user_id": s["user_id"],
            "username": s["username"],
            "full_name": s["full_name"],
            "job_title": cleaned_job_title,
            "department": s["department"] or "",
            "standard_days": float(s["standard_days"]),
            "actual_days": final_actual,
            "paid_leave_days": final_paid,
            "unpaid_leave_days": final_unpaid,
            "overtime_hours": final_ot,
            "att_note": s["att_note"],
            "daily_cells": daily_cells,
            "daily_details": details,
            "ot_status": "Đạt" if final_ot <= 40 else "Vượt trần!",
        })

    return {
        "days_info": days_info,
        "num_days": num_days,
        "rows": processed_rows,
        "month": month_str[:7],
        "year": y,
        "month_num": m,
        "standard_days": 26,
    }


def save_timesheet_matrix(month_str: str, company_id: int | None, updates: list[dict]) -> None:
    month_start = f"{month_str[:7]}-01"
    for item in updates:
        user_id = item.get("user_id")
        if not user_id:
            continue
        details = item.get("daily_details", {})
        note = (item.get("note") or "").strip()

        actual_days = 0.0
        paid_leave = 0.0
        unpaid_leave = 0.0
        overtime_hours = 0.0

        for d_str, d_val in details.items():
            hc = d_val.get("hc", "")
            w, p, u = parse_attendance_mark(hc)
            actual_days += w
            paid_leave += p
            unpaid_leave += u
            try:
                ot = float(d_val.get("ot", 0) or 0)
            except:
                ot = 0.0
            overtime_hours += ot

        standard_days = float(item.get("standard_days", 26))

        db._execute_query("""
            INSERT INTO public.staff_attendance (
                user_id, company_id, month, standard_days, actual_days, 
                paid_leave_days, unpaid_leave_days, overtime_hours, note, daily_details, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, month) DO UPDATE SET
                standard_days = EXCLUDED.standard_days,
                actual_days = EXCLUDED.actual_days,
                paid_leave_days = EXCLUDED.paid_leave_days,
                unpaid_leave_days = EXCLUDED.unpaid_leave_days,
                overtime_hours = EXCLUDED.overtime_hours,
                note = EXCLUDED.note,
                daily_details = EXCLUDED.daily_details,
                updated_at = NOW()
        """, (user_id, company_id, month_start, standard_days, actual_days, paid_leave, unpaid_leave, overtime_hours, note or None, json.dumps(details)), commit=True)

        db._execute_query("""
            INSERT INTO public.staff_payroll_months (
                user_id, month, work_days, standard_days, updated_at
            ) VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, month) DO UPDATE SET
                work_days = EXCLUDED.work_days,
                standard_days = EXCLUDED.standard_days,
                updated_at = NOW()
        """, (user_id, month_start, actual_days, standard_days), commit=True)

