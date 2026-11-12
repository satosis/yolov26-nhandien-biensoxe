"""Bulk payment status changes for cargo transport documents."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from psycopg2.extras import RealDictCursor

from backend.services.database import db
from frontend.modules.invoices.export import parse_vat_rate
from frontend.modules.transport.invoice_accounting import (
    accounts_for_invoice,
    default_payment_account,
    save_accounts,
)
from frontend.modules.transport.invoice_store import invoice_ref_doc, set_invoice_debt


def mark_unpaid(
    invoice_guids: list[str],
    trip_ids: list[int],
    *,
    owner_company_id: int | None,
    user_id: int | None = None,
) -> dict:
    """Chuyển các chứng từ đang 'paid' sang 'debt' trong MỘT transaction. Trả về
    {"invoices": n, "trips": m, "skipped": k}; skipped = số mục xin chuyển nhưng không chuyển được."""
    total_requested = len(invoice_guids) + len(trip_ids)
    converted_invoices = 0
    converted_trips = 0

    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            if invoice_guids:
                query = """
                    SELECT invoice_guid, seller_company_id, buyer_company_id,
                           invoice_date, invoice_number, invoice_serial,
                           payment_method, gross
                    FROM public.bkav_invoices
                    WHERE invoice_guid = ANY(%s::uuid[]) AND payment_status = 'paid'
                """
                params: list = [invoice_guids]
                if owner_company_id is not None:
                    query += " AND seller_company_id = %s"
                    params.append(owner_company_id)
                query += " FOR UPDATE"
                cursor.execute(query, params)
                invoices = cursor.fetchall()

                for row in invoices:
                    gross = Decimal(str(row.get("gross") or 0))
                    if gross <= 0:
                        continue
                    guid_str = str(row["invoice_guid"])
                    method = row.get("payment_method") or "CK"

                    accounts = accounts_for_invoice({
                        "invoice_guid": guid_str,
                        "payment_status": "paid",
                        "payment_method": method,
                    })
                    accounts["payment_account"] = default_payment_account("debt", method)
                    save_accounts(cursor, guid_str, accounts, "debt", method)

                    cursor.execute("""
                        UPDATE public.bkav_invoices
                        SET payment_status = 'debt',
                            debt_amount = %s,
                            entry_origin = CASE WHEN entry_origin = 'manual' THEN 'manual' ELSE 'edited' END
                        WHERE invoice_guid = %s
                    """, (gross, guid_str))

                    cursor.execute(
                        "DELETE FROM public.transport_cash_book WHERE source_type = 'invoice' AND source_id = %s",
                        (guid_str,),
                    )

                    buyer_id = row.get("buyer_company_id")
                    seller_id = row.get("seller_company_id") or owner_company_id
                    if buyer_id and seller_id:
                        inv_num = row.get("invoice_number")
                        inv_serial = row.get("invoice_serial") or ""
                        set_invoice_debt(cursor, seller_id, buyer_id, invoice_ref_doc(inv_num), gross,
                                         row.get("invoice_date") or date.today(),
                                         f"Nợ cước vận tải HĐ {inv_serial}-{inv_num}", user_id)

                    converted_invoices += 1

            if trip_ids:
                query = """
                    SELECT id, company_id, seller_company_id, freight_revenue, vat_rate, document_number
                    FROM public.cargo_trips
                    WHERE id = ANY(%s) AND payment_status = 'paid'
                """
                params = [trip_ids]
                if owner_company_id is not None:
                    query += " AND seller_company_id = %s"
                    params.append(owner_company_id)
                query += " FOR UPDATE"
                cursor.execute(query, params)
                trips = cursor.fetchall()

                for row in trips:
                    if row.get("freight_revenue") is None:
                        continue
                    rev = Decimal(str(row["freight_revenue"]))
                    raw_vat = row.get("vat_rate")
                    vat_rate = parse_vat_rate(raw_vat) if raw_vat is not None else None
                    rate = vat_rate if vat_rate is not None else Decimal("0")
                    total_amount = (rev * (Decimal("1") + rate)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                    if total_amount <= 0:
                        continue

                    trip_id = row["id"]
                    cursor.execute("""
                        UPDATE public.cargo_trips
                        SET payment_status = 'debt', debt_amount = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (total_amount, trip_id))

                    cid = row.get("company_id")
                    owner_cid = row.get("seller_company_id") or owner_company_id
                    if cid and owner_cid:
                        cursor.execute("""
                            INSERT INTO public.company_debt_accounts (owner_company_id, company_id, current_debt, updated_at)
                            VALUES (%s, %s, %s, NOW())
                            ON CONFLICT (owner_company_id, company_id)
                            DO UPDATE SET current_debt = public.company_debt_accounts.current_debt + EXCLUDED.current_debt,
                                          updated_at = NOW()
                        """, (owner_cid, cid, total_amount))
                        doc_num = row.get("document_number")
                        cursor.execute("""
                            INSERT INTO public.company_debt_logs
                                (owner_company_id, company_id, amount, debt_type, note, reference_doc, recorded_date, created_by)
                            VALUES (%s, %s, %s, 'trip_debt', %s, %s, CURRENT_DATE, %s)
                        """, (owner_cid, cid, total_amount, f"Nợ chuyến hàng {doc_num or trip_id}", str(trip_id), user_id))

                    converted_trips += 1

        conn.commit()

    skipped = total_requested - (converted_invoices + converted_trips)
    return {"invoices": converted_invoices, "trips": converted_trips, "skipped": max(0, skipped)}
