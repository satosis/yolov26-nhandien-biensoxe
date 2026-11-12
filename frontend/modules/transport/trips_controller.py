import asyncio
import base64
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
from datetime import datetime, date
from uuid import UUID, uuid4
from typing import Any, Optional
from fastapi import Request, Depends, UploadFile, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from pydantic import BaseModel
from starlette.responses import RedirectResponse
from pathlib import Path
from frontend.deps import get_current_company_id, get_menu_context, require_menu, templates
from frontend.modules.transport.invoice_store import (
    InvoiceNumberTaken,
    delete_sales_invoices,
    next_invoice_number_and_serial,
    save_sales_invoice,
)
from frontend.modules.bank_transfer import BANK_CODE_MAP, remove_accents as _remove_accents
from frontend.modules.invoices.companies import (
    TaxLookupError,
    find_company_by_name,
    get_company,
    get_company_bank_accounts,
    list_companies,
    refresh_company_from_tax,
    lookup_tax_code,
    save_company,
)
from frontend.modules.invoices.debt_service import adjust_company_debt
from frontend.modules.invoices.export import (
    CRANE_BY_SHIFT_SERVICE, DEFAULT_SERVICE_NAME, REMOVED_INVOICE_SERVICES,
    build_bkav_invoice, build_saved_invoice_workbook, cargo_description_from_service,
    format_vat_percent, format_vat_rate, is_crane_by_shift_service,
    parse_payment_method, parse_vat_percent, parse_vat_rate,
)
from frontend.modules.invoices.bkav_push import (
    is_configured as bkav_is_configured,
    push_invoice as push_invoice_to_bkav,
    push_state as bkav_push_state,
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
from frontend.modules.enterprise.sales_service import fold_text, _folded
from frontend.modules.invoices.share_link import public_base_url, share_path
from frontend.context.list_state import recall_list_url, remember_list_url
from frontend.modules.invoices.replacement_note import fill_missing_notes
from frontend.modules.transport.bulk_payment_status import mark_unpaid
from frontend.modules.transport.catalog import list_items as list_catalog_items
from frontend.modules.transport.date_utils import month_bounds
from frontend.modules.transport.route_segments import parse_route_segments, route_totals
from frontend.modules.transport.invoice_accounting import (
    ACCOUNT_KEYS,
    effective_accounts,
    load_saved_accounts,
)
from frontend.modules.transport.invoice_forms import parse_invoice_form
from frontend.modules.transport.invoice_locations import google_normalized_place, load_commune_directory, normalize_invoice_location, split_invoice_destination_stops
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
    confirm_invoice_not_created,
    get_trip_request,
    register_trip_request,
    send_invoice_notice,
)
from frontend.modules.transport.vehicle_matching import (
    resolve_vehicle_from_identifier,
)
from backend.ai.handwriting_ocr import extract_trip_candidates_from_document
from frontend.formatting.http_headers import content_disposition
from backend.services.google_maps import (
    compute_routes_api as _gmaps_routes_distance,
    driving_distance_km as _gmaps_driving_distance,
    route_distance_km,
    geocode_address_google,
    reverse_geocode_google,
    check_google_maps_services_status,
)
import frontend.modules.transport.trips_queries as queries

logger = logging.getLogger(__name__)


PER_PAGE = 100
SCAN_REVIEW_TTL = 30 * 60
SCAN_REVIEW_DIR = Path(__file__).parent.parent / ".runtime" / "cargo_scan_reviews"
OCR_SAMPLE_DIR = Path(__file__).parent.parent / ".runtime" / "cargo_ocr_samples"
_SCAN_REVIEWS: dict[str, dict] = {}
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
        q = str(document_query).strip()
        if q:
            raw_like = f"%{q}%"
            folded_q = fold_text(q)
            folded_like = f"%{folded_q}%"
            plate_clean = re.sub(r"[^A-Za-z0-9]", "", q).upper()
            digits_only = re.sub(r"[.,]", "", q)

            or_clauses, or_params = [], []
            if invoice:
                or_clauses.append(
                    f"(LPAD({alias}.invoice_number::text, 8, '0') ILIKE %s "
                    f"OR {alias}.invoice_number::text ILIKE %s "
                    f"OR (COALESCE({alias}.invoice_serial, '') || '-' || "
                    f"LPAD({alias}.invoice_number::text, 8, '0')) ILIKE %s)"
                )
                or_params.extend([raw_like, raw_like, raw_like])

                or_clauses.append(
                    f"EXISTS (SELECT 1 FROM public.invoice_companies ic "
                    f"WHERE ic.id = {alias}.buyer_company_id AND {_folded('ic.name')} ILIKE %s)"
                )
                or_params.append(folded_like)

                or_clauses.append(f"COALESCE({alias}.buyer_tax_code, '') ILIKE %s")
                or_params.append(raw_like)

                or_clauses.append(f"{_folded(f'{alias}.service_name')} ILIKE %s")
                or_params.append(folded_like)

                or_clauses.append(
                    f"EXISTS (SELECT 1 FROM public.bkav_invoice_lines line WHERE line.invoice_guid = {alias}.invoice_guid AND ("
                    f"{_folded('line.description')} ILIKE %s OR "
                    f"{_folded('line.origin')} ILIKE %s OR "
                    f"{_folded('line.destination')} ILIKE %s OR "
                    f"{_folded('line.cargo_description')} ILIKE %s"
                    f"))"
                )
                or_params.extend([folded_like, folded_like, folded_like, folded_like])

                if len(plate_clean) >= 3:
                    or_clauses.append(
                        f"EXISTS (SELECT 1 FROM public.bkav_invoice_lines line "
                        f"JOIN public.plate_whitelist vehicle ON vehicle.id = line.vehicle_id "
                        f"WHERE line.invoice_guid = {alias}.invoice_guid AND ("
                        f"vehicle.plate_number ILIKE %s OR "
                        f"regexp_replace(upper(vehicle.plate_number), '[^A-Z0-9]', '', 'g') LIKE %s"
                        f"))"
                    )
                    or_params.extend([raw_like, f"%{plate_clean}%"])
                else:
                    or_clauses.append(
                        f"EXISTS (SELECT 1 FROM public.bkav_invoice_lines line "
                        f"JOIN public.plate_whitelist vehicle ON vehicle.id = line.vehicle_id "
                        f"WHERE line.invoice_guid = {alias}.invoice_guid AND "
                        f"vehicle.plate_number ILIKE %s)"
                    )
                    or_params.append(raw_like)

                or_clauses.append(f"COALESCE({alias}.payment_method, '') ILIKE %s")
                or_params.append(raw_like)

                or_clauses.append(
                    f"(to_char({alias}.invoice_date, 'DD/MM/YYYY') ILIKE %s OR {alias}.invoice_date::text ILIKE %s)"
                )
                or_params.extend([raw_like, raw_like])

                if digits_only.isdigit() and len(digits_only) >= 4:
                    or_clauses.append(
                        f"(round(COALESCE({alias}.gross, 0))::bigint::text ILIKE %s "
                        f"OR round(COALESCE({alias}.subtotal, 0))::bigint::text ILIKE %s "
                        f"OR round(COALESCE({alias}.debt_amount, 0))::bigint::text ILIKE %s)"
                    )
                    amt_like = f"%{digits_only}%"
                    or_params.extend([amt_like, amt_like, amt_like])

            else:
                or_clauses.append(f"COALESCE({alias}.document_number, '') ILIKE %s")
                or_params.append(raw_like)

                or_clauses.append(
                    f"EXISTS (SELECT 1 FROM public.invoice_companies ic "
                    f"WHERE ic.id = {alias}.company_id AND {_folded('ic.name')} ILIKE %s)"
                )
                or_params.append(folded_like)

                or_clauses.append(f"{_folded(f'{alias}.service_name')} ILIKE %s")
                or_params.append(folded_like)

                if len(plate_clean) >= 3:
                    or_clauses.append(
                        f"(COALESCE({alias}.plate_number, '') ILIKE %s OR "
                        f"regexp_replace(upper(COALESCE({alias}.plate_number, '')), '[^A-Z0-9]', '', 'g') LIKE %s OR "
                        f"EXISTS (SELECT 1 FROM public.plate_whitelist vehicle WHERE vehicle.id = {alias}.vehicle_id AND ("
                        f"vehicle.plate_number ILIKE %s OR "
                        f"regexp_replace(upper(vehicle.plate_number), '[^A-Z0-9]', '', 'g') LIKE %s)))"
                    )
                    or_params.extend([raw_like, f"%{plate_clean}%", raw_like, f"%{plate_clean}%"])
                else:
                    or_clauses.append(
                        f"(COALESCE({alias}.plate_number, '') ILIKE %s OR "
                        f"EXISTS (SELECT 1 FROM public.plate_whitelist vehicle WHERE vehicle.id = {alias}.vehicle_id AND "
                        f"vehicle.plate_number ILIKE %s))"
                    )
                    or_params.extend([raw_like, raw_like])

                or_clauses.append(
                    f"({_folded(f'{alias}.origin')} ILIKE %s OR "
                    f"{_folded(f'{alias}.destination')} ILIKE %s OR "
                    f"{_folded(f'{alias}.cargo_description')} ILIKE %s)"
                )
                or_params.extend([folded_like, folded_like, folded_like])

                or_clauses.append(
                    f"(to_char({alias}.invoice_date, 'DD/MM/YYYY') ILIKE %s OR "
                    f"{alias}.invoice_date::text ILIKE %s OR "
                    f"to_char({alias}.departure_time, 'DD/MM/YYYY') ILIKE %s)"
                )
                or_params.extend([raw_like, raw_like, raw_like])

                or_clauses.append(f"COALESCE({alias}.payment_method, '') ILIKE %s")
                or_params.append(raw_like)

                if digits_only.isdigit() and len(digits_only) >= 4:
                    or_clauses.append(
                        f"(round(COALESCE({alias}.freight_revenue, 0))::bigint::text ILIKE %s "
                        f"OR round(COALESCE({alias}.debt_amount, 0))::bigint::text ILIKE %s "
                        f"OR round(COALESCE({alias}.source_revenue_gross, 0))::bigint::text ILIKE %s)"
                    )
                    amt_like = f"%{digits_only}%"
                    or_params.extend([amt_like, amt_like, amt_like])

            clauses.append(f"({' OR '.join(or_clauses)})")
            params.extend(or_params)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def count_cargo_history_groups(**filters) -> int:
    trip_where, trip_params = _history_conditions("trip", **filters)
    invoice_where, invoice_params = _history_conditions("invoice", invoice=True, **filters)
    row = queries.count_cargo_history_groups_select_cargo_trips(trip_where, invoice_where, trip_params, invoice_params)
    return row["count"] if row else 0


