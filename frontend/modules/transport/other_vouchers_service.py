"""Service for Chứng từ nghiệp vụ khác (TK 131, 331, 711, 811, 3339, 3388)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import logging
from typing import Any

from backend.services.database import db

logger = logging.getLogger(__name__)

ALLOWED_ACCOUNTS = ("131", "331", "711", "811", "3339", "3388")
PARTNER_ACCOUNTS = ("131", "331")

ACCOUNT_LABELS = {
    "131": "Phải thu khách hàng",
    "331": "Phải trả người bán",
    "711": "Thu nhập khác",
    "811": "Chi phí khác",
    "3339": "Phí, lệ phí và các khoản phải nộp khác",
    "3388": "Phải trả, phải nộp khác",
}

_VOUCHER_LOCK_KEY = 1_118_002


def list_vendor_partners() -> list[dict]:
    sql = """
        SELECT id, name, tax_code
        FROM public.invoice_companies
        WHERE COALESCE(is_vendor, FALSE) = TRUE
        ORDER BY name ASC
    """
    return db._execute_query(sql, fetchall=True, commit=False) or []


def list_customer_partners() -> list[dict]:
    sql = """
        SELECT id, name, tax_code
        FROM public.invoice_companies
        WHERE COALESCE(is_customer, TRUE) = TRUE
        ORDER BY name ASC
    """
    return db._execute_query(sql, fetchall=True, commit=False) or []


def count_vouchers(company_id: int | None) -> int:
    if not company_id:
        return 0
    row = db._execute_query(
        "SELECT COUNT(*) AS total FROM public.transport_other_vouchers WHERE company_id = %s",
        (company_id,),
        fetch=True,
        commit=False,
    ) or {}
    return int(row.get("total") or 0)


def list_vouchers(company_id: int | None, limit: int = 20, offset: int = 0) -> list[dict]:
    if not company_id:
        return []
    sql = """
        SELECT v.id, v.company_id, v.voucher_no, v.voucher_date,
               v.description, v.debit_account, v.credit_account,
               v.debit_partner_id, v.credit_partner_id, v.amount,
               v.created_by, v.created_at,
               d.name AS debit_partner_name, d.tax_code AS debit_tax_code,
               c.name AS credit_partner_name, c.tax_code AS credit_tax_code
        FROM public.transport_other_vouchers v
        LEFT JOIN public.invoice_companies d ON v.debit_partner_id = d.id
        LEFT JOIN public.invoice_companies c ON v.credit_partner_id = c.id
        WHERE v.company_id = %s
        ORDER BY v.voucher_date DESC, v.id DESC
        LIMIT %s OFFSET %s
    """
    rows = db._execute_query(sql, (company_id, limit, offset), fetchall=True, commit=False) or []
    for r in rows:
        amt = Decimal(str(r.get("amount") or 0))
        r["amount"] = amt
        n = int(round(float(amt)))
        r["amount_fmt"] = f"{n:,}".replace(",", ".")
    return rows


def _parse_voucher_fields(
    voucher_date: str | date | None,
    debit_account: str | None,
    credit_account: str | None,
    debit_partner_id: int | str | None,
    credit_partner_id: int | str | None,
    amount: int | float | str | Decimal | None,
    description: str | None,
) -> tuple[str, tuple | None]:
    from frontend.modules.transport.cash_bank_service import parse_flexible_date
    if not voucher_date:
        return ("Ngày không hợp lệ.", None)
    try:
        parsed_date = parse_flexible_date(voucher_date)
    except (ValueError, TypeError):
        return ("Ngày không hợp lệ.", None)

    d_acc = str(debit_account or "").strip()
    c_acc = str(credit_account or "").strip()

    if d_acc not in ALLOWED_ACCOUNTS or c_acc not in ALLOWED_ACCOUNTS:
        return ("Tài khoản không hợp lệ.", None)
    if d_acc == c_acc:
        return ("Chọn hai tài khoản khác nhau.", None)
    if d_acc not in ("131", "331", "3388") and c_acc not in ("131", "331", "3388"):
        return ("Ít nhất một vế phải là TK 131, 331 hoặc 3388.", None)

    d_pid: int | None = None
    c_pid: int | None = None

    if d_acc in PARTNER_ACCOUNTS:
        try:
            d_pid = int(debit_partner_id)
        except (ValueError, TypeError):
            return ("Chưa chọn đối tác Nợ.", None)
        if d_acc == "331":
            row = db._execute_query(
                "SELECT id, COALESCE(is_vendor, FALSE) AS is_v FROM public.invoice_companies WHERE id = %s",
                (d_pid,),
                fetch=True,
                commit=False,
            )
            if not row or not row.get("is_v"):
                return ("Đối tác Nợ không phải nhà cung cấp.", None)
        elif d_acc == "131":
            row = db._execute_query(
                "SELECT id, COALESCE(is_customer, TRUE) AS is_c FROM public.invoice_companies WHERE id = %s",
                (d_pid,),
                fetch=True,
                commit=False,
            )
            if not row or not row.get("is_c"):
                return ("Đối tác Nợ không phải khách hàng.", None)
    else:
        d_pid = None

    if c_acc in PARTNER_ACCOUNTS:
        try:
            c_pid = int(credit_partner_id)
        except (ValueError, TypeError):
            return ("Chưa chọn đối tác Có.", None)
        if c_acc == "331":
            row = db._execute_query(
                "SELECT id, COALESCE(is_vendor, FALSE) AS is_v FROM public.invoice_companies WHERE id = %s",
                (c_pid,),
                fetch=True,
                commit=False,
            )
            if not row or not row.get("is_v"):
                return ("Đối tác Có không phải nhà cung cấp.", None)
        elif c_acc == "131":
            row = db._execute_query(
                "SELECT id, COALESCE(is_customer, TRUE) AS is_c FROM public.invoice_companies WHERE id = %s",
                (c_pid,),
                fetch=True,
                commit=False,
            )
            if not row or not row.get("is_c"):
                return ("Đối tác Có không phải khách hàng.", None)
    else:
        c_pid = None

    from frontend.formatting.money import parse_vnd
    try:
        amt = parse_vnd(amount)
    except (ValueError, TypeError):
        return ("Số tiền không hợp lệ.", None)
    if amt <= 0:
        return ("Số tiền phải lớn hơn 0.", None)

    desc = (description or "").strip()
    return ("", (parsed_date, d_acc, c_acc, d_pid, c_pid, Decimal(str(amt)), desc))


def _allocate_voucher_no(cursor, company_id: int) -> str:
    cursor.execute("""
        SELECT substring(voucher_no from '^NVK-([0-9]+)$')::int AS num
        FROM public.transport_other_vouchers
        WHERE company_id = %s AND voucher_no ~ '^NVK-[0-9]+$'
        ORDER BY num DESC
        LIMIT 1
    """, (company_id,))
    row = cursor.fetchone()
    max_num = int((row[0] if (row and row[0] is not None) else 0) or 0)
    next_num = max_num + 1
    return f"NVK-{next_num:04d}"


def create_voucher(
    company_id: int | None,
    voucher_date: str | date | None,
    debit_account: str | None,
    credit_account: str | None,
    debit_partner_id: int | str | None,
    credit_partner_id: int | str | None,
    amount: int | float | str | Decimal | None,
    description: str | None = "",
    created_by: int | None = None,
) -> tuple[bool, str]:
    if not company_id:
        return (False, "Chưa chọn công ty làm việc.")

    error, fields = _parse_voucher_fields(
        voucher_date,
        debit_account,
        credit_account,
        debit_partner_id,
        credit_partner_id,
        amount,
        description,
    )
    if not fields:
        return (False, error)
    parsed_date, d_acc, c_acc, d_pid, c_pid, amt, desc = fields

    try:
        with db._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_VOUCHER_LOCK_KEY, int(company_id)))
                voucher_no = _allocate_voucher_no(cursor, company_id)
                cursor.execute("""
                    INSERT INTO public.transport_other_vouchers
                    (company_id, voucher_no, voucher_date, debit_account, credit_account,
                     debit_partner_id, credit_partner_id, amount, description, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (company_id, voucher_no, parsed_date, d_acc, c_acc, d_pid, c_pid, amt, desc, created_by))
            conn.commit()
    except Exception as e:
        err_msg = str(e)
        if "unique" in err_msg.lower() or "trùng" in err_msg.lower() or getattr(e, "pgcode", "") == "23505":
            return (False, "Số chứng từ đã tồn tại.")
        logger.exception("Error creating other voucher")
        return (False, f"Lỗi tạo chứng từ: {err_msg}")
    return (True, "Đã lưu chứng từ.")


