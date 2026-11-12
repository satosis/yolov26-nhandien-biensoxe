"""Personal-customer debt ledger, scoped to the active seller company."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import unicodedata

from backend.services.database import db
from psycopg2.extras import RealDictCursor

def list_customers(
    company_id: int,
    month_start: date,
    month_end: date,
    q: str = "",
    debt_only: bool = False,
) -> tuple[list[dict], dict]:
    """Return the Excel-style customer summary and month totals, matching all keywords across customer info, debt logs, and sales."""
    term = (q or "").strip()
    where_parts = ["TRUE"]
    params: list = []
    if term:
        from frontend.modules.enterprise.sales_service import _folded, fold_text

        words = fold_text(term).split()
        for w in words:
            pattern = f"%{w}%"
            where_parts.append(
                f"""(
                    lower({_folded('customer_summary.customer_code')}) ILIKE %s
                    OR lower({_folded('customer_summary.full_name')}) ILIKE %s
                    OR lower({_folded('customer_summary.phone')}) ILIKE %s
                    OR lower({_folded('customer_summary.address')}) ILIKE %s
                    OR EXISTS (
                        SELECT 1 FROM public.personal_debt_logs l
                        WHERE l.company_id = customer_summary.company_id
                          AND l.customer_id = customer_summary.id
                          AND (
                              lower({_folded('l.invoice_number')}) ILIKE %s
                              OR lower({_folded('l.note')}) ILIKE %s
                              OR lower({_folded('l.received_from')}) ILIKE %s
                          )
                    )
                    OR EXISTS (
                        SELECT 1 FROM public.personal_sales s
                        WHERE s.company_id = customer_summary.company_id
                          AND s.customer_id = customer_summary.id
                          AND (
                              lower({_folded('s.invoice_number')}) ILIKE %s
                              OR lower({_folded('s.source_invoice_number')}) ILIKE %s
                              OR lower({_folded('s.note')}) ILIKE %s
                              OR lower({_folded('s.carrier')}) ILIKE %s
                              OR EXISTS (
                                  SELECT 1 FROM public.personal_sale_items fi
                                  WHERE fi.sale_id = s.id
                                    AND lower({_folded('fi.product_name')}) ILIKE %s
                              )
                          )
                    )
                )"""
            )
            params.extend([pattern] * 12)

    cte = """
        WITH balances AS (
            SELECT c.id, c.company_id, c.customer_code, c.full_name, c.phone, c.address,
                   c.opening_balance, c.opening_balance_date, c.created_at,
                   COALESCE(SUM(l.amount_due), 0) AS amount_due_all,
                   COALESCE(SUM(l.amount_paid), 0) AS amount_paid_all,
                   COALESCE(SUM(l.amount_due) FILTER (
                       WHERE l.transaction_date >= %s AND l.transaction_date < %s
                   ), 0) AS month_due,
                   COALESCE(SUM(l.amount_paid) FILTER (
                       WHERE l.transaction_date >= %s AND l.transaction_date < %s
                   ), 0) AS month_paid,
                   COUNT(l.id)::INTEGER AS log_count,
                   MAX(l.transaction_date) FILTER (WHERE COALESCE(l.amount_due, 0) <> 0 OR COALESCE(l.amount_paid, 0) <> 0) AS last_transaction_date
            FROM public.personal_debt_customers c
            LEFT JOIN public.personal_debt_logs l
              ON l.customer_id = c.id AND l.company_id = c.company_id
            WHERE c.company_id = %s
            GROUP BY c.id
        ), customer_summary AS (
            SELECT *, opening_balance + amount_due_all - amount_paid_all AS current_balance
            FROM balances
        )
    """
    # The month values are used twice by filtered aggregates; company_id is then used in the CTE.
    cte_params = [month_start, month_end, month_start, month_end, company_id]
    filters = list(where_parts)
    filter_params = list(params)
    if debt_only:
        filters.append("current_balance > 0")

    rows = db._execute_query(
        cte + f"""
        SELECT id, customer_code, full_name, phone, address, opening_balance, opening_balance_date,
               month_due, month_paid, current_balance, log_count, last_transaction_date, created_at
        FROM customer_summary
        WHERE {' AND '.join(filters)}
        ORDER BY current_balance DESC, full_name ASC
        """,
        tuple(cte_params + filter_params),
        fetchall=True,
        commit=False,
    ) or []

    stats = db._execute_query(
        cte + """
        SELECT COUNT(*)::INTEGER AS customer_count,
               COUNT(*) FILTER (WHERE current_balance > 0)::INTEGER AS debtor_count,
               COALESCE(SUM(GREATEST(current_balance, 0)), 0) AS total_debt,
               COALESCE(SUM(month_due), 0) AS month_due,
               COALESCE(SUM(month_paid), 0) AS month_paid
        FROM customer_summary
        """,
        tuple(cte_params),
        fetch=True,
        commit=False,
    ) or {}
    return rows, stats


def name_key(name) -> str:
    return " ".join(unicodedata.normalize("NFC", str(name or "")).split()).lower()


def _customer_rows(company_id: int) -> list[dict]:
    return db._execute_query(
        """
        SELECT id, customer_code, full_name, phone, address, opening_balance, opening_balance_date
        FROM public.personal_debt_customers
        WHERE company_id = %s
        ORDER BY id
        """,
        (company_id,),
        fetchall=True,
        commit=False,
    ) or []


def find_customer_by_name(company_id: int, name: str, exclude_id: int | None = None) -> dict | None:
    key = name_key(name)
    if not key or not company_id:
        return None
    rows = _customer_rows(company_id)
    for r in rows:
        if exclude_id is not None and r["id"] == exclude_id:
            continue
        if name_key(r["full_name"]) == key:
            return dict(r)
    return None


def count_duplicate_name_groups(company_id: int) -> int:
    if not company_id:
        return 0
    rows = _customer_rows(company_id)
    groups: dict[str, list[dict]] = {}
    for r in rows:
        k = name_key(r["full_name"])
        if not k:
            continue
        groups.setdefault(k, []).append(r)
    return sum(1 for g in groups.values() if len(g) >= 2)


def merge_duplicate_customers(company_id: int, only_name: str | None = None) -> dict:
    if not company_id:
        return {"groups": 0, "removed": 0, "keeper_ids": {}}
    target_key = name_key(only_name) if only_name is not None else None
    groups_count = 0
    removed_count = 0
    keeper_ids: dict[str, int] = {}

    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, customer_code, full_name, phone, address, opening_balance, opening_balance_date
                FROM public.personal_debt_customers
                WHERE company_id = %s
                ORDER BY id
                FOR UPDATE
                """,
                (company_id,),
            )
            rows = cursor.fetchall() or []
            groups: dict[str, list[dict]] = {}
            for r in rows:
                k = name_key(r["full_name"])
                if not k:
                    continue
                groups.setdefault(k, []).append(r)

            for k, members in groups.items():
                if len(members) < 2:
                    continue
                if target_key is not None and k != target_key:
                    continue

                keeper = members[0]
                keeper_id = keeper["id"]
                keeper_code = keeper["customer_code"]
                keeper_name = keeper["full_name"]
                keeper_bal = Decimal(str(keeper["opening_balance"] or 0))
                keeper_dates = [keeper["opening_balance_date"]] if keeper["opening_balance_date"] else []
                keeper_phone = keeper["phone"]
                keeper_address = keeper["address"]

                for other in members[1:]:
                    other_id = other["id"]
                    cursor.execute(
                        """
                        UPDATE public.personal_debt_logs
                        SET customer_id = %s
                        WHERE company_id = %s AND customer_id = %s
                        """,
                        (keeper_id, company_id, other_id),
                    )
                    cursor.execute(
                        """
                        UPDATE public.personal_sales
                        SET customer_id = %s, customer_code = %s, customer_name = %s
                        WHERE company_id = %s AND customer_id = %s
                        """,
                        (keeper_id, keeper_code, keeper_name, company_id, other_id),
                    )
                    other_bal = Decimal(str(other["opening_balance"] or 0))
                    keeper_bal += other_bal
                    if other["opening_balance_date"]:
                        keeper_dates.append(other["opening_balance_date"])
                    if not (keeper_phone or "").strip() and (other.get("phone") or "").strip():
                        keeper_phone = other["phone"]
                    if not (keeper_address or "").strip() and (other.get("address") or "").strip():
                        keeper_address = other["address"]

                    cursor.execute(
                        "DELETE FROM public.personal_debt_customers WHERE company_id = %s AND id = %s",
                        (company_id, other_id),
                    )
                    removed_count += 1

                earliest_date = min(keeper_dates) if keeper_dates else None

                cursor.execute(
                    """
                    UPDATE public.personal_debt_customers
                    SET opening_balance = %s, opening_balance_date = %s, phone = %s, address = %s, updated_at = NOW()
                    WHERE company_id = %s AND id = %s
                    """,
                    (keeper_bal, earliest_date, keeper_phone, keeper_address, company_id, keeper_id),
                )
                groups_count += 1
                keeper_ids[k] = keeper_id

            conn.commit()

    return {"groups": groups_count, "removed": removed_count, "keeper_ids": keeper_ids}