def get_cargo_history_groups(*, limit: int, offset: int, **filters) -> list[dict]:
    """Return one history row per manual trip or source BKAV invoice."""
    trip_where, trip_params = _history_conditions("trip", **filters)
    invoice_where, invoice_params = _history_conditions("invoice", invoice=True, **filters)
    page = queries.get_cargo_history_groups_select_cargo_trips(trip_where, invoice_where, limit, offset, trip_params, invoice_params) or []
    if not page:
        return []
    trip_ids = [int(row["record_key"]) for row in page if row["kind"] == "trip"]
    guids = [row["record_key"] for row in page if row["kind"] == "invoice"]
    trip_rows = queries.get_cargo_history_groups_select_cargo_trips_2(trip_ids) if trip_ids else []
    invoice_rows = queries.get_cargo_history_groups_select_bkav_invoices(guids) if guids else []
    missing_guids = [str(r["invoice_guid"]) for r in (invoice_rows or []) if r.get("replaces_note") is None]
    filled = fill_missing_notes(missing_guids) if missing_guids else {}
    line_rows = queries.get_cargo_history_groups_select_bkav_invoice_lines(guids) if guids else []
    lines_by_guid = {}
    for line in line_rows or []:
        lines_by_guid.setdefault(str(line["invoice_guid"]), []).append(dict(line))
    by_key = {}
    for trip in trip_rows or []:
        entry = dict(trip)
        entry["invoice_guid"] = None
        entry["invoice_lines"] = []
        entry["replaces_note"] = ""
        entry["selection_key"] = f"trip:{entry['id']}"
        by_key[("trip", str(entry["id"]))] = entry
    for invoice in invoice_rows or []:
        guid = str(invoice["invoice_guid"])
        lines = lines_by_guid.get(guid, [])
        distance_values = [Decimal(str(line["distance_km"])) for line in lines if line.get("distance_km") is not None]
        fuel_values = [Decimal(str(line["fuel_cost_total"])) for line in lines if line.get("fuel_cost_total") is not None]
        route_lines = [l for l in lines if l.get("origin") and l.get("destination")]
        plates = list(dict.fromkeys(line["plate_number"] for line in lines if line.get("plate_number")))
        entry = {
            "id": guid, "invoice_guid": guid, "selection_key": f"invoice:{guid}",
            "invoice_lines": lines, "invoice_date": invoice["invoice_date"],
            "document_number": str(invoice["invoice_number"]).zfill(8),
            "company_name": invoice["company_name"], "service_name": invoice["service_name"],
            "seller_company_name": invoice["seller_company_name"],
            "replaces_note": (invoice.get("replaces_note") if invoice.get("replaces_note") is not None else filled.get(guid, "")) or "",
            "freight_revenue": invoice["subtotal"], "source_revenue_gross": invoice["gross"],
            "vat_rate": invoice["vat_rate"], "payment_method": invoice["payment_method"],
            "payment_status": invoice["payment_status"], "debt_amount": invoice["debt_amount"],
            "fuel_cost_total": sum(fuel_values, Decimal(0)) if fuel_values else None,
            "distance_km": sum(distance_values, Decimal(0)) if distance_values else None,
            "plate_number": ", ".join(plates),
            "origin": route_lines[0]["origin"] if route_lines else (lines[0]["origin"] if lines else ""),
            "destination": route_lines[-1]["destination"] if route_lines else (lines[-1]["destination"] if lines else ""),
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
        driver_row = queries.get_default_seller_company_id_select_transport_drivers(vehicle_id)
        if driver_row:
            driver_code = driver_row.get("driver_code")
            if not driver_name:
                driver_name = driver_row.get("full_name")

    matched_user = None
    if driver_code:
        matched_user = queries.get_default_seller_company_id_select_users(driver_code)
    if not matched_user and driver_name:
        matched_user = queries.get_default_seller_company_id_select_users_2(driver_name)
    if matched_user and matched_user.get("company_ids"):
        cids = [int(cid) for cid in matched_user["company_ids"] if cid is not None]
        if cids:
            return cids[0]

    if current_user and current_user.get("id"):
        user_row = queries.get_default_seller_company_id_select_users_3(current_user)
        if user_row and user_row.get("company_ids"):
            cids = [int(cid) for cid in user_row["company_ids"] if cid is not None]
            if cids:
                return cids[0]

    return None


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
    updated = queries.update_cargo_trip_update_cargo_trips(vehicle_id, invoice_date, company_id, origin, destination, fuel_liters, fuel_price_per_liter, freight_revenue, distance_km, vat_rate, payment_method, payment_status, debt_amount, service_name, document_number, driver_name, cargo_description, trip_quantity, cargo_weight_tons, trip_wage, trip_other_cost, trip_note, odometer_end, vehicle_owner_type, outsourced_freight, seller_company_id, trip_id, route_segments)
    if updated and isinstance(old_trip, dict):
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
    return updated


def get_cargo_trip(trip_id: int) -> dict | None:
    return queries.get_cargo_trip_select_cargo_trips(trip_id)


def list_cargo_types() -> list[str]:
    return [item["name"] for item in list_catalog_items("cargo_type")]


def _catalog_back_url(value: str | None) -> str:
    if not value or value == "/admin":
        return "/admin/vehicles"
    return value if value.startswith("/admin/") and not value.startswith("//") else "/admin/vehicles"


def _catalog_redirect(tab: str, back: str | None, *, message: str = "", error: str = "") -> RedirectResponse:
    params = urllib.parse.urlencode({"tab": tab, "back": _catalog_back_url(back), "message": message, "error": error})
    return RedirectResponse(f"/admin/transport/catalog?{params}", status_code=303)


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
    row = queries.get_next_document_number_select_cargo_trips()
    max_num = int(row["max_num"]) if row and row.get("max_num") is not None else 0
    return f"{max_num + 1:08d}"


def ensure_invoice_bill_number(trip_id: int, vehicle_id: int | None = None) -> int | None:
    trip = get_cargo_trip(trip_id)
    if not trip:
        return None
    vid = vehicle_id if vehicle_id is not None else trip.get("vehicle_id")
    bill_num = None
    if trip.get("document_number"):
        digits = re.sub(r"[^0-9]", "", str(trip["document_number"]))
        if digits:
            bill_num = int(digits)
    if bill_num is not None:
        if vid is not None:
            queries.ensure_invoice_bill_number_update_cargo_trips_3(bill_num, trip_id, vid)
        else:
            queries.ensure_invoice_bill_number_update_cargo_trips_4(bill_num, trip_id)
        return bill_num

    if vid is not None:
        row = queries.ensure_invoice_bill_number_update_cargo_trips(trip_id, vid)
    else:
        row = queries.ensure_invoice_bill_number_update_cargo_trips_2(trip_id)
    return row["invoice_bill_number"] if row else None


def delete_cargo_trip(trip_id: int, vehicle_id: int | None = None) -> bool:
    trip = get_cargo_trip(trip_id)
    if vehicle_id is not None:
        deleted = queries.delete_cargo_trip_delete_cargo_trips(trip_id, vehicle_id)
    else:
        deleted = queries.delete_cargo_trip_delete_cargo_trips_2(trip_id)
    if deleted and trip and trip.get("payment_status") == "debt" and trip.get("company_id"):
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
    return deleted


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
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
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
        if _TRIP_NUMBER_RE.fullmatch(value) is None:
            return None, f"{label} phải là số không âm hợp lệ."
        number = _num(value)
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


def _optional_transport_number(raw: dict, key: str, label: str) -> Decimal | None:
    value = str(raw.get(key) or "").strip().replace(",", ".")
    if not value:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} không hợp lệ.") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{label} không hợp lệ.")
    return number


def _normalise_invoice_trip_fields(raw: dict[str, str | None], *, generate_document_number: bool = True,
                                   preserve_paid_debt: bool = False,
                                   format_document: bool = True) -> tuple[dict | None, str | None]:
    invoice_date = str(raw.get("invoice_date") or "").strip()
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

    for key in ("document_number", "driver_name", "cargo_description", "trip_note"):
        value = str(raw.get(key) or "").strip()
        if key == "document_number":
            value = (format_document_number(value) if format_document else value) if value else (get_next_document_number() if generate_document_number else None)
        trip_fields[key] = value or None
    try:
        for key, label in (
            ("trip_quantity", "Số chuyến"),
            ("cargo_weight_tons", "Khối lượng"),
            ("trip_wage", "Lương chuyến"),
            ("trip_other_cost", "Chi phí chuyến"),
            ("odometer_end", "Km cuối đồng hồ"),
            ("outsourced_freight", "Cước trả xe thuê ngoài"),
        ):
            trip_fields[key] = _optional_transport_number(raw, key, label)
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
    return queries.crane_transport_tax_rule_select_invoice_tax_rules(invoice_day)


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
        "<body style='font-family:\"Times New Roman\",Times,serif;font-size:16px;max-width:40rem;margin:3rem auto;padding:1rem'>"
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
            "Content-Disposition": content_disposition(filename),
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
    return None


