"""Supplier management service: Purchase books (Sổ Bích / Sổ Trang-P.A) and Supplier Debts."""

from __future__ import annotations

import ast
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import io
import logging
import operator
import re
from typing import Optional

from backend.services.database import db

logger = logging.getLogger(__name__)

# Safe math operators for formula evaluation
_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def evaluate_qty_formula(raw_val: any) -> tuple[Decimal, str]:
    """
    Safely evaluate math formulas entered for quantity_kg (e.g., '=3820+8655' or '42+1166+478').
    Returns (calculated_qty, formula_string).
    """
    if raw_val is None:
        return Decimal(0), ""

    s = str(raw_val).strip()
    if not s:
        return Decimal(0), ""

    formula_str = ""
    expr = s
    if expr.startswith("="):
        formula_str = expr[1:].strip()
        expr = formula_str

    # Check if there are math operators
    has_operator = any(op in expr for op in ["+", "-", "*", "/"])
    if has_operator:
        if not formula_str:
            formula_str = expr
        # Validate characters: only digits, decimal point, operators, parentheses, spaces
        if not re.match(r"^[\d\.\+\-\*\/\(\)\s,]+$", expr):
            try:
                val = Decimal(re.sub(r"[^\d.]", "", expr) or "0")
                return val, ""
            except Exception:
                return Decimal(0), ""

        # Normalize commas used as decimal or thousand separators if any
        # If pattern is e.g. 1,884kg -> replace comma with dot if decimal
        cleaned_expr = expr.replace(",", ".")

        def _eval_node(node):
            if isinstance(node, ast.Constant):
                if isinstance(node.value, (int, float)):
                    return Decimal(str(node.value))
                raise ValueError("Unsupported constant")
            elif isinstance(node, ast.BinOp):
                left = _eval_node(node.left)
                right = _eval_node(node.right)
                op_type = type(node.op)
                if op_type in _SAFE_OPERATORS:
                    op_func = _SAFE_OPERATORS[op_type]
                    if op_type is ast.Div and right == 0:
                        raise ZeroDivisionError("Division by zero")
                    return op_func(left, right)
                raise ValueError(f"Unsupported operator {op_type}")
            elif isinstance(node, ast.UnaryOp):
                operand = _eval_node(node.operand)
                op_type = type(node.op)
                if op_type in _SAFE_OPERATORS:
                    return _SAFE_OPERATORS[op_type](operand)
                raise ValueError(f"Unsupported unary operator {op_type}")
            raise ValueError(f"Unsupported AST node {type(node)}")

        try:
            tree = ast.parse(cleaned_expr, mode="eval")
            res = _eval_node(tree.body)
            res_dec = Decimal(str(res)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            return res_dec, formula_str
        except Exception as e:
            logger.warning(f"Error evaluating formula '{expr}': {e}")
            try:
                val = Decimal(re.sub(r"[^\d.]", "", expr) or "0")
                return val, formula_str
            except Exception:
                return Decimal(0), formula_str

    # Just a normal number
    try:
        val = Decimal(re.sub(r"[^\d.]", "", expr.replace(",", ".")) or "0")
        return val, ""
    except Exception:
        return Decimal(0), ""


def ensure_supplier_profile(company_id: int, supplier_name: str, phone: str = "", address: str = "", book_type: str = "bich") -> dict:
    """Ensure supplier exists in public.supplier_profiles."""
    clean_name = (supplier_name or "").strip()
    if not clean_name:
        return {}

    existing = db._execute_query(
        """
        SELECT id, company_id, supplier_name, phone, address, tax_code, opening_balance, book_type
        FROM public.supplier_profiles
        WHERE company_id = %s AND LOWER(TRIM(supplier_name)) = LOWER(TRIM(%s))
        LIMIT 1
        """,
        (company_id, clean_name),
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
        if book_type and not existing.get("book_type"):
            updates.append("book_type = %s")
            params.append(book_type)
        if updates:
            params.extend([company_id, existing["id"]])
            db._execute_query(
                f"UPDATE public.supplier_profiles SET {', '.join(updates)}, updated_at = NOW() WHERE company_id = %s AND id = %s",
                tuple(params), commit=True
            )
        return existing

    inserted = db._execute_query(
        """
        INSERT INTO public.supplier_profiles (company_id, supplier_name, phone, address, opening_balance, book_type, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 0, %s, NOW(), NOW())
        RETURNING id, company_id, supplier_name, phone, address, opening_balance, book_type
        """,
        (company_id, clean_name, phone[:50] if phone else None, address[:500] if address else None, book_type or "bich"),
        fetch=True, commit=True
    )
    return inserted or {}


def save_supplier_opening_balance(
    company_id: int,
    supplier_name: str,
    opening_balance: Decimal | float | int | str,
    book_type: str = "bich",
    opening_date: date | str | None = None,
    note: str = "",
) -> dict:
    """Save or update opening debt balance for a supplier (chuyển số dư nợ từ sổ Excel sang)."""
    clean_name = (supplier_name or "").strip()
    if not clean_name:
        raise ValueError("Tên nhà cung cấp không được để trống")

    if isinstance(opening_balance, str):
        # Strip all thousand separators (. or , or spaces) so 2.517.000 or 2,517,000 parses to 2517000
        clean_amt_str = re.sub(r"[^\d-]", "", opening_balance) or "0"
        amt = Decimal(clean_amt_str).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    else:
        try:
            amt = Decimal(str(int(opening_balance or 0))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        except Exception:
            amt = Decimal(0)

    op_date = opening_date or date.today()
    if isinstance(op_date, str) and op_date.strip():
        try:
            op_date = datetime.strptime(op_date.strip()[:10], "%Y-%m-%d").date()
        except Exception:
            op_date = date.today()

    clean_book = (book_type or "bich").strip().lower()
    clean_note = (note or "").strip()

    existing = db._execute_query(
        """
        SELECT id, company_id, supplier_name, phone, address, tax_code, opening_balance, opening_balance_date, note, book_type
        FROM public.supplier_profiles
        WHERE company_id = %s AND LOWER(TRIM(supplier_name)) = LOWER(TRIM(%s))
        LIMIT 1
        """,
        (company_id, clean_name),
        fetch=True, commit=False
    )

    if existing:
        res = db._execute_query(
            """
            UPDATE public.supplier_profiles
            SET opening_balance = %s,
                opening_balance_date = %s,
                note = %s,
                book_type = %s,
                updated_at = NOW()
            WHERE company_id = %s AND id = %s
            RETURNING id, company_id, supplier_name, phone, address, tax_code, opening_balance, opening_balance_date, note, book_type
            """,
            (amt, op_date, clean_note or None, clean_book, company_id, existing["id"]),
            fetch=True, commit=True
        )
        return res or existing
    else:
        res = db._execute_query(
            """
            INSERT INTO public.supplier_profiles (
                company_id, supplier_name, opening_balance, opening_balance_date, note, book_type, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, NOW(), NOW()
            )
            RETURNING id, company_id, supplier_name, phone, address, tax_code, opening_balance, opening_balance_date, note, book_type
            """,
            (company_id, clean_name, amt, op_date, clean_note or None, clean_book),
            fetch=True, commit=True
        )
        return res or {}


def list_purchases(
    company_id: int,
    book_type: Optional[str] = None,
    supplier_name: Optional[str] = None,
    product_name: Optional[str] = None,
    month: Optional[str] = None,
    date_from: Optional[date | str] = None,
    date_to: Optional[date | str] = None,
    q: Optional[str] = None,
    page: int = 1,
    per_page: int = 100,
) -> dict:
    """
    Query purchase history with filters and summary totals.
    """
    where_parts = ["company_id = %s"]
    params: list = [company_id]

    if book_type and book_type.lower() not in ["all", "tat_ca", ""]:
        where_parts.append("LOWER(book_type) = LOWER(%s)")
        params.append(book_type.strip())

    if supplier_name and supplier_name.strip():
        where_parts.append("supplier_name ILIKE %s")
        params.append(f"%{supplier_name.strip()}%")

    if product_name and product_name.strip():
        where_parts.append("product_name ILIKE %s")
        params.append(f"%{product_name.strip()}%")

    if q and q.strip():
        where_parts.append("(supplier_name ILIKE %s OR product_name ILIKE %s OR invoice_number ILIKE %s OR note ILIKE %s)")
        wild = f"%{q.strip()}%"
        params.extend([wild, wild, wild, wild])

    if month and re.match(r"^\d{4}-\d{2}$", month.strip()):
        where_parts.append("TO_CHAR(purchase_date, 'YYYY-MM') = %s")
        params.append(month.strip())
    else:
        if date_from:
            where_parts.append("purchase_date >= %s")
            params.append(date_from)
        if date_to:
            where_parts.append("purchase_date <= %s")
            params.append(date_to)

    where_sql = " AND ".join(where_parts)

    # Summary totals
    totals_query = f"""
        SELECT
            COUNT(*)::INTEGER AS total_rows,
            COALESCE(SUM(pieces), 0) AS total_pieces,
            COALESCE(SUM(quantity_kg), 0) AS total_kg,
            COALESCE(SUM(total_amount), 0) AS total_amount
        FROM public.supplier_purchases
        WHERE {where_sql}
    """
    totals = db._execute_query(totals_query, tuple(params), fetch=True, commit=False) or {
        "total_rows": 0, "total_pieces": 0, "total_kg": 0, "total_amount": 0
    }

    total_rows = totals.get("total_rows", 0)
    limit = max(1, min(per_page, 500))
    offset = max(0, (page - 1) * limit)

    data_query = f"""
        SELECT
            id, company_id, book_type, invoice_number, purchase_date,
            supplier_name, product_name, pieces, unit, quantity_kg, qty_formula,
            unit_price, total_amount, unit_weight, note, created_by, created_at, updated_at
        FROM public.supplier_purchases
        WHERE {where_sql}
        ORDER BY purchase_date DESC, id DESC
        LIMIT %s OFFSET %s
    """
    query_params = list(params) + [limit, offset]
    rows = db._execute_query(data_query, tuple(query_params), fetchall=True, commit=False) or []

    return {
        "items": rows,
        "total_rows": total_rows,
        "page": page,
        "per_page": limit,
        "total_pages": (total_rows + limit - 1) // limit if limit > 0 else 1,
        "total_pieces": float(totals.get("total_pieces", 0)),
        "total_kg": float(totals.get("total_kg", 0)),
        "total_amount": float(totals.get("total_amount", 0)),
    }


def get_purchase(purchase_id: int, company_id: int) -> dict | None:
    return db._execute_query(
        """
        SELECT * FROM public.supplier_purchases
        WHERE id = %s AND company_id = %s
        LIMIT 1
        """,
        (purchase_id, company_id),
        fetch=True, commit=False
    )


def get_next_purchase_stt(company_id: int, book_type: str = "bich") -> str:
    """Calculate the next sequential STT (numeric invoice number) for purchases in the specified book."""
    clean_book = (book_type or "bich").strip().lower()
    res = db._execute_query(
        """
        SELECT COALESCE(
            MAX(
                CAST(
                    NULLIF(REGEXP_REPLACE(invoice_number, '[^0-9]', '', 'g'), '')
                    AS BIGINT
                )
            ),
            0
        ) AS max_num
        FROM public.supplier_purchases
        WHERE company_id = %s AND LOWER(book_type) = LOWER(%s)
        """,
        (company_id, clean_book),
        fetch=True,
        commit=False,
    )
    max_val = int(res["max_num"]) if res and res.get("max_num") is not None else 0
    return str(max_val + 1)


def _create_single_purchase_item(company_id: int, data: dict, created_by: str = "") -> dict:
    """Create a single purchase order line with auto calculation."""
    book_type = (data.get("book_type") or "bich").strip().lower()
    invoice_number = (data.get("invoice_number") or "").strip()
    purchase_date = data.get("purchase_date") or date.today()
    supplier_name = (data.get("supplier_name") or "").strip()
    product_name = (data.get("product_name") or "").strip()
    unit = (data.get("unit") or "Kg").strip()
    note = (data.get("note") or "").strip()

    pieces = None
    if data.get("pieces") is not None and str(data.get("pieces")).strip() != "":
        try:
            pieces = Decimal(str(data.get("pieces")).replace(",", "."))
        except Exception:
            pieces = None

    raw_qty = data.get("qty_formula") or data.get("quantity_kg") or 0
    quantity_kg, formula = evaluate_qty_formula(raw_qty)

    unit_price = Decimal(0)
    if data.get("unit_price") is not None and str(data.get("unit_price")).strip() != "":
        try:
            unit_price = Decimal(re.sub(r"[^\d.]", "", str(data.get("unit_price")).replace(",", ".")) or "0")
        except Exception:
            unit_price = Decimal(0)

    total_amount = (quantity_kg * unit_price).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    unit_weight = None
    if pieces and pieces > 0:
        unit_weight = (quantity_kg / pieces).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    res = db._execute_query(
        """
        INSERT INTO public.supplier_purchases (
            company_id, book_type, invoice_number, purchase_date,
            supplier_name, product_name, pieces, unit, quantity_kg, qty_formula,
            unit_price, total_amount, unit_weight, note, created_by, created_at, updated_at
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, NOW(), NOW()
        ) RETURNING *
        """,
        (
            company_id, book_type, invoice_number, purchase_date,
            supplier_name, product_name, pieces, unit, quantity_kg, formula if formula else None,
            unit_price, total_amount, unit_weight, note, created_by
        ),
        fetch=True, commit=True
    )
    return res or {}


def create_purchase(company_id: int, data: dict, created_by: str = "") -> dict:
    """Create a new purchase order line (or multiple lines for an invoice) with auto calculation."""
    # Check if multiple items are submitted in a batch for the same invoice
    items = data.get("items")
    if items and isinstance(items, list) and len(items) > 0:
        book_type = (data.get("book_type") or "bich").strip().lower()
        invoice_number = (data.get("invoice_number") or "").strip()
        purchase_date = data.get("purchase_date") or date.today()
        supplier_name = (data.get("supplier_name") or "").strip()
        common_note = (data.get("note") or "").strip()

        created_items = []
        for it in items:
            p_name = (it.get("product_name") or "").strip()
            if not p_name and not it.get("quantity_kg") and not it.get("unit_price"):
                continue
            item_data = dict(it)
            item_data["book_type"] = book_type
            item_data["invoice_number"] = invoice_number
            item_data["purchase_date"] = purchase_date
            item_data["supplier_name"] = supplier_name
            if not item_data.get("note") and common_note:
                item_data["note"] = common_note
            single_res = _create_single_purchase_item(company_id, item_data, created_by)
            if single_res:
                created_items.append(single_res)
        return {"items": created_items, "count": len(created_items)}

    # Single item
    return _create_single_purchase_item(company_id, data, created_by)


def update_purchase(purchase_id: int, company_id: int, data: dict) -> dict | None:
    """Update purchase line with formula recalculation."""
    existing = get_purchase(purchase_id, company_id)
    if not existing:
        return None

    book_type = (data.get("book_type") or existing.get("book_type") or "bich").strip().lower()
    invoice_number = (data.get("invoice_number") if "invoice_number" in data else existing.get("invoice_number") or "").strip()
    purchase_date = data.get("purchase_date") or existing.get("purchase_date")
    supplier_name = (data.get("supplier_name") if "supplier_name" in data else existing.get("supplier_name") or "").strip()
    product_name = (data.get("product_name") if "product_name" in data else existing.get("product_name") or "").strip()
    unit = (data.get("unit") if "unit" in data else existing.get("unit") or "Kg").strip()
    note = (data.get("note") if "note" in data else existing.get("note") or "").strip()

    pieces = existing.get("pieces")
    if "pieces" in data:
        if data.get("pieces") is not None and str(data.get("pieces")).strip() != "":
            try:
                pieces = Decimal(str(data.get("pieces")).replace(",", "."))
            except Exception:
                pieces = None
        else:
            pieces = None

    raw_qty = data.get("qty_formula") if "qty_formula" in data else (data.get("quantity_kg") if "quantity_kg" in data else existing.get("qty_formula") or existing.get("quantity_kg"))
    quantity_kg, formula = evaluate_qty_formula(raw_qty)

    unit_price = existing.get("unit_price") or Decimal(0)
    if "unit_price" in data:
        try:
            unit_price = Decimal(re.sub(r"[^\d.]", "", str(data.get("unit_price")).replace(",", ".")) or "0")
        except Exception:
            unit_price = Decimal(0)

    total_amount = (quantity_kg * unit_price).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    unit_weight = None
    if pieces and pieces > 0:
        unit_weight = (quantity_kg / pieces).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    res = db._execute_query(
        """
        UPDATE public.supplier_purchases SET
            book_type = %s,
            invoice_number = %s,
            purchase_date = %s,
            supplier_name = %s,
            product_name = %s,
            pieces = %s,
            unit = %s,
            quantity_kg = %s,
            qty_formula = %s,
            unit_price = %s,
            total_amount = %s,
            unit_weight = %s,
            note = %s,
            updated_at = NOW()
        WHERE id = %s AND company_id = %s
        RETURNING *
        """,
        (
            book_type, invoice_number, purchase_date,
            supplier_name, product_name, pieces, unit,
            quantity_kg, formula if formula else None,
            unit_price, total_amount, unit_weight, note,
            purchase_id, company_id
        ),
        fetch=True, commit=True
    )
    return res


def delete_purchase(purchase_id: int, company_id: int) -> bool:
    res = db._execute_query(
        "DELETE FROM public.supplier_purchases WHERE id = %s AND company_id = %s RETURNING id",
        (purchase_id, company_id),
        fetch=True, commit=True
    )
    return bool(res)


def list_supplier_debts(
    company_id: int,
    book_type: Optional[str] = None,
    month: Optional[str] = None,
    q: Optional[str] = None,
    debt_only: bool = False,
) -> dict:
    """
    Returns supplier debt summary list matching personal debt style (clean table, no cards).
    Can filter by specific book_type ('bich' or 'trang_pa') or all.
    Computes: Total purchases, Total payments, Ending balance (debt).
    """
    where_prof = ["p.company_id = %s"]
    params: list = [company_id]

    if q and q.strip():
        where_prof.append("(p.supplier_name ILIKE %s OR p.phone ILIKE %s OR p.address ILIKE %s)")
        w = f"%{q.strip()}%"
        params.extend([w, w, w])

    prof_where_sql = " AND ".join(where_prof)

    pu_book_sql = ""
    pu_params = [company_id]
    py_book_sql = ""
    py_params = [company_id]
    clean_book = None

    if book_type and book_type.lower() not in ["all", "tat_ca", ""]:
        clean_book = book_type.strip().lower()
        pu_book_sql = " AND LOWER(book_type) = LOWER(%s)"
        pu_params.append(clean_book)
        py_book_sql = " AND LOWER(book_type) = LOWER(%s)"
        py_params.append(clean_book)

    if clean_book:
        opening_sql = "CASE WHEN (p.book_type IS NULL OR LOWER(p.book_type) = LOWER(%s)) THEN COALESCE(p.opening_balance, 0) ELSE 0 END"
        filter_cond_sql = "(COALESCE(pu.total_purchase_all, 0) > 0 OR COALESCE(py.total_paid_all, 0) > 0 OR (COALESCE(p.opening_balance, 0) != 0 AND (p.book_type IS NULL OR LOWER(p.book_type) = LOWER(%s))))"
        select_params = [clean_book, clean_book]
        where_cond_params = [clean_book]
    else:
        opening_sql = "COALESCE(p.opening_balance, 0)"
        filter_cond_sql = "(COALESCE(pu.total_purchase_all, 0) > 0 OR COALESCE(py.total_paid_all, 0) > 0 OR COALESCE(p.opening_balance, 0) != 0)"
        select_params = []
        where_cond_params = []

    # Build query combining supplier_profiles, purchases, payments
    query = f"""
        WITH purchases_agg AS (
            SELECT
                supplier_name,
                COALESCE(SUM(total_amount), 0) AS total_purchase_all,
                COUNT(id) AS purchase_count
            FROM public.supplier_purchases
            WHERE company_id = %s {pu_book_sql}
            GROUP BY supplier_name
        ),
        payments_agg AS (
            SELECT
                supplier_name,
                COALESCE(SUM(amount), 0) AS total_paid_all,
                COUNT(id) AS payment_count,
                MAX(payment_date) AS last_payment_date
            FROM public.supplier_payments
            WHERE company_id = %s {py_book_sql}
            GROUP BY supplier_name
        )
        SELECT
            p.id,
            p.company_id,
            p.supplier_name,
            p.phone,
            p.address,
            p.tax_code,
            p.book_type,
            p.opening_balance_date,
            p.note,
            {opening_sql} AS opening_balance,
            COALESCE(pu.total_purchase_all, 0) AS total_purchased,
            COALESCE(py.total_paid_all, 0) AS total_paid,
            ({opening_sql} + COALESCE(pu.total_purchase_all, 0) - COALESCE(py.total_paid_all, 0)) AS balance_due,
            COALESCE(pu.purchase_count, 0) AS purchase_count,
            COALESCE(py.payment_count, 0) AS payment_count,
            py.last_payment_date
        FROM public.supplier_profiles p
        LEFT JOIN purchases_agg pu ON LOWER(TRIM(pu.supplier_name)) = LOWER(TRIM(p.supplier_name))
        LEFT JOIN payments_agg py ON LOWER(TRIM(py.supplier_name)) = LOWER(TRIM(p.supplier_name))
        WHERE {prof_where_sql}
          AND {filter_cond_sql}
        ORDER BY balance_due DESC, p.supplier_name ASC
    """
    full_params = pu_params + py_params + select_params + params + where_cond_params
    rows = db._execute_query(query, tuple(full_params), fetchall=True, commit=False) or []

    if debt_only:
        rows = [r for r in rows if (r.get("balance_due") or 0) > 0]

    # Calculate aggregates
    total_opening_balance_sum = sum(Decimal(str(r.get("opening_balance") or 0)) for r in rows)
    total_purchased_sum = sum(Decimal(str(r.get("total_purchased") or 0)) for r in rows)
    total_paid_sum = sum(Decimal(str(r.get("total_paid") or 0)) for r in rows)
    total_balance_sum = sum(Decimal(str(r.get("balance_due") or 0)) for r in rows)

    return {
        "items": rows,
        "total_suppliers": len(rows),
        "total_opening_balance": float(total_opening_balance_sum),
        "total_purchased": float(total_purchased_sum),
        "total_paid": float(total_paid_sum),
        "total_balance_due": float(total_balance_sum),
    }


def record_supplier_payment(
    company_id: int,
    supplier_name: str,
    amount: Decimal | float | int,
    book_type: str = "bich",
    payment_date: date | str | None = None,
    invoice_number: str = "",
    payment_method: str = "ck",
    note: str = "",
    created_by: str = "",
) -> dict:
    clean_name = (supplier_name or "").strip()
    if not clean_name:
        raise ValueError("Tên nhà cung cấp không được để trống")

    pay_date = payment_date or date.today()
    amt = Decimal(str(amount or 0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if amt <= 0:
        raise ValueError("Số tiền thanh toán phải lớn hơn 0")

    res = db._execute_query(
        """
        INSERT INTO public.supplier_payments (
            company_id, supplier_name, book_type, payment_date, amount,
            invoice_number, payment_method, note, created_by, created_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, NOW()
        ) RETURNING *
        """,
        (company_id, clean_name, book_type.strip().lower(), pay_date, amt, invoice_number.strip(), payment_method.strip(), note.strip(), created_by),
        fetch=True, commit=True
    )

    if res and company_id == 33 and (book_type or "").lower() != "bich":
        try:
            from frontend.modules.transport.cash_bank_service import record_cash_or_bank_entry
            is_cash = "tiền mặt" in (payment_method or "").lower() or payment_method == "cash"
            b_type = "cash" if is_cash else "bank"
            desc = note.strip() or f"Thanh toán tiền hàng cho {clean_name}"
            if invoice_number.strip():
                desc += f" (HĐ {invoice_number.strip()})"
            record_cash_or_bank_entry(
                company_id=company_id,
                book_type=b_type,
                voucher_type="payment",
                amount=amt,
                voucher_date=pay_date,
                voucher_no="",
                description=desc,
                offset_account="331",
                partner_name=clean_name,
                bank_account_no="" if not is_cash else None,
                bank_name="" if not is_cash else None,
                source_type="supplier_payments",
                source_id=res.get("id"),
                note=f"Thanh toán NCC {clean_name}",
            )
        except Exception as e:
            logger.warning(f"Could not sync supplier payment to cash/bank book: {e}")

    return res or {}


def update_supplier_payment(
    payment_id: int,
    company_id: int,
    data: dict,
) -> dict:
    clean_name = (data.get("supplier_name") or "").strip()
    book_type = (data.get("book_type") or "bich").strip().lower()
    pay_date = data.get("payment_date") or date.today()
    if isinstance(pay_date, str):
        try:
            pay_date = datetime.strptime(pay_date.strip()[:10], "%Y-%m-%d").date()
        except Exception:
            pay_date = date.today()

    amt = parse_decimal(data.get("amount", 0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if amt <= 0:
        raise ValueError("Số tiền thanh toán phải lớn hơn 0")

    res = db._execute_query(
        """
        UPDATE public.supplier_payments
        SET
            supplier_name = COALESCE(NULLIF(%s, ''), supplier_name),
            book_type = COALESCE(NULLIF(%s, ''), book_type),
            payment_date = %s,
            amount = %s,
            invoice_number = %s,
            payment_method = %s,
            note = %s
        WHERE id = %s AND company_id = %s
        RETURNING *
        """,
        (
            clean_name,
            book_type,
            pay_date,
            amt,
            (data.get("invoice_number") or "").strip(),
            (data.get("payment_method") or "Chuyển khoản").strip(),
            (data.get("note") or "").strip(),
            payment_id,
            company_id
        ),
        fetch=True, commit=True
    )
    return res or {}


def delete_supplier_payment(payment_id: int, company_id: int) -> bool:
    res = db._execute_query(
        "DELETE FROM public.supplier_payments WHERE id = %s AND company_id = %s RETURNING id",
        (payment_id, company_id),
        fetch=True, commit=True
    )
    return bool(res)


def get_supplier_statement(company_id: int, supplier_name: str, book_type: Optional[str] = None) -> dict:
    """
    Returns full transaction history (purchases + payments) for a supplier like an order ledger/statement.
    """
    clean_name = (supplier_name or "").strip()
    profile = db._execute_query(
        "SELECT * FROM public.supplier_profiles WHERE company_id = %s AND LOWER(TRIM(supplier_name)) = LOWER(TRIM(%s)) LIMIT 1",
        (company_id, clean_name),
        fetch=True, commit=False
    ) or {"supplier_name": clean_name, "opening_balance": 0}

    pu_where = ["company_id = %s", "LOWER(TRIM(supplier_name)) = LOWER(TRIM(%s))"]
    pu_params = [company_id, clean_name]
    py_where = ["company_id = %s", "LOWER(TRIM(supplier_name)) = LOWER(TRIM(%s))"]
    py_params = [company_id, clean_name]

    if book_type and book_type.lower() not in ["all", "tat_ca", ""]:
        pu_where.append("LOWER(book_type) = LOWER(%s)")
        pu_params.append(book_type.strip())
        py_where.append("LOWER(book_type) = LOWER(%s)")
        py_params.append(book_type.strip())

    purchases = db._execute_query(
        f"""
        SELECT
            id, 'purchase' AS type, purchase_date AS date, invoice_number,
            supplier_name, product_name,
            product_name AS description, pieces, quantity_kg, qty_formula, unit_price, unit_weight,
            total_amount AS amount_due, 0 AS amount_paid, note, book_type
        FROM public.supplier_purchases
        WHERE {' AND '.join(pu_where)}
        ORDER BY purchase_date ASC, id ASC
        """,
        tuple(pu_params),
        fetchall=True, commit=False
    ) or []

    payments = db._execute_query(
        f"""
        SELECT
            id, 'payment' AS type, payment_date AS date, invoice_number,
            supplier_name, payment_method,
            'TT' AS description,
            NULL AS pieces, NULL AS quantity_kg, NULL AS qty_formula, NULL AS unit_price, NULL AS unit_weight,
            0 AS amount_due, amount AS amount_paid, note, book_type
        FROM public.supplier_payments
        WHERE {' AND '.join(py_where)}
        ORDER BY payment_date ASC, id ASC
        """,
        tuple(py_params),
        fetchall=True, commit=False
    ) or []

    # Merge and sort chronologically
    all_events = list(purchases) + list(payments)
    all_events.sort(key=lambda x: (x.get("date") or date.min, x.get("id") or 0))

    opening = Decimal(str(profile.get("opening_balance") or 0))
    running_balance = opening

    ledger = []
    for item in all_events:
        due = Decimal(str(item.get("amount_due") or 0))
        paid = Decimal(str(item.get("amount_paid") or 0))
        running_balance += (due - paid)
        row = dict(item)
        row["running_balance"] = float(running_balance)
        row["amount_due"] = float(due)
        row["amount_paid"] = float(paid)
        ledger.append(row)

    total_purchased = sum(Decimal(str(p.get("amount_due") or 0)) for p in purchases)
    total_paid = sum(Decimal(str(p.get("amount_paid") or 0)) for p in payments)

    op_date = profile.get("opening_balance_date") if profile else None
    return {
        "profile": profile,
        "opening_balance": float(opening),
        "opening_balance_date": op_date.isoformat() if isinstance(op_date, (date, datetime)) else (str(op_date) if op_date else None),
        "total_purchased": float(total_purchased),
        "total_paid": float(total_paid),
        "balance_due": float(running_balance),
        "ledger": ledger,
    }


def get_supplier_suggestions(company_id: int) -> list[str]:
    """Get list of distinct supplier names for autocomplete."""
    res = db._execute_query(
        """
        SELECT DISTINCT supplier_name
        FROM public.supplier_profiles
        WHERE company_id = %s AND supplier_name IS NOT NULL AND TRIM(supplier_name) != ''
        ORDER BY supplier_name ASC
        """,
        (company_id,),
        fetchall=True, commit=False
    ) or []
    return [r["supplier_name"] for r in res]


def get_product_suggestions(company_id: int) -> list[str]:
    """Get list of distinct product names for autocomplete."""
    res = db._execute_query(
        """
        SELECT DISTINCT product_name
        FROM public.supplier_purchases
        WHERE company_id = %s AND product_name IS NOT NULL AND TRIM(product_name) != ''
        ORDER BY product_name ASC
        LIMIT 200
        """,
        (company_id,),
        fetchall=True, commit=False
    ) or []
    return [r["product_name"] for r in res]


def import_excel_book(
    file_path: str,
    company_id: int,
    default_book: str = "bich",
    created_by: str = "import",
) -> dict:
    """
    Import the user's Excel file (e.g., Book1 (2).xlsx) into supplier_purchases and supplier_payments.
    Skips the 'Mã KH' column as requested by the user.
    Evaluates formulas and preserves them in qty_formula.
    """
    import openpyxl

    wb_val = openpyxl.load_workbook(file_path, data_only=True)
    wb_raw = openpyxl.load_workbook(file_path, data_only=False)

    sheet_val = wb_val.active
    sheet_raw = wb_raw.active

    purchases_imported = 0
    payments_imported = 0
    errors = []

    for r in range(3, sheet_val.max_row + 1):
        try:
            # Columns:
            # 1: Invoice / ID
            # 2: STT
            # 3: Ngày/tháng
            # 4: Mã KH (User explicitly said: Bỏ cột mã đi nhé!)
            # 5: Tên KH / Tên NCC
            # 6: Tên hàng hoá
            # 7: Số cây/tấm
            # 8: ĐVT
            # 9: Số Lượng (kg)
            # 10: Đơn giá
            # 11: Thành tiền
            # 12: Thanh Toán
            # 13: Trọng lượng
            # 14: Ghi chú

            raw_stt = sheet_val.cell(r, 2).value
            raw_date = sheet_val.cell(r, 3).value
            supp_name = sheet_val.cell(r, 5).value
            prod_name = sheet_val.cell(r, 6).value
            raw_pcs = sheet_val.cell(r, 7).value
            unit = sheet_val.cell(r, 8).value or "Kg"
            val_qty = sheet_val.cell(r, 9).value
            raw_qty = sheet_raw.cell(r, 9).value
            val_price = sheet_val.cell(r, 10).value
            val_total = sheet_val.cell(r, 11).value
            val_payment = sheet_val.cell(r, 12).value
            note = sheet_val.cell(r, 14).value or ""

            # Check if row is empty
            if not raw_date and not supp_name and not prod_name and not val_qty and not val_payment:
                continue

            supp_str = str(supp_name or "").strip()
            if not supp_str:
                continue

            # Parse date
            p_date = date.today()
            if isinstance(raw_date, datetime):
                p_date = raw_date.date()
            elif isinstance(raw_date, date):
                p_date = raw_date
            elif raw_date:
                try:
                    p_date = datetime.strptime(str(raw_date).strip()[:10], "%Y-%m-%d").date()
                except Exception:
                    pass

            # Invoice number from STT or col 1
            inv_num = f"HD-{raw_stt}" if raw_stt is not None else ""

            # Check if this row is a payment or has payment
            payment_amount = None
            if val_payment is not None:
                try:
                    amt_num = Decimal(str(val_payment).replace(",", "."))
                    if amt_num > 0:
                        payment_amount = amt_num
                except Exception:
                    pass

            is_pure_payment = False
            prod_str = str(prod_name or "").strip()
            if payment_amount and (not prod_str or "ck" in prod_str.lower() or "thanh toán" in prod_str.lower() or "tt đơn" in prod_str.lower()):
                is_pure_payment = True

            # If it's a purchase item
            if not is_pure_payment and (prod_str or val_qty or val_total):
                pieces = None
                if raw_pcs is not None:
                    try:
                        pieces = Decimal(str(raw_pcs).replace(",", "."))
                    except Exception:
                        pieces = None

                # Safe formula handling for qty
                formula_str = ""
                qty_dec = Decimal(0)
                if raw_qty is not None and str(raw_qty).startswith("="):
                    formula_str = str(raw_qty)[1:].strip()
                if val_qty is not None:
                    try:
                        qty_dec = Decimal(str(val_qty).replace(",", "."))
                    except Exception:
                        qty_dec, formula_str = evaluate_qty_formula(raw_qty)
                else:
                    qty_dec, formula_str = evaluate_qty_formula(raw_qty)

                unit_price = Decimal(0)
                if val_price is not None:
                    try:
                        unit_price = Decimal(str(val_price).replace(",", "."))
                    except Exception:
                        pass

                total_amount = Decimal(0)
                if val_total is not None and str(val_total) != "#VALUE!":
                    try:
                        total_amount = Decimal(str(val_total).replace(",", "."))
                    except Exception:
                        total_amount = (qty_dec * unit_price).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                else:
                    total_amount = (qty_dec * unit_price).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

                unit_weight = None
                if pieces and pieces > 0:
                    unit_weight = (qty_dec / pieces).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

                db._execute_query(
                    """
                    INSERT INTO public.supplier_purchases (
                        company_id, book_type, invoice_number, purchase_date,
                        supplier_name, product_name, pieces, unit, quantity_kg, qty_formula,
                        unit_price, total_amount, unit_weight, note, created_by, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, NOW(), NOW()
                    )
                    """,
                    (
                        company_id, default_book, inv_num, p_date,
                        supp_str, prod_str, pieces, str(unit), qty_dec,
                        formula_str if formula_str else None,
                        unit_price, total_amount, unit_weight, str(note) if note else None,
                        created_by
                    ),
                    commit=True
                )
                purchases_imported += 1

            # If row had a payment amount (either pure payment or deduction/payment on purchase)
            if payment_amount:
                db._execute_query(
                    """
                    INSERT INTO public.supplier_payments (
                        company_id, supplier_name, payment_date, amount,
                        invoice_number, payment_method, note, created_by, created_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, NOW()
                    )
                    """,
                    (
                        company_id, supp_str, p_date, payment_amount,
                        inv_num, "Chuyển khoản", prod_str if is_pure_payment else (str(note) if note else "Thanh toán"),
                        created_by
                    ),
                    commit=True
                )
                payments_imported += 1

        except Exception as ex:
            logger.error(f"Error importing row {r}: {ex}", exc_info=True)
            errors.append(f"Dòng {r}: {str(ex)}")

    return {
        "purchases_imported": purchases_imported,
        "payments_imported": payments_imported,
        "errors": errors[:10],
    }
