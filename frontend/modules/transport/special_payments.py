"""Service for recording special payments (taxes, insurances, salaries) in cash & bank books."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import logging
import re
from typing import Any
from uuid import uuid4

from backend.services.database import db
from frontend.formatting.money import parse_vnd
from frontend.modules.transport.cash_bank_service import (
    get_company_info,
    next_counted_voucher_no,
    parse_flexible_date,
)
from frontend.modules.transport.date_utils import month_bounds

logger = logging.getLogger(__name__)

TAX_ITEMS = {           # khóa form -> (TK đối ứng theo TT133, tên hiển thị)
    "gtgt": ("33311", "Thuế GTGT"),
    "tndn": ("3334", "Thuế TNDN"),
    "tncn": ("3335", "Thuế TNCN"),
    "monbai": ("3338", "Thuế môn bài"),
    "truocba": ("3339", "Lệ phí trước bạ"),
    "khac": ("3339", "Thuế, phí, lệ phí khác"),
}
INSURANCE_ITEMS = {
    "bhxh": ("3383", "BHXH"),
    "bhyt": ("3384", "BHYT"),
    "bhtn": ("3385", "BHTN"),
    "kpcd": ("3382", "KPCĐ"),
}
SALARY_ACCOUNT = "334"
RENT_ACCOUNT = "6422"
INTEREST_ACCOUNT = "515"
KIND_LABELS = {
    "tax": "nộp thuế",
    "insurance": "nộp bảo hiểm",
    "salary": "trả lương",
    "rent": "thuê nhà văn phòng",
}


def get_company_director_name(company_id: int | None) -> str:
    """Retrieve director name for a company."""
    if not company_id:
        return ""
    row = db._execute_query(
        "SELECT director_name FROM public.invoice_companies WHERE id = %s",
        (int(company_id),),
        fetch=True,
        commit=False,
    ) or {}
    return str(row.get("director_name") or "").strip()


def _insert_row(cursor, inserted_ids: list, company_id: int, book_type: str, v_date: date, voucher_no: str,
                voucher_type: str, description: str, offset_account: str, amount, bank_account_no, bank_name,
                partner_name: str, source_type: str, source_id: str, note: str) -> None:
    """One transport_cash_book line inside the caller's transaction; its id is appended to `inserted_ids`."""
    cursor.execute('''
        INSERT INTO public.transport_cash_book
        (company_id, book_type, entry_date, voucher_date, voucher_no, voucher_type,
         description, offset_account, amount, bank_account_no, bank_name, partner_name,
         partner_code, source_type, source_id, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    ''', (company_id, book_type, v_date, v_date, voucher_no, voucher_type, description, offset_account, amount,
          bank_account_no, bank_name, partner_name, None, source_type, source_id, note))
    row_id = cursor.fetchone()
    if row_id:
        inserted_ids.append(row_id[0] if isinstance(row_id, (tuple, list)) else (row_id["id"] if isinstance(row_id, dict) else int(row_id[0])))


def _format_period_mmyyyy(period: Any, fallback_date: date | None = None) -> str:
    if isinstance(period, (date, datetime)):
        return f"{period.month:02d}/{period.year}"
    p = str(period or "").strip()
    if p:
        m = re.match(r"^(\d{4})-(0?[1-9]|1[0-2])$", p)
        if m:
            return f"{int(m.group(2)):02d}/{m.group(1)}"
        m2 = re.match(r"^(0?[1-9]|1[0-2])/(\d{4})$", p)
        if m2:
            return f"{int(m2.group(1)):02d}/{m2.group(2)}"
    if fallback_date:
        return f"{fallback_date.month:02d}/{fallback_date.year}"
    return ""


def _tax_period_suffix(period: Any) -> str:
    """' quý 1/2026' khi kỳ thuế dạng 'YYYY-Qn', ngược lại ' tháng MM/YYYY' (hoặc rỗng khi chưa chọn kỳ)."""
    quarter = re.match(r"^(\d{4})-Q([1-4])$", str(period or "").strip(), re.IGNORECASE)
    if quarter:
        return f" quý {quarter.group(2)}/{quarter.group(1)}"
    period_mmyyyy = _format_period_mmyyyy(period, fallback_date=None)
    return f" tháng {period_mmyyyy}" if period_mmyyyy else ""