def _source_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = str(value).strip()
    if _SOURCE_NUMBER_RE.fullmatch(text) is None:
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
    g_loc = await geocode_address_google(q)
    if g_loc and g_loc.get("lat") is not None and g_loc.get("lon") is not None:
        return g_loc

    return None


_SHORT_MAPS_HOSTS = {"goo.gl", "maps.app.goo.gl", "maps.app"}
_MAPS_URL_RE = re.compile(
    r"https://(?:google\.com|www\.google\.com|maps\.google\.com|goo\.gl|maps\.app\.goo\.gl|maps\.app)(?:[/?#][^\x00-\x20\x7f]*)?",
    re.IGNORECASE,
)


def _is_maps_url(value: str) -> bool:
    return _MAPS_URL_RE.fullmatch(value) is not None


async def _location_label_for_storage(value: str | None) -> str | None:
    text = (value or "").strip()
    if not _is_maps_url(text):
        return value
    location = await _resolve_location(text)
    label = location.get("label") if location else None
    return label if isinstance(label, str) and label.strip() and label != text else value


def _bkav_sync_context(request: Request, user: dict, payload: BkavSyncInput):
    vehicle = queries.bkav_sync_context_select_plate_whitelist(payload) if payload.vehicle_id > 0 else None
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


async def _resolve_location(address: str):
    res = await geocode_address_google(address)
    if res and "lat" in res and "lon" in res:
        return res
    return None


async def _populate_bkav_invoice_distances(invoices: list[dict], selected_guids: list[str] | None = None) -> None:
    target_invoices = invoices
    if selected_guids is not None:
        target_guids = set(selected_guids)
        target_invoices = [inv for inv in invoices if inv.get("guid") in target_guids]

    cache: dict[tuple[str, str], float] = {}

    for invoice in target_invoices:
        for segment in (invoice.get("segments") or []):
            if segment.get("is_crane"):
                continue
            if segment.get("distance_km") not in (None, ""):
                continue
            origin = str(segment.get("origin") or "").strip()
            destination = str(segment.get("destination") or "").strip()
            if not origin or not destination:
                continue
            key = (origin.casefold(), destination.casefold())
            if key in cache:
                segment["distance_km"] = str(cache[key])
                continue

            dist = None
            try:
                d, _ = await _gmaps_routes_distance(origin, destination)
                if d is not None and math.isfinite(float(d)) and float(d) > 0:
                    dist = round(float(d), 2)
            except Exception:
                pass

            if dist is None:
                try:
                    loc1 = await _resolve_location(origin)
                    loc2 = await _resolve_location(destination)
                    if loc1 and loc2:
                        lat1, lon1 = (loc1.get("lat"), loc1.get("lon"))
                        lat2, lon2 = (loc2.get("lat"), loc2.get("lon"))
                        if lat1 is not None and lat2 is not None:
                            d_km, prov = await _gmaps_driving_distance(lat1, lon1, lat2, lon2)
                            if d_km is not None and prov not in ("haversine_winding", "haversine", "estimated"):
                                dist = round(float(d_km), 2)
                except Exception:
                    pass

            cache[key] = dist
            segment["distance_km"] = str(dist) if dist is not None else None
        total_d = sum((Decimal(str(seg["distance_km"])) for seg in (invoice.get("segments") or []) if seg.get("distance_km") not in (None, "")), Decimal(0))
        invoice["distance_km"] = total_d if total_d > 0 else None


async def preview_bkav_cargo_invoices(request: Request, payload: BkavSyncInput, user: dict):
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


async def import_bkav_cargo_invoices(request: Request, payload: BkavSyncInput, user: dict):
    vehicle, seller_id, buyer, start, end, seller_tax = _bkav_sync_context(request, user, payload)
    if not payload.guids:
        raise ValueError("Chọn ít nhất một hóa đơn.")
    invoices, _ = await run_in_threadpool(fetch_bkav_invoices, start, end, seller_tax)
    if buyer:
        invoices = [item for item in invoices if item["buyer_tax_code"] == buyer["tax_code"]]
    await _populate_bkav_invoice_distances(invoices, payload.guids)
    counts = await run_in_threadpool(
        import_bkav_invoices, invoices, payload.guids,
        vehicle=vehicle, seller_company_id=seller_id,
        payment_status=payload.payment_status,
    )
    return {"ok": True, **counts}


async def cargo_trips_page(
    request: Request,
    vehicle_id: str | None,
    company_id: str | None,
    page: int,
    month: str | None,
    status: str | None,
    payment_status: str | None,
    q: str | None,
    user: dict,
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"

    all_vehicles = queries.select_vehicles_by_plate() or []
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

    month = (month or f"{date.today().year}-all").strip().lower()
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
        # An invoice shows its own total (VAT rounded per line, as on BKAV); only a manual trip is worked out here.
        t["current_revenue_gross_fmt"] = _fmt_currency(
            (Decimal(str(t["freight_revenue"])) * (Decimal("1") + vat_rate)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP,
            )
        ) if (t.get("freight_revenue") is not None and vat_rate is not None
              and not (t.get("invoice_guid") and t.get("source_revenue_gross") is not None)) else None
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
    debt_sum_row = queries.cargo_trips_page_select_cargo_trips(debt_sum_params, trip_debt_where)
    total_debt = Decimal(str(debt_sum_row["total_debt"])) if debt_sum_row and debt_sum_row.get("total_debt") else Decimal(0)
    invoice_debt_where, invoice_debt_params = _history_conditions(
        "invoice", invoice=True, vehicle_id=vid, company_id=cid,
        payment_status="debt", start=start, end=end,
        seller_company_id=active_company_id, document_query=document_query,
    )
    invoice_debt_row = queries.cargo_trips_page_select_bkav_invoices(invoice_debt_params, invoice_debt_where)
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

    share_base = public_base_url(request)
    from frontend.modules.zalo.trip_requests import invoice_zalo_status
    z_status = invoice_zalo_status([str(t["invoice_guid"]) for t in trips if t.get("invoice_guid")])

    for t in trips:
        if t.get("invoice_guid"):
            guid_k = str(t["invoice_guid"])
            t["share_url"] = f"{share_base}{share_path(t['invoice_guid'])}"
            st = z_status.get(guid_k) or {}
            t["zalo_sent"] = st.get("sent", False)
            t["zalo_group"] = st.get("group_name", "")

    remember_list_url(
        request, "cargo_list", "/admin/vehicles/cargo",
        ("vehicle_id", "company_id", "month", "status", "q", "page"),
    )
    return templates.TemplateResponse("pages/cargo/index.html", {"request": request, **ctx})


async def lookup_invoice_company(tax_code: str, _: dict):
    try:
        company = await lookup_tax_code(tax_code)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=422)
    except TaxLookupError as exc:
        return JSONResponse({"ok": False, "message": exc.message}, status_code=exc.status_code)
    return {"ok": True, **company}


async def refresh_invoice_company(company_id: int, _: dict):
    try:
        updated = await refresh_company_from_tax(company_id)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=422)
    except TaxLookupError as exc:
        return JSONResponse({"ok": False, "message": exc.message}, status_code=exc.status_code)
    return {"ok": True, "company": updated}


async def invoice_tax_suggestion(invoice_date: str, _: dict):
    try:
        invoice_day = date.fromisoformat(invoice_date)
    except ValueError:
        return JSONResponse({"ok": False, "message": "Ngày hóa đơn không hợp lệ."}, status_code=422)
    return _vat_rule_response(invoice_day)


async def create_invoice_company(payload: InvoiceCompanyInput, _: dict):
    try:
        company = save_company(payload.tax_code, payload.name, payload.address, payload.email, phone=payload.phone)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=422)
    return {"ok": True, "company": company}


async def resolve_maps_url(url: str, user: dict):
    if not _is_maps_url(url):
        return JSONResponse({"ok": False, "message": "Link Google Maps không hợp lệ."}, status_code=422)
    location = await _resolve_location(url)
    if location is None:
        return JSONResponse({"ok": False, "message": "Không lấy được tọa độ từ link Google Maps."}, status_code=404)
    if location["label"] == url:
        # Try Google Reverse Geocoding first
        google_label = await reverse_geocode_google(location["lat"], location["lon"])
        if google_label:
            location["label"] = google_label

    return JSONResponse({"ok": True, **location})


async def geocode_address(q: str, user: dict):
    location = await _geocode_location(q)
    if location:
        return JSONResponse({"ok": True, **location})
    return JSONResponse({"ok": False, "message": "Không tìm thấy địa điểm."}, status_code=404)


async def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float, user: dict):
    if _coordinates({"lat": lat1, "lon": lon1}) is None or _coordinates({"lat": lat2, "lon": lon2}) is None:
        return JSONResponse({"ok": False, "message": "Tọa độ không hợp lệ."}, status_code=422)
    distance, provider = await _gmaps_driving_distance(lat1, lon1, lat2, lon2)
    if distance is not None:
        return JSONResponse({"ok": True, "distance_km": distance, "provider": provider})
    return JSONResponse({"ok": False, "message": "Không tìm được đường đi."}, status_code=404)


