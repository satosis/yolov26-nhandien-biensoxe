"""Sync fuel, toll, inspection and insurance input invoices with vehicle expenses."""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from backend.services.database import db
from frontend.context.company_context import get_current_company_id
from frontend.modules.invoices.service import _fold

logger = logging.getLogger(__name__)

FUEL_SOURCE_PREFIX = "input_invoice:"
VEHICLE_EXPENSE_TYPES = ("fuel", "repair", "toll", "inspection", "insurance", "other")

_FUEL_RE = re.compile(r"(\bdie[sz]e[nl]\b|\bxang\b|\bdau do\b)")
_TOLL_PLACE_RE = re.compile(r"đi qua\s+(?:đoạn|trạm)\s+(.+?)\s+thời gian", re.IGNORECASE)


def is_fuel_text(text: str | None) -> bool:
    """Return True if text describes vehicle fuel (diesel, petrol, fuel oil)."""
    if not text:
        return False
    folded = _fold(str(text))
    if any(k in folded for k in ("nhot", "nhon", "mo bo")):
        return False
    return bool(_FUEL_RE.search(folded))


def is_toll_text(text: str | None) -> bool:
    """Return True if text describes toll/road fee."""
    if not text:
        return False
    if is_fuel_text(text):
        return False
    folded = _fold(str(text))
    return any(k in folded for k in ("cuoc duong bo", "thu phi", "ve cau duong", "phi cau duong", "cao toc", "di qua doan", "di qua tram"))


def is_inspection_text(text: str | None) -> bool:
    """Return True if text describes vehicle inspection/registration fee."""
    if not text or is_fuel_text(text) or is_toll_text(text):
        return False
    folded = _fold(str(text))
    return any(k in folded for k in ("dang kiem", "kiem dinh o to", "kiem dinh xe",
                                     "kiem dinh phuong tien", "phi su dung duong bo",
                                     "phi bao tri duong bo"))


def is_insurance_text(text: str | None) -> bool:
    """Return True if text describes vehicle insurance (excluding social/health/life insurance)."""
    if not text or is_fuel_text(text) or is_toll_text(text) or is_inspection_text(text):
        return False
    folded = _fold(str(text))
    if any(k in folded for k in ("xa hoi", "y te", "that nghiep", "bhxh", "bhyt", "nhan tho")):
        return False
    return "bao hiem" in folded or "tnds" in folded


def _parse_items_list(items: Any) -> list[dict]:
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            return []
    if not isinstance(items, list):
        return []
    return [line for line in items if isinstance(line, dict)]


def fuel_lines(items: Any) -> list[dict]:
    """Filter lines that represent fuel items from invoice items list or json string."""
    return [line for line in _parse_items_list(items) if is_fuel_text(line.get("description"))]


def toll_lines(items: Any) -> list[dict]:
    """Filter lines that represent toll items from invoice items list or json string."""
    return [line for line in _parse_items_list(items) if is_toll_text(line.get("description"))]


def inspection_lines(items: Any) -> list[dict]:
    """Filter lines that represent inspection items from invoice items list or json string."""
    return [line for line in _parse_items_list(items) if is_inspection_text(line.get("description"))]


def insurance_lines(items: Any) -> list[dict]:
    """Filter lines that represent insurance items from invoice items list or json string."""
    return [line for line in _parse_items_list(items) if is_insurance_text(line.get("description"))]


def expense_kind(items: Any) -> str | None:
    """Return 'fuel' if items contain fuel lines, else 'toll', 'inspection', 'insurance', or None."""
    if fuel_lines(items):
        return "fuel"
    if toll_lines(items):
        return "toll"
    if inspection_lines(items):
        return "inspection"
    if insurance_lines(items):
        return "insurance"
    return None


def toll_place(description: str | None) -> str | None:
    """Extract road segment or toll station name from invoice line description."""
    if not description:
        return None
    m = _TOLL_PLACE_RE.search(str(description))
    if not m:
        return None
    val = m.group(1).strip().rstrip(",;.").strip()
    return val or None


