"""Persistence and validation for company purchase invoices."""

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import logging
import re

from psycopg2 import Binary

from backend.services.database import db
from frontend.formatting.text import fold_text as _fold

logger = logging.getLogger(__name__)


MAX_FILE_BYTES = 40 * 1024 * 1024
ALLOWED_FILES = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}




_ELEC_PATTERNS = ("dien nang", "dien sinh hoat", "dien kinh doanh", "dien san xuat", "dien tieu thu", "kwh")
_TIEN_DIEN_RE = re.compile(r"\btien dien\b(?!\s+(thoai|tu|toan))")


def is_electricity_text(text) -> bool:
    f = _fold(text)
    if not f:
        return False
    if any(p in f for p in _ELEC_PATTERNS):
        return True
    return bool(_TIEN_DIEN_RE.search(f))


def _invoice_items(items) -> list:
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = []
    return items or []


_DIESEL_RE = re.compile(r"\bdau\s+(?:diezen|diesel|diesen|do)\b")
_PETROL_RE = re.compile(r"\bxang\b")


def fuel_payment_description(items, invoice_number) -> str | None:
    """Diễn giải phiếu chi mua nhiên liệu: 'Chuyển tiền mua dầu HĐ626014'; None nếu không phải hóa đơn xăng dầu."""
    names = [_fold(it.get("description")) for it in _invoice_items(items) if isinstance(it, dict)]
    if any(_DIESEL_RE.search(n) for n in names):
        fuel = "dầu"
    elif any(_PETROL_RE.search(n) for n in names):
        fuel = "xăng"
    else:
        return None
    return f"Chuyển tiền mua {fuel} HĐ{str(invoice_number or '').strip()}"


def invoice_payment_description(items, invoice_date, invoice_number) -> str | None:
    """Diễn giải phiếu chi có sẵn cho hóa đơn tiền điện hoặc xăng dầu; None nếu là hàng hóa khác."""
    return (electricity_payment_description(items, invoice_date, invoice_number)
            or fuel_payment_description(items, invoice_number))


def electricity_payment_description(items, invoice_date, invoice_number) -> str | None:
    """Diễn giải phiếu chi tiền điện: 'TT tiền điện tháng 7 (HĐ 16094)'; None nếu không phải hóa đơn tiền điện."""
    items = _invoice_items(items)
    if not any(is_electricity_text(it.get("description")) for it in (items or []) if isinstance(it, dict)):
        return None
    month = getattr(invoice_date, "month", None)
    if month is None and invoice_date:
        match = re.match(r"^\d{4}-(\d{1,2})", str(invoice_date))
        month = int(match.group(1)) if match else None
    text = f"TT tiền điện tháng {month}" if month else "TT tiền điện"
    number = str(invoice_number or "").strip()
    return f"{text} (HĐ {number})" if number else text


def suggest_cost_account(descriptions):
    """Tài khoản Nợ gợi ý theo tên hàng hóa/dịch vụ trên hóa đơn mua vào."""
    all_desc = " ".join(str(d or "") for d in descriptions).lower()
    if any(w in all_desc for w in ("quả cân", "quả chuẩn", "công cụ", "dụng cụ", "thiết bị đo", "máy in", "máy tính")):
        return "153"
    if "bảo hiểm xã hội điện tử" in all_desc or "bhxh điện tử" in all_desc:
        return "642"   # dịch vụ phần mềm kê khai BHXH điện tử, không phải bảo hiểm trả trước
    if any(w in all_desc for w in ("chữ ký số", "phần mềm", "bảo hiểm", "cks")):
        return "242"
    if is_electricity_text(all_desc):
        return "642"
    if (any(w in all_desc for w in ("hóa đơn điện tử", "văn phòng phẩm", "tiếp khách", "dịch vụ chữ ký số",
                                    "tên miền", "domain", "hosting", "máy chủ ảo"))
            or re.search(r"\.(com|net|org|vn|info|biz)\b", all_desc)):
        return "642"
    if any(w in all_desc for w in ("xe ô tô", "xe tải", "rơ mooc", "sơ mi")):
        return "211"
    return "154"


def suggest_pay_account(descriptions):
    """Tài khoản Có gợi ý: tiền điện trả bằng tiền mặt (1111), còn lại ghi công nợ phải trả (331)."""
    all_desc = " ".join(str(d or "") for d in descriptions)
    return "1111" if is_electricity_text(all_desc) else "331"


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