async def calculate_distance_by_address(origin: str, destination: str, waypoints: str, user: dict):
    try:
        via = json.loads(waypoints or "[]")
    except (TypeError, json.JSONDecodeError):
        via = None
    if not isinstance(via, list):
        return JSONResponse({"ok": False, "message": "Điểm ghé không hợp lệ."}, status_code=422)
    try:
        async def google_leg(origin_address: str, destination_address: str):
            # Google can route the entered addresses directly; a name it cannot place is searched like Google Maps does.
            start, end = origin_address.strip(), destination_address.strip()
            if not start or not end:
                return None, None, {}
            if _is_maps_url(start):
                location = await _resolve_location(start)
                coordinates = _coordinates(location) if location else None
                if coordinates is None:
                    return None, None, {}
                start = {"lat": coordinates[0], "lon": coordinates[1]}
            if _is_maps_url(end):
                location = await _resolve_location(end)
                coordinates = _coordinates(location) if location else None
                if coordinates is None:
                    return None, None, {}
                end = {"lat": coordinates[0], "lon": coordinates[1]}
            if isinstance(start, str) and isinstance(end, str):
                distance_km, found = await route_distance_km(start, end)
            else:
                distance_km, found = (await _gmaps_routes_distance(start, end))[0], {}
            if distance_km is not None and math.isfinite(float(distance_km)) and distance_km > 0:
                return float(distance_km), "google_routes", found
            return None, None, {}

        async def google_distance():
            points = [origin, *via, destination]
            if any(not point.strip() for point in points):
                return {"ok": False, "message": "Nhập điểm lấy, điểm ghé và điểm giao hàng."}
            legs = await asyncio.gather(*(google_leg(start, end) for start, end in zip(points, points[1:])))
            if all(distance is not None for distance, _, _ in legs):
                providers = {provider for _, provider, _ in legs}
                return {"ok": True, "distance_km": round(sum(distance for distance, _, _ in legs), 2),
                        "provider": providers.pop() if len(providers) == 1 else "google_maps",
                        "origin_match": legs[0][2].get("origin", ""), "destination_match": legs[-1][2].get("destination", "")}
            return {"ok": False, "message": "Google Maps không tính được quãng đường."}

        result = await asyncio.wait_for(google_distance(), timeout=18)
        return JSONResponse(result)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "message": "Google Maps phản hồi quá lâu."})


class InvoiceLocationsInput(BaseModel):
    origin: str = ""
    destination: str = ""
    via_points: list[str] = []


async def normalize_invoice_locations(payload: InvoiceLocationsInput, _: dict):
    directory = await run_in_threadpool(load_commune_directory)
    extra_stops, destination = split_invoice_destination_stops(payload.destination, directory)
    via_points = [normalize_invoice_location(point, directory) for point in payload.via_points]
    for stop in extra_stops:
        if not via_points or via_points[-1] != stop:
            via_points.append(stop)
    origin = normalize_invoice_location(payload.origin, directory)

    origin, destination, *via_points = await asyncio.gather(
        google_normalized_place(origin, directory), google_normalized_place(destination, directory),
        *(google_normalized_place(point, directory) for point in via_points))
    return JSONResponse({"ok": True, "origin": origin, "destination": destination, "via_points": via_points})


async def google_maps_status_api(user: dict):
    report = await check_google_maps_services_status()
    return JSONResponse(report)


async def get_fuel_price_api(target_date: str, user: dict):
    d = target_date or date.today().isoformat()
    if not _valid_trip_datetime(d):
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


async def get_next_invoice_number_api(request: Request, user: dict):
    number, serial = next_invoice_number_and_serial()
    return JSONResponse({"ok": True, "invoice_number": number, "invoice_serial": serial})


def _find_vehicle_by_nickname(nickname: str, all_vehicles: list[dict]) -> dict | None:
    if not nickname:
        return None
    # A person's nickname ("Tư", "chú Ba", "bố") names the vehicle that person owns.
    vehicle, person = resolve_vehicle_from_identifier(nickname, all_vehicles)
    if vehicle:
        return vehicle
    if person:
        return None  # a known person without a vehicle on the list: fuzzy matching below could pick someone else's
    nick_folded = _remove_accents(nickname).casefold().strip()

    # 1. Match plate numbers directly
    clean_nick = re.sub(r"[^0-9A-Za-z]", "", nick_folded)
    for v in all_vehicles:
        clean_plate = re.sub(r"[^0-9A-Za-z]", "", (v.get("plate_number") or "").casefold())
        if clean_nick and (clean_nick in clean_plate or clean_plate.endswith(clean_nick)):
            return v

    # 2. Match family / driver nickname terms
    # "bố" / "bo" -> Xe Nguyễn Kim Quyết (19H-147.98)
    if nick_folded in {"bo", "xe bo", "quyet", "kim quyet", "chu quyet", "bac quyet"}:
        for v in all_vehicles:
            owner = _remove_accents(v.get("owner_name") or "").casefold()
            if "quyet" in owner:
                return v

    # "chú lợi" / "loi" -> Xe Nguyễn Văn Lợi (19H-209.07)
    if nick_folded in {"loi", "van loi", "chu loi", "anh loi", "xe chu loi"}:
        for v in all_vehicles:
            owner = _remove_accents(v.get("owner_name") or "").casefold()
            if "loi" in owner:
                return v

    # 3. Match against owner_name or note
    for v in all_vehicles:
        owner = _remove_accents(v.get("owner_name") or "").casefold()
        note = _remove_accents(v.get("note") or "").casefold()
        if nick_folded in owner or nick_folded in note:
            return v

    # 4. Check transport_drivers table
    drivers = queries.select_drivers_with_vehicle()
    for d in drivers or []:
        fname = _remove_accents(d.get("full_name") or "").casefold()
        dcode = _remove_accents(d.get("driver_code") or "").casefold()
        if not fname:
            continue
        if nick_folded in fname or nick_folded in dcode or fname.split()[-1] in nick_folded:
            vid = d.get("vehicle_id")
            matched = next((v for v in all_vehicles if v["id"] == vid), None)
            if matched:
                return matched

    return None


async def add_cargo_trip_page(request: Request, vehicle_id: Optional[int], user: dict):
    if request.query_params.get("zalo_draft") != "1":
        selected = vehicle_id or request.query_params.get("vehicle_id")
        suffix = f"?vehicle_id={selected}" if selected and str(selected).isdigit() else ""
        return RedirectResponse(f"/admin/vehicles/cargo/invoices/add{suffix}", status_code=303)
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    draft = request.session.get("zalo_trip_draft") if request.query_params.get("zalo_draft") == "1" else None
    if draft and draft.get("user_id") != user.get("id"):
        draft = None

    all_vehicles = queries.select_vehicles_by_plate() or []
    if vehicle_id is None:
        v_param = request.query_params.get("vehicle_id")
        if v_param and str(v_param).isdigit():
            vehicle_id = int(v_param)
    if draft and draft.get("vehicle_nickname"):
        matched_v = _find_vehicle_by_nickname(draft["vehicle_nickname"], all_vehicles)
        if matched_v:
            vehicle_id = matched_v["id"]
            draft["vehicle_id"] = vehicle_id
        else:
            draft["missing"].append("Chọn xe: không xác định được xe " + draft["vehicle_nickname"])

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
    draft_seller_id = draft.get("seller_company_id") if draft else None
    if draft_seller_id:
        ctx["seller_company_id"] = draft_seller_id
        request.session["active_company_id"] = draft_seller_id
    else:
        ctx["seller_company_id"] = get_default_seller_company_id(vehicle_id=vehicle_id, trip=None, current_user=user)
    ctx["vat_percent"] = "8"
    ctx["service_presets"] = [item["name"] for item in list_catalog_items("service")]
    ctx["service_name"] = (draft.get("service") or "") if draft else DEFAULT_SERVICE_NAME
    ctx["trip_note_value"] = (draft.get("note") or ctx["service_name"]) if draft else DEFAULT_SERVICE_NAME
    ctx["cargo_types"] = list_cargo_types()
    ctx["suggested_cargo_type"] = (draft.get("cargo") or "") if draft else cargo_description_from_service(DEFAULT_SERVICE_NAME)
    ctx["administrative_provinces"] = list_administrative_provinces()
    ctx["zalo_draft"] = draft
    default_back = f"/admin/vehicles/cargo?vehicle_id={vehicle['id']}" if vehicle and vehicle.get("id") else "/admin/vehicles/cargo"
    ctx["list_url"] = recall_list_url(request, "cargo_list", "/admin/vehicles/cargo", default_back)
    return templates.TemplateResponse("pages/cargo/add.html", {"request": request, **ctx})