def create_customer(
    company_id: int,
    customer_code: str,
    full_name: str,
    phone: str,
    address: str,
    opening_balance: Decimal,
    user_id: int | None,
    opening_balance_date: date | None = None,
) -> dict | None:
    existing = find_customer_by_name(company_id, full_name)
    if existing:
        current_bal = Decimal(str(existing.get("opening_balance") or 0))
        input_bal = Decimal(str(opening_balance or 0))
        new_balance = (current_bal + input_bal) if input_bal != 0 else current_bal

        old_phone = (existing.get("phone") or "").strip()
        new_phone = phone if not old_phone and (phone or "").strip() else existing.get("phone")

        old_address = (existing.get("address") or "").strip()
        new_address = address if not old_address and (address or "").strip() else existing.get("address")

        new_date = existing.get("opening_balance_date") or opening_balance_date

        row = db._execute_query(
            """
            UPDATE public.personal_debt_customers
            SET opening_balance = %s,
                opening_balance_date = %s,
                phone = %s,
                address = %s,
                updated_at = NOW()
            WHERE company_id = %s AND id = %s
            RETURNING id, customer_code, full_name, phone, address, opening_balance, opening_balance_date
            """,
            (new_balance, new_date, new_phone, new_address, company_id, existing["id"]),
            fetch=True,
            commit=True,
        )
        if row:
            row = dict(row)
            row["merged"] = True
        return row

    return db._execute_query(
        """
        INSERT INTO public.personal_debt_customers
            (company_id, customer_code, full_name, phone, address, opening_balance, opening_balance_date, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, customer_code, full_name, phone, address, opening_balance, opening_balance_date
        """,
        (company_id, customer_code, full_name, phone, address, opening_balance, opening_balance_date, user_id),
        fetch=True,
        commit=True,
    )