def update_voucher(
    company_id: int | None,
    voucher_id: int | str | None,
    voucher_date: str | date | None,
    debit_account: str | None,
    credit_account: str | None,
    debit_partner_id: int | str | None,
    credit_partner_id: int | str | None,
    amount: int | float | str | Decimal | None,
    description: str | None = "",
) -> tuple[bool, str]:
    if not company_id:
        return (False, "Chưa chọn công ty làm việc.")

    error, fields = _parse_voucher_fields(
        voucher_date,
        debit_account,
        credit_account,
        debit_partner_id,
        credit_partner_id,
        amount,
        description,
    )
    if not fields:
        return (False, error)
    parsed_date, d_acc, c_acc, d_pid, c_pid, amt, desc = fields

    res = db._execute_query("""
            UPDATE public.transport_other_vouchers
               SET voucher_date = %s, debit_account = %s, credit_account = %s,
                   debit_partner_id = %s, credit_partner_id = %s, amount = %s, description = %s
             WHERE id = %s AND company_id = %s
            RETURNING id
        """, (parsed_date, d_acc, c_acc, d_pid, c_pid, amt, desc, voucher_id, company_id), fetch=True, commit=True)
    if not res:
        return (False, "Không tìm thấy chứng từ.")
    return (True, "Đã cập nhật chứng từ.")


