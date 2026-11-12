"""Service for Cash Book (Mau S04a-DNN) and Bank Book (Mau S05a-DNN) under Circular 133/2016/TT-BTC."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import logging
import re
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from backend.services.database import db
from frontend.context.company_context import get_current_company_id
from frontend.formatting.money import parse_vnd

logger = logging.getLogger(__name__)

DAI_LO_XANH_COMPANY_ID = 33

# Số dư ban đầu mặc định
CASH_DEFAULT_OPENING = Decimal("0")               # TK 111: 0 đ
BANK_DEFAULT_OPENING = Decimal("0")               # TK 112: 0 đ


def parse_flexible_date(raw_date: Any) -> date:
    """Parse various date formats (ISO, DD/MM/YYYY, localized VN strings) reliably."""
    if not raw_date:
        return date.today()
    if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
        return raw_date
    if isinstance(raw_date, datetime):
        return raw_date.date()
    s = str(raw_date).strip()
    if not s:
        return date.today()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    # Localized VN strings like "ngày 6 thg 5, 2026" or "06/05/2026"
    m = re.search(r"(\d{1,2})[^\d]+(\d{1,2})[^\d]+(\d{4})", s)
    if m:
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return date(y, mo, d)
        except ValueError:
            pass
    m2 = re.search(r"(\d{4})[^\d]+(\d{1,2})[^\d]+(\d{1,2})", s)
    if m2:
        try:
            y, mo, d = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
            return date(y, mo, d)
        except ValueError:
            pass
    raise ValueError(f"Ngày không hợp lệ: {raw_date}")


from functools import lru_cache


@lru_cache(maxsize=64)
def _fetch_company_info(company_id: int) -> dict[str, str]:
    comp = db._execute_query(
        "SELECT name, address, tax_code, bank_account, bank_name FROM public.invoice_companies WHERE id = %s",
        (company_id,), fetch=True, commit=False
    ) or {}
    return {
        "name": comp.get("name") or "",
        "address": comp.get("address") or "",
        "tax_code": comp.get("tax_code") or "",
        "bank_account": comp.get("bank_account") or "",
        "bank_name": comp.get("bank_name") or "",
    }


def get_company_info(company_id: int | None = None) -> dict[str, str]:
    """Retrieve company name, address, tax code and bank info from DB (cached)."""
    company_id = company_id or get_current_company_id()
    if not company_id:
        return {"name": "", "address": "", "tax_code": "", "bank_account": "", "bank_name": ""}
    return _fetch_company_info(int(company_id))


def get_opening_balance_setting(book_type: str, company_id: int | None = None) -> Decimal:
    """Retrieve opening balance from app_settings or fallback to default."""
    company_id = company_id or get_current_company_id()
    if not company_id:
        return Decimal(0)
    b_type = "bank" if "bank" in book_type.lower() or "ngân hàng" in book_type.lower() else "cash"
    key = f"transport_{b_type}_opening_balance_{company_id}"
    row = db._execute_query(
        "SELECT value FROM public.app_settings WHERE key = %s",
        (key,), fetch=True, commit=False
    )
    if row and row.get("value") is not None and str(row.get("value")).strip() != "":
        try:
            return parse_vnd(row["value"])
        except ValueError:
            pass
    return BANK_DEFAULT_OPENING if b_type == "bank" else CASH_DEFAULT_OPENING


def set_opening_balance_setting(book_type: str, amount: Any, company_id: int | None = None) -> tuple[bool, str]:
    """Save user-configured opening balance into app_settings."""
    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return False, "Chưa chọn công ty làm việc."
    b_type = "bank" if "bank" in book_type.lower() or "ngân hàng" in book_type.lower() else "cash"
    key = f"transport_{b_type}_opening_balance_{company_id}"

    try:
        val = parse_vnd(amount) if str(amount or "").strip() else Decimal("0")
    except ValueError:
        return False, "Số tiền số dư đầu kỳ không hợp lệ."
    db._execute_query('''
        INSERT INTO public.app_settings (key, value, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
    ''', (key, str(val)), commit=True)
    b_name = "Sổ tiền gửi ngân hàng (TK 112)" if b_type == "bank" else "Sổ quỹ tiền mặt (TK 111)"
    return True, f"Đã cập nhật số dư đầu kỳ cho {b_name} thành {int(val):,} đ."


def sync_cash_bank_transfers(company_id: int | None = None) -> int:
    """
    Tự động đồng bộ liên kết 2 chiều giữa Sổ quỹ tiền mặt (TK 111) và Sổ tiền gửi NH (TK 112):
    1. Khi Phiếu chi tiền mặt có TK đối 112 (112, 1121...) -> Tự động sinh/cập nhật Báo Có trên Sổ ngân hàng (TK đối 1111).
    2. Khi Phiếu thu tiền mặt có TK đối 112 (112, 1121...) -> Tự động sinh/cập nhật Báo Nợ trên Sổ ngân hàng (TK đối 1111).
    3. Ngược lại, khi Giao dịch ngân hàng có TK đối 111 (111, 1111...) -> Tự động sinh/cập nhật Phiếu thu/chi tiền mặt.
    4. Tự động dọn dẹp các chứng từ liên kết mồ côi nếu phiếu gốc bị xóa hoặc đổi sang TK đối khác.
    """
    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return 0
    comp_info = get_company_info(company_id)
    bank_acc = comp_info.get("bank_account") or ""
    bank_name = comp_info.get("bank_name") or ""
    synced_count = 0

    # 1. Đồng bộ Cash -> Bank
    cash_transfers = db._execute_query('''
        SELECT id, company_id, book_type, entry_date, voucher_date, voucher_no, voucher_type,
               description, offset_account, amount, partner_name, partner_code, note
        FROM public.transport_cash_book
        WHERE company_id = %s 
          AND book_type = 'cash' 
          AND (offset_account LIKE '112%%' OR offset_account = '112')
          AND (source_type IS NULL OR source_type != 'bank_transfer')
        ORDER BY id ASC
    ''', (company_id,), fetchall=True, commit=False) or []

    valid_cash_ids = set()
    for c in cash_transfers:
        cid = c["id"]
        valid_cash_ids.add(str(cid))
        is_payment = (c["voucher_type"] == "payment")
        target_v_type = "receipt" if is_payment else "payment"
        target_offset = "1111" if str(c.get("offset_account") or "").endswith("1") else "111"

        c_vno = (c.get("voucher_no") or "").strip()
        target_vno = f"BC-{c_vno}" if target_v_type == "receipt" else f"UNC-{c_vno}"
        target_desc = c.get("description") or ("Nộp tiền vào TK ngân hàng" if target_v_type == "receipt" else "Rút tiền ngân hàng về quỹ")
        target_amount = c["amount"]
        target_date = c["voucher_date"] or c["entry_date"]
        target_note = c.get("note") or f"Liên kết tự động từ Phiếu {c_vno} (Sổ quỹ tiền mặt)"

        existing_bank = db._execute_query('''
            SELECT id, voucher_no, voucher_type, amount, voucher_date, description, offset_account
            FROM public.transport_cash_book
            WHERE company_id = %s AND book_type = 'bank' AND source_type = 'cash_transfer' AND source_id = %s
        ''', (company_id, str(cid)), fetch=True, commit=False)

        if not existing_bank:
            db._execute_query('''
                INSERT INTO public.transport_cash_book
                (company_id, book_type, entry_date, voucher_date, voucher_no, voucher_type,
                 description, offset_account, amount, bank_account_no, bank_name,
                 partner_name, partner_code, source_type, source_id, note)
                VALUES (%s, 'bank', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'cash_transfer', %s, %s)
            ''', (
                company_id, target_date, target_date, target_vno, target_v_type,
                target_desc, target_offset, target_amount, bank_acc, bank_name,
                c.get("partner_name") or "", c.get("partner_code") or "",
                str(cid), target_note
            ), commit=True)
            synced_count += 1
        else:
            db._execute_query('''
                UPDATE public.transport_cash_book
                SET entry_date = %s, voucher_date = %s, voucher_type = %s,
                    description = %s, offset_account = %s, amount = %s,
                    bank_account_no = COALESCE(bank_account_no, %s),
                    bank_name = COALESCE(bank_name, %s),
                    partner_name = %s, partner_code = %s, note = %s
                WHERE id = %s
            ''', (
                target_date, target_date, target_v_type,
                target_desc, target_offset, target_amount,
                bank_acc, bank_name,
                c.get("partner_name") or "", c.get("partner_code") or "", target_note,
                existing_bank["id"]
            ), commit=True)

    # Dọn dẹp bank mồ côi
    if valid_cash_ids:
        db._execute_query('''
            DELETE FROM public.transport_cash_book
            WHERE company_id = %s AND book_type = 'bank' AND source_type = 'cash_transfer'
              AND (source_id IS NULL OR source_id NOT IN %s)
        ''', (company_id, tuple(valid_cash_ids)), commit=True)
    else:
        db._execute_query('''
            DELETE FROM public.transport_cash_book
            WHERE company_id = %s AND book_type = 'bank' AND source_type = 'cash_transfer'
        ''', (company_id,), commit=True)

    # 2. Đồng bộ Bank -> Cash
    bank_transfers = db._execute_query('''
        SELECT id, company_id, book_type, entry_date, voucher_date, voucher_no, voucher_type,
               description, offset_account, amount, partner_name, partner_code, note
        FROM public.transport_cash_book
        WHERE company_id = %s 
          AND book_type = 'bank' 
          AND (offset_account LIKE '111%%' OR offset_account = '111')
          AND (source_type IS NULL OR source_type != 'cash_transfer')
        ORDER BY id ASC
    ''', (company_id,), fetchall=True, commit=False) or []

    valid_bank_ids = set()
    for b in bank_transfers:
        bid = b["id"]
        valid_bank_ids.add(str(bid))
        is_receipt = (b["voucher_type"] == "receipt")
        target_v_type = "payment" if is_receipt else "receipt"
        target_offset = "1121" if str(b.get("offset_account") or "").endswith("1") else "112"

        b_vno = (b.get("voucher_no") or "").strip()
        target_vno = f"PC-{b_vno}" if target_v_type == "payment" else f"PT-{b_vno}"
        target_desc = b.get("description") or ("Nộp tiền vào TK ngân hàng" if target_v_type == "payment" else "Rút tiền ngân hàng về quỹ")
        target_amount = b["amount"]
        target_date = b["voucher_date"] or b["entry_date"]
        target_note = b.get("note") or f"Liên kết tự động từ Giao dịch {b_vno} (Sổ tiền gửi NH)"

        existing_cash = db._execute_query('''
            SELECT id FROM public.transport_cash_book
            WHERE company_id = %s AND book_type = 'cash' AND source_type = 'bank_transfer' AND source_id = %s
        ''', (company_id, str(bid)), fetch=True, commit=False)

        if not existing_cash:
            db._execute_query('''
                INSERT INTO public.transport_cash_book
                (company_id, book_type, entry_date, voucher_date, voucher_no, voucher_type,
                 description, offset_account, amount, partner_name, partner_code, source_type, source_id, note)
                VALUES (%s, 'cash', %s, %s, %s, %s, %s, %s, %s, %s, %s, 'bank_transfer', %s, %s)
            ''', (
                company_id, target_date, target_date, target_vno, target_v_type,
                target_desc, target_offset, target_amount,
                b.get("partner_name") or "", b.get("partner_code") or "",
                str(bid), target_note
            ), commit=True)
            synced_count += 1
        else:
            db._execute_query('''
                UPDATE public.transport_cash_book
                SET entry_date = %s, voucher_date = %s, voucher_type = %s,
                    description = %s, offset_account = %s, amount = %s,
                    partner_name = %s, partner_code = %s, note = %s
                WHERE id = %s
            ''', (
                target_date, target_date, target_v_type,
                target_desc, target_offset, target_amount,
                b.get("partner_name") or "", b.get("partner_code") or "", target_note,
                existing_cash["id"]
            ), commit=True)

    if valid_bank_ids:
        db._execute_query('''
            DELETE FROM public.transport_cash_book
            WHERE company_id = %s AND book_type = 'cash' AND source_type = 'bank_transfer'
              AND (source_id IS NULL OR source_id NOT IN %s)
        ''', (company_id, tuple(valid_bank_ids)), commit=True)
    else:
        db._execute_query('''
            DELETE FROM public.transport_cash_book
            WHERE company_id = %s AND book_type = 'cash' AND source_type = 'bank_transfer'
        ''', (company_id,), commit=True)

    return synced_count


def get_cash_book_data(
    company_id: int | None = None,
    year: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Retrieve Cash Book data (Mau S04a-DNN) with running balance."""
    company_id = company_id or get_current_company_id()
    today = date.today()
    if date_from is None:
        y = year or today.year
        date_from = date(y, 1, 1)
    if date_to is None:
        y = year or today.year
        date_to = date(y, 12, 31)

    if not company_id or int(company_id) <= 0:
        return {
            "company": {"name": "", "address": "", "tax_code": "", "bank_account": "", "bank_name": ""},
            "year": year or today.year,
            "date_from": date_from,
            "date_to": date_to,
            "opening_balance": 0,
            "initial_opening": 0,
            "total_receipt": 0,
            "total_payment": 0,
            "closing_balance": 0,
            "entries": [],
        }

    # Tự động đồng bộ các khoản chuyển tiền mặt <-> ngân hàng (111 <-> 112)
    sync_cash_bank_transfers(company_id=company_id)

    # 1. Tính số dư đầu kỳ (Trước date_from)
    # Lấy các phát sinh trước date_from
    prev_sums = db._execute_query('''
        SELECT 
            COALESCE(SUM(CASE WHEN voucher_type = 'receipt' THEN amount ELSE 0 END), 0) as total_receipt,
            COALESCE(SUM(CASE WHEN voucher_type = 'payment' THEN amount ELSE 0 END), 0) as total_payment
        FROM public.transport_cash_book
        WHERE company_id = %s AND book_type = 'cash' AND entry_date < %s
    ''', (company_id, date_from), fetch=True, commit=False) or {}

    prev_receipt = Decimal(str(prev_sums.get("total_receipt") or 0))
    prev_payment = Decimal(str(prev_sums.get("total_payment") or 0))
    initial_opening = get_opening_balance_setting("cash")
    opening_balance = initial_opening + prev_receipt - prev_payment

    # 2. Lấy các phát sinh trong kỳ [date_from, date_to]
    rows_db = db._execute_query('''
        SELECT id, entry_date, voucher_date, voucher_no, voucher_type,
               description, offset_account, amount, partner_name, note,
               COALESCE(partner_code, '') as partner_code,
               COALESCE(source_type, '') as source_type
        FROM public.transport_cash_book
        WHERE company_id = %s AND book_type = 'cash' AND entry_date >= %s AND entry_date <= %s
        ORDER BY entry_date ASC, id ASC
    ''', (company_id, date_from, date_to), fetchall=True, commit=False) or []
    rows_db = _order_book_rows(rows_db)

    running_bal = opening_balance
    entries = []
    total_receipt = Decimal(0)
    total_payment = Decimal(0)

    for r in rows_db:
        amt = Decimal(int(round(Decimal(str(r["amount"] or 0)))))  # VND: làm tròn từng chứng từ để tổng sổ = tổng các dòng
        is_rec = (r["voucher_type"] == "receipt")
        receipt_amt = amt if is_rec else Decimal(0)
        payment_amt = Decimal(0) if is_rec else amt

        if is_rec:
            running_bal += amt
            total_receipt += amt
        else:
            running_bal -= amt
            total_payment += amt

        entries.append({
            "id": r["id"],
            "entry_date": r["entry_date"].strftime("%d/%m/%Y") if r["entry_date"] else "",
            "voucher_date": r["voucher_date"].strftime("%d/%m/%Y") if r["voucher_date"] else "",
            "receipt_no": r["voucher_no"] if is_rec else "",
            "payment_no": "" if is_rec else r["voucher_no"],
            "partner_code": r.get("partner_code") or "",
            "description": r["description"] or "",
            "offset_account": r["offset_account"] or ("131" if is_rec else "331"),
            "receipt_amount": int(round(receipt_amt)),
            "payment_amount": int(round(payment_amt)),
            "balance": int(round(running_bal)),
            "partner_name": r["partner_name"] or "",
            "note": r["note"] or "",
            "source_type": r.get("source_type") or "",
        })

    closing_balance = running_bal
    comp_info = get_company_info()

    return {
        "title": "SỔ QUỸ TIỀN MẶT",
        "form_code": "Mẫu số S04a-DNN",
        "circular": "Ban hành theo Thông tư số 133/2016/TT-BTC ngày 26/8/2016 của Bộ Tài chính",
        "currency": "VND",
        "company_name": comp_info["name"],
        "company_address": comp_info["address"],
        "tax_code": comp_info["tax_code"],
        "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"),
        "date_str": f"Từ ngày {date_from.strftime('%d/%m/%Y')} đến ngày {date_to.strftime('%d/%m/%Y')}",
        "opening_balance": int(round(opening_balance)),
        "initial_opening": int(round(initial_opening)),
        "total_receipt": int(round(total_receipt)),
        "total_payment": int(round(total_payment)),
        "closing_balance": int(round(closing_balance)),
        "entries": entries,
    }


