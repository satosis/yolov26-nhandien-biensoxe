"""Automated grouping of Zalo messages for invoice creation with 5-minute debounce."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from backend.services.database import db
from frontend.modules.zalo.inbox import INBOX_DIR, get_message, list_messages

logger = logging.getLogger(__name__)

SESSION_TIMEOUT_SECONDS = 300  # 5 minutes
SESSIONS_DIR = INBOX_DIR / "invoice_sessions"
DRAFTS_DIR = INBOX_DIR / "trip_drafts"

# Memory cache for coordinates -> address to avoid repeated HTTP calls
_GEOCODE_CACHE: dict[str, str] = {}
_GEOCODE_STANDARD_CACHE: dict[str, str] = {}
_ACTIVE_TIMERS: dict[str, asyncio.TimerHandle] = {}

_MILLION = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:triệu|tr\b)", re.I)
_COORD_RE = re.compile(r"([0-9]{1,2}\.[0-9]{4,8})[\s,]+([0-9]{2,3}\.[0-9]{4,8})")


def _clean_coordinate_key(lat: float, lon: float) -> str:
    return f"{round(lat, 5)},{round(lon, 5)}"


def resolve_coordinates_address(lat: float, lon: float, timeout_secs: float = 6.0) -> str:
    """Reverse geocode (lat, lon) to a readable Vietnamese address."""
    key = _clean_coordinate_key(lat, lon)
    if key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[key]

    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=18&addressdetails=1"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "YoloV26-Transport-App/1.0 (contact@dailoxanh.vn)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_secs) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            addr = data.get("display_name", "")
            clean_addr = re.sub(r",\s*việt\s*nam\s*$", "", addr, flags=re.IGNORECASE).strip()
            result = clean_addr or addr or f"Tọa độ ({lat}, {lon})"
            _GEOCODE_CACHE[key] = result
            return result
    except Exception as exc:
        logger.warning("Reverse geocode failed for (%s, %s): %s", lat, lon, exc)

    fallback = f"Tọa độ ({lat}, {lon})"
    _GEOCODE_CACHE[key] = fallback
    return fallback


def resolve_coordinates_address_standard(lat: float, lon: float, timeout_secs: float = 6.0) -> str:
    """Reverse geocode (lat, lon) to concise administrative location: 'Xã/Phường, Tỉnh'."""
    key = _clean_coordinate_key(lat, lon)
    if key in _GEOCODE_STANDARD_CACHE:
        return _GEOCODE_STANDARD_CACHE[key]

    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=18&addressdetails=1"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "YoloV26-Transport-App/1.0 (contact@dailoxanh.vn)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_secs) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            addr = data.get("address", {})
            county = addr.get("county") or addr.get("suburb") or addr.get("village") or addr.get("town") or ""
            state = addr.get("state") or ""
            if county and state:
                result = f"{county}, {state}".strip(" ,")
                _GEOCODE_STANDARD_CACHE[key] = result
                return result
            clean = re.sub(r",\s*việt\s*nam\s*$", "", data.get("display_name", ""), flags=re.IGNORECASE).strip()
            result = clean or f"Tọa độ ({lat}, {lon})"
            _GEOCODE_STANDARD_CACHE[key] = result
            return result
    except Exception as exc:
        logger.warning("Standard geocode failed for (%s, %s): %s", lat, lon, exc)

    fallback = f"Tọa độ ({lat}, {lon})"
    _GEOCODE_STANDARD_CACHE[key] = fallback
    return fallback


def extract_matching_company(text: str) -> Optional[dict]:
    """Check if the text contains a tax code matching an active company in invoice_companies."""
    if not text:
        return None
    codes = re.findall(r"\b\d{10}(?:-\d{3})?\b", text)
    if not codes:
        return None
    try:
        rows = db._execute_query(
            "SELECT id, name, tax_code, address, phone, email FROM public.invoice_companies WHERE tax_code = ANY(%s)",
            (codes,),
            fetchall=True,
        )
        if rows:
            return rows[0]
    except Exception as exc:
        logger.warning("Error looking up tax code in DB: %s", exc)
    return None


def get_active_session(group_id: str) -> Optional[dict]:
    """Load current gathering session for group_id if still valid (< 5 min since last message)."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{group_id}.json"
    if not path.exists():
        return None
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
        if session.get("status") == "collecting":
            elapsed = time.time() - session.get("last_message_at", 0)
            if elapsed <= SESSION_TIMEOUT_SECONDS + 60:
                return session
            session["status"] = "expired"
            path.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return None


def save_session(session: dict) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{session['group_id']}.json"
    path.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")