def update_customer(
    company_id: int,
    customer_id: int,
    customer_code: str,
    full_name: str,
    phone: str,
    address: str,
    opening_balance: Decimal,
    opening_balance_date: date | None = None,
) -> dict | None:
    row = db._execute_query(
        """
        UPDATE public.personal_debt_customers
        SET customer_code = %s, full_name = %s, phone = %s, address = %s,
            opening_balance = %s, opening_balance_date = %s, updated_at = NOW()
        WHERE company_id = %s AND id = %s
        RETURNING id, customer_code, full_name, phone, address, opening_balance, opening_balance_date
        """,
        (customer_code, full_name, phone, address, opening_balance, opening_balance_date, company_id, customer_id),
        fetch=True,
        commit=True,
    )
    if not row:
        return None

    duplicate = find_customer_by_name(company_id, full_name, exclude_id=customer_id)
    if duplicate:
        merge_res = merge_duplicate_customers(company_id, only_name=full_name)
        k_id = merge_res.get("keeper_ids", {}).get(name_key(full_name)) or customer_id
        keeper_row = db._execute_query(
            """
            SELECT id, customer_code, full_name, phone, address, opening_balance, opening_balance_date
            FROM public.personal_debt_customers
            WHERE company_id = %s AND id = %s
            """,
            (company_id, k_id),
            fetch=True,
            commit=False,
        )
        if keeper_row:
            keeper_row = dict(keeper_row)
            keeper_row["merged"] = True
            return keeper_row

    return row