def record_special_payment(
    *,
    company_id: int | None,
    kind: str,
    book_type: str = "bank",
    voucher_date: str = "",
    voucher_no: str = "",
    items: Any,
    partner_name: str = "",
    note: str = "",
    period: str = "",
    notice_total: int | float | Decimal | str | None = None,
) -> dict[str, Any]:
    """kind 'tax' | 'insurance' | 'salary'. Return {"ok", "message", "voucher_no", "ids", "total"}."""
    if not company_id:
        return {"ok": False, "message": "Chưa chọn công ty làm việc.", "voucher_no": "", "ids": [], "total": 0}

    kind = str(kind or "").strip().lower()
    if kind not in KIND_LABELS:
        return {"ok": False, "message": f"Loại chi không hợp lệ: {kind}", "voucher_no": "", "ids": [], "total": 0}

    book_type = str(book_type or "").strip().lower()
    if book_type not in ("cash", "bank"):
        return {"ok": False, "message": "Sổ không hợp lệ (chỉ hỗ trợ 'cash' hoặc 'bank').", "voucher_no": "", "ids": [], "total": 0}

    try:
        v_date = parse_flexible_date(voucher_date)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "message": f"Ngày chứng từ không hợp lệ: {exc}", "voucher_no": "", "ids": [], "total": 0}

    user_partner = str(partner_name or "").strip()
    common_source_id = str(uuid4())

    # Build note suffix
    full_note = str(note or "").strip()
    if notice_total is not None and str(notice_total).strip() != "":
        try:
            n_val = int(round(parse_vnd(notice_total)))
            n_str = f"Theo thông báo/C12: {n_val:,}".replace(",", ".")
            full_note = f"{full_note} - {n_str}" if full_note else n_str
        except (ValueError, TypeError):
            pass

    # Parse and validate items
    rows_to_insert: list[dict[str, Any]] = []

    if kind == "tax":
        if not isinstance(items, dict):
            return {"ok": False, "message": "Dữ liệu thuế không hợp lệ.", "voucher_no": "", "ids": [], "total": 0}

        period_suffix = _tax_period_suffix(period)
        default_partner = user_partner or "Kho bạc Nhà nước / Cơ quan Thuế"

        for key, val in items.items():
            if val is None or str(val).strip() == "":
                continue
            if key not in TAX_ITEMS:
                return {"ok": False, "message": f"Mục thuế không hợp lệ: {key}", "voucher_no": "", "ids": [], "total": 0}
            tk, item_name = TAX_ITEMS[key]
            try:
                amt_dec = parse_vnd(val)
            except (ValueError, TypeError):
                return {"ok": False, "message": f"Số tiền không hợp lệ cho mục {item_name}.", "voucher_no": "", "ids": [], "total": 0}

            if amt_dec == 0:
                continue
            if amt_dec < 0:
                return {"ok": False, "message": f"Số tiền thuế phải lớn hơn 0 cho mục {item_name}.", "voucher_no": "", "ids": [], "total": 0}

            amt = int(round(amt_dec))
            rows_to_insert.append({
                "offset_account": tk,
                "amount": amt,
                "description": f"Nộp {item_name}{period_suffix}",
                "partner_name": default_partner,
                "source_type": "tax_payment",
            })

    elif kind == "insurance":
        if not isinstance(items, dict):
            return {"ok": False, "message": "Dữ liệu bảo hiểm không hợp lệ.", "voucher_no": "", "ids": [], "total": 0}

        period_mmyyyy = _format_period_mmyyyy(period, fallback_date=v_date)
        period_suffix = f" tháng {period_mmyyyy}" if period_mmyyyy else ""
        default_partner = user_partner or "Cơ quan Bảo hiểm xã hội"

        for key, val in items.items():
            if val is None or str(val).strip() == "":
                continue
            if key not in INSURANCE_ITEMS:
                return {"ok": False, "message": f"Mục bảo hiểm không hợp lệ: {key}", "voucher_no": "", "ids": [], "total": 0}
            tk, item_name = INSURANCE_ITEMS[key]
            try:
                amt_dec = parse_vnd(val)
            except (ValueError, TypeError):
                return {"ok": False, "message": f"Số tiền không hợp lệ cho mục {item_name}.", "voucher_no": "", "ids": [], "total": 0}

            if amt_dec == 0:
                continue
            if amt_dec < 0:
                return {"ok": False, "message": f"Số tiền bảo hiểm phải lớn hơn 0 cho mục {item_name}.", "voucher_no": "", "ids": [], "total": 0}

            amt = int(round(amt_dec))
            rows_to_insert.append({
                "offset_account": tk,
                "amount": amt,
                "description": f"Nộp {item_name}{period_suffix}",
                "partner_name": default_partner,
                "source_type": "insurance_payment",
            })

    elif kind == "salary":
        if not isinstance(items, list):
            return {"ok": False, "message": "Dữ liệu trả lương không hợp lệ.", "voucher_no": "", "ids": [], "total": 0}

        period_mmyyyy = _format_period_mmyyyy(period, fallback_date=v_date)
        period_suffix = f" tháng {period_mmyyyy}" if period_mmyyyy else ""

        for idx, entry in enumerate(items):
            if not isinstance(entry, dict):
                continue
            val = entry.get("amount")
            if val is None or str(val).strip() == "":
                continue
            name = str(entry.get("name") or "").strip()
            label_name = name or f"dòng #{idx + 1}"
            try:
                amt_dec = parse_vnd(val)
            except (ValueError, TypeError):
                return {"ok": False, "message": f"Số tiền không hợp lệ cho {label_name}.", "voucher_no": "", "ids": [], "total": 0}

            if amt_dec == 0:
                continue
            if amt_dec < 0:
                return {"ok": False, "message": f"Số tiền lương phải lớn hơn 0 cho {label_name}.", "voucher_no": "", "ids": [], "total": 0}

            amt = int(round(amt_dec))
            desc = f"Trả lương{period_suffix}" + (f" - {name}" if name else "")
            p_name = name or user_partner or "Người lao động"

            rows_to_insert.append({
                "offset_account": SALARY_ACCOUNT,
                "amount": amt,
                "description": desc,
                "partner_name": p_name,
                "source_type": "salary_payment",
            })

    elif kind == "rent":
        if not isinstance(items, dict):
            return {"ok": False, "message": "Dữ liệu thuê nhà không hợp lệ.", "voucher_no": "", "ids": [], "total": 0}

        val = items.get("amount")
        if val is not None and str(val).strip() != "":
            try:
                amt_dec = parse_vnd(val)
            except (ValueError, TypeError):
                return {"ok": False, "message": "Số tiền thuê nhà không hợp lệ.", "voucher_no": "", "ids": [], "total": 0}

            if amt_dec <= 0:
                return {"ok": False, "message": "Số tiền thuê nhà phải lớn hơn 0.", "voucher_no": "", "ids": [], "total": 0}

            amt = int(round(amt_dec))
            period_mmyyyy = _format_period_mmyyyy(period, fallback_date=v_date)
            period_suffix = f" tháng {period_mmyyyy}" if period_mmyyyy else ""
            landlord = user_partner or get_company_director_name(company_id) or "Chủ nhà"
            desc = f"Chi phí thuê nhà làm văn phòng{period_suffix} - {landlord}"

            rows_to_insert.append({
                "offset_account": RENT_ACCOUNT,
                "amount": amt,
                "description": desc,
                "partner_name": landlord,
                "source_type": "rent_payment",
            })

    if not rows_to_insert:
        return {"ok": False, "message": "Phải có ít nhất một khoản thanh toán có số tiền hợp lệ.", "voucher_no": "", "ids": [], "total": 0}

    # Resolve voucher_no
    final_voucher_no = str(voucher_no or "").strip()
    if not final_voucher_no:
        final_voucher_no = next_counted_voucher_no(int(company_id), book_type, "payment")

    # Bank information
    bank_account_no: str | None = None
    bank_name: str | None = None
    if book_type == "bank":
        comp_info = get_company_info(int(company_id))
        bank_account_no = comp_info.get("bank_account") or None
        bank_name = comp_info.get("bank_name") or None

    # Insert all rows in one transaction
    inserted_ids: list[int] = []
    total_amount = 0
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            for r in rows_to_insert:
                _insert_row(cursor, inserted_ids, int(company_id), book_type, v_date, final_voucher_no, "payment",
                            r["description"].strip(), r["offset_account"], r["amount"], bank_account_no, bank_name,
                            r["partner_name"], r["source_type"], common_source_id, full_note)
                total_amount += r["amount"]
        conn.commit()

    book_label = "sổ quỹ" if book_type == "cash" else "sổ ngân hàng"
    total_int = int(total_amount)
    total_str = f"{total_int:,}".replace(",", ".")
    msg = f"Đã ghi {book_label} ({KIND_LABELS[kind]}, Số CT: {final_voucher_no}, {len(inserted_ids)} dòng, tổng {total_str} đ)."
    return {
        "ok": True,
        "message": msg,
        "voucher_no": final_voucher_no,
        "ids": inserted_ids,
        "total": total_int,
    }