def extract_multiline_invoice_data(messages: list[dict], seller_company_id: int | None = None) -> dict:
    """Parse messages from Zalo group and extract complete multi-line invoice payload matching standard schema."""
    texts = [str(m.get("text") or "").strip() for m in messages]
    all_text = " \n ".join(texts)

    # 1. Tax code & Buyer Company
    matched_comp = None
    tax_codes = re.findall(r"\b\d{10}(?:-\d{3})?\b", all_text)
    if tax_codes:
        matched_comp = extract_matching_company(tax_codes[0])

    buyer_id = matched_comp["id"] if matched_comp else None
    buyer_name = matched_comp["name"] if matched_comp else ""
    buyer_tax = matched_comp["tax_code"] if matched_comp else (tax_codes[0] if tax_codes else "")
    buyer_addr = matched_comp.get("address", "") if matched_comp else ""

    # 2. Seller Company (from messages or default)
    seller_id = seller_company_id or messages[0].get("company_id")
    if not seller_id:
        seller_id = 33  # CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH default

    # 3. Clean Base Origin: e.g. "Phường Vĩnh Yên, Tỉnh Phú Thọ"
    origin = "Phường Vĩnh Yên, Tỉnh Phú Thọ"
    for t in texts:
        if "phu tho" in t.lower() or "vinh phuc" in t.lower() or "vinh yen" in t.lower():
            # Check if text contains "phường Vĩnh Yên, tỉnh Phú Thọ"
            m_orig = re.search(r"((?:phường|xã|thị trấn)\s+[^,]+,\s*(?:tỉnh|thành phố)\s+[^,\.]+)", t, re.I)
            if m_orig:
                origin = m_orig.group(1).strip(" .,-")
                break
    if not origin and buyer_addr:
        origin = buyer_addr

    # 4. Vehicle detection
    vehicle_id = 6  # 19H-147.98 (Nguyễn Kim Quyết - "Xe bố")
    all_vehicles = db.select("plate_whitelist", order_by="plate_number ASC") or []
    from frontend.modules.transport.trips_router import _find_vehicle_by_nickname
    vehicle_nick = ""
    for t in texts:
        m_v = re.search(r"\bxe\s+(chú|bác|anh|em|ông|bố|mẹ)(?:\s+([A-Za-z0-9_À-ỹ]+))?", t, re.I)
        if m_v:
            title = m_v.group(1).strip()
            name = (m_v.group(2) or "").strip()
            if name.lower() in {"xuat", "chua", "roi", "di", "ve", "nay", "do", "can", "chuyen", "vat"}:
                name = ""
            vehicle_nick = f"{title} {name}".strip() if name else title
            break
    if vehicle_nick:
        matched_v = _find_vehicle_by_nickname(vehicle_nick, all_vehicles, db)
        if matched_v:
            vehicle_id = matched_v["id"]

    # 5. Cargo & Service
    cargo = "Bàn cân"
    if "bàn cân" in all_text.lower():
        cargo = "Bàn cân"
    service = "Cước cẩu, vận chuyển"
    if any(k in all_text.lower() for k in ("cước cẩu", "cước cau", "vận chuyển")):
        service = "Cước cẩu, vận chuyển"

    # 6. Extract Legs / Segments: pair each coordinate with its subsequent price
    legs = []
    curr_dest = ""
    for m in messages:
        t = str(m.get("text") or "").strip()
        cm = _COORD_RE.search(t)
        if cm:
            lat, lon = float(cm.group(1)), float(cm.group(2))
            curr_dest = resolve_coordinates_address_standard(lat, lon)
        pm = _MILLION.search(t)
        if pm:
            amt = float(pm.group(1).replace(",", ".")) * 1_000_000
            legs.append({
                "destination": curr_dest or "Địa điểm giao hàng",
                "amount": amt,
                "text": t,
            })
            curr_dest = ""  # reset for next leg

    # Build bkav_invoice_lines
    lines = []
    for idx, leg in enumerate(legs, 1):
        amt = leg["amount"]
        dest = leg["destination"]
        tax_amt = round(amt * 0.08)
        desc = f"{service} {cargo.lower()} từ {origin} đi {dest}"
        lines.append({
            "line_number": idx,
            "vehicle_id": vehicle_id,
            "cargo_description": cargo,
            "cargo_weight_tons": None,
            "origin": origin,
            "destination": dest,
            "amount": amt,
            "tax_amount": tax_amt,
            "description": desc,
            "via_points": [],
        })

    subtotal = sum(l["amount"] for l in lines) if lines else 0.0
    vat_rate = 0.08
    gross = round(subtotal * (1 + vat_rate))
    debt_amount = gross

    return {
        "seller_company_id": seller_id,
        "buyer_company_id": buyer_id,
        "buyer_name": buyer_name,
        "buyer_tax_code": buyer_tax,
        "invoice_date": date.today().isoformat(),
        "service_name": service,
        "vat_rate": vat_rate,
        "payment_method": "CK",
        "payment_status": "debt",
        "debt_amount": debt_amount,
        "subtotal": subtotal,
        "gross": gross,
        "line_count": len(lines),
        "lines": lines,
    }


