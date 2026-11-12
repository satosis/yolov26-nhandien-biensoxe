"""Accounting entries and summary for staff payroll (TT133)."""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import unicodedata


EMPLOYER_RATES = {"3383": Decimal("0.175"), "3384": Decimal("0.03"), "3385": Decimal("0.01")}   # tổng 21,5%
EMPLOYEE_RATES = {"3383": Decimal("0.08"), "3384": Decimal("0.015"), "3385": Decimal("0.01")}   # tổng 10,5%
ACCOUNT_NAMES = {
    "1542": "Chi phí nhân công trực tiếp (lái xe)",
    "6422": "Chi phí quản lý doanh nghiệp",
    "3341": "Phải trả công nhân viên",
    "3382": "Kinh phí công đoàn",
    "3383": "Bảo hiểm xã hội",
    "3384": "Bảo hiểm y tế",
    "3385": "Bảo hiểm thất nghiệp",
    "3388": "Phải trả, phải nộp khác",
    "3335": "Thuế thu nhập cá nhân",
}
MANAGEMENT_WORDS = ("giám đốc", "kế toán", "quản lý", "hành chính", "văn phòng", "trưởng phòng")


def cost_account(row: dict) -> str:
    """Normalize NFC and casefold job_title + department.
    
    If contains any MANAGEMENT_WORDS -> 6422.
    Else if row['is_driver'] -> 1542.
    Else -> 6422.
    """
    title = row.get("job_title") or ""
    dept = row.get("department") or ""
    norm = unicodedata.normalize("NFC", f"{title} {dept}").casefold()
    if any(w in norm for w in MANAGEMENT_WORDS):
        return "6422"
    if row.get("is_driver"):
        return "1542"
    return "6422"


