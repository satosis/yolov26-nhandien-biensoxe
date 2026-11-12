"""Monthly staff payroll, based on the company's October 2026 workbook."""

import calendar
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
import json
import unicodedata
from zoneinfo import ZoneInfo

from backend.services.database import db
from frontend.modules.payroll.revenue_bonus import month_revenue


COMPANY_TAX_CODE = "2500774911"
EMPLOYEE_INSURANCE_RATE = Decimal("0.105")
EMPLOYER_INSURANCE_RATE = Decimal("0.215")
VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NIGHT_SHIFT_START = time(22, 0)
NIGHT_SHIFT_END = time(6, 0)
# Ca sáng 8-12h and ca chiều 14-18h are the two halves of the day shift. A time between shifts
# goes to the nearer day half, with the lunch break split at 13h.
DAY_HALVES_SPLIT = time(13, 0)
SHIFT_LABELS = {"morning": "ca sáng", "afternoon": "ca chiều", "night": "ca đêm"}
# Overtime multipliers from the pay decision (Điều 98 BLLĐ 2019): weekday 150%, weekly rest day
# (Sunday) 200%, public holiday (marked L on the timesheet) 300%.
OVERTIME_RATES = {"weekday": Decimal("1.5"), "sunday": Decimal("2"), "holiday": Decimal("3")}
# Phụ cấp ăn ca of drivers = công ca ngày x 30.000 instead of a fixed monthly PC tiền ăn
# (ca đêm công is not counted: night shift has its own phụ cấp ca đêm).
MEAL_PER_DAY_ROLES = ("tài xế", "lái xe")
DAY_ALLOWANCE_PER_DAY = Decimal("30000")
# Phụ cấp điện thoại of drivers is paid the same way: công ca ngày x 30.000 instead of a fixed monthly
# PC điện thoại (other staff keep the fixed amount from their salary profile).
PHONE_ALLOWANCE_PER_DAY = Decimal("30000")
# Ca đêm is paid 130% of the normal-day hourly wage (Điều 98 khoản 2 BLLĐ 2019). The 100% is already
# in the fixed pay because night hours count as công (8h = 1 công), so phụ cấp ca đêm is the extra 30%:
# giờ ca đêm x lương giờ cơ bản x 30%.
NIGHT_PREMIUM_RATE = Decimal("0.3")
# Work on Sunday, the weekly rest day (Điều 98 BLLĐ 2019): at least 200% of the normal-day hourly wage
# by day, 250% at night (22h-6h: 200% + 30% night + 20% night overtime). When the week has a nghỉ bù
# (NB) day, the Sunday is an ordinary working day and is paid 100% like any other.
SUNDAY_DAY_RATE = Decimal("2")
SUNDAY_NIGHT_RATE = Decimal("2.5")
COMPENSATORY_REST_MARK = "NB"
# Drivers (the director too, as he drives a vehicle) earn a bonus of 1% of the revenue (before VAT)
# their vehicle ran in the month.
REVENUE_BONUS_RATE = Decimal("0.01")
# Staff assigned a vehicle in transport_drivers drive it whatever their job title says (the director
# also drives truck 6), so they are paid like drivers. Needs the salary profile aliased as "p".
HAS_VEHICLE_SQL = """EXISTS (SELECT 1 FROM public.transport_drivers td
                               JOIN public.users du ON lower(du.username) = lower(td.driver_code)
                              WHERE du.id = p.user_id AND td.vehicle_id IS NOT NULL) AS has_vehicle"""




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
    rows = db._execute_query(f"""
        SELECT u.id AS user_id, u.username, u.full_name, p.job_title, p.department,
               p.base_salary, p.responsibility_allowance, p.meal_allowance,
               p.phone_allowance, p.insurance_salary, {HAS_VEHICLE_SQL},
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
               COALESCE(att.daily_details, '{{}}'::jsonb) AS daily_details,
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
    revenue = month_revenue(month_start, [r["username"] for r in rows if is_driver(r)])
    for row in rows:
        row.update(revenue.get(row["username"].casefold(), {"trip_count": 0, "revenue_net": Decimal(0)}))
        details = _daily_details(row["daily_details"])
        row["overtime_breakdown"] = calculate_overtime_pay(
            details, month_start, monthly_fixed_pay(row), Decimal(row["standard_days"]))
        row["night_hours"] = night_hours_in(details)
        row["sunday_breakdown"] = calculate_sunday_work(details, month_start)
        row["sunday_day_hours"] = row["sunday_breakdown"]["day_hours"]
        row["sunday_night_hours"] = row["sunday_breakdown"]["night_hours"]
        calculate_payroll_row(row)
    return rows


def is_driver(profile: dict) -> bool:
    """Job title or department says tài xế / lái xe, or the staff member is assigned a vehicle."""
    if profile.get("has_vehicle"):
        return True
    text =unicodedata.normalize("NFC", f"{profile.get('job_title') or ''} {profile.get('department') or ''}").casefold()
    return any(role in text for role in MEAL_PER_DAY_ROLES)


def meal_paid_per_day(profile: dict) -> bool:
    """Drivers get tiền ăn per day worked (phụ cấp ăn ca), not a fixed monthly amount."""
    return is_driver(profile)


def phone_paid_per_day(profile: dict) -> bool:
    """Drivers get phụ cấp điện thoại per day worked, not a fixed monthly amount."""
    return is_driver(profile)


def monthly_fixed_pay(profile: dict) -> Decimal:
    """Lương thỏa thuận cố định: base salary plus the fixed allowances, as shown on the payslip.

    The fixed PC tiền ăn and PC điện thoại are left out for staff whose allowance is paid per day worked.
    """
    keys = ["base_salary", "responsibility_allowance"]
    if not phone_paid_per_day(profile):
        keys.append("phone_allowance")
    if not meal_paid_per_day(profile):
        keys.append("meal_allowance")
    return sum((Decimal(profile[key]) for key in keys), Decimal(0))


def basic_hourly_rate(fixed_pay: Decimal, standard_days: Decimal) -> Decimal:
    """Mức lương giờ cơ bản: fixed pay / (standard days x 8h); shared by overtime and night allowance."""
    return fixed_pay / (standard_days * 8) if standard_days > 0 else Decimal(0)


def night_hours_in(details: dict) -> Decimal:
    """Total ca đêm hours on the timesheet (X = 8h, X/2 = 4h, or a number of hours)."""
    return sum((Decimal(str(_night_hours(cell.get("cd")))) for cell in (details or {}).values()
                if isinstance(cell, dict)), Decimal(0))


def _worked_day_hours(mark) -> Decimal:
    """Hours really worked on a day-shift mark: X = 8h, X/2 = 4h, a number = công x 8h.

    Leave and holiday marks (P, L, NB, ...) are paid days off, not work, so they count as 0.
    """
    text = str(mark or "").strip().upper().replace(",", ".")
    if text in ("X", "NL"):
        return Decimal(8)
    if text in ("X/2", "NL/2"):
        return Decimal(4)
    try:
        cong = Decimal(text) if text else Decimal(0)
    except ArithmeticError:
        return Decimal(0)
    return min(cong * 8, Decimal(24)) if cong.is_finite() and cong > 0 else Decimal(0)


def calculate_sunday_work(details: dict, month_start) -> dict:
    """Hours worked on Sundays (the weekly rest day) from the timesheet: ban ngày from the day mark,
    ban đêm from the ca đêm row. A Sunday in a week that has a nghỉ bù (NB) day Monday-Saturday of the
    same month counts as an ordinary working day and is left out ("compensated_days").
    """
    if isinstance(month_start, str):
        month_start = date.fromisoformat(month_start[:10])
    details = details or {}
    days_in_month = calendar.monthrange(month_start.year, month_start.month)[1]

    def mark(day: int) -> str:
        cell = details.get(str(day))
        return str((cell.get("hc") if isinstance(cell, dict) else "") or "").strip().upper()

    result = {"day_hours": Decimal(0), "night_hours": Decimal(0), "paid_days": [], "compensated_days": []}
    for day in range(1, days_in_month + 1):
        if date(month_start.year, month_start.month, day).weekday() != 6:
            continue
        cell = details.get(str(day))
        if not isinstance(cell, dict):
            continue
        day_hours = _worked_day_hours(cell.get("hc"))
        night_hours = Decimal(str(_night_hours(cell.get("cd"))))
        if day_hours <= 0 and night_hours <= 0:
            continue
        if any(mark(rest) == COMPENSATORY_REST_MARK for rest in range(max(day - 6, 1), day)):
            result["compensated_days"].append(day)
            continue
        result["day_hours"] += day_hours
        result["night_hours"] += night_hours
        result["paid_days"].append(day)
    return result


def _daily_details(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def calculate_overtime_pay(details: dict, month_start, fixed_pay: Decimal, standard_days: Decimal) -> dict:
    """Overtime pay from the timesheet's per-day "ot" hours, as the company's pay decision sets it:
    fixed pay / (standard days x 8h) x hours x 150% (weekday), 200% (Sunday) or 300% (day marked L).
    """
    if isinstance(month_start, str):
        month_start = date.fromisoformat(month_start[:10])
    days_in_month = calendar.monthrange(month_start.year, month_start.month)[1]
    hours = {kind: Decimal(0) for kind in OVERTIME_RATES}
    for day_text, cell in (details or {}).items():
        try:
            day = int(day_text)
            ot = Decimal(str(cell.get("ot") or 0).replace(",", "."))
        except (ValueError, ArithmeticError, AttributeError):
            continue
        if not 1 <= day <= days_in_month or not ot.is_finite() or ot <= 0:
            continue
        if str(cell.get("hc") or "").strip().upper() == "L":
            kind = "holiday"
        elif date(month_start.year, month_start.month, day).weekday() == 6:
            kind = "sunday"
        else:
            kind = "weekday"
        hours[kind] += ot
    hourly = basic_hourly_rate(fixed_pay, standard_days)
    pay = hourly * sum((hours[kind] * OVERTIME_RATES[kind] for kind in hours), Decimal(0))
    return {
        "hours": hours,
        "total_hours": sum(hours.values(), Decimal(0)),
        "hourly_rate": hourly.quantize(Decimal("1"), rounding=ROUND_HALF_UP),
        "pay": pay.quantize(Decimal("1"), rounding=ROUND_HALF_UP),
    }


def refresh_overtime_pay(user_id: int, month_start: date) -> None:
    """Recompute a month's overtime pay after the salary profile changed (existing month entries only)."""
    row = db._execute_query(f"""
        SELECT p.job_title, p.department,
               p.base_salary, p.responsibility_allowance, p.meal_allowance, p.phone_allowance,
               {HAS_VEHICLE_SQL}, m.standard_days, att.daily_details
          FROM public.staff_payroll_months m
          JOIN public.staff_salary_profiles p ON p.user_id = m.user_id
          JOIN public.staff_attendance att ON att.user_id = m.user_id AND att.month = m.month
         WHERE m.user_id = %s AND m.month = %s
    """, (user_id, month_start), fetch=True, commit=False)
    if not row:
        return
    pay = calculate_overtime_pay(_daily_details(row["daily_details"]), month_start,
                                 monthly_fixed_pay(row), Decimal(row["standard_days"]))["pay"]
    db._execute_query("""
        UPDATE public.staff_payroll_months SET overtime_pay = %s, updated_at = now()
         WHERE user_id = %s AND month = %s
    """, (pay, user_id, month_start), commit=True)


def calculate_payroll_row(row: dict) -> dict:
    """Use the same salary calculation for display and locked advance checks."""
    work = Decimal(row["work_days"])
    standard = Decimal(row["standard_days"])
    factor = min(work / standard, Decimal(1)) if standard > 0 else Decimal(0)
    fixed = monthly_fixed_pay(row)
    row["work_factor"] = factor
    prorated_fixed = fixed * factor
    row["fixed_pay"] = prorated_fixed.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    row["fixed_total"] = fixed
    row["meal_per_day"] = meal_paid_per_day(row)
    night_hours = Decimal(row.get("night_hours") or 0)
    # Công ca ngày = công thực tế minus the công that come from ca đêm (giờ / 8)
    row["day_work_days"] = max(work - night_hours / 8, Decimal(0))
    row["day_allowance_rate"] = DAY_ALLOWANCE_PER_DAY
    row["day_allowance"] = ((row["day_work_days"] * DAY_ALLOWANCE_PER_DAY) if row["meal_per_day"] else Decimal(0)
                            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    row["phone_per_day"] = phone_paid_per_day(row)
    row["phone_allowance_rate"] = PHONE_ALLOWANCE_PER_DAY
    row["phone_day_allowance"] = ((row["day_work_days"] * PHONE_ALLOWANCE_PER_DAY) if row["phone_per_day"] else Decimal(0)
                                  ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    hourly = basic_hourly_rate(fixed, standard)
    row["basic_hourly_rate"] = hourly.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    row["night_allowance"] = (night_hours * hourly * NIGHT_PREMIUM_RATE
                              ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    sunday_day = Decimal(row.get("sunday_day_hours") or 0)
    sunday_night = Decimal(row.get("sunday_night_hours") or 0)
    sunday_cong = (sunday_day + sunday_night) / 8
    # work_days counts the Sunday công too; the payslip shows them apart (Sunday work has its own pay line)
    row["sunday_work_days"] = sunday_cong
    row["weekday_work_days"] = max(work - sunday_cong, Decimal(0))
    # The 100% of Sunday work sits in the fixed pay as công, but only up to the standard days (the pay
    # factor stops at 1). Sunday công beyond that are not paid there, so they get the full 200%/250%
    # here; the rest only the extra: +100% by day, +120% at night (250% - 100% - 30% ca đêm).
    covered = min(sunday_cong, max(min(work, standard) - max(work - sunday_cong, Decimal(0)), Decimal(0)))
    row["sunday_covered_days"] = covered
    unpaid_share = (sunday_cong - covered) / sunday_cong if sunday_cong > 0 else Decimal(0)
    row["sunday_day_rate"] = SUNDAY_DAY_RATE
    row["sunday_night_rate"] = SUNDAY_NIGHT_RATE
    row["sunday_pay"] = (hourly * (sunday_day * (SUNDAY_DAY_RATE - 1 + unpaid_share)
                                   + sunday_night * (SUNDAY_NIGHT_RATE - 1 - NIGHT_PREMIUM_RATE + unpaid_share))
                         ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    row["is_driver"] = is_driver(row)
    row["revenue_net"] = Decimal(row.get("revenue_net") or 0)
    row["revenue_trips"] = int(row.get("trip_count") or 0)
    row["revenue_bonus_rate"] = REVENUE_BONUS_RATE
    row["revenue_bonus"] = ((row["revenue_net"] * REVENUE_BONUS_RATE) if row["is_driver"] else Decimal(0)
                            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    row["gross_pay"] = (prorated_fixed + row["day_allowance"] + row["phone_day_allowance"] + row["night_allowance"] + row["sunday_pay"] + row["revenue_bonus"] + sum((Decimal(row[key]) for key in
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
        "overtime_breakdown": None,
        "night_hours": Decimal("0"),
        "fixed_total": Decimal("0"),
        "is_driver": False,
        "revenue_net": Decimal("0"),
        "revenue_trips": 0,
        "revenue_bonus_rate": REVENUE_BONUS_RATE,
        "revenue_bonus": Decimal("0"),
        "meal_per_day": False,
        "day_work_days": Decimal("0"),
        "day_allowance_rate": DAY_ALLOWANCE_PER_DAY,
        "day_allowance": Decimal("0"),
        "phone_per_day": False,
        "phone_allowance_rate": PHONE_ALLOWANCE_PER_DAY,
        "phone_day_allowance": Decimal("0"),
        "basic_hourly_rate": Decimal("0"),
        "night_allowance": Decimal("0"),
        "sunday_breakdown": None,
        "sunday_work_days": Decimal("0"),
        "sunday_covered_days": Decimal("0"),
        "weekday_work_days": Decimal("0"),
        "sunday_day_hours": Decimal("0"),
        "sunday_night_hours": Decimal("0"),
        "sunday_day_rate": SUNDAY_DAY_RATE,
        "sunday_night_rate": SUNDAY_NIGHT_RATE,
        "sunday_pay": Decimal("0"),
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
         WHERE c.id = %s AND u.full_name IS NOT NULL AND u.username <> 'admin'
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

        calc_day_work = 0.0
        calc_night_work = 0.0
        calc_paid_leave = 0.0
        calc_unpaid_leave = 0.0
        calc_ot = 0.0
        calc_night = 0.0
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

            night_hrs = day_data.get("cd", "")
            night_num = _night_hours(night_hrs)

            w, p, u = parse_attendance_mark(hc_mark)
            calc_day_work += w
            calc_night_work += night_num / 8
            calc_paid_leave += p
            calc_unpaid_leave += u
            calc_ot += ot_num
            calc_night += night_num

            daily_cells.append({
                "day": d,
                "hc": hc_mark,
                "cd": night_hrs if night_hrs not in ("", 0, "0", None) else "",
                "ot": ot_hrs if ot_hrs not in ("", 0, "0", None) else "",
                "is_sunday": days_info[d - 1]["is_sunday"],
                "is_saturday": days_info[d - 1]["is_saturday"],
            })

        # Công TT = công ca ngày + công ca đêm (giờ / 8); shown per row and as a separate total.
        final_day_work = calc_day_work if details else float(s["actual_days"])
        final_night_work = calc_night_work if details else 0.0
        final_actual = final_day_work + final_night_work
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
            "day_work_days": final_day_work,
            "night_work_days": final_night_work,
            "paid_leave_days": final_paid,
            "unpaid_leave_days": final_unpaid,
            "overtime_hours": final_ot,
            "night_hours": calc_night,
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


def _night_hours(value) -> float:
    """Night-shift hours (22h-6h) for one day; X = 8h, X/2 = 4h as in the BCC template."""
    mark = str(value or "").strip().upper()
    if mark == "X":
        return 8.0
    if mark == "X/2":
        return 4.0
    try:
        return max(float(mark.replace(",", ".")), 0.0) if mark else 0.0
    except ValueError:
        return 0.0


def shift_for_time(moment: datetime) -> tuple[str, date]:
    """Shift a clock time falls in, e.g. a toll-station pass: ("morning" | "afternoon" | "night", credited date).

    Ca sáng is 8-12h, ca chiều 14-18h, ca đêm 22h-6h. The night shift is credited to the day it
    started, so 02:30 on the 5th belongs to the night shift of the 4th. Times outside every shift
    (6-8h, 12-14h, 18-22h) are still working time and go to the nearer day half: before 13h is
    ca sáng, from 13h to 22h is ca chiều. Naive datetimes are taken as Vietnam time.
    """
    local = moment.replace(tzinfo=VIETNAM_TZ) if moment.tzinfo is None else moment.astimezone(VIETNAM_TZ)
    clock = local.time()
    if clock >= NIGHT_SHIFT_START:
        return "night", local.date()
    if clock < NIGHT_SHIFT_END:
        return "night", local.date() - timedelta(days=1)
    return ("morning" if clock < DAY_HALVES_SPLIT else "afternoon"), local.date()


def parse_pass_time(day_text: str, time_text: str) -> datetime:
    """Toll-station sheet columns: date as dd/mm/yyyy and pass time as H:i:s (H:i also accepted)."""
    day_text, time_text = str(day_text or "").strip(), str(time_text or "").strip()
    for day_format in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            day = datetime.strptime(day_text, day_format).date()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"Ngày '{day_text}' không đúng dạng dd/mm/yyyy.")
    for time_format in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.combine(day, datetime.strptime(time_text, time_format).time())
        except ValueError:
            continue
    raise ValueError(f"Giờ qua trạm '{time_text}' không đúng dạng H:i:s.")


def mark_shifts_from_times(user_id: int, company_id: int | None, moments: list[datetime]) -> dict:
    """Credit the shift containing each moment (e.g. toll-station passes) on the staff member's timesheet.

    A day with both ca sáng and ca chiều is X (1 công), with only one of them X/2; ca đêm is X (8 giờ).
    Only empty cells are filled; an existing mark (X, P, O, night hours...) is left for a person to
    judge, so send the whole month's passes together.
    """
    day_halves: dict[date, set[str]] = {}
    cells: dict[tuple[date, str], tuple[str, tuple[str, ...]]] = {}
    for shift, work_date in {shift_for_time(moment) for moment in moments}:
        if shift == "night":
            cells[(work_date, "cd")] = ("X", (shift,))
        else:
            day_halves.setdefault(work_date, set()).add(shift)
    for work_date, halves in day_halves.items():
        cells[(work_date, "hc")] = ("X" if len(halves) == 2 else "X/2", tuple(s for s in ("morning", "afternoon") if s in halves))
    if not cells:
        raise ValueError("Chưa có giờ qua trạm nào để chấm công.")
    by_month: dict[date, list[tuple[date, str]]] = {}
    for work_date, cell in sorted(cells):
        by_month.setdefault(work_date.replace(day=1), []).append((work_date, cell))

    marked, kept, saves = [], [], []
    for month_start, month_cells in by_month.items():
        row = db._execute_query("""
            SELECT COALESCE(att.standard_days, 26) AS standard_days,
                   COALESCE(att.note, '') AS note,
                   COALESCE(att.daily_details, '{}'::jsonb) AS daily_details,
                   COALESCE(att.actual_days, m.work_days, 0) AS actual_days
              FROM public.users u
              LEFT JOIN public.staff_attendance att ON att.user_id = u.id AND att.month = %s
              LEFT JOIN public.staff_payroll_months m ON m.user_id = u.id AND m.month = %s
             WHERE u.id = %s
        """, (month_start, month_start, user_id), fetch=True, commit=False)
        if not row:
            raise ValueError("Không tìm thấy nhân sự.")
        details = row["daily_details"]
        if isinstance(details, str):
            details = json.loads(details or "{}")
        if not isinstance(details, dict):
            details = {}
        if not details and row["actual_days"]:
            raise ValueError(f"Tháng {month_start:%m/%Y} đã có tổng công nhập tay, chưa có chi tiết từng ngày nên không tự chấm thêm.")

        changed = False
        for work_date, cell in month_cells:
            mark, shifts = cells[(work_date, cell)]
            day_data = details.setdefault(str(work_date.day), {})
            current = str(day_data.get(cell) or "").strip()
            if current not in ("", "0"):
                kept.append((work_date, shifts, mark, current))
                continue
            day_data[cell] = mark
            marked.append((work_date, shifts, mark))
            changed = True
        if changed:
            saves.append((month_start.strftime("%Y-%m"), {
                "user_id": user_id,
                "daily_details": details,
                "note": row["note"],
                "standard_days": row["standard_days"],
            }))
    for month, update in saves:
        save_timesheet_matrix(month, company_id, [update])

    def shift_text(day: date, shifts: tuple[str, ...]) -> str:
        return f"{' + '.join(SHIFT_LABELS[s] for s in shifts)} {day:%d/%m}"

    message = ("Đã chấm: " + "; ".join(f"{shift_text(day, shifts)} = {mark}" for day, shifts, mark in marked) + "."
               if marked else "Không có ô nào cần chấm thêm.")
    if kept:
        message += (" Giữ nguyên (ô đã có giá trị): "
                    + "; ".join(f"{shift_text(day, shifts)} đang '{current}' (theo giờ qua trạm là {mark})"
                                for day, shifts, mark, current in kept) + ".")
    return {
        "ok": True,
        "marked": [{"work_date": day.isoformat(), "shifts": list(shifts), "mark": mark} for day, shifts, mark in marked],
        "kept": [{"work_date": day.isoformat(), "shifts": list(shifts), "mark": mark, "current": current}
                 for day, shifts, mark, current in kept],
        "message": message,
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
            actual_days += w + _night_hours(d_val.get("cd")) / 8
            paid_leave += p
            unpaid_leave += u
            try:
                ot = float(d_val.get("ot", 0) or 0)
            except:
                ot = 0.0
            overtime_hours += ot

        standard_days = float(item.get("standard_days", 26))
        profile = db._execute_query(f"""
            SELECT p.job_title, p.department, p.base_salary, p.responsibility_allowance, p.meal_allowance,
                   p.phone_allowance, {HAS_VEHICLE_SQL}
              FROM public.staff_salary_profiles p WHERE p.user_id = %s
        """, (user_id,), fetch=True, commit=False)
        overtime_pay = calculate_overtime_pay(
            details, month_start, monthly_fixed_pay(profile) if profile else Decimal(0),
            Decimal(str(standard_days)))["pay"]

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
                user_id, month, work_days, standard_days, overtime_pay, updated_at
            ) VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, month) DO UPDATE SET
                work_days = EXCLUDED.work_days,
                standard_days = EXCLUDED.standard_days,
                overtime_pay = EXCLUDED.overtime_pay,
                updated_at = NOW()
        """, (user_id, month_start, actual_days, standard_days, overtime_pay), commit=True)