async def create_cargo_trip(
    request: Request,
    vehicle_id: Optional[int],
    invoice_date: str,
    company_id: int,
    service_name: str,
    seller_company_id: Optional[int],
    origin: Optional[str],
    destination: Optional[str],
    fuel_liters: Optional[str],
    fuel_price_per_liter: Optional[str],
    freight_revenue: Optional[str],
    distance_km: Optional[str],
    vat_percent: Optional[str],
    payment_method: Optional[str],
    payment_status: Optional[str],
    debt_amount: Optional[str],
    document_number: Optional[str],
    cargo_description: Optional[str],
    cargo_weight_tons: Optional[str],
    trip_note: Optional[str],
    route_segments: Optional[str],
    zalo_draft_token: Optional[str],
    zalo_payment_confirmed: bool,
    user: dict,
):
    if vehicle_id is None:
        form_data = await request.form()
        v_form = form_data.get("vehicle_id")
        if v_form and str(v_form).isdigit():
            vehicle_id = int(v_form)

    vehicle = queries.select_vehicle_by_id(vehicle_id) if vehicle_id else None
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
        if not draft.get("company_id") and not company_id:
            missing_fields.append("đơn vị khách hàng (bên mua)")
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
    trip_fields["company_id"] = company_id or (draft.get("company_id") if draft else None)
    trip_fields["seller_company_id"] = (draft.get("seller_company_id") if draft else None) or seller_company_id or request.session.get("active_company_id")
    trip_fields["service_name"] = service_name.strip()
    trip_fields["route_segments"] = segments

    trip_id = queries.add_cargo_trip(
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


async def edit_cargo_trip_page(trip_id: int, request: Request, vehicle_id: Optional[int], user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    active_seller = get_company(ctx.get("active_company_id")) if ctx.get("active_company_id") else None

    trip = get_cargo_trip(trip_id)
    if not trip:
        return RedirectResponse(url="/admin/vehicles/cargo", status_code=303)
    vehicle_id = trip.get("vehicle_id")

    all_vehicles = queries.select_vehicles_by_plate() or []
    vehicle = queries.select_vehicle_by_id(vehicle_id)
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
    default_back = f"/admin/vehicles/cargo?vehicle_id={vehicle_id}" if vehicle_id else "/admin/vehicles/cargo"
    ctx["list_url"] = recall_list_url(request, "cargo_list", "/admin/vehicles/cargo", default_back)
    return templates.TemplateResponse("pages/cargo/edit.html", {"request": request, **ctx})


async def edit_cargo_trip(
    trip_id: int,
    request: Request,
    vehicle_id: Optional[int],
    invoice_date: str,
    company_id: int,
    service_name: str,
    seller_company_id: Optional[int],
    origin: Optional[str],
    destination: Optional[str],
    fuel_liters: Optional[str],
    fuel_price_per_liter: Optional[str],
    freight_revenue: Optional[str],
    distance_km: Optional[str],
    vat_percent: Optional[str],
    payment_method: Optional[str],
    payment_status: Optional[str],
    debt_amount: Optional[str],
    document_number: Optional[str],
    cargo_description: Optional[str],
    cargo_weight_tons: Optional[str],
    trip_note: Optional[str],
    route_segments: Optional[str],
    user: dict,
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

    vehicle = queries.select_vehicle_by_id(vehicle_id)
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


async def update_cargo_service(trip_id: int, vehicle_id: Optional[int], service_name: str, _: dict):
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
    updated = queries.update_cargo_service_update_cargo_trips(next_cargo, next_note, trip_id, service_name)
    if not updated:
        return JSONResponse({"ok": False, "message": "Không tìm thấy chuyến hàng."}, status_code=404)
    return {"ok": True}


async def push_cargo_invoice_to_bkav(trip_id: int, request: Request, user: dict):
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
    if not seller:
        return respond_error("Không tìm thấy đơn vị bán/phát hành hóa đơn.", status_code=400)
    if not buyer or not all(str(buyer.get(k) or "").strip() for k in ("name", "address", "tax_code")):
        return respond_error("Chọn đơn vị mua có đủ tên, địa chỉ và mã số thuế.", status_code=400)

    result = await run_in_threadpool(push_invoice_to_bkav, trip, buyer, seller)

    already_sent = bool(result.get("already_sent"))
    notice = ("Hóa đơn chuyến này đã được đồng bộ sang BKAV trước đó."
              if already_sent else "Hóa đơn nháp đã có trên BKAV."
              if result.get("status") == "sent" else "Đã kiểm tra trạng thái hóa đơn trên BKAV.")
    png_file = None
    if result.get("status") == "sent" and (result.get("invoice_guid") or result.get("InvoiceGUID")):
        png_file, _ = await asyncio.to_thread(
            get_or_fetch_bkav_invoice_files,
            str(result.get("invoice_guid") or result.get("InvoiceGUID")),
            force_refresh=not already_sent,
        )
    if zalo_request and result.get("status") == "sent" and not already_sent:
        _, message = await send_invoice_notice(
            trip_id, trip=trip, buyer=buyer, invoice=result,
            image_path=str(png_file) if png_file and png_file.exists() else ""
        )
        notice += " " + message

    if is_ajax:
        image_url = None
        if png_file and png_file.exists():
            image_url = f"/upload/bkav_invoices/{png_file.name}?v={png_file.stat().st_mtime_ns}"
        return JSONResponse({
            "ok": True,
            "message": notice,
            "already_sent": already_sent,
            "invoice_guid": result.get("invoice_guid") or result.get("InvoiceGUID"),
            "image_url": image_url,
        })
    return RedirectResponse(
        back_url + "?" + urllib.parse.urlencode({"bkav_notice": notice}), status_code=303,
    )

async def confirm_zalo_invoice_not_created(trip_id: int, request: Request, user: dict):
    trip = get_cargo_trip(trip_id)
    if not trip or trip.get("seller_company_id") != get_menu_context(request, user).get("active_company_id"):
        return HTMLResponse("Không tìm thấy chuyến hàng trong đơn vị đang chọn.", status_code=404)
    confirm_invoice_not_created(trip_id)
    return RedirectResponse(
        f"/admin/vehicles/cargo/{trip_id}/edit?" + urllib.parse.urlencode({
            "zalo_notice": "Đã xác nhận chưa có hóa đơn trên BKAV. Có thể tạo hóa đơn nháp."
        }), status_code=303,
    )


async def retry_zalo_invoice_notice(trip_id: int, request: Request, user: dict):
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


async def export_cargo_invoice(trip_id: int, vehicle_id: Optional[int], user: dict):
    trip = get_cargo_trip(trip_id)
    if not trip or (vehicle_id is not None and trip.get("vehicle_id") != vehicle_id):
        return HTMLResponse("Không tìm thấy chuyến hàng.", status_code=404)
    if vehicle_id is None:
        vehicle_id = trip.get("vehicle_id")

    vehicle = queries.select_vehicle_by_id(vehicle_id)
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


async def export_cargo_contract(
    trip_id: int,
    request: Request,
    vehicle_id: Optional[int],
    download: Optional[str],
    format: Optional[str],
    view: Optional[str],
    user: dict,
):
    trip = get_cargo_trip(trip_id)
    if not trip or (vehicle_id is not None and trip.get("vehicle_id") != vehicle_id):
        return HTMLResponse("Không tìm thấy chuyến hàng.", status_code=404)

    back_url = f"/admin/vehicles/cargo/{trip_id}/edit"
    company = get_company(trip["company_id"]) if trip.get("company_id") else None
    if not company or not company.get("name"):
        return _trip_form_error("Chọn đơn vị khách hàng hợp lệ trước khi xuất hợp đồng.", back_url)

    seller_id = trip.get("seller_company_id") or get_current_company_id() or 33
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
            headers={"Content-Disposition": content_disposition(filename)},
        )

    # Default: PDF
    pdf_bytes = render_contract_pdf(ctx)
    filename = f"Hop_dong_{safe_no}.pdf"
    disposition = "attachment" if download == "pdf" else "inline"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(filename, disposition)},
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
            headers={"Content-Disposition": content_disposition(filename)},
        )
    # Default to .doc
    doc_bytes = convert_docx_bytes_to_doc(docx_bytes)
    filename = f"{prefix}_{safe_no}.doc"
    media_type = "application/msword"
    return Response(
        content=doc_bytes,
        media_type=media_type,
        headers={"Content-Disposition": content_disposition(filename)},
    )


def _export_trip_document(trip_id: int, vehicle_id: Optional[int], requested_format: Optional[str], render, label: str, no_company_error: str):
    """Word contract / handover record of one trip for its customer company."""
    trip = get_cargo_trip(trip_id)
    if not trip or (vehicle_id is not None and trip.get("vehicle_id") != vehicle_id):
        return HTMLResponse("Không tìm thấy chuyến hàng.", status_code=404)

    company = get_company(trip["company_id"]) if trip.get("company_id") else None
    if not company or not company.get("name"):
        return _trip_form_error(no_company_error, f"/admin/vehicles/cargo/{trip_id}/edit")

    seller = get_company(trip.get("seller_company_id")) if trip.get("seller_company_id") else None
    docx_bytes, contract_no = render(trip, company, seller)
    return _handle_word_download(docx_bytes, contract_no, label, requested_format or "doc")


async def export_cargo_hdnt(
    trip_id: int,
    vehicle_id: Optional[int],
    download: Optional[str],
    format: Optional[str],
    user: dict,
):
    return _export_trip_document(trip_id, vehicle_id, download or format, render_hdnt_docx, "HDNT",
                                 "Chọn đơn vị khách hàng hợp lệ trước khi xuất HĐNT.")


async def export_cargo_bbbg(
    trip_id: int,
    vehicle_id: Optional[int],
    download: Optional[str],
    format: Optional[str],
    user: dict,
):
    return _export_trip_document(trip_id, vehicle_id, download or format, render_bbbg_docx, "BBBG",
                                 "Chọn đơn vị khách hàng hợp lệ trước khi xuất BBBG.")


async def export_cargo_hdnt_bbbg(
    trip_id: int,
    vehicle_id: Optional[int],
    download: Optional[str],
    format: Optional[str],
    user: dict,
):
    return _export_trip_document(trip_id, vehicle_id, download or format, render_hdnt_bbbg_combined_docx, "HDNT_BBBG",
                                 "Chọn đơn vị khách hàng hợp lệ trước khi xuất hợp đồng & biên bản.")


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


async def get_trip_qr_endpoint(trip_id: int, vehicle_id: Optional[int], user: dict):
    if vehicle_id is not None:
        trip = queries.select_trip_of_vehicle(trip_id, vehicle_id)
    else:
        trip = get_cargo_trip(trip_id)
    if not trip:
        return JSONResponse({"ok": False, "error": "Không tìm thấy chuyến hàng."}, status_code=404)

    qr_url = trip.get("qr_url")
    gen_url, details = generate_trip_vietqr_url(trip)
    if qr_url != gen_url:
        qr_url = gen_url
        queries.update_trip_qr_url(qr_url, trip_id)
    details["qr_url"] = qr_url
    return JSONResponse({"ok": True, "qr_url": qr_url, "details": details})