_VOUCHER_SERIES_RE = re.compile(r"^(.*?)(\d+)\s*$")


def _order_book_rows(rows: list[dict]) -> list[dict]:
    """Giữ thứ tự nhập/nhập từ sao kê (id) làm thứ tự theo ngân hàng, chỉ sửa chỗ số chứng từ cùng loạt bị đảo.

    Trong cùng một ngày, các chứng từ cùng loại (thu/chi) và cùng loạt số (BC-, UNC-, PT, PC...) được xếp lại theo số
    tăng dần (BC-0014 trước BC-0015) nhưng đúng vào các vị trí mà loạt đó đang chiếm, nên thu/chi xen kẽ vẫn như cũ."""
    rows = list(rows)
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and rows[j].get("entry_date") == rows[i].get("entry_date"):
            j += 1
        series: dict[tuple, list[int]] = {}
        for k in range(i, j):
            m = _VOUCHER_SERIES_RE.match(str(rows[k].get("voucher_no") or "").strip())
            if m:
                series.setdefault((rows[k].get("voucher_type") or "", m.group(1)), []).append(k)
        for slots in series.values():
            if len(slots) < 2:
                continue
            ordered = sorted(
                (rows[k] for k in slots),
                key=lambda r: (int(_VOUCHER_SERIES_RE.match(str(r["voucher_no"]).strip()).group(2)), r["id"]),
            )
            for k, r in zip(slots, ordered):
                rows[k] = r
        i = j
    return rows


def get_bank_book_data(
    company_id: int | None = None,
    year: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    bank_account_no: str = "",
    bank_name: str = "",
) -> dict[str, Any]:
    """Retrieve Bank Deposit Book data (Mau S05a-DNN) with running balance."""
    company_id = company_id or get_current_company_id()
    today = date.today()
    if date_from is None:
        y = year or today.year
        date_from = date(y, 1, 1)
    if date_to is None:
        y = year or today.year
        date_to = date(y, 12, 31)

    if not company_id or int(company_id) <= 0:
        return {
            "company": {"name": "", "address": "", "tax_code": "", "bank_account": "", "bank_name": ""},
            "bank_account": "",
            "bank_name": "",
            "year": year or today.year,
            "date_from": date_from,
            "date_to": date_to,
            "opening_balance": 0,
            "initial_opening": 0,
            "total_deposit": 0,
            "total_withdraw": 0,
            "closing_balance": 0,
            "entries": [],
            "bank_accounts": [],
        }

    comp_info = get_company_info(company_id)
    actual_bank_account = bank_account_no or comp_info["bank_account"]
    actual_bank_name = bank_name or comp_info["bank_name"]

    # Tự động đồng bộ các khoản chuyển tiền mặt <-> ngân hàng (111 <-> 112)
    sync_cash_bank_transfers(company_id=company_id)

    prev_sums = db._execute_query('''
        SELECT 
            COALESCE(SUM(CASE WHEN voucher_type = 'receipt' THEN amount ELSE 0 END), 0) as total_receipt,
            COALESCE(SUM(CASE WHEN voucher_type = 'payment' THEN amount ELSE 0 END), 0) as total_payment
        FROM public.transport_cash_book
        WHERE company_id = %s AND book_type = 'bank' AND entry_date < %s
    ''', (company_id, date_from), fetch=True, commit=False) or {}

    prev_receipt = Decimal(str(prev_sums.get("total_receipt") or 0))
    prev_payment = Decimal(str(prev_sums.get("total_payment") or 0))
    initial_opening = get_opening_balance_setting("bank")
    opening_balance = initial_opening + prev_receipt - prev_payment

    rows_db = db._execute_query('''
        SELECT id, entry_date, voucher_date, voucher_no, voucher_type,
               description, offset_account, amount, bank_account_no, bank_name, partner_name, note,
               COALESCE(partner_code, '') as partner_code,
               COALESCE(source_type, '') as source_type
        FROM public.transport_cash_book
        WHERE company_id = %s AND book_type = 'bank' AND entry_date >= %s AND entry_date <= %s
        ORDER BY entry_date ASC, id ASC
    ''', (company_id, date_from, date_to), fetchall=True, commit=False) or []
    rows_db = _order_book_rows(rows_db)

    running_bal = opening_balance
    entries = []
    total_deposit = Decimal(0)
    total_withdraw = Decimal(0)

    for r in rows_db:
        amt = Decimal(int(round(Decimal(str(r["amount"] or 0)))))  # VND: làm tròn từng chứng từ để tổng sổ = tổng các dòng
        is_rec = (r["voucher_type"] == "receipt")
        deposit_amt = amt if is_rec else Decimal(0)
        withdraw_amt = Decimal(0) if is_rec else amt

        if is_rec:
            running_bal += amt
            total_deposit += amt
        else:
            running_bal -= amt
            total_withdraw += amt

        entries.append({
            "id": r["id"],
            "entry_date": r["entry_date"].strftime("%d/%m/%Y") if r["entry_date"] else "",
            "voucher_date": r["voucher_date"].strftime("%d/%m/%Y") if r["voucher_date"] else "",
            "voucher_no": r["voucher_no"] or "",
            "voucher_type": r["voucher_type"],
            "partner_code": r.get("partner_code") or "",
            "description": r["description"] or "",
            "offset_account": r["offset_account"] or ("131" if is_rec else "331"),
            "deposit_amount": int(round(deposit_amt)),
            "withdraw_amount": int(round(withdraw_amt)),
            "balance": int(round(running_bal)),
            "bank_account_no": r["bank_account_no"] or actual_bank_account,
            "bank_name": r["bank_name"] or actual_bank_name,
            "partner_name": r["partner_name"] or "",
            "note": r["note"] or "",
            "source_type": r.get("source_type") or "",
        })

    closing_balance = running_bal

    return {
        "title": "SỔ TIỀN GỬI NGÂN HÀNG",
        "form_code": "Mẫu số S05a-DNN",
        "circular": "Ban hành theo Thông tư số 133/2016/TT-BTC ngày 26/8/2016 của Bộ Tài chính",
        "currency": "VND",
        "bank_account_no": actual_bank_account,
        "bank_name": actual_bank_name,
        "company_name": comp_info["name"],
        "company_address": comp_info["address"],
        "tax_code": comp_info["tax_code"],
        "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"),
        "date_str": f"Từ ngày {date_from.strftime('%d/%m/%Y')} đến ngày {date_to.strftime('%d/%m/%Y')}",
        "opening_balance": int(round(opening_balance)),
        "initial_opening": int(round(initial_opening)),
        "total_deposit": int(round(total_deposit)),
        "total_withdraw": int(round(total_withdraw)),
        "closing_balance": int(round(closing_balance)),
        "entries": entries,
    }


