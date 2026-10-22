import asyncio
import base64
import calendar
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
import json
import math
import httpx
import logging
import re
import secrets
import time
import urllib.parse
import unicodedata
from functools import lru_cache
from datetime import datetime, date
from uuid import UUID
from typing import Any, Optional
from fastapi import APIRouter, Request, Depends, Form, Query, File, UploadFile, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from pydantic import BaseModel
from starlette.responses import RedirectResponse
from pathlib import Path
from psycopg2.extras import Json, RealDictCursor
from frontend.deps import get_menu_context, require_menu, templates
from frontend.modules.bank_transfer import BANK_CODE_MAP, remove_accents as _remove_accents
from frontend.modules.invoices.companies import (
    TaxLookupError,
    get_company,
    list_companies,
    refresh_company_from_tax,
    lookup_tax_code,
    save_company,
    normalize_tax_code,
    update_company,
    delete_company,
)
from frontend.modules.invoices.debt_service import adjust_company_debt
from frontend.modules.invoices.export import (
    CRANE_BY_SHIFT_SERVICE, DEFAULT_SERVICE_NAME, REMOVED_INVOICE_SERVICES,
    build_bkav_invoice, build_saved_invoice_workbook, cargo_description_from_service,
    format_vat_percent, format_vat_rate, is_crane_by_shift_service,
    parse_payment_method, parse_vat_percent, parse_vat_rate,
)
from frontend.modules.invoices.bkav_push import (
    BkavUncertainError, is_configured as bkav_is_configured,
    push_invoice as push_invoice_to_bkav, push_state as bkav_push_state,
    push_form_invoice as push_form_invoice_to_bkav,
    push_saved_invoice as push_saved_invoice_to_bkav,
    saved_invoice_push_state as saved_bkav_push_state,
)
from frontend.modules.invoices.bkav_sync import (
    existing_imports as bkav_existing_imports,
    fetch_invoices as fetch_bkav_invoices,
    import_invoices as import_bkav_invoices,
    invoice_vehicle_groups as bkav_invoice_vehicle_groups,
    vehicle_map as bkav_vehicle_map,
    get_or_fetch_bkav_invoice_files,
    stored_bkav_invoice_image_url,
    delete_stored_bkav_invoice_files,
    _fuel_price_for_date,
)
from frontend.modules.transport.catalog import KINDS as CATALOG_KINDS, list_items as list_catalog_items, save_item as save_catalog_item, delete_item as delete_catalog_item
from frontend.modules.transport.date_utils import month_bounds
from frontend.modules.transport.route_segments import parse_route_segments, route_totals
from frontend.modules.transport.invoice_forms import parse_invoice_form
from frontend.modules.transport.invoice_locations import normalize_invoice_location, split_invoice_destination_stops
from frontend.modules.transport.contract import (
    get_contract_context,
    render_contract_html,
    render_contract_pdf,
    render_contract_docx,
)
from frontend.modules.transport.hdnt_bbbg import (
    render_hdnt_docx,
    render_bbbg_docx,
    render_hdnt_bbbg_combined_docx,
    convert_docx_bytes_to_doc,
    render_hdnt_bbbg_html,
    compute_contract_no,
)
from frontend.modules.transport.trip_documents import (
    build_dlx_trip_workbook,
    render_dlx_document_html,
    render_dlx_document_docx,
)
from frontend.modules.administrative_divisions import (
    list_provinces as list_administrative_provinces,
)
from frontend.modules.zalo.trip_requests import (
    confirm_invoice_not_created, get_trip_request,
    register_trip_request, send_invoice_notice, send_saved_invoice_to_zalo,
)
from backend.services.database import db
from backend.ai.handwriting_ocr import extract_trip_candidates_from_document
from backend.services.google_maps import (
    compute_routes_api as _gmaps_routes_distance,
    compute_directions_api as _gmaps_directions_distance,
    driving_distance_km as _gmaps_driving_distance,
    geocode_address_google,
    reverse_geocode_google,
    check_google_maps_services_status,
)

logger = logging.getLogger(__name__)





router = APIRouter(prefix="/vehicles")
from frontend.modules.transport.catalog_router import catalog_router

PER_PAGE = 20
SCAN_REVIEW_TTL = 30 * 60
SCAN_REVIEW_DIR = Path(__file__).parent.parent / ".runtime" / "cargo_scan_reviews"
OCR_SAMPLE_DIR = Path(__file__).parent.parent / ".runtime" / "cargo_ocr_samples"
_SCAN_REVIEWS: dict[str, dict] = {}
_TRIP_DATE_RE = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"(?:[T ](?P<hour>\d{2}):(?P<minute>\d{2})"
    r"(?::(?P<second>\d{2})(?:\.(?P<fraction>\d{1,6}))?)?)?",
    re.ASCII,
)
_INVOICE_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z", re.ASCII)
_TRIP_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?(?:[eE][+-]?\d{1,3})?", re.ASCII)
_SOURCE_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?", re.ASCII)
_TRIP_NUMBER_LABELS = {
    "fuel_liters": "Số lít dầu",
    "fuel_price_per_liter": "Giá dầu",
    "freight_revenue": "Cước vận chuyển",
    "distance_km": "Quãng đường",
    "debt_amount": "Số tiền nợ",
}


class InvoiceCompanyInput(BaseModel):
    tax_code: str
    name: str
    address: str
    email: str = ""
    phone: str = ""


class BkavSyncInput(BaseModel):
    vehicle_id: int
    month: str
    company_id: int | None = None
    guids: list[str] = []
    payment_status: str = "debt"


def _scan_review_path(token: str) -> Path:
    safe_token = "".join(ch for ch in token if ch.isalnum() or ch in ("-", "_"))
    return SCAN_REVIEW_DIR / f"{safe_token}.json"


def _save_scan_review(token: str, data: dict):
    _SCAN_REVIEWS[token] = data
    SCAN_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    _scan_review_path(token).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _load_scan_review(token: str) -> dict | None:
    data = _SCAN_REVIEWS.get(token)
    if data:
        return data
    path = _scan_review_path(token)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if time.time() - data.get("created_at", 0) > SCAN_REVIEW_TTL:
        path.unlink(missing_ok=True)
        return None
    _SCAN_REVIEWS[token] = data
    return data


def _delete_scan_review(token: str):
    _SCAN_REVIEWS.pop(token, None)
    _scan_review_path(token).unlink(missing_ok=True)


def get_cargo_trips(vehicle_id: int | None = None, limit: int = 50, offset: int = 0,
                    company_id: int | None = None,
                    payment_status: str | None = None,
                    start: date | None = None, end: date | None = None,
                    seller_company_id: int | None = None) -> list[dict]:
    where_clauses = []
    params: list = []
    if vehicle_id is not None:
        where_clauses.append("trip.vehicle_id = %s")
        params.append(vehicle_id)
    if company_id is not None:
        where_clauses.append("trip.company_id = %s")
        params.append(company_id)
    if seller_company_id is not None:
        where_clauses.append("trip.seller_company_id = %s")
        params.append(seller_company_id)
    if start is not None and end is not None:
        where_clauses.append("trip.invoice_date >= %s AND trip.invoice_date < %s")
        params.extend((start, end))
    if payment_status:
        where_clauses.append("trip.payment_status = %s")
        params.append(payment_status)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.extend([limit, offset])
    query = f"""
        SELECT trip.*,
               COALESCE(trip.plate_number, v.plate_number, '') AS plate_number,
               company.name AS company_name,
               seller_company.name AS seller_company_name
        FROM cargo_trips AS trip
        LEFT JOIN plate_whitelist AS v ON v.id = trip.vehicle_id
        LEFT JOIN invoice_companies AS company ON company.id = trip.company_id
        LEFT JOIN invoice_companies AS seller_company ON seller_company.id = trip.seller_company_id
        {where_sql}
        ORDER BY trip.invoice_date DESC NULLS LAST, trip.departure_time DESC
        LIMIT %s OFFSET %s
    """
    return db._execute_query(query, tuple(params), fetchall=True, commit=False) or []


def count_cargo_trips(vehicle_id: int | None = None,
                      company_id: int | None = None,
                      payment_status: str | None = None,
                      start: date | None = None, end: date | None = None,
                      seller_company_id: int | None = None) -> int:
    where_clauses = []
    params: list = []
    if vehicle_id is not None:
        where_clauses.append("vehicle_id = %s")
        params.append(vehicle_id)
    if company_id is not None:
        where_clauses.append("company_id = %s")
        params.append(company_id)
    if seller_company_id is not None:
        where_clauses.append("seller_company_id = %s")
        params.append(seller_company_id)
    if start is not None and end is not None:
        where_clauses.append("invoice_date >= %s AND invoice_date < %s")
        params.extend((start, end))
    if payment_status:
        where_clauses.append("payment_status = %s")
        params.append(payment_status)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    row = db._execute_query(
        f"SELECT COUNT(*) FROM cargo_trips {where_sql}",
        tuple(params), fetch=True, commit=False
    )
    return row["count"] if row else 0


def _history_conditions(alias: str, *, invoice: bool = False, vehicle_id=None,
                        company_id=None, payment_status=None, start=None, end=None,
                        seller_company_id=None, document_query=None):
    clauses, params = [], []
    if vehicle_id is not None:
        if invoice:
            clauses.append(f"EXISTS (SELECT 1 FROM public.bkav_invoice_lines line WHERE line.invoice_guid = {alias}.invoice_guid AND line.vehicle_id = %s)")
        else:
            clauses.append(f"{alias}.vehicle_id = %s")
        params.append(vehicle_id)
    for column, value in (("buyer_company_id" if invoice else "company_id", company_id),
                          ("seller_company_id", seller_company_id),
                          ("payment_status", payment_status)):
        if value is not None:
            clauses.append(f"{alias}.{column} = %s")
            params.append(value)
    if start is not None and end is not None:
        clauses.append(f"{alias}.invoice_date >= %s AND {alias}.invoice_date < %s")
        params.extend((start, end))
    if document_query:
        like = f"%{document_query}%"
        if invoice:
            clauses.append(
                f"(LPAD({alias}.invoice_number::text, 8, '0') ILIKE %s "
                f"OR {alias}.invoice_number::text ILIKE %s "
                f"OR (COALESCE({alias}.invoice_serial, '') || '-' || "
                f"LPAD({alias}.invoice_number::text, 8, '0')) ILIKE %s)"
            )
            params.extend((like, like, like))
        else:
            clauses.append(f"COALESCE({alias}.document_number, '') ILIKE %s")
            params.append(like)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def count_cargo_history_groups(**filters) -> int:
    trip_where, trip_params = _history_conditions("trip", **filters)
    invoice_where, invoice_params = _history_conditions("invoice", invoice=True, **filters)
    row = db._execute_query(f"""
        SELECT
            (SELECT COUNT(*) FROM public.cargo_trips trip {trip_where}) +
            (SELECT COUNT(*) FROM public.bkav_invoices invoice {invoice_where}) AS count
    """, tuple([*trip_params, *invoice_params]), fetch=True, commit=False)
    return row["count"] if row else 0


def get_cargo_history_groups(*, limit: int, offset: int, **filters) -> list[dict]:
    """Return one history row per manual trip or source BKAV invoice."""
    trip_where, trip_params = _history_conditions("trip", **filters)
    invoice_where, invoice_params = _history_conditions("invoice", invoice=True, **filters)
    page = db._execute_query(f"""
        SELECT kind, record_key, invoice_date, departure_time FROM (
            SELECT 'trip' AS kind, trip.id::text AS record_key,
                   trip.invoice_date, trip.departure_time
            FROM public.cargo_trips trip {trip_where}
            UNION ALL
            SELECT 'invoice' AS kind, invoice.invoice_guid::text AS record_key,
                   invoice.invoice_date, NULL::timestamptz AS departure_time
            FROM public.bkav_invoices invoice {invoice_where}
        ) history
        ORDER BY invoice_date DESC NULLS LAST, departure_time DESC NULLS LAST,
                 record_key DESC
        LIMIT %s OFFSET %s
    """, tuple([*trip_params, *invoice_params, limit, offset]), fetchall=True, commit=False) or []
    if not page:
        return []
    trip_ids = [int(row["record_key"]) for row in page if row["kind"] == "trip"]
    guids = [row["record_key"] for row in page if row["kind"] == "invoice"]
    trip_rows = db._execute_query("""
        SELECT trip.*, COALESCE(trip.plate_number, vehicle.plate_number, '') AS plate_number,
               company.name AS company_name, seller.name AS seller_company_name
        FROM public.cargo_trips trip
        LEFT JOIN public.plate_whitelist vehicle ON vehicle.id = trip.vehicle_id
        LEFT JOIN public.invoice_companies company ON company.id = trip.company_id
        LEFT JOIN public.invoice_companies seller ON seller.id = trip.seller_company_id
        WHERE trip.id = ANY(%s)
    """, (trip_ids,), fetchall=True, commit=False) if trip_ids else []
    invoice_rows = db._execute_query("""
        SELECT invoice.*, buyer.name AS company_name, seller.name AS seller_company_name
        FROM public.bkav_invoices invoice
        LEFT JOIN public.invoice_companies buyer ON buyer.id = invoice.buyer_company_id
        LEFT JOIN public.invoice_companies seller ON seller.id = invoice.seller_company_id
        WHERE invoice.invoice_guid = ANY(%s::uuid[])
    """, (guids,), fetchall=True, commit=False) if guids else []
    line_rows = db._execute_query("""
        SELECT line.*, vehicle.plate_number AS plate_number
        FROM public.bkav_invoice_lines line
        LEFT JOIN public.plate_whitelist vehicle ON vehicle.id = line.vehicle_id
        WHERE line.invoice_guid = ANY(%s::uuid[])
        ORDER BY line.invoice_guid, line.line_number
    """, (guids,), fetchall=True, commit=False) if guids else []
    lines_by_guid = {}
    for line in line_rows or []:
        lines_by_guid.setdefault(str(line["invoice_guid"]), []).append(dict(line))
    by_key = {}
    for trip in trip_rows or []:
        entry = dict(trip)
        entry["invoice_guid"] = None
        entry["invoice_lines"] = []
        entry["selection_key"] = f"trip:{entry['id']}"
        by_key[("trip", str(entry["id"]))] = entry
    for invoice in invoice_rows or []:
        guid = str(invoice["invoice_guid"])
        lines = lines_by_guid.get(guid, [])
        distance_values = [Decimal(str(line["distance_km"])) for line in lines if line.get("distance_km") is not None]
        fuel_values = [Decimal(str(line["fuel_cost_total"])) for line in lines if line.get("fuel_cost_total") is not None]
        entry = {
            "id": guid, "invoice_guid": guid, "selection_key": f"invoice:{guid}",
            "invoice_lines": lines, "invoice_date": invoice["invoice_date"],
            "document_number": str(invoice["invoice_number"]).zfill(8),
            "company_name": invoice["company_name"], "service_name": invoice["service_name"],
            "seller_company_name": invoice["seller_company_name"],
            "freight_revenue": invoice["subtotal"], "source_revenue_gross": invoice["gross"],
            "vat_rate": invoice["vat_rate"], "payment_method": invoice["payment_method"],
            "payment_status": invoice["payment_status"], "debt_amount": invoice["debt_amount"],
            "fuel_cost_total": sum(fuel_values, Decimal(0)) if fuel_values else None,
            "distance_km": sum(distance_values, Decimal(0)) if distance_values else None,
            "plate_number": "", "origin": lines[0]["origin"] if lines else "",
            "destination": lines[-1]["destination"] if lines else "",
            "fuel_price_per_liter": None,
        }
        by_key[("invoice", guid)] = entry
    return [by_key[(row["kind"], row["record_key"])] for row in page
            if (row["kind"], row["record_key"]) in by_key]

def get_default_seller_company_id(vehicle_id: int | None = None, trip: dict | None = None, current_user: dict | None = None) -> int | None:
    """
    Get the default issuing company (công ty lập hóa đơn).
    Default is the first company that the person (driver or user) works for.
    """
    if trip and trip.get("seller_company_id"):
        return trip["seller_company_id"]

    driver_name = trip.get("driver_name") if trip else None
    driver_code = None
    if vehicle_id:
        driver_row = db._execute_query(
            "SELECT driver_code, full_name FROM transport_drivers WHERE vehicle_id = %s LIMIT 1",
            (vehicle_id,), fetch=True, commit=False
        )
        if driver_row:
            driver_code = driver_row.get("driver_code")
            if not driver_name:
                driver_name = driver_row.get("full_name")

    matched_user = None
    if driver_code:
        matched_user = db._execute_query(
            "SELECT id, username, full_name, company_ids FROM users WHERE username = %s LIMIT 1",
            (driver_code,), fetch=True, commit=False
        )
    if not matched_user and driver_name:
        matched_user = db._execute_query(
            "SELECT id, username, full_name, company_ids FROM users WHERE LOWER(full_name) = LOWER(%s) OR LOWER(username) = LOWER(%s) LIMIT 1",
            (str(driver_name).strip(), str(driver_name).strip()), fetch=True, commit=False
        )
    if matched_user and matched_user.get("company_ids"):
        cids = [int(cid) for cid in matched_user["company_ids"] if cid is not None]
        if cids:
            return cids[0]

    if current_user and current_user.get("id"):
        user_row = db._execute_query(
            "SELECT company_ids FROM users WHERE id = %s",
            (current_user["id"],), fetch=True, commit=False
        )
        if user_row and user_row.get("company_ids"):
            cids = [int(cid) for cid in user_row["company_ids"] if cid is not None]
            if cids:
                return cids[0]

    return None