def delete_customer(company_id: int, customer_id: int) -> str:
    row = db._execute_query(
        """
        DELETE FROM public.personal_debt_customers c
        WHERE c.company_id = %s AND c.id = %s
          AND NOT EXISTS (
              SELECT 1 FROM public.personal_debt_logs l
              WHERE l.company_id = c.company_id AND l.customer_id = c.id
          )
        RETURNING c.id
        """,
        (company_id, customer_id),
        fetch=True,
        commit=True,
    )
    if row:
        return ""
    exists = db._execute_query(
        "SELECT 1 FROM public.personal_debt_customers WHERE company_id = %s AND id = %s",
        (company_id, customer_id),
        fetch=True,
        commit=False,
    )
    return "Khách hàng không tồn tại." if not exists else "Khách hàng đã có nhật ký công nợ nên không thể xóa."


def _customer_sale_invoice(company_id: int, customer_id: int, invoice_number: str) -> str:
    """Số HĐ chỉ giữ khi khách này có đơn trong nhật ký bán hàng; ngược lại để trống."""
    clean = (invoice_number or "").strip()
    if not clean:
        return ""
    found = db._execute_query(
        """SELECT 1 FROM public.personal_sales
            WHERE company_id = %s AND customer_id = %s AND invoice_number = %s LIMIT 1""",
        (company_id, customer_id, clean),
        fetch=True,
        commit=False,
    )
    return clean if found else ""


def add_transaction(
    company_id: int,
    customer_id: int,
    invoice_number: str,
    transaction_date: date,
    amount_due: Decimal,
    amount_paid: Decimal,
    received_from: str,
    note: str,
    user_id: int | None,
) -> dict | None:
    invoice_number = _customer_sale_invoice(company_id, customer_id, invoice_number)
    return db._execute_query(
        """
        INSERT INTO public.personal_debt_logs
            (company_id, customer_id, invoice_number, transaction_date,
             amount_due, amount_paid, received_from, note, created_by)
        SELECT c.company_id, c.id, %s, %s, %s, %s, %s, %s, %s
        FROM public.personal_debt_customers c
        WHERE c.company_id = %s AND c.id = %s
        RETURNING id, customer_id, invoice_number, transaction_date,
                  amount_due, amount_paid, received_from, note
        """,
        (invoice_number, transaction_date, amount_due, amount_paid, received_from, note, user_id,
         company_id, customer_id),
        fetch=True,
        commit=True,
    )