def _find_partner_flags(partner_code: str = "", partner_name: str = "") -> dict | None:
    """Tìm đối tác trong danh mục theo MST, không có thì theo đúng tên; trả is_customer / is_vendor."""
    code = (partner_code or "").strip()
    name = (partner_name or "").strip()
    row = None
    if code:
        row = db._execute_query(
            "SELECT name, is_customer, is_vendor FROM public.invoice_companies WHERE tax_code = %s LIMIT 1",
            (code,), fetch=True, commit=False,
        )
    if not row and name:
        row = db._execute_query(
            "SELECT name, is_customer, is_vendor FROM public.invoice_companies "
            "WHERE lower(btrim(name)) = lower(btrim(%s)) LIMIT 1",
            (name,), fetch=True, commit=False,
        )
    return row or None


def check_partner_account(partner_code: str, partner_name: str, offset_account: str) -> str | None:
    """Báo sai khi đối tác là nhà cung cấp mà dùng TK 131, hoặc là khách hàng mà dùng TK 331. Đúng/không rõ thì trả None."""
    off = str(offset_account or "").strip()
    if not (off.startswith("131") or off.startswith("331")):
        return None
    row = _find_partner_flags(partner_code, partner_name)
    if not row:
        return None
    is_cust = bool(row.get("is_customer"))
    is_vend = bool(row.get("is_vendor"))
    name = row.get("name") or partner_name or partner_code
    if off.startswith("131") and is_vend and not is_cust:
        return f'Sai tài khoản đối ứng: "{name}" là nhà cung cấp, phải dùng TK 331, không dùng TK 131.'
    if off.startswith("331") and is_cust and not is_vend:
        return f'Sai tài khoản đối ứng: "{name}" là khách hàng, phải dùng TK 131, không dùng TK 331.'
    return None


def default_partner_account(partner_code: str, partner_name: str, is_payment: bool) -> str:
    """TK đối ứng mặc định khi để trống: đối tác là NCC thì 331, là khách hàng thì 131, không rõ thì theo chiều thu/chi."""
    row = _find_partner_flags(partner_code, partner_name)
    if row:
        is_cust = bool(row.get("is_customer"))
        is_vend = bool(row.get("is_vendor"))
        if is_vend and not is_cust:
            return "331"
        if is_cust and not is_vend:
            return "131"
    return "331" if is_payment else "131"


from frontend.modules.enterprise.sales_service import fold_text

_INVOICE_REF_PATTERN = re.compile(
    r'(?:\b|\A)(?:hoa\s+don|h\s*\.?\s*d)(?:\s*(?:so|no\.?))?[\s.:]*(\d+)((?:(?:\s*[,;+&]\s*|\s+va\s+)\d+)*)'
)


def extract_invoice_refs(text: str) -> list[str]:
    """Tìm các số hóa đơn trong chuỗi diễn giải.

    Nhận dạng 'HĐ', 'HD', 'H.Đ', 'hóa đơn', 'hoa don' (không phân biệt hoa/thường, dấu),
    tùy chọn 'số'/'so'/'no'/dấu/khoảng trắng, rồi một hoặc nhiều số cách nhau bởi , ; + & hoặc 'và'/'va'.
    Trả về danh sách chuỗi số đã bỏ số 0 đầu, giữ thứ tự xuất hiện, không trùng.
    """
    if not text:
        return []
    folded = fold_text(text)
    results: list[str] = []
    seen: set[str] = set()
    for m in _INVOICE_REF_PATTERN.finditer(folded):
        first_num = m.group(1)
        rest = m.group(2) or ""
        nums = [first_num]
        if rest:
            nums.extend(re.findall(r"\d+", rest))
        for n in nums:
            norm = n.lstrip("0") or "0"
            if norm not in seen:
                seen.add(norm)
                results.append(norm)
    return results


def allocate_payments(
    items: list[dict],
    opening: Decimal,
    payments: list[dict],
) -> tuple[Decimal, list[dict]]:
    """Phân bổ các khoản thanh toán vào hóa đơn theo số tham chiếu trong diễn giải trước,
    phần không ghi hoặc dư thì trừ FIFO vào dư đầu kỳ rồi các hóa đơn theo thứ tự ngày.
    """
    allocated_items = []
    for it in items:
        allocated_items.append({
            **it,
            "number": str(it.get("number") or "").lstrip("0") or "0",
            "owed": Decimal(str(it.get("owed") or 0)),
        })

    fifo_fund = Decimal(0)
    for p in payments:
        amt = Decimal(str(p.get("amount") or 0))
        if amt <= Decimal(0):
            continue
        desc = str(p.get("description") or "")
        refs = extract_invoice_refs(desc)
        if not refs:
            fifo_fund += amt
        else:
            rem_payment = amt
            for ref in refs:
                if rem_payment <= Decimal(0):
                    break
                for it in allocated_items:
                    if it["number"] == ref and it["owed"] > Decimal(0):
                        deduct = min(rem_payment, it["owed"])
                        it["owed"] -= deduct
                        rem_payment -= deduct
                        if rem_payment <= Decimal(0):
                            break
            if rem_payment > Decimal(0):
                fifo_fund += rem_payment

    rem_opening = max(Decimal(0), Decimal(str(opening or 0)))
    if rem_opening > Decimal(0) and fifo_fund > Decimal(0):
        deduct = min(fifo_fund, rem_opening)
        rem_opening -= deduct
        fifo_fund -= deduct

    if fifo_fund > Decimal(0):
        for it in allocated_items:
            if it["owed"] > Decimal(0):
                deduct = min(fifo_fund, it["owed"])
                it["owed"] -= deduct
                fifo_fund -= deduct
                if fifo_fund <= Decimal(0):
                    break

    return rem_opening, allocated_items


def get_open_receivable_invoices(
    company_id: int | None,
    partner_code: str = "",
    partner_name: str = "",
    as_of: date | str | None = None,
    exclude_entry_id: int | None = None,
) -> dict:
    as_of_date = date.today()
    if as_of:
        try:
            as_of_date = parse_flexible_date(as_of)
        except Exception:
            as_of_date = date.today()

    cid = None
    try:
        if company_id is not None:
            cid = int(company_id)
    except (ValueError, TypeError):
        cid = None

    p_code = (partner_code or "").strip()
    p_name = (partner_name or "").strip()

    if not cid or cid <= 0 or (not p_code and not p_name):
        return {"as_of": as_of_date.strftime("%Y-%m-%d"), "total": 0.0, "invoices": []}

    # b. Tìm đối tác trong public.invoice_companies bằng MST (tax_code) rồi bằng tên
    partner_row = None
    if p_code:
        partner_row = db._execute_query(
            "SELECT id, name, tax_code, opening_debt_dr_131, opening_debt_cr_131 "
            "FROM public.invoice_companies WHERE tax_code = %s LIMIT 1",
            (p_code,), fetch=True, commit=False,
        )
    if not partner_row and p_name:
        partner_row = db._execute_query(
            "SELECT id, name, tax_code, opening_debt_dr_131, opening_debt_cr_131 "
            "FROM public.invoice_companies "
            "WHERE lower(btrim(name)) = lower(btrim(%s)) LIMIT 1",
            (p_name,), fetch=True, commit=False,
        )

    partner_id = partner_row.get("id") if partner_row else None
    effective_mst = p_code or (str(partner_row.get("tax_code") or "").strip() if partner_row else "")
    effective_name = p_name or (str(partner_row.get("name") or "").strip() if partner_row else "")

    # c. Hóa đơn bán ra: payment_status = 'debt' và invoice_date <= as_of_date
    buyer_conds = []
    inv_params: list[Any] = [cid, as_of_date]
    if partner_id:
        buyer_conds.append("buyer_company_id = %s")
        inv_params.append(partner_id)
    if effective_mst:
        buyer_conds.append("(buyer_tax_code <> '' AND buyer_tax_code = %s)")
        inv_params.append(effective_mst)

    if not buyer_conds:
        invoices_db = []
    else:
        buyer_clause = " OR ".join(buyer_conds)
        query_inv = f"""
            SELECT invoice_guid::text, invoice_number, invoice_serial, invoice_date, gross, debt_amount
            FROM public.bkav_invoices
            WHERE seller_company_id = %s
              AND payment_status = 'debt'
              AND invoice_date <= %s
              AND ({buyer_clause})
            ORDER BY invoice_date ASC, invoice_number ASC
        """
        invoices_db = db._execute_query(query_inv, tuple(inv_params), fetchall=True, commit=False) or []

    # d. Khoản đã thu trước thời điểm
    exclude_id = None
    try:
        if exclude_entry_id is not None:
            exclude_id = int(exclude_entry_id)
    except (ValueError, TypeError):
        exclude_id = None

    cash_params: list[Any] = [cid]
    partner_cash_conds = []
    if effective_mst:
        partner_cash_conds.append("partner_code = %s")
        cash_params.append(effective_mst)
    if effective_name:
        partner_cash_conds.append("lower(btrim(partner_name)) = lower(btrim(%s))")
        cash_params.append(effective_name)

    if not partner_cash_conds:
        Decimal(0)
    else:
        cash_conds_sql = f"({' OR '.join(partner_cash_conds)})"
        if exclude_id and exclude_id > 0:
            date_cond = "AND (entry_date < %s OR (entry_date = %s AND id < %s)) AND id <> %s"
            cash_date_params = [as_of_date, as_of_date, exclude_id, exclude_id]
        else:
            date_cond = "AND entry_date <= %s"
            cash_date_params = [as_of_date]

        query_cash = f"""
            SELECT amount, description, entry_date, id
            FROM public.transport_cash_book
            WHERE company_id = %s
              AND voucher_type = 'receipt'
              AND offset_account LIKE '131%%'
              AND COALESCE(source_type, '') <> 'invoice'
              AND {cash_conds_sql}
              {date_cond}
            ORDER BY entry_date ASC, id ASC
        """
        receipt_rows = db._execute_query(
            query_cash,
            tuple(cash_params + cash_date_params),
            fetchall=True,
            commit=False,
        ) or []

    # e. Dư đầu kỳ và phân bổ theo số HĐ / FIFO
    open_dr = Decimal(str(partner_row.get("opening_debt_dr_131") or 0)) if partner_row else Decimal(0)
    open_cr = Decimal(str(partner_row.get("opening_debt_cr_131") or 0)) if partner_row else Decimal(0)
    opening_net = open_dr - open_cr

    inv_items = []
    for inv in invoices_db:
        debt = Decimal(str(inv.get("debt_amount") or 0))
        inv_num = inv.get("invoice_number")
        num_str = str(inv_num) if inv_num is not None else ""
        inv_items.append({
            "inv": inv,
            "raw_number": num_str,
            "number": num_str.lstrip("0") or "0",
            "owed": debt,
        })

    rem_opening, allocated_items = allocate_payments(inv_items, opening_net, receipt_rows)
    result_invoices = []

    if rem_opening > Decimal(0):
        result_invoices.append({
            "guid": "",
            "number": "",
            "serial": "",
            "date": "",
            "date_iso": "",
            "gross": float(opening_net),
            "owed": float(rem_opening),
            "opening": True,
        })

    for item in allocated_items:
        owed = item["owed"]
        if owed > Decimal(0):
            inv = item["inv"]
            gross = Decimal(str(inv.get("gross") or 0))
            inv_date = inv.get("invoice_date")
            d_str = ""
            d_iso = ""
            if isinstance(inv_date, (datetime, date)):
                d_str = inv_date.strftime("%d/%m/%Y")
                d_iso = inv_date.strftime("%Y-%m-%d")
            elif inv_date:
                d_str = str(inv_date)
                d_iso = str(inv_date)

            result_invoices.append({
                "guid": str(inv.get("invoice_guid") or ""),
                "number": item.get("raw_number", item["number"]),
                "serial": str(inv.get("invoice_serial") or ""),
                "date": d_str,
                "date_iso": d_iso,
                "gross": float(gross),
                "owed": float(owed),
            })

    total_owed = sum((Decimal(str(item["owed"])) for item in result_invoices), Decimal(0))
    return {
        "as_of": as_of_date.strftime("%Y-%m-%d"),
        "total": float(total_owed),
        "invoices": result_invoices,
    }