def add_cargo_trip(vehicle_id: int, plate_number: str, departure_time,
                   return_time=None, invoice_date=None, company_id=None,
                   origin=None, destination=None,
                   fuel_liters=None, fuel_price_per_liter=None,
                   freight_revenue=None, distance_km=None, vat_rate=None,
                   payment_method=None, payment_status="paid", debt_amount=0,
                   service_name=DEFAULT_SERVICE_NAME, document_number=None, driver_name=None,
                   cargo_description=None, trip_quantity=1, cargo_weight_tons=None,
                   trip_wage=None, trip_other_cost=None, trip_note=None, odometer_end=None,
                   vehicle_owner_type=None, outsourced_freight=None, seller_company_id=None,
                   route_segments=None) -> int | None:
    query = """
        INSERT INTO cargo_trips
            (vehicle_id, plate_number, departure_time, return_time, invoice_date, company_id,
             origin, destination,
             fuel_liters, fuel_price_per_liter,
             freight_revenue, distance_km, vat_rate, payment_method,
             payment_status, debt_amount, service_name,
             document_number, driver_name, cargo_description, trip_quantity, cargo_weight_tons,
             trip_wage, trip_other_cost, trip_note, odometer_end, vehicle_owner_type, outsourced_freight, seller_company_id,
             route_segments)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """
    trip_id = db._execute_query(
        query,
        (vehicle_id, plate_number, departure_time, return_time, invoice_date, company_id,
         origin, destination,
         fuel_liters, fuel_price_per_liter,
         freight_revenue, distance_km, vat_rate, payment_method,
         payment_status, debt_amount, service_name,
         document_number, driver_name, cargo_description, trip_quantity, cargo_weight_tons,
         trip_wage, trip_other_cost, trip_note, odometer_end, vehicle_owner_type, outsourced_freight, seller_company_id,
         Json(route_segments or [])),
        commit=True, return_id=True
    )
    if trip_id is None:
        logger.error("Cargo trip insert returned no ID for vehicle %s", vehicle_id)
    elif company_id and payment_status == "debt":
        try:
            d_amt = Decimal(str(debt_amount or 0))
            if d_amt > 0:
                adjust_company_debt(
                    company_id=company_id,
                    amount=d_amt,
                    owner_company_id=seller_company_id,
                    debt_type="trip_debt",
                    note=f"Nợ chuyến hàng {document_number or trip_id}",
                    reference_doc=str(trip_id),
                )
        except Exception as exc:
            logger.warning("Could not adjust debt for new trip: %s", exc)
    return trip_id


def update_cargo_trip(trip_id: int, *, vehicle_id, invoice_date, company_id, origin,
                      destination, fuel_liters, fuel_price_per_liter,
                      freight_revenue, distance_km, vat_rate,
                      payment_method, service_name, payment_status="paid", debt_amount=0,
                      document_number=None, driver_name=None, cargo_description=None,
                      trip_quantity=1, cargo_weight_tons=None, trip_wage=None,
                      trip_other_cost=None, trip_note=None, odometer_end=None,
                      vehicle_owner_type=None, outsourced_freight=None, seller_company_id=None,
                      route_segments=None) -> bool:
    old_trip = get_cargo_trip(trip_id)
    updated = db._execute_query(
        """UPDATE cargo_trips SET vehicle_id = %s, invoice_date = %s, company_id = %s,
           origin = %s, destination = %s, fuel_liters = %s,
           fuel_price_per_liter = %s, freight_revenue = %s,
           distance_km = %s, vat_rate = %s, payment_method = %s,
           payment_status = %s, debt_amount = %s, service_name = %s,
           document_number = %s, driver_name = %s, cargo_description = %s,
           trip_quantity = %s, cargo_weight_tons = %s, trip_wage = %s,
           trip_other_cost = %s, trip_note = %s, odometer_end = %s,
           vehicle_owner_type = %s, outsourced_freight = %s,
           seller_company_id = %s, route_segments = %s, updated_at = NOW() WHERE id = %s""",
        (vehicle_id, invoice_date, company_id, origin, destination, fuel_liters,
         fuel_price_per_liter, freight_revenue, distance_km,
         vat_rate, payment_method, payment_status, debt_amount, service_name,
         document_number, driver_name, cargo_description, trip_quantity, cargo_weight_tons,
         trip_wage, trip_other_cost, trip_note, odometer_end, vehicle_owner_type, outsourced_freight,
         seller_company_id, Json(route_segments or []), trip_id),
        commit=True,
    )
    if updated and old_trip:
        try:
            old_debt = Decimal(str(old_trip.get("debt_amount") or 0)) if old_trip.get("payment_status") == "debt" else Decimal("0")
            new_debt = Decimal(str(debt_amount or 0)) if payment_status == "debt" else Decimal("0")
            old_cid = old_trip.get("company_id")
            new_cid = company_id
            old_owner_id = old_trip.get("seller_company_id")
            new_owner_id = seller_company_id
            if old_cid == new_cid and old_owner_id == new_owner_id:
                diff = new_debt - old_debt
                if diff != 0 and new_cid:
                    adjust_company_debt(new_cid, diff, owner_company_id=new_owner_id, debt_type="trip_debt", note=f"Điều chỉnh nợ chuyến {document_number or trip_id}", reference_doc=str(trip_id))
            else:
                if old_debt > 0 and old_cid:
                    adjust_company_debt(old_cid, -old_debt, owner_company_id=old_owner_id, debt_type="trip_debt", note=f"Chuyển nợ chuyến {document_number or trip_id}", reference_doc=str(trip_id))
                if new_debt > 0 and new_cid:
                    adjust_company_debt(new_cid, new_debt, owner_company_id=new_owner_id, debt_type="trip_debt", note=f"Nợ chuyến {document_number or trip_id}", reference_doc=str(trip_id))
        except Exception as exc:
            logger.warning("Could not adjust debt on trip update: %s", exc)
    return updated


def get_cargo_trip(trip_id: int) -> dict | None:
    return db._execute_query("SELECT * FROM cargo_trips WHERE id = %s", (trip_id,), fetch=True, commit=False)


def list_cargo_types() -> list[str]:
    return [item["name"] for item in list_catalog_items("cargo_type")]


def _catalog_back_url(value: str | None) -> str:
    if not value or value == "/admin":
        return "/admin/vehicles"
    return value if value.startswith("/admin/") and not value.startswith("//") else "/admin/vehicles"


def _catalog_redirect(tab: str, back: str | None, *, message: str = "", error: str = "") -> RedirectResponse:
    params = urllib.parse.urlencode({"tab": tab, "back": _catalog_back_url(back), "message": message, "error": error})
    return RedirectResponse(f"/admin/transport/catalog?{params}", status_code=303)


@router.get("/choose")
async def choose_transport_vehicle(
    user: dict = Depends(require_menu("vehicles")),
):
    return RedirectResponse("/admin/vehicles", status_code=303)


def format_document_number(val: str | int | None) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if s.isdigit():
        return f"{int(s):08d}"
    return s


def get_next_document_number() -> str:
    row = db._execute_query(
        """SELECT COALESCE(
            MAX(NULLIF(regexp_replace(document_number, '[^0-9]', '', 'g'), '')::bigint),
            0
        ) AS max_num FROM cargo_trips""",
        fetch=True,
        commit=False,
    )
    max_num = int(row["max_num"]) if row and row.get("max_num") is not None else 0
    return f"{max_num + 1:08d}"


def ensure_invoice_bill_number(trip_id: int, vehicle_id: int) -> int | None:
    trip = get_cargo_trip(trip_id)
    bill_num = None
    if trip and trip.get("document_number"):
        digits = re.sub(r"[^0-9]", "", str(trip["document_number"]))
        if digits:
            bill_num = int(digits)
    if bill_num is not None:
        db._execute_query(
            "UPDATE public.cargo_trips SET invoice_bill_number = %s WHERE id = %s AND vehicle_id = %s",
            (bill_num, trip_id, vehicle_id),
            commit=True,
        )
        return bill_num

    row = db._execute_query(
        """UPDATE public.cargo_trips
           SET invoice_bill_number = COALESCE(
               invoice_bill_number, nextval('public.cargo_invoice_bill_number_seq')
           )
           WHERE id = %s AND vehicle_id = %s
           RETURNING invoice_bill_number""",
        (trip_id, vehicle_id),
        fetch=True,
        commit=True,
    )
    return row["invoice_bill_number"] if row else None


def delete_cargo_trip(trip_id: int, vehicle_id: int | None = None) -> bool:
    trip = get_cargo_trip(trip_id)
    if vehicle_id is not None:
        deleted = db._execute_query(
            "DELETE FROM public.cargo_trips WHERE id = %s AND vehicle_id = %s",
            (trip_id, vehicle_id),
            commit=True,
        )
    else:
        deleted = db._execute_query(
            "DELETE FROM public.cargo_trips WHERE id = %s",
            (trip_id,),
            commit=True,
        )
    if deleted and trip and trip.get("payment_status") == "debt" and trip.get("company_id"):
        try:
            d_amt = Decimal(str(trip.get("debt_amount") or 0))
            if d_amt > 0:
                adjust_company_debt(
                    company_id=trip["company_id"],
                    amount=-d_amt,
                    owner_company_id=trip.get("seller_company_id"),
                    debt_type="trip_debt",
                    note=f"Hủy nợ do xóa chuyến {trip.get('document_number') or trip_id}",
                    reference_doc=str(trip_id),
                )
        except Exception as exc:
            logger.warning("Could not adjust debt on trip delete: %s", exc)
    return deleted


def bulk_delete_cargo_trips(trip_ids: list[int], vehicle_id: int | None = None) -> int:
    valid_ids = [int(i) for i in dict.fromkeys(trip_ids) if str(i).isdigit()]
    if not valid_ids:
        return 0
    placeholders = ", ".join(["%s"] * len(valid_ids))
    if vehicle_id is not None:
        where_sql = f"vehicle_id = %s AND id IN ({placeholders})"
        params = [vehicle_id] + valid_ids
    else:
        where_sql = f"id IN ({placeholders})"
        params = valid_ids
    rows = db._execute_query(
        f"DELETE FROM public.cargo_trips WHERE {where_sql} RETURNING id",
        tuple(params),
        fetchall=True,
        commit=True,
    )
    return len(rows) if rows else 0


def add_reviewed_cargo_trips(vehicle_id: int, plate_number: str, trips: list[dict], seller_company_id: int | None = None) -> bool:
    if not trips:
        return True
    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    columns = (
        "vehicle_id", "plate_number", "invoice_date", "departure_time", "return_time", "origin",
        "destination", "fuel_liters", "fuel_price_per_liter", "freight_revenue", "distance_km", "driver_name", "seller_company_id",
    )
    placeholders = ", ".join(["(" + ", ".join(["%s"] * len(columns)) + ")"] * len(trips))
    values = tuple(
        value
        for trip in trips
        for value in (vehicle_id, plate_number, trip["departure_time"].date(), *(trip[column] for column in columns[3:-2]), vehicle["owner_name"], seller_company_id)
    )
    query = "INSERT INTO cargo_trips (" + ", ".join(columns) + ") VALUES " + placeholders + " RETURNING id"
    rows = db._execute_query(query, values, fetchall=True, commit=True)
    if not isinstance(rows, list) or len(rows) != len(trips):
        logger.error(
            "Cargo trip bulk insert returned %s IDs for %s rows on vehicle %s",
            len(rows) if isinstance(rows, list) else 0, len(trips), vehicle_id,
        )
        return False
    return True


def _data_uri_to_png(data_uri: str) -> bytes | None:
    if not data_uri or "," not in data_uri:
        return None
    header, payload = data_uri.split(",", 1)
    if "base64" not in header:
        return None
    return base64.b64decode(payload)


def _persist_ocr_sample(
    token: str,
    vehicle_id: int,
    row_index: str,
    candidate: dict,
    corrected: dict,
    corrected_line_texts: dict[str, str] | None = None,
):
    sample_id = f"{int(time.time())}_{token[:8]}_{row_index}"
    sample_dir = OCR_SAMPLE_DIR / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)

    source_lines = []
    for line_pos, line in enumerate(candidate.get("source_lines", []), start=1):
        line_copy = dict(line)
        line_key = str(line_pos - 1)
        reviewed_text = (corrected_line_texts or {}).get(line_key)
        if reviewed_text is not None:
            line_copy["reviewed_text"] = reviewed_text.strip()
            line_copy["reviewed_text_changed"] = reviewed_text.strip() != str(line.get("text") or "").strip()
        crop_data = line_copy.pop("crop_image", "")
        crop_bytes = _data_uri_to_png(crop_data)
        if crop_bytes:
            crop_name = f"line_{line_pos:02d}.png"
            (sample_dir / crop_name).write_bytes(crop_bytes)
            line_copy["crop_file"] = crop_name
        source_lines.append(line_copy)

    ocr_public = {
        "departure_time": candidate.get("departure_time") or "",
        "return_time": candidate.get("return_time") or "",
        "origin": candidate.get("origin") or "",
        "destination": candidate.get("destination") or "",
        "fuel_liters": "" if candidate.get("fuel_liters") is None else str(candidate.get("fuel_liters")),
        "freight_revenue": "" if candidate.get("freight_revenue") is None else str(candidate.get("freight_revenue")),
        "distance_km": "" if candidate.get("distance_km") is None else str(candidate.get("distance_km")),
    }
    changed_fields = [
        field for field, value in corrected.items()
        if str(value or "").strip() != str(ocr_public.get(field) or "").strip()
    ]

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "vehicle_id": vehicle_id,
        "row_index": row_index,
        "changed_fields": changed_fields,
        "ocr": {
            "parsed": ocr_public,
            "candidate": {k: v for k, v in candidate.items() if k not in ("source_lines",)},
            "source_lines": source_lines,
        },
        "corrected": corrected,
    }
    (sample_dir / "sample.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _cleanup_scan_reviews():
    now = time.time()
    stale = [token for token, data in _SCAN_REVIEWS.items() if now - data.get("created_at", 0) > SCAN_REVIEW_TTL]
    for token in stale:
        _delete_scan_review(token)
    if SCAN_REVIEW_DIR.exists():
        for path in SCAN_REVIEW_DIR.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if now - data.get("created_at", 0) > SCAN_REVIEW_TTL:
                path.unlink(missing_ok=True)


def _fmt_currency(value) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}".replace(",", ".") + " ₫"


def _num(v):
    return float(v.replace(",", ".")) if v and v.strip() else None


def _dt(v):
    return datetime.fromisoformat(v) if v and v.strip() else None


def _valid_trip_datetime(value: str) -> bool:
    match = _TRIP_DATE_RE.fullmatch(value)
    if match is None:
        return False
    year, month, day = (int(match[name]) for name in ("year", "month", "day"))
    if not (1 <= year <= 9999 and 1 <= month <= 12):
        return False
    if not (1 <= day <= calendar.monthrange(year, month)[1]):
        return False
    if match["hour"] is not None:
        return (
            0 <= int(match["hour"]) <= 23
            and 0 <= int(match["minute"]) <= 59
            and (match["second"] is None or 0 <= int(match["second"]) <= 59)
        )
    return True


def _normalise_trip_fields(raw: dict[str, str | None]) -> tuple[dict | None, str | None]:
    values = {field: str(raw.get(field) or "").strip() for field in (
        "departure_time", "return_time", "origin", "destination", *_TRIP_NUMBER_LABELS,
    )}
    if not values["departure_time"] or not _valid_trip_datetime(values["departure_time"]):
        return None, "Ngày xuất phát không hợp lệ."
    if values["return_time"] and not _valid_trip_datetime(values["return_time"]):
        return None, "Ngày về không hợp lệ."
    numbers = {}
    for field, label in _TRIP_NUMBER_LABELS.items():
        value = values[field]
        if not value:
            numbers[field] = None
            continue
        if len(value) > 40 or _TRIP_NUMBER_RE.fullmatch(value) is None:
            return None, f"{label} phải là số không âm hợp lệ."
        number = _num(value)
        if not math.isfinite(number) or number > 1_000_000_000_000:
            return None, f"{label} phải là số không âm hợp lệ."
        numbers[field] = number

    departure = datetime.fromisoformat(values["departure_time"])
    returned = _dt(values["return_time"])
    if returned is not None and returned < departure:
        return None, "Ngày về phải từ ngày xuất phát trở đi."
    return {
        "departure_time": departure,
        "return_time": returned,
        "origin": values["origin"] or None,
        "destination": values["destination"] or None,
        **numbers,
    }, None


def _optional_transport_number(raw: dict, key: str, label: str, limit: int) -> Decimal | None:
    value = str(raw.get(key) or "").strip().replace(",", ".")
    if not value:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} không hợp lệ.") from exc
    if not number.is_finite() or number < 0 or number >= limit:
        raise ValueError(f"{label} không hợp lệ.")
    return number


def _normalise_invoice_trip_fields(raw: dict[str, str | None], *, generate_document_number: bool = True,
                                   preserve_paid_debt: bool = False,
                                   format_document: bool = True) -> tuple[dict | None, str | None]:
    invoice_date = str(raw.get("invoice_date") or "").strip()
    if not _INVOICE_DATE_RE.fullmatch(invoice_date):
        return None, "Ngày hóa đơn không hợp lệ."
    try:
        invoice_day = date.fromisoformat(invoice_date)
    except ValueError:
        return None, "Ngày hóa đơn không hợp lệ."
    trip_fields, error = _normalise_trip_fields({
        **raw,
        "departure_time": invoice_date,
        "return_time": None,
    })
    if error:
        return None, error
    try:
        vat_rate = parse_vat_percent(raw.get("vat_percent"))
        payment_method = parse_payment_method(raw.get("payment_method") or "CK", allow_unknown=True)
    except ValueError as exc:
        return None, str(exc)

    transport_text = {
        "document_number": 80, "driver_name": 160, "cargo_description": 200,
        "trip_note": 4000,
    }
    for key, limit in transport_text.items():
        value = str(raw.get(key) or "").strip()
        if len(value) > limit:
            return None, f"{key} quá dài."
        if key == "document_number":
            value = (format_document_number(value) if format_document else value) if value else (get_next_document_number() if generate_document_number else None)
        trip_fields[key] = value or None
    try:
        for key, label, limit in (
            ("trip_quantity", "Số chuyến", 10**6),
            ("cargo_weight_tons", "Khối lượng", 10**9),
            ("trip_wage", "Lương chuyến", 10**14),
            ("trip_other_cost", "Chi phí chuyến", 10**14),
            ("odometer_end", "Km cuối đồng hồ", 10**13),
            ("outsourced_freight", "Cước trả xe thuê ngoài", 10**14),
        ):
            trip_fields[key] = _optional_transport_number(raw, key, label, limit)
    except ValueError as exc:
        return None, str(exc)
    if trip_fields["trip_quantity"] is None:
        trip_fields["trip_quantity"] = Decimal("1")
    owner_type = str(raw.get("vehicle_owner_type") or "").strip()
    if owner_type not in ("", "company", "external"):
        return None, "Loại chủ xe không hợp lệ."
    trip_fields["vehicle_owner_type"] = owner_type or None

    payment_status = str(raw.get("payment_status") or "paid").strip().lower()
    if payment_status not in ("paid", "debt"):
        payment_status = "paid"

    if payment_status == "debt":
        debt_amount = trip_fields.get("debt_amount")
        if debt_amount is None:
            debt_amount = (trip_fields.get("freight_revenue") or 0) * (1 + vat_rate)
        trip_fields["debt_amount"] = debt_amount
    else:
        trip_fields["debt_amount"] = trip_fields.get("debt_amount") if preserve_paid_debt else 0

    trip_fields["payment_status"] = payment_status
    trip_fields["invoice_date"] = invoice_day
    trip_fields["vat_rate"] = vat_rate
    trip_fields["payment_method"] = payment_method
    return trip_fields, None


