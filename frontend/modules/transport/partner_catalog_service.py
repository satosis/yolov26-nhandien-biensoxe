"""Service for Partner Catalog (Khách hàng & Nhà cung cấp) and Sub-ledgers 131 & 331.
Complies with Circular 133/2016/TT-BTC.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import logging
from typing import Any

from backend.services.database import db
from frontend.context.company_context import get_current_company_id

logger = logging.getLogger(__name__)

DAI_LO_XANH_COMPANY_ID = 33


def _fmt(val: Decimal | int | float | None) -> str:
    if val is None:
        return "0 đ"
    n = int(round(float(val)))
    if n == 0:
        return "0 đ"
    return f"{n:,}".replace(",", ".") + " đ"


def list_partners_sheet1(
    q: str = "",
    partner_type: str = "all",  # 'all', 'organization', 'individual'
    role_filter: str = "all",   # 'all', 'customer', 'vendor', 'both'
    page: int = 1,
    per_page: int = 100,
) -> dict[str, Any]:
    """Retrieve Sheet 1: Danh sách KH / NCC with toggleable KH/NCC flags."""
    conditions = []
    params: list[Any] = []

    if q and q.strip():
        term = f"%{q.strip()}%"
        conditions.append("(c.name ILIKE %s OR c.tax_code ILIKE %s OR c.address ILIKE %s OR c.phone ILIKE %s)")
        params.extend([term, term, term, term])

    if partner_type in ("organization", "individual"):
        conditions.append("c.partner_type = %s")
        params.append(partner_type)

    if role_filter == "customer":
        conditions.append("c.is_customer = TRUE")
    elif role_filter == "vendor":
        conditions.append("c.is_vendor = TRUE")
    elif role_filter == "both":
        conditions.append("(c.is_customer = TRUE AND c.is_vendor = TRUE)")

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # Count total
    count_sql = f"SELECT COUNT(*) as cnt FROM public.invoice_companies c {where_sql}"
    cnt_row = db._execute_query(count_sql, tuple(params), fetch=True, commit=False) or {}
    total = cnt_row.get("cnt", 0)

    offset = max(0, (page - 1) * per_page)
    limit_params = list(params) + [per_page, offset]

    # Fetch rows
    sql = f"""
        SELECT c.id, c.name, c.tax_code, c.address, c.phone, c.email,
               COALESCE(c.is_customer, TRUE) as is_customer,
               COALESCE(c.is_vendor, FALSE) as is_vendor,
               COALESCE(c.partner_type, 'organization') as partner_type,
               COALESCE(c.current_debt, 0) as current_debt,
               COALESCE(c.opening_debt_dr_131, 0) as opening_debt_dr_131,
               COALESCE(c.opening_debt_cr_131, 0) as opening_debt_cr_131,
               COALESCE(c.opening_debt_dr_331, 0) as opening_debt_dr_331,
               COALESCE(c.opening_debt_cr_331, 0) as opening_debt_cr_331
        FROM public.invoice_companies c
        {where_sql}
        ORDER BY c.name ASC
        LIMIT %s OFFSET %s
    """
    rows = db._execute_query(sql, tuple(limit_params), fetchall=True, commit=False) or []

    # Map trip counts
    c_ids = [r["id"] for r in rows]
    trip_counts = {}
    if c_ids:
        t_rows = db._execute_query(
            "SELECT company_id, COUNT(*) as cnt FROM public.cargo_trips WHERE company_id = ANY(%s::integer[]) GROUP BY company_id",
            (c_ids,), fetchall=True, commit=False
        ) or []
        trip_counts = {r["company_id"]: r["cnt"] for r in t_rows}

    items = []
    for r in rows:
        debt_val = Decimal(str(r["current_debt"] or 0))
        items.append({
            "id": r["id"],
            "name": r["name"],
            "tax_code": r["tax_code"] or "",
            "address": r["address"] or "",
            "phone": r["phone"] or "",
            "is_customer": bool(r["is_customer"]),
            "is_vendor": bool(r["is_vendor"]),
            "partner_type": r["partner_type"],
            "current_debt": debt_val,
            "current_debt_fmt": _fmt(debt_val),
            "trip_count": trip_counts.get(r["id"], 0),
            "opening_debt_dr_131": Decimal(str(r["opening_debt_dr_131"] or 0)),
            "opening_debt_cr_131": Decimal(str(r["opening_debt_cr_131"] or 0)),
            "opening_debt_dr_331": Decimal(str(r["opening_debt_dr_331"] or 0)),
            "opening_debt_cr_331": Decimal(str(r["opening_debt_cr_331"] or 0)),
        })

    total_pages = max(1, (total + per_page - 1) // per_page)
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


def get_ledger_131_sheet2(
    company_id: int | None = None,
    year: int = 2026,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str = "",
) -> dict[str, Any]:
    """Retrieve Sheet 2: Sổ chi tiết TK 131 - Phải thu khách hàng.
    Columns: STT, MST, Tên KH, Số đầu kỳ (Nợ/Có), PS trong kỳ (Nợ/Có), Số dư cuối kỳ (Nợ/Có).
    """
    if date_from is None:
        date_from = date(year, 1, 1)
    if date_to is None:
        date_to = date(year, 12, 31)

    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return {
            "year": year,
            "date_from": date_from,
            "date_to": date_to,
            "entries": [],
            "total_count": 0,
            "total_open_dr": 0,
            "total_open_cr": 0,
            "total_ps_dr": 0,
            "total_ps_cr": 0,
            "total_close_dr": 0,
            "total_close_cr": 0,
            "company": None,
        }

    where_q = ""
    params_q: list[Any] = []
    if q and q.strip():
        term = f"%{q.strip()}%"
        where_q = "AND (c.name ILIKE %s OR c.tax_code ILIKE %s)"
        params_q = [term, term]

    # Customers query
    sql = f"""
        SELECT c.id, c.name, c.tax_code, c.address,
               COALESCE(c.opening_debt_dr_131, 0) as open_dr,
               COALESCE(c.opening_debt_cr_131, 0) as open_cr
        FROM public.invoice_companies c
        WHERE COALESCE(c.is_customer, TRUE) = TRUE
        {where_q}
        ORDER BY c.name ASC
    """
    customers = db._execute_query(sql, tuple(params_q), fetchall=True, commit=False) or []

    # Phát sinh Nợ 131 (Hóa đơn xuất ra / Cước vận chuyển)
    ps_dr_rows = db._execute_query("""
        SELECT buyer_company_id, COALESCE(SUM(gross), 0) as total_gross
        FROM public.bkav_invoices
        WHERE seller_company_id = %s AND invoice_date >= %s AND invoice_date <= %s
        GROUP BY buyer_company_id
    """, (company_id, date_from, date_to), fetchall=True, commit=False) or []
    ps_dr_map = {r["buyer_company_id"]: Decimal(str(r["total_gross"] or 0)) for r in ps_dr_rows}

    # Phát sinh Có 131:
    # 1. Thu tiền ngay từ HĐ (payment_status = 'paid')
    paid_inv_rows = db._execute_query("""
        SELECT buyer_company_id, COALESCE(SUM(gross), 0) as total_paid
        FROM public.bkav_invoices
        WHERE seller_company_id = %s AND invoice_date >= %s AND invoice_date <= %s AND payment_status = 'paid'
        GROUP BY buyer_company_id
    """, (company_id, date_from, date_to), fetchall=True, commit=False) or []
    ps_cr_map = {r["buyer_company_id"]: Decimal(str(r["total_paid"] or 0)) for r in paid_inv_rows}

    # 2. Khách trả nợ thêm qua Sổ quỹ tiền mặt / Báo Có ngân hàng
    cash_receipt_rows = db._execute_query("""
        SELECT COALESCE(partner_code, '') as p_code, COALESCE(SUM(amount), 0) as total_rec
        FROM public.transport_cash_book
        WHERE company_id = %s AND voucher_type = 'receipt' AND offset_account LIKE '131%%'
          AND entry_date >= %s AND entry_date <= %s
        GROUP BY partner_code
    """, (company_id, date_from, date_to), fetchall=True, commit=False) or []
    cash_receipt_map = {r["p_code"].strip(): Decimal(str(r["total_rec"] or 0)) for r in cash_receipt_rows if r.get("p_code")}

    # 3. Khách trả nợ qua company_debt_logs (nếu có)
    extra_pay_rows = db._execute_query("""
        SELECT company_id, COALESCE(SUM(amount), 0) as extra_paid
        FROM public.company_debt_logs
        WHERE (owner_company_id = %s OR owner_company_id IS NULL)
          AND debt_type IN ('payment', 'paid') AND recorded_date >= %s AND recorded_date <= %s
        GROUP BY company_id
    """, (company_id, date_from, date_to), fetchall=True, commit=False) or []
    for r in extra_pay_rows:
        cid = r["company_id"]
        amt = Decimal(str(r["extra_paid"] or 0))
        ps_cr_map[cid] = ps_cr_map.get(cid, Decimal(0)) + amt

    rows = []
    tot_open_dr = Decimal(0)
    tot_open_cr = Decimal(0)
    tot_ps_dr = Decimal(0)
    tot_ps_cr = Decimal(0)
    tot_close_dr = Decimal(0)
    tot_close_cr = Decimal(0)

    for c in customers:
        cid = c["id"]
        o_dr = Decimal(str(c["open_dr"] or 0))
        o_cr = Decimal(str(c["open_cr"] or 0))
        p_dr = ps_dr_map.get(cid, Decimal(0))
        p_cr = ps_cr_map.get(cid, Decimal(0))

        p_cr += cash_receipt_map.get((c["tax_code"] or "").strip(), Decimal(0))

        # Dư cuối kỳ
        net_diff = (o_dr - o_cr) + (p_dr - p_cr)
        c_dr = net_diff if net_diff > 0 else Decimal(0)
        c_cr = -net_diff if net_diff < 0 else Decimal(0)

        # Only list customers that have either opening balance or in-period transactions or debt
        if o_dr == 0 and o_cr == 0 and p_dr == 0 and p_cr == 0 and c_dr == 0 and c_cr == 0 and not q:
            continue

        tot_open_dr += o_dr
        tot_open_cr += o_cr
        tot_ps_dr += p_dr
        tot_ps_cr += p_cr
        tot_close_dr += c_dr
        tot_close_cr += c_cr

        rows.append({
            "id": cid,
            "name": c["name"],
            "tax_code": c["tax_code"] or "",
            "open_dr": o_dr,
            "open_cr": o_cr,
            "open_dr_fmt": _fmt(o_dr),
            "open_cr_fmt": _fmt(o_cr),
            "ps_dr": p_dr,
            "ps_cr": p_cr,
            "ps_dr_fmt": _fmt(p_dr),
            "ps_cr_fmt": _fmt(p_cr),
            "close_dr": c_dr,
            "close_cr": c_cr,
            "close_dr_fmt": _fmt(c_dr),
            "close_cr_fmt": _fmt(c_cr),
            "detail_url": f"/admin/transport/catalog/companies/{cid}/subledger?account=131",
        })

    return {
        "rows": rows,
        "total_count": len(rows),
        "totals": {
            "open_dr": tot_open_dr,
            "open_cr": tot_open_cr,
            "open_dr_fmt": _fmt(tot_open_dr),
            "open_cr_fmt": _fmt(tot_open_cr),
            "ps_dr": tot_ps_dr,
            "ps_cr": tot_ps_cr,
            "ps_dr_fmt": _fmt(tot_ps_dr),
            "ps_cr_fmt": _fmt(tot_ps_cr),
            "close_dr": tot_close_dr,
            "close_cr": tot_close_cr,
            "close_dr_fmt": _fmt(tot_close_dr),
            "close_cr_fmt": _fmt(tot_close_cr),
        }
    }


def get_ledger_331_sheet3(
    company_id: int | None = None,
    year: int = 2026,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str = "",
) -> dict[str, Any]:
    """Retrieve Sheet 3: Sổ chi tiết TK 331 - Phải trả người bán.
    Columns: STT, MST, Tên NCC, Số đầu kỳ (Nợ/Có), PS trong kỳ (Nợ/Có), Số dư cuối kỳ (Nợ/Có).
    """
    if date_from is None:
        date_from = date(year, 1, 1)
    if date_to is None:
        date_to = date(year, 12, 31)

    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return {
            "year": year,
            "date_from": date_from,
            "date_to": date_to,
            "entries": [],
            "total_count": 0,
            "total_open_dr": 0,
            "total_open_cr": 0,
            "total_ps_dr": 0,
            "total_ps_cr": 0,
            "total_close_dr": 0,
            "total_close_cr": 0,
            "company": None,
        }

    where_q = ""
    params_q: list[Any] = []
    if q and q.strip():
        term = f"%{q.strip()}%"
        where_q = "AND (c.name ILIKE %s OR c.tax_code ILIKE %s)"
        params_q = [term, term]

    sql = f"""
        SELECT c.id, c.name, c.tax_code, c.address,
               COALESCE(c.opening_debt_dr_331, 0) as open_dr,
               COALESCE(c.opening_debt_cr_331, 0) as open_cr
        FROM public.invoice_companies c
        WHERE COALESCE(c.is_vendor, FALSE) = TRUE
        {where_q}
        ORDER BY c.name ASC
    """
    vendors = db._execute_query(sql, tuple(params_q), fetchall=True, commit=False) or []

    # Phát sinh Có 331: Hóa đơn mua vào từ nhà cung cấp (transport_input_invoices)
    ps_cr_rows = db._execute_query("""
        SELECT COALESCE(seller_company_id, 0) as s_cid, seller_tax_code,
               COALESCE(SUM(total), 0) as total_bought
        FROM public.transport_input_invoices
        WHERE buyer_company_id = %s AND invoice_date >= %s AND invoice_date <= %s
        GROUP BY seller_company_id, seller_tax_code
    """, (company_id, date_from, date_to), fetchall=True, commit=False) or []

    ps_cr_by_cid: dict[int, Decimal] = {}
    ps_cr_by_tax: dict[str, Decimal] = {}
    for r in ps_cr_rows:
        amt = Decimal(str(r["total_bought"] or 0))
        if r["s_cid"]:
            ps_cr_by_cid[r["s_cid"]] = ps_cr_by_cid.get(r["s_cid"], Decimal(0)) + amt
        if r.get("seller_tax_code"):
            clean_tax = r["seller_tax_code"].strip()
            ps_cr_by_tax[clean_tax] = ps_cr_by_tax.get(clean_tax, Decimal(0)) + amt

    # Phát sinh Nợ 331: Đã thanh toán cho NCC qua Sổ quỹ tiền mặt hoặc qua ngân hàng
    ps_dr_rows = db._execute_query("""
        SELECT COALESCE(partner_code, '') as p_code,
               COALESCE(SUM(amount), 0) as total_paid
        FROM public.transport_cash_book
        WHERE company_id = %s AND voucher_type = 'payment' AND offset_account LIKE '331%%'
          AND entry_date >= %s AND entry_date <= %s
        GROUP BY partner_code
    """, (company_id, date_from, date_to), fetchall=True, commit=False) or []
    ps_dr_by_tax: dict[str, Decimal] = {}
    for r in ps_dr_rows:
        clean_code = (r.get("p_code") or "").strip()
        if clean_code:
            ps_dr_by_tax[clean_code] = Decimal(str(r["total_paid"] or 0))

    rows = []
    tot_open_dr = Decimal(0)
    tot_open_cr = Decimal(0)
    tot_ps_dr = Decimal(0)
    tot_ps_cr = Decimal(0)
    tot_close_dr = Decimal(0)
    tot_close_cr = Decimal(0)

    for v in vendors:
        vid = v["id"]
        vtax = (v["tax_code"] or "").strip()
        o_dr = Decimal(str(v["open_dr"] or 0))
        o_cr = Decimal(str(v["open_cr"] or 0))

        p_cr = ps_cr_by_cid.get(vid) or (ps_cr_by_tax.get(vtax) if vtax else Decimal(0)) or Decimal(0)
        p_dr = (ps_dr_by_tax.get(vtax) if vtax else Decimal(0)) or Decimal(0)

        # Dư cuối kỳ cho TK 331: (Đầu kỳ Có - Nợ) + (Phát sinh Có - Nợ)
        net_diff = (o_cr - o_dr) + (p_cr - p_dr)
        c_cr = net_diff if net_diff > 0 else Decimal(0)
        c_dr = -net_diff if net_diff < 0 else Decimal(0)

        if o_dr == 0 and o_cr == 0 and p_dr == 0 and p_cr == 0 and c_dr == 0 and c_cr == 0 and not q:
            continue

        tot_open_dr += o_dr
        tot_open_cr += o_cr
        tot_ps_dr += p_dr
        tot_ps_cr += p_cr
        tot_close_dr += c_dr
        tot_close_cr += c_cr

        rows.append({
            "id": vid,
            "name": v["name"],
            "tax_code": vtax,
            "open_dr": o_dr,
            "open_cr": o_cr,
            "open_dr_fmt": _fmt(o_dr),
            "open_cr_fmt": _fmt(o_cr),
            "ps_dr": p_dr,
            "ps_cr": p_cr,
            "ps_dr_fmt": _fmt(p_dr),
            "ps_cr_fmt": _fmt(p_cr),
            "close_dr": c_dr,
            "close_cr": c_cr,
            "close_dr_fmt": _fmt(c_dr),
            "close_cr_fmt": _fmt(c_cr),
            "detail_url": f"/admin/transport/catalog/companies/{vid}/subledger?account=331",
        })

    return {
        "rows": rows,
        "total_count": len(rows),
        "totals": {
            "open_dr": tot_open_dr,
            "open_cr": tot_open_cr,
            "open_dr_fmt": _fmt(tot_open_dr),
            "open_cr_fmt": _fmt(tot_open_cr),
            "ps_dr": tot_ps_dr,
            "ps_cr": tot_ps_cr,
            "ps_dr_fmt": _fmt(tot_ps_dr),
            "ps_cr_fmt": _fmt(tot_ps_cr),
            "close_dr": tot_close_dr,
            "close_cr": tot_close_cr,
            "close_dr_fmt": _fmt(tot_close_dr),
            "close_cr_fmt": _fmt(tot_close_cr),
        }
    }


def update_partner_flags(partner_id: int, is_customer: bool, is_vendor: bool, partner_type: str | None = None) -> bool:
    """Update KH/NCC role checkboxes and partner type."""
    updates = ["is_customer = %s", "is_vendor = %s"]
    params: list[Any] = [is_customer, is_vendor]
    if partner_type:
        updates.append("partner_type = %s")
        params.append(partner_type)
    params.append(partner_id)
    sql = f"UPDATE public.invoice_companies SET {', '.join(updates)} WHERE id = %s"
    db._execute_query(sql, tuple(params), commit=True)
    return True


def update_partner_opening_debts(
    partner_id: int,
    account: str,  # '131' or '331'
    open_dr: Decimal | int | float = 0,
    open_cr: Decimal | int | float = 0,
) -> bool:
    """Update opening balances for 131 or 331 for a partner."""
    if account == "131":
        sql = "UPDATE public.invoice_companies SET opening_debt_dr_131 = %s, opening_debt_cr_131 = %s WHERE id = %s"
    elif account == "331":
        sql = "UPDATE public.invoice_companies SET opening_debt_dr_331 = %s, opening_debt_cr_331 = %s WHERE id = %s"
    else:
        return False
    db._execute_query(sql, (open_dr, open_cr, partner_id), commit=True)
    return True


def get_partner_subledger(
    owner_company_id: int | None = None,
    partner_id: int = 0,
    account: str = "331",
    year: int = 2026,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Retrieve Sổ chi tiết công nợ (Subledger) for a single partner (Mẫu số S31-DNN / S32-DNN).
    Acc 331: Phải trả người bán (Mẫu S31-DNN)
    Acc 131: Phải thu khách hàng (Mẫu S32-DNN)
    Complies with Circular 133/2016/TT-BTC.
    """
    owner_company_id = owner_company_id or get_current_company_id()
    if not owner_company_id or int(owner_company_id) <= 0:
        return {"error": "Vui lòng chọn đơn vị làm việc để xem sổ công nợ."}
    if date_from is None:
        date_from = date(year, 1, 1)
    if date_to is None:
        date_to = date(year, 12, 31)

    account_clean = "131" if str(account).strip().startswith("131") else "331"

    partner = db._execute_query(
        "SELECT id, name, tax_code, address, phone, partner_type, is_customer, is_vendor, "
        "COALESCE(opening_debt_dr_131, 0) as opening_debt_dr_131, "
        "COALESCE(opening_debt_cr_131, 0) as opening_debt_cr_131, "
        "COALESCE(opening_debt_dr_331, 0) as opening_debt_dr_331, "
        "COALESCE(opening_debt_cr_331, 0) as opening_debt_cr_331 "
        "FROM public.invoice_companies WHERE id = %s",
        (partner_id,), fetch=True
    )
    if not partner:
        return {"error": "Không tìm thấy đối tác."}

    owner = db._execute_query(
        "SELECT id, name, tax_code, address FROM public.invoice_companies WHERE id = %s",
        (owner_company_id,), fetch=True
    ) or {
        "id": owner_company_id,
        "name": "CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH",
        "tax_code": "2500774911",
        "address": "Khu Đồng Hòa, Phường Vĩnh Yên, Tỉnh Vĩnh Phúc",
    }

    p_tax = (partner.get("tax_code") or "").strip()
    p_name = partner.get("name") or ""
    entries: list[dict[str, Any]] = []

    if account_clean == "331":
        account_title = "TK 331 - Phải trả cho người bán"
        form_code = "Mẫu số S31-DNN"
        open_dr = Decimal(str(partner.get("opening_debt_dr_331") or 0))
        open_cr = Decimal(str(partner.get("opening_debt_cr_331") or 0))

        # A. Input Invoices (Phát sinh Có 331)
        invs = db._execute_query("""
            SELECT id, invoice_number, serial, invoice_date, total, subtotal, vat_amount,
                   COALESCE(NULLIF(note, ''), 'Mua xăng dầu / Phụ tùng / Dịch vụ') as description,
                   COALESCE(tk_no_cost, '154') as tk_no_cost,
                   COALESCE(tk_no_vat, '1331') as tk_no_vat,
                   COALESCE(tk_co_pay, '331') as tk_co_pay
            FROM public.transport_input_invoices
            WHERE buyer_company_id = %s
              AND (seller_company_id = %s OR (seller_tax_code <> '' AND seller_tax_code = %s))
              AND invoice_date >= %s AND invoice_date <= %s
            ORDER BY invoice_date ASC, id ASC
        """, (owner_company_id, partner_id, p_tax, date_from, date_to), fetchall=True) or []

        for inv in invs:
            inv_num = inv.get("invoice_number") or ""
            inv_date = inv.get("invoice_date")
            total_amt = Decimal(str(inv.get("total") or 0))
            offset_tk = f"{inv.get('tk_no_cost') or '154'}, {inv.get('tk_no_vat') or '1331'}"
            entries.append({
                "date": inv_date,
                "date_str": inv_date.strftime("%d/%m/%Y") if inv_date else "",
                "voucher_no": f"HĐ {inv_num}" if inv_num else "HĐ —",
                "voucher_date_str": inv_date.strftime("%d/%m/%Y") if inv_date else "",
                "serial": inv.get("serial") or "",
                "description": inv.get("description") or "Mua xăng dầu / Hàng hóa / Dịch vụ",
                "offset_account": offset_tk,
                "ps_dr": Decimal(0),
                "ps_cr": total_amt,
                "doc_type": "Hóa đơn mua vào",
                "doc_link": f"/admin/transport/input-invoices/{inv['id']}/edit",
            })

        # B. Cash / Bank Payments (Phát sinh Nợ 331)
        tax_cond = "partner_code = %s" if p_tax else "1=0"
        pays = db._execute_query(f"""
            SELECT id, voucher_no, entry_date, amount, offset_account, description, book_type
            FROM public.transport_cash_book
            WHERE company_id = %s
              AND voucher_type = 'payment'
              AND offset_account LIKE '331%%'
              AND ({tax_cond} OR (partner_name ILIKE %s))
              AND entry_date >= %s AND entry_date <= %s
            ORDER BY entry_date ASC, id ASC
        """, (owner_company_id, *( [p_tax] if p_tax else [] ), f"%{p_name}%", date_from, date_to), fetchall=True) or []

        for pay in pays:
            pay_date = pay.get("entry_date")
            amt = Decimal(str(pay.get("amount") or 0))
            b_type = pay.get("book_type") or "cash"
            offset_tk = "1121" if b_type == "bank" else "1111"
            entries.append({
                "date": pay_date,
                "date_str": pay_date.strftime("%d/%m/%Y") if pay_date else "",
                "voucher_no": pay.get("voucher_no") or f"PC-{pay['id']}",
                "voucher_date_str": pay_date.strftime("%d/%m/%Y") if pay_date else "",
                "serial": "",
                "description": pay.get("description") or "Chi trả tiền nhà cung cấp",
                "offset_account": offset_tk,
                "ps_dr": amt,
                "ps_cr": Decimal(0),
                "doc_type": "Ủy nhiệm chi" if b_type == "bank" else "Phiếu chi tiền",
                "doc_link": f"/admin/transport/bank-book?id={pay['id']}" if b_type == "bank" else f"/admin/transport/cash-book?id={pay['id']}",
            })

    else:  # TK 131 - Phải thu khách hàng
        account_title = "TK 131 - Phải thu của khách hàng"
        form_code = "Mẫu số S32-DNN"
        open_dr = Decimal(str(partner.get("opening_debt_dr_131") or 0))
        open_cr = Decimal(str(partner.get("opening_debt_cr_131") or 0))

        # A. Sales invoices (Phát sinh Nợ 131)
        invs = db._execute_query("""
            SELECT b.invoice_guid, b.invoice_date, b.invoice_number, b.invoice_serial,
                   b.service_name, b.gross, b.payment_status, b.debt_amount
            FROM public.bkav_invoices b
            WHERE b.seller_company_id = %s
              AND (b.buyer_company_id = %s OR (b.buyer_tax_code <> '' AND b.buyer_tax_code = %s))
              AND b.invoice_date >= %s AND b.invoice_date <= %s
            ORDER BY b.invoice_date ASC, b.invoice_number ASC
        """, (owner_company_id, partner_id, p_tax, date_from, date_to), fetchall=True) or []

        for inv in invs:
            inv_date = inv.get("invoice_date")
            inv_num = inv.get("invoice_number") or ""
            gross = Decimal(str(inv.get("gross") or 0))
            entries.append({
                "date": inv_date,
                "date_str": inv_date.strftime("%d/%m/%Y") if inv_date else "",
                "voucher_no": f"HĐ {inv_num}" if inv_num else "HĐ —",
                "voucher_date_str": inv_date.strftime("%d/%m/%Y") if inv_date else "",
                "serial": inv.get("invoice_serial") or "",
                "description": inv.get("service_name") or "Cước dịch vụ vận chuyển hàng hóa",
                "offset_account": "5113, 33311",
                "ps_dr": gross,
                "ps_cr": Decimal(0),
                "doc_type": "Hóa đơn GTGT",
                "doc_link": f"/admin/vehicles/cargo/invoices/{inv['invoice_guid']}/edit" if inv.get("invoice_guid") else None,
            })

            # If paid immediately
            if inv.get("payment_status") == "paid":
                entries.append({
                    "date": inv_date,
                    "date_str": inv_date.strftime("%d/%m/%Y") if inv_date else "",
                    "voucher_no": f"TT-HĐ {inv_num}" if inv_num else "Thu tiền",
                    "voucher_date_str": inv_date.strftime("%d/%m/%Y") if inv_date else "",
                    "serial": "",
                    "description": f"Thu tiền ngay theo HĐ số {inv_num}" if inv_num else "Thu tiền khách hàng",
                    "offset_account": "1111",
                    "ps_dr": Decimal(0),
                    "ps_cr": gross,
                    "doc_type": "Phiếu thu / Báo Có",
                    "doc_link": None,
                })

        # B. Cash / Bank Receipts from cash book (Phát sinh Có 131)
        tax_cond = "partner_code = %s" if p_tax else "1=0"
        receipts = db._execute_query(f"""
            SELECT id, voucher_no, entry_date, amount, offset_account, description, book_type
            FROM public.transport_cash_book
            WHERE company_id = %s
              AND voucher_type = 'receipt'
              AND offset_account LIKE '131%%'
              AND ({tax_cond} OR (partner_name ILIKE %s))
              AND entry_date >= %s AND entry_date <= %s
            ORDER BY entry_date ASC, id ASC
        """, (owner_company_id, *( [p_tax] if p_tax else [] ), f"%{p_name}%", date_from, date_to), fetchall=True) or []

        for rec in receipts:
            rec_date = rec.get("entry_date")
            amt = Decimal(str(rec.get("amount") or 0))
            b_type = rec.get("book_type") or "cash"
            offset_tk = "1121" if b_type == "bank" else "1111"
            entries.append({
                "date": rec_date,
                "date_str": rec_date.strftime("%d/%m/%Y") if rec_date else "",
                "voucher_no": rec.get("voucher_no") or f"PT-{rec['id']}",
                "voucher_date_str": rec_date.strftime("%d/%m/%Y") if rec_date else "",
                "serial": "",
                "description": rec.get("description") or "Thu tiền khách hàng trả nợ",
                "offset_account": offset_tk,
                "ps_dr": Decimal(0),
                "ps_cr": amt,
                "doc_type": "Báo Có ngân hàng" if b_type == "bank" else "Phiếu thu tiền",
                "doc_link": f"/admin/transport/bank-book?id={rec['id']}" if b_type == "bank" else f"/admin/transport/cash-book?id={rec['id']}",
            })

    # Sort all entries chronologically
    entries.sort(key=lambda x: (x["date"] or date.min))

    # Calculate running balances
    tot_ps_dr = Decimal(0)
    tot_ps_cr = Decimal(0)

    if account_clean == "331":
        # Sổ 331 (Phải trả NCC): Dư Có ban đầu = open_cr - open_dr
        running_net = open_cr - open_dr
        for e in entries:
            tot_ps_dr += e["ps_dr"]
            tot_ps_cr += e["ps_cr"]
            running_net += (e["ps_cr"] - e["ps_dr"])
            if running_net >= 0:
                e["bal_cr"] = running_net
                e["bal_dr"] = Decimal(0)
            else:
                e["bal_cr"] = Decimal(0)
                e["bal_dr"] = -running_net
            e["ps_dr_fmt"] = _fmt(e["ps_dr"])
            e["ps_cr_fmt"] = _fmt(e["ps_cr"])
            e["bal_dr_fmt"] = _fmt(e["bal_dr"]) if e["bal_dr"] > 0 else "—"
            e["bal_cr_fmt"] = _fmt(e["bal_cr"]) if e["bal_cr"] > 0 else "—"

        net_close = (open_cr - open_dr) + (tot_ps_cr - tot_ps_dr)
        close_cr = net_close if net_close > 0 else Decimal(0)
        close_dr = -net_close if net_close < 0 else Decimal(0)

    else:
        # Sổ 131 (Phải thu KH): Dư Nợ ban đầu = open_dr - open_cr
        running_net = open_dr - open_cr
        for e in entries:
            tot_ps_dr += e["ps_dr"]
            tot_ps_cr += e["ps_cr"]
            running_net += (e["ps_dr"] - e["ps_cr"])
            if running_net >= 0:
                e["bal_dr"] = running_net
                e["bal_cr"] = Decimal(0)
            else:
                e["bal_dr"] = Decimal(0)
                e["bal_cr"] = -running_net
            e["ps_dr_fmt"] = _fmt(e["ps_dr"])
            e["ps_cr_fmt"] = _fmt(e["ps_cr"])
            e["bal_dr_fmt"] = _fmt(e["bal_dr"]) if e["bal_dr"] > 0 else "—"
            e["bal_cr_fmt"] = _fmt(e["bal_cr"]) if e["bal_cr"] > 0 else "—"

        net_close = (open_dr - open_cr) + (tot_ps_dr - tot_ps_cr)
        close_dr = net_close if net_close > 0 else Decimal(0)
        close_cr = -net_close if net_close < 0 else Decimal(0)

    return {
        "owner_company": owner,
        "partner": partner,
        "account": account_clean,
        "account_title": account_title,
        "form_code": form_code,
        "circular": "Thông tư số 133/2016/TT-BTC ngày 26/08/2016 của Bộ Tài chính",
        "year": year,
        "date_from": date_from,
        "date_to": date_to,
        "date_from_fmt": date_from.strftime("%d/%m/%Y"),
        "date_to_fmt": date_to.strftime("%d/%m/%Y"),
        "open_dr": open_dr,
        "open_cr": open_cr,
        "open_dr_fmt": _fmt(open_dr),
        "open_cr_fmt": _fmt(open_cr),
        "tot_ps_dr": tot_ps_dr,
        "tot_ps_cr": tot_ps_cr,
        "tot_ps_dr_fmt": _fmt(tot_ps_dr),
        "tot_ps_cr_fmt": _fmt(tot_ps_cr),
        "close_dr": close_dr,
        "close_cr": close_cr,
        "close_dr_fmt": _fmt(close_dr),
        "close_cr_fmt": _fmt(close_cr),
        "entries": entries,
        "total_entries": len(entries),
    }
