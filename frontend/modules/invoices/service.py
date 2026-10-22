"""Persistence and validation for company purchase invoices."""

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json

from psycopg2 import Binary

from backend.services.database import db


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
    values["payment_method"] = _text(form, "payment_method", "hình thức thanh toán", 30)
    if values["payment_method"] not in {"", "Chuyển khoản", "Tiền mặt", "Khác"}:
        raise ValueError("Hình thức thanh toán không hợp lệ.")
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
    vat_percent = money(form.get("vat_percent"), "Thuế suất VAT", maximum=Decimal("100"))
    vat_amount = (subtotal * vat_percent / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    values.update(items=items, subtotal=subtotal, vat_percent=vat_percent,
                  vat_amount=vat_amount, total=subtotal + vat_amount)
    return values


_FIELDS = ("invoice_date", "serial", "invoice_number", "seller_company_id", "seller_name", "seller_tax_code",
           "seller_address", "buyer_name", "buyer_tax_code", "buyer_address",
           "payment_method", "items", "subtotal", "vat_percent", "vat_amount", "total", "note", "buyer_company_id")


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
            return row[0]


def invoice_list_stats(start, end, buyer_company_id=None) -> dict:
    params = [start, end]
    buyer_filter = ""
    if buyer_company_id:
        buyer_filter = " AND (buyer_company_id = %s OR buyer_company_id IS NULL)"
        params.append(buyer_company_id)
    return db._execute_query(
        f"""SELECT COUNT(*) AS total, COALESCE(SUM(total), 0) AS total_amount
              FROM public.transport_input_invoices
             WHERE invoice_date >= %s AND invoice_date < %s{buyer_filter}""",
        tuple(params), fetch=True, commit=False,
    ) or {"total": 0, "total_amount": 0}


def list_invoices(start, end, buyer_company_id=None, limit=None, offset=None):
    params = [start, end]
    buyer_filter = ""
    if buyer_company_id:
        buyer_filter = " AND (buyer_company_id = %s OR buyer_company_id IS NULL)"
        params.append(buyer_company_id)
    query = f"""
            SELECT id, invoice_date, serial, invoice_number, seller_name, seller_tax_code,
                   buyer_name, subtotal, vat_percent, vat_amount, total, source_filename
            FROM public.transport_input_invoices
            WHERE invoice_date >= %s AND invoice_date < %s{buyer_filter}
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