def _crane_transport_tax_rule(invoice_day: date) -> dict | None:
    return db._execute_query(
        """SELECT vat_rate, source_ref, source_url, valid_to
           FROM public.invoice_tax_rules
           WHERE service_code = %s AND valid_from <= %s AND valid_to >= %s
           ORDER BY valid_from DESC LIMIT 1""",
        ("CRANE_TRANSPORT_DOMESTIC", invoice_day, invoice_day),
        fetch=True,
        commit=False,
    )


def _vat_rule_response(invoice_day: date) -> dict:
    rule = _crane_transport_tax_rule(invoice_day)
    if not rule:
        return {"ok": True, "vat_rate": None, "source_ref": None, "source_url": None}
    return {
        "ok": True,
        "vat_rate": format_vat_rate(rule["vat_rate"]),
        "source_ref": rule["source_ref"],
        "source_url": rule["source_url"],
        "valid_to": rule["valid_to"].isoformat(),
    }


def _trip_form_error(message: str, back_url: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='vi'><meta charset='utf-8'>"
        "<title>Dữ liệu chuyến hàng không hợp lệ</title>"
        "<body style='font:16px system-ui;max-width:40rem;margin:3rem auto;padding:1rem'>"
        "<h1>Chưa lưu chuyến hàng</h1>"
        f"<p>{escape(message)}</p>"
        "<button type='button' onclick='history.back()'>Quay lại sửa</button> "
        f"<a href='{escape(back_url, quote=True)}'>Mở lại biểu mẫu</a>"
        "</body></html>",
        status_code=422,
    )


def _invoice_file_response(trip: dict, content: bytes, company: dict | None = None) -> Response:
    tax_code = str(company.get("tax_code") or "").strip() if company else ""
    if not tax_code and trip.get("company_id"):
        comp = get_company(trip["company_id"])
        if comp:
            tax_code = str(comp.get("tax_code") or "").strip()
    clean_tax_code = re.sub(r"[^0-9A-Za-z_-]", "", tax_code)
    if clean_tax_code:
        filename = f"FileImport_Bkav_BienSoXe {clean_tax_code}.xlsx"
    else:
        filename = "FileImport_Bkav_BienSoXe.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# Plus Code (Open Location Code) decoder — không cần package ngoài
# Ref: https://github.com/google/open-location-code
# ---------------------------------------------------------------------------
_OLC_ALPHABET = "23456789CFGHJMPQRVWX"
_OLC_SHORT_RE = re.compile(
    r"^([2-9A-HJ-NP-Z]{4,8}\+[2-9A-HJ-NP-Z]{0,2})"  # short plus code part
    r"(?:\s*,?\s*(.+))?$",
    re.IGNORECASE,
)
_OLC_FULL_RE = re.compile(
    r"^([2-9A-HJ-NP-Z]{8}\+[2-9A-HJ-NP-Z]{0,2}[0-9]*)$",
    re.IGNORECASE,
)


def _olc_decode_full(code: str) -> tuple[float, float] | None:
    """Decode a full Plus Code to (lat, lon) centre."""
    code = code.upper().replace("+", "")
    if len(code) < 8:
        return None
    PAIR_LEN = 8
    ALPHABET = _OLC_ALPHABET
    lat = -90.0
    lon = -180.0
    lat_step = 400.0
    lon_step = 400.0
    i = 0
    is_pair = True
    while i < min(len(code), PAIR_LEN):
        lat_digit = ALPHABET.find(code[i])
        lon_digit = ALPHABET.find(code[i + 1]) if i + 1 < len(code) else -1
        if lat_digit < 0 or lon_digit < 0:
            return None
        lat_step /= 20
        lon_step /= 20
        lat += lat_digit * lat_step
        lon += lon_digit * lon_step
        i += 2
    # grid refinement for characters after the +
    for ch in code[PAIR_LEN:]:
        digit = ALPHABET.find(ch)
        if digit < 0:
            return None
        lat_step /= 5
        lon_step /= 4
        row = digit // 4
        col = digit % 4
        lat += row * lat_step
        lon += col * lon_step
    return round(lat + lat_step / 2, 7), round(lon + lon_step / 2, 7)


def _extract_plus_code(q: str) -> tuple[str, str] | None:
    """
    Try to extract a Plus Code from an address string.
    Returns (plus_code_raw, reference_location) or None.
    E.g. "23Q5+9CQ Trí Quả, Bắc Ninh, Việt Nam" → ("23Q5+9CQ", "Trí Quả, Bắc Ninh, Việt Nam")
    """
    q = (q or "").strip()
    tokens = q.split()
    for i, tok in enumerate(tokens):
        if "+" not in tok:
            continue
        clean_tok = tok.strip(" ,;").upper()
        if "+" not in clean_tok:
            continue
        before_plus = clean_tok.split("+")[0]
        # Only handle valid Plus Code lengths (4, 6, 8 chars before +)
        if len(before_plus) not in (4, 6, 8):
            continue
        rest_parts = [t for j, t in enumerate(tokens) if j != i]
        ref = " ".join(rest_parts).strip(" ,;")
        return (clean_tok, ref)
    return None


async def _resolve_plus_code(code: str, ref_location: str) -> dict | None:
    """
    Resolve a Plus Code (short or full) to lat/lon.
    Short codes: 4 chars before '+' (missing 4 global chars) or 6 chars before '+' (missing 2 chars).
    """
    code = code.upper().strip(" ,;")
    before_plus = code.split("+")[0]
    label_text = f"{code} {ref_location}".strip() if ref_location else code

    if len(before_plus) == 8:
        # Full OLC — decode directly
        coords = _olc_decode_full(code.replace("+", ""))
        if coords:
            return {"lat": coords[0], "lon": coords[1], "label": label_text, "full_code": code}
        return None

    # Short code — need reference coordinates to recover full code
    if not ref_location:
        return None
    ref = await _geocode_location(ref_location)
    if not ref or ref.get("lat") is None:
        return None
    ref_lat = float(ref["lat"])
    ref_lon = float(ref["lon"])

    # Number of chars missing before '+'
    missing = 8 - len(before_plus)   # e.g. 8-4=4 missing, or 8-6=2 missing
    num_prefix_pairs = missing // 2   # 2 pairs or 1 pair

    try:
        ALPHABET = _OLC_ALPHABET
        adj_lat = ref_lat + 90.0
        adj_lon = ref_lon + 180.0
        lat_step = 400.0
        lon_step = 400.0
        prefix_chars = []
        for _ in range(num_prefix_pairs):
            lat_step /= 20
            lon_step /= 20
            lat_d = min(int(adj_lat / lat_step), 19)
            lon_d = min(int(adj_lon / lon_step), 19)
            prefix_chars.append(ALPHABET[lat_d])
            prefix_chars.append(ALPHABET[lon_d])
            adj_lat -= lat_d * lat_step
            adj_lon -= lon_d * lon_step

        short_body = code.replace("+", "").upper()
        full_code_body = "".join(prefix_chars) + short_body
        coords = _olc_decode_full(full_code_body)
        if coords:
            full_code = full_code_body[:8] + "+" + full_code_body[8:] if len(full_code_body) > 8 else full_code_body
            logger.info("Short Plus Code '%s' → full '%s', coords=(%s,%s)",
                        code, full_code, coords[0], coords[1])
            return {
                "lat": coords[0],
                "lon": coords[1],
                "label": label_text,
                "full_code": full_code,
            }
    except Exception as exc:
        logger.warning("Plus Code short recovery failed for '%s': %s", code, exc)
    return None


def _source_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = str(value).strip()
    if len(text) > 40 or _SOURCE_NUMBER_RE.fullmatch(text) is None:
        return None
    number = float(text)
    return number if math.isfinite(number) else None


