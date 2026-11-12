"""Service for Bù trừ công nợ TK 331 (Chuyển công nợ phải trả giữa hai nhà cung cấp)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import logging
from typing import Any

from backend.services.database import db

logger = logging.getLogger(__name__)


def list_vendor_partners() -> list[dict]:
    sql = """
        SELECT id, name, tax_code
        FROM public.invoice_companies
        WHERE COALESCE(is_vendor, FALSE) = TRUE
        ORDER BY name ASC
    """
    return db._execute_query(sql, fetchall=True, commit=False) or []


def count_offsets(company_id: int) -> int:
    row = db._execute_query(
        "SELECT COUNT(*) AS total FROM public.transport_debt_offsets WHERE company_id = %s",
        (company_id,),
        fetch=True,
        commit=False,
    ) or {}
    return int(row.get("total") or 0)


def list_offsets(company_id: int, limit: int = 20, offset: int = 0) -> list[dict]:
    sql = """
        SELECT o.id, o.company_id, o.offset_date, o.debit_partner_id, o.credit_partner_id,
               o.amount, o.description, o.created_by, o.created_at,
               d.name AS debit_name, d.tax_code AS debit_tax_code,
               c.name AS credit_name, c.tax_code AS credit_tax_code
        FROM public.transport_debt_offsets o
        LEFT JOIN public.invoice_companies d ON o.debit_partner_id = d.id
        LEFT JOIN public.invoice_companies c ON o.credit_partner_id = c.id
        WHERE o.company_id = %s
        ORDER BY o.offset_date DESC, o.id DESC
        LIMIT %s OFFSET %s
    """
    rows = db._execute_query(sql, (company_id, limit, offset), fetchall=True, commit=False) or []
    for r in rows:
        amt = Decimal(str(r.get("amount") or 0))
        r["amount"] = amt
        n = int(round(float(amt)))
        r["amount_fmt"] = f"{n:,}".replace(",", ".")
    return rows


def _parse_offset_fields(
    offset_date: str | date | None,
    debit_partner_id: int | str | None,
    credit_partner_id: int | str | None,
    amount: int | float | str | Decimal | None,
    description: str | None,
) -> tuple[str, tuple | None]:
    """Kiểm tra các trường của một bút toán. Trả (thông báo lỗi, None) hoặc ("", (ngày, nợ, có, số tiền, diễn giải))."""
    from frontend.modules.transport.cash_bank_service import parse_flexible_date
    if not offset_date:
        return ("Ngày không hợp lệ.", None)
    try:
        parsed_date = parse_flexible_date(offset_date)
    except ValueError:
        return ("Ngày không hợp lệ.", None)

    try:
        d_id, c_id = int(debit_partner_id), int(credit_partner_id)
    except (ValueError, TypeError):
        return ("Đối tác không hợp lệ.", None)

    if d_id == c_id:
        return ("Chọn hai đối tác khác nhau.", None)

    rows = db._execute_query(
        "SELECT id, COALESCE(is_vendor, FALSE) AS is_vendor FROM public.invoice_companies WHERE id IN (%s, %s)",
        (d_id, c_id),
        fetchall=True,
        commit=False,
    ) or []
    found = {r["id"]: bool(r.get("is_vendor")) for r in rows}
    if len(found) < 2 or not found.get(d_id) or not found.get(c_id):
        return ("Đối tác không phải nhà cung cấp.", None)

    from frontend.formatting.money import parse_vnd
    try:
        amt = parse_vnd(amount)
    except (ValueError, TypeError):
        return ("Số tiền không hợp lệ.", None)
    if amt <= 0:
        return ("Số tiền phải lớn hơn 0.", None)

    return ("", (parsed_date, d_id, c_id, Decimal(str(amt)), (description or "").strip()[:500]))


def create_offset(
    company_id: int | None,
    offset_date: str | date | None,
    debit_partner_id: int | str | None,
    credit_partner_id: int | str | None,
    amount: int | float | str | Decimal | None,
    description: str | None = "",
    created_by: int | None = None,
) -> tuple[bool, str]:
    if not company_id:
        return (False, "Chưa chọn công ty làm việc.")

    error, fields = _parse_offset_fields(offset_date, debit_partner_id, credit_partner_id, amount, description)
    if not fields:
        return (False, error)
    parsed_date, d_id, c_id, amt, desc = fields

    db._execute_query(
        """
        INSERT INTO public.transport_debt_offsets
        (company_id, offset_date, debit_partner_id, credit_partner_id, amount, description, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (company_id, parsed_date, d_id, c_id, amt, desc, created_by),
        commit=True,
    )
    return (True, "Đã lưu bút toán.")


def update_offset(
    company_id: int | None,
    offset_id: int | str | None,
    offset_date: str | date | None,
    debit_partner_id: int | str | None,
    credit_partner_id: int | str | None,
    amount: int | float | str | Decimal | None,
    description: str | None = "",
) -> tuple[bool, str]:
    if not company_id:
        return (False, "Chưa chọn công ty làm việc.")

    error, fields = _parse_offset_fields(offset_date, debit_partner_id, credit_partner_id, amount, description)
    if not fields:
        return (False, error)
    parsed_date, d_id, c_id, amt, desc = fields

    res = db._execute_query(
        """
        UPDATE public.transport_debt_offsets
           SET offset_date = %s, debit_partner_id = %s, credit_partner_id = %s, amount = %s, description = %s
         WHERE id = %s AND company_id = %s
        RETURNING id
        """,
        (parsed_date, d_id, c_id, amt, desc, offset_id, company_id),
        fetch=True,
        commit=True,
    )
    if not res:
        return (False, "Không tìm thấy bút toán.")
    return (True, "Đã cập nhật bút toán.")