def vehicle_options() -> list[dict]:
    """List white-list vehicles available for linking to invoices."""
    return db._execute_query(
        "SELECT id, plate_number FROM public.plate_whitelist WHERE list_type = 'white' ORDER BY plate_number",
        fetchall=True,
        commit=False,
    ) or []


def company_vehicle_options() -> list[dict]:
    """White-list vehicles of the active company, for the invoice page's vehicle dropdown."""
    return db._execute_query(
        "SELECT id, plate_number FROM public.plate_whitelist WHERE list_type = 'white' AND company_id = %s ORDER BY plate_number",
        (get_current_company_id(),),
        fetchall=True,
        commit=False,
    ) or []


def guess_vehicle_id(items: Any, line_notes: Any = ()) -> int | None:
    """The one whitelisted vehicle whose plate the item names or a line note (e.g. the insurance certificate column) carries."""
    parsed = _parse_items_list(items) or []
    text = " ".join([str(item.get("description") or "") for item in parsed] + [str(note) for note in line_notes or ()])
    norm_text = re.sub(r"[^A-Z0-9]", "", text.upper())
    if not norm_text:
        return None
    matched_ids: list[int] = []
    for row in vehicle_options():
        plate = row.get("plate_number") or ""
        norm_plate = re.sub(r"[^A-Z0-9]", "", str(plate).upper())
        if len(norm_plate) < 6:
            continue
        if norm_plate in norm_text:
            matched_ids.append(row["id"])
    if len(matched_ids) == 1:
        return matched_ids[0]
    return None