def _coordinates(location: dict) -> tuple[float, float] | None:
    lat = _source_number(location.get("lat"))
    lon = _source_number(location.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


async def _geocode_location(q: str) -> dict | None:
    if not q or not q.strip():
        return None
    # --- Plus Code detection (e.g. "23Q5+9CQ Trí Quả, Bắc Ninh, Việt Nam") ---
    plus_code_info = _extract_plus_code(q)
    if plus_code_info:
        code, ref = plus_code_info
        result = await _resolve_plus_code(code, ref)
        if result and result.get("lat") is not None:
            logger.info("Plus Code '%s' resolved to lat=%s lon=%s", code, result["lat"], result["lon"])
            return result
        logger.warning("Plus Code '%s' could not be resolved, falling back to Google / Nominatim", code)
    # ---------------------------------------------------------------------------

    # Resolve addresses only through Google Maps.
    try:
        g_loc = await geocode_address_google(q)
        if g_loc and g_loc.get("lat") is not None and g_loc.get("lon") is not None:
            return g_loc
    except Exception as exc:
        logger.warning("Google Geocoding failed for '%s': %s", q, exc)

    return None


_SHORT_MAPS_HOSTS = {"goo.gl", "maps.app.goo.gl", "maps.app"}
_MAPS_URL_RE = re.compile(
    r"https://(?:google\.com|www\.google\.com|maps\.google\.com|goo\.gl|maps\.app\.goo\.gl|maps\.app)(?:[/?#][^\x00-\x20\x7f]*)?",
    re.IGNORECASE,
)


def _is_maps_url(value: str) -> bool:
    return _MAPS_URL_RE.fullmatch(value) is not None


async def _expand_maps_url(url: str) -> str | None:
    if not _is_maps_url(url):
        return None
    if urllib.parse.urlparse(url).hostname not in _SHORT_MAPS_HOSTS:
        return url
    async with httpx.AsyncClient(timeout=8) as client:
        for _ in range(4):
            response = await client.head(url, follow_redirects=False)
            if response.is_error:
                logger.warning("Google Maps short link request returned HTTP %s", response.status_code)
                response.raise_for_status()
            location = response.headers.get("location")
            if response.status_code not in (301, 302, 303, 307, 308) or not location:
                logger.warning("Google Maps short link did not provide a redirect")
                return None
            if location.startswith("//"):
                url = "https:" + location
            elif location.startswith(("http://", "https://")):
                url = location
            else:
                url = urllib.parse.urljoin(url, location)
            if not _is_maps_url(url):
                logger.warning("Google Maps short link redirected to an unsupported host")
                return None
            if urllib.parse.urlparse(url).hostname not in _SHORT_MAPS_HOSTS:
                return url
    logger.warning("Google Maps short link exceeded the redirect limit")
    return None


async def _resolve_location(value: str) -> dict | None:
    value = (value or "").strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        return await _geocode_location(value)
    url = await _expand_maps_url(value)
    if url is None:
        return None

    path_parts = urllib.parse.urlparse(url).path.split("/")
    name = ""
    if "dir" in path_parts:
        stops = []
        for part in path_parts[path_parts.index("dir") + 1:]:
            if part.startswith(("@", "data=", "am=")):
                break
            if part:
                stops.append(part)
        if stops:
            name = urllib.parse.unquote_plus(stops[-1]).strip(" ,")
    elif "place" in path_parts:
        parts = path_parts[path_parts.index("place") + 1:]
        if parts:
            name = urllib.parse.unquote_plus(parts[0]).strip(" ,")
    if name:
        embedded = re.findall(r"!1d(-?\d+(?:\.\d+)?)!2d(-?\d+(?:\.\d+)?)", url)
        if embedded:
            lon, lat = embedded[-1]
            coordinates = _coordinates({"lat": lat, "lon": lon})
            if coordinates:
                return {"lat": coordinates[0], "lon": coordinates[1], "label": name}
        geocoded = await _geocode_location(name)
        if geocoded:
            geocoded["label"] = name
            return geocoded
        return {"lat": None, "lon": None, "label": name}

    coord = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if not coord:
        coord = re.search(r'[?&](?:q|ll)=(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if coord:
        lat, lon = coord.group(1), coord.group(2)
        coordinates = _coordinates({"lat": lat, "lon": lon})
        if coordinates:
            return {"lat": coordinates[0], "lon": coordinates[1], "label": value}
    return None


async def _location_label_for_storage(value: str | None) -> str | None:
    text = (value or "").strip()
    if not _is_maps_url(text):
        return value
    location = await _resolve_location(text)
    label = location.get("label") if location else None
    return label if isinstance(label, str) and label.strip() and label != text else value


async def _driving_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
    distance, _ = await _gmaps_driving_distance(lat1, lon1, lat2, lon2)
    return distance


def _bkav_sync_context(request: Request, user: dict, payload: BkavSyncInput):
    vehicle = db.select("plate_whitelist", conditions={"id": payload.vehicle_id}, fetchall=False) if payload.vehicle_id > 0 else None
    if payload.vehicle_id > 0 and not vehicle:
        raise ValueError("Không tìm thấy xe đã chọn.")
    try:
        start, end = month_bounds(payload.month)
    except ValueError as exc:
        raise ValueError("Chọn một tháng cụ thể để đồng bộ hóa đơn.") from exc
    ctx = get_menu_context(request, user)
    seller_id = ctx.get("active_company_id")
    seller = get_company(seller_id) if seller_id else None
    if not seller:
        raise ValueError("Đơn vị đang chọn không hợp lệ.")
    seller_tax = (seller.get("tax_code") or "").strip()
    from frontend.modules.invoices.bkav_push import configuration
    configuration(seller_tax)
    buyer = None
    if payload.company_id:
        buyer = get_company(payload.company_id)
        if not buyer:
            raise ValueError("Đơn vị mua được chọn không hợp lệ.")
    return vehicle, seller_id, buyer, start, end, seller_tax


def _extract_address_provinces(text: str) -> list[str]:
    provinces = []
    m = re.findall(r"(?:tỉnh|thành phố|tp)\s+([^,;\n]+)", text, re.I)
    for p in m:
        clean = p.strip()
        if clean and clean not in provinces:
            provinces.append(clean)
    known = ["Phú Thọ", "Vĩnh Phúc", "Hà Nội", "Bắc Ninh", "Lào Cai", "Lạng Sơn", "Nghệ An", "Hải Phòng", "Hưng Yên"]
    for k in known:
        if k.lower() in text.lower() and k not in provinces:
            provinces.append(k)
    return provinces


async def _fill_bkav_google_distances(invoices: list[dict]) -> None:
    """Fill missing BKAV route distances using only the configured Google Maps APIs."""
    route_segments = [segment for invoice in invoices for segment in (invoice.get("segments") or [])]
    unique_routes: dict[tuple[str, str], tuple[str, str]] = {}
    for segment in route_segments:
        if segment.get("distance_km") not in (None, ""):
            continue
        origin = str(segment.get("origin") or "").strip()
        destination = str(segment.get("destination") or "").strip()
        if origin and destination:
            unique_routes.setdefault((origin.casefold(), destination.casefold()), (origin, destination))
    if not unique_routes:
        return

    semaphore = asyncio.Semaphore(4)
    async def fetch_route(origin: str, destination: str):
        async with semaphore:
            start, end = origin, destination
            if _is_maps_url(start):
                location = await _resolve_location(start)
                coordinates = _coordinates(location) if location else None
                if coordinates is None:
                    return None
                start = {"lat": coordinates[0], "lon": coordinates[1]}
            if _is_maps_url(end):
                location = await _resolve_location(end)
                coordinates = _coordinates(location) if location else None
                if coordinates is None:
                    return None
                end = {"lat": coordinates[0], "lon": coordinates[1]}
            for api in (_gmaps_routes_distance, _gmaps_directions_distance):
                try:
                    distance, _ = await api(start, end)
                    if distance is not None and math.isfinite(float(distance)) and float(distance) > 0:
                        return round(float(distance), 2)
                except Exception:
                    logger.debug("Google Maps distance lookup failed for BKAV route", exc_info=True)
            return None

    keys = list(unique_routes)
    results = await asyncio.gather(*(fetch_route(*unique_routes[key]) for key in keys))
    distances = dict(zip(keys, results))
    unresolved = []
    for segment in route_segments:
        if segment.get("distance_km") not in (None, ""):
            continue
        key = (str(segment.get("origin") or "").strip().casefold(),
               str(segment.get("destination") or "").strip().casefold())
        distance = distances.get(key)
        if distance is None:
            unresolved.append(f"{segment.get('origin', '')} -> {segment.get('destination', '')}")
        else:
            segment["distance_km"] = distance
    if unresolved:
        raise ValueError("Google Maps không tính được quãng đường: " + "; ".join(unresolved[:3]))


@router.post("/cargo/bkav/preview", include_in_schema=False)
@router.post("/cargo/invoices/preview")
async def preview_bkav_cargo_invoices(
    request: Request, payload: BkavSyncInput,
    user: dict = Depends(require_menu("vehicles")),
):
    vehicle, seller_id, buyer, start, end, seller_tax = _bkav_sync_context(request, user, payload)
    invoices, ignored = await run_in_threadpool(fetch_bkav_invoices, start, end, seller_tax)
    if buyer:
        invoices = [item for item in invoices if item["buyer_tax_code"] == buyer["tax_code"]]
    vehicles = await run_in_threadpool(bkav_vehicle_map)
    eligible = []
    for item in invoices:
        try:
            groups = bkav_invoice_vehicle_groups(item, vehicles, vehicle)
            item["target_vehicles"] = [target["plate_number"] for target, _ in groups if target]
        except ValueError as exc:
            item["target_vehicles"] = []
            item["unavailable_reason"] = str(exc)
        eligible.append(item)
    existing = await run_in_threadpool(
        bkav_existing_imports, eligible, seller_id, vehicle, vehicles,
    )
    already_imported = [item for item in eligible
                        if existing.get(item["guid"]) and existing[item["guid"]]["kind"] == "imported"]
    new_invoices = [item for item in eligible
                    if not (existing.get(item["guid"])
                            and existing[item["guid"]]["kind"] == "imported")]

    def _preview_item(item):
        return {"guid": item["guid"], "date": item["date"].isoformat(),
                "number": str(item["number"]).zfill(8), "serial": item["serial"],
                "buyer": item["buyer_name"], "description": item["description"],
                "target_vehicles": item["target_vehicles"],
                "invoice_plates": list(dict.fromkeys(
                    segment["vehicle_plate"] for segment in item.get("segments", [])
                    if segment.get("vehicle_plate")
                )),
                "is_crane_by_shift": bool(item.get("is_crane_by_shift")),
                "unavailable_reason": item.get("unavailable_reason"),
                "already_imported": bool(existing.get(item["guid"]) and
                                          existing[item["guid"]]["kind"] == "imported"),
                "line_count": item["line_count"], "segment_count": len(item["segments"]),
                "origin": item["origin"], "destination": item["destination"],
                "subtotal": str(item["subtotal"]), "gross": str(item["gross"]),
                "existing": existing.get(item["guid"])}

    unavailable = sum(1 for item in new_invoices if item.get("unavailable_reason"))
    return {"ok": True, "found": len(new_invoices), "ignored": ignored,
            "already_imported": len(already_imported),
            "unavailable": unavailable,
            "invoices": [_preview_item(item) for item in new_invoices]}


@router.post("/cargo/bkav/import", include_in_schema=False)
@router.post("/cargo/invoices/import")
async def import_bkav_cargo_invoices(
    request: Request, payload: BkavSyncInput,
    user: dict = Depends(require_menu("vehicles")),
):
    vehicle, seller_id, buyer, start, end, seller_tax = _bkav_sync_context(request, user, payload)
    if not payload.guids or len(payload.guids) > 500:
        raise ValueError("Ch\u1ecdn ít nhất một hóa đơn và không quá 500 hóa đơn mỗi lần.")
    invoices, _ = await run_in_threadpool(fetch_bkav_invoices, start, end, seller_tax)
    if buyer:
        invoices = [item for item in invoices if item["buyer_tax_code"] == buyer["tax_code"]]
    selected_guids = set(payload.guids)
    selected_invoices = [item for item in invoices if item["guid"] in selected_guids]
    await _fill_bkav_google_distances(selected_invoices)
    counts = await run_in_threadpool(
        import_bkav_invoices, invoices, payload.guids,
        vehicle=vehicle, seller_company_id=seller_id,
        payment_status=payload.payment_status,
    )
    return {"ok": True, **counts}


@router.get("/cargo")
async def cargo_trips_page(
    request: Request,
    vehicle_id: str | None = Query(None),
    company_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    month: str | None = Query(None),
    status: str | None = Query(None),
    payment_status: str | None = Query(None),
    q: str | None = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"

    all_vehicles = db.select("plate_whitelist", order_by="plate_number ASC") or []
    all_companies = list_companies() or []
    vid: int | None = None
    if vehicle_id and str(vehicle_id).strip().isdigit() and int(vehicle_id) > 0:
        vid = int(vehicle_id)

    cid: int | None = None
    if company_id and str(company_id).strip().isdigit() and int(company_id) > 0:
        cid = int(company_id)

    vehicle = next((v for v in all_vehicles if v["id"] == vid), None) if vid is not None else None
    if vid is not None and not vehicle:
        vid = None

    company = next((c for c in all_companies if c["id"] == cid), None) if cid is not None else None
    if cid is not None and not company:
        cid = None

    month = (month or date.today().strftime("%Y-%m")).strip()
    if len(month) == 4 and month.isdigit():
        month = f"{month}-all"

    start: date | None = None
    end: date | None = None
    if month == "all":
        start, end = None, None
    elif month.endswith("-all"):
        year_str = month[:4]
        try:
            year = int(year_str)
            start = date(year, 1, 1)
            end = date(year + 1, 1, 1)
        except ValueError:
            return HTMLResponse("Năm không hợp lệ.", status_code=422)
    else:
        try:
            start, end = month_bounds(month)
        except ValueError as exc:
            return HTMLResponse(str(exc), status_code=422)

    filter_status = status or payment_status
    if filter_status not in ("debt", "paid"):
        filter_status = None

    document_query = (q or "").strip()[:100]
    if document_query[:3].casefold() in ("hđ ", "hd "):
        document_query = document_query[3:].strip()

    active_company_id = ctx.get("active_company_id")

    all_count = count_cargo_history_groups(vehicle_id=vid, company_id=cid, start=start, end=end, seller_company_id=active_company_id, document_query=document_query)
    debt_count = count_cargo_history_groups(vehicle_id=vid, company_id=cid, payment_status="debt", start=start, end=end, seller_company_id=active_company_id, document_query=document_query)
    paid_count = count_cargo_history_groups(vehicle_id=vid, company_id=cid, payment_status="paid", start=start, end=end, seller_company_id=active_company_id, document_query=document_query)

    total = count_cargo_history_groups(vehicle_id=vid, company_id=cid, payment_status=filter_status, start=start, end=end, seller_company_id=active_company_id, document_query=document_query)
    total_pages = max(1, math.ceil(total / PER_PAGE))
    page = min(page, total_pages)
    offset = (page - 1) * PER_PAGE

    trips = get_cargo_history_groups(vehicle_id=vid, company_id=cid, limit=PER_PAGE, offset=offset,
                                     payment_status=filter_status, start=start, end=end,
                                     seller_company_id=active_company_id,
                                     document_query=document_query)
    for t in trips:
        if t.get("document_number"):
            t["document_number"] = format_document_number(t["document_number"])
        t["fuel_cost_total_fmt"] = _fmt_currency(t.get("fuel_cost_total"))
        t["freight_revenue_fmt"] = _fmt_currency(t.get("freight_revenue"))
        t["source_revenue_gross_fmt"] = _fmt_currency(t.get("source_revenue_gross"))
        vat_rate = parse_vat_rate(t.get("vat_rate")) if t.get("vat_rate") is not None else None
        t["vat_percent_fmt"] = format_vat_percent(vat_rate) if vat_rate is not None else None
        t["current_revenue_gross_fmt"] = _fmt_currency(
            (Decimal(str(t["freight_revenue"])) * (Decimal("1") + vat_rate)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP,
            )
        ) if t.get("freight_revenue") is not None and vat_rate is not None else None
        t["debt_amount_fmt"] = _fmt_currency(t.get("debt_amount"))
        if t.get("freight_revenue") is not None:
            rev = Decimal(str(t["freight_revenue"]))
            fuel = Decimal(str(t.get("fuel_cost_total") or 0))
            profit = rev - fuel
            t["estimated_profit"] = profit
            t["estimated_profit_fmt"] = _fmt_currency(profit)
        else:
            t["estimated_profit"] = None
            t["estimated_profit_fmt"] = None
        if t.get("fuel_price_per_liter"):
            t["fuel_price_fmt"] = _fmt_currency(t["fuel_price_per_liter"])
        else:
            t["fuel_price_fmt"] = "—"
        for k, v in t.items():
            if k in ("invoice_date", "departure_time", "return_time", "created_at", "updated_at") and v and hasattr(v, "isoformat"):
                t[k] = v.isoformat()

    trip_debt_where, debt_sum_params = _history_conditions(
        "trip", vehicle_id=vid, company_id=cid,
        payment_status="debt", start=start, end=end,
        seller_company_id=active_company_id, document_query=document_query,
    )
    debt_sum_row = db._execute_query(
        f"SELECT COALESCE(SUM(trip.debt_amount), 0) AS total_debt "
        f"FROM cargo_trips trip {trip_debt_where}",
        tuple(debt_sum_params), fetch=True, commit=False
    )
    total_debt = Decimal(str(debt_sum_row["total_debt"])) if debt_sum_row and debt_sum_row.get("total_debt") else Decimal(0)
    invoice_debt_where, invoice_debt_params = _history_conditions(
        "invoice", invoice=True, vehicle_id=vid, company_id=cid,
        payment_status="debt", start=start, end=end,
        seller_company_id=active_company_id, document_query=document_query,
    )
    invoice_debt_row = db._execute_query(
        f"SELECT COALESCE(SUM(invoice.debt_amount), 0) AS total_debt "
        f"FROM public.bkav_invoices invoice {invoice_debt_where}",
        tuple(invoice_debt_params), fetch=True, commit=False,
    )
    total_debt += Decimal(str(invoice_debt_row["total_debt"] or 0)) if invoice_debt_row else Decimal(0)

    url_params = {}
    if vid is not None:
        url_params["vehicle_id"] = vid
    if cid is not None:
        url_params["company_id"] = cid
    if month:
        url_params["month"] = month
    if filter_status:
        url_params["status"] = filter_status
    if document_query:
        url_params["q"] = document_query
    base_query = urllib.parse.urlencode(url_params)
    page_base_url = f"/admin/vehicles/cargo?{base_query}&page=" if base_query else "/admin/vehicles/cargo?page="

    ctx["vehicles"] = all_vehicles
    ctx["companies"] = all_companies
    ctx["vehicle"] = vehicle
    ctx["selected_vehicle"] = vehicle
    ctx["vehicle_id"] = vid
    ctx["company_id"] = cid
    ctx["company"] = company
    ctx["vehicle_section"] = "cargo"
    ctx["month"] = month
    ctx["now_year"] = date.today().year
    ctx["trips"] = trips
    ctx["all_trip_ids"] = [item["selection_key"] for item in trips]
    ctx["entry_trip_ids"] = [[item["selection_key"]] for item in trips]
    ctx["page"] = page
    ctx["total_pages"] = total_pages
    ctx["total"] = total
    ctx["current_status"] = filter_status or ""
    ctx["search_query"] = document_query
    ctx["all_count"] = all_count
    ctx["debt_count"] = debt_count
    ctx["paid_count"] = paid_count
    ctx["total_vehicle_debt_fmt"] = _fmt_currency(total_debt)
    ctx["total_debt_fmt"] = _fmt_currency(total_debt)
    ctx["page_base_url"] = page_base_url
    ctx["fmt_currency"] = _fmt_currency
    ctx["now_iso"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
    ctx["service_presets"] = [item["name"] for item in list_catalog_items("service")]

    return templates.TemplateResponse("pages/cargo/index.html", {"request": request, **ctx})


@router.get("/{vehicle_id}/cargo")
async def legacy_vehicle_cargo_redirect(
    vehicle_id: int,
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    query_params = dict(request.query_params)
    query_params["vehicle_id"] = str(vehicle_id)
    return RedirectResponse(f"/admin/vehicles/cargo?{urllib.parse.urlencode(query_params)}", status_code=303)


@router.get("/api/invoice-companies/lookup")
async def lookup_invoice_company(
    tax_code: str = Query(...),
    _: dict = Depends(require_menu("vehicles")),
):
    try:
        company = await lookup_tax_code(tax_code)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=422)
    except TaxLookupError as exc:
        return JSONResponse({"ok": False, "message": exc.message}, status_code=exc.status_code)
    return {"ok": True, **company}


@router.post("/api/invoice-companies/{company_id}/refresh")
async def refresh_invoice_company(
    company_id: int,
    _: dict = Depends(require_menu("vehicles")),
):
    try:
        updated = await refresh_company_from_tax(company_id)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=422)
    except TaxLookupError as exc:
        return JSONResponse({"ok": False, "message": exc.message}, status_code=exc.status_code)
    return {"ok": True, "company": updated}


@router.get("/api/invoice-tax-suggestion")
async def invoice_tax_suggestion(
    invoice_date: str = Query(...),
    _: dict = Depends(require_menu("vehicles")),
):
    if not _INVOICE_DATE_RE.fullmatch(invoice_date):
        return JSONResponse({"ok": False, "message": "Ngày hóa đơn không hợp lệ."}, status_code=422)
    try:
        invoice_day = date.fromisoformat(invoice_date)
    except ValueError:
        return JSONResponse({"ok": False, "message": "Ngày hóa đơn không hợp lệ."}, status_code=422)
    return _vat_rule_response(invoice_day)


@router.post("/api/invoice-companies")
async def create_invoice_company(
    payload: InvoiceCompanyInput,
    _: dict = Depends(require_menu("vehicles")),
):
    try:
        company = save_company(payload.tax_code, payload.name, payload.address, payload.email, phone=payload.phone)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=422)
    return {"ok": True, "company": company}


@router.get("/api/resolve-maps")
async def resolve_maps_url(url: str = Query(...)):
    if not _is_maps_url(url):
        return JSONResponse({"ok": False, "message": "Link Google Maps không hợp lệ."}, status_code=422)
    location = await _resolve_location(url)
    if location is None:
        return JSONResponse({"ok": False, "message": "Không lấy được tọa độ từ link Google Maps."}, status_code=404)
    if location["label"] == url:
        # Try Google Reverse Geocoding first
        try:
            google_label = await reverse_geocode_google(location["lat"], location["lon"])
            if google_label:
                location["label"] = google_label
        except Exception as exc:
            logger.warning("Google reverse geocode failed: %s", exc)

    return JSONResponse({"ok": True, **location})


@router.get("/api/geocode")
async def geocode_address(q: str = Query(...)):
    location = await _geocode_location(q)
    if location:
        return JSONResponse({"ok": True, **location})
    return JSONResponse({"ok": False, "message": "Không tìm thấy địa điểm."}, status_code=404)


@router.get("/api/calculate-distance")
async def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float):
    if _coordinates({"lat": lat1, "lon": lon1}) is None or _coordinates({"lat": lat2, "lon": lon2}) is None:
        return JSONResponse({"ok": False, "message": "Tọa độ không hợp lệ."}, status_code=422)
    distance, provider = await _gmaps_driving_distance(lat1, lon1, lat2, lon2)
    if distance is not None:
        return JSONResponse({"ok": True, "distance_km": distance, "provider": provider})
    return JSONResponse({"ok": False, "message": "Không tìm được đường đi."}, status_code=404)


@router.get("/api/calculate-distance-by-address")
async def calculate_distance_by_address(origin: str = Query(...), destination: str = Query(...),
                                        waypoints: str = "[]"):
    try:
        via = json.loads(waypoints or "[]")
    except (TypeError, json.JSONDecodeError):
        via = None
    if (not isinstance(via, list) or len(via) > 8
            or any(not isinstance(point, str) or not point.strip() or len(point) > 500 for point in via)):
        return JSONResponse({"ok": False, "message": "Điểm ghé không hợp lệ."}, status_code=422)
    try:
        async def google_leg(origin_address: str, destination_address: str):
            # Google can route the entered addresses directly. Geocoding each
            # address first makes this form unnecessarily slow and can fail when
            # the key has Routes access but no Geocoding access.
            start, end = origin_address.strip(), destination_address.strip()
            if not start or not end:
                return None, None
            if _is_maps_url(start):
                location = await _resolve_location(start)
                coordinates = _coordinates(location) if location else None
                if coordinates is None:
                    return None, None
                start = {"lat": coordinates[0], "lon": coordinates[1]}
            if _is_maps_url(end):
                location = await _resolve_location(end)
                coordinates = _coordinates(location) if location else None
                if coordinates is None:
                    return None, None
                end = {"lat": coordinates[0], "lon": coordinates[1]}
            for api, provider in ((_gmaps_routes_distance, "google_routes"),
                                  (_gmaps_directions_distance, "google_directions")):
                distance_km, _ = await api(start, end)
                if distance_km is not None and math.isfinite(float(distance_km)) and distance_km > 0:
                    return float(distance_km), provider
            return None, None

        async def google_distance():
            points = [origin, *via, destination]
            if any(not point.strip() for point in points):
                return {"ok": False, "message": "Nhập điểm lấy, điểm ghé và điểm giao hàng."}
            legs = await asyncio.gather(*(google_leg(start, end)
                                          for start, end in zip(points, points[1:])))
            if all(distance is not None for distance, _ in legs):
                providers = {provider for _, provider in legs}
                return {"ok": True, "distance_km": round(sum(distance for distance, _ in legs), 2),
                        "provider": providers.pop() if len(providers) == 1 else "google_maps"}
            return {"ok": False, "message": "Google Maps không tính được quãng đường. Kiểm tra địa chỉ hoặc quyền Routes/Directions API."}

        result = await asyncio.wait_for(google_distance(), timeout=18)
        return JSONResponse(result)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "message": "Google Maps phản hồi quá lâu. Thử lại hoặc nhập số km thủ công."})
    except Exception as exc:
        logger.warning("calculate_distance_by_address error: %s", exc)
        return JSONResponse({"ok": False, "message": "Không tính được quãng đường bằng Google Maps."})


class InvoiceLocationsInput(BaseModel):
    origin: str
    destination: str
    via_points: list[str] = []


@router.post("/api/normalize-invoice-locations")
async def normalize_invoice_locations(
    payload: InvoiceLocationsInput,
    _: dict = Depends(require_menu("vehicles")),
):
    if (len(payload.origin) > 500 or len(payload.destination) > 500
            or len(payload.via_points) > 8 or any(len(point) > 500 for point in payload.via_points)):
        return JSONResponse({"ok": False, "message": "Địa danh quá dài."}, status_code=422)
    try:
        directory = await run_in_threadpool(lambda: db._execute_query("""
            SELECT c.name AS commune_name, p.name AS province_name
            FROM public.administrative_communes c
            JOIN public.administrative_provinces p ON p.code = c.province_code
            WHERE c.is_active AND p.is_active
        """, fetchall=True, commit=False) or [])
    except Exception:
        logger.exception("Cannot load administrative directory for invoice normalization")
        return JSONResponse({"ok": False, "message": "Không đọc được danh mục địa danh."}, status_code=503)
    extra_stops, destination = split_invoice_destination_stops(payload.destination, directory)
    via_points = [normalize_invoice_location(point, directory) for point in payload.via_points]
    if len(via_points) + len(extra_stops) > 8:
        return JSONResponse({"ok": False, "message": "Quá nhiều điểm ghé."}, status_code=422)
    for stop in extra_stops:
        if not via_points or via_points[-1] != stop:
            via_points.append(stop)
    return JSONResponse({
        "ok": True,
        "origin": normalize_invoice_location(payload.origin, directory),
        "destination": destination,
        "via_points": via_points,
    })


@router.get("/api/google-maps-status")
async def google_maps_status_api():
    report = await check_google_maps_services_status()
    return JSONResponse(report)