def delete_offset(company_id: int | None, offset_id: int | str | None) -> tuple[bool, str]:
    res = db._execute_query(
        "DELETE FROM public.transport_debt_offsets WHERE id = %s AND company_id = %s RETURNING id",
        (offset_id, company_id),
        fetch=True,
        commit=True,
    )
    if not res:
        return (False, "Không tìm thấy bút toán.")
    return (True, "Đã xóa bút toán.")


def offsets_for_partner(
    company_id: int,
    partner_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    date_conds = []
    params: list[Any] = [company_id, partner_id, partner_id]
    if date_from is not None:
        date_conds.append("o.offset_date >= %s")
        params.append(date_from)
    if date_to is not None:
        date_conds.append("o.offset_date <= %s")
        params.append(date_to)
    date_clause = (" AND " + " AND ".join(date_conds)) if date_conds else ""
    sql = f"""
        SELECT o.id, o.offset_date, o.amount, o.description,
               o.debit_partner_id, o.credit_partner_id,
               d.name AS debit_name,
               c.name AS credit_name
        FROM public.transport_debt_offsets o
        LEFT JOIN public.invoice_companies d ON o.debit_partner_id = d.id
        LEFT JOIN public.invoice_companies c ON o.credit_partner_id = c.id
        WHERE o.company_id = %s
          AND (o.debit_partner_id = %s OR o.credit_partner_id = %s)
          {date_clause}
        ORDER BY o.offset_date ASC, o.id ASC
    """
    rows = db._execute_query(sql, tuple(params), fetchall=True, commit=False) or []
    results = []
    for r in rows:
        is_dr = (int(r["debit_partner_id"]) == int(partner_id))
        side = "dr" if is_dr else "cr"
        other_name = (r.get("credit_name") or "") if is_dr else (r.get("debit_name") or "")
        results.append({
            "id": r["id"],
            "date": r["offset_date"],
            "amount": Decimal(str(r["amount"])),
            "description": r.get("description") or "",
            "side": side,
            "other_name": other_name.strip(),
        })
    return results


def offset_totals_by_partner(
    company_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[int, dict[str, Decimal]]:
    date_conds = []
    params_sub: list[Any] = [company_id]
    if date_from is not None:
        date_conds.append("offset_date >= %s")
        params_sub.append(date_from)
    if date_to is not None:
        date_conds.append("offset_date <= %s")
        params_sub.append(date_to)
    date_clause = (" AND " + " AND ".join(date_conds)) if date_conds else ""

    sql = f"""
        SELECT partner_id,
               COALESCE(SUM(dr_amt), 0) AS total_dr,
               COALESCE(SUM(cr_amt), 0) AS total_cr
        FROM (
            SELECT debit_partner_id AS partner_id, amount AS dr_amt, 0 AS cr_amt
            FROM public.transport_debt_offsets
            WHERE company_id = %s {date_clause}
            UNION ALL
            SELECT credit_partner_id AS partner_id, 0 AS dr_amt, amount AS cr_amt
            FROM public.transport_debt_offsets
            WHERE company_id = %s {date_clause}
        ) sub
        GROUP BY partner_id
    """
    rows = db._execute_query(sql, tuple(params_sub + params_sub), fetchall=True, commit=False) or []
    result: dict[int, dict[str, Decimal]] = {}
    for row in rows:
        pid = int(row["partner_id"])
        result[pid] = {
            "dr": Decimal(str(row["total_dr"] or 0)),
            "cr": Decimal(str(row["total_cr"] or 0)),
        }
    return result


def offsets_by_invoice_number(company_id: int | None, invoice_numbers: list) -> dict[str, list[dict]]:
    """Bù trừ công nợ whose description names an invoice or receipt number ("theo Biên Lai số 8138001"),
    keyed by the number without leading zeros."""
    import re
    from frontend.formatting.text import normalize_invoice_number

    if not company_id:
        return {}
    wanted = {normalize_invoice_number(n) for n in invoice_numbers if len(normalize_invoice_number(n)) >= 4}
    if not wanted:
        return {}
    rows = db._execute_query(
        """
        SELECT o.id, o.offset_date, o.amount, o.description, d.name AS debit_name, c.name AS credit_name
        FROM public.transport_debt_offsets o
        LEFT JOIN public.invoice_companies d ON o.debit_partner_id = d.id
        LEFT JOIN public.invoice_companies c ON o.credit_partner_id = c.id
        WHERE o.company_id = %s
        ORDER BY o.offset_date ASC, o.id ASC
        """,
        (company_id,), fetchall=True, commit=False,
    ) or []
    found: dict[str, list[dict]] = {}
    for row in rows:
        named = {normalize_invoice_number(token) for token in re.findall(r"\d{4,}", row.get("description") or "")}
        for number in named & wanted:
            found.setdefault(number, []).append({
                "id": row["id"], "date": row["offset_date"], "amount": Decimal(str(row["amount"])),
                "debit_name": (row.get("debit_name") or "").strip(), "credit_name": (row.get("credit_name") or "").strip(),
            })
    return found