def partner_payment_descriptions(company_id: int | None, companies) -> dict:
    """Nội dung chuyển khoản có sẵn theo MST đối tác: ví trả trước của đơn vị thu phí, lấy từ tài khoản dịch vụ."""
    if not company_id:
        return {}
    accounts = db._execute_query(
        """SELECT service_name, account_username FROM public.transport_service_accounts
            WHERE company_id = %s AND category = 'toll' AND status = 'active' AND btrim(account_username) <> ''
            ORDER BY id""",
        (company_id,), fetchall=True, commit=False,
    ) or []
    result: dict[str, str] = {}
    for account in accounts:
        brand = str(account["service_name"]).split(" - ")[0].strip()
        if not brand:
            continue
        for company in companies:
            tax_code = str(company.get("tax_code") or "").strip()
            if tax_code and brand.lower() in str(company.get("name") or "").lower():
                result.setdefault(tax_code, f"Nạp ví {brand} {account['account_username'].strip()}")
    return result


def get_open_payable_invoices(
    company_id: int | None,
    partner_code: str = "",
    partner_name: str = "",
    as_of: date | str | None = None,
    exclude_entry_id: int | None = None,
) -> dict:
    from frontend.modules.invoices.service import invoice_payment_description

    as_of_date = date.today()
    if as_of:
        try:
            as_of_date = parse_flexible_date(as_of)
        except Exception:
            as_of_date = date.today()

    cid = None
    try:
        if company_id is not None:
            cid = int(company_id)
    except (ValueError, TypeError):
        cid = None

    p_code = (partner_code or "").strip()
    p_name = (partner_name or "").strip()

    if not cid or cid <= 0 or (not p_code and not p_name):
        return {"as_of": as_of_date.strftime("%Y-%m-%d"), "total": 0.0, "invoices": []}

    # Tìm NCC trong public.invoice_companies bằng MST (tax_code) rồi bằng tên
    partner_row = None
    if p_code:
        partner_row = db._execute_query(
            "SELECT id, name, tax_code, opening_debt_dr_331, opening_debt_cr_331 "
            "FROM public.invoice_companies WHERE tax_code = %s LIMIT 1",
            (p_code,), fetch=True, commit=False,
        )
    if not partner_row and p_name:
        partner_row = db._execute_query(
            "SELECT id, name, tax_code, opening_debt_dr_331, opening_debt_cr_331 "
            "FROM public.invoice_companies "
            "WHERE lower(btrim(name)) = lower(btrim(%s)) LIMIT 1",
            (p_name,), fetch=True, commit=False,
        )

    partner_id = partner_row.get("id") if partner_row else None
    effective_mst = p_code or (str(partner_row.get("tax_code") or "").strip() if partner_row else "")
    effective_name = p_name or (str(partner_row.get("name") or "").strip() if partner_row else "")

    # Hóa đơn mua vào: COALESCE(NULLIF(TRIM(tk_co_pay), ''), '331') LIKE '331%' và invoice_date <= as_of_date
    seller_conds = []
    inv_params: list[Any] = [cid, as_of_date]
    if partner_id:
        seller_conds.append("seller_company_id = %s")
        inv_params.append(partner_id)
    if effective_mst:
        seller_conds.append("(seller_tax_code <> '' AND seller_tax_code = %s)")
        inv_params.append(effective_mst)

    if not seller_conds:
        invoices_db = []
    else:
        seller_clause = " OR ".join(seller_conds)
        query_inv = f"""
            SELECT id, invoice_number, serial, invoice_date, total, items
            FROM public.transport_input_invoices
            WHERE buyer_company_id = %s
              AND COALESCE(NULLIF(TRIM(tk_co_pay), ''), '331') LIKE '331%%'
              AND invoice_date <= %s
              AND ({seller_clause})
            ORDER BY invoice_date ASC, id ASC
        """
        invoices_db = db._execute_query(query_inv, tuple(inv_params), fetchall=True, commit=False) or []

    # Khoản đã trả trước thời điểm
    exclude_id = None
    try:
        if exclude_entry_id is not None:
            exclude_id = int(exclude_entry_id)
    except (ValueError, TypeError):
        exclude_id = None

    cash_params: list[Any] = [cid]
    partner_cash_conds = []
    if effective_mst:
        partner_cash_conds.append("partner_code = %s")
        cash_params.append(effective_mst)
    if effective_name:
        partner_cash_conds.append("lower(btrim(partner_name)) = lower(btrim(%s))")
        cash_params.append(effective_name)

    payment_rows = []
    if not partner_cash_conds:
        Decimal(0)
    else:
        cash_conds_sql = f"({' OR '.join(partner_cash_conds)})"
        if exclude_id and exclude_id > 0:
            date_cond = "AND (entry_date < %s OR (entry_date = %s AND id < %s)) AND id <> %s"
            cash_date_params = [as_of_date, as_of_date, exclude_id, exclude_id]
        else:
            date_cond = "AND entry_date <= %s"
            cash_date_params = [as_of_date]

        query_cash = f"""
            SELECT amount, description, entry_date, id
            FROM public.transport_cash_book
            WHERE company_id = %s
              AND voucher_type = 'payment'
              AND offset_account LIKE '331%%'
              AND COALESCE(source_type, '') <> 'input_invoice'
              AND {cash_conds_sql}
              {date_cond}
            ORDER BY entry_date ASC, id ASC
        """
        payment_rows = db._execute_query(
            query_cash,
            tuple(cash_params + cash_date_params),
            fetchall=True,
            commit=False,
        ) or []

    # Dư đầu kỳ NCC: opening_debt_cr_331 - opening_debt_dr_331
    open_dr = Decimal(str(partner_row.get("opening_debt_dr_331") or 0)) if partner_row else Decimal(0)
    open_cr = Decimal(str(partner_row.get("opening_debt_cr_331") or 0)) if partner_row else Decimal(0)
    opening_net = open_cr - open_dr

    inv_items = []
    for inv in invoices_db:
        gross = Decimal(str(inv.get("total") or 0))
        inv_num = inv.get("invoice_number")
        num_str = str(inv_num) if inv_num is not None else ""
        inv_items.append({
            "inv": inv,
            "raw_number": num_str,
            "number": num_str.lstrip("0") or "0",
            "owed": gross,
        })

    if partner_id:
        from frontend.modules.transport.debt_offset_service import offsets_for_partner
        payment_rows = list(payment_rows)
        for o in offsets_for_partner(cid, partner_id, date_to=as_of_date):
            if o["date"] <= as_of_date:
                if o["side"] == "dr":
                    payment_rows.append({"amount": o["amount"], "description": "", "entry_date": o["date"], "id": o["id"]})
                elif o["side"] == "cr":
                    opening_net += o["amount"]
        from frontend.modules.transport.other_vouchers_service import vouchers_for_partner
        for ov in vouchers_for_partner(cid, partner_id, "331", date_to=as_of_date):
            if ov["date"] <= as_of_date:
                if ov["side"] == "dr":
                    payment_rows.append({"amount": ov["amount"], "description": ov.get("description") or "", "entry_date": ov["date"], "id": ov["id"]})
                elif ov["side"] == "cr":
                    opening_net += ov["amount"]

    rem_opening, allocated_items = allocate_payments(inv_items, opening_net, payment_rows)
    result_invoices = []

    if rem_opening > Decimal(0):
        result_invoices.append({
            "guid": "",
            "number": "",
            "serial": "",
            "date": "",
            "date_iso": "",
            "gross": float(opening_net),
            "owed": float(rem_opening),
            "opening": True,
        })

    for item in allocated_items:
        owed = item["owed"]
        if owed > Decimal(0):
            inv = item["inv"]
            gross = Decimal(str(inv.get("total") or 0))
            inv_date = inv.get("invoice_date")
            d_str = ""
            d_iso = ""
            if isinstance(inv_date, (datetime, date)):
                d_str = inv_date.strftime("%d/%m/%Y")
                d_iso = inv_date.strftime("%Y-%m-%d")
            elif inv_date:
                d_str = str(inv_date)
                d_iso = str(inv_date)

            result_invoices.append({
                "guid": str(inv.get("id") or ""),
                "number": item.get("raw_number", item["number"]),
                "serial": str(inv.get("serial") or ""),
                "date": d_str,
                "date_iso": d_iso,
                "gross": float(gross),
                "owed": float(owed),
                "payment_desc": invoice_payment_description(
                    inv.get("items"), inv_date, item.get("raw_number", item["number"])) or "",
            })

    total_owed = sum((Decimal(str(item["owed"])) for item in result_invoices), Decimal(0))
    return {
        "as_of": as_of_date.strftime("%Y-%m-%d"),
        "total": float(total_owed),
        "invoices": result_invoices,
    }


def next_counted_voucher_no(company_id: int, book_type: str, voucher_type: str) -> str:
    """Next voucher number from the count of that company's vouchers of the same book and type."""
    prefix = "PT" if (book_type == "cash" and voucher_type == "receipt") else (
        "PC" if (book_type == "cash" and voucher_type == "payment") else (
            "BC-" if voucher_type == "receipt" else "UNC-"
        )
    )
    count_row = db._execute_query('''
        SELECT COUNT(*) as cnt FROM public.transport_cash_book
        WHERE company_id = %s AND book_type = %s AND voucher_type = %s
    ''', (company_id, book_type, voucher_type), fetch=True, commit=False) or {}
    num = (count_row.get("cnt") or 0) + 1
    return f"{prefix}{num:04d}" if "-" in prefix else f"{prefix}{num:03d}"