@router.get("/api/fuel-price")
async def get_fuel_price_api(target_date: str = Query(None)):
    d = target_date or date.today().isoformat()
    if len(d) != 10 or not _valid_trip_datetime(d):
        return JSONResponse({"ok": False, "message": "Ngày tra giá dầu không hợp lệ.", "price": None}, status_code=422)
    url = f"https://giaxanghomnay.com/api/pvdate/{d}"
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.get(url)
        if resp.is_error:
            logger.warning("Fuel price request returned HTTP %s", resp.status_code)
        resp.raise_for_status()
        data = resp.json()
    items = data[0] if isinstance(data, list) and data and isinstance(data[0], list) else data
    if not isinstance(items, list):
        logger.warning("Fuel price response is not a list")
        return JSONResponse({"ok": False, "message": "Dữ liệu giá dầu không hợp lệ.", "price": None}, status_code=502)
    diesel_items = [r for r in items if isinstance(r, dict) and isinstance(r.get("title"), str)]
    if items and not diesel_items:
        logger.warning("Fuel price response contains no valid entries")
    diesel = next(
        (r for r in diesel_items if "DO" in r["title"] and "0,05" in r["title"]),
        next((r for r in diesel_items if "DO" in r["title"]), None)
    )
    if diesel:
        price = _source_number(diesel.get("zone1_price"))
        if price is not None and price > 0:
            return JSONResponse({"ok": True, "price": price, "title": diesel["title"], "date": d})
    return JSONResponse({"ok": False, "message": "Không tìm thấy giá dầu.", "price": None}, status_code=404)


@router.get("/cargo/add")
@router.get("/{vehicle_id}/cargo/add")
async def add_cargo_trip_page(
    request: Request,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu("vehicles")),
):
    if request.query_params.get("zalo_draft") != "1":
        selected = vehicle_id or request.query_params.get("vehicle_id")
        suffix = f"?vehicle_id={selected}" if selected and str(selected).isdigit() else ""
        return RedirectResponse(f"/admin/vehicles/cargo/invoices/add{suffix}", status_code=303)
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    draft = request.session.get("zalo_trip_draft") if request.query_params.get("zalo_draft") == "1" else None
    if draft and draft.get("user_id") != user.get("id"):
        draft = None

    all_vehicles = db.select("plate_whitelist", order_by="plate_number ASC") or []
    if vehicle_id is None:
        v_param = request.query_params.get("vehicle_id")
        if v_param and str(v_param).isdigit():
            vehicle_id = int(v_param)
    if draft and draft.get("vehicle_nickname"):
        linked_user = db._execute_query(
            "SELECT full_name FROM public.users WHERE lower(nickname) = lower(%s) LIMIT 2",
            (draft["vehicle_nickname"],), fetchall=True, commit=False,
        ) or []
        vehicles = [v for v in all_vehicles if any(v.get("owner_name") == row["full_name"] for row in linked_user)]
        if len(linked_user) == 1 and len(vehicles) == 1:
            vehicle_id = vehicles[0]["id"]
            draft["vehicle_id"] = vehicle_id
        else:
            draft["missing"].append("Chọn xe: không xác định được xe chú " + draft["vehicle_nickname"])

    vehicle = None
    if vehicle_id:
        vehicle = next((v for v in all_vehicles if v["id"] == vehicle_id), None)
    if not vehicle and all_vehicles:
        vehicle = all_vehicles[0]
        vehicle_id = vehicle["id"]

    if not vehicle:
        return RedirectResponse(url="/admin/vehicles", status_code=303)

    ctx["vehicles"] = all_vehicles
    ctx["vehicle"] = vehicle
    ctx["vehicle_id"] = vehicle_id
    now = datetime.now()
    ctx["now_iso"] = now.strftime("%Y-%m-%dT%H:%M")
    ctx["today_iso"] = now.date().isoformat()
    ctx["document_number"] = get_next_document_number()
    ctx["companies"] = list_companies()
    if draft and draft.get("tax_code"):
        buyers = [item for item in ctx["companies"] if item.get("tax_code") == draft["tax_code"]]
        draft["company_id"] = buyers[0]["id"] if len(buyers) == 1 else None
        if not draft["company_id"]:
            draft["missing"].append("Đơn vị mua theo MST " + draft["tax_code"])
    ctx["seller_company_id"] = get_default_seller_company_id(vehicle_id=vehicle_id, trip=None, current_user=user)
    ctx["vat_percent"] = "8"
    ctx["service_presets"] = [item["name"] for item in list_catalog_items("service")]
    ctx["service_name"] = (draft.get("service") or "") if draft else DEFAULT_SERVICE_NAME
    ctx["trip_note_value"] = (draft.get("note") or ctx["service_name"]) if draft else DEFAULT_SERVICE_NAME
    ctx["cargo_types"] = list_cargo_types()
    ctx["suggested_cargo_type"] = (draft.get("cargo") or "") if draft else cargo_description_from_service(DEFAULT_SERVICE_NAME)
    ctx["administrative_provinces"] = list_administrative_provinces()
    ctx["zalo_draft"] = draft
    return templates.TemplateResponse("pages/cargo/add.html", {"request": request, **ctx})


@router.post("/cargo/add")
@router.post("/{vehicle_id}/cargo/add")
async def create_cargo_trip(
    request: Request,
    vehicle_id: Optional[int] = None,
    invoice_date: str = Form(...),
    company_id: int = Form(...),
    service_name: str = Form(...),
    seller_company_id: Optional[int] = Form(None),
    origin: Optional[str] = Form(None),
    destination: Optional[str] = Form(None),
    fuel_liters: Optional[str] = Form(None),
    fuel_price_per_liter: Optional[str] = Form(None),
    freight_revenue: Optional[str] = Form(None),
    distance_km: Optional[str] = Form(None),
    vat_percent: Optional[str] = Form("8"),
    payment_method: Optional[str] = Form("CK"),
    payment_status: Optional[str] = Form("paid"),
    debt_amount: Optional[str] = Form(None),
    document_number: Optional[str] = Form(None),
    cargo_description: Optional[str] = Form(None),
    cargo_weight_tons: Optional[str] = Form(None),
    trip_note: Optional[str] = Form(None),
    route_segments: Optional[str] = Form(None),
    zalo_draft_token: Optional[str] = Form(None),
    zalo_payment_confirmed: bool = Form(False),
    user: dict = Depends(require_menu("vehicles")),
):
    if vehicle_id is None:
        form_data = await request.form()
        v_form = form_data.get("vehicle_id")
        if v_form and str(v_form).isdigit():
            vehicle_id = int(v_form)

    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False) if vehicle_id else None
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles", status_code=303)

    draft = None
    if zalo_draft_token:
        draft = request.session.get("zalo_trip_draft")
        if not draft or draft.get("token") != zalo_draft_token or draft.get("user_id") != user.get("id"):
            return HTMLResponse("Bản nháp Zalo đã hết hạn hoặc đã được lưu.", status_code=409)

    back_url = f"/admin/vehicles/cargo/add?vehicle_id={vehicle_id}"
    if draft:
        back_url += "&zalo_draft=1"
    company = get_company(company_id)
    if not company:
        return _trip_form_error("Chọn đơn vị hợp lệ.", back_url)

    try:
        segments = parse_route_segments(route_segments)
    except ValueError as exc:
        return _trip_form_error(str(exc), back_url)
    if segments:
        origin, destination = segments[0]["origin"], segments[-1]["destination"]
        freight_revenue, distance_km = route_totals(segments)

    if draft:
        if not zalo_payment_confirmed:
            return _trip_form_error("Xác nhận trạng thái thanh toán trước khi lưu chuyến từ Zalo.", back_url)
        missing_fields = []
        if not (origin or "").strip():
            missing_fields.append("điểm lấy hàng")
        if not (destination or "").strip():
            missing_fields.append("điểm giao hàng")
        if not (cargo_description or "").strip():
            missing_fields.append("loại hàng hóa")
        if not (service_name or "").strip():
            missing_fields.append("dịch vụ")
        if _source_number(freight_revenue) is None or _source_number(freight_revenue) <= 0:
            missing_fields.append("cước trước VAT")
        if missing_fields:
            return _trip_form_error("Cần bổ sung: " + ", ".join(missing_fields) + ".", back_url)

    origin = await _location_label_for_storage(origin)
    destination = await _location_label_for_storage(destination)
    trip_fields, error = _normalise_invoice_trip_fields({
        "invoice_date": invoice_date,
        "origin": (origin or "").strip() or company["address"],
        "destination": destination,
        "fuel_liters": fuel_liters,
        "fuel_price_per_liter": fuel_price_per_liter,
        "freight_revenue": freight_revenue,
        "distance_km": distance_km,
        "vat_percent": vat_percent,
        "payment_method": payment_method,
        "payment_status": payment_status,
        "debt_amount": debt_amount,
        "document_number": document_number,
        "driver_name": vehicle["owner_name"],
        "cargo_description": (cargo_description or "").strip() or cargo_description_from_service(service_name),
        "cargo_weight_tons": cargo_weight_tons,
        "trip_note": (trip_note or "").strip() or service_name.strip(),
    })
    if error:
        return _trip_form_error(error, back_url)
    trip_fields["company_id"] = company_id
    trip_fields["seller_company_id"] = request.session.get("active_company_id") or seller_company_id
    trip_fields["service_name"] = service_name.strip()
    trip_fields["route_segments"] = segments

    trip_id = add_cargo_trip(
        vehicle_id=vehicle_id,
        plate_number=vehicle["plate_number"],
        **trip_fields,
    )
    if trip_id is None:
        return HTMLResponse("Không thể lưu chuyến hàng.", status_code=500)
    if draft:
        register_trip_request(trip_id, draft)
        request.session.pop("zalo_trip_draft", None)
        return RedirectResponse(url=f"/admin/vehicles/cargo/{trip_id}/edit?zalo_created=1", status_code=303)
    return RedirectResponse(url=f"/admin/vehicles/cargo?vehicle_id={vehicle_id}", status_code=303)