def create_bkav_invoice_from_data(payload: dict) -> str:
    """Save payload directly into bkav_invoices and bkav_invoice_lines, returning invoice_guid."""
    from frontend.modules.transport.trips_router import _get_next_invoice_number_and_serial
    next_num, next_serial = _get_next_invoice_number_and_serial(payload["seller_company_id"])
    invoice_number = int(next_num)
    invoice_serial = next_serial

    guid = str(uuid4())
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO public.bkav_invoices
                    (invoice_guid, seller_company_id, buyer_company_id, invoice_date,
                     invoice_number, invoice_serial, buyer_tax_code, service_name,
                     vat_rate, payment_method, payment_status, debt_amount,
                     subtotal, gross, line_count, entry_origin)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'manual')
            """, (
                guid, payload["seller_company_id"], payload["buyer_company_id"],
                payload["invoice_date"], invoice_number, invoice_serial,
                payload["buyer_tax_code"], payload["service_name"],
                str(payload["vat_rate"]), payload["payment_method"],
                payload["payment_status"], str(payload["debt_amount"]),
                str(payload["subtotal"]), str(payload["gross"]),
                payload["line_count"],
            ))
            for line in payload["lines"]:
                cursor.execute("""
                    INSERT INTO public.bkav_invoice_lines
                        (invoice_guid, line_number, vehicle_id,
                         cargo_description, cargo_weight_tons, description, origin,
                         destination, via_points, amount, tax_amount, distance_km,
                         fuel_liters, fuel_price_per_liter, fuel_cost_total)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    guid, line["line_number"], line["vehicle_id"],
                    line.get("cargo_description"), None,
                    line["description"], line["origin"], line["destination"],
                    json.dumps(line.get("via_points") or [], ensure_ascii=False),
                    str(line["amount"]), str(line.get("tax_amount") or 0),
                    None, None, "29250.00", None,
                ))
        conn.commit()

    logger.info("Created real bkav_invoice %s (Invoice #%s-%s) from Zalo messages", guid, invoice_number, invoice_serial)
    return guid


