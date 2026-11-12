"""The one place that writes a sales invoice: header, lines, accounting and the books it feeds.

The invoice form, the Zalo order automation and the "chưa thanh toán" button all end here, so an invoice
gets the same number check, the same 131 debt entry or cash/bank receipt, whichever way it arrives.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import NamedTuple

from psycopg2.extras import Json

from backend.services.database import db
from frontend.context.company_context import get_current_company_id
from frontend.modules.transport.invoice_accounting import (
    book_for_payment_account,
    default_accounts,
    save_accounts,
)

logger = logging.getLogger(__name__)

_NUMBER_LOCK_KEY = 1_118_001  # pg_advisory_xact_lock(key, seller): numbering and number check of one seller


class InvoiceNumberTaken(ValueError):
    """Another invoice of the same seller already uses this series and number."""


class SavedInvoice(NamedTuple):
    guid: str
    invoice_number: int
    invoice_serial: str


def _col(row, key: str, index: int):
    return row[key] if isinstance(row, dict) else row[index]


def next_invoice_number_and_serial(seller_company_id: int | None = None) -> tuple[str, str]:
    """Next number (8 digits) and the series of the seller's latest invoice."""
    cid = seller_company_id or get_current_company_id()
    if cid:
        row = db._execute_query("""
                SELECT invoice_serial, MAX(invoice_number) as max_num
                FROM public.bkav_invoices
                WHERE seller_company_id = %s
                  AND invoice_number IS NOT NULL
                GROUP BY invoice_serial
                ORDER BY MAX(invoice_number) DESC
                LIMIT 1
            """, (cid,), fetch=True)
    else:
        row = db._execute_query("""
                SELECT invoice_serial, MAX(invoice_number) as max_num
                FROM public.bkav_invoices
                WHERE invoice_number IS NOT NULL
                GROUP BY invoice_serial
                ORDER BY MAX(invoice_number) DESC
                LIMIT 1
            """, fetch=True)
    serial = (row.get("invoice_serial") if row else None) or "C26TAA"
    next_num = ((row.get("max_num") or 0) + 1) if row else 1
    return str(next_num).zfill(8), serial


def _allocate_number(cursor, seller_company_id: int) -> tuple[int, str]:
    """The next number, read inside the transaction that holds the seller's number lock."""
    cursor.execute("""
        SELECT invoice_serial, MAX(invoice_number) AS max_num
        FROM public.bkav_invoices
        WHERE seller_company_id = %s AND invoice_number IS NOT NULL
        GROUP BY invoice_serial
        ORDER BY MAX(invoice_number) DESC
        LIMIT 1
    """, (seller_company_id,))
    row = cursor.fetchone()
    serial = (_col(row, "invoice_serial", 0) if row else None) or "C26TAA"
    return int((_col(row, "max_num", 1) if row else 0) or 0) + 1, serial


def _refuse_taken_number(cursor, payload: dict, guid: str) -> None:
    # A seller must not have two invoices with the same series + number (a hand-typed number once collided
    # with a real one synced from BKAV).
    cursor.execute("""
        SELECT i.invoice_guid, i.entry_origin, b.name AS buyer_name FROM public.bkav_invoices i
        LEFT JOIN public.invoice_companies b ON b.id = i.buyer_company_id
        WHERE i.seller_company_id = %s AND i.invoice_serial IS NOT DISTINCT FROM %s AND i.invoice_number = %s
          AND i.invoice_guid::text <> %s
        LIMIT 1
    """, (payload["seller_company_id"], payload["invoice_serial"], payload["invoice_number"], guid))
    taken = cursor.fetchone()
    if taken:
        buyer = _col(taken, "buyer_name", 2)
        raise InvoiceNumberTaken(
            f"Số hóa đơn {payload['invoice_number']} (ký hiệu {payload['invoice_serial'] or '—'}) đã tồn tại"
            f" cho khách {buyer or 'khác'}.")


