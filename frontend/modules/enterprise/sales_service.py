"""Company-scoped sales history and invoice data."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import json
import re
import unicodedata

from backend.services.database import db
from psycopg2.extras import RealDictCursor


def get_or_create_customer(
    company_id: int,
    name: str,
    phone: str = "",
    address: str = "",
    user_id: int | None = None,
) -> dict | None:
    clean_name = (name or "").strip()
    if not clean_name:
        return None

    existing = db._execute_query(
        """
        SELECT id, customer_code, full_name, phone, address
        FROM public.personal_debt_customers
        WHERE company_id = %s AND (LOWER(TRIM(full_name)) = LOWER(TRIM(%s)) OR LOWER(TRIM(customer_code)) = LOWER(TRIM(%s)))
        ORDER BY id ASC
        LIMIT 1
        """,
        (company_id, clean_name, clean_name),
        fetch=True, commit=False
    )
    if existing:
        updates = []
        params = []
        if phone and not existing.get("phone"):
            updates.append("phone = %s")
            params.append(phone[:50])
        if address and not existing.get("address"):
            updates.append("address = %s")
            params.append(address[:500])
        if updates:
            params.extend([company_id, existing["id"]])
            db._execute_query(
                f"UPDATE public.personal_debt_customers SET {', '.join(updates)}, updated_at = NOW() WHERE company_id = %s AND id = %s",
                tuple(params), commit=True
            )
            existing["phone"] = phone if phone and not existing.get("phone") else existing.get("phone")
            existing["address"] = address if address and not existing.get("address") else existing.get("address")
        return existing

    s = unicodedata.normalize('NFKD', clean_name).encode('ASCII', 'ignore').decode('utf-8')
    s = re.sub(r'[^a-zA-Z0-9]+', '', s).lower()
    base_code = s[:30] if s else "kh"
    code = base_code
    idx = 1
    while True:
        taken = db._execute_query(
            "SELECT 1 FROM public.personal_debt_customers WHERE company_id = %s AND LOWER(customer_code) = LOWER(%s)",
            (company_id, code), fetch=True, commit=False
        )
        if not taken:
            break
        idx += 1
        code = f"{base_code}_{idx}"

    new_cust = db._execute_query(
        """
        INSERT INTO public.personal_debt_customers
            (company_id, customer_code, full_name, phone, address, opening_balance, created_by)
        VALUES (%s, %s, %s, %s, %s, 0, %s)
        RETURNING id, customer_code, full_name, phone, address
        """,
        (company_id, code, clean_name[:255], (phone or "")[:50], (address or "")[:500], user_id),
        fetch=True, commit=True
    )
    return new_cust


def _parse_math_decimal(val) -> Decimal | None:
    if val in (None, ""):
        return None
    s = str(val).strip().replace(" ", "")
    import re
    # Strip Vietnamese thousands separator dots (e.g. 1.111 -> 1111)
    s = re.sub(r"\.(?=\d{3}(?:\.|$|,|\s|[+\-*\/]))", "", s)
    s = s.replace(",", ".")
    try:
        return Decimal(s)
    except Exception:
        pass
    try:
        if re.match(r"^[0-9+\-*\/().]+$", s):
            res = eval(s, {"__builtins__": None}, {})
            if isinstance(res, (int, float)):
                return Decimal(str(round(float(res), 3)))
    except Exception:
        pass
    return None


def normalize_items(items: object, allow_unpriced: bool = False) -> tuple[list[dict] | None, str | None]:
    if not isinstance(items, list) or not items:
        return None, "Hóa đơn cần có ít nhất một mặt hàng."

    normalized: list[dict] = []
    try:
        for row in items:
            if not isinstance(row, dict):
                raise ValueError
            name = str(row.get("product_name") or "").strip()
            raw_quantity = row.get("quantity")
            raw_unit_price = row.get("unit_price")
            raw_line_total = row.get("line_total")
            raw_gross = row.get("gross_weight")
            raw_tare = row.get("tare_weight")

            # Skip completely empty rows
            if not name and not raw_quantity and not raw_unit_price and not raw_line_total and not raw_gross:
                continue

            if not name:
                return None, "Tên mặt hàng là bắt buộc."

            unit = str(row.get("unit") or "").strip()
            weigh_code = str(row.get("weigh_code") or "").strip()[:100]

            gross_weight = None
            if raw_gross not in (None, ""):
                gross_weight = _parse_math_decimal(raw_gross)
                if gross_weight is None or not gross_weight.is_finite() or gross_weight < 0:
                    return None, "Cân tổng không hợp lệ."

            tare_weight = None
            if raw_tare not in (None, ""):
                tare_weight = _parse_math_decimal(raw_tare)
                if tare_weight is None or not tare_weight.is_finite() or tare_weight < 0:
                    return None, "Bì không hợp lệ."

            custom_total = None
            if raw_line_total not in (None, ""):
                try:
                    custom_total = Decimal(str(raw_line_total))
                except Exception:
                    custom_total = None

            parsed_qty = _parse_math_decimal(raw_quantity)
            if parsed_qty is not None and parsed_qty > 0:
                quantity = parsed_qty
            elif gross_weight is not None and (gross_weight - (tare_weight or Decimal("0"))) > 0:
                quantity = gross_weight - (tare_weight or Decimal("0"))
            else:
                quantity = Decimal("0")

            unit_price = Decimal(str(row.get("unit_price") or "0"))
            if quantity > 0 and unit_price <= 0 and custom_total is not None and custom_total > 0:
                unit_price = (custom_total / quantity).quantize(Decimal("0.01"))
            elif quantity <= 0:
                # Retain positive unit_price if provided (e.g. advance orders with agreed pricing before weighing)
                if not unit_price.is_finite() or unit_price < 0:
                    unit_price = Decimal("0")

            if custom_total is not None and custom_total >= 0:
                line_total = (custom_total / Decimal("1000")).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                ) * Decimal("1000")
            elif quantity > 0 and unit_price > 0:
                raw_total = quantity * unit_price
                line_total = (raw_total / Decimal("1000")).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                ) * Decimal("1000")
            else:
                line_total = Decimal("0")

            if not name:
                return None, "Tên mặt hàng/khoản phát sinh là bắt buộc."
            is_order_item = allow_unpriced and line_total == 0
            has_positive_amount = (
                (custom_total is not None and custom_total > 0)
                or (quantity > 0 and unit_price > 0)
                or (allow_unpriced and unit_price > 0)
            )
            if line_total <= 0 and not is_order_item and not has_positive_amount:
                return None, f"Mục '{name}' cần có số lượng và đơn giá, hoặc thành tiền lớn hơn 0."
            if not quantity.is_finite() or quantity < 0:
                return None, "Số lượng không hợp lệ."
            if not unit_price.is_finite() or unit_price < 0:
                return None, "Đơn giá không hợp lệ."
            if quantity.quantize(Decimal("0.001")) != quantity or unit_price.quantize(Decimal("0.01")) != unit_price:
                return None, "Số lượng tối đa 3 chữ số thập phân, đơn giá tối đa 2 chữ số thập phân."

            gross_formula = str(row.get("gross_formula") or "").strip()
            tare_formula = str(row.get("tare_formula") or "").strip()
            qty_formula = str(row.get("qty_formula") or "").strip()

            normalized.append({
                "product_name": name[:255],
                "unit": unit[:80] if quantity > 0 else (unit[:80] or ""),
                "weigh_code": weigh_code,
                "gross_weight": gross_weight,
                "tare_weight": tare_weight,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
                "gross_formula": gross_formula,
                "tare_formula": tare_formula,
                "qty_formula": qty_formula,
            })
        if not normalized:
            return None, "Hóa đơn cần ít nhất một mặt hàng."
    except (ArithmeticError, ValueError, TypeError):
        return None, "Số lượng hoặc đơn giá không hợp lệ."
    return normalized, None


def get_next_sale_stt(company_id: int) -> str:
    """Calculate the next sequential STT (numeric invoice number) for the company."""
    res = db._execute_query(
        """
        SELECT COALESCE(
            (
                SELECT MAX(CAST(inv AS BIGINT))
                FROM (
                    SELECT COALESCE(NULLIF(source_invoice_number, ''), invoice_number, '') AS inv
                    FROM public.personal_sales
                    WHERE company_id = %s
                ) sub
                WHERE inv ~ '^[0-9]+$'
            ),
            (
                SELECT COUNT(*)
                FROM public.personal_sales
                WHERE company_id = %s
                  AND NULLIF(COALESCE(NULLIF(source_invoice_number, ''), invoice_number, ''), '') IS NOT NULL
            )
        ) AS max_stt
        """,
        (company_id, company_id),
        fetch=True,
        commit=False,
    )
    max_val = int(res["max_stt"]) if res and res.get("max_stt") is not None else 0
    return str(max_val + 1)


def create_sale(company_id: int, values: dict, user_id: int | None) -> dict | None:
    items = values["items"]
    raw_invoice_number = (values.get("invoice_number") or "").strip()
    status = (values.get("status") or "").strip()
    if not status:
        status = "pending" if not raw_invoice_number else "delivered"
    if status in ("pending", "delivering"):
        raw_invoice_number = ""
    elif not raw_invoice_number:
        raw_invoice_number = get_next_sale_stt(company_id)
    invoice_number = raw_invoice_number
    lines_json = json.dumps([
        {
            "product_name": row["product_name"],
            "unit": row["unit"],
            "weigh_code": row.get("weigh_code") or "",
            "gross_weight": str(row["gross_weight"]) if row.get("gross_weight") is not None else None,
            "tare_weight": str(row["tare_weight"]) if row.get("tare_weight") is not None else None,
            "quantity": str(row["quantity"]),
            "unit_price": str(row["unit_price"]),
            "line_total": str(row["line_total"]),
            "gross_formula": str(row.get("gross_formula") or ""),
            "tare_formula": str(row.get("tare_formula") or ""),
            "qty_formula": str(row.get("qty_formula") or ""),
        }
        for row in items
    ], ensure_ascii=False)

    return db._execute_query(
        """
        WITH sale AS (
            INSERT INTO public.personal_sales (
                company_id, customer_id, invoice_number, sale_date,
                customer_code, customer_name, customer_phone, customer_address, carrier, note,
                total_amount, paid_on_sale, payment_source, created_by,
                source_invoice_number, source_key, status
            )
            SELECT c.company_id, c.id,
                   %s, %s::date, c.customer_code, c.full_name, %s, COALESCE(NULLIF(%s, ''), c.address), %s, %s,
                   %s, %s, %s, %s, %s, NULL, %s
            FROM public.personal_debt_customers c
            WHERE c.company_id = %s AND c.id = %s
            RETURNING id, company_id, customer_id, invoice_number, sale_date,
                      total_amount, paid_on_sale, payment_source, status
        ), inserted_items AS (
            INSERT INTO public.personal_sale_items
                (sale_id, product_name, unit, weigh_code, gross_weight, tare_weight, quantity, unit_price, line_total, gross_formula, tare_formula, qty_formula)
            SELECT s.id, x.product_name, x.unit, COALESCE(x.weigh_code, ''), x.gross_weight, x.tare_weight, x.quantity, x.unit_price, x.line_total,
                   COALESCE(x.gross_formula, ''), COALESCE(x.tare_formula, ''), COALESCE(x.qty_formula, '')
            FROM sale s
            CROSS JOIN jsonb_to_recordset(%s::jsonb) AS x(
                product_name TEXT, unit TEXT, weigh_code TEXT,
                gross_weight NUMERIC, tare_weight NUMERIC,
                quantity NUMERIC, unit_price NUMERIC, line_total NUMERIC,
                gross_formula TEXT, tare_formula TEXT, qty_formula TEXT
            )
            RETURNING id
        ), debt_entry AS (
            INSERT INTO public.personal_debt_logs (
                company_id, customer_id, sale_id, invoice_number, transaction_date,
                amount_due, amount_paid, received_from, note, created_by
            )
            SELECT s.company_id, s.customer_id, s.id, COALESCE(s.invoice_number, ''), s.sale_date,
                   s.total_amount, s.paid_on_sale, s.payment_source, %s, %s
            FROM sale s
            RETURNING id
        )
        SELECT id, invoice_number, sale_date, total_amount, paid_on_sale
        FROM sale
        """,
        (
            invoice_number, values["sale_date"], values["customer_phone"],
            values.get("customer_address", ""), values["carrier"], values["note"],
            values["total_amount"], values["paid_amount"], values["payment_source"],
            user_id, invoice_number, status,
            company_id, values["customer_id"], lines_json, values["note"], user_id,
        ),
        fetch=True,
        commit=True,
    )


def _sales_filters(company_id: int, q: str = "", date_from=None, date_to=None, status: str = "") -> tuple[str, list]:
    term = (q or "").strip()
    filters = ["s.company_id = %s"]
    params: list = [company_id]
    if term:
        wildcard = f"%{term}%"
        filters.append("(s.invoice_number ILIKE %s OR s.source_invoice_number ILIKE %s OR s.customer_name ILIKE %s OR s.customer_phone ILIKE %s OR s.note ILIKE %s)")
        params.extend([wildcard] * 5)
    if date_from:
        filters.append("s.sale_date >= %s")
        params.append(date_from)
    if date_to:
        filters.append("s.sale_date <= %s")
        params.append(date_to)
    if status:
        filters.append("s.status = %s")
        params.append(status.strip())
    return " AND ".join(filters), params


def count_sales(company_id: int, q: str = "", date_from=None, date_to=None, status: str = "") -> dict:
    where, params = _sales_filters(company_id, q, date_from, date_to, status=status)
    return db._execute_query(
        f"""SELECT COUNT(*) AS total,
                   COALESCE(SUM(s.total_amount), 0) AS total_amount,
                   COALESCE(SUM(COALESCE(payments.paid_total, s.paid_on_sale)), 0) AS paid_amount
              FROM public.personal_sales s
              LEFT JOIN LATERAL (
                  SELECT SUM(l.amount_paid) AS paid_total
                    FROM public.personal_debt_logs l
                   WHERE (l.sale_id IS NOT NULL AND l.sale_id = s.id)
                      OR (l.sale_id IS NULL AND l.company_id = s.company_id AND l.customer_id = s.customer_id
                          AND NULLIF(s.invoice_number, '') IS NOT NULL AND l.invoice_number = s.invoice_number)
              ) payments ON TRUE
             WHERE {where}""",
        tuple(params), fetch=True, commit=False,
    ) or {"total": 0, "total_amount": 0, "paid_amount": 0}


def list_sales(company_id: int, q: str = "", date_from=None, date_to=None, status: str = "",
               limit: int | None = None, offset: int | None = None) -> list[dict]:
    where, params = _sales_filters(company_id, q, date_from, date_to, status=status)

    query = f"""
        SELECT s.id, s.invoice_number,
               COALESCE(NULLIF(s.source_invoice_number, ''), s.invoice_number) AS display_invoice_number,
               s.sale_date, s.customer_name, s.customer_phone,
               s.customer_code, s.carrier, s.total_amount, s.paid_on_sale, s.payment_source, s.note,
               COALESCE(s.status, 'delivered') AS status,
               COALESCE(items.item_count, 0)::INTEGER AS item_count,
               COALESCE(payments.paid_total, s.paid_on_sale) AS paid_total
        FROM public.personal_sales s
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS item_count
            FROM public.personal_sale_items i WHERE i.sale_id = s.id
        ) items ON TRUE
        LEFT JOIN LATERAL (
            SELECT SUM(l.amount_paid) AS paid_total
            FROM public.personal_debt_logs l
            WHERE (l.sale_id IS NOT NULL AND l.sale_id = s.id)
               OR (l.sale_id IS NULL AND l.company_id = s.company_id AND l.customer_id = s.customer_id
                   AND NULLIF(s.invoice_number, '') IS NOT NULL AND l.invoice_number = s.invoice_number)
        ) payments ON TRUE
        WHERE {where}
        ORDER BY
            CASE
                WHEN COALESCE(s.status, 'delivered') = 'delivering' THEN 0
                WHEN COALESCE(s.status, 'delivered') = 'pending' THEN 1
                WHEN COALESCE(s.status, 'delivered') = 'delivered' THEN 2
                ELSE 3
            END,
            CASE
                WHEN COALESCE(NULLIF(s.source_invoice_number, ''), s.invoice_number, '') ~ '^[0-9]+$'
                THEN COALESCE(NULLIF(s.source_invoice_number, ''), s.invoice_number, '')::NUMERIC
                ELSE NULL
            END DESC NULLS LAST,
            s.sale_date DESC,
            s.id DESC
    """
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)

    return db._execute_query(
        query,
        tuple(params),
        fetchall=True,
        commit=False,
    ) or []


def get_sale(company_id: int, sale_id: int) -> tuple[dict | None, list[dict]]:
    sale = db._execute_query(
        """
        SELECT s.*, c.name AS seller_name, c.address AS seller_address,
               c.tax_code, c.phone AS seller_phone, c.signature_url, c.stamp_url,
               COALESCE(payments.paid_total, s.paid_on_sale) AS paid_total
        FROM public.personal_sales s
        JOIN public.invoice_companies c ON c.id = s.company_id
        LEFT JOIN LATERAL (
            SELECT SUM(l.amount_paid) AS paid_total
            FROM public.personal_debt_logs l
            WHERE (l.sale_id IS NOT NULL AND l.sale_id = s.id)
               OR (l.sale_id IS NULL AND l.company_id = s.company_id AND l.customer_id = s.customer_id
                   AND NULLIF(s.invoice_number, '') IS NOT NULL AND l.invoice_number = s.invoice_number)
        ) payments ON TRUE
        WHERE s.company_id = %s AND s.id = %s
        """,
        (company_id, sale_id),
        fetch=True,
        commit=False,
    )
    if not sale:
        return None, []
    items = db._execute_query(
        """
        SELECT id, product_name, unit, weigh_code, gross_weight, tare_weight, quantity, unit_price, line_total,
               COALESCE(gross_formula, '') AS gross_formula,
               COALESCE(tare_formula, '') AS tare_formula,
               COALESCE(qty_formula, '') AS qty_formula
        FROM public.personal_sale_items WHERE sale_id = %s ORDER BY id
        """,
        (sale_id,),
        fetchall=True,
        commit=False,
    ) or []
    return sale, items


def update_sale(company_id: int, sale_id: int, values: dict) -> bool:
    """Update a sale and its original debt entry without changing its customer or payments."""
    items = values["items"]
    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """SELECT company_id, customer_id, invoice_number
                     FROM public.personal_sales
                    WHERE company_id = %s AND id = %s
                    FOR UPDATE""",
                (company_id, sale_id),
            )
            sale = cursor.fetchone()
            if not sale:
                return False

            old_invoice_number = sale.get("invoice_number") or ""
            old_source = sale.get("source_invoice_number") or old_invoice_number
            new_invoice_number = (values.get("invoice_number") or "").strip()
            new_status = values.get("status") or sale.get("status") or ("pending" if not new_invoice_number else "delivered")
            if new_status in ("pending", "delivering"):
                new_invoice_number = ""
            elif not new_invoice_number:
                new_invoice_number = old_invoice_number or get_next_sale_stt(company_id)

            update_cols = [
                "invoice_number = %s", "source_invoice_number = %s",
                "sale_date = %s", "customer_phone = %s", "customer_address = %s",
                "carrier = %s", "note = %s", "total_amount = %s", "status = %s"
            ]
            sale_params = [
                new_invoice_number, new_invoice_number,
                values["sale_date"], values["customer_phone"],
                values["customer_address"], values["carrier"],
                values["note"], values["total_amount"], new_status
            ]
            if "paid_amount" in values:
                update_cols.extend(["paid_on_sale = %s", "payment_source = %s"])
                sale_params.extend([values["paid_amount"], values["payment_source"]])
            sale_params.extend([company_id, sale_id])

            cursor.execute(
                f"""UPDATE public.personal_sales
                      SET {', '.join(update_cols)}
                    WHERE company_id = %s AND id = %s""",
                tuple(sale_params),
            )
            cursor.execute("DELETE FROM public.personal_sale_items WHERE sale_id = %s", (sale_id,))
            cursor.executemany(
                """INSERT INTO public.personal_sale_items
                       (sale_id, product_name, unit, weigh_code, gross_weight, tare_weight, quantity, unit_price, line_total, gross_formula, tare_formula, qty_formula)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [
                    (sale_id, item["product_name"], item["unit"],
                     item.get("weigh_code") or "", item.get("gross_weight"), item.get("tare_weight"),
                     item["quantity"], item["unit_price"], item["line_total"],
                     item.get("gross_formula") or "", item.get("tare_formula") or "", item.get("qty_formula") or "")
                    for item in items
                ],
            )

            cursor.execute(
                """SELECT id FROM public.personal_debt_logs
                    WHERE (sale_id = %s)
                       OR (company_id = %s AND customer_id = %s AND (invoice_number = %s OR invoice_number = %s) AND amount_due > 0)
                    ORDER BY id LIMIT 1 FOR UPDATE""",
                (sale_id, company_id, sale["customer_id"], old_invoice_number, old_source),
            )
            debt_entry = cursor.fetchone()
            if debt_entry:
                debt_cols = [
                    "sale_id = %s", "invoice_number = %s", "transaction_date = %s",
                    "amount_due = %s", "note = %s"
                ]
                debt_params = [
                    sale_id, new_invoice_number, values["sale_date"],
                    values["total_amount"], values["note"]
                ]
                if "paid_amount" in values:
                    debt_cols.extend(["amount_paid = %s", "received_from = %s"])
                    debt_params.extend([values["paid_amount"], values["payment_source"]])
                debt_params.extend([company_id, debt_entry["id"]])
                cursor.execute(
                    f"""UPDATE public.personal_debt_logs
                          SET {', '.join(debt_cols)}
                        WHERE company_id = %s AND id = %s""",
                    tuple(debt_params),
                )
            elif "paid_amount" in values:
                cursor.execute(
                    """INSERT INTO public.personal_debt_logs (
                        company_id, customer_id, sale_id, invoice_number, transaction_date,
                        amount_due, amount_paid, received_from, note
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        company_id, sale["customer_id"], sale_id, new_invoice_number, values["sale_date"],
                        values["total_amount"], values["paid_amount"], values["payment_source"], values["note"],
                    ),
                )
            if new_invoice_number != old_invoice_number:
                cursor.execute(
                    """UPDATE public.personal_debt_logs
                          SET invoice_number = %s
                        WHERE (sale_id = %s)
                           OR (company_id = %s AND customer_id = %s AND NULLIF(invoice_number, '') IS NOT NULL AND invoice_number = %s)""",
                    (new_invoice_number, sale_id, company_id, sale["customer_id"], old_invoice_number),
                )
        conn.commit()
    return True


def update_sale_status(company_id: int, sale_id: int, status: str, invoice_number: str | None = None) -> bool:
    clean_status = (status or "").strip()
    if clean_status not in ("delivered", "pending", "delivering", "cancelled"):
        raise ValueError("Trạng thái không hợp lệ.")
    
    no_stt_status = clean_status in ("pending", "delivering")
    clean_inv = "" if no_stt_status else (invoice_number.strip() if invoice_number is not None else None)
    if not no_stt_status and clean_inv is not None and not clean_inv:
        clean_inv = get_next_sale_stt(company_id)

    old_sale = None
    if no_stt_status:
        old_sale = db._execute_query(
            "SELECT customer_id, invoice_number FROM public.personal_sales WHERE company_id = %s AND id = %s",
            (company_id, sale_id), fetch=True, commit=False
        )
        if not old_sale:
            return False

    if clean_inv is not None:
        res = db._execute_query(
            """UPDATE public.personal_sales
                  SET status = %s,
                      invoice_number = %s,
                      source_invoice_number = %s,
                      updated_at = NOW()
                WHERE company_id = %s AND id = %s
            RETURNING id, invoice_number""",
            (clean_status, clean_inv, clean_inv, company_id, sale_id), fetch=True, commit=True
        )
        if clean_inv:
            db._execute_query(
                """UPDATE public.personal_debt_logs
                      SET invoice_number = %s
                    WHERE (sale_id = %s) OR (company_id = %s AND sale_id IS NULL AND invoice_number = '')""",
                (clean_inv, sale_id, company_id), commit=True
            )
        elif no_stt_status:
            old_invoice = str(old_sale.get("invoice_number") or "").strip()
            db._execute_query(
                """UPDATE public.personal_debt_logs SET invoice_number = ''
                    WHERE sale_id = %s
                       OR (%s <> '' AND company_id = %s AND customer_id = %s AND invoice_number = %s)""",
                (sale_id, old_invoice, company_id, old_sale["customer_id"], old_invoice), commit=True
            )
    else:
        if not no_stt_status:
            curr = db._execute_query(
                "SELECT invoice_number FROM public.personal_sales WHERE company_id = %s AND id = %s",
                (company_id, sale_id), fetch=True, commit=False
            )
            if curr and not (curr.get("invoice_number") or "").strip():
                clean_inv = get_next_sale_stt(company_id)
                res = db._execute_query(
                    """UPDATE public.personal_sales
                          SET status = %s, invoice_number = %s, source_invoice_number = %s, updated_at = NOW()
                        WHERE company_id = %s AND id = %s
                    RETURNING id, invoice_number""",
                    (clean_status, clean_inv, clean_inv, company_id, sale_id), fetch=True, commit=True
                )
                db._execute_query(
                    """UPDATE public.personal_debt_logs
                          SET invoice_number = %s
                        WHERE (sale_id = %s) OR (company_id = %s AND sale_id IS NULL AND invoice_number = '')""",
                    (clean_inv, sale_id, company_id), commit=True
                )
                return bool(res)
        res = db._execute_query(
            """UPDATE public.personal_sales
                  SET status = %s, updated_at = NOW()
                WHERE company_id = %s AND id = %s
            RETURNING id""",
            (clean_status, company_id, sale_id), fetch=True, commit=True
        )
    return bool(res)


def record_payment(company_id: int, sale_id: int, amount: Decimal, source: str, note: str, user_id: int | None) -> bool:
    row = db._execute_query(
        """
        WITH target AS (
            SELECT s.id, s.company_id, s.customer_id, s.invoice_number,
                   s.sale_date, s.total_amount
            FROM public.personal_sales s
            WHERE s.company_id = %s AND s.id = %s
            FOR UPDATE
        ), balance AS (
            SELECT t.*, COALESCE(SUM(l.amount_paid), 0) AS paid_total
            FROM target t
            LEFT JOIN public.personal_debt_logs l
              ON (l.sale_id IS NOT NULL AND l.sale_id = t.id)
              OR (l.sale_id IS NULL AND l.company_id = t.company_id AND l.customer_id = t.customer_id
                  AND NULLIF(t.invoice_number, '') IS NOT NULL AND l.invoice_number = t.invoice_number)
            GROUP BY t.id, t.company_id, t.customer_id, t.invoice_number,
                     t.sale_date, t.total_amount
        )
        INSERT INTO public.personal_debt_logs (
            company_id, customer_id, sale_id, invoice_number, transaction_date,
            amount_due, amount_paid, received_from, note, created_by
        )
        SELECT company_id, customer_id, id, COALESCE(invoice_number, ''), CURRENT_DATE,
               0, %s, %s, %s, %s
        FROM balance
        WHERE %s > 0
        RETURNING id
        """,
        (company_id, sale_id, amount, source, note, user_id, amount),
        fetch=True,
        commit=True,
    )
    return bool(row)


def delete_sale(company_id: int, sale_id: int) -> bool:
    """Delete a sale and its invoice-linked debt/payment rows within its company."""
    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """SELECT company_id, customer_id, invoice_number
                     FROM public.personal_sales
                    WHERE company_id = %s AND id = %s
                    FOR UPDATE""",
                (company_id, sale_id),
            )
            sale = cursor.fetchone()
            if not sale:
                return False
            cursor.execute(
                "DELETE FROM public.personal_sale_items WHERE sale_id = %s",
                (sale_id,),
            )
            cursor.execute(
                """DELETE FROM public.personal_debt_logs
                    WHERE (sale_id = %s)
                       OR (company_id = %s AND customer_id = %s AND NULLIF(invoice_number, '') IS NOT NULL AND invoice_number = %s)""",
                (sale_id, company_id, sale["customer_id"], sale["invoice_number"]),
            )
            cursor.execute(
                "DELETE FROM public.personal_sales WHERE company_id = %s AND id = %s RETURNING id",
                (company_id, sale_id),
            )
            deleted = cursor.fetchone()
        conn.commit()
    return bool(deleted)


def sales_import_preview(company_id: int, plan: dict) -> dict:
    customer_codes = [item["customer_code"] for item in plan["customers"]]
    source_keys = [item["source_key"] for item in plan["sales"]]
    import_keys = [item["import_key"] for item in plan["payments"]]
    existing_customers = []
    if customer_codes:
        existing_customers = db._execute_query(
            "SELECT customer_code FROM public.personal_debt_customers WHERE company_id = %s AND customer_code = ANY(%s)",
            (company_id, customer_codes), fetchall=True, commit=False,
        ) or []
    existing_sales = []
    if source_keys:
        existing_sales = db._execute_query(
            "SELECT source_key FROM public.personal_sales WHERE company_id = %s AND source_key = ANY(%s)",
            (company_id, source_keys), fetchall=True, commit=False,
        ) or []
    existing_payments = []
    if import_keys:
        existing_payments = db._execute_query(
            "SELECT import_key FROM public.personal_debt_logs WHERE company_id = %s AND import_key = ANY(%s)",
            (company_id, import_keys), fetchall=True, commit=False,
        ) or []
    customer_set = {row["customer_code"].casefold() for row in existing_customers}
    sales_set = {row["source_key"] for row in existing_sales}
    payment_set = {row["import_key"] for row in existing_payments}
    return {
        "workbook_count": plan["workbook_count"],
        "customers_total": len(plan["customers"]),
        "customers_new": sum(1 for row in plan["customers"] if row["customer_code"].casefold() not in customer_set),
        "sales_total": len(plan["sales"]),
        "sales_new": sum(1 for row in plan["sales"] if row["source_key"] not in sales_set),
        "sales_duplicate": sum(1 for row in plan["sales"] if row["source_key"] in sales_set),
        "payments_total": len(plan["payments"]),
        "payments_new": sum(1 for row in plan["payments"] if row["import_key"] not in payment_set),
        "payments_duplicate": sum(1 for row in plan["payments"] if row["import_key"] in payment_set),
        "warnings": plan["warnings"][:30],
    }


def list_import_customers(company_id: int) -> list[dict]:
    return db._execute_query(
        """SELECT id, customer_code, full_name, phone, address
           FROM public.personal_debt_customers
           WHERE company_id = %s
           ORDER BY full_name, customer_code""",
        (company_id,), fetchall=True, commit=False,
    ) or []


def import_sales_workbooks(company_id: int, plan: dict, user_id: int | None) -> dict:
    """Import customers, sale rows, and debt/payment ledger atomically."""
    counts = {"customers_added": 0, "sales_added": 0, "sales_skipped": 0,
              "payments_added": 0, "payments_skipped": 0}
    customer_ids: dict[str, int] = {}
    sale_invoice_numbers: dict[str, str] = {}

    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            codes = [row["customer_code"] for row in plan["customers"]]
            if codes:
                cursor.execute(
                    "SELECT id, customer_code FROM public.personal_debt_customers "
                    "WHERE company_id = %s AND customer_code = ANY(%s)",
                    (company_id, codes),
                )
                customer_ids.update({row["customer_code"].casefold(): row["id"] for row in cursor.fetchall()})

            for customer in plan["customers"]:
                key = customer["customer_code"].casefold()
                if key in customer_ids:
                    continue
                cursor.execute(
                    """INSERT INTO public.personal_debt_customers
                        (company_id, customer_code, full_name, phone, address, opening_balance, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (company_id, customer_code) DO NOTHING
                        RETURNING id""",
                    (company_id, customer["customer_code"], customer["full_name"], customer.get("phone", ""),
                     customer.get("address", ""), customer.get("opening_balance", Decimal(0)), user_id),
                )
                inserted = cursor.fetchone()
                if inserted:
                    customer_ids[key] = inserted["id"]
                    counts["customers_added"] += 1
                else:
                    cursor.execute(
                        "SELECT id FROM public.personal_debt_customers WHERE company_id = %s AND customer_code = %s",
                        (company_id, customer["customer_code"]),
                    )
                    existing = cursor.fetchone()
                    if existing:
                        customer_ids[key] = existing["id"]

            sale_keys = [row["source_key"] for row in plan["sales"]]
            if sale_keys:
                cursor.execute(
                    "SELECT source_key, invoice_number FROM public.personal_sales "
                    "WHERE company_id = %s AND source_key = ANY(%s)",
                    (company_id, sale_keys),
                )
                sale_invoice_numbers.update({row["source_key"]: row["invoice_number"] for row in cursor.fetchall()})

            for sale in plan["sales"]:
                customer_id = customer_ids.get(sale["customer_code"].casefold())
                if not customer_id:
                    raise ValueError(f"Không tạo được khách hàng {sale['customer_name']}.")
                existing_invoice = sale_invoice_numbers.get(sale["source_key"])
                if existing_invoice:
                    counts["sales_skipped"] += 1
                    continue
                cursor.execute(
                    """INSERT INTO public.personal_sales (
                        company_id, customer_id, invoice_number, sale_date, customer_code,
                        customer_name, customer_phone, customer_address, carrier, note,
                        total_amount, paid_on_sale, payment_source, created_by,
                        source_invoice_number, source_key
                    ) VALUES (
                        %s, %s, 'BH' || TO_CHAR(%s::date, 'YYYYMM') || '-' ||
                            LPAD(NEXTVAL('public.personal_sale_invoice_seq')::text, 6, '0'),
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) ON CONFLICT (company_id, source_key) WHERE source_key IS NOT NULL
                      DO NOTHING
                    RETURNING id, invoice_number""",
                    (company_id, customer_id, sale["sale_date"], sale["sale_date"],
                     sale["customer_code"], sale["customer_name"], sale.get("customer_phone", ""),
                     sale.get("customer_address", ""), sale.get("carrier", ""), sale.get("note", ""),
                     sale["total_amount"], sale["paid_amount"], sale.get("payment_source", ""),
                     user_id, sale["source_invoice_number"], sale["source_key"]),
                )
                inserted_sale = cursor.fetchone()
                if not inserted_sale:
                    counts["sales_skipped"] += 1
                    continue
                invoice_number = inserted_sale["invoice_number"]
                sale_invoice_numbers[sale["source_key"]] = invoice_number
                item_rows = [
                    (inserted_sale["id"], row["product_name"], row["unit"],
                     row.get("weigh_code") or "", row.get("gross_weight"), row.get("tare_weight"),
                     row["quantity"], row["unit_price"], row["line_total"])
                    for row in sale["items"]
                ]
                cursor.executemany(
                    """INSERT INTO public.personal_sale_items
                        (sale_id, product_name, unit, weigh_code, gross_weight, tare_weight, quantity, unit_price, line_total)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    item_rows,
                )
                cursor.execute(
                    """INSERT INTO public.personal_debt_logs
                        (company_id, customer_id, invoice_number, transaction_date,
                         amount_due, amount_paid, received_from, note, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (company_id, customer_id, invoice_number, sale["sale_date"],
                     sale["total_amount"], sale["paid_amount"], sale.get("payment_source", ""),
                     sale.get("note", ""), user_id),
                )
                counts["sales_added"] += 1

            for payment in plan["payments"]:
                customer_id = customer_ids.get(payment["customer_code"].casefold())
                if not customer_id:
                    raise ValueError(f"Không tạo được khách hàng {payment['customer_name']}.")
                invoice_number = payment["source_invoice_number"]
                sale_key = payment.get("sale_source_key")
                if sale_key:
                    invoice_number = sale_invoice_numbers.get(sale_key, invoice_number)
                cursor.execute(
                    """INSERT INTO public.personal_debt_logs
                        (company_id, customer_id, invoice_number, transaction_date,
                         amount_due, amount_paid, received_from, note, created_by, import_key)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (company_id, import_key) WHERE import_key IS NOT NULL
                          DO UPDATE SET transaction_date = EXCLUDED.transaction_date,
                              amount_due = EXCLUDED.amount_due, amount_paid = EXCLUDED.amount_paid,
                              received_from = EXCLUDED.received_from, note = EXCLUDED.note
                        RETURNING id""",
                    (company_id, customer_id, invoice_number, payment["transaction_date"],
                     Decimal(0) if sale_key else payment["amount_due"], payment["amount_paid"],
                     payment.get("received_from", ""), payment.get("note", ""), user_id,
                     payment["import_key"]),
                )
                if cursor.fetchone():
                    counts["payments_added"] += 1
                else:
                    counts["payments_skipped"] += 1
            conn.commit()

    return counts


def get_sale_previous_debt(company_id: int, customer_id: int | None, sale_date, invoice_number: str) -> tuple[Decimal, date | None]:
    """Calculate the customer's accumulated debt and last transaction date before this sale invoice."""
    if not customer_id:
        return Decimal("0"), None
    current_log = db._execute_query(
        """
        SELECT id FROM public.personal_debt_logs
        WHERE company_id = %s AND customer_id = %s AND invoice_number = %s
        ORDER BY id ASC LIMIT 1
        """,
        (company_id, customer_id, invoice_number),
        fetch=True, commit=False
    )
    if current_log:
        log_id = current_log["id"]
        row = db._execute_query(
            """
            SELECT c.opening_balance + COALESCE(SUM(l.amount_due - l.amount_paid), 0) AS old_debt,
                   MAX(l.transaction_date) AS last_date,
                   c.opening_balance_date
            FROM public.personal_debt_customers c
            LEFT JOIN public.personal_debt_logs l
              ON l.company_id = c.company_id AND l.customer_id = c.id
              AND (
                  l.transaction_date < %s
                  OR (l.transaction_date = %s AND l.id < %s)
              )
            WHERE c.company_id = %s AND c.id = %s
            GROUP BY c.id, c.opening_balance, c.opening_balance_date
            """,
            (sale_date, sale_date, log_id, company_id, customer_id),
            fetch=True, commit=False
        )
    else:
        row = db._execute_query(
            """
            SELECT c.opening_balance + COALESCE(SUM(l.amount_due - l.amount_paid), 0) AS old_debt,
                   MAX(l.transaction_date) AS last_date,
                   c.opening_balance_date
            FROM public.personal_debt_customers c
            LEFT JOIN public.personal_debt_logs l
              ON l.company_id = c.company_id AND l.customer_id = c.id
              AND (
                  l.transaction_date < %s
                  OR (l.transaction_date = %s AND l.invoice_number != %s)
              )
            WHERE c.company_id = %s AND c.id = %s
            GROUP BY c.id, c.opening_balance, c.opening_balance_date
            """,
            (sale_date, sale_date, invoice_number, company_id, customer_id),
            fetch=True, commit=False
        )
    if row and row.get("old_debt") is not None:
        last_dt = row.get("last_date")
        ob_dt = row.get("opening_balance_date")
        effective_date = last_dt or ob_dt
        if last_dt and ob_dt:
            effective_date = max(last_dt, ob_dt)
        return Decimal(str(row["old_debt"])), effective_date
    return Decimal("0"), None


def get_sale_payments(company_id: int, customer_id: int | None, invoice_number: str) -> list[dict]:
    """Retrieve all payments recorded for a sale invoice."""
    if not customer_id or not invoice_number:
        return []
    return db._execute_query(
        """
        SELECT id, transaction_date, amount_paid, received_from, note
        FROM public.personal_debt_logs
        WHERE company_id = %s AND customer_id = %s AND invoice_number = %s AND amount_paid > 0
        ORDER BY transaction_date ASC, id ASC
        """,
        (company_id, customer_id, invoice_number),
        fetchall=True, commit=False
    ) or []


def _sale_items_filters(
    company_id: int,
    q: str = "",
    product_name: str = "",
    customer_id: int | None = None,
    date_from=None,
    date_to=None,
) -> tuple[str, list]:
    filters = ["s.company_id = %s"]
    params: list = [company_id]
    if product_name:
        p_term = f"%{product_name.strip()}%"
        filters.append(
            "(i.product_name ILIKE %s OR s.customer_name ILIKE %s OR s.customer_code ILIKE %s OR s.invoice_number ILIKE %s OR s.source_invoice_number ILIKE %s)"
        )
        params.extend([p_term] * 5)
    if customer_id:
        filters.append("s.customer_id = %s")
        params.append(customer_id)
    if date_from:
        filters.append("s.sale_date >= %s")
        params.append(date_from)
    if date_to:
        filters.append("s.sale_date <= %s")
        params.append(date_to)
    term = (q or "").strip()
    if term:
        wildcard = f"%{term}%"
        filters.append(
            "(i.product_name ILIKE %s OR s.invoice_number ILIKE %s OR s.source_invoice_number ILIKE %s "
            "OR s.customer_name ILIKE %s OR s.customer_code ILIKE %s OR s.carrier ILIKE %s OR s.note ILIKE %s)"
        )
        params.extend([wildcard] * 7)
    return " AND ".join(filters), params


def count_sale_items(
    company_id: int,
    q: str = "",
    product_name: str = "",
    customer_id: int | None = None,
    date_from=None,
    date_to=None,
) -> dict:
    where, params = _sale_items_filters(company_id, q, product_name, customer_id, date_from, date_to)
    return db._execute_query(
        f"""
        SELECT COUNT(*) AS total_items,
               COALESCE(SUM(i.quantity), 0) AS total_quantity,
               COALESCE(SUM(i.line_total), 0) AS total_amount,
               COUNT(DISTINCT i.product_name) AS total_products
        FROM public.personal_sale_items i
        JOIN public.personal_sales s ON s.id = i.sale_id
        WHERE {where}
        """,
        tuple(params),
        fetch=True,
        commit=False,
    ) or {"total_items": 0, "total_quantity": 0, "total_amount": 0, "total_products": 0}


def list_sale_items(
    company_id: int,
    q: str = "",
    product_name: str = "",
    customer_id: int | None = None,
    date_from=None,
    date_to=None,
    limit: int | None = 50,
    offset: int | None = 0,
) -> list[dict]:
    where, params = _sale_items_filters(company_id, q, product_name, customer_id, date_from, date_to)
    query = f"""
        SELECT
            i.id AS item_id,
            s.id AS sale_id,
            s.invoice_number,
            COALESCE(NULLIF(s.source_invoice_number, ''), s.invoice_number) AS display_invoice_number,
            s.sale_date,
            s.customer_id,
            s.customer_code,
            s.customer_name,
            s.customer_phone,
            s.carrier,
            s.note AS sale_note,
            COALESCE(s.status, 'delivered') AS status,
            i.product_name,
            i.unit,
            i.gross_weight,
            i.tare_weight,
            i.quantity,
            i.unit_price,
            i.line_total,
            COALESCE(i.gross_formula, '') AS gross_formula,
            COALESCE(i.tare_formula, '') AS tare_formula,
            COALESCE(i.qty_formula, '') AS qty_formula
        FROM public.personal_sale_items i
        JOIN public.personal_sales s ON s.id = i.sale_id
        WHERE {where}
        ORDER BY
            CASE
                WHEN COALESCE(s.status, 'delivered') = 'delivering' THEN 0
                WHEN COALESCE(s.status, 'delivered') = 'pending' THEN 1
                WHEN COALESCE(s.status, 'delivered') = 'delivered' THEN 2
                ELSE 3
            END,
            s.sale_date DESC, s.id DESC, i.id ASC
    """
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)

    return db._execute_query(
        query,
        tuple(params),
        fetchall=True,
        commit=False,
    ) or []