def payroll_preview(company_id: int | None, month: str) -> dict[str, Any]:
    """Preview net pay and total insurance for the given month."""
    if not company_id:
        return {"ok": False, "message": "Chưa chọn công ty.", "month": month, "rows": [], "insurance_total": 0}

    m = str(month or "").strip()
    try:
        start_date, _ = month_bounds(m)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "message": f"Tháng không hợp lệ: {exc}", "month": month, "rows": [], "insurance_total": 0}

    from frontend.modules.payroll.staff_payroll import payroll_rows

    raw_rows = payroll_rows(start_date, int(company_id)) or []

    total_ins = Decimal("0")
    preview_rows: list[dict[str, Any]] = []

    for r in raw_rows:
        emp_ins = Decimal(str(r.get("employee_insurance") or 0))
        empr_ins = Decimal(str(r.get("employer_insurance") or 0))
        total_ins += (emp_ins + empr_ins)

        net_val = int(round(float(r.get("net_pay") or 0)))
        if net_val > 0:
            preview_rows.append({
                "user_id": r.get("user_id"),
                "full_name": r.get("full_name") or "",
                "net_pay": net_val,
            })

    return {
        "ok": True,
        "month": m,
        "rows": preview_rows,
        "insurance_total": int(round(float(total_ins))),
    }