def get_customer_logs(company_id: int, customer_id: int) -> tuple[dict | None, list[dict]]:
    customer = db._execute_query(
        """
        SELECT c.id, c.customer_code, c.full_name, c.phone, c.address, c.opening_balance, c.opening_balance_date,
               c.opening_balance + COALESCE(SUM(l.amount_due - l.amount_paid), 0) AS current_balance
        FROM public.personal_debt_customers c
        LEFT JOIN public.personal_debt_logs l
          ON l.customer_id = c.id AND l.company_id = c.company_id
        WHERE c.company_id = %s AND c.id = %s
        GROUP BY c.id, c.opening_balance_date
        """,
        (company_id, customer_id),
        fetch=True,
        commit=False,
    )
    if not customer:
        return None, []
    logs = db._execute_query(
        """
        WITH raw_logs AS (
            SELECT l.id, l.invoice_number, l.transaction_date, l.amount_due,
                   l.amount_paid, l.received_from, l.note,
                   COALESCE(u.full_name, u.username) AS creator_name,
                   COALESCE(l.sale_id, (
                       SELECT s.id FROM public.personal_sales s
                       WHERE s.company_id = l.company_id AND s.customer_id = l.customer_id
                         AND s.invoice_number = l.invoice_number AND COALESCE(l.invoice_number, '') <> ''
                       ORDER BY s.id LIMIT 1
                   )) AS linked_sale_id
            FROM public.personal_debt_logs l
            LEFT JOIN public.users u ON u.id = l.created_by
            WHERE l.company_id = %s AND l.customer_id = %s
              AND (COALESCE(l.amount_due, 0) <> 0 OR COALESCE(l.amount_paid, 0) <> 0)
        )
        SELECT id, invoice_number, transaction_date, amount_due,
               amount_paid, received_from, note, creator_name,
               linked_sale_id,
               (linked_sale_id IS NULL) AS is_editable,
               (linked_sale_id IS NOT NULL AND COALESCE(amount_due, 0) = 0) AS is_sale_payment
        FROM raw_logs
        ORDER BY transaction_date ASC, CASE WHEN COALESCE(amount_due, 0) > 0 THEN 0 ELSE 1 END ASC, id ASC
        """,
        (company_id, customer_id),
        fetchall=True,
        commit=False,
    ) or []
    return customer, logs


def delete_transaction(company_id: int, log_id: int) -> bool:
    return bool(db._execute_query(
        "DELETE FROM public.personal_debt_logs WHERE company_id = %s AND id = %s",
        (company_id, log_id),
        commit=True,
    ))


def update_transaction(company_id: int, log_id: int, values: dict) -> bool:
    """Edit a manual ledger row or sale payment row, leaving sale invoice debt rows to sales."""
    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """SELECT customer_id, invoice_number, amount_due, sale_id
                     FROM public.personal_debt_logs
                    WHERE company_id = %s AND id = %s FOR UPDATE""",
                (company_id, log_id),
            )
            row = cursor.fetchone()
            if not row:
                return False

            is_linked_sale = row["sale_id"] is not None
            if not is_linked_sale and (row["invoice_number"] or "").strip():
                cursor.execute(
                    """SELECT 1 FROM public.personal_sales
                        WHERE company_id = %s AND customer_id = %s AND invoice_number = %s
                          AND COALESCE(invoice_number, '') <> ''
                        LIMIT 1""",
                    (company_id, row["customer_id"], row["invoice_number"]),
                )
                if cursor.fetchone():
                    is_linked_sale = True

            if is_linked_sale:
                current_due = Decimal(str(row["amount_due"] or 0))
                if current_due > 0:
                    raise ValueError("Dòng này sửa trong hóa đơn bán hàng.")
                new_due = Decimal(str(values.get("amount_due") or 0))
                current_inv = str(row["invoice_number"] or "").strip()
                new_inv = str(values.get("invoice_number") or "").strip()
                if new_due != 0 or new_inv != current_inv:
                    raise ValueError("Khoản thu của hóa đơn chỉ sửa ngày, tiền trả, nguồn nhận, ghi chú.")
            else:
                values = dict(values, invoice_number=_customer_sale_invoice(
                    company_id, row["customer_id"], values.get("invoice_number")))
                new_invoice = values["invoice_number"]
                if new_invoice:
                    cursor.execute(
                        """SELECT 1 FROM public.personal_sales
                            WHERE company_id = %s AND customer_id = %s AND invoice_number = %s
                            LIMIT 1""",
                        (company_id, row["customer_id"], new_invoice),
                    )
                    if cursor.fetchone():
                        raise ValueError("Số chứng từ đã thuộc hóa đơn bán hàng.")

            cursor.execute(
                """UPDATE public.personal_debt_logs
                      SET invoice_number = %s, transaction_date = %s, amount_due = %s,
                          amount_paid = %s, received_from = %s, note = %s
                    WHERE company_id = %s AND id = %s""",
                (
                    values["invoice_number"], values["transaction_date"],
                    values["amount_due"], values["amount_paid"],
                    values["received_from"], values["note"], company_id, log_id,
                ),
            )
        conn.commit()
    return True