async def generate_and_save_trip_qr_endpoint(
    trip_id: int,
    request: Request,
    vehicle_id: Optional[int],
    user: dict,
):
    if vehicle_id is not None:
        trip = queries.select_trip_of_vehicle(trip_id, vehicle_id)
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
        queries.generate_and_save_trip_qr_endpoint_update_cargo_trips(note, trip_id)
    if body.get("debt_amount") is not None and str(body["debt_amount"]).strip() != "":
        try:
            raw_debt = str(body["debt_amount"]).replace(".", "").replace(",", "").strip()
            val = float(raw_debt)
            if val < 0:
                return JSONResponse({"ok": False, "error": "Số tiền công nợ không được âm."}, status_code=400)
            trip["debt_amount"] = val
            queries.generate_and_save_trip_qr_endpoint_update_cargo_trips_3(val, trip_id)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "error": f"Số tiền công nợ không hợp lệ: {exc}"}, status_code=400)

    qr_url, details = generate_trip_vietqr_url(trip)
    queries.update_trip_qr_url(qr_url, trip_id)
    return JSONResponse({"ok": True, "qr_url": qr_url, "details": details})


def _invoice_form_context(request: Request, user: dict, *, invoice: dict | None = None,
                          lines: list[dict] | None = None, values: dict | None = None,
                          error: str = "") -> dict:
    ctx = get_menu_context(request, user)
    active_seller = get_company(ctx.get("active_company_id")) if ctx.get("active_company_id") else None
    ctx["current_menu"] = "vehicles"
    ctx["invoice"] = invoice
    ctx["zalo_sent"] = False
    ctx["zalo_group"] = ""
    ctx["vehicles"] = queries.select_vehicles_by_plate() or []
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
        ctx["form_values"] = dict(values)
        if not ctx["form_values"].get("invoice_number"):
            next_num, next_serial = next_invoice_number_and_serial()
            ctx["form_values"]["invoice_number"] = next_num
            if not ctx["form_values"].get("invoice_serial"):
                ctx["form_values"]["invoice_serial"] = next_serial
        if not ctx["form_values"].get("tax_code") and ctx["form_values"].get("company_id"):
            c_id = str(ctx["form_values"].get("company_id"))
            for c in ctx["companies"]:
                if str(c.get("id")) == c_id:
                    ctx["form_values"]["tax_code"] = c.get("tax_code") or ""
                    break
    else:
        if invoice:
            default_number = str(invoice["invoice_number"]).zfill(8)
            default_serial = invoice.get("invoice_serial") or ""
        else:
            default_number, default_serial = next_invoice_number_and_serial()

        buyer_id = invoice["buyer_company_id"] if invoice else request.query_params.get("company_id", "")
        buyer_tax_code = (invoice.get("buyer_tax_code") or "") if invoice else ""
        if not buyer_tax_code and buyer_id and str(buyer_id).isdigit():
            for c in ctx["companies"]:
                if str(c.get("id")) == str(buyer_id):
                    buyer_tax_code = c.get("tax_code") or ""
                    break

        ctx["form_values"] = {
            "invoice_date": (invoice["invoice_date"].isoformat() if invoice and hasattr(invoice["invoice_date"], "isoformat")
                             else str(invoice["invoice_date"])[:10] if invoice else date.today().isoformat()),
            "invoice_number": default_number,
            "invoice_serial": default_serial,
            "company_id": buyer_id,
            "tax_code": buyer_tax_code,
            "service_name": invoice["service_name"] if invoice else "",
            "vat_percent": format_vat_percent(parse_vat_rate(invoice["vat_rate"])) if invoice and invoice.get("vat_rate") is not None else "8",
            "payment_method": invoice["payment_method"] if invoice else "CK",
            "payment_status": invoice["payment_status"] if invoice else "paid",
            "debt_amount": str(invoice["debt_amount"]) if invoice else "",
        }
    form_values = ctx["form_values"]
    if values is not None:
        saved_accounts = {key: form_values.get(key) for key in ACCOUNT_KEYS}
    else:
        saved_accounts = load_saved_accounts(str(invoice["invoice_guid"])) if invoice else {}
    form_values["accounts"] = effective_accounts(
        form_values.get("payment_status") or "paid", form_values.get("payment_method") or "CK", saved_accounts)
    current_service = str(ctx["form_values"].get("service_name") or "")
    if is_crane_by_shift_service(current_service):
        ctx["form_values"]["service_name"] = CRANE_BY_SHIFT_SERVICE
    elif current_service.casefold() in removed_service_names:
        ctx["form_values"]["service_name"] = ""
    if lines is None:
        lines = []
    is_crane_by_shift_service(ctx["form_values"].get("service_name"))
    vehicle_by_plate = {re.sub(r"[^A-Z0-9]", "", str(vehicle.get("plate_number") or "").upper()): vehicle
                        for vehicle in ctx["vehicles"] if vehicle.get("plate_number")}
    line_items = []
    for source_line in lines:
        if not isinstance(source_line, dict):
            continue
        line = dict(source_line)
        plate = str(line.get("plate_number") or "").strip()
        if not line.get("vehicle_id") and plate:
            linked_vehicle = vehicle_by_plate.get(re.sub(r"[^A-Z0-9]", "", plate.upper()))
            if linked_vehicle:
                line["vehicle_id"] = linked_vehicle["id"]
        row_dict = {}
        for key in ("vehicle_id", "plate_number", "cargo_description", "cargo_weight_tons",
                    "description", "origin", "destination", "amount", "distance_km",
                    "fuel_liters", "fuel_price_per_liter", "fuel_cost_total"):
            val = line.get(key)
            if val is not None:
                if key in ("amount", "fuel_price_per_liter"):
                    try:
                        row_dict[key] = str(int(round(Decimal(str(val)))))
                    except Exception:
                        row_dict[key] = str(val)
                else:
                    row_dict[key] = str(val)
            else:
                row_dict[key] = ""
        row_dict["via_points"] = line.get("via_points") if isinstance(line.get("via_points"), list) else []
        line_items.append(row_dict)
    ctx["line_items"] = line_items
    ctx["vehicle_options"] = [{"id": vehicle["id"], "plate": vehicle["plate_number"],
                               "owner": vehicle.get("owner_name") or "",
                               "rate": str(vehicle.get("fuel_consumption") or "")}
                              for vehicle in ctx["vehicles"]]
    ctx["back_url"] = recall_list_url(request, "cargo_list", "/admin/vehicles/cargo", "/admin/vehicles/cargo")
    return ctx


async def _invoice_payload_from_form(form: dict, request: Request, user: dict,
                                     *, seller_company_id: int | None = None) -> dict:
    seller_company_id = seller_company_id or (int(form.get("seller_company_id")) if str(form.get("seller_company_id", "")).isdigit() else None) or get_current_company_id() or 33
    raw_buyer_id = str(form.get("company_id") or "").strip()
    raw_tax_code = str(form.get("tax_code") or "").strip()

    buyer = get_company(int(raw_buyer_id)) if raw_buyer_id.isdigit() else None

    # Tìm theo mã số thuế nếu chưa tìm được theo ID
    if not buyer and raw_tax_code:
        buyer = queries.select_invoice_company_by_tax_code(raw_tax_code)

    # Tìm theo tên nếu người dùng gõ tên trực tiếp
    if not buyer and raw_buyer_id and not raw_buyer_id.isdigit():
        buyer = find_company_by_name(raw_buyer_id)

    # Nếu chưa có trong danh mục doanh nghiệp, tự động tạo để đồng bộ xuyên suốt các sổ
    if not buyer:
        if not raw_buyer_id and not raw_tax_code:
            raise ValueError("Chọn hoặc nhập đơn vị khách hàng.")
        company_name = raw_buyer_id if not raw_buyer_id.isdigit() else f"Khách hàng {raw_tax_code}"
        buyer = queries.insert_invoice_company(company_name, raw_tax_code or None)
    elif raw_tax_code and buyer.get("tax_code") != raw_tax_code:
        # Cập nhật mã số thuế cho doanh nghiệp nếu người dùng nhập/sửa MST
        queries.update_invoice_company_tax_code(raw_tax_code, buyer["id"])
        buyer["tax_code"] = raw_tax_code

    vehicles = queries.select_vehicles_by_plate(of_company=False) or []
    default_price = None
    try:
        raw_lines = json.loads(str(form.get("lines_json") or "[]"))
        if isinstance(raw_lines, list) and any(isinstance(line, dict) and not line.get("fuel_price_per_liter")
                                               and (line.get("fuel_liters") or line.get("distance_km"))
                                               for line in raw_lines):
            default_price = _fuel_price_for_date(form.get("invoice_date"))
    except (TypeError, json.JSONDecodeError):
        pass
    return parse_invoice_form(form, vehicles=vehicles, buyer=buyer,
                              seller_company_id=seller_company_id, default_fuel_price=default_price)


async def add_invoice_page(request: Request, user: dict):
    ctx = _invoice_form_context(request, user)
    return templates.TemplateResponse("pages/cargo/invoice_form.html", {"request": request, **ctx})


async def create_invoice(request: Request, user: dict):
    form = dict(await request.form())
    headers = getattr(request, "headers", None) or {}
    accept = headers.get("Accept", "") if hasattr(headers, "get") else ""
    if form.get("action") == "export" or "application/vnd.openxmlformats" in accept:
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
            headers={"Content-Disposition": content_disposition(filename),
                     "Cache-Control": "no-store"},
        )
    return await save_invoice_to_db(request, user=user)


