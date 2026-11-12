"""Truy vấn cơ sở dữ liệu của frontend.modules.invoices.controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def extract_input_invoice_select_invoice_companies(seller_tax):
    return db._execute_query("SELECT id FROM public.invoice_companies WHERE tax_code = %s LIMIT 1",
                                (seller_tax,), fetch=True, commit=False)


def extract_input_invoice_select_invoice_companies_2(buyer_tax):
    return db._execute_query(
        "SELECT name, address FROM public.invoice_companies WHERE tax_code = %s LIMIT 1",
        (buyer_tax,), fetch=True, commit=False)


def delete_input_invoice_delete_transport_input_invoices(invoice_id):
    return db._execute_query("DELETE FROM public.transport_input_invoices WHERE id = %s RETURNING id",
                                (invoice_id,), fetch=True)


def delete_input_invoice_delete_transport_cash_book(invoice_id):
    return db._execute_query("DELETE FROM public.transport_cash_book WHERE source_type = 'input_invoice' AND source_id = %s",
                      (str(invoice_id),), commit=True)