def record_cash_or_bank_entry(
    company_id: int | None = None,
    book_type: str = "cash",   # 'cash' hoặc 'bank'
    voucher_type: str = "receipt", # 'receipt' (Thu / Báo Có) hoặc 'payment' (Chi / Báo Nợ)
    amount: Decimal | float | int = 0,
    voucher_date: date | str | None = None,
    voucher_no: str = "",
    description: str = "",
    offset_account: str = "131",
    partner_name: str = "",
    partner_code: str = "",
    bank_account_no: str = "",
    bank_name: str = "",
    source_type: str = "manual",
    source_id: str | int | None = None,
    note: str = "",
) -> tuple[bool, str, int]:
    """Record a cash/bank transaction when debt is collected or paid."""
    company_id = company_id or get_current_company_id()
    if not company_id:
        return False, "Chưa chọn công ty làm việc.", 0
    try:
        val = parse_vnd(amount)
    except ValueError:
        return False, "Số tiền không hợp lệ.", 0

    if val <= 0:
        return False, "Số tiền phải lớn hơn 0.", 0

    vt = (voucher_type or "").strip().lower()
    if vt not in ("receipt", "payment", "thu", "chi", "default"):
        return False, "Loại chứng từ không hợp lệ.", 0
    voucher_type = "receipt" if vt in ("receipt", "thu", "default") else "payment"

    if source_type == "manual":
        partner_err = check_partner_account(partner_code, partner_name, offset_account)
        if partner_err:
            return False, partner_err, 0

    try:
        v_date = parse_flexible_date(voucher_date)
    except ValueError as exc:
        return False, str(exc), 0

    b_type = "bank" if "ngân hàng" in book_type.lower() or "bank" in book_type.lower() or "chuyển khoản" in book_type.lower() or "ck" in book_type.lower() else "cash"
    if b_type == "bank" and (not bank_account_no or not bank_name):
        comp_info = get_company_info(company_id)
        bank_account_no = bank_account_no or comp_info["bank_account"]
        bank_name = bank_name or comp_info["bank_name"]

    if not voucher_no:
        # Tự sinh số chứng từ
        voucher_no = next_counted_voucher_no(company_id, b_type, voucher_type)

    row_id = db._execute_query('''
        INSERT INTO public.transport_cash_book
        (company_id, book_type, entry_date, voucher_date, voucher_no, voucher_type,
         description, offset_account, amount, bank_account_no, bank_name, partner_name,
         partner_code, source_type, source_id, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    ''', (
        company_id, b_type, v_date, v_date, voucher_no, voucher_type,
        description.strip() or ("Thu tiền" if voucher_type == "receipt" else "Chi tiền"),
        offset_account, val,
        bank_account_no if b_type == "bank" else None,
        bank_name if b_type == "bank" else None,
        partner_name.strip(),
        partner_code.strip() if partner_code else None,
        source_type, str(source_id) if source_id else None,
        note.strip()
    ), fetch=True, commit=True)

    ins_id = row_id["id"] if row_id else 0
    # Đồng bộ liên kết nếu đây là giao dịch chuyển tiền giữa 111 và 112
    sync_cash_bank_transfers(company_id=company_id)

    # TỰ ĐỘNG LIÊN THÔNG VỚI BẢNG KHẤU HAO TSCĐ (TK 211) & BẢNG PHÂN BỔ CCDC (TK 242)
    clean_offset = str(offset_account or "").strip()
    desc_lower = (description or "").lower()
    book_name = "Sổ quỹ tiền mặt (TK 111)" if b_type == "cash" else "Sổ tiền gửi ngân hàng (TK 112)"

    if voucher_type == "payment":
        # 1. Liên thông ghi nhận Tài sản cố định (Nợ 211)
        if clean_offset.startswith("211") or ("mua xe" in desc_lower or "mua ô tô" in desc_lower):
            exist_asset = db._execute_query('''
                    SELECT id FROM public.enterprise_assets
                    WHERE company_id = %s AND (serial_number = %s OR note LIKE %s)
                ''', (company_id, f"CT-{ins_id}", f"%{voucher_no}%"), fetch=True, commit=False)
            if not exist_asset:
                asset_cat = "Phương tiện vận tải" if ("xe" in desc_lower or "ô tô" in desc_lower or "cẩu" in desc_lower) else "Máy móc thiết bị"
                depr_yrs = 6 if ("xe" in desc_lower or "ô tô" in desc_lower) else 5
                db._execute_query('''
                        INSERT INTO public.enterprise_assets
                        (company_id, asset_code, name, category, serial_number, acquired_date,
                         purchase_cost, assigned_to, status, note, depreciation_years)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'in_use', %s, %s)
                    ''', (
                    company_id, f"TSCD-{ins_id}", description.strip(), asset_cat,
                    f"CT-{ins_id}", v_date, val, partner_name.strip() or "Bộ phận vận tải",
                    f"Tự động ghi nhận từ chứng từ {voucher_no} ({book_name})", depr_yrs
                ), commit=True)
                logger.info(f"Auto-created enterprise_asset for company_id={company_id}: {description} ({val})")

        # 2. Liên thông ghi nhận Công cụ dụng cụ / Chi phí trả trước (Nợ 242)
        elif clean_offset.startswith("242") or ("quả cân" in desc_lower or "mua lốp" in desc_lower):
            from frontend.modules.transport.prepaid_expense_service import add_prepaid_expense
            add_prepaid_expense(
                company_id=company_id,
                item_code=f"CCDC-{ins_id}",
                name=description.strip(),
                unit="Cái" if ("quả cân" in desc_lower or "lốp" in desc_lower) else "Bộ",
                quantity=1,
                original_cost=val,
                start_date=v_date,
                total_months=24 if "quả cân" in desc_lower else 12,
                note=f"Tự động ghi nhận từ chứng từ {voucher_no} ({book_name})",
            )
            logger.info(f"Auto-created prepaid_expense for company_id={company_id}: {description} ({val})")

    return True, f"Đã ghi sổ vào {book_name} thành công (Số CT: {voucher_no}).", ins_id