def decimal_input(value, label, *, maximum=Decimal("999999999999999")):
    """A number typed in a number field (quantity, unit price): "." is the decimal point, so 134.357 litres stay 134,357
    (money() would read it as 134.357 thousands = 134357)."""
    raw = str(value or "").strip().replace(" ", "").replace(",", ".")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{label} không hợp lệ.") from exc
    if not number.is_finite() or number < 0 or number > maximum:
        raise ValueError(f"{label} không hợp lệ.")
    return number


def _text(form, key, label, required=False):
    value = str(form.get(key, "")).strip()
    if required and not value:
        raise ValueError(f"Cần nhập {label}.")
    return value


def _valid_tax_code(value: str, label: str) -> str:
    """MST rỗng thì bỏ qua; 10 số / 10-3 số chi nhánh / 12 số (cá nhân) mới hợp lệ."""
    from frontend.modules.invoices.companies import normalize_tax_code
    code = (value or "").strip()
    if not code or re.fullmatch(r"[0-9]{12}", code):
        return code
    try:
        return normalize_tax_code(code)
    except ValueError:
        raise ValueError(f"{label} phải có 10 chữ số, 10 chữ số-3 chữ số chi nhánh, hoặc 12 chữ số (cá nhân).") from None


def parse_invoice(form):
    from frontend.modules.transport.cash_bank_service import parse_flexible_date
    raw_date = _text(form, "invoice_date", "ngày hóa đơn", True)
    try:
        invoice_date = parse_flexible_date(raw_date)
    except ValueError as exc:
        raise ValueError("Ngày hóa đơn không hợp lệ.") from exc
    values = {"invoice_date": invoice_date}
    for key, label, required in (
        ("serial", "ký hiệu", False),
        ("invoice_number", "số hóa đơn", False),
        ("seller_name", "tên bên bán", False),
        ("seller_tax_code", "MST bên bán", False),
        ("seller_address", "địa chỉ bên bán", False),
        ("buyer_name", "tên bên mua", True),
        ("buyer_tax_code", "MST bên mua", False),
        ("buyer_address", "địa chỉ bên mua", False),
        ("note", "ghi chú", False),
    ):
        values[key] = _text(form, key, label, required)
    for key, label in (("seller_tax_code", "MST bên bán"), ("buyer_tax_code", "MST bên mua")):
        values[key] = _valid_tax_code(values[key], label)
    values["payment_method"] = _text(form, "payment_method", "hình thức thanh toán")
    descriptions = form.getlist("description")
    units = form.getlist("unit")
    quantities = form.getlist("quantity")
    prices = form.getlist("unit_price")
    posted_amounts = form.getlist("amount")
    if len(posted_amounts) != len(descriptions):
        posted_amounts = [""] * len(descriptions)
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
        quantity = decimal_input(quantities[i], f"Dòng {i + 1}: số lượng", maximum=Decimal("999999999"))
        unit_price = decimal_input(prices[i], f"Dòng {i + 1}: đơn giá")
        if quantity <= 0:
            raise ValueError(f"Dòng {i + 1}: số lượng phải lớn hơn 0.")
        # Tiền hàng của dòng là số trên hóa đơn (form giữ nguyên khi không sửa số lượng, đơn giá); dòng mới hoặc dòng
        # đã sửa thì tính đến đồng: 101,52285 lít x 29.550 = 3.000.000,2175 ghi 3.000.000
        posted = str(posted_amounts[i]).strip()
        amount = (decimal_input(posted, f"Dòng {i + 1}: thành tiền") if posted
                  else quantity * unit_price).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        subtotal += amount
        items.append({"description": description, "unit": str(units[i]).strip(),
                      "quantity": str(quantity), "unit_price": str(unit_price), "amount": str(amount)})
    if not items:
        raise ValueError("Cần nhập ít nhất một hàng hóa hoặc dịch vụ.")
    raw_vat = form.get("vat_percent")
    vat_percent = money(raw_vat, "Thuế suất VAT", maximum=Decimal("100")) if raw_vat not in (None, "") else Decimal(0)
    # Hoá đơn VND làm tròn tiền thuế đến đồng (14.907.407 x 8% ghi 1.192.593, không phải 1.192.592,56)
    vat_amount = (subtotal * vat_percent / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    # Tiền thuế ghi tay: hoá đơn có dòng không chịu thuế (lệ phí...) thì thuế không bằng thuế suất x tổng tiền hàng
    raw_vat_amount = str(form.get("vat_amount") or "").strip()
    if raw_vat_amount:
        vat_amount = money(raw_vat_amount, "Tiền thuế GTGT").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if vat_amount > subtotal:
            raise ValueError("Tiền thuế GTGT không được lớn hơn tổng tiền hàng.")
    raw_cost = str(form.get("tk_no_cost") or "").strip()
    tk_no_cost = raw_cost or suggest_cost_account(it.get("description", "") for it in items)
    tk_no_vat = str(form.get("tk_no_vat") or "1331").strip() or "1331"
    tk_co_pay = str(form.get("tk_co_pay") or "").strip() or suggest_pay_account(it.get("description", "") for it in items)
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
            sync_invoice_to_cash_book(saved_id)
            return saved_id


def insert_synced_input_invoice(*, invoice_date, serial, invoice_number, seller_company_id, seller_name,
                                 seller_tax_code, seller_address, buyer_name, buyer_tax_code, buyer_address,
                                 payment_method, items, subtotal, vat_percent, vat_amount, total, note,
                                 buyer_company_id, source_filename=None, source_type=None, source_data=None):
    """One input invoice fetched from Gmail or SaveInvoice; the cost account follows what the lines say."""
    row = db._execute_query("""
        INSERT INTO public.transport_input_invoices (
            invoice_date, serial, invoice_number, seller_company_id, seller_name,
            seller_tax_code, seller_address, buyer_name, buyer_tax_code, buyer_address,
            payment_method, items, subtotal, vat_percent, vat_amount, total, note,
            buyer_company_id, source_filename, source_type, source_data, tk_no_cost, tk_co_pay
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        ) RETURNING id
    """, (
        invoice_date, serial, invoice_number, seller_company_id, seller_name,
        seller_tax_code, seller_address, buyer_name, buyer_tax_code, buyer_address,
        payment_method, json.dumps(items, ensure_ascii=False),
        subtotal, vat_percent, vat_amount, total, note,
        buyer_company_id, source_filename, source_type,
        Binary(source_data) if source_data else None,
        suggest_cost_account(it.get("description", "") for it in items if isinstance(it, dict)),
        suggest_pay_account(it.get("description", "") for it in items if isinstance(it, dict)),
    ), fetch=True, commit=True)
    invoice_id = row["id"] if row else None
    if invoice_id and suggest_pay_account(it.get("description", "") for it in items if isinstance(it, dict)).startswith("111"):
        sync_invoice_to_cash_book(invoice_id)
    return invoice_id


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
        buyer_filter = " AND t.buyer_company_id = %s"
        params.append(buyer_company_id)
    search_filter = ""
    if q and str(q).strip():
        term = f"%{str(q).strip()}%"
        search_filter = """ AND (
            t.invoice_number ILIKE %s OR
            t.serial ILIKE %s OR
            t.seller_name ILIKE %s OR
            t.seller_tax_code ILIKE %s OR
            t.note ILIKE %s OR
            t.items::text ILIKE %s
        )"""
        params.extend([term, term, term, term, term, term])
    query = f"""
            SELECT t.id, t.invoice_date, t.serial, t.invoice_number, t.seller_name, t.seller_tax_code,
                   t.buyer_name, t.subtotal, t.vat_percent, t.vat_amount, t.total, t.source_filename,
                   t.items, t.vehicle_id, v.plate_number AS vehicle_plate,
                   COALESCE(t.tk_no_cost, '154') as tk_no_cost,
                   COALESCE(t.tk_no_vat, '1331') as tk_no_vat,
                   COALESCE(t.tk_co_pay, '331') as tk_co_pay
            FROM public.transport_input_invoices t
            LEFT JOIN public.plate_whitelist v ON v.id = t.vehicle_id
            WHERE t.invoice_date >= %s AND t.invoice_date < %s{buyer_filter}{search_filter}
            ORDER BY t.invoice_date DESC, COALESCE(NULLIF(t.seller_tax_code, ''), t.seller_name),
                     NULLIF(regexp_replace(t.invoice_number, '[^0-9]', '', 'g'), '')::numeric ASC NULLS LAST,
                     t.invoice_number, t.id
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

    desc = invoice_payment_description(items, inv_date, inv_num) or desc

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


def book_electricity_invoices(buyer_company_id: int) -> dict:
    if not buyer_company_id:
        return {"ok": False, "message": "Chưa chọn công ty làm việc.", "updated": 0, "ids": []}

    rows = db._execute_query(
        """
        SELECT id, items
        FROM public.transport_input_invoices
        WHERE buyer_company_id = %s
          AND COALESCE(NULLIF(TRIM(tk_no_cost), ''), '154') = '154'
        ORDER BY id
        """,
        (buyer_company_id,),
        fetchall=True,
        commit=False,
    ) or []

    selected_ids = []
    for r in rows:
        raw_items = r.get("items")
        if isinstance(raw_items, str):
            try:
                items = json.loads(raw_items)
            except Exception:
                continue
        elif isinstance(raw_items, list):
            items = raw_items
        else:
            continue

        descriptions = [
            str(it.get("description") or "")
            for it in items
            if isinstance(it, dict) and it.get("description")
        ]

        if any(is_electricity_text(d) for d in descriptions) and suggest_cost_account(descriptions) == "642":
            selected_ids.append(r["id"])

    if selected_ids:
        with db._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE public.transport_input_invoices SET tk_no_cost = '642', updated_at = now() WHERE id = ANY(%s)",
                    (selected_ids,),
                )
            conn.commit()

        for inv_id in selected_ids:
            sync_invoice_to_cash_book(inv_id)

    # Hoá đơn tiền điện còn ghi công nợ 331 mà chưa có chứng từ trả tiền cho người bán: mặc định trả bằng tiền mặt
    cash_ids = []
    unpaid = db._execute_query(
        """
        SELECT i.id, i.items
        FROM public.transport_input_invoices i
        WHERE i.buyer_company_id = %s
          AND COALESCE(NULLIF(TRIM(i.tk_co_pay), ''), '331') LIKE '331%%'
          AND NOT EXISTS (
            SELECT 1 FROM public.transport_cash_book c
            WHERE c.company_id = i.buyer_company_id
              AND c.voucher_type = 'payment'
              AND COALESCE(c.source_type, '') <> 'input_invoice'
              AND ((i.seller_tax_code <> '' AND c.partner_code = i.seller_tax_code)
                   OR (i.seller_name <> '' AND c.partner_name = i.seller_name))
          )
        ORDER BY i.id
        """,
        (buyer_company_id,),
        fetchall=True,
        commit=False,
    ) or []
    for r in unpaid:
        raw_items = r.get("items")
        if isinstance(raw_items, str):
            try:
                raw_items = json.loads(raw_items)
            except Exception:
                continue
        descriptions = [
            str(it.get("description") or "")
            for it in (raw_items if isinstance(raw_items, list) else [])
            if isinstance(it, dict) and it.get("description")
        ]
        if any(is_electricity_text(d) for d in descriptions):
            cash_ids.append(r["id"])
    if cash_ids:
        db._execute_query(
            "UPDATE public.transport_input_invoices SET tk_co_pay = '1111', updated_at = now() WHERE id = ANY(%s)",
            (cash_ids,), commit=True,
        )
        for inv_id in cash_ids:
            sync_invoice_to_cash_book(inv_id)

    vouchers = db._execute_query(
        """
        SELECT c.id AS cash_id, c.description, i.invoice_date, i.invoice_number, i.items
        FROM public.transport_input_invoices i
        JOIN public.transport_cash_book c
          ON c.source_type = 'input_invoice' AND c.source_id = i.id::text AND c.company_id = i.buyer_company_id
        WHERE i.buyer_company_id = %s
        """,
        (buyer_company_id,),
        fetchall=True,
        commit=False,
    ) or []
    renamed = 0
    for v in vouchers:
        wanted = electricity_payment_description(v.get("items"), v.get("invoice_date"), v.get("invoice_number"))
        if wanted and wanted != v.get("description"):
            db._execute_query(
                "UPDATE public.transport_cash_book SET description = %s WHERE id = %s",
                (wanted, v["cash_id"]), commit=True,
            )
            renamed += 1

    all_ids = list(dict.fromkeys([*selected_ids, *cash_ids]))
    return {"ok": True, "updated": len(all_ids), "ids": all_ids, "renamed": renamed}