async def save_invoice_to_db(request: Request, invoice_guid: Optional[UUID] = None, user: dict = Depends(require_menu("vehicles"))):
    """Save invoice form data to the database (bkav_invoices + bkav_invoice_lines).
    If invoice_guid exists, update the existing invoice header and replace line items.
    """
    form = dict(await request.form())
    try:
        payload = await _invoice_payload_from_form(form, request, user)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)

    existing_guid = str(invoice_guid or form.get("invoice_guid") or "").strip()
    try:
        saved = save_sales_invoice(payload, guid=existing_guid or str(uuid4()), edit=bool(existing_guid),
                                   created_by=user.get("id") if isinstance(user, dict) else None)
    except InvoiceNumberTaken as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    guid = saved.guid
    headers = getattr(request, "headers", None) or {}
    is_ajax = bool(
        hasattr(headers, "get")
        and (
            headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in headers.get("Accept", "")
        )
    )
    if is_ajax:
        return JSONResponse({"ok": True, "invoice_guid": guid,
                             "redirect": f"/admin/vehicles/cargo/invoices/{guid}/edit?saved=1"})
    return RedirectResponse(f"/admin/vehicles/cargo/invoices/{guid}/edit?saved=1", status_code=303)


async def push_invoice_form_to_bkav(request: Request, user: dict):
    form = dict(await request.form())
    guid_str = str(form.get("invoice_guid") or "").strip()
    is_new = not bool(guid_str)
    headers = getattr(request, "headers", None) or {}
    is_ajax = bool(
        hasattr(headers, "get")
        and (
            headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in headers.get("Accept", "")
        )
    )
    target_guid: UUID | None = UUID(guid_str) if guid_str else None

    if target_guid:
        save_res = await save_invoice_to_db(request, invoice_guid=target_guid, user=user)
    else:
        save_res = await save_invoice_to_db(request, user=user)

    if save_res.status_code >= 400:
        return save_res

    # Resolve target_guid if newly created
    if not target_guid:
        if isinstance(save_res, JSONResponse):
            target_guid = UUID(str(json.loads(save_res.body.decode("utf-8"))["invoice_guid"]))
        else:
            loc = str(save_res.headers["location"])
            target_guid = UUID(re.search(r"/invoices/([0-9a-fA-F-]{36})", loc).group(1))

    push_res = await push_saved_invoice_to_bkav_endpoint(target_guid, request, user=user)
    if is_new and is_ajax and isinstance(push_res, JSONResponse):
        body_data = json.loads(push_res.body.decode("utf-8"))
        body_data["redirect"] = f"/admin/vehicles/cargo/invoices/{target_guid}/edit"
        return JSONResponse(body_data, status_code=push_res.status_code)
    return push_res


def _saved_invoice_for_action(invoice_guid: UUID, request: Request, user: dict) -> tuple[dict, list[dict]]:
    """Read a saved invoice and all its vehicle lines within the active company."""
    invoice = queries.saved_invoice_for_action_select_bkav_invoices(invoice_guid)
    if not invoice:
        raise HTTPException(status_code=404, detail="Không tìm thấy hóa đơn.")
    active_company_id = get_menu_context(request, user).get("active_company_id")
    if active_company_id is not None and invoice["seller_company_id"] != active_company_id:
        raise HTTPException(status_code=403, detail="Hóa đơn không thuộc đơn vị đang chọn.")
    lines = queries.saved_invoice_for_action_select_bkav_invoice_lines(invoice_guid) or []
    return dict(invoice), [dict(line) for line in lines]


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
    if not seller:
        raise ValueError("Không tìm thấy thông tin đơn vị bên bán/phát hành hóa đơn.")
    if not buyer:
        raise ValueError("Vui lòng chọn đơn vị khách hàng/bên mua.")
    if not lines:
        raise ValueError("Hóa đơn chưa có dòng hàng/xe nào để đồng bộ sang BKAV.")

    result = await run_in_threadpool(push_saved_invoice_to_bkav, invoice, lines, buyer, seller)
    already_sent = bool(result.get("already_sent"))
    notice = ("Hóa đơn này đã được đồng bộ sang BKAV trước đó."
              if already_sent else "Hóa đơn nháp đã được đồng bộ sang BKAV."
              if result.get("status") == "sent" else "Đã kiểm tra trạng thái hóa đơn trên BKAV.")

    image_url = None
    if result.get("status") == "sent":
        png_path, _ = await asyncio.to_thread(
            get_or_fetch_bkav_invoice_files, str(invoice_guid),
            force_refresh=not already_sent
        )
        if png_path and png_path.exists():
            image_url = f"/upload/bkav_invoices/{png_path.name}?v={png_path.stat().st_mtime_ns}"

    if is_ajax:
        return JSONResponse({
            "ok": True,
            "message": notice,
            "already_sent": already_sent,
            "image_url": image_url,
            "invoice_guid": str(invoice_guid),
            "download_url": f"/admin/vehicles/cargo/invoices/{invoice_guid}/bkav-image?download=1",
        })
    return RedirectResponse(back_url + "?" + urllib.parse.urlencode({"bkav_notice": notice}), status_code=303)


async def get_saved_invoice_qr(invoice_guid: UUID, request: Request, user: dict):
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


async def get_saved_invoice_bkav_image_info(
    invoice_guid: UUID, request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    invoice, _ = _saved_invoice_for_action(invoice_guid, request, user)
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


def _group_named_like(groups: list[dict], company_name: str) -> str | None:
    """The Zalo group named after a company: the same name, or one name inside the other (10+ letters)."""
    from frontend.formatting.text import fold_text
    company_folded = fold_text(company_name or "").strip()
    if not company_folded:
        return None
    for g in groups:
        g_folded = fold_text(g.get("name", "")).strip()
        if g_folded and (g_folded == company_folded
                         or (len(g_folded) >= 10 and g_folded in company_folded)
                         or (len(company_folded) >= 10 and company_folded in g_folded)):
            return g.get("group_id")
    return None


async def list_invoice_zalo_groups_endpoint(invoice_guid: UUID, request: Request, user: dict):
    invoice, _ = _saved_invoice_for_action(invoice_guid, request, user)
    buyer = get_company(invoice["buyer_company_id"]) if invoice.get("buyer_company_id") else None
    buyer_name = (buyer.get("name") if buyer else "") or ""

    from frontend.modules.zalo.trip_requests import list_zalo_groups
    groups = await list_zalo_groups()

    # The customer's own group first; otherwise the seller's company group, where its orders are asked for.
    seller = get_company(invoice["seller_company_id"]) if invoice.get("seller_company_id") else None
    suggested_group_id = (_group_named_like(groups, buyer_name)
                          or _group_named_like(groups, (seller or {}).get("name", "")))

    return JSONResponse({"ok": True, "groups": groups, "suggested_group_id": suggested_group_id})


async def send_saved_invoice_to_zalo_endpoint(invoice_guid: UUID, request: Request, user: dict):
    _saved_invoice_for_action(invoice_guid, request, user)
    try:
        body = await request.json()
    except Exception:
        body = {}
    group_id = str(body.get("group_id") or "").strip()
    group_name = str(body.get("group_name") or "").strip()
    if not group_id:
        return JSONResponse({"ok": False, "error": "Chọn nhóm Zalo nhận."}, status_code=400)

    from frontend.modules.zalo.trip_requests import send_invoice_pdf_to_zalo
    ok, msg = await send_invoice_pdf_to_zalo(
        invoice_guid=str(invoice_guid),
        group_id=group_id,
        group_name=group_name,
        sent_by=user.get("username", "") or "",
    )
    if not ok:
        return JSONResponse({"ok": False, "error": msg}, status_code=400)
    return JSONResponse({"ok": True, "message": msg})


async def recall_saved_invoice_zalo_endpoint(invoice_guid: UUID, request: Request, user: dict):
    _saved_invoice_for_action(invoice_guid, request, user)
    from frontend.modules.zalo.trip_requests import recall_invoice_zalo
    ok, msg = await recall_invoice_zalo(str(invoice_guid))
    if not ok:
        return JSONResponse({"ok": False, "error": msg}, status_code=400)
    return JSONResponse({"ok": True, "message": msg})


async def get_saved_invoice_bkav_image(invoice_guid: UUID, request: Request, download: int, user: dict):
    invoice, _ = _saved_invoice_for_action(invoice_guid, request, user)
    png_path, _ = await run_in_threadpool(get_or_fetch_bkav_invoice_files, str(invoice_guid))

    if not png_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh hóa đơn.")

    filename = f"Hoa_don_{int(invoice.get('invoice_number') or 0):08d}_{invoice.get('invoice_serial') or ''}.png"
    headers = {"Content-Disposition": content_disposition(filename)} if download else {}
    return FileResponse(png_path, media_type="image/png", headers=headers)


async def get_saved_invoice_bkav_pdf(invoice_guid: UUID, request: Request, user: dict):
    invoice, _ = _saved_invoice_for_action(invoice_guid, request, user)
    _, pdf_path = await run_in_threadpool(get_or_fetch_bkav_invoice_files, str(invoice_guid))

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy file PDF hóa đơn.")

    filename = f"Hoa_don_{int(invoice.get('invoice_number') or 0):08d}_{invoice.get('invoice_serial') or ''}.pdf"
    return FileResponse(pdf_path, media_type="application/pdf", headers={"Content-Disposition": content_disposition(filename)})


async def get_trip_bkav_image_info(trip_id: int, request: Request, user: dict):
    link = queries.get_trip_bkav_image_info_select_bkav_invoice_imports(trip_id)
    if not link:
        push = queries.get_trip_bkav_image_info_select_bkav_invoice_pushes(trip_id)
        if push and push.get("invoice_guid"):
            link = push
    saved_link = queries.get_trip_bkav_image_info_select_bkav_saved_invoice_links(trip_id)
    target_guid = UUID(str(saved_link["invoice_guid"])) if (saved_link and saved_link.get("invoice_guid")) else UUID(str(link["invoice_guid"]))
    inv_check = queries.get_trip_bkav_image_info_select_bkav_invoices(target_guid)
    if inv_check:
        return await get_saved_invoice_bkav_image_info(target_guid, request, user)

    png_path, pdf_path = await run_in_threadpool(
        get_or_fetch_bkav_invoice_files, str(link["invoice_guid"]), force_refresh=True)
    return {
        "ok": True,
        "image_url": f"/upload/bkav_invoices/{png_path.name}?v={png_path.stat().st_mtime_ns}",
        "pdf_url": f"/upload/bkav_invoices/{pdf_path.name}",
        "download_url": f"/upload/bkav_invoices/{png_path.name}",
    }


async def export_saved_invoice(invoice_guid: UUID, request: Request, user: dict):
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
        headers={"Content-Disposition": content_disposition(filename),
                 "Cache-Control": "no-store"},
    )


