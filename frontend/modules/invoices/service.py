"""Persistence and validation for company purchase invoices."""

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import logging

from psycopg2 import Binary

from backend.services.database import db

logger = logging.getLogger(__name__)


MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_FILES = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}




def money(value, label, *, maximum=Decimal("999999999999999")):
    raw = str(value or "").strip().replace(" ", "")
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif raw.count(".") > 1 or ("." in raw and len(raw.rsplit(".", 1)[1]) == 3):
        raw = raw.replace(".", "")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{label} không hợp lệ.") from exc
    if not number.is_finite() or number < 0 or number > maximum:
        raise ValueError(f"{label} không hợp lệ.")
    return number


def _text(form, key, label, limit, required=False):
    value = str(form.get(key, "")).strip()
    if required and not value:
        raise ValueError(f"Cần nhập {label}.")
    if len(value) > limit:
        raise ValueError(f"{label} quá dài.")
    return value


def parse_invoice(form):
    try:
        invoice_date = date.fromisoformat(_text(form, "invoice_date", "ngày hóa đơn", 10, True))
    except ValueError as exc:
        raise ValueError("Ngày hóa đơn không hợp lệ.") from exc
    values = {"invoice_date": invoice_date}
    for key, label, limit, required in (
        ("serial", "ký hiệu", 50, False),
        ("invoice_number", "số hóa đơn", 80, False),
        ("seller_name", "tên bên bán", 255, False),
        ("seller_tax_code", "MST bên bán", 30, False),
        ("seller_address", "địa chỉ bên bán", 1000, False),
        ("buyer_name", "tên bên mua", 255, True),
        ("buyer_tax_code", "MST bên mua", 30, False),
        ("buyer_address", "địa chỉ bên mua", 1000, False),
        ("note", "ghi chú", 1000, False),
    ):
        values[key] = _text(form, key, label, limit, required)
    values["payment_method"] = _text(form, "payment_method", "hình thức thanh toán", 50, False)
    descriptions = form.getlist("description")
    units = form.getlist("unit")
    quantities = form.getlist("quantity")
    prices = form.getlist("unit_price")
    if not (len(descriptions) == len(units) == len(quantities) == len(prices)) or len(descriptions) > 50:
        raise ValueError("Danh sách hàng hóa không hợp lệ.")
    items = []
    subtotal = Decimal(0)
    for i, description in enumerate(descriptions):
        description = str(description).strip()
        if not description and not str(quantities[i]).strip() and not str(prices[i]).strip():
            continue
        if not description or len(description) > 500 or len(str(units[i])) > 50:
            raise ValueError(f"Dòng {i + 1}: tên hàng hóa không hợp lệ.")
        quantity = money(quantities[i], f"Dòng {i + 1}: số lượng", maximum=Decimal("999999999"))
        unit_price = money(prices[i], f"Dòng {i + 1}: đơn giá")
        if quantity <= 0:
            raise ValueError(f"Dòng {i + 1}: số lượng phải lớn hơn 0.")
        amount = (quantity * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        subtotal += amount
        items.append({"description": description, "unit": str(units[i]).strip(),
                      "quantity": str(quantity), "unit_price": str(unit_price), "amount": str(amount)})
    if not items:
        raise ValueError("Cần nhập ít nhất một hàng hóa hoặc dịch vụ.")
    raw_vat = form.get("vat_percent")
    vat_percent = money(raw_vat, "Thuế suất VAT", maximum=Decimal("100")) if raw_vat not in (None, "") else Decimal(0)
    vat_amount = (subtotal * vat_percent / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    raw_cost = str(form.get("tk_no_cost") or "").strip()
    if not raw_cost:
        all_desc = " ".join([str(it.get("description", "")) for it in items]).lower()
        if any(w in all_desc for w in ("quả cân", "quả chuẩn", "công cụ", "dụng cụ", "thiết bị đo", "máy in", "máy tính")):
            tk_no_cost = "153"
        elif any(w in all_desc for w in ("chữ ký số", "phần mềm", "bảo hiểm", "cks")):
            tk_no_cost = "242"
        elif any(w in all_desc for w in ("hóa đơn điện tử", "văn phòng phẩm", "tiếp khách", "dịch vụ chữ ký số")):
            tk_no_cost = "642"
        elif any(w in all_desc for w in ("xe ô tô", "xe tải", "rơ mooc", "sơ mi")):
            tk_no_cost = "211"
        else:
            tk_no_cost = "154"
    else:
        tk_no_cost = raw_cost
    tk_no_vat = str(form.get("tk_no_vat") or "1331").strip() or "1331"
    tk_co_pay = str(form.get("tk_co_pay") or "331").strip() or "331"
    values.update(items=items, subtotal=subtotal, vat_percent=vat_percent,
                  vat_amount=vat_amount, total=subtotal + vat_amount,
                  tk_no_cost=tk_no_cost, tk_no_vat=tk_no_vat, tk_co_pay=tk_co_pay)
    return values


_FIELDS = ("invoice_date", "serial", "invoice_number", "seller_company_id", "seller_name", "seller_tax_code",
           "seller_address", "buyer_name", "buyer_tax_code", "buyer_address",
           "payment_method", "items", "subtotal", "vat_percent", "vat_amount", "total", "note", "buyer_company_id",
           "tk_no_cost", "tk_no_vat", "tk_co_pay")


def save_invoice(values, invoice_id=None, attachment=None):
    data = [json.dumps(values[key], ensure_ascii=False) if key == "items" else values.get(key)
            for key in _FIELDS]
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            if invoice_id is None:
                fields = (*_FIELDS, "source_filename", "source_type", "source_data")
                placeholders = ["%s::jsonb" if key == "items" else "%s" for key in fields]
                cursor.execute(
                    f"INSERT INTO public.transport_input_invoices ({', '.join(fields)}) "
                    f"VALUES ({', '.join(placeholders)}) RETURNING id",
                    (*data, attachment[0] if attachment else None,
                      attachment[1] if attachment else None,
                      Binary(attachment[2]) if attachment else None))
            else:
                assignments = ", ".join(f"{key} = %s{'::jsonb' if key == 'items' else ''}" for key in _FIELDS)
                query = f"UPDATE public.transport_input_invoices SET {assignments}, updated_at = now()"
                if attachment:
                    query += ", source_filename = %s, source_type = %s, source_data = %s"
                    data.extend((attachment[0], attachment[1], Binary(attachment[2])))
                query += " WHERE id = %s RETURNING id"
                cursor.execute(query, (*data, invoice_id))
            row = cursor.fetchone()
            if not row:
                raise ValueError("Không tìm thấy hóa đơn.")
            conn.commit()
            saved_id = row[0]
            try:
                sync_invoice_to_cash_book(saved_id)
            except Exception as exc:
                logger.warning("Lỗi đồng bộ hóa đơn %s sang sổ quỹ/ngân hàng: %s", saved_id, exc)
            return saved_id


def invoice_list_stats(start, end, buyer_company_id=None, q=None) -> dict:
    params = [start, end]
    buyer_filter = ""
    if buyer_company_id:
        buyer_filter = " AND buyer_company_id = %s"
        params.append(buyer_company_id)
    search_filter = ""
    if q and str(q).strip():
        term = f"%{str(q).strip()}%"
        search_filter = """ AND (
            invoice_number ILIKE %s OR
            serial ILIKE %s OR
            seller_name ILIKE %s OR
            seller_tax_code ILIKE %s OR
            note ILIKE %s OR
            items::text ILIKE %s
        )"""
        params.extend([term, term, term, term, term, term])
    return db._execute_query(
        f"""SELECT COUNT(*) AS total,
                   COALESCE(SUM(subtotal), 0) AS total_subtotal,
                   COALESCE(SUM(vat_amount), 0) AS total_vat,
                   COALESCE(SUM(total), 0) AS total_amount
              FROM public.transport_input_invoices
             WHERE invoice_date >= %s AND invoice_date < %s{buyer_filter}{search_filter}""",
        tuple(params), fetch=True, commit=False,
    ) or {"total": 0, "total_subtotal": 0, "total_vat": 0, "total_amount": 0}


def list_invoices(start, end, buyer_company_id=None, limit=None, offset=None, q=None):
    params = [start, end]
    buyer_filter = ""
    if buyer_company_id:
        buyer_filter = " AND buyer_company_id = %s"
        params.append(buyer_company_id)
    search_filter = ""
    if q and str(q).strip():
        term = f"%{str(q).strip()}%"
        search_filter = """ AND (
            invoice_number ILIKE %s OR
            serial ILIKE %s OR
            seller_name ILIKE %s OR
            seller_tax_code ILIKE %s OR
            note ILIKE %s OR
            items::text ILIKE %s
        )"""
        params.extend([term, term, term, term, term, term])
    query = f"""
            SELECT id, invoice_date, serial, invoice_number, seller_name, seller_tax_code,
                   buyer_name, subtotal, vat_percent, vat_amount, total, source_filename,
                   COALESCE(tk_no_cost, '154') as tk_no_cost,
                   COALESCE(tk_no_vat, '1331') as tk_no_vat,
                   COALESCE(tk_co_pay, '331') as tk_co_pay
            FROM public.transport_input_invoices
            WHERE invoice_date >= %s AND invoice_date < %s{buyer_filter}{search_filter}
            ORDER BY invoice_date DESC, id DESC
        """
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)
    return db._execute_query(query, tuple(params), fetchall=True, commit=False) or []


def get_invoice(invoice_id):
    return db._execute_query("SELECT * FROM public.transport_input_invoices WHERE id = %s",
                             (invoice_id,), fetch=True, commit=False)


def sync_invoice_to_cash_book(invoice_id: int):
    """
    Tự động đồng bộ hóa đơn đầu vào sang Sổ quỹ tiền mặt (TK 111 / 1111)
    hoặc Sổ tiền gửi ngân hàng (TK 112 / 1121).
    Nếu tài khoản có (tk_co_pay) là công nợ (331, v.v.), xóa khỏi sổ quỹ/ngân hàng nếu đã tồn tại.
    """
    invoice = get_invoice(invoice_id)
    if not invoice:
        db._execute_query(
            "DELETE FROM public.transport_cash_book WHERE source_type = 'input_invoice' AND source_id = %s",
            (str(invoice_id),), commit=True
        )
        return

    tk_co = str(invoice.get("tk_co_pay") or "331").strip()
    target_book_type = None
    if tk_co.startswith("111"):
        target_book_type = "cash"
    elif tk_co.startswith("112"):
        target_book_type = "bank"

    if not target_book_type:
        # Nếu hạch toán vào 331 hoặc tài khoản khác (không phải 111/112):
        # Tự động xóa chứng từ thu/chi tiền mặt/ngân hàng đã đồng bộ trước đó nếu có
        db._execute_query(
            "DELETE FROM public.transport_cash_book WHERE source_type = 'input_invoice' AND source_id = %s",
            (str(invoice_id),), commit=True
        )
        return

    company_id = invoice.get("buyer_company_id")
    total_amount = Decimal(str(invoice.get("total") or 0))
    inv_date = invoice.get("invoice_date") or date.today()
    inv_num = invoice.get("invoice_number") or ""
    seller_name = (invoice.get("seller_name") or "").strip()
    seller_tax = (invoice.get("seller_tax_code") or "").strip()
    offset_account = str(invoice.get("tk_no_cost") or "154").strip() or "154"

    # Xây dựng diễn giải thông minh từ hàng hóa
    items = invoice.get("items") or []
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = []
    item_names = [it.get("description", "").strip() for it in items if isinstance(it, dict) and it.get("description")]
    items_summary = ", ".join(item_names[:2])
    if len(item_names) > 2:
        items_summary += "..."

    note = (invoice.get("note") or "").strip()
    if items_summary and inv_num:
        desc = f"Chi tiền mua {items_summary} (HĐ {inv_num})"
    elif items_summary:
        desc = f"Chi tiền mua {items_summary}"
    elif inv_num:
        desc = f"Chi tiền mua hàng/DV theo HĐ {inv_num}"
    elif note and note != "Đồng bộ từ Email":
        desc = note
    else:
        desc = f"Chi tiền mua hàng cho {seller_name or 'nhà cung cấp'}"

    note_text = f"Hóa đơn đầu vào #{inv_num} - {seller_name}" if inv_num else f"Hóa đơn đầu vào ID {invoice_id}"

    from frontend.modules.transport.cash_bank_service import get_company_info
    comp_info = get_company_info(company_id)
    bank_account_no = comp_info.get("bank_account") if target_book_type == "bank" else None
    bank_name = comp_info.get("bank_name") if target_book_type == "bank" else None

    existing = db._execute_query("""
        SELECT id, book_type, voucher_no 
        FROM public.transport_cash_book 
        WHERE source_type = 'input_invoice' AND source_id = %s
    """, (str(invoice_id),), fetch=True, commit=False)

    if existing:
        if existing["book_type"] == target_book_type:
            db._execute_query("""
                UPDATE public.transport_cash_book
                SET entry_date = %s, voucher_date = %s, amount = %s,
                    description = %s, offset_account = %s, partner_name = %s,
                    partner_code = %s, bank_account_no = %s, bank_name = %s,
                    note = %s, company_id = %s
                WHERE id = %s
            """, (inv_date, inv_date, total_amount, desc, offset_account,
                  seller_name, seller_tax, bank_account_no, bank_name,
                  note_text, company_id, existing["id"]), commit=True)
            return existing["id"]
        else:
            db._execute_query("DELETE FROM public.transport_cash_book WHERE id = %s", (existing["id"],), commit=True)

    prefix = "PC" if target_book_type == "cash" else "UNC-"
    rows = db._execute_query("""
        SELECT voucher_no FROM public.transport_cash_book
        WHERE company_id = %s AND book_type = %s AND voucher_no LIKE %s
    """, (company_id, target_book_type, f"{prefix}%"), fetchall=True, commit=False) or []

    max_num = 0
    for r in rows:
        vno = str(r.get("voucher_no") or "")
        clean_num = vno.replace(prefix, "").split("/")[0].strip()
        try:
            val = int(clean_num)
            if val > max_num:
                max_num = val
        except ValueError:
            pass

    next_num = max_num + 1
    voucher_no = f"{prefix}{next_num:04d}" if "-" in prefix else f"{prefix}{next_num:03d}"

    ins_row = db._execute_query("""
        INSERT INTO public.transport_cash_book
            (company_id, book_type, entry_date, voucher_date, voucher_no,
             voucher_type, description, offset_account, amount, bank_account_no,
             bank_name, partner_code, partner_name, source_type, source_id, note)
        VALUES (%s, %s, %s, %s, %s, 'payment', %s, %s, %s, %s, %s, %s, %s, 'input_invoice', %s, %s)
        RETURNING id
    """, (company_id, target_book_type, inv_date, inv_date, voucher_no,
          desc, offset_account, total_amount, bank_account_no, bank_name,
          seller_tax, seller_name, str(invoice_id), note_text), fetch=True, commit=True)

    return ins_row["id"] if ins_row else None