def finalize_invoice_session_sync(group_id: str) -> Optional[dict]:
    """Group all messages in the session, build trip draft, save invoice in database, and link URLs."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{group_id}.json"
    if not path.exists():
        return None
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if session.get("status") not in ("collecting", "ready_to_finalize"):
        return None

    message_ids = session.get("message_ids") or []
    if not message_ids:
        session["status"] = "cancelled"
        save_session(session)
        return None

    messages = [get_message(mid) for mid in message_ids]
    messages = [m for m in messages if m is not None]
    if not messages:
        session["status"] = "cancelled"
        save_session(session)
        return None

    from frontend.modules.zalo.trip_requests import build_trip_draft
    from frontend.modules.administrative_divisions import list_provinces
    from frontend.modules.transport.catalog import list_items as list_catalog_items

    try:
        provinces = [row["name"] for row in list_provinces()]
        services = [row["name"] for row in list_catalog_items("service")]
        cargo_types = [row["name"] for row in list_catalog_items("cargo_type")]

        draft = build_trip_draft(
            messages,
            provinces=provinces,
            services=services,
            cargo_types=cargo_types,
        )
    except Exception as exc:
        logger.exception("Failed to build trip draft for group %s: %s", group_id, exc)
        session["status"] = "error"
        session["error"] = str(exc)
        save_session(session)
        return None

    # Multi-line invoice extraction
    invoice_payload = extract_multiline_invoice_data(messages, seller_company_id=draft.get("seller_company_id"))
    invoice_guid = None
    if invoice_payload.get("lines"):
        try:
            invoice_guid = create_bkav_invoice_from_data(invoice_payload)
        except Exception as exc:
            logger.exception("Could not insert bkav_invoice directly: %s", exc)

    draft_id = f"draft_{group_id}_{int(time.time())}"
    draft["draft_id"] = draft_id
    draft["group_id"] = group_id
    draft["created_at"] = datetime.now(timezone.utc).isoformat()
    draft["session_messages_count"] = len(messages)
    draft["invoice_guid"] = invoice_guid
    draft["invoice_url"] = f"/admin/vehicles/cargo/invoices/{invoice_guid}/edit" if invoice_guid else ""
    draft["lines"] = invoice_payload.get("lines", [])
    draft["subtotal"] = invoice_payload.get("subtotal", draft.get("amount", 0))
    draft["gross"] = invoice_payload.get("gross", draft.get("amount", 0))

    # Save draft file
    draft_path = DRAFTS_DIR / f"{draft_id}.json"
    draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")

    # Update session status
    session["status"] = "completed"
    session["draft_id"] = draft_id
    session["invoice_guid"] = invoice_guid
    session["invoice_url"] = draft["invoice_url"]
    session["completed_at"] = time.time()
    save_session(session)

    # Update individual messages with invoice draft link
    for m in messages:
        mid = m["id"]
        m_path = INBOX_DIR / f"{mid}.json"
        if m_path.exists():
            try:
                m_data = json.loads(m_path.read_text(encoding="utf-8"))
                m_data["invoice_draft"] = {
                    "draft_id": draft_id,
                    "invoice_guid": invoice_guid,
                    "invoice_url": draft["invoice_url"],
                    "company_name": invoice_payload.get("buyer_name") or draft.get("company_name") or session.get("company_name", ""),
                    "amount": invoice_payload.get("gross", draft.get("amount", 0)),
                    "subtotal": invoice_payload.get("subtotal", 0),
                    "line_count": len(invoice_payload.get("lines", [])),
                    "vehicle": draft.get("vehicle_nickname", ""),
                    "destination": draft.get("destination", ""),
                    "state": "ready",
                }
                m_path.write_text(json.dumps(m_data, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    logger.info("Successfully finalized invoice session for group %s -> draft %s (Invoice: %s)", group_id, draft_id, invoice_guid)
    return draft


async def _delay_and_finalize(group_id: str, scheduled_for: float) -> None:
    """Async worker: wait until scheduled_for, checking if new messages extended the window."""
    while True:
        now = time.time()
        remaining = scheduled_for - now
        if remaining > 0:
            await asyncio.sleep(min(remaining, 5.0))
            session = get_active_session(group_id)
            if not session or session.get("status") != "collecting":
                return
            new_scheduled = session.get("last_message_at", 0) + SESSION_TIMEOUT_SECONDS
            if new_scheduled > scheduled_for:
                scheduled_for = new_scheduled
                continue
        else:
            break

    # 5 minutes elapsed without new messages -> finalize!
    await asyncio.to_thread(finalize_invoice_session_sync, group_id)


def schedule_session_finalization(group_id: str, scheduled_for: float) -> None:
    """Ensure background async task is running for this session."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_delay_and_finalize(group_id, scheduled_for))
    except RuntimeError:
        pass


async def process_invoice_message_ingest(item: dict) -> None:
    """Inspect ingested message; if MST matches DB or active session exists, group and set 5-min timer."""
    group_id = str(item.get("group_id") or "")
    if not group_id:
        return

    text = str(item.get("text") or "").strip()
    matched_company = extract_matching_company(text)
    active_session = get_active_session(group_id)
    now = time.time()

    if matched_company:
        session = {
            "group_id": group_id,
            "group_name": item.get("group_name", ""),
            "company_id": matched_company["id"],
            "company_name": matched_company["name"],
            "tax_code": matched_company["tax_code"],
            "message_ids": (active_session.get("message_ids", []) if active_session else []) + [item["id"]],
            "started_at": active_session.get("started_at", now) if active_session else now,
            "last_message_at": now,
            "status": "collecting",
        }
        session["message_ids"] = list(dict.fromkeys(session["message_ids"]))
        save_session(session)
        schedule_session_finalization(group_id, now + SESSION_TIMEOUT_SECONDS)
        logger.info(
            "Group %s: message has matching tax code %s (%s). Starting 5-minute debounce window.",
            group_id, matched_company["tax_code"], matched_company["name"],
        )
    elif active_session and active_session.get("status") == "collecting":
        active_session["message_ids"].append(item["id"])
        active_session["message_ids"] = list(dict.fromkeys(active_session["message_ids"]))
        active_session["last_message_at"] = now
        save_session(active_session)
        schedule_session_finalization(group_id, now + SESSION_TIMEOUT_SECONDS)
        logger.info(
            "Group %s: message added to active invoice session for company '%s'. Debounce extended by 5 minutes.",
            group_id, active_session.get("company_name", ""),
        )