async def export_saved_invoice_dlx_excel(invoice_guid: UUID, request: Request, user: dict):
    invoice, lines = _saved_invoice_for_action(invoice_guid, request, user)
    buyer = get_company(invoice["buyer_company_id"]) if invoice.get("buyer_company_id") else None
    seller = get_company(invoice["seller_company_id"])
    if not buyer or not seller:
        raise HTTPException(status_code=422, detail="Hóa đơn thiếu thông tin khách hàng hoặc đơn vị xuất.")
    content = build_dlx_trip_workbook(invoice, lines, buyer, seller)
    filename = f"Ho_so_chuyen_hang_HD{int(invoice['invoice_number']):08d}_DLX.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(filename), "Cache-Control": "no-store"},
    )


async def export_saved_invoice_document(
    invoice_guid: UUID,
    request: Request,
    kind: str,
    format: Optional[str],
    download: Optional[str],
    view: Optional[str],
    user: dict,
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
                headers={"Content-Disposition": content_disposition(filename), "Cache-Control": "no-store"},
            )

        if format == "docx" or download == "docx" or format == "doc" or download == "doc":
            doc_bytes, prefix = render_dlx_document_docx(invoice, lines, buyer, seller, kind=kind)
            filename = f"{prefix}.docx"
            return Response(
                content=doc_bytes,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": content_disposition(filename), "Cache-Control": "no-store"},
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
            headers={"Content-Disposition": content_disposition(filename), "Cache-Control": "no-store"},
        )

    renderer = {"hdnt": render_hdnt_docx, "bbbg": render_bbbg_docx,
                "both": render_hdnt_bbbg_combined_docx}[kind]
    content, contract_no = renderer(trip, buyer, seller)
    req_format = download or format or "docx"
    return _handle_word_download(content, contract_no, prefix, req_format)


async def edit_bkav_invoice_page(invoice_guid: UUID, request: Request, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    invoice = queries.edit_bkav_invoice_page_select_bkav_invoices(invoice_guid)
    if not invoice:
        return HTMLResponse("Không tìm thấy hóa đơn.", status_code=404)
    if ctx.get("active_company_id") is not None and invoice["seller_company_id"] != ctx["active_company_id"]:
        return HTMLResponse("Hóa đơn không thuộc đơn vị đang chọn.", status_code=403)
    lines = queries.edit_bkav_invoice_page_select_bkav_invoice_lines(invoice_guid) or []
    ctx = _invoice_form_context(request, user, invoice=dict(invoice), lines=[dict(line) for line in lines])
    from frontend.modules.zalo.trip_requests import invoice_zalo_status
    st = invoice_zalo_status([str(invoice_guid)]).get(str(invoice_guid), {})
    ctx["zalo_sent"] = st.get("sent", False)
    ctx["zalo_group"] = st.get("group_name", "")
    ctx["back_url"] = recall_list_url(request, "cargo_list", "/admin/vehicles/cargo", f"/admin/vehicles/cargo?month={invoice['invoice_date']:%Y-%m}")
    return templates.TemplateResponse("pages/cargo/invoice_form.html", {"request": request, **ctx})


async def _delete_invoices_fully(
    guids: list[str],
    *,
    seller_company_id: Optional[int] = None,
    vehicle_id: Optional[int] = None,
) -> list[str]:
    from frontend.modules.zalo.inbox import clear_invoice_draft
    from frontend.modules.zalo.trip_requests import recall_invoice_zalo

    clean_guids = [str(g) for g in guids if g]
    if not clean_guids:
        return []

    target_rows = queries.select_invoices_to_delete(clean_guids, seller_company_id, vehicle_id) or []
    target_guids = [str(r["invoice_guid"]) for r in target_rows]
    if not target_guids:
        return []

    for guid in target_guids:
        await recall_invoice_zalo(guid)
        delete_stored_bkav_invoice_files(guid)

    deleted = await run_in_threadpool(
        delete_sales_invoices,
        target_guids,
        seller_company_id=seller_company_id,
        vehicle_id=vehicle_id,
    )

    for guid in deleted:
        clear_invoice_draft(guid)

    return deleted


async def delete_bkav_invoice_endpoint(invoice_guid: UUID, request: Request, user: dict):
    active_company_id = get_menu_context(request, user).get("active_company_id")
    deleted = await _delete_invoices_fully([str(invoice_guid)], seller_company_id=active_company_id)
    if not deleted:
        return JSONResponse({"ok": False, "message": "Không tìm thấy hóa đơn."}, status_code=404)
    return {"ok": True, "count": 1}


async def delete_cargo_trip_endpoint(trip_id: int, vehicle_id: Optional[int], user: dict):
    if not delete_cargo_trip(trip_id, vehicle_id):
        return JSONResponse({"ok": False, "message": "Không tìm thấy chuyến hàng."}, status_code=404)
    return {"ok": True}


class BulkDeleteTripsInput(BaseModel):
    ids: list[str | int]


async def bulk_delete_cargo_trips_endpoint(
    payload: BulkDeleteTripsInput,
    vehicle_id: Optional[int],
    user: dict,
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
    count = queries.delete_cargo_trips(trip_ids, vehicle_id)
    if invoice_guids:
        deleted_invoices = await _delete_invoices_fully(invoice_guids, vehicle_id=vehicle_id)
        count += len(deleted_invoices)
    if not count:
        return JSONResponse({"ok": False, "message": "Không tìm thấy chuyến hàng cần xóa."}, status_code=404)
    return {"ok": True, "count": count}


class BulkMarkUnpaidInput(BaseModel):
    ids: list[str | int]


async def bulk_mark_unpaid_endpoint(request: Request, payload: BulkMarkUnpaidInput, user: dict):
    if not payload.ids:
        return JSONResponse({"ok": False, "message": "Chưa chọn chứng từ nào."}, status_code=400)

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

    owner = get_menu_context(request, user).get("active_company_id")
    result = mark_unpaid(
        invoice_guids=invoice_guids,
        trip_ids=trip_ids,
        owner_company_id=owner,
        user_id=user.get("id"),
    )

    count = result["invoices"] + result["trips"]
    if count == 0:
        return JSONResponse(
            {"ok": False, "message": "Không có chứng từ nào đang 'Đã trả' để chuyển."},
            status_code=409,
        )
    return {"ok": True, "count": count, "skipped": result.get("skipped", 0)}


async def scan_handwriting(request: Request, vehicle_id: Optional[int], image: UploadFile, user: dict):
    _cleanup_scan_reviews()
    if vehicle_id is None:
        form = await request.form()
        v_form = form.get("vehicle_id")
        if v_form and str(v_form).isdigit():
            vehicle_id = int(v_form)
        else:
            first_v = queries.scan_handwriting_select_plate_whitelist_2()
            vehicle_id = first_v["id"] if first_v else None

    if not vehicle_id:
        return RedirectResponse(url="/admin/vehicles/cargo?scan_error=1", status_code=303)

    vehicle = queries.select_vehicle_by_id(vehicle_id)
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


async def review_cargo_scan(request: Request, token: str, vehicle_id: Optional[int], user: dict):
    _cleanup_scan_reviews()
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"

    review = _load_scan_review(token)
    if not review or review.get("user_id") != user.get("id"):
        return RedirectResponse(url="/admin/vehicles/cargo?scan_error=1", status_code=303)

    vid = vehicle_id or review.get("vehicle_id")
    vehicle = queries.select_vehicle_by_id(vid)
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


async def confirm_cargo_scan(request: Request, vehicle_id: Optional[int], user: dict):
    _cleanup_scan_reviews()
    form = await request.form()
    token = str(form.get("token") or "")
    review = _load_scan_review(token)

    vid = vehicle_id or (int(form.get("vehicle_id")) if form.get("vehicle_id") and str(form.get("vehicle_id")).isdigit() else (review.get("vehicle_id") if review else None))
    if not review or not vid or review.get("user_id") != user.get("id"):
        return RedirectResponse(url="/admin/vehicles/cargo?scan_error=1", status_code=303)

    vehicle = queries.select_vehicle_by_id(vid)
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

    if not queries.add_reviewed_cargo_trips(
        vid, vehicle["plate_number"], [trip_fields for _, trip_fields, _, _ in pending_rows],
        seller_company_id=request.session.get("active_company_id"),
    ):
        return HTMLResponse("Không thể lưu các chuyến hàng đã duyệt.", status_code=500)

    for row_index, trip_fields, corrected, corrected_line_texts in pending_rows:
        _persist_ocr_sample(token, vid, str(row_index), candidates[row_index], corrected, corrected_line_texts)

    _delete_scan_review(token)
    return RedirectResponse(url=f"/admin/vehicles/cargo?vehicle_id={vid}&scanned={len(pending_rows)}&scan_skipped={skipped}", status_code=303)
