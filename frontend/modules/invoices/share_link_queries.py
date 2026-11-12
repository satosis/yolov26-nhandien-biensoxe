"""Truy vấn cơ sở dữ liệu của frontend.modules.invoices.share_link: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def get_public_invoice_pdf_select_bkav_invoices(invoice_guid):
    return db._execute_query(
        "SELECT invoice_guid, invoice_number, invoice_serial, invoice_code FROM public.bkav_invoices WHERE invoice_guid = %s",
        (str(invoice_guid),),
        fetch=True,
        commit=False,
    )