@router.get("/cargo/{trip_id}/edit")
@router.get("/{vehicle_id}/cargo/{trip_id}/edit")
async def edit_cargo_trip_page(
    trip_id: int,
    request: Request,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    active_seller = get_company(ctx.get("active_company_id")) if ctx.get("active_company_id") else None

    trip = get_cargo_trip(trip_id)
    if not trip:
        return RedirectResponse(url="/admin/vehicles/cargo", status_code=303)
    vehicle_id = trip.get("vehicle_id")

    all_vehicles = db.select("plate_whitelist", order_by="plate_number ASC") or []
    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles/cargo", status_code=303)

    for dt_col in ("invoice_date", "departure_time", "return_time"):
        if trip.get(dt_col) and hasattr(trip[dt_col], "isoformat"):
            trip[dt_col] = trip[dt_col].isoformat()

    ctx["vehicles"] = all_vehicles
    ctx["vehicle"] = vehicle
    ctx["vehicle_id"] = vehicle_id
    ctx["trip"] = trip
    ctx["document_number"] = trip.get("document_number") or ""
    ctx["trip_note_value"] = trip.get("trip_note") or ""
    ctx["companies"] = list_companies()
    ctx["seller_company_id"] = trip.get("seller_company_id")
    ctx["vat_percent"] = format_vat_percent(parse_vat_rate(trip["vat_rate"])) if trip.get("vat_rate") is not None else ""
    ctx["service_presets"] = [item["name"] for item in list_catalog_items("service")]
    ctx["cargo_types"] = list_cargo_types()
    ctx["suggested_cargo_type"] = ""
    ctx["administrative_provinces"] = list_administrative_provinces()
    ctx["bkav_configured"] = bkav_is_configured((active_seller or {}).get("tax_code"))
    ctx["bkav_push"] = bkav_push_state(trip_id)
    ctx["zalo_request"] = get_trip_request(trip_id)
    ctx["zalo_created"] = request.query_params.get("zalo_created") == "1"
    ctx["zalo_notice"] = request.query_params.get("zalo_notice", "")[:300]
    ctx["bkav_notice"] = request.query_params.get("bkav_notice", "")[:300]
    ctx["bkav_error"] = request.query_params.get("bkav_error", "")[:300]
    return templates.TemplateResponse("pages/cargo/edit.html", {"request": request, **ctx})


@router.post("/cargo/{trip_id}/edit")
@router.post("/{vehicle_id}/cargo/{trip_id}/edit")
async def edit_cargo_trip(
    trip_id: int,
    request: Request,
    vehicle_id: Optional[int] = None,
    invoice_date: str = Form(...),
    company_id: int = Form(...),
    service_name: str = Form(...),
    seller_company_id: Optional[int] = Form(None),
    origin: Optional[str] = Form(None),
    destination: Optional[str] = Form(None),
    fuel_liters: Optional[str] = Form(None),
    fuel_price_per_liter: Optional[str] = Form(None),
    freight_revenue: Optional[str] = Form(None),
    distance_km: Optional[str] = Form(None),
    vat_percent: Optional[str] = Form(None),
    payment_method: Optional[str] = Form(None),
    payment_status: Optional[str] = Form(None),
    debt_amount: Optional[str] = Form(None),
    document_number: Optional[str] = Form(None),
    cargo_description: Optional[str] = Form(None),
    cargo_weight_tons: Optional[str] = Form(None),
    trip_note: Optional[str] = Form(None),
    route_segments: Optional[str] = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    trip = get_cargo_trip(trip_id)
    if not trip:
        return RedirectResponse(url="/admin/vehicles/cargo", status_code=303)
    if vehicle_id is None:
        form_data = await request.form()
        v_form = form_data.get("vehicle_id")
        if v_form and str(v_form).isdigit():
            vehicle_id = int(v_form)
        else:
            vehicle_id = trip.get("vehicle_id")

    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles/cargo", status_code=303)

    back_url = f"/admin/vehicles/cargo/{trip_id}/edit"
    company = get_company(company_id)
    if not company:
        return _trip_form_error("Chọn đơn vị hợp lệ.", back_url)
    if not str(vat_percent or "").strip():
        return _trip_form_error("Nhập thuế suất GTGT.", back_url)
    if payment_status not in ("paid", "debt"):
        return _trip_form_error("Chọn trạng thái thanh toán.", back_url)
    if payment_method not in ("CK", "TM", "UNKNOWN"):
        return _trip_form_error("Chọn hình thức trả tiền.", back_url)
    if payment_status == "debt" and not str(debt_amount or "").strip():
        return _trip_form_error("Nhập số tiền còn nợ.", back_url)

    try:
        segments = parse_route_segments(route_segments)
    except ValueError as exc:
        return _trip_form_error(str(exc), back_url)
    if segments:
        origin, destination = segments[0]["origin"], segments[-1]["destination"]
        freight_revenue, distance_km = route_totals(segments)

    origin = await _location_label_for_storage(origin)
    destination = await _location_label_for_storage(destination)
    trip_fields, error = _normalise_invoice_trip_fields({
        "invoice_date": invoice_date,
        "origin": (origin or "").strip(),
        "destination": destination,
        "fuel_liters": fuel_liters,
        "fuel_price_per_liter": fuel_price_per_liter,
        "freight_revenue": freight_revenue,
        "distance_km": distance_km,
        "vat_percent": vat_percent,
        "payment_method": payment_method,
        "payment_status": payment_status,
        "debt_amount": debt_amount,
        "document_number": document_number,
        "driver_name": trip.get("driver_name"),
        "cargo_description": (cargo_description or "").strip(),
        "cargo_weight_tons": cargo_weight_tons,
        "trip_note": (trip_note or "").strip(),
    }, generate_document_number=False, preserve_paid_debt=True, format_document=False)
    if error:
        return _trip_form_error(error, back_url)

    for field in ("trip_quantity", "odometer_end", "trip_wage", "trip_other_cost", "outsourced_freight", "vehicle_owner_type"):
        trip_fields[field] = trip.get(field)
    trip_fields.pop("departure_time", None)
    trip_fields.pop("return_time", None)
    trip_fields["vehicle_id"] = vehicle_id
    trip_fields["company_id"] = company_id
    trip_fields["seller_company_id"] = seller_company_id if seller_company_id is not None else trip.get("seller_company_id")
    trip_fields["service_name"] = service_name.strip()
    trip_fields["route_segments"] = segments
    if not update_cargo_trip(trip_id=trip_id, **trip_fields):
        return HTMLResponse("Không tìm thấy chuyến hàng.", status_code=404)
    return RedirectResponse(url=f"/admin/vehicles/cargo?vehicle_id={vehicle_id}", status_code=303)


@router.post("/cargo/{trip_id}/service")
@router.post("/{vehicle_id}/cargo/{trip_id}/service")
async def update_cargo_service(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    service_name: str = Form(...),
    _: dict = Depends(require_menu("vehicles")),
):
    trip = get_cargo_trip(trip_id)
    if not trip or (vehicle_id is not None and trip.get("vehicle_id") != vehicle_id):
        return JSONResponse({"ok": False, "message": "Không tìm thấy chuyến hàng."}, status_code=404)
    current_cargo = (trip.get("cargo_description") or "").strip()
    inferred_cargo = cargo_description_from_service(trip.get("service_name") or "")
    next_cargo = (
        cargo_description_from_service(service_name)
        if not current_cargo or current_cargo == inferred_cargo
        else current_cargo
    )
    current_note = (trip.get("trip_note") or "").strip()
    next_note = (
        service_name.strip()
        if not current_note or current_note.startswith("Nội dung hóa đơn gốc:") or current_note == trip["service_name"]
        else current_note
    )
    updated = db._execute_query(
        """UPDATE public.cargo_trips SET service_name = %s,
           cargo_description = %s, trip_note = %s, updated_at = NOW()
           WHERE id = %s""",
        (service_name.strip(), next_cargo, next_note, trip_id),
        commit=True,
    )
    if not updated:
        return JSONResponse({"ok": False, "message": "Không tìm thấy chuyến hàng."}, status_code=404)
    return {"ok": True}


@router.post("/cargo/{trip_id}/push-bkav")
async def push_cargo_invoice_to_bkav(
    trip_id: int,
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    headers = getattr(request, "headers", None) or {}
    is_ajax = bool(
        hasattr(headers, "get")
        and (headers.get("X-Requested-With") == "XMLHttpRequest"
             or "application/json" in headers.get("Accept", ""))
    )

    def respond_error(message: str, status_code: int = 400):
        if is_ajax:
            return JSONResponse({"ok": False, "error": message}, status_code=status_code)
        return RedirectResponse(
            "/admin/vehicles/cargo/" + str(trip_id) + "/edit?" + urllib.parse.urlencode({"bkav_error": message}),
            status_code=303,
        )

    trip = get_cargo_trip(trip_id)
    if not trip:
        if is_ajax:
            return JSONResponse({"ok": False, "error": "Kh\u00f4ng t\u00ecm th\u1ea5y chuy\u1ebfn h\u00e0ng."}, status_code=404)
        return HTMLResponse("Kh\u00f4ng t\u00ecm th\u1ea5y chuy\u1ebfn h\u00e0ng.", status_code=404)
    ctx = get_menu_context(request, user)
    if trip.get("seller_company_id") != ctx.get("active_company_id"):
        if is_ajax:
            return JSONResponse({"ok": False, "error": "Chuy\u1ebfn h\u00e0ng kh\u00f4ng thu\u1ed9c \u0111\u01a1n v\u1ecb \u0111ang ch\u1ecdn."}, status_code=403)
        return HTMLResponse("Chuy\u1ebfn h\u00e0ng kh\u00f4ng thu\u1ed9c \u0111\u01a1n v\u1ecb \u0111ang ch\u1ecdn.", status_code=403)

    zalo_request = get_trip_request(trip_id)
    if zalo_request and zalo_request["possible_existing_invoice"]:
        return respond_error(
            "Nh\u00f3m Zalo c\u00f3 tin h\u00f3a \u0111\u01a1n \u0111\u00e3 \u0111\u01b0\u1ee3c t\u1ea1o. \u0110\u1ed1i chi\u1ebfu tr\u00ean BKAV tr\u01b0\u1edbc khi t\u1ea1o h\u00f3a \u0111\u01a1n m\u1edbi.",
            status_code=409,
        )

    buyer = get_company(trip["company_id"]) if trip.get("company_id") else None
    seller = get_company(trip["seller_company_id"]) if trip.get("seller_company_id") else None
    back_url = f"/admin/vehicles/cargo/{trip_id}/edit"
    try:
        result = await run_in_threadpool(push_invoice_to_bkav, trip, buyer, seller)
    except BkavUncertainError as exc:
        return respond_error(str(exc)[:300], status_code=409)
    except ValueError as exc:
        return respond_error(str(exc)[:300], status_code=400)
    except Exception:
        logger.exception("BKAV invoice push failed for cargo trip %s", trip_id)
        return respond_error(
            "Kh\u00f4ng th\u1ec3 ho\u00e0n t\u1ea5t g\u1eedi BKAV. Ki\u1ec3m tra tr\u1ea1ng th\u00e1i tr\u00ean BKAV tr\u01b0\u1edbc khi th\u1eed l\u1ea1i.",
            status_code=502,
        )

    notice = "H\u00f3a \u0111\u01a1n nh\u00e1p \u0111\u00e3 c\u00f3 tr\u00ean BKAV."
    png_file = None
    if result.get("status") == "sent" and (result.get("invoice_guid") or result.get("InvoiceGUID")):
        try:
            png_file, _ = await asyncio.to_thread(
                get_or_fetch_bkav_invoice_files,
                str(result.get("invoice_guid") or result.get("InvoiceGUID")),
                force_refresh=True,
            )
        except Exception as img_err:
            logger.warning("Auto-fetch BKAV invoice image after trip push failed: %s", img_err)
    if zalo_request and result.get("status") == "sent":
        try:
            _, message = await send_invoice_notice(
                trip_id, trip=trip, buyer=buyer, invoice=result,
                image_path=str(png_file) if png_file and png_file.exists() else ""
            )
            notice += " " + message
        except Exception:
            logger.exception("Zalo invoice notice failed for cargo trip %s", trip_id)
            notice += " Ch\u01b0a g\u1eedi \u0111\u01b0\u1ee3c th\u00f4ng b\u00e1o Zalo; ki\u1ec3m tra tr\u1ea1ng th\u00e1i tr\u00ean trang chuy\u1ebfn h\u00e0ng."

    if is_ajax:
        image_url = None
        if png_file and png_file.exists():
            image_url = f"/upload/bkav_invoices/{png_file.name}?v={png_file.stat().st_mtime_ns}"
        return JSONResponse({
            "ok": True,
            "message": notice,
            "invoice_guid": result.get("invoice_guid") or result.get("InvoiceGUID"),
            "image_url": image_url,
        })
    return RedirectResponse(
        back_url + "?" + urllib.parse.urlencode({"bkav_notice": notice}), status_code=303,
    )

@router.post("/cargo/{trip_id}/confirm-new-invoice")
async def confirm_zalo_invoice_not_created(
    trip_id: int, request: Request, user: dict = Depends(require_menu("vehicles")),
):
    trip = get_cargo_trip(trip_id)
    if not trip or trip.get("seller_company_id") != get_menu_context(request, user).get("active_company_id"):
        return HTMLResponse("Không tìm thấy chuyến hàng trong đơn vị đang chọn.", status_code=404)
    confirm_invoice_not_created(trip_id)
    return RedirectResponse(
        f"/admin/vehicles/cargo/{trip_id}/edit?" + urllib.parse.urlencode({
            "zalo_notice": "Đã xác nhận chưa có hóa đơn trên BKAV. Có thể tạo hóa đơn nháp."
        }), status_code=303,
    )


@router.post("/cargo/{trip_id}/retry-zalo-invoice-notice")
async def retry_zalo_invoice_notice(
    trip_id: int, request: Request, user: dict = Depends(require_menu("vehicles")),
):
    trip = get_cargo_trip(trip_id)
    if not trip or trip.get("seller_company_id") != get_menu_context(request, user).get("active_company_id"):
        return HTMLResponse("Không tìm thấy chuyến hàng trong đơn vị đang chọn.", status_code=404)
    result = bkav_push_state(trip_id)
    if not result or result.get("status") != "sent":
        return HTMLResponse("BKAV chưa xác nhận hóa đơn nháp.", status_code=409)
    request_row = get_trip_request(trip_id)
    if not request_row or request_row["status"] not in ("send_failed", "send_uncertain"):
        return HTMLResponse("Thông báo không cần gửi lại.", status_code=409)
    buyer = get_company(trip["company_id"]) if trip.get("company_id") else None
    if not buyer:
        return HTMLResponse("Chuyến hàng chưa có đơn vị mua.", status_code=409)
    _, message = await send_invoice_notice(
        trip_id, trip=trip, buyer=buyer, invoice=result,
        retry_uncertain=request_row["status"] == "send_uncertain",
    )
    return RedirectResponse(
        f"/admin/vehicles/cargo/{trip_id}/edit?" + urllib.parse.urlencode({"zalo_notice": message}),
        status_code=303,
    )


@router.post("/cargo/{trip_id}/export-invoice")
@router.post("/{vehicle_id}/cargo/{trip_id}/export-invoice")
async def export_cargo_invoice(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu("vehicles")),
):
    trip = get_cargo_trip(trip_id)
    if not trip or (vehicle_id is not None and trip.get("vehicle_id") != vehicle_id):
        return HTMLResponse("Không tìm thấy chuyến hàng.", status_code=404)
    if vehicle_id is None:
        vehicle_id = trip.get("vehicle_id")

    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return HTMLResponse("Không tìm thấy xe của chuyến hàng.", status_code=404)

    back_url = f"/admin/vehicles/cargo/{trip_id}/edit"
    company = get_company(trip["company_id"]) if trip.get("company_id") else None
    if not company or not all(company.get(field) for field in ("name", "address", "tax_code")):
        return _trip_form_error("Chọn đơn vị hợp lệ.", back_url)
    if not trip.get("invoice_date"):
        return _trip_form_error("Chuyến hàng chưa có ngày hóa đơn.", back_url)
    if ensure_invoice_bill_number(trip_id, vehicle_id) is None:
        return HTMLResponse("Không thể cấp IDChungTu/MaBill.", status_code=500)
    export_trip = get_cargo_trip(trip_id)
    if not export_trip or export_trip.get("vehicle_id") != vehicle_id:
        return HTMLResponse("Không tìm thấy chuyến hàng đã lưu.", status_code=404)
    try:
        content = build_bkav_invoice(export_trip, company)
    except ValueError as exc:
        return _trip_form_error(str(exc), back_url)
    return _invoice_file_response(export_trip, content, company)


@router.get("/cargo/{trip_id}/export-contract")
@router.get("/{vehicle_id}/cargo/{trip_id}/export-contract")
async def export_cargo_contract(
    trip_id: int,
    request: Request,
    vehicle_id: Optional[int] = None,
    download: Optional[str] = Query(None),
    format: Optional[str] = Query(None),
    view: Optional[str] = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    trip = get_cargo_trip(trip_id)
    if not trip or (vehicle_id is not None and trip.get("vehicle_id") != vehicle_id):
        return HTMLResponse("Không tìm thấy chuyến hàng.", status_code=404)

    back_url = f"/admin/vehicles/cargo/{trip_id}/edit"
    company = get_company(trip["company_id"]) if trip.get("company_id") else None
    if not company or not company.get("name"):
        return _trip_form_error("Chọn đơn vị khách hàng hợp lệ trước khi xuất hợp đồng.", back_url)

    # Bước 2: Bắt buộc phải có công ty đang vào, không chạy nhánh dự phòng
    active_cid = request.session.get("active_company_id")
    if not active_cid and user:
        ctx_menu = get_menu_context(request, user)
        active_cid = ctx_menu.get("active_company_id")

    seller_id = trip.get("seller_company_id") or active_cid
    if not seller_id:
        msg = "Bắt buộc phải chọn công ty đang làm việc ở thanh trên cùng trước khi xuất hợp đồng."
        if view == "html" or (download or format or "").lower() == "html":
            return HTMLResponse(f"<h3>{msg}</h3>", status_code=400)
        return _trip_form_error(msg, back_url)

    seller = get_company(int(seller_id))
    if not seller:
        return _trip_form_error("Không tìm thấy thông tin công ty Bên B.", back_url)
    seller["bank_accounts"] = get_company_bank_accounts(seller["id"])
    company["bank_accounts"] = get_company_bank_accounts(company["id"])

    ctx = get_contract_context(trip, company, seller)

    req_format = (download or format or "").lower()
    if view == "html" or req_format == "html":
        html_content = render_contract_html(ctx, preview=True)
        return HTMLResponse(html_content)

    safe_no = re.sub(r"[^A-Za-z0-9_-]", "_", ctx["contract_no"])
    if req_format == "docx":
        docx_bytes = render_contract_docx(ctx)
        filename = f"Hop_dong_{safe_no}.docx"
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # Default: PDF
    pdf_bytes = render_contract_pdf(ctx)
    filename = f"Hop_dong_{safe_no}.pdf"
    disposition = "attachment" if download == "pdf" else "inline"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


def _handle_word_download(docx_bytes: bytes, contract_no: str, prefix: str, req_format: Any) -> Response:
    fmt = str(req_format.default if hasattr(req_format, "default") else (req_format or "doc")).lower()
    from frontend.modules.transport.contract import _remove_accents
    safe_no = re.sub(r"[^A-Za-z0-9_-]", "_", _remove_accents(contract_no))
    if fmt == "docx":
        filename = f"{prefix}_{safe_no}.docx"
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    # Default to .doc
    try:
        doc_bytes = convert_docx_bytes_to_doc(docx_bytes)
        filename = f"{prefix}_{safe_no}.doc"
        media_type = "application/msword"
        return Response(
            content=doc_bytes,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        logger.warning("Word COM conversion to .doc failed, falling back to .docx: %s", exc)
        filename = f"{prefix}_{safe_no}.docx"
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@router.get("/cargo/{trip_id}/export-hdnt")
@router.get("/{vehicle_id}/cargo/{trip_id}/export-hdnt")
async def export_cargo_hdnt(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    download: Optional[str] = Query(None),
    format: Optional[str] = Query("doc"),
    user: dict = Depends(require_menu("vehicles")),
):
    trip = get_cargo_trip(trip_id)
    if not trip or (vehicle_id is not None and trip.get("vehicle_id") != vehicle_id):
        return HTMLResponse("Không tìm thấy chuyến hàng.", status_code=404)

    back_url = f"/admin/vehicles/cargo/{trip_id}/edit"
    company = get_company(trip["company_id"]) if trip.get("company_id") else None
    if not company or not company.get("name"):
        return _trip_form_error("Chọn đơn vị khách hàng hợp lệ trước khi xuất HĐNT.", back_url)

    seller = get_company(trip.get("seller_company_id")) if trip.get("seller_company_id") else None
    docx_bytes, contract_no = render_hdnt_docx(trip, company, seller)
    req_format = download or format or "doc"
    return _handle_word_download(docx_bytes, contract_no, "HDNT", req_format)


@router.get("/cargo/{trip_id}/export-bbbg")
@router.get("/{vehicle_id}/cargo/{trip_id}/export-bbbg")
async def export_cargo_bbbg(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    download: Optional[str] = Query(None),
    format: Optional[str] = Query("doc"),
    user: dict = Depends(require_menu("vehicles")),
):
    trip = get_cargo_trip(trip_id)
    if not trip or (vehicle_id is not None and trip.get("vehicle_id") != vehicle_id):
        return HTMLResponse("Không tìm thấy chuyến hàng.", status_code=404)

    back_url = f"/admin/vehicles/cargo/{trip_id}/edit"
    company = get_company(trip["company_id"]) if trip.get("company_id") else None
    if not company or not company.get("name"):
        return _trip_form_error("Chọn đơn vị khách hàng hợp lệ trước khi xuất BBBG.", back_url)

    seller = get_company(trip.get("seller_company_id")) if trip.get("seller_company_id") else None
    docx_bytes, contract_no = render_bbbg_docx(trip, company, seller)
    req_format = download or format or "doc"
    return _handle_word_download(docx_bytes, contract_no, "BBBG", req_format)


@router.get("/cargo/{trip_id}/export-hdnt-bbbg")
@router.get("/{vehicle_id}/cargo/{trip_id}/export-hdnt-bbbg")
async def export_cargo_hdnt_bbbg(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    download: Optional[str] = Query(None),
    format: Optional[str] = Query("doc"),
    user: dict = Depends(require_menu("vehicles")),
):
    trip = get_cargo_trip(trip_id)
    if not trip or (vehicle_id is not None and trip.get("vehicle_id") != vehicle_id):
        return HTMLResponse("Không tìm thấy chuyến hàng.", status_code=404)

    back_url = f"/admin/vehicles/cargo/{trip_id}/edit"
    company = get_company(trip["company_id"]) if trip.get("company_id") else None
    if not company or not company.get("name"):
        return _trip_form_error("Chọn đơn vị khách hàng hợp lệ trước khi xuất hợp đồng & biên bản.", back_url)

    seller = get_company(trip.get("seller_company_id")) if trip.get("seller_company_id") else None
    docx_bytes, contract_no = render_hdnt_bbbg_combined_docx(trip, company, seller)
    req_format = download or format or "doc"
    return _handle_word_download(docx_bytes, contract_no, "HDNT_BBBG", req_format)


def get_company_bank(seller_company_id: int | None = None):
    if seller_company_id:
        comp = get_company(int(seller_company_id))
        if comp and comp.get("bank_account") and str(comp["bank_account"]).strip():
            return comp
        accounts = get_company_bank_accounts(int(seller_company_id))
        if accounts:
            first_acc = accounts[0]
            if comp:
                comp = dict(comp)
                comp["bank_account"] = first_acc.get("account_number", "")
                comp["bank_name"] = first_acc.get("bank_name", "")
                return comp
    return None


def generate_trip_vietqr_url(trip: dict) -> tuple[str, dict]:
    seller_id = trip.get("seller_company_id")
    company = get_company_bank(seller_id)
    if not company or not company.get("bank_account"):
        raise ValueError("Chưa tìm thấy thông tin tài khoản ngân hàng của đơn vị xuất hóa đơn / vận chuyển.")

    raw_bank = (company.get("bank_name") or "").strip().upper()
    bank_code = BANK_CODE_MAP.get(raw_bank, raw_bank)
    account_no = str(company["bank_account"]).strip()
    company_name = (company.get("name") or "").strip()
    clean_acc_name = _remove_accents(company_name).upper()

    # Số tiền thanh toán (ưu tiên debt_amount nếu nợ, hoặc cước có VAT)
    if trip.get("payment_status") == "debt" and trip.get("debt_amount") is not None and float(trip["debt_amount"]) > 0:
        amount = int(round(float(trip["debt_amount"])))
    elif trip.get("freight_revenue"):
        vat_rate = float(trip.get("vat_rate") or 0)
        amount = int(round(float(trip["freight_revenue"]) * (1 + vat_rate)))
    else:
        amount = 0

    # Nội dung chuyển khoản là Nội dung công việc (trip_note) theo yêu cầu
    raw_work = trip.get("trip_note") or trip.get("service_name") or trip.get("cargo_description") or "Cước vận chuyển"
    raw_work = str(raw_work).strip()
    if raw_work.startswith("Nội dung hóa đơn gốc:"):
        raw_work = raw_work[len("Nội dung hóa đơn gốc:"):].strip()
    work_content = re.sub(r"\s+", " ", raw_work).strip()

    params = {}
    if amount > 0:
        params["amount"] = str(amount)
    if work_content:
        params["addInfo"] = work_content[:50]
    if clean_acc_name:
        params["accountName"] = clean_acc_name

    query_str = urllib.parse.urlencode(params)
    qr_url = f"https://img.vietqr.io/image/{bank_code}-{account_no}-compact2.png?{query_str}"

    details = {
        "bank_name": raw_bank,
        "bank_code": bank_code,
        "account_no": account_no,
        "account_name": company_name,
        "amount": amount,
        "work_content": work_content,
        "qr_url": qr_url,
    }
    return qr_url, details


@router.get("/cargo/{trip_id}/qr")
@router.get("/{vehicle_id}/cargo/{trip_id}/qr")
async def get_trip_qr_endpoint(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu("vehicles")),
):
    if vehicle_id is not None:
        trip = db._execute_query("SELECT * FROM cargo_trips WHERE id = %s AND vehicle_id = %s", (trip_id, vehicle_id), fetch=True, commit=False)
    else:
        trip = get_cargo_trip(trip_id)
    if not trip:
        return JSONResponse({"ok": False, "error": "Không tìm thấy chuyến hàng."}, status_code=404)

    qr_url = trip.get("qr_url")
    try:
        gen_url, details = generate_trip_vietqr_url(trip)
        if qr_url != gen_url:
            qr_url = gen_url
            db._execute_query("UPDATE cargo_trips SET qr_url = %s, updated_at = NOW() WHERE id = %s", (qr_url, trip_id), commit=True)
        details["qr_url"] = qr_url
        return JSONResponse({"ok": True, "qr_url": qr_url, "details": details})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/cargo/{trip_id}/qr")
@router.post("/{vehicle_id}/cargo/{trip_id}/qr")
async def generate_and_save_trip_qr_endpoint(
    trip_id: int,
    request: Request,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu("vehicles")),
):
    if vehicle_id is not None:
        trip = db._execute_query("SELECT * FROM cargo_trips WHERE id = %s AND vehicle_id = %s", (trip_id, vehicle_id), fetch=True, commit=False)
    else:
        trip = get_cargo_trip(trip_id)
    if not trip:
        return JSONResponse({"ok": False, "error": "Không tìm thấy chuyến hàng."}, status_code=404)

    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
        elif "form" in content_type:
            form = await request.form()
            body = dict(form)
        else:
            body = {}
    except Exception:
        body = {}

    if body.get("trip_note"):
        note = str(body["trip_note"]).strip()
        trip["trip_note"] = note
        db._execute_query("UPDATE cargo_trips SET trip_note = %s WHERE id = %s", (note, trip_id), commit=True)
    if body.get("debt_amount") is not None and str(body["debt_amount"]).strip() != "":
        try:
            raw_debt = str(body["debt_amount"]).replace(".", "").replace(",", "").strip()
            val = float(raw_debt)
            trip["debt_amount"] = val
            db._execute_query("UPDATE cargo_trips SET debt_amount = %s WHERE id = %s", (val, trip_id), commit=True)
        except (ValueError, TypeError):
            pass

    try:
        qr_url, details = generate_trip_vietqr_url(trip)
        db._execute_query("UPDATE cargo_trips SET qr_url = %s, updated_at = NOW() WHERE id = %s", (qr_url, trip_id), commit=True)
        return JSONResponse({"ok": True, "qr_url": qr_url, "details": details})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


def _bkav_edit_decimal(value: str | None, label: str, *, money: bool = False,
                       required: bool = False, places: str = "0.01") -> Decimal | None:
    raw = str(value or "").strip()
    if not raw:
        if required:
            raise ValueError(f"Nhập {label}.")
        return None
    if money:
        if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:[., ][0-9]{3})+)", raw):
            raise ValueError(f"{label} phải là số tiền nguyên.")
        raw = re.sub(r"[., ]", "", raw)
    else:
        if not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?", raw):
            raise ValueError(f"{label} không hợp lệ.")
        raw = raw.replace(",", ".")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{label} không hợp lệ.") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{label} không hợp lệ.")
    if money and number > Decimal("9999999999999999"):
        raise ValueError(f"{label} vượt giới hạn.")
    return number if money else number.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _invoice_form_context(request: Request, user: dict, *, invoice: dict | None = None,
                          lines: list[dict] | None = None, values: dict | None = None,
                          error: str = "") -> dict:
    ctx = get_menu_context(request, user)
    active_seller = get_company(ctx.get("active_company_id")) if ctx.get("active_company_id") else None
    ctx["current_menu"] = "vehicles"
    ctx["invoice"] = invoice
    ctx["vehicles"] = db.select("plate_whitelist", order_by="plate_number ASC") or []
    ctx["companies"] = list_companies() or []
    removed_service_names = {name.casefold() for name in REMOVED_INVOICE_SERVICES}
    invoice_services = [item["name"] for item in list_catalog_items("service")
                        if item["name"].casefold() not in removed_service_names]
    if not any(name.casefold() == CRANE_BY_SHIFT_SERVICE.casefold() for name in invoice_services):
        invoice_services.append(CRANE_BY_SHIFT_SERVICE)
    ctx["service_presets"] = invoice_services
    ctx["removed_invoice_services"] = list(REMOVED_INVOICE_SERVICES)
    ctx["cargo_types"] = list_cargo_types()
    ctx["default_vehicle_id"] = request.query_params.get("vehicle_id", "")
    if not ctx["default_vehicle_id"] and ctx["vehicles"]:
        ctx["default_vehicle_id"] = str(ctx["vehicles"][0]["id"])
    ctx["error"] = error or request.query_params.get("error", "")[:300]
    ctx["saved"] = request.query_params.get("saved") == "1"
    ctx["bkav_configured"] = bkav_is_configured((active_seller or {}).get("tax_code"))
    ctx["bkav_push"] = (saved_bkav_push_state(str(invoice["invoice_guid"]))
                        if invoice and invoice.get("entry_origin") == "manual" else None)
    ctx["bkav_notice"] = request.query_params.get("bkav_notice", "")[:300]
    ctx["bkav_error"] = request.query_params.get("bkav_error", "")[:300]
    ctx["bkav_image_url"] = (stored_bkav_invoice_image_url(str(invoice["invoice_guid"]))
                              if invoice else None)

    if values is not None:
        ctx["form_values"] = values
    else:
        default_number = ""
        default_serial = ""
        if invoice:
            default_number = str(invoice["invoice_number"]).zfill(8)
            default_serial = invoice.get("invoice_serial") or ""
        else:
            default_number, default_serial = "", ""

        ctx["form_values"] = {
            "invoice_date": (invoice["invoice_date"].isoformat() if invoice and hasattr(invoice["invoice_date"], "isoformat")
                             else str(invoice["invoice_date"])[:10] if invoice else date.today().isoformat()),
            "invoice_number": default_number,
            "invoice_serial": default_serial,
            "company_id": invoice["buyer_company_id"] if invoice else request.query_params.get("company_id", ""),
            "service_name": invoice["service_name"] if invoice else "",
            "vat_percent": format_vat_percent(parse_vat_rate(invoice["vat_rate"])) if invoice and invoice.get("vat_rate") is not None else "8",
            "payment_method": invoice["payment_method"] if invoice else "CK",
            "payment_status": invoice["payment_status"] if invoice else "paid",
            "debt_amount": str(invoice["debt_amount"]) if invoice else "",
        }
    current_service = str(ctx["form_values"].get("service_name") or "")
    if is_crane_by_shift_service(current_service):
        ctx["form_values"]["service_name"] = CRANE_BY_SHIFT_SERVICE
    elif current_service.casefold() in removed_service_names:
        ctx["form_values"]["service_name"] = ""
    if lines is None:
        lines = []
    crane_invoice = is_crane_by_shift_service(ctx["form_values"].get("service_name"))
    vehicle_by_plate = {re.sub(r"[^A-Z0-9]", "", str(vehicle.get("plate_number") or "").upper()): vehicle
                        for vehicle in ctx["vehicles"] if vehicle.get("plate_number")}
    line_items = []
    for source_line in lines:
        if not isinstance(source_line, dict):
            continue
        line = dict(source_line)
        plate = str(line.get("plate_number") or "").strip()
        if crane_invoice and not line.get("vehicle_id") and plate:
            linked_vehicle = vehicle_by_plate.get(re.sub(r"[^A-Z0-9]", "", plate.upper()))
            if linked_vehicle:
                line["vehicle_id"] = linked_vehicle["id"]
        line_items.append({**{key: str(line.get(key) if line.get(key) is not None else "")
                              for key in ("vehicle_id", "plate_number", "cargo_description", "cargo_weight_tons",
                                          "description", "origin", "destination", "amount", "distance_km",
                                          "fuel_liters", "fuel_price_per_liter", "fuel_cost_total")},
                           "via_points": line.get("via_points") if isinstance(line.get("via_points"), list) else []})
    ctx["line_items"] = line_items
    ctx["vehicle_options"] = [{"id": vehicle["id"], "plate": vehicle["plate_number"],
                               "owner": vehicle.get("owner_name") or "",
                               "rate": str(vehicle.get("fuel_consumption") or "")}
                              for vehicle in ctx["vehicles"]]
    ctx["back_url"] = "/admin/vehicles/cargo"
    return ctx


