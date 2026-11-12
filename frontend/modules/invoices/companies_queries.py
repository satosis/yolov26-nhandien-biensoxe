"""Truy vấn cơ sở dữ liệu của frontend.modules.invoices.companies_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def remove_unused_company_image_select_invoice_companies(filename):
    return db._execute_query(
        "SELECT 1 FROM public.invoice_companies WHERE signature_url ILIKE %s OR stamp_url ILIKE %s LIMIT 1",
        (f"%{filename}", f"%{filename}"), fetch=True, commit=False,
    )


def add_form_lists_select_users():
    return db._execute_query(
        "SELECT id, username, full_name, email, phone, bank_account, bank_name FROM public.users ORDER BY COALESCE(full_name, username) ASC",
        fetchall=True, commit=False,
    )


def companies_management_page_select_company_debt_accounts(active_cid):
    return db._execute_query(
        """SELECT COALESCE(SUM(current_debt), 0) AS total_debt_all
           FROM public.company_debt_accounts WHERE owner_company_id = %s AND current_debt > 0""",
        (active_cid,), fetch=True, commit=False,
    )


def companies_management_page_select_cargo_trips(active_cid, company_ids):
    return db._execute_query(
        """SELECT company_id, COUNT(*) AS cnt FROM public.cargo_trips
           WHERE seller_company_id = %s AND company_id = ANY(%s::integer[])
           GROUP BY company_id""",
        (active_cid, company_ids), fetchall=True, commit=False
    )


def select_company_by_tax_code(code):
    return db._execute_query(
        "SELECT 1 FROM public.invoice_companies WHERE tax_code = %s", (code,), fetch=True, commit=False,
    )


def sync_decrease_trip_debts_select_cargo_trips(company_id, owner_company_id):
    return db._execute_query(
        """SELECT * FROM (
                   SELECT id::text AS id, 'trip' AS source_type, debt_amount
                   FROM public.cargo_trips
                   WHERE company_id = %s AND seller_company_id = %s
                     AND payment_status = 'debt' AND debt_amount > 0
                   UNION ALL
                   SELECT invoice_guid::text AS id, 'invoice' AS source_type, debt_amount
                   FROM public.bkav_invoices
                   WHERE buyer_company_id = %s AND seller_company_id = %s
                     AND payment_status = 'debt' AND debt_amount > 0
               ) documents ORDER BY id ASC""",
        (company_id, owner_company_id, company_id, owner_company_id), fetchall=True, commit=False,
    )


def sync_decrease_trip_debts_update_set(trip_id, owner_company_id, table, key):
    owner_column = "seller_company_id"
    return db._execute_query(
        f"UPDATE {table} SET debt_amount = 0, payment_status = 'paid' WHERE {key} = %s AND {owner_column} = %s",
        (trip_id, owner_company_id), commit=True,
    )


def sync_decrease_trip_debts_update_set_2(new_trip_debt, trip_id, owner_company_id, table, key):
    return db._execute_query(
        f"UPDATE {table} SET debt_amount = %s, payment_status = 'debt' WHERE {key} = %s AND seller_company_id = %s",
        (new_trip_debt, trip_id, owner_company_id), commit=True,
    )


def upload_company_signature_update_invoice_companies(url, company_id):
    return db._execute_query(
        "UPDATE public.invoice_companies SET signature_url = %s, updated_at = NOW() WHERE id = %s RETURNING id",
        (url, company_id), fetch=True, commit=True,
    )


def upload_company_stamp_update_invoice_companies(url, company_id):
    return db._execute_query(
        "UPDATE public.invoice_companies SET stamp_url = %s, updated_at = NOW() WHERE id = %s RETURNING id",
        (url, company_id), fetch=True, commit=True,
    )


def remove_company_signature_update_invoice_companies(company_id):
    return db._execute_query(
        "UPDATE public.invoice_companies SET signature_url = NULL, updated_at = NOW() WHERE id = %s",
        (company_id,),
        commit=True,
    )


def remove_company_stamp_update_invoice_companies(company_id):
    return db._execute_query(
        "UPDATE public.invoice_companies SET stamp_url = NULL, updated_at = NOW() WHERE id = %s",
        (company_id,),
        commit=True,
    )
