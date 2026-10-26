"""Personal-customer debt ledger, scoped to the active seller company."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.services.database import db
from psycopg2.extras import RealDictCursor

def list_customers(
    company_id: int,
    month_start: date,
    month_end: date,
    q: str = "",
    debt_only: bool = False,
) -> tuple[list[dict], dict]:
    """Return the Excel-style customer summary and month totals."""
    term = (q or "").strip()
    where_parts = ["TRUE"]
    params: list = []
    if term:
        where_parts.append("(customer_code ILIKE %s OR full_name ILIKE %s OR phone ILIKE %s)")
        wildcard = f"%{term}%"
        params.extend([wildcard, wildcard, wildcard])

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
                   MAX(l.transaction_date) AS last_transaction_date
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
    return db._execute_query(
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
        SELECT l.id, l.invoice_number, l.transaction_date, l.amount_due,
               l.amount_paid, l.received_from, l.note,
               COALESCE(u.full_name, u.username) AS creator_name,
               NOT EXISTS (
                   SELECT 1 FROM public.personal_sales s
                   WHERE s.company_id = l.company_id AND s.customer_id = l.customer_id
                     AND s.invoice_number = l.invoice_number AND COALESCE(l.invoice_number, '') <> ''
               ) AS is_editable
        FROM public.personal_debt_logs l
        LEFT JOIN public.users u ON u.id = l.created_by
        WHERE l.company_id = %s AND l.customer_id = %s
        ORDER BY l.transaction_date ASC, CASE WHEN COALESCE(l.amount_due, 0) > 0 THEN 0 ELSE 1 END ASC, l.id ASC
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
    """Edit a manual ledger row, leaving sale-linked invoice and payment rows to sales."""
    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """SELECT customer_id, invoice_number
                     FROM public.personal_debt_logs
                    WHERE company_id = %s AND id = %s FOR UPDATE""",
                (company_id, log_id),
            )
            row = cursor.fetchone()
            if not row:
                return False
            cursor.execute(
                """SELECT 1 FROM public.personal_sales
                    WHERE company_id = %s AND customer_id = %s AND invoice_number = %s
                      AND COALESCE(invoice_number, '') <> ''
                    LIMIT 1""",
                (company_id, row["customer_id"], row["invoice_number"]),
            )
            if cursor.fetchone():
                raise ValueError("Giao dịch gắn với hóa đơn bán hàng không thể sửa tại sổ này.")
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