async def _invoice_payload_from_form(form: dict, request: Request, user: dict,
                                     *, seller_company_id: int | None = None) -> dict:
    seller_company_id = seller_company_id or get_menu_context(request, user).get("active_company_id")
    if not seller_company_id:
        raise ValueError("Chọn đơn vị phát hành hóa đơn.")
    raw_buyer_id = str(form.get("company_id") or "")
    buyer = get_company(int(raw_buyer_id)) if raw_buyer_id.isdigit() else None
    if not buyer:
        raise ValueError("Chọn đơn vị khách hàng.")
    vehicles = db.select("plate_whitelist", order_by="plate_number ASC") or []
    default_price = None
    try:
        raw_lines = json.loads(str(form.get("lines_json") or "[]"))
        if isinstance(raw_lines, list) and any(isinstance(line, dict) and not line.get("fuel_price_per_liter")
                                               and (line.get("fuel_liters") or line.get("distance_km"))
                                               for line in raw_lines):
            default_price = await run_in_threadpool(_fuel_price_for_date, form.get("invoice_date"))
    except (TypeError, json.JSONDecodeError):
        pass
    return parse_invoice_form(form, vehicles=vehicles, buyer=buyer,
                              seller_company_id=seller_company_id, default_fuel_price=default_price)


@router.get("/cargo/invoices/add")
async def add_invoice_page(request: Request, user: dict = Depends(require_menu("vehicles"))):
    ctx = _invoice_form_context(request, user)
    return templates.TemplateResponse("pages/cargo/invoice_form.html", {"request": request, **ctx})


@router.post("/cargo/invoices/add")
async def create_invoice(request: Request, user: dict = Depends(require_menu("vehicles"))):
    form = dict(await request.form())
    try:
        payload = await _invoice_payload_from_form(form, request, user)
        buyer = get_company(payload["buyer_company_id"])
        if not buyer:
            raise ValueError("Không tìm thấy đơn vị khách hàng.")
        content = build_saved_invoice_workbook(payload, payload["lines"], buyer)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=422)
    filename = f"Hoa_don_van_chuyen_{int(payload['invoice_number']):08d}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "no-store"},
    )


@router.post("/cargo/invoices/push-bkav")
async def push_invoice_form_to_bkav(request: Request, user: dict = Depends(require_menu("vehicles"))):
    form = dict(await request.form())
    try:
        payload = await _invoice_payload_from_form(form, request, user)
        buyer = get_company(payload["buyer_company_id"])
        seller = get_company(payload["seller_company_id"])
        if not buyer or not seller:
            raise ValueError("Thiếu thông tin đơn vị mua hoặc đơn vị xuất hóa đơn.")
        result = await run_in_threadpool(push_form_invoice_to_bkav, payload, buyer, seller)
    except (ValueError, BkavUncertainError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=422)
    except Exception:
        logger.exception("BKAV direct invoice push failed")
        return JSONResponse({"ok": False, "error": "Không thể hoàn tất gửi BKAV. Hãy kiểm tra trạng thái hóa đơn trên BKAV."}, status_code=502)
    return {"ok": True, **result, "message": "Đã đồng bộ hóa đơn trực tiếp sang BKAV."}


@router.get("/cargo/bkav/{invoice_guid}/edit", include_in_schema=False)
async def redirect_bkav_invoice_edit(
    invoice_guid: UUID,
    user: dict = Depends(require_menu("vehicles")),
):
    return RedirectResponse(f"/admin/vehicles/cargo/invoices/{invoice_guid}/edit", status_code=303)


def _saved_invoice_for_action(invoice_guid: UUID, request: Request, user: dict) -> tuple[dict, list[dict]]:
    """Read a saved invoice and all its vehicle lines within the active company."""
    invoice = db._execute_query("""
        SELECT * FROM public.bkav_invoices WHERE invoice_guid = %s
    """, (str(invoice_guid),), fetch=True, commit=False)
    if not invoice:
        raise HTTPException(status_code=404, detail="Không tìm thấy hóa đơn.")
    active_company_id = get_menu_context(request, user).get("active_company_id")
    if active_company_id is not None and invoice["seller_company_id"] != active_company_id:
        raise HTTPException(status_code=403, detail="Hóa đơn không thuộc đơn vị đang chọn.")
    lines = db._execute_query("""
        SELECT line.*, vehicle.plate_number AS plate_number
        FROM public.bkav_invoice_lines line
        LEFT JOIN public.plate_whitelist vehicle ON vehicle.id = line.vehicle_id
        WHERE line.invoice_guid = %s ORDER BY line.line_number
    """, (str(invoice_guid),), fetchall=True, commit=False) or []
    return dict(invoice), [dict(line) for line in lines]