def record_cash_transfer(
    company_id: int | None,
    direction: str,
    voucher_date: Any,
    amount: Any,
    voucher_no: str = "",
    description: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Hạch toán bút toán nộp/rút tiền mặt giữa TK 112 và TK 111."""
    dir_clean = (direction or "").strip().lower()
    if dir_clean not in ("deposit", "withdraw"):
        return {
            "ok": False,
            "message": "Loại giao dịch không hợp lệ (chỉ nhận 'deposit' hoặc 'withdraw').",
            "voucher_no": "",
            "cash_voucher_no": "",
            "id": 0,
        }

    company_id = company_id or get_current_company_id()
    if not company_id:
        return {
            "ok": False,
            "message": "Chưa chọn công ty làm việc.",
            "voucher_no": "",
            "cash_voucher_no": "",
            "id": 0,
        }

    voucher_type = "receipt" if dir_clean == "deposit" else "payment"
    action_label = "Nộp" if dir_clean == "deposit" else "Rút"
    default_desc = (
        "Nộp tiền TK"
        if dir_clean == "deposit"
        else "Rút tiền nhập quỹ"
    )
    final_desc = (description or "").strip() or default_desc

    comp_info = get_company_info(int(company_id)) if company_id else {}
    bank_account_no = comp_info.get("bank_account") or ""
    bank_name = comp_info.get("bank_name") or ""
    partner_name = bank_name

    ok, msg, new_id = record_cash_or_bank_entry(
        company_id=company_id,
        book_type="bank",
        voucher_type=voucher_type,
        amount=amount,
        voucher_date=voucher_date,
        voucher_no=voucher_no,
        description=final_desc,
        offset_account="1111",
        partner_name=partner_name,
        partner_code="",
        bank_account_no=bank_account_no,
        bank_name=bank_name,
        source_type="manual",
        source_id=None,
        note=note,
    )
    if not ok:
        return {
            "ok": False,
            "message": msg,
            "voucher_no": "",
            "cash_voucher_no": "",
            "id": 0,
        }

    bank_row = db._execute_query(
        "SELECT voucher_no, amount FROM public.transport_cash_book WHERE id = %s",
        (new_id,),
        fetch=True,
        commit=False,
    )
    bank_voucher_no = bank_row.get("voucher_no") if bank_row else (voucher_no or "")
    actual_amount = bank_row.get("amount") if bank_row else amount

    cash_row = db._execute_query(
        "SELECT voucher_no FROM public.transport_cash_book "
        "WHERE company_id = %s AND book_type = 'cash' AND source_type = 'bank_transfer' AND source_id = %s",
        (company_id, str(new_id)),
        fetch=True,
        commit=False,
    )
    cash_voucher_no = (cash_row.get("voucher_no") or "").strip() if cash_row else ""

    amt_val = int(round(float(actual_amount or 0)))
    amt_str = f"{amt_val:,}".replace(",", ".")
    message = (
        f"Đã ghi sổ ngân hàng ({action_label} tiền mặt, Số CT: {bank_voucher_no}, {amt_str} đ) "
        f"và lập phiếu quỹ tương ứng (Số CT quỹ: {cash_voucher_no})."
    )

    return {
        "ok": True,
        "message": message,
        "voucher_no": bank_voucher_no,
        "cash_voucher_no": cash_voucher_no,
        "id": new_id,
    }


def get_cash_or_bank_entry(entry_id: int, company_id: int | None = None) -> dict[str, Any] | None:
    """Fetch single entry by ID and company_id for viewing/editing."""
    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return None
    row = db._execute_query('''
        SELECT id, company_id, book_type, entry_date, voucher_date, voucher_no, voucher_type,
               description, offset_account, amount, bank_account_no, bank_name, partner_name,
               COALESCE(partner_code, '') as partner_code,
               source_type, source_id, note
        FROM public.transport_cash_book
        WHERE id = %s AND company_id = %s
    ''', (entry_id, company_id), fetch=True, commit=False)
    if not row:
        return None
    return {
        "id": row["id"],
        "company_id": row["company_id"],
        "book_type": row["book_type"],
        "voucher_type": row["voucher_type"],
        "voucher_no": row["voucher_no"] or "",
        "voucher_date": row["voucher_date"].strftime("%Y-%m-%d") if row["voucher_date"] else "",
        "entry_date": row["entry_date"].strftime("%Y-%m-%d") if row["entry_date"] else "",
        "amount": int(round(Decimal(str(row["amount"] or 0)))),
        "description": row["description"] or "",
        "offset_account": row["offset_account"] or "",
        "partner_name": row["partner_name"] or "",
        "partner_code": row.get("partner_code") or "",
        "bank_account_no": row["bank_account_no"] or "",
        "bank_name": row["bank_name"] or "",
        "note": row["note"] or "",
        "source_type": row["source_type"] or "",
    }


def partner_address_by_tax_code(tax_code: str) -> str:
    """Lookup company address by tax code for voucher printing."""
    if not tax_code:
        return ""
    row = db._execute_query(
        "SELECT address FROM public.invoice_companies WHERE tax_code = %s LIMIT 1",
        (tax_code.strip(),),
        fetch=True,
        commit=False,
    )
    return (row.get("address") or "") if row else ""


def update_cash_or_bank_entry(
    entry_id: int,
    company_id: int | None = None,
    voucher_type: str | None = None,
    amount: Decimal | float | int | str | None = None,
    voucher_date: date | str | None = None,
    voucher_no: str | None = None,
    description: str | None = None,
    offset_account: str | None = None,
    partner_name: str | None = None,
    partner_code: str | None = None,
    bank_account_no: str | None = None,
    bank_name: str | None = None,
    note: str | None = None,
) -> tuple[bool, str]:
    """Update an existing cash or bank voucher entry."""
    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return False, "Chưa chọn công ty làm việc."
    existing = db._execute_query('''
        SELECT id, book_type, voucher_type, amount, voucher_date, voucher_no, description, offset_account, partner_name, note,
               COALESCE(partner_code, '') AS partner_code, source_type, source_id
        FROM public.transport_cash_book
        WHERE id = %s AND company_id = %s
    ''', (entry_id, company_id), fetch=True, commit=False)
    if not existing:
        return False, "Không tìm thấy chứng từ cần cập nhật."

    if (existing.get("source_type") or "manual") in ("manual", "excel_import"):
        eff_off = offset_account.strip() if offset_account is not None else (existing.get("offset_account") or "").strip()
        eff_name = partner_name.strip() if partner_name is not None else (existing.get("partner_name") or "").strip()
        eff_code = partner_code.strip() if partner_code is not None else (existing.get("partner_code") or "").strip()
        changed = (
            eff_off != (existing.get("offset_account") or "").strip()
            or eff_name != (existing.get("partner_name") or "").strip()
            or eff_code != (existing.get("partner_code") or "").strip()
        )
        if changed:
            partner_err = check_partner_account(eff_code, eff_name, eff_off)
            if partner_err:
                return False, partner_err

    updates = []
    params = []
    val = None
    v_date = None

    if voucher_type is not None and str(voucher_type).strip() != "":
        vt = str(voucher_type).strip().lower()
        if vt not in ("receipt", "payment", "thu", "chi", "default"):
            return False, "Loại chứng từ không hợp lệ."
        norm_vt = "receipt" if vt in ("receipt", "thu", "default") else "payment"
        updates.append("voucher_type = %s")
        params.append(norm_vt)

    if amount is not None and str(amount).strip() != "":
        try:
            val = parse_vnd(amount)
            if val <= 0:
                return False, "Số tiền phải lớn hơn 0."
            updates.append("amount = %s")
            params.append(val)
        except ValueError:
            return False, "Số tiền không hợp lệ."

    if voucher_date is not None and str(voucher_date).strip() != "":
        try:
            v_date = parse_flexible_date(voucher_date)
        except ValueError as exc:
            return False, str(exc)
        updates.append("voucher_date = %s")
        params.append(v_date)
        updates.append("entry_date = %s")
        params.append(v_date)

    if voucher_no is not None:
        updates.append("voucher_no = %s")
        params.append(voucher_no.strip())

    if description is not None:
        updates.append("description = %s")
        params.append(description.strip())

    if offset_account is not None:
        updates.append("offset_account = %s")
        params.append(offset_account.strip())

    if partner_name is not None:
        updates.append("partner_name = %s")
        params.append(partner_name.strip())

    if partner_code is not None:
        updates.append("partner_code = %s")
        params.append(partner_code.strip())

    if bank_account_no is not None:
        updates.append("bank_account_no = %s")
        params.append(bank_account_no.strip())

    if bank_name is not None:
        updates.append("bank_name = %s")
        params.append(bank_name.strip())

    if note is not None:
        updates.append("note = %s")
        params.append(note.strip())

    if not updates:
        return True, "Không có thay đổi nào."

    # Nếu người dùng sửa trực tiếp trên bản ghi counterpart liên kết (cash_transfer hoặc bank_transfer)
    if existing.get("source_type") in ("cash_transfer", "bank_transfer") and existing.get("source_id"):
        parent_id = existing["source_id"]
        parent_updates = []
        parent_params = []
        if val is not None:
            parent_updates.append("amount = %s")
            parent_params.append(val)
        if v_date is not None:
            parent_updates.append("voucher_date = %s")
            parent_updates.append("entry_date = %s")
            parent_params.extend([v_date, v_date])
        if description is not None:
            parent_updates.append("description = %s")
            parent_params.append(description.strip())
        if parent_updates:
            parent_params.extend([parent_id, company_id])
            db._execute_query(f'''
                UPDATE public.transport_cash_book
                SET {", ".join(parent_updates)}
                WHERE id = %s AND company_id = %s
            ''', tuple(parent_params), commit=True)

    params.extend([entry_id, company_id])
    db._execute_query(f'''
        UPDATE public.transport_cash_book
        SET {", ".join(updates)}
        WHERE id = %s AND company_id = %s
    ''', tuple(params), commit=True)

    # Tự động đồng bộ liên kết 111 <-> 112
    sync_cash_bank_transfers(company_id=company_id)
    return True, "Cập nhật chứng từ thành công."


def delete_cash_or_bank_entry(entry_id: int, company_id: int | None = None) -> tuple[bool, str]:
    """Delete an existing cash or bank voucher entry."""
    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return False, "Chưa chọn công ty làm việc."
    existing = db._execute_query('''
        SELECT id, voucher_no, source_type, source_id FROM public.transport_cash_book
        WHERE id = %s AND company_id = %s
    ''', (entry_id, company_id), fetch=True, commit=False)
    if not existing:
        return False, "Không tìm thấy chứng từ cần xóa."

    v_no = existing.get("voucher_no") or f"ID {entry_id}"

    # 1. Xóa bản ghi counterpart nếu đây là nguồn transfer
    db._execute_query('''
        DELETE FROM public.transport_cash_book
        WHERE company_id = %s AND source_id = %s AND source_type IN ('cash_transfer', 'bank_transfer')
    ''', (company_id, str(entry_id)), commit=True)

    # 2. Xóa bản ghi parent nếu người dùng bấm xóa trên bản ghi counterpart
    if existing.get("source_type") in ("cash_transfer", "bank_transfer") and existing.get("source_id"):
        db._execute_query('''
            DELETE FROM public.transport_cash_book
            WHERE company_id = %s AND id = %s
        ''', (company_id, existing["source_id"]), commit=True)

    # 3. Xóa chính bản ghi này
    db._execute_query('''
        DELETE FROM public.transport_cash_book
        WHERE id = %s AND company_id = %s
    ''', (entry_id, company_id), commit=True)

    sync_cash_bank_transfers(company_id=company_id)
    return True, f"Đã xóa chứng từ {v_no} thành công."


def export_cash_book_excel(
    company_id: int | None = None,
    year: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> bytes:
    """Export Cash Book to Excel matching Mau S04a-DNN."""
    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return b""
    data = get_cash_book_data(company_id=company_id, year=year, date_from=date_from, date_to=date_to)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "S04a-DNN_So_Quy_Tien_Mat"
    ws.views.sheetView[0].showGridLines = True

    font_title = Font(name="Times New Roman", size=14, bold=True, color="1E3A8A")
    font_bold = Font(name="Times New Roman", size=10, bold=True)
    font_regular = Font(name="Times New Roman", size=10)
    font_italic = Font(name="Times New Roman", size=9, italic=True)

    border_thin = Side(border_style="thin", color="000000")
    border_double = Side(border_style="double", color="000000")
    box_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

    fill_header = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    fill_total = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")

    # Header info
    ws["A1"] = f"Đơn vị: {data['company_name']}"
    ws["A1"].font = font_bold
    ws["A2"] = f"Địa chỉ: {data['company_address']}"
    ws["A2"].font = font_regular
    ws["A3"] = f"Mã số thuế: {data['tax_code']}"
    ws["A3"].font = font_regular

    ws["I1"] = data["form_code"]
    ws["I1"].font = font_bold
    ws["I1"].alignment = Alignment(horizontal="right")
    ws["I2"] = data["circular"]
    ws["I2"].font = font_italic
    ws["I2"].alignment = Alignment(horizontal="right")

    ws.merge_cells("A5:K5")
    ws["A5"] = data["title"]
    ws["A5"].font = font_title
    ws["A5"].alignment = Alignment(horizontal="center")

    ws.merge_cells("A6:K6")
    ws["A6"] = data["date_str"]
    ws["A6"].font = font_italic
    ws["A6"].alignment = Alignment(horizontal="center")

    ws.merge_cells("A7:K7")
    ws["A7"] = f"Loại quỹ: {data['currency']}"
    ws["A7"].font = font_regular
    ws["A7"].alignment = Alignment(horizontal="center")

    # Headers table (Rows 9-11)
    ws.merge_cells("A9:A10")
    ws["A9"] = "Ngày, tháng\nghi sổ"
    ws.merge_cells("B9:B10")
    ws["B9"] = "Ngày, tháng\nchứng từ"
    ws.merge_cells("C9:D9")
    ws["C9"] = "Số hiệu chứng từ"
    ws["C10"] = "Thu"
    ws["D10"] = "Chi"
    ws.merge_cells("E9:E10")
    ws["E9"] = "Mã khách"
    ws.merge_cells("F9:F10")
    ws["F9"] = "Tên khách"
    ws.merge_cells("G9:G10")
    ws["G9"] = "Diễn giải"
    ws.merge_cells("H9:H10")
    ws["H9"] = "TK đối ứng"
    ws.merge_cells("I9:K9")
    ws["I9"] = "Số tiền"
    ws["I10"] = "Thu"
    ws["J10"] = "Chi"
    ws["K10"] = "Tồn"

    letters = ["A", "B", "C", "D", "E", "F", "G", "H", "1", "2", "3"]
    for c_idx, ltr in enumerate(letters, 1):
        cell = ws.cell(row=11, column=c_idx, value=ltr)
        cell.font = font_bold
        cell.alignment = Alignment(horizontal="center")
        cell.fill = fill_header
        cell.border = box_border

    for r in range(9, 11):
        for c in range(1, 12):
            cell = ws.cell(row=r, column=c)
            cell.font = font_bold
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.fill = fill_header
            cell.border = box_border

    # Dòng Số dư đầu kỳ (Row 12)
    for c in range(1, 12):
        ws.cell(row=12, column=c).border = box_border
    ws["G12"] = "Số dư đầu kỳ"
    ws["G12"].font = font_bold
    ws["K12"] = data["opening_balance"]
    ws["K12"].number_format = "#,##0"
    ws["K12"].font = font_bold
    ws["K12"].alignment = Alignment(horizontal="right")

    curr_row = 13
    for e in data["entries"]:
        ws.cell(row=curr_row, column=1, value=e["entry_date"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_row, column=2, value=e["voucher_date"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_row, column=3, value=e["receipt_no"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_row, column=4, value=e["payment_no"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_row, column=5, value=e.get("partner_code") or "").alignment = Alignment(horizontal="center")
        ws.cell(row=curr_row, column=6, value=e.get("partner_name") or "").alignment = Alignment(horizontal="left")
        ws.cell(row=curr_row, column=7, value=e["description"]).alignment = Alignment(horizontal="left")
        ws.cell(row=curr_row, column=8, value=e.get("offset_account") or "").alignment = Alignment(horizontal="center")

        c_rec = ws.cell(row=curr_row, column=9, value=e["receipt_amount"] if e["receipt_amount"] > 0 else "")
        c_rec.number_format = "#,##0"
        c_rec.alignment = Alignment(horizontal="right")

        c_pay = ws.cell(row=curr_row, column=10, value=e["payment_amount"] if e["payment_amount"] > 0 else "")
        c_pay.number_format = "#,##0"
        c_pay.alignment = Alignment(horizontal="right")

        c_bal = ws.cell(row=curr_row, column=11, value=e["balance"])
        c_bal.number_format = "#,##0"
        c_bal.alignment = Alignment(horizontal="right")

        for c in range(1, 12):
            c_cell = ws.cell(row=curr_row, column=c)
            c_cell.font = font_regular
            c_cell.border = box_border

        curr_row += 1

    # Dòng Cộng số phát sinh
    ws.merge_cells(f"A{curr_row}:H{curr_row}")
    ws[f"A{curr_row}"] = "Cộng số phát sinh trong kỳ"
    ws[f"A{curr_row}"].font = font_bold
    c_tot_rec = ws.cell(row=curr_row, column=9, value=data["total_receipt"])
    c_tot_rec.number_format = "#,##0"
    c_tot_rec.font = font_bold
    c_tot_rec.alignment = Alignment(horizontal="right")

    c_tot_pay = ws.cell(row=curr_row, column=10, value=data["total_payment"])
    c_tot_pay.number_format = "#,##0"
    c_tot_pay.font = font_bold
    c_tot_pay.alignment = Alignment(horizontal="right")

    ws.cell(row=curr_row, column=11, value="")
    for c in range(1, 12):
        ws.cell(row=curr_row, column=c).border = box_border
        ws.cell(row=curr_row, column=c).fill = fill_total

    curr_row += 1
    # Dòng Số dư cuối kỳ
    ws.merge_cells(f"A{curr_row}:H{curr_row}")
    ws[f"A{curr_row}"] = "Số dư cuối kỳ"
    ws[f"A{curr_row}"].font = font_bold
    c_close = ws.cell(row=curr_row, column=11, value=data["closing_balance"])
    c_close.number_format = "#,##0"
    c_close.font = font_bold
    c_close.alignment = Alignment(horizontal="right")
    for c in range(1, 12):
        ws.cell(row=curr_row, column=c).border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_double)
        ws.cell(row=curr_row, column=c).fill = fill_total

    col_widths = {1: 13, 2: 13, 3: 11, 4: 11, 5: 15, 6: 28, 7: 35, 8: 12, 9: 17, 10: 17, 11: 18}
    for c, w in col_widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def export_bank_book_excel(
    company_id: int | None = None,
    year: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> bytes:
    """Export Bank Deposit Book to Excel matching Mau S05a-DNN."""
    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return b""
    data = get_bank_book_data(company_id=company_id, year=year, date_from=date_from, date_to=date_to)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "S05a-DNN_So_Tien_Gui_NH"
    ws.views.sheetView[0].showGridLines = True

    font_title = Font(name="Times New Roman", size=14, bold=True, color="1E3A8A")
    font_bold = Font(name="Times New Roman", size=10, bold=True)
    font_regular = Font(name="Times New Roman", size=10)
    font_italic = Font(name="Times New Roman", size=9, italic=True)

    border_thin = Side(border_style="thin", color="000000")
    border_double = Side(border_style="double", color="000000")
    box_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

    fill_header = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    fill_total = PatternFill(start_color="E0F2FE", end_color="E0F2FE", fill_type="solid")

    ws["A1"] = f"Đơn vị: {data['company_name']}"
    ws["A1"].font = font_bold
    ws["A2"] = f"Địa chỉ: {data['company_address']}"
    ws["A2"].font = font_regular
    ws["A3"] = f"Mã số thuế: {data['tax_code']}"
    ws["A3"].font = font_regular

    ws["G1"] = data["form_code"]
    ws["G1"].font = font_bold
    ws["G1"].alignment = Alignment(horizontal="right")
    ws["G2"] = data["circular"]
    ws["G2"].font = font_italic
    ws["G2"].alignment = Alignment(horizontal="right")

    ws.merge_cells("A5:J5")
    ws["A5"] = data["title"]
    ws["A5"].font = font_title
    ws["A5"].alignment = Alignment(horizontal="center")

    ws.merge_cells("A6:J6")
    ws["A6"] = f"Số tài khoản: {data['bank_account_no']} tại {data['bank_name']}"
    ws["A6"].font = font_bold
    ws["A6"].alignment = Alignment(horizontal="center")

    ws.merge_cells("A7:J7")
    ws["A7"] = data["date_str"]
    ws["A7"].font = font_italic
    ws["A7"].alignment = Alignment(horizontal="center")

    ws.merge_cells("A9:A10")
    ws["A9"] = "Ngày, tháng\nghi sổ"
    ws.merge_cells("B9:C9")
    ws["B9"] = "Chứng từ"
    ws["B10"] = "Số hiệu"
    ws["C10"] = "Ngày tháng"
    ws.merge_cells("D9:D10")
    ws["D9"] = "Mã khách"
    ws.merge_cells("E9:E10")
    ws["E9"] = "Tên khách"
    ws.merge_cells("F9:F10")
    ws["F9"] = "Diễn giải"
    ws.merge_cells("G9:G10")
    ws["G9"] = "TK đối ứng"
    ws.merge_cells("H9:J9")
    ws["H9"] = "Số tiền"
    ws["H10"] = "Gửi vào"
    ws["I10"] = "Rút ra"
    ws["J10"] = "Còn lại (Tồn)"

    for r in range(9, 11):
        for c in range(1, 11):
            cell = ws.cell(row=r, column=c)
            cell.font = font_bold
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.fill = fill_header
            cell.border = box_border

    # Row 11: Letters
    letters = ["A", "B", "C", "D", "E", "F", "G", "1", "2", "3"]
    for c_idx, ltr in enumerate(letters, 1):
        cell = ws.cell(row=11, column=c_idx, value=ltr)
        cell.font = font_bold
        cell.alignment = Alignment(horizontal="center")
        cell.fill = fill_header
        cell.border = box_border

    # Row 12: Số dư đầu kỳ
    ws["F12"] = "Số dư đầu kỳ"
    ws["F12"].font = font_bold
    ws["J12"] = data["opening_balance"]
    ws["J12"].number_format = "#,##0"
    ws["J12"].font = font_bold
    ws["J12"].alignment = Alignment(horizontal="right")
    for c in range(1, 11):
        ws.cell(row=12, column=c).border = box_border

    curr_row = 13
    for e in data["entries"]:
        ws.cell(row=curr_row, column=1, value=e["entry_date"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_row, column=2, value=e["voucher_no"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_row, column=3, value=e["voucher_date"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_row, column=4, value=e.get("partner_code") or "").alignment = Alignment(horizontal="center")
        ws.cell(row=curr_row, column=5, value=e.get("partner_name") or "").alignment = Alignment(horizontal="left")
        ws.cell(row=curr_row, column=6, value=e["description"]).alignment = Alignment(horizontal="left")
        ws.cell(row=curr_row, column=7, value=e["offset_account"]).alignment = Alignment(horizontal="center")

        c_dep = ws.cell(row=curr_row, column=8, value=e["deposit_amount"] if e["deposit_amount"] > 0 else "")
        c_dep.number_format = "#,##0"
        c_dep.alignment = Alignment(horizontal="right")

        c_wth = ws.cell(row=curr_row, column=9, value=e["withdraw_amount"] if e["withdraw_amount"] > 0 else "")
        c_wth.number_format = "#,##0"
        c_wth.alignment = Alignment(horizontal="right")

        c_bal = ws.cell(row=curr_row, column=10, value=e["balance"])
        c_bal.number_format = "#,##0"
        c_bal.alignment = Alignment(horizontal="right")

        for c in range(1, 11):
            c_cell = ws.cell(row=curr_row, column=c)
            c_cell.font = font_regular
            c_cell.border = box_border

        curr_row += 1

    # Dòng Cộng phát sinh
    ws.merge_cells(f"A{curr_row}:G{curr_row}")
    ws[f"A{curr_row}"] = "Cộng số phát sinh trong kỳ"
    ws[f"A{curr_row}"].font = font_bold
    c_tot_dep = ws.cell(row=curr_row, column=8, value=data["total_deposit"])
    c_tot_dep.number_format = "#,##0"
    c_tot_dep.font = font_bold
    c_tot_dep.alignment = Alignment(horizontal="right")

    c_tot_wth = ws.cell(row=curr_row, column=9, value=data["total_withdraw"])
    c_tot_wth.number_format = "#,##0"
    c_tot_wth.font = font_bold
    c_tot_wth.alignment = Alignment(horizontal="right")

    for c in range(1, 11):
        ws.cell(row=curr_row, column=c).border = box_border
        ws.cell(row=curr_row, column=c).fill = fill_total

    curr_row += 1
    # Dòng Số dư cuối kỳ
    ws.merge_cells(f"A{curr_row}:G{curr_row}")
    ws[f"A{curr_row}"] = "Số dư cuối kỳ"
    ws[f"A{curr_row}"].font = font_bold
    c_close = ws.cell(row=curr_row, column=10, value=data["closing_balance"])
    c_close.number_format = "#,##0"
    c_close.font = font_bold
    c_close.alignment = Alignment(horizontal="right")
    for c in range(1, 11):
        ws.cell(row=curr_row, column=c).border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_double)
        ws.cell(row=curr_row, column=c).fill = fill_total

    col_widths = {1: 13, 2: 12, 3: 13, 4: 15, 5: 35, 6: 38, 7: 11, 8: 18, 9: 18, 10: 18}
    for c, w in col_widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    output = BytesIO()
    wb.save(output)
    return output.getvalue()


# =====================================================================
# TẠO FILE MẪU EXCEL IMPORT VÀ IMPORT GIAO DỊCH HÀNG LOẠT
# =====================================================================

def generate_bank_import_template() -> bytes:
    """Tạo file mẫu Excel chuẩn để người dùng nhập giao dịch ngân hàng hàng loạt."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Giao_Dich_Ngan_Hang"
    ws.views.sheetView[0].showGridLines = True

    font_title = Font(name="Times New Roman", size=13, bold=True, color="1E3A8A")
    font_header = Font(name="Times New Roman", size=11, bold=True, color="FFFFFF")
    font_guide = Font(name="Times New Roman", size=10, italic=True, color="475569")

    fill_header = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")

    thin = Side(border_style="thin", color="CBD5E1")
    box_border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws["A1"] = "MẪU NHẬP GIAO DỊCH TIỀN GỬI NGÂN HÀNG"
    ws["A1"].font = font_title
    ws.merge_cells("A1:I1")

    ws["A2"] = "Hướng dẫn: Nhập giao dịch từ dòng 5 trở đi. Cột 'Loại giao dịch': Ghi 'Thu' (hoặc 'Báo Có') / 'Chi' (hoặc 'Báo Nợ' / 'UNC'). Cột Ngày: DD/MM/YYYY. Số tiền: nhập số tiền dương."
    ws["A2"].font = font_guide
    ws.merge_cells("A2:I2")

    headers = [
        "STT",
        "Ngày giao dịch (*)",
        "Loại giao dịch (*)",
        "Số chứng từ / UNC",
        "Số tiền (VNĐ) (*)",
        "Tài khoản đối ứng",
        "Khách hàng / Đối tác / NCC",
        "Diễn giải nội dung (*)",
        "Ghi chú kèm theo",
    ]
    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = box_border

    col_widths = {1: 7, 2: 18, 3: 22, 4: 18, 5: 18, 6: 16, 7: 38, 8: 48, 9: 25}
    for c, w in col_widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    ws.row_dimensions[4].height = 28

    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def generate_cash_import_template() -> bytes:
    """Tạo file mẫu Excel chuẩn để người dùng nhập thu chi tiền mặt hàng loạt."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "So_Quy_Tien_Mat"
    ws.views.sheetView[0].showGridLines = True

    font_title = Font(name="Times New Roman", size=13, bold=True, color="1E3A8A")
    font_header = Font(name="Times New Roman", size=11, bold=True, color="FFFFFF")
    font_guide = Font(name="Times New Roman", size=10, italic=True, color="475569")

    fill_header = PatternFill(start_color="047857", end_color="047857", fill_type="solid")

    thin = Side(border_style="thin", color="CBD5E1")
    box_border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws["A1"] = "MẪU NHẬP THU CHI SỔ QUỸ TIỀN MẶT (TK 111)"
    ws["A1"].font = font_title
    ws.merge_cells("A1:I1")

    ws["A2"] = "Hướng dẫn: Nhập giao dịch từ dòng 5 trở đi. Cột 'Loại giao dịch': Ghi 'Thu' (Phiếu thu) / 'Chi' (Phiếu chi). Cột Ngày: DD/MM/YYYY. Số tiền: nhập số tiền dương."
    ws["A2"].font = font_guide
    ws.merge_cells("A2:I2")

    headers = [
        "STT",
        "Ngày chứng từ (*)",
        "Loại chứng từ (*)",
        "Số chứng từ",
        "Số tiền (VNĐ) (*)",
        "Tài khoản đối ứng",
        "Đối tác / Người nộp - nhận",
        "Diễn giải nội dung (*)",
        "Ghi chú kèm theo",
    ]
    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = box_border

    col_widths = {1: 7, 2: 18, 3: 22, 4: 18, 5: 18, 6: 16, 7: 35, 8: 45, 9: 25}
    for c, w in col_widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    ws.row_dimensions[4].height = 28

    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def _skipped_note(skipped_rows: list[str]) -> str:
    if not skipped_rows:
        return ""
    shown = " ".join(skipped_rows[:5])
    more = f" (và {len(skipped_rows) - 5} dòng khác)" if len(skipped_rows) > 5 else ""
    return f" Bỏ qua {len(skipped_rows)} dòng sai tài khoản đối ứng — {shown}{more}"


def import_bank_transactions_from_excel(
    file_content: bytes,
    bank_account_no: str = "",
    bank_name: str = "",
    company_id: int | None = None,
) -> tuple[bool, str, int]:
    """Đọc file Excel và nạp hàng loạt giao dịch ngân hàng vào public.transport_cash_book."""
    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return False, "Chưa chọn công ty làm việc.", 0
    if not bank_account_no or not bank_name:
        comp_info = get_company_info(company_id)
        bank_account_no = bank_account_no or comp_info["bank_account"]
        bank_name = bank_name or comp_info["bank_name"]

    try:
        wb = openpyxl.load_workbook(BytesIO(file_content), data_only=True)
    except Exception as e:
        return False, f"Không mở được file Excel: {e}", 0

    ws = wb.active
    success_count = 0
    skipped_rows: list[str] = []

    # Tìm dòng bắt đầu dữ liệu (sau dòng tiêu đề cột)
    start_row = 5
    for r in range(1, 15):
        txt_b = str(ws.cell(row=r, column=2).value or "").lower()
        txt_c = str(ws.cell(row=r, column=3).value or "").lower()
        if "ngày" in txt_b or "loại" in txt_c or "ngày" in txt_c:
            start_row = r + 1
            break

    for r in range(start_row, ws.max_row + 1):
        raw_date = ws.cell(row=r, column=2).value
        raw_type = str(ws.cell(row=r, column=3).value or "").strip()
        raw_v_no = str(ws.cell(row=r, column=4).value or "").strip()
        raw_amount = ws.cell(row=r, column=5).value
        raw_offset = str(ws.cell(row=r, column=6).value or "").strip()
        raw_partner = str(ws.cell(row=r, column=7).value or "").strip()
        raw_desc = str(ws.cell(row=r, column=8).value or "").strip()
        raw_note = str(ws.cell(row=r, column=9).value or "").strip()

        # Dòng trống hoàn toàn -> bỏ qua
        if raw_date is None and not raw_type and raw_amount is None and not raw_desc:
            continue

        # Dòng hướng dẫn hoặc ghi chú mẫu -> bỏ qua
        if "hướng dẫn" in str(raw_date).lower() or "tự sinh số ct nếu để trống" in raw_note.lower():
            continue

        # Parse số tiền
        if raw_amount is None:
            continue
        try:
            amt = parse_vnd(raw_amount)
            if amt <= 0:
                continue
        except ValueError:
            continue

        # Parse ngày tháng
        v_date = date.today()
        if isinstance(raw_date, datetime):
            v_date = raw_date.date()
        elif isinstance(raw_date, date):
            v_date = raw_date
        elif isinstance(raw_date, str) and raw_date.strip():
            s = raw_date.strip()
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
                try:
                    v_date = datetime.strptime(s, fmt).date()
                    break
                except ValueError:
                    pass

        # Parse loại giao dịch: Thu (Báo Có) hay Chi (Báo Nợ / UNC)
        t_low = raw_type.lower()
        is_payment = (
            "chi" in t_low or "nợ" in t_low or "unc" in t_low or
            "rút" in t_low or "payment" in t_low or "out" in t_low
        )
        v_type = "payment" if is_payment else "receipt"

        if not raw_offset:
            raw_offset = default_partner_account("", raw_partner, is_payment)
        else:
            partner_err = check_partner_account("", raw_partner, raw_offset)
            if partner_err:
                skipped_rows.append(f"dòng {r}: {partner_err.replace('Sai tài khoản đối ứng: ', '')}")
                continue

        if not raw_desc:
            raw_desc = "Rút ra thanh toán tiền gửi NH" if is_payment else "Gửi vào tài khoản ngân hàng"

        v_no = raw_v_no
        if not v_no:
            prefix = "UNC-" if is_payment else "BC-"
            count_row = db._execute_query('''
                SELECT COUNT(*) as cnt FROM public.transport_cash_book
                WHERE company_id = %s AND book_type = 'bank' AND voucher_type = %s
            ''', (company_id, v_type), fetch=True, commit=False) or {}
            num = (count_row.get("cnt") or 0) + success_count + 1
            v_no = f"{prefix}{num:04d}"

        db._execute_query('''
            INSERT INTO public.transport_cash_book
            (company_id, book_type, entry_date, voucher_date, voucher_no, voucher_type,
             description, offset_account, amount, bank_account_no, bank_name, partner_name,
             source_type, note)
            VALUES (%s, 'bank', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'excel_import', %s)
        ''', (
            company_id, v_date, v_date, v_no, v_type,
            raw_desc, raw_offset, amt, bank_account_no, bank_name, raw_partner, raw_note
        ), commit=True)

        success_count += 1

    if success_count == 0:
        return False, "Không tìm thấy dòng giao dịch hợp lệ nào trong file Excel để nạp." + _skipped_note(skipped_rows), 0

    return True, f"Đã nạp thành công {success_count} giao dịch vào Sổ tiền gửi ngân hàng!" + _skipped_note(skipped_rows), success_count


def import_cash_transactions_from_excel(
    file_content: bytes,
    company_id: int | None = None,
) -> tuple[bool, str, int]:
    """Đọc file Excel và nạp hàng loạt thu chi tiền mặt vào public.transport_cash_book."""
    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return False, "Chưa chọn công ty làm việc.", 0
    try:
        wb = openpyxl.load_workbook(BytesIO(file_content), data_only=True)
    except Exception as e:
        return False, f"Không mở được file Excel: {e}", 0

    ws = wb.active
    success_count = 0
    skipped_rows: list[str] = []

    start_row = 5
    for r in range(1, 15):
        txt_b = str(ws.cell(row=r, column=2).value or "").lower()
        txt_c = str(ws.cell(row=r, column=3).value or "").lower()
        if "ngày" in txt_b or "loại" in txt_c or "chứng từ" in txt_c:
            start_row = r + 1
            break

    for r in range(start_row, ws.max_row + 1):
        raw_date = ws.cell(row=r, column=2).value
        raw_type = str(ws.cell(row=r, column=3).value or "").strip()
        raw_v_no = str(ws.cell(row=r, column=4).value or "").strip()
        raw_amount = ws.cell(row=r, column=5).value
        raw_offset = str(ws.cell(row=r, column=6).value or "").strip()
        raw_partner = str(ws.cell(row=r, column=7).value or "").strip()
        raw_desc = str(ws.cell(row=r, column=8).value or "").strip()
        raw_note = str(ws.cell(row=r, column=9).value or "").strip()

        if raw_date is None and not raw_type and raw_amount is None and not raw_desc:
            continue
        if "hướng dẫn" in str(raw_date).lower():
            continue

        if raw_amount is None:
            continue
        try:
            amt = parse_vnd(raw_amount)
            if amt <= 0:
                continue
        except ValueError:
            continue

        v_date = date.today()
        if isinstance(raw_date, datetime):
            v_date = raw_date.date()
        elif isinstance(raw_date, date):
            v_date = raw_date
        elif isinstance(raw_date, str) and raw_date.strip():
            s = raw_date.strip()
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
                try:
                    v_date = datetime.strptime(s, fmt).date()
                    break
                except ValueError:
                    pass

        t_low = raw_type.lower()
        is_payment = ("chi" in t_low or "payment" in t_low or "out" in t_low or "pc" in t_low)
        v_type = "payment" if is_payment else "receipt"

        if not raw_offset:
            raw_offset = default_partner_account("", raw_partner, is_payment)
        else:
            partner_err = check_partner_account("", raw_partner, raw_offset)
            if partner_err:
                skipped_rows.append(f"dòng {r}: {partner_err.replace('Sai tài khoản đối ứng: ', '')}")
                continue

        if not raw_desc:
            raw_desc = "Chi tiền mặt" if is_payment else "Thu tiền mặt"

        v_no = raw_v_no
        if not v_no:
            prefix = "PC" if is_payment else "PT"
            count_row = db._execute_query('''
                SELECT COUNT(*) as cnt FROM public.transport_cash_book
                WHERE company_id = %s AND book_type = 'cash' AND voucher_type = %s
            ''', (company_id, v_type), fetch=True, commit=False) or {}
            num = (count_row.get("cnt") or 0) + success_count + 1
            v_no = f"{prefix}{num:03d}"

        db._execute_query('''
            INSERT INTO public.transport_cash_book
            (company_id, book_type, entry_date, voucher_date, voucher_no, voucher_type,
             description, offset_account, amount, partner_name, source_type, note)
            VALUES (%s, 'cash', %s, %s, %s, %s, %s, %s, %s, %s, 'excel_import', %s)
        ''', (
            company_id, v_date, v_date, v_no, v_type,
            raw_desc, raw_offset, amt, raw_partner, raw_note
        ), commit=True)

        success_count += 1

    if success_count == 0:
        return False, "Không tìm thấy dòng chứng từ hợp lệ nào trong file Excel để nạp." + _skipped_note(skipped_rows), 0

    return True, f"Đã nạp thành công {success_count} chứng từ vào Sổ quỹ tiền mặt!" + _skipped_note(skipped_rows), success_count


from psycopg2.extras import RealDictCursor


def renumber_vouchers(book_type: str, company_id: int | None = None) -> dict:
    """Đánh lại số chứng từ tuần tự cho một sổ. Trả {"ok", "message", "vouchers", "rows"}."""
    if book_type not in ("cash", "bank"):
        return {"ok": False, "message": "Sổ không hợp lệ.", "vouchers": 0, "rows": 0}

    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return {"ok": False, "message": "Chưa chọn công ty làm việc.", "vouchers": 0, "rows": 0}
    company_id = int(company_id)

    def _row_date(r: dict) -> date:
        d = r.get("voucher_date") or r.get("entry_date")
        if not d:
            return date.max
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, date):
            return d
        try:
            return parse_flexible_date(d)
        except Exception:
            return date.max

    special_payment_types = ("tax_payment", "insurance_payment", "salary_payment")
    rows_changed = 0

    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # a. Lấy các dòng của sổ thuộc công ty
            cursor.execute(
                """
                    SELECT id, voucher_type, voucher_date, entry_date, voucher_no, source_type, source_id
                    FROM public.transport_cash_book
                    WHERE company_id = %s AND book_type = %s
                    ORDER BY id
                    FOR UPDATE
                    """,
                (company_id, book_type),
            )
            rows = cursor.fetchall() or []

            # b. Tách các dòng đồng bộ, gom các dòng còn lại thành chứng từ
            voucher_groups: dict[Any, list[dict]] = {}
            for r in rows:
                st = (r.get("source_type") or "").strip()
                if st in ("cash_transfer", "bank_transfer"):
                    continue

                sid = str(r.get("source_id") or "").strip()
                v_type = (r.get("voucher_type") or "payment").strip().lower()
                if st in special_payment_types and sid:
                    key = (v_type, st, sid)
                else:
                    key = ("single", r["id"])

                if key not in voucher_groups:
                    voucher_groups[key] = []
                voucher_groups[key].append(r)

            # c. Tách theo voucher_type và sắp xếp theo (ngày, id nhỏ nhất của chứng từ)
            receipt_vouchers = []
            payment_vouchers = []
            for group_rows in voucher_groups.values():
                v_type = (group_rows[0].get("voucher_type") or "payment").strip().lower()
                min_id = min(r["id"] for r in group_rows)
                v_date = min(_row_date(r) for r in group_rows)
                item = {
                    "voucher_type": v_type,
                    "rows": group_rows,
                    "min_id": min_id,
                    "date": v_date,
                }
                if v_type == "receipt":
                    receipt_vouchers.append(item)
                else:
                    payment_vouchers.append(item)

            receipt_vouchers.sort(key=lambda v: (v["date"], v["min_id"]))
            payment_vouchers.sort(key=lambda v: (v["date"], v["min_id"]))

            total_vouchers = len(receipt_vouchers) + len(payment_vouchers)

            # d. UPDATE voucher_no của từng dòng (chỉ UPDATE khi khác số hiện tại)
            for idx, v in enumerate(receipt_vouchers, start=1):
                new_no = f"PT{idx:03d}" if book_type == "cash" else f"BC-{idx:04d}"
                for r in v["rows"]:
                    if (r.get("voucher_no") or "").strip() != new_no:
                        cursor.execute(
                            "UPDATE public.transport_cash_book SET voucher_no = %s WHERE id = %s",
                            (new_no, r["id"]),
                        )
                        rows_changed += cursor.rowcount
                        r["voucher_no"] = new_no

            for idx, v in enumerate(payment_vouchers, start=1):
                new_no = f"PC{idx:03d}" if book_type == "cash" else f"UNC-{idx:04d}"
                for r in v["rows"]:
                    if (r.get("voucher_no") or "").strip() != new_no:
                        cursor.execute(
                            "UPDATE public.transport_cash_book SET voucher_no = %s WHERE id = %s",
                            (new_no, r["id"]),
                        )
                        rows_changed += cursor.rowcount
                        r["voucher_no"] = new_no

            # e. Cập nhật các dòng đồng bộ hai chiều theo nguồn hiện tại trong DB
            cursor.execute(
                """
                    SELECT id, book_type, voucher_type, voucher_no, source_type, source_id
                    FROM public.transport_cash_book
                    WHERE company_id = %s AND source_type IN ('cash_transfer', 'bank_transfer')
                    FOR UPDATE
                    """,
                (company_id,),
            )
            synced_rows = cursor.fetchall() or []
            for s in synced_rows:
                raw_sid = (s.get("source_id") or "").strip()
                try:
                    src_id = int(raw_sid)
                except (ValueError, TypeError):
                    continue

                cursor.execute(
                    """
                        SELECT id, voucher_no, voucher_type
                        FROM public.transport_cash_book
                        WHERE company_id = %s AND id = %s
                        """,
                    (company_id, src_id),
                )
                src_row = cursor.fetchone()
                if not src_row or not src_row.get("voucher_no"):
                    continue

                src_vno = src_row["voucher_no"].strip()
                if s["book_type"] == "bank":
                    prefix = "BC-" if (s.get("voucher_type") or "").strip().lower() == "receipt" else "UNC-"
                else:
                    prefix = "PC-" if (s.get("voucher_type") or "").strip().lower() == "payment" else "PT-"

                target_vno = f"{prefix}{src_vno}"
                if (s.get("voucher_no") or "").strip() != target_vno:
                    cursor.execute(
                        "UPDATE public.transport_cash_book SET voucher_no = %s WHERE id = %s",
                        (target_vno, s["id"]),
                    )
                    rows_changed += cursor.rowcount

            conn.commit()

    return {
        "ok": True,
        "message": f"Đã đánh lại số chứng từ ({total_vouchers} chứng từ).",
        "vouchers": total_vouchers,
        "rows": rows_changed,
    }