def delete_voucher(company_id: int | None, voucher_id: int | str | None) -> tuple[bool, str]:
    if not company_id:
        return (False, "Chưa chọn công ty làm việc.")
    res = db._execute_query(
        "DELETE FROM public.transport_other_vouchers WHERE id = %s AND company_id = %s RETURNING id",
        (voucher_id, company_id),
        fetch=True,
        commit=True,
    )
    if not res:
        return (False, "Không tìm thấy chứng từ.")
    return (True, "Đã xóa chứng từ.")


def voucher_totals_by_partner(
    company_id: int | None,
    account: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[int, dict[str, Decimal]]:
    if not company_id:
        return {}
    date_conds = []
    params: list[Any] = [company_id, account]
    if date_from is not None:
        date_conds.append("voucher_date >= %s")
        params.append(date_from)
    if date_to is not None:
        date_conds.append("voucher_date <= %s")
        params.append(date_to)
    date_clause = (" AND " + " AND ".join(date_conds)) if date_conds else ""

    sql = f"""
        SELECT partner_id,
               COALESCE(SUM(dr_amt), 0) AS total_dr,
               COALESCE(SUM(cr_amt), 0) AS total_cr
        FROM (
            SELECT debit_partner_id AS partner_id, amount AS dr_amt, 0 AS cr_amt
            FROM public.transport_other_vouchers
            WHERE company_id = %s AND debit_account = %s AND debit_partner_id IS NOT NULL {date_clause}
            UNION ALL
            SELECT credit_partner_id AS partner_id, 0 AS dr_amt, amount AS cr_amt
            FROM public.transport_other_vouchers
            WHERE company_id = %s AND credit_account = %s AND credit_partner_id IS NOT NULL {date_clause}
        ) sub
        GROUP BY partner_id
    """
    rows = db._execute_query(sql, tuple(params + params), fetchall=True, commit=False) or []
    result: dict[int, dict[str, Decimal]] = {}
    for row in rows:
        pid = int(row["partner_id"])
        result[pid] = {
            "dr": Decimal(str(row["total_dr"] or 0)),
            "cr": Decimal(str(row["total_cr"] or 0)),
        }
    return result


def vouchers_for_partner(
    company_id: int | None,
    partner_id: int,
    account: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    if not company_id or not partner_id:
        return []
    date_conds = []
    params: list[Any] = [company_id, account, partner_id, account, partner_id]
    if date_from is not None:
        date_conds.append("voucher_date >= %s")
        params.append(date_from)
    if date_to is not None:
        date_conds.append("voucher_date <= %s")
        params.append(date_to)
    date_clause = (" AND " + " AND ".join(date_conds)) if date_conds else ""

    sql = f"""
        SELECT id, voucher_no, voucher_date, amount, description,
               debit_account, credit_account, debit_partner_id, credit_partner_id
        FROM public.transport_other_vouchers
        WHERE company_id = %s
          AND (
            (debit_account = %s AND debit_partner_id = %s)
            OR (credit_account = %s AND credit_partner_id = %s)
          )
          {date_clause}
        ORDER BY voucher_date ASC, id ASC
    """
    rows = db._execute_query(sql, tuple(params), fetchall=True, commit=False) or []
    results = []
    for r in rows:
        is_dr = (r["debit_account"] == account and int(r["debit_partner_id"] or 0) == int(partner_id))
        side = "dr" if is_dr else "cr"
        other_account = r["credit_account"] if is_dr else r["debit_account"]
        results.append({
            "id": r["id"],
            "date": r["voucher_date"],
            "voucher_no": r["voucher_no"],
            "description": r.get("description") or "",
            "side": side,
            "amount": Decimal(str(r["amount"] or 0)),
            "other_account": other_account,
        })
    return results


def voucher_totals_by_account(
    company_id: int | None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, dict[str, int]]:
    result = {acc: {"dr": 0, "cr": 0} for acc in ALLOWED_ACCOUNTS}
    if not company_id:
        return result

    date_conds = []
    params: list[Any] = [company_id]
    if date_from is not None:
        date_conds.append("voucher_date >= %s")
        params.append(date_from)
    if date_to is not None:
        date_conds.append("voucher_date <= %s")
        params.append(date_to)
    date_clause = (" AND " + " AND ".join(date_conds)) if date_conds else ""

    sql = f"""
        SELECT account,
               COALESCE(SUM(dr_amt), 0) AS total_dr,
               COALESCE(SUM(cr_amt), 0) AS total_cr
        FROM (
            SELECT debit_account AS account, amount AS dr_amt, 0 AS cr_amt
            FROM public.transport_other_vouchers
            WHERE company_id = %s {date_clause}
            UNION ALL
            SELECT credit_account AS account, 0 AS dr_amt, amount AS cr_amt
            FROM public.transport_other_vouchers
            WHERE company_id = %s {date_clause}
        ) sub
        WHERE account = ANY(%s)
        GROUP BY account
    """
    rows = db._execute_query(
        sql,
        tuple(params + params + [list(ALLOWED_ACCOUNTS)]),
        fetchall=True,
        commit=False,
    ) or []
    for r in rows:
        acc = str(r["account"])
        if acc in result:
            result[acc] = {
                "dr": int(round(float(r["total_dr"] or 0))),
                "cr": int(round(float(r["total_cr"] or 0))),
            }
    return result


def voucher_registration_fee_total(
    company_id: int | None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> int:
    """Nợ 3339 của chứng từ nghiệp vụ khác có diễn giải lệ phí trước bạ (không phải nộp thừa): phần nguyên giá TSCĐ do người khác nộp hộ."""
    if not company_id:
        return 0
    date_conds = []
    params: list[Any] = [company_id]
    if date_from is not None:
        date_conds.append("voucher_date >= %s")
        params.append(date_from)
    if date_to is not None:
        date_conds.append("voucher_date <= %s")
        params.append(date_to)
    date_clause = (" AND " + " AND ".join(date_conds)) if date_conds else ""

    sql = f"""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM public.transport_other_vouchers
        WHERE company_id = %s
          AND debit_account = '3339'
          AND description ILIKE '%%trước bạ%%'
          AND description NOT ILIKE '%%thừa%%'
          {date_clause}
    """
    row = db._execute_query(sql, tuple(params), fetch=True, commit=False) or {}
    return int(round(float(row.get("total") or 0)))


def list_vouchers_for_account(
    company_id: int | None,
    account: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    if not company_id:
        return []
    date_conds = []
    params: list[Any] = [company_id, account, account]
    if date_from is not None:
        date_conds.append("v.voucher_date >= %s")
        params.append(date_from)
    if date_to is not None:
        date_conds.append("v.voucher_date <= %s")
        params.append(date_to)
    date_clause = (" AND " + " AND ".join(date_conds)) if date_conds else ""

    sql = f"""
        SELECT v.id, v.voucher_no, v.voucher_date, v.amount, v.description,
               v.debit_account, v.credit_account,
               v.debit_partner_id, v.credit_partner_id,
               d.name AS debit_name, c.name AS credit_name
        FROM public.transport_other_vouchers v
        LEFT JOIN public.invoice_companies d ON v.debit_partner_id = d.id
        LEFT JOIN public.invoice_companies c ON v.credit_partner_id = c.id
        WHERE v.company_id = %s
          AND (v.debit_account = %s OR v.credit_account = %s)
          {date_clause}
        ORDER BY v.voucher_date DESC, v.id DESC
    """
    rows = db._execute_query(sql, tuple(params), fetchall=True, commit=False) or []
    results = []
    for r in rows:
        if r["debit_account"] == account and r.get("debit_name"):
            partner_name = r["debit_name"]
        elif r["credit_account"] == account and r.get("credit_name"):
            partner_name = r["credit_name"]
        else:
            partner_name = r.get("debit_name") or r.get("credit_name") or ""

        results.append({
            "id": r["id"],
            "date": r["voucher_date"],
            "voucher_no": r["voucher_no"],
            "description": r.get("description") or "",
            "debit_account": r["debit_account"],
            "credit_account": r["credit_account"],
            "partner_name": partner_name.strip(),
            "amount": Decimal(str(r["amount"] or 0)),
        })
    return results
