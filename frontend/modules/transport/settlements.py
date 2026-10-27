"""Amounts paid and still owed for invoiced transport trips."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from backend.services.database import db


def monthly_trip_settlements(start: date, end: date, vehicle_ids: set[int]) -> list[dict]:
    if not vehicle_ids:
        return []
    trips = db._execute_query(
        """SELECT t.id, t.invoice_date, t.vehicle_id, t.plate_number, t.company_id,
                  t.document_number, t.service_name, t.freight_revenue, t.vat_rate,
                  t.payment_status, t.payment_method, t.debt_amount,
                  c.name AS company_name, c.tax_code, c.email AS company_email
             FROM public.cargo_trips t
             JOIN public.invoice_companies c ON c.id = t.company_id
            WHERE t.invoice_date >= %s AND t.invoice_date < %s
              AND t.vehicle_id = ANY(%s::int[])
              AND t.payment_status IN ('paid', 'debt')
            ORDER BY t.invoice_date, t.id""",
        (start, end, list(vehicle_ids)), fetchall=True, commit=False,
    ) or []
    result = [trip_settlement(trip) for trip in trips]
    invoice_lines = db._execute_query(
        """SELECT i.invoice_guid::text AS invoice_guid, i.invoice_date,
                  i.buyer_company_id AS company_id,
                  lpad(i.invoice_number::text, 8, '0') AS document_number,
                  i.service_name, i.vat_rate, i.payment_status, i.payment_method,
                  i.debt_amount AS invoice_debt, i.gross AS invoice_gross,
                  l.line_number, l.vehicle_id,
                  v.plate_number AS plate_number,
                  l.amount AS freight_revenue, l.tax_amount,
                  c.name AS company_name, c.tax_code, c.email AS company_email
             FROM public.bkav_invoices i
             JOIN public.bkav_invoice_lines l ON l.invoice_guid = i.invoice_guid
             JOIN public.plate_whitelist v ON v.id = l.vehicle_id
             JOIN public.invoice_companies c ON c.id = i.buyer_company_id
            WHERE i.invoice_date >= %s AND i.invoice_date < %s
              AND i.payment_status IN ('paid', 'debt')
            ORDER BY i.invoice_guid, l.line_number""",
        (start, end), fetchall=True, commit=False,
    ) or []
    by_invoice: dict[str, list[dict]] = {}
    for line in invoice_lines:
        by_invoice.setdefault(line["invoice_guid"], []).append(line)
    for guid, lines in by_invoice.items():
        debt = Decimal(str(lines[0]["invoice_debt"] or 0))
        invoice_gross = Decimal(str(lines[0]["invoice_gross"]))
        allocated = Decimal(0)
        for index, line in enumerate(lines):
            gross = Decimal(str(line["freight_revenue"])) + (
                Decimal(str(line["tax_amount"])) if line["tax_amount"] is not None
                else (Decimal(str(line["freight_revenue"])) * Decimal(str(line["vat_rate"] or 0))).quantize(Decimal("1")))
            outstanding = (debt - allocated if index == len(lines) - 1 else
                           (debt * gross / invoice_gross).quantize(Decimal("0.01"))
                           if invoice_gross else Decimal(0))
            allocated += outstanding
            if line["vehicle_id"] in vehicle_ids:
                result.append({**line, "id": guid, "invoice_total": gross,
                               "outstanding": outstanding, "paid_amount": gross - outstanding,
                               "debt_amount": outstanding})
    return result


def trip_settlement(trip: dict) -> dict:
    invoice_total = trip["freight_revenue"] * (Decimal("1") + (trip["vat_rate"] or Decimal("0")))
    outstanding = trip["debt_amount"] if trip["payment_status"] == "debt" else Decimal("0")
    return {**trip, "invoice_total": invoice_total,
            "outstanding": outstanding, "paid_amount": invoice_total - outstanding}


def company_settlements(trips: list[dict]) -> list[dict]:
    companies = defaultdict(lambda: {
        "company_name": "", "tax_code": "", "invoice_total": Decimal("0"),
        "paid_amount": Decimal("0"), "outstanding": Decimal("0"), "trip_count": 0,
    })
    for trip in trips:
        company = companies[trip["company_id"]]
        company["company_name"] = trip["company_name"]
        company["tax_code"] = trip["tax_code"]
        company["invoice_total"] += trip["invoice_total"]
        company["paid_amount"] += trip["paid_amount"]
        company["outstanding"] += trip["outstanding"]
        company["trip_count"] += 1
    return sorted(companies.values(), key=lambda row: row["company_name"])