def _split(total: Decimal | int, rates: dict) -> dict[str, int]:
    """Split total according to rate proportions.
    
    3384 and 3385 rounded ROUND_HALF_UP to VND, 3383 = total - remainder so sum equals total.
    """
    tot = Decimal(str(total or 0))
    if tot == 0:
        return {k: 0 for k in rates}
    total_rate = sum(rates.values(), Decimal(0))
    if total_rate <= 0:
        return {k: 0 for k in rates}
    amt_3384 = int((tot * rates.get("3384", Decimal(0)) / total_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    amt_3385 = int((tot * rates.get("3385", Decimal(0)) / total_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    amt_3383 = int(tot) - amt_3384 - amt_3385
    return {"3383": amt_3383, "3384": amt_3384, "3385": amt_3385}


def month_payroll_summary(rows: list[dict]) -> dict:
    """Aggregate payroll numbers for rows with has_month_entry True."""
    active_rows = [r for r in rows if r.get("has_month_entry")]
    gross = {"1542": 0, "6422": 0}
    employer = {"1542": 0, "6422": 0}
    employee_total = 0
    pit = 0
    union_fee = 0
    other_deduction = 0
    advance = 0
    net = 0

    for r in active_rows:
        acct = cost_account(r)
        g = int(round(Decimal(str(r.get("gross_pay") or 0))))
        empr = int(round(Decimal(str(r.get("employer_insurance") or 0))))
        empe = int(round(Decimal(str(r.get("employee_insurance") or 0))))
        p = int(round(Decimal(str(r.get("tax_deduction") or 0))))
        u = int(round(Decimal(str(r.get("union_fee") or 0))))
        o = int(round(Decimal(str(r.get("other_deduction") or 0))))
        adv = int(round(Decimal(str(r.get("advance_amount") or 0))))
        n = int(round(Decimal(str(r.get("net_pay") or 0))))

        gross[acct] += g
        employer[acct] += empr
        employee_total += empe
        pit += p
        union_fee += u
        other_deduction += o
        advance += adv
        net += n

    gross_total = gross["1542"] + gross["6422"]
    employer_total = employer["1542"] + employer["6422"]
    employer_by_ins = _split(Decimal(employer_total), EMPLOYER_RATES)
    employee_by_ins = _split(Decimal(employee_total), EMPLOYEE_RATES)

    return {
        "gross": gross,
        "employer": employer,
        "employer_by_ins": employer_by_ins,
        "employee_by_ins": employee_by_ins,
        "pit": pit,
        "union_fee": union_fee,
        "other_deduction": other_deduction,
        "advance": advance,
        "net": net,
        "gross_total": gross_total,
        "employer_total": employer_total,
        "employee_total": employee_total,
        "staff_count": len(active_rows),
    }


def month_journal(rows: list[dict]) -> dict:
    """Generate accounting journal entries from staff payroll rows."""
    summary = month_payroll_summary(rows)
    entries = []

    # 1. Tiền lương phải trả bộ phận lái xe
    if summary["gross"]["1542"] > 0:
        entries.append({
            "desc": "Tiền lương phải trả bộ phận lái xe",
            "debit": "1542",
            "credit": "3341",
            "amount": summary["gross"]["1542"],
        })

    # 2. Tiền lương phải trả bộ phận quản lý
    if summary["gross"]["6422"] > 0:
        entries.append({
            "desc": "Tiền lương phải trả bộ phận quản lý",
            "debit": "6422",
            "credit": "3341",
            "amount": summary["gross"]["6422"],
        })

    # 3. BHXH, BHYT, BHTN công ty đóng (21,5%) – bộ phận lái xe
    if summary["employer"]["1542"] > 0:
        emp_1542 = _split(Decimal(summary["employer"]["1542"]), EMPLOYER_RATES)
        for code in ("3383", "3384", "3385"):
            amt = emp_1542[code]
            if amt > 0:
                entries.append({
                    "desc": "BHXH, BHYT, BHTN công ty đóng (21,5%) – bộ phận lái xe",
                    "debit": "1542",
                    "credit": code,
                    "amount": amt,
                })

    # 4. BHXH, BHYT, BHTN công ty đóng (21,5%) – bộ phận quản lý
    if summary["employer"]["6422"] > 0:
        emp_6422 = _split(Decimal(summary["employer"]["6422"]), EMPLOYER_RATES)
        for code in ("3383", "3384", "3385"):
            amt = emp_6422[code]
            if amt > 0:
                entries.append({
                    "desc": "BHXH, BHYT, BHTN công ty đóng (21,5%) – bộ phận quản lý",
                    "debit": "6422",
                    "credit": code,
                    "amount": amt,
                })

    # 5. BHXH, BHYT, BHTN trừ lương người lao động (10,5%)
    for code in ("3383", "3384", "3385"):
        amt = summary["employee_by_ins"][code]
        if amt > 0:
            entries.append({
                "desc": "BHXH, BHYT, BHTN trừ lương người lao động (10,5%)",
                "debit": "3341",
                "credit": code,
                "amount": amt,
            })

    # 6. Thuế TNCN khấu trừ từ lương
    if summary["pit"] > 0:
        entries.append({
            "desc": "Thuế TNCN khấu trừ từ lương",
            "debit": "3341",
            "credit": "3335",
            "amount": summary["pit"],
        })

    # 7. Đoàn phí công đoàn trừ lương
    if summary["union_fee"] > 0:
        entries.append({
            "desc": "Đoàn phí công đoàn trừ lương",
            "debit": "3341",
            "credit": "3382",
            "amount": summary["union_fee"],
        })

    # 8. Các khoản khác trừ vào lương
    if summary["other_deduction"] > 0:
        entries.append({
            "desc": "Các khoản khác trừ vào lương",
            "debit": "3341",
            "credit": "3388",
            "amount": summary["other_deduction"],
        })

    net_payable = summary["net"] + summary["advance"]
    return {
        "entries": entries,
        "summary": summary,
        "net_payable": net_payable,
    }


def period_payroll(company_id: int | None, date_from: date, date_to: date) -> dict:
    """Aggregate payroll summaries for all months with 1st of month in [date_from, date_to]."""
    from frontend.modules.payroll.staff_payroll import payroll_rows

    gross = {"1542": 0, "6422": 0}
    employer = {"1542": 0, "6422": 0}
    employee_total = 0
    pit = 0
    union_fee = 0
    other_deduction = 0
    advance = 0
    net = 0
    gross_total = 0
    employer_total = 0
    staff_count = 0
    months = []

    if not company_id or not date_from or not date_to or date_from > date_to:
        return {
            "gross": gross,
            "employer": employer,
            "employer_by_ins": _split(Decimal(0), EMPLOYER_RATES),
            "employee_by_ins": _split(Decimal(0), EMPLOYEE_RATES),
            "pit": 0,
            "union_fee": 0,
            "other_deduction": 0,
            "advance": 0,
            "net": 0,
            "gross_total": 0,
            "employer_total": 0,
            "employee_total": 0,
            "staff_count": 0,
            "months": [],
        }

    cur = date(date_from.year, date_from.month, 1)
    while cur <= date_to:
        rows = payroll_rows(cur, company_id)
        s = month_payroll_summary(rows)
        if s["staff_count"] > 0:
            months.append(f"{cur.month:02d}/{cur.year}")
            gross["1542"] += s["gross"]["1542"]
            gross["6422"] += s["gross"]["6422"]
            employer["1542"] += s["employer"]["1542"]
            employer["6422"] += s["employer"]["6422"]
            employee_total += s["employee_total"]
            pit += s["pit"]
            union_fee += s["union_fee"]
            other_deduction += s["other_deduction"]
            advance += s["advance"]
            net += s["net"]
            gross_total += s["gross_total"]
            employer_total += s["employer_total"]
            staff_count += s["staff_count"]

        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)

    employer_by_ins = _split(Decimal(employer_total), EMPLOYER_RATES)
    employee_by_ins = _split(Decimal(employee_total), EMPLOYEE_RATES)

    return {
        "gross": gross,
        "employer": employer,
        "employer_by_ins": employer_by_ins,
        "employee_by_ins": employee_by_ins,
        "pit": pit,
        "union_fee": union_fee,
        "other_deduction": other_deduction,
        "advance": advance,
        "net": net,
        "gross_total": gross_total,
        "employer_total": employer_total,
        "employee_total": employee_total,
        "staff_count": staff_count,
        "months": months,
    }
