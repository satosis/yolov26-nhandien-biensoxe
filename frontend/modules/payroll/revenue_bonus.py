"""Revenue a driver's vehicle ran in a month, the base of the revenue bonus on the payslip.

Revenue is counted exactly like the monthly vehicle report: manual cargo trips plus Bkav invoice
lines, dated by invoice date and taken before VAT. A driver is tied to the vehicle(s) listed for
their code in transport_drivers.
"""

from datetime import date
from decimal import Decimal

from backend.services.database import db


# One row per trip or invoice line: vehicle, date, amount before VAT and what it is.
_ACTIVITY = """
    SELECT vehicle_id, invoice_date, freight_revenue AS amount, 'Chuyến' AS source,
           COALESCE(NULLIF(document_number, ''), invoice_bill_number::text) AS document,
           origin, destination, cargo_description
      FROM public.cargo_trips
    UNION ALL
    SELECT line.vehicle_id, invoice.invoice_date, line.amount, 'Hóa đơn' AS source,
           invoice.invoice_number::text AS document,
           line.origin, line.destination, line.cargo_description
      FROM public.bkav_invoice_lines line
      JOIN public.bkav_invoices invoice ON invoice.invoice_guid = line.invoice_guid
"""


def _month_end(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def month_revenue(month_start: date, driver_codes: list[str]) -> dict[str, dict]:
    """Revenue before VAT and number of trips/invoice lines per driver code (lower case)."""
    if not driver_codes:
        return {}
    rows = db._execute_query(f"""
        SELECT lower(d.driver_code) AS driver_code,
               COUNT(a.vehicle_id) AS trip_count,
               COALESCE(SUM(a.amount), 0) AS revenue_net
          FROM public.transport_drivers d
          JOIN ({_ACTIVITY}) a ON a.vehicle_id = d.vehicle_id
         WHERE lower(d.driver_code) = ANY(%s)
           AND a.invoice_date >= %s AND a.invoice_date < %s
         GROUP BY lower(d.driver_code)
    """, ([code.casefold() for code in driver_codes], month_start, _month_end(month_start)),
        fetchall=True, commit=False) or []
    return {row["driver_code"]: {"trip_count": int(row["trip_count"]),
                                 "revenue_net": Decimal(row["revenue_net"])} for row in rows}


def month_revenue_lines(month_start: date, driver_code: str) -> list[dict]:
    """The trips and invoice lines behind a driver's monthly revenue, oldest first."""
    return db._execute_query(f"""
        SELECT a.invoice_date, a.source, a.document, a.origin, a.destination,
               a.cargo_description, a.amount
          FROM public.transport_drivers d
          JOIN ({_ACTIVITY}) a ON a.vehicle_id = d.vehicle_id
         WHERE lower(d.driver_code) = %s
           AND a.invoice_date >= %s AND a.invoice_date < %s
         ORDER BY a.invoice_date, a.document, a.origin
    """, (driver_code.casefold(), month_start, _month_end(month_start)),
        fetchall=True, commit=False) or []