def _write_header_and_lines(cursor, payload: dict, guid: str, edit: bool) -> None:
    exists = False
    if edit:
        cursor.execute("SELECT seller_company_id, entry_origin FROM public.bkav_invoices WHERE invoice_guid = %s", (guid,))
        exists = bool(cursor.fetchone())
    if exists:
        cursor.execute("""
            UPDATE public.bkav_invoices
            SET buyer_company_id = %s, invoice_date = %s, invoice_number = %s,
                invoice_serial = %s, buyer_tax_code = %s, service_name = %s,
                vat_rate = %s, payment_method = %s, payment_status = %s,
                debt_amount = %s, subtotal = %s, gross = %s, line_count = %s,
                entry_origin = CASE WHEN entry_origin = 'manual' THEN 'manual' ELSE 'edited' END
            WHERE invoice_guid = %s
        """, (payload["buyer_company_id"], payload["invoice_date"],
              payload["invoice_number"], payload["invoice_serial"],
              payload["buyer_tax_code"], payload["service_name"],
              str(payload["vat_rate"]), payload["payment_method"],
              payload["payment_status"], str(payload["debt_amount"]),
              str(payload["subtotal"]), str(payload["gross"]),
              payload["line_count"], guid))
        cursor.execute("DELETE FROM public.bkav_invoice_lines WHERE invoice_guid = %s", (guid,))
    else:
        cursor.execute("""
            INSERT INTO public.bkav_invoices
                (invoice_guid, seller_company_id, buyer_company_id, invoice_date,
                 invoice_number, invoice_serial, buyer_tax_code, service_name,
                 vat_rate, payment_method, payment_status, debt_amount,
                 subtotal, gross, line_count, entry_origin)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'manual')
        """, (guid, payload["seller_company_id"], payload["buyer_company_id"],
              payload["invoice_date"], payload["invoice_number"],
              payload["invoice_serial"], payload["buyer_tax_code"],
              payload["service_name"], str(payload["vat_rate"]),
              payload["payment_method"], payload["payment_status"],
              str(payload["debt_amount"]), str(payload["subtotal"]),
              str(payload["gross"]), payload["line_count"]))

    for line in payload["lines"]:
        cursor.execute("""
            INSERT INTO public.bkav_invoice_lines
                (invoice_guid, line_number, vehicle_id,
                 cargo_description, cargo_weight_tons, description, origin,
                 destination, distance_km, via_points, amount, tax_amount,
                 fuel_liters, fuel_price_per_liter, fuel_cost_total)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (guid, line["line_number"], line["vehicle_id"],
              line.get("cargo_description"),
              str(line["cargo_weight_tons"]) if line.get("cargo_weight_tons") is not None else None,
              line["description"], line["origin"], line["destination"],
              str(line["distance_km"]) if line.get("distance_km") is not None else None,
              Json(line.get("via_points") or []),
              str(line["amount"]),
              str(line["tax_amount"]) if line.get("tax_amount") is not None else None,
              str(line["fuel_liters"]) if line.get("fuel_liters") is not None else None,
              str(line["fuel_price_per_liter"]) if line.get("fuel_price_per_liter") is not None else None,
              str(line["fuel_cost_total"]) if line.get("fuel_cost_total") is not None else None))


def invoice_ref_doc(invoice_number) -> str:
    return f"HĐ-{invoice_number:08d}" if isinstance(invoice_number, int) else f"HĐ-{invoice_number}"


def clear_invoice_debt(cursor, owner_company_id: int, buyer_company_id: int, ref_doc: str) -> None:
    """Take back the 131 entry an invoice made (it was paid after all)."""
    cursor.execute("""
        SELECT id, amount FROM public.company_debt_logs
        WHERE company_id = %s AND owner_company_id = %s AND reference_doc = %s
    """, (buyer_company_id, owner_company_id, ref_doc))
    old = cursor.fetchone()
    if not old:
        return
    cursor.execute("""
        UPDATE public.company_debt_accounts
        SET current_debt = current_debt - %s, updated_at = NOW()
        WHERE owner_company_id = %s AND company_id = %s
    """, (Decimal(str(_col(old, "amount", 1) or 0)), owner_company_id, buyer_company_id))
    cursor.execute("DELETE FROM public.company_debt_logs WHERE id = %s", (_col(old, "id", 0),))


def set_invoice_debt(cursor, owner_company_id: int, buyer_company_id: int, ref_doc: str, amount,
                     recorded_date, note: str, created_by: int | None = None) -> None:
    """The buyer owes `amount` for this invoice: one log row per invoice, the balance moves by the difference."""
    target = Decimal(str(amount))
    cursor.execute("""
        SELECT id, amount FROM public.company_debt_logs
        WHERE company_id = %s AND owner_company_id = %s AND reference_doc = %s
    """, (buyer_company_id, owner_company_id, ref_doc))
    existing = cursor.fetchone()
    if existing:
        diff = target - Decimal(str(_col(existing, "amount", 1) or 0))
        if diff == 0:
            return
        cursor.execute("""
            UPDATE public.company_debt_logs SET amount = %s, recorded_date = %s, note = %s WHERE id = %s
        """, (target, recorded_date, note, _col(existing, "id", 0)))
    else:
        diff = target
        cursor.execute("""
            INSERT INTO public.company_debt_logs
                (owner_company_id, company_id, amount, debt_type, note, recorded_date, reference_doc, created_by)
            VALUES (%s, %s, %s, 'invoice_debt', %s, %s, %s, %s)
        """, (owner_company_id, buyer_company_id, target, note, recorded_date, ref_doc, created_by))
    cursor.execute("""
        INSERT INTO public.company_debt_accounts (owner_company_id, company_id, current_debt, updated_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (owner_company_id, company_id)
        DO UPDATE SET current_debt = public.company_debt_accounts.current_debt + EXCLUDED.current_debt,
                      updated_at = NOW()
    """, (owner_company_id, buyer_company_id, diff))


def _post_to_books(cursor, payload: dict, guid: str, accounting: dict, created_by: int | None) -> None:
    """Sổ Tiền gửi NH / Sổ Quỹ tiền mặt for a paid invoice, Sổ Công nợ (131) for an owed one."""
    seller_id = payload["seller_company_id"]
    buyer_id = payload["buyer_company_id"]
    cursor.execute("SELECT name, tax_code FROM public.invoice_companies WHERE id = %s", (buyer_id,))
    b_row = cursor.fetchone()
    buyer_name = (_col(b_row, "name", 0) if b_row else "") or ""
    buyer_tax = payload.get("buyer_tax_code") or (_col(b_row, "tax_code", 1) if b_row else "") or ""
    inv_date = payload["invoice_date"]
    inv_num = payload["invoice_number"]
    inv_serial = payload["invoice_serial"]
    payment_status = payload["payment_status"]
    payment_method = payload["payment_method"]
    ref_doc = invoice_ref_doc(inv_num)

    cursor.execute("DELETE FROM public.transport_cash_book WHERE source_type = 'invoice' AND source_id = %s", (guid,))

    if payment_status == "paid":
        book_type = book_for_payment_account(accounting["payment_account"], payment_method)
        voucher_prefix = "BC" if book_type == "bank" else "PT"
        voucher_num_str = f"{voucher_prefix}-{inv_num:04d}" if isinstance(inv_num, int) else f"{voucher_prefix}-{inv_num}"
        desc = f"Khách hàng {buyer_name} thanh toán cước vận chuyển HĐ {inv_num}"
        note_text = f"Hóa đơn {inv_serial} - {inv_num:08d}" if isinstance(inv_num, int) else f"Hóa đơn {inv_serial} - {inv_num}"
        cursor.execute("""
            INSERT INTO public.transport_cash_book
                (company_id, book_type, entry_date, voucher_date, voucher_no,
                 voucher_type, description, offset_account, amount,
                 partner_code, partner_name, source_type, source_id, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'invoice', %s, %s)
        """, (seller_id, book_type, inv_date, inv_date, voucher_num_str,
              "receipt", desc, "131", payload["gross"],
              buyer_tax, buyer_name, guid, note_text))
        clear_invoice_debt(cursor, seller_id, buyer_id, ref_doc)
    elif payment_status == "debt":
        set_invoice_debt(cursor, seller_id, buyer_id, ref_doc, payload["debt_amount"] or payload["gross"],
                         inv_date, f"Nợ cước vận tải HĐ {inv_serial}-{inv_num}", created_by)


def save_sales_invoice(payload: dict, *, guid: str, edit: bool = False, created_by: int | None = None) -> SavedInvoice:
    """Insert the invoice `guid` and post it to the books in one transaction; `edit` updates it when it exists.

    A payload without `invoice_number` takes the seller's next number and series; one with a number is refused
    when another invoice of the seller already uses it. Raises InvoiceNumberTaken.
    """
    payload = dict(payload)
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            # Numbering and the number check of one seller run one after another, so two writers cannot pick the same number.
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_NUMBER_LOCK_KEY, int(payload["seller_company_id"])))
            if not payload.get("invoice_number"):
                payload["invoice_number"], payload["invoice_serial"] = _allocate_number(cursor, payload["seller_company_id"])
            else:
                _refuse_taken_number(cursor, payload, guid)

            _write_header_and_lines(cursor, payload, guid, edit)
            # Hạch toán người dùng đã sửa trên form (chỉ lưu tài khoản khác mặc định)
            accounting = payload.get("accounting") or default_accounts(payload["payment_status"], payload["payment_method"])
            save_accounts(cursor, guid, accounting, payload["payment_status"], payload["payment_method"])
            _post_to_books(cursor, payload, guid, accounting, created_by)
        conn.commit()
    return SavedInvoice(guid, payload["invoice_number"], payload["invoice_serial"])


def delete_sales_invoices(guids: list[str], *, seller_company_id: int | None = None,
                          vehicle_id: int | None = None) -> list[str]:
    """Xóa hẳn các hóa đơn bán ra cùng mọi thứ đã ghi cho chúng: dòng hàng, hạch toán, phiếu thu/báo có,
    công nợ 131, liên kết và trạng thái đẩy BKAV, nhật ký gửi Zalo. Trả các guid đã xóa."""
    clean_guids = [str(g) for g in guids if g]
    if not clean_guids:
        return []

    deleted_guids: list[str] = []
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            query = (
                "SELECT invoice_guid::text, seller_company_id, buyer_company_id, invoice_number "
                "FROM public.bkav_invoices "
                "WHERE invoice_guid = ANY(%s::uuid[])"
            )
            params = [clean_guids]
            if seller_company_id is not None:
                query += " AND seller_company_id = %s"
                params.append(seller_company_id)
            if vehicle_id is not None:
                query += (
                    " AND EXISTS (SELECT 1 FROM public.bkav_invoice_lines l "
                    "WHERE l.invoice_guid = bkav_invoices.invoice_guid AND l.vehicle_id = %s)"
                )
                params.append(vehicle_id)
            query += " FOR UPDATE"

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            for row in rows:
                guid = str(_col(row, "invoice_guid", 0))
                seller_id = _col(row, "seller_company_id", 1)
                buyer_id = _col(row, "buyer_company_id", 2)
                inv_number = _col(row, "invoice_number", 3)

                if buyer_id and inv_number:
                    clear_invoice_debt(cursor, seller_id, buyer_id, invoice_ref_doc(inv_number))

                cursor.execute(
                    "DELETE FROM public.transport_cash_book WHERE source_type = 'invoice' AND source_id = %s",
                    (guid,),
                )

                cursor.execute(
                    "SELECT partner_invoice_id FROM public.bkav_saved_invoice_links WHERE invoice_guid = %s",
                    (guid,),
                )
                links = cursor.fetchall()
                partner_ids = [
                    _col(r, "partner_invoice_id", 0)
                    for r in links
                    if _col(r, "partner_invoice_id", 0) is not None
                ]
                if partner_ids:
                    cursor.execute(
                        "DELETE FROM public.bkav_invoice_pushes WHERE trip_id = ANY(%s)",
                        (partner_ids,),
                    )
                cursor.execute(
                    "DELETE FROM public.bkav_saved_invoice_links WHERE invoice_guid = %s",
                    (guid,),
                )

                cursor.execute(
                    "DELETE FROM public.zalo_invoice_messages WHERE invoice_guid = %s",
                    (guid,),
                )

                cursor.execute(
                    "DELETE FROM public.bkav_invoices WHERE invoice_guid = %s",
                    (guid,),
                )
                deleted_guids.append(guid)
        conn.commit()
    return deleted_guids