@router.post("/cargo/invoices/{invoice_guid}/push-bkav")
async def push_saved_invoice_to_bkav_endpoint(
    invoice_guid: UUID, request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    invoice, lines = _saved_invoice_for_action(invoice_guid, request, user)
    buyer = get_company(invoice["buyer_company_id"]) if invoice.get("buyer_company_id") else None
    seller = get_company(invoice["seller_company_id"])
    back_url = f"/admin/vehicles/cargo/invoices/{invoice_guid}/edit"
    headers = getattr(request, "headers", None) or {}
    is_ajax = bool(
        hasattr(headers, "get")
        and (
            headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in headers.get("Accept", "")
        )
    )
    try:
        result = await run_in_threadpool(push_saved_invoice_to_bkav, invoice, lines, buyer, seller)
        notice = ("Hóa đơn nháp đã được đồng bộ sang BKAV."
                  if result.get("status") == "sent" else "Đã kiểm tra trạng thái hóa đơn trên BKAV.")
        key, message = "bkav_notice", notice

        image_url = None
        if result.get("status") == "sent":
            try:
                png_path, _ = await asyncio.to_thread(
                    get_or_fetch_bkav_invoice_files, str(invoice_guid), force_refresh=True
                )
                if png_path and png_path.exists():
                    image_url = f"/upload/bkav_invoices/{png_path.name}?v={png_path.stat().st_mtime_ns}"
                    try:
                        from frontend.modules.zalo.trip_requests import send_saved_invoice_to_zalo
                        z_ok, z_msg = await send_saved_invoice_to_zalo(str(invoice_guid), image_path=str(png_path))
                        if z_ok:
                            notice += " Đã gửi ảnh hóa đơn vào nhóm Zalo Đại Lộ Xanh."
                    except Exception as z_err:
                        logger.warning("Auto-send Zalo notice failed for %s: %s", invoice_guid, z_err)
            except Exception as img_err:
                logger.warning("Auto-fetch BKAV invoice image after push failed for %s: %s", invoice_guid, img_err)

        if is_ajax:
            return JSONResponse({
                "ok": True,
                "message": notice,
                "image_url": image_url,
                "invoice_guid": str(invoice_guid),
                "download_url": f"/admin/vehicles/cargo/invoices/{invoice_guid}/bkav-image?download=1",
            })
    except (ValueError, BkavUncertainError) as exc:
        key, message = "bkav_error", str(exc)[:300]
        if is_ajax:
            return JSONResponse({"ok": False, "error": message}, status_code=400)
    except Exception:
        logger.exception("BKAV push failed for saved invoice %s", invoice_guid)
        key, message = "bkav_error", "Không thể hoàn tất đồng bộ. Kiểm tra BKAV trước khi thử lại."
        if is_ajax:
            return JSONResponse({"ok": False, "error": message}, status_code=500)
    return RedirectResponse(back_url + "?" + urllib.parse.urlencode({key: message}), status_code=303)


@router.get("/cargo/invoices/{invoice_guid}/qr")
async def get_saved_invoice_qr(
    invoice_guid: UUID, request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    invoice, _ = _saved_invoice_for_action(invoice_guid, request, user)
    seller = get_company(invoice["seller_company_id"])
    if not seller or not seller.get("bank_account") or not seller.get("bank_name"):
        return JSONResponse({"ok": False, "error": "Đơn vị phát hành chưa có tài khoản và ngân hàng."}, status_code=422)
    bank_name = str(seller["bank_name"]).strip().upper()
    bank_code = BANK_CODE_MAP.get(bank_name, bank_name)
    account_no = str(seller["bank_account"]).strip()
    amount = (int(round(float(invoice["debt_amount"])))
              if invoice.get("payment_status") == "debt" and invoice.get("debt_amount")
              else int(round(float(invoice.get("gross") or 0))))
    info = f"HD {int(invoice['invoice_number']):08d}"
    params = {"amount": str(amount), "addInfo": info,
              "accountName": _remove_accents(str(seller["name"])).upper()}
    url = f"https://img.vietqr.io/image/{bank_code}-{account_no}-compact2.png?{urllib.parse.urlencode(params)}"
    return {"ok": True, "qr_url": url}


@router.get("/cargo/invoices/{invoice_guid}/bkav-image-info")
async def get_saved_invoice_bkav_image_info(
    invoice_guid: UUID, request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    invoice, _ = _saved_invoice_for_action(invoice_guid, request, user)
    try:
        png_path, pdf_path = await run_in_threadpool(
            get_or_fetch_bkav_invoice_files, str(invoice_guid), force_refresh=True)
        return {
            "ok": True,
            "image_url": f"/upload/bkav_invoices/{png_path.name}?v={png_path.stat().st_mtime_ns}",
            "pdf_url": f"/admin/vehicles/cargo/invoices/{invoice_guid}/bkav-pdf",
            "download_url": f"/admin/vehicles/cargo/invoices/{invoice_guid}/bkav-image?download=1",
            "invoice_number": invoice.get("invoice_number"),
            "invoice_serial": invoice.get("invoice_serial"),
        }
    except Exception as exc:
        logger.exception("Failed to get BKAV invoice image for %s", invoice_guid)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/cargo/invoices/{invoice_guid}/send-zalo")
async def send_saved_invoice_to_zalo_endpoint(
    invoice_guid: UUID, request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    _saved_invoice_for_action(invoice_guid, request, user)
    from frontend.modules.zalo.trip_requests import send_saved_invoice_to_zalo
    ok, msg = await send_saved_invoice_to_zalo(str(invoice_guid))
    if not ok:
        return JSONResponse({"ok": False, "error": msg}, status_code=400)
    return JSONResponse({"ok": True, "message": msg})


@router.get("/cargo/invoices/{invoice_guid}/bkav-image")
async def get_saved_invoice_bkav_image(
    invoice_guid: UUID, request: Request,
    download: int = Query(0),
    user: dict = Depends(require_menu("vehicles")),
):
    invoice, _ = _saved_invoice_for_action(invoice_guid, request, user)
    try:
        png_path, _ = await run_in_threadpool(get_or_fetch_bkav_invoice_files, str(invoice_guid))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not png_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh hóa đơn.")

    filename = f"Hoa_don_{int(invoice.get('invoice_number') or 0):08d}_{invoice.get('invoice_serial') or ''}.png"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'} if download else {}
    return FileResponse(png_path, media_type="image/png", headers=headers)


@router.get("/cargo/invoices/{invoice_guid}/bkav-pdf")
async def get_saved_invoice_bkav_pdf(
    invoice_guid: UUID, request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    invoice, _ = _saved_invoice_for_action(invoice_guid, request, user)
    try:
        _, pdf_path = await run_in_threadpool(get_or_fetch_bkav_invoice_files, str(invoice_guid))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy file PDF hóa đơn.")

    filename = f"Hoa_don_{int(invoice.get('invoice_number') or 0):08d}_{invoice.get('invoice_serial') or ''}.pdf"
    return FileResponse(pdf_path, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/cargo/{trip_id}/bkav-image-info")
async def get_trip_bkav_image_info(
    trip_id: int, request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    link = db._execute_query(
        "SELECT invoice_guid FROM public.bkav_invoice_imports WHERE trip_id = %s",
        (trip_id,), fetch=True, commit=False,
    )
    if not link:
        push = db._execute_query(
            "SELECT invoice_guid FROM public.bkav_invoice_pushes WHERE trip_id = %s",
            (trip_id,), fetch=True, commit=False,
        )
        if push and push.get("invoice_guid"):
            link = push
    saved_link = db._execute_query(
        "SELECT invoice_guid FROM public.bkav_saved_invoice_links WHERE partner_invoice_id = %s",
        (trip_id,), fetch=True, commit=False,
    )
    target_guid = UUID(str(saved_link["invoice_guid"])) if (saved_link and saved_link.get("invoice_guid")) else UUID(str(link["invoice_guid"]))
    inv_check = db._execute_query(
        "SELECT 1 FROM public.bkav_invoices WHERE invoice_guid = %s",
        (str(target_guid),), fetch=True, commit=False,
    )
    if inv_check:
        return await get_saved_invoice_bkav_image_info(target_guid, request, user)

    try:
        png_path, pdf_path = await run_in_threadpool(
            get_or_fetch_bkav_invoice_files, str(link["invoice_guid"]), force_refresh=True)
        return {
            "ok": True,
            "image_url": f"/upload/bkav_invoices/{png_path.name}?v={png_path.stat().st_mtime_ns}",
            "pdf_url": f"/upload/bkav_invoices/{pdf_path.name}",
            "download_url": f"/upload/bkav_invoices/{png_path.name}",
        }
    except Exception as exc:
        logger.exception("Failed to get BKAV invoice image for trip %s", trip_id)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/cargo/invoices/{invoice_guid}/export-invoice")
async def export_saved_invoice(
    invoice_guid: UUID, request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    invoice, lines = _saved_invoice_for_action(invoice_guid, request, user)
    company = get_company(invoice["buyer_company_id"]) if invoice.get("buyer_company_id") else None
    if not company:
        raise HTTPException(status_code=422, detail="Chọn đơn vị khách hàng trước khi xuất hóa đơn.")
    try:
        content = build_saved_invoice_workbook(invoice, lines, company)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = f"Hoa_don_van_chuyen_{int(invoice['invoice_number']):08d}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "no-store"},
    )


@router.get("/cargo/invoices/{invoice_guid}/export-dlx-excel")
async def export_saved_invoice_dlx_excel(
    invoice_guid: UUID, request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    invoice, lines = _saved_invoice_for_action(invoice_guid, request, user)
    buyer = get_company(invoice["buyer_company_id"]) if invoice.get("buyer_company_id") else None
    seller = get_company(invoice["seller_company_id"])
    if not buyer or not seller:
        raise HTTPException(status_code=422, detail="Hóa đơn thiếu thông tin khách hàng hoặc đơn vị xuất.")
    try:
        content = build_dlx_trip_workbook(invoice, lines, buyer, seller)
    except Exception as exc:
        logger.exception("Failed to build DLX workbook for %s", invoice_guid)
        raise HTTPException(status_code=422, detail=f"Không thể xuất file Excel: {exc}") from exc
    filename = f"Ho_so_chuyen_hang_HD{int(invoice['invoice_number']):08d}_DLX.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/cargo/invoices/{invoice_guid}/export-document")
async def export_saved_invoice_document(
    invoice_guid: UUID, request: Request,
    kind: str = Query("hdvc"),
    format: Optional[str] = Query(None),
    download: Optional[str] = Query(None),
    view: Optional[str] = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    valid_kinds = ("hdvc", "bbvc", "dntt", "bbtl", "all", "hdnt", "bbbg", "both")
    if kind not in valid_kinds:
        raise HTTPException(status_code=422, detail="Loại chứng từ không hợp lệ.")
    invoice, lines = _saved_invoice_for_action(invoice_guid, request, user)
    buyer = get_company(invoice["buyer_company_id"]) if invoice.get("buyer_company_id") else None
    seller = get_company(invoice["seller_company_id"])
    if not buyer or not seller:
        raise HTTPException(status_code=422, detail="Hóa đơn thiếu thông tin khách hàng hoặc đơn vị xuất.")

    inv_num_fmt = f"HD{int(invoice['invoice_number']):08d}"

    # 1. New Đại Lộ Xanh document formats: all, bbvc, dntt, bbtl, hdvc
    if kind in ("all", "bbvc", "dntt", "bbtl", "hdvc"):
        if view == "html" or format == "html" or (not format and not download):
            html_preview = render_dlx_document_html(
                invoice, lines, buyer, seller, kind=kind, preview=True,
                invoice_guid=str(invoice_guid)
            )
            return HTMLResponse(content=html_preview)

        if format == "pdf" or download == "pdf":
            html_for_pdf = render_dlx_document_html(
                invoice, lines, buyer, seller, kind=kind, preview=False,
                invoice_guid=str(invoice_guid)
            )
            from frontend.modules.transport.contract import render_pdf_from_html
            pdf_bytes = render_pdf_from_html(html_for_pdf, title=f"{kind.upper()}_{inv_num_fmt}")
            filename = f"{kind.upper()}_{inv_num_fmt}.pdf"
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
            )

        if format == "docx" or download == "docx" or format == "doc" or download == "doc":
            doc_bytes, prefix = render_dlx_document_docx(invoice, lines, buyer, seller, kind=kind)
            filename = f"{prefix}.docx"
            return Response(
                content=doc_bytes,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
            )

    # 2. Legacy HĐNT / BBBG formats
    trip = {
        "invoice_date": invoice["invoice_date"], "service_name": invoice.get("service_name"),
        "vat_rate": invoice.get("vat_rate"),
        "cargo_description": ", ".join(dict.fromkeys(
            str(line.get("cargo_description") or line.get("description") or "").strip()
            for line in lines if (line.get("cargo_description") or line.get("description")))),
        "route_segments": [
            {"description": line.get("description") or "",
             "plate_number": line.get("plate_number") or "",
             "origin": line.get("origin"), "destination": line.get("destination"),
             "amount": line.get("amount")}
            for line in lines
        ],
    }

    if view == "html" or format == "html":
        html_preview = render_hdnt_bbbg_html(
            trip, buyer, seller, kind=kind, preview=True,
            invoice_guid=str(invoice_guid)
        )
        return HTMLResponse(content=html_preview)

    contract_no = compute_contract_no(trip, buyer, seller)
    prefix = {"hdnt": "HDNT", "bbbg": "BBBG", "both": "HDNT_BBBG"}.get(kind, "DOC")

    if format == "pdf" or download == "pdf":
        html_for_pdf = render_hdnt_bbbg_html(
            trip, buyer, seller, kind=kind, preview=False,
            invoice_guid=str(invoice_guid)
        )
        from frontend.modules.transport.contract import render_pdf_from_html, _remove_accents
        pdf_bytes = render_pdf_from_html(html_for_pdf, title=f"Chung_tu_{contract_no}")
        safe_no = re.sub(r"[^A-Za-z0-9_-]", "_", _remove_accents(contract_no))
        filename = f"{prefix}_{safe_no}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
        )

    renderer = {"hdnt": render_hdnt_docx, "bbbg": render_bbbg_docx,
                "both": render_hdnt_bbbg_combined_docx}[kind]
    content, contract_no = renderer(trip, buyer, seller)
    req_format = download or format or "docx"
    return _handle_word_download(content, contract_no, prefix, req_format)


@router.get("/cargo/invoices/{invoice_guid}/edit")
async def edit_bkav_invoice_page(
    invoice_guid: UUID, request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    invoice = db._execute_query("""
        SELECT invoice.*, buyer.name AS buyer_name
        FROM public.bkav_invoices invoice
        LEFT JOIN public.invoice_companies buyer ON buyer.id = invoice.buyer_company_id
        WHERE invoice.invoice_guid = %s
    """, (str(invoice_guid),), fetch=True, commit=False)
    if not invoice:
        return HTMLResponse("Không tìm thấy hóa đơn.", status_code=404)
    if ctx.get("active_company_id") is not None and invoice["seller_company_id"] != ctx["active_company_id"]:
        return HTMLResponse("Hóa đơn không thuộc đơn vị đang chọn.", status_code=403)
    lines = db._execute_query("""
        SELECT line.*, vehicle.plate_number AS plate_number,
               vehicle.fuel_consumption
        FROM public.bkav_invoice_lines line
        LEFT JOIN public.plate_whitelist vehicle ON vehicle.id = line.vehicle_id
        WHERE line.invoice_guid = %s ORDER BY line.line_number
    """, (str(invoice_guid),), fetchall=True, commit=False) or []
    ctx = _invoice_form_context(request, user, invoice=dict(invoice), lines=[dict(line) for line in lines])
    ctx["back_url"] = f"/admin/vehicles/cargo?month={invoice['invoice_date']:%Y-%m}"
    return templates.TemplateResponse("pages/cargo/invoice_form.html", {"request": request, **ctx})


@router.post("/cargo/bkav/{invoice_guid}/delete", include_in_schema=False)
@router.post("/cargo/invoices/{invoice_guid}/delete")
async def delete_bkav_invoice_endpoint(
    invoice_guid: UUID,
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    active_company_id = get_menu_context(request, user).get("active_company_id")
    deleted = db._execute_query(
        "DELETE FROM public.bkav_invoices WHERE invoice_guid = %s"
        + (" AND seller_company_id = %s" if active_company_id is not None else "")
        + " RETURNING invoice_guid",
        (str(invoice_guid), active_company_id) if active_company_id is not None
        else (str(invoice_guid),), fetch=True, commit=True,
    )
    if not deleted:
        return JSONResponse({"ok": False, "message": "Không tìm thấy hóa đơn."}, status_code=404)
    try:
        delete_stored_bkav_invoice_files(str(invoice_guid))
    except Exception:
        logger.exception("Could not remove files for deleted BKAV invoice %s", invoice_guid)
    return {"ok": True, "count": 1}


@router.post("/cargo/{trip_id}/delete")
@router.post("/{vehicle_id}/cargo/{trip_id}/delete")
async def delete_cargo_trip_endpoint(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu("vehicles")),
):
    if not delete_cargo_trip(trip_id, vehicle_id):
        return JSONResponse({"ok": False, "message": "Không tìm thấy chuyến hàng."}, status_code=404)
    return {"ok": True}


class BulkDeleteTripsInput(BaseModel):
    ids: list[str | int]


@router.post("/cargo/bulk-delete")
@router.post("/{vehicle_id}/cargo/bulk-delete")
async def bulk_delete_cargo_trips_endpoint(
    payload: BulkDeleteTripsInput,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu("vehicles")),
):
    if not payload.ids:
        return JSONResponse({"ok": False, "message": "Chưa chọn chuyến hàng nào."}, status_code=400)
    trip_ids, invoice_guids = [], []
    for raw_key in payload.ids:
        key = str(raw_key)
        if key.startswith("trip:") and key[5:].isdigit():
            trip_ids.append(int(key[5:]))
        elif key.isdigit():
            trip_ids.append(int(key))
        elif key.startswith("invoice:"):
            try:
                invoice_guids.append(str(UUID(key[8:])))
            except ValueError:
                return JSONResponse({"ok": False, "message": "Mục đã chọn không hợp lệ."}, status_code=422)
        else:
            return JSONResponse({"ok": False, "message": "Mục đã chọn không hợp lệ."}, status_code=422)
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM public.cargo_trips WHERE id = ANY(%s) "
                + ("AND vehicle_id = %s " if vehicle_id is not None else "")
                + "RETURNING id",
                (trip_ids, vehicle_id) if vehicle_id is not None else (trip_ids,),
            )
            count = len(cursor.fetchall())
            cursor.execute(
                "DELETE FROM public.bkav_invoices invoice WHERE invoice.invoice_guid = ANY(%s::uuid[]) "
                + ("AND EXISTS (SELECT 1 FROM public.bkav_invoice_lines line "
                   "WHERE line.invoice_guid = invoice.invoice_guid AND line.vehicle_id = %s) "
                   if vehicle_id is not None else "")
                + "RETURNING invoice_guid",
                (invoice_guids, vehicle_id) if vehicle_id is not None else (invoice_guids,),
            )
            deleted_invoice_guids = [str(row[0]) for row in cursor.fetchall()]
            count += len(deleted_invoice_guids)
        conn.commit()
    for guid in deleted_invoice_guids:
        try:
            delete_stored_bkav_invoice_files(guid)
        except Exception:
            logger.exception("Could not remove files for deleted BKAV invoice %s", guid)
    if not count:
        return JSONResponse({"ok": False, "message": "Không tìm thấy chuyến hàng cần xóa."}, status_code=404)
    return {"ok": True, "count": count}


@router.post("/cargo/scan")
@router.post("/{vehicle_id}/cargo/scan")
async def scan_handwriting(
    request: Request,
    vehicle_id: Optional[int] = None,
    image: UploadFile = File(...),
    user: dict = Depends(require_menu("vehicles")),
):
    _cleanup_scan_reviews()
    if vehicle_id is None:
        form = await request.form()
        v_form = form.get("vehicle_id")
        if v_form and str(v_form).isdigit():
            vehicle_id = int(v_form)
        else:
            first_v = db.select("plate_whitelist", order_by="plate_number ASC", limit=1, fetchall=False)
            vehicle_id = first_v["id"] if first_v else None

    if not vehicle_id:
        return RedirectResponse(url="/admin/vehicles/cargo?scan_error=1", status_code=303)

    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles/cargo?scan_error=1", status_code=303)

    image_bytes = await image.read()

    scan_result = extract_trip_candidates_from_document(
        image_bytes,
        filename=image.filename or "",
        content_type=image.content_type or "",
    )

    token = secrets.token_urlsafe(24)
    _save_scan_review(token, {
        "created_at": time.time(),
        "vehicle_id": vehicle_id,
        "user_id": user.get("id"),
        "filename": image.filename or "",
        "content_type": image.content_type or "",
        "scan_result": scan_result,
    })
    return RedirectResponse(url=f"/admin/vehicles/cargo/scan/review?token={token}&vehicle_id={vehicle_id}", status_code=303)


@router.get("/cargo/scan/review")
@router.get("/{vehicle_id}/cargo/scan/review")
async def review_cargo_scan(
    request: Request,
    token: str = Query(...),
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu("vehicles")),
):
    _cleanup_scan_reviews()
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"

    review = _load_scan_review(token)
    if not review or review.get("user_id") != user.get("id"):
        return RedirectResponse(url="/admin/vehicles/cargo?scan_error=1", status_code=303)

    vid = vehicle_id or review.get("vehicle_id")
    vehicle = db.select("plate_whitelist", conditions={"id": vid}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles/cargo?scan_error=1", status_code=303)

    scan_result = review["scan_result"]
    ctx["vehicle"] = vehicle
    ctx["vehicle_id"] = vid
    ctx["token"] = token
    ctx["filename"] = review.get("filename", "")
    ctx["lines"] = scan_result.get("lines", [])
    ctx["candidates"] = scan_result.get("candidates", [])
    ctx["line_count"] = scan_result.get("line_count", 0)
    ctx["candidate_count"] = scan_result.get("candidate_count", 0)
    ctx["quality"] = scan_result.get("quality", {})
    ctx["quality_warnings"] = scan_result.get("quality_warnings", [])
    return templates.TemplateResponse("pages/cargo/scan_review.html", {"request": request, **ctx})


@router.post("/cargo/scan/confirm")
@router.post("/{vehicle_id}/cargo/scan/confirm")
async def confirm_cargo_scan(
    request: Request,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu("vehicles")),
):
    _cleanup_scan_reviews()
    form = await request.form()
    token = str(form.get("token") or "")
    review = _load_scan_review(token)

    vid = vehicle_id or (int(form.get("vehicle_id")) if form.get("vehicle_id") and str(form.get("vehicle_id")).isdigit() else (review.get("vehicle_id") if review else None))
    if not review or not vid or review.get("user_id") != user.get("id"):
        return RedirectResponse(url="/admin/vehicles/cargo?scan_error=1", status_code=303)

    vehicle = db.select("plate_whitelist", conditions={"id": vid}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles/cargo?scan_error=1", status_code=303)

    skipped = 0
    scan_result = review.get("scan_result", {})
    candidates = scan_result.get("candidates", [])
    pending_rows = []
    for row_index in range(len(candidates)):
        idx = str(row_index)
        if form.get(f"skip_{idx}") == "on":
            skipped += 1
            continue
        departure_time = str(form.get(f"departure_{idx}") or "").strip()
        if not departure_time:
            skipped += 1
            continue
        corrected = {
            "departure_time": departure_time,
            "return_time": str(form.get(f"return_{idx}") or "").strip(),
            "origin": str(form.get(f"origin_{idx}") or "").strip(),
            "destination": str(form.get(f"destination_{idx}") or "").strip(),
            "fuel_liters": str(form.get(f"fuel_{idx}") or "").strip(),
            "freight_revenue": str(form.get(f"revenue_{idx}") or "").strip(),
            "distance_km": str(form.get(f"distance_{idx}") or "").strip(),
        }
        trip_fields, error = _normalise_trip_fields({**corrected, "fuel_price_per_liter": None})
        if error:
            return _trip_form_error(
                f"Chuyến {row_index + 1}: {error}",
                f"/admin/vehicles/cargo/scan/review?token={urllib.parse.quote(token)}&vehicle_id={vid}",
            )
        corrected_line_texts = {
            key.rsplit("_", 1)[1]: str(value or "").strip()
            for key, value in form.items()
            if key.startswith(f"line_text_{idx}_")
        }
        pending_rows.append((row_index, trip_fields, corrected, corrected_line_texts))

    if not add_reviewed_cargo_trips(
        vid, vehicle["plate_number"], [trip_fields for _, trip_fields, _, _ in pending_rows],
        seller_company_id=request.session.get("active_company_id"),
    ):
        return HTMLResponse("Không thể lưu các chuyến hàng đã duyệt.", status_code=500)

    for row_index, trip_fields, corrected, corrected_line_texts in pending_rows:
        _persist_ocr_sample(token, vid, str(row_index), candidates[row_index], corrected, corrected_line_texts)

    _delete_scan_review(token)
    return RedirectResponse(url=f"/admin/vehicles/cargo?vehicle_id={vid}&scanned={len(pending_rows)}&scan_skipped={skipped}", status_code=303)