def record_bank_interest(
    *,
    company_id: int | None,
    voucher_date: str = "",
    voucher_no: str = "",
    amount: Any,
    partner_name: str = "",
    note: str = "",
    period: str = "",
) -> dict[str, Any]:
    """Record bank interest income (Dr 112 / Cr 515). Return {"ok", "message", "voucher_no", "ids", "total"}."""
    if not company_id:
        return {"ok": False, "message": "Chưa chọn công ty làm việc.", "voucher_no": "", "ids": [], "total": 0}

    try:
        v_date = parse_flexible_date(voucher_date)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "message": f"Ngày chứng từ không hợp lệ: {exc}", "voucher_no": "", "ids": [], "total": 0}

    try:
        raw_amt = parse_vnd(amount)
        amt = int(round(raw_amt))
    except (ValueError, TypeError):
        return {"ok": False, "message": "Số tiền lãi không hợp lệ.", "voucher_no": "", "ids": [], "total": 0}

    if amt <= 0:
        return {"ok": False, "message": "Số tiền lãi phải lớn hơn 0.", "voucher_no": "", "ids": [], "total": 0}

    period_mmyyyy = _format_period_mmyyyy(period, fallback_date=v_date)
    period_suffix = f" tháng {period_mmyyyy}" if period_mmyyyy else ""
    description = f"Lãi tiền gửi ngân hàng{period_suffix}"

    comp_info = get_company_info(int(company_id))
    bank_account_no = comp_info.get("bank_account") or None
    bank_name = comp_info.get("bank_name") or None
    final_partner = (partner_name or "").strip() or (bank_name or "Ngân hàng")

    final_voucher_no = str(voucher_no or "").strip()
    if not final_voucher_no:
        final_voucher_no = next_counted_voucher_no(int(company_id), "bank", "receipt")

    clean_note = str(note or "").strip()

    inserted_ids: list[int] = []
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            _insert_row(cursor, inserted_ids, int(company_id), "bank", v_date, final_voucher_no, "receipt",
                        description.strip(), INTEREST_ACCOUNT, amt, bank_account_no, bank_name, final_partner,
                        "bank_interest", str(uuid4()), clean_note)
        conn.commit()

    amt_str = f"{amt:,}".replace(",", ".")
    message = f"Đã ghi sổ ngân hàng (lãi tiền gửi, Số CT: {final_voucher_no}, tổng {amt_str} đ)."
    return {
        "ok": True,
        "message": message,
        "voucher_no": final_voucher_no,
        "ids": inserted_ids,
        "total": amt,
    }