def parse_vehicle_id(raw: Any) -> int | None:
    """Validate and return vehicle_id, None if empty, or raise ValueError."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if not s.isdigit() or int(s) <= 0:
        raise ValueError("Biển số xe không hợp lệ.")
    v_id = int(s)
    valid_ids = {row["id"] for row in vehicle_options()}
    if v_id not in valid_ids:
        raise ValueError("Biển số xe không có trong danh sách xe.")
    return v_id


def sync_invoice_fuel_expense(invoice_id: int) -> None:
    """Synchronize vehicle_expenses table for a given fuel invoice."""
    inv = db._execute_query(
        "SELECT id, invoice_date, serial, invoice_number, seller_name, items, vehicle_id, subtotal, total FROM public.transport_input_invoices WHERE id = %s",
        (invoice_id,),
        fetch=True,
        commit=False,
    )
    source_ref = f"{FUEL_SOURCE_PREFIX}{invoice_id}"
    if not inv or not inv.get("vehicle_id"):
        remove_invoice_expense(invoice_id)
        return

    kind = expense_kind(inv.get("items"))
    if not kind:
        remove_invoice_expense(invoice_id)
        return

    if kind == "fuel":
        lines = fuel_lines(inv.get("items"))
    elif kind == "toll":
        lines = toll_lines(inv.get("items"))
    elif kind == "inspection":
        lines = inspection_lines(inv.get("items"))
    elif kind == "insurance":
        lines = insurance_lines(inv.get("items"))
    else:
        lines = []
    if not lines:
        remove_invoice_expense(invoice_id)
        return

    vehicle_id = inv["vehicle_id"]
    invoice_date = inv["invoice_date"]

    def _dec(val: Any) -> Decimal:
        if val is None or val == "":
            return Decimal(0)
        try:
            return Decimal(str(val).strip().replace(",", ""))
        except Exception:
            return Decimal(0)

    lines_sum = Decimal(0)
    liters = Decimal(0)
    for line in lines:
        q = _dec(line.get("quantity"))
        liters += q
        amt = _dec(line.get("amount"))
        if amt == 0 and q > 0 and line.get("unit_price"):
            amt = (q * _dec(line.get("unit_price"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        lines_sum += amt

    subtotal_val = _dec(inv.get("subtotal"))
    total_val = _dec(inv.get("total"))
    if subtotal_val > 0 and total_val > 0:
        amount = (lines_sum * total_val / subtotal_val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        amount = lines_sum

    if kind == "fuel":
        quantity = liters
        unit_price = round(amount / liters, 2) if liters > 0 else None
        vendor = str(inv.get("seller_name") or "").strip()[:500] or None
    elif kind == "toll":
        quantity = None
        unit_price = None
        places: list[str] = []
        for line in lines:
            p = toll_place(line.get("description"))
            if p and p not in places:
                places.append(p)
        if places:
            vendor = "; ".join(places).strip()[:500] or None
        else:
            vendor = str(inv.get("seller_name") or "").strip()[:500] or None
    else:
        quantity = None
        unit_price = None
        vendor = str(inv.get("seller_name") or "").strip()[:500] or None

    serial = str(inv.get("serial") or "").strip()
    inv_num = str(inv.get("invoice_number") or "").strip()
    if serial and inv_num:
        invoice_number = f"{serial} / {inv_num}"
    else:
        invoice_number = serial or inv_num or None
    if invoice_number:
        invoice_number = invoice_number[:500]

    # a. Update if existing row with source_ref = 'input_invoice:<id>'
    existing_ref = db._execute_query(
        "SELECT id FROM public.vehicle_expenses WHERE source_ref = %s",
        (source_ref,),
        fetch=True,
        commit=False,
    )
    if existing_ref:
        db._execute_query(
            """UPDATE public.vehicle_expenses
               SET vehicle_id = %s,
                   expense_date = %s,
                   expense_type = %s,
                   amount = %s,
                   quantity = %s,
                   unit_price = %s,
                   vendor = %s,
                   invoice_number = %s
             WHERE id = %s""",
            (vehicle_id, invoice_date, kind, amount, quantity, unit_price, vendor, invoice_number, existing_ref["id"]),
            commit=True,
        )
        return

    # b. Find manual entry for same vehicle, date, type=kind, source_ref NULL or not input_invoice:,
    # amount IS NULL or ABS(amount - new_amount) <= 0.01 * new_amount, order by id asc limit 1
    manual_row = db._execute_query(
        """SELECT id FROM public.vehicle_expenses
           WHERE vehicle_id = %s
             AND expense_date = %s
             AND expense_type = %s
             AND (source_ref IS NULL OR source_ref NOT LIKE 'input_invoice:%%')
             AND (amount IS NULL OR ABS(amount - %s) <= 0.01 * %s)
           ORDER BY id ASC
           LIMIT 1""",
        (vehicle_id, invoice_date, kind, amount, amount),
        fetch=True,
        commit=False,
    )
    if manual_row:
        db._execute_query(
            """UPDATE public.vehicle_expenses
               SET amount = %s,
                   quantity = %s,
                   unit_price = %s,
                   vendor = %s,
                   invoice_number = %s,
                   source_ref = %s
             WHERE id = %s""",
            (amount, quantity, unit_price, vendor, invoice_number, source_ref, manual_row["id"]),
            commit=True,
        )
        return

    # c. Insert new expense
    db._execute_query(
        """INSERT INTO public.vehicle_expenses
           (vehicle_id, expense_date, expense_type, amount, quantity, unit_price, vendor, invoice_number, source_ref)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (vehicle_id, invoice_date, kind, amount, quantity, unit_price, vendor, invoice_number, source_ref),
        commit=True,
    )


def remove_invoice_expense(invoice_id: int) -> None:
    """Remove vehicle expense record associated with input invoice."""
    db._execute_query(
        "DELETE FROM public.vehicle_expenses WHERE source_ref = %s",
        (f"{FUEL_SOURCE_PREFIX}{invoice_id}",),
        commit=True,
    )


def set_invoice_vehicle(invoice_id: int, vehicle_id: int | None) -> bool:
    """Assign vehicle to input invoice and synchronize vehicle expenses."""
    res = db._execute_query(
        "UPDATE public.transport_input_invoices SET vehicle_id = %s, updated_at = now() WHERE id = %s RETURNING id",
        (vehicle_id, invoice_id),
        fetch=True,
        commit=True,
    )
    if not res:
        return False
    sync_invoice_fuel_expense(invoice_id)
    return True

