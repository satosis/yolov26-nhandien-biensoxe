"""
SaveInvoice Synchronization Service.
Connects to SaveInvoice (https://login.saveinvoice.vn) to fetch and sync
e-invoices (Hóa đơn đầu vào / mua vào) directly from General Department of Taxation (Tổng cục Thuế).

Features:
  1. Multi-company account resolution: strictly isolates and filters invoices by target company tax code (e.g. 2500774911 - CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH).
  2. Automatic token refresh and session maintenance via Express connect.sid and JWT tokens.
  3. Seamless preview and comparison with existing records in transport_input_invoices.
  4. Full XML e-invoice download and attachment persistence.
  5. Automatic seller company resolution into public.invoice_companies.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import io
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import websockets

import httpx
from psycopg2 import Binary

from backend.services.database import db
from backend.services.settings_service import get_app_setting, set_many_app_settings

logger = logging.getLogger(__name__)

SAVEINVOICE_BASE = "https://login.saveinvoice.vn"
DEFAULT_TARGET_TAX_CODE = "2500774911"
DEFAULT_ACCOUNT_ID = "6a09211567e41b01ff3d13ca"


def get_saveinvoice_config() -> Dict[str, str]:
    return {
        "username": get_app_setting("saveinvoice_username", "2500561906").strip(),
        "password": get_app_setting("saveinvoice_password", "Thue@123").strip(),
        "target_tax_code": (get_app_setting("saveinvoice_target_tax_code", "").strip() or DEFAULT_TARGET_TAX_CODE),
        "account_id": (get_app_setting("saveinvoice_account_id", "").strip() or DEFAULT_ACCOUNT_ID),
        "access_token": get_app_setting("saveinvoice_access_token", "").strip(),
        "refresh_token": get_app_setting("saveinvoice_refresh_token", "").strip(),
        "connect_sid": get_app_setting("saveinvoice_connect_sid", "").strip(),
    }


def refresh_saveinvoice_tokens() -> bool:
    """Refreshes access_token using refresh_token against SaveInvoice API v2."""
    cfg = get_saveinvoice_config()
    refresh_token = cfg["refresh_token"]
    connect_sid = cfg["connect_sid"]
    if not refresh_token:
        logger.warning("No refresh_token found for SaveInvoice.")
        return False

    cookies = {"connect.sid": connect_sid} if connect_sid else {}
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        with httpx.Client(timeout=15.0, cookies=cookies) as client:
            resp = client.post(
                f"{SAVEINVOICE_BASE}/api_v2/auth/refresh-token",
                json={"refresh_token": refresh_token},
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                new_access = data.get("access_token")
                new_refresh = data.get("refresh_token")
                updates = {}
                if new_access:
                    updates["saveinvoice_access_token"] = new_access
                if new_refresh:
                    updates["saveinvoice_refresh_token"] = new_refresh
                if updates:
                    set_many_app_settings(updates)
                    logger.info("SaveInvoice access_token refreshed successfully.")
                    return True
            logger.warning("SaveInvoice refresh-token endpoint returned %s: %s", resp.status_code, resp.text[:150])
    except Exception as exc:
        logger.error("Failed to refresh SaveInvoice token: %s", exc)

    # Fallback: attempt to read latest tokens from active Chrome CDP session if available
    return _try_recover_session_via_cdp()


def _try_recover_session_via_cdp() -> bool:
    """Attempts to read latest tokens and cookies from Chrome CDP on port 9222."""
    try:
        import asyncio
        async def _read_cdp():
            req = urllib.request.Request("http://127.0.0.1:9222/json")
            pages = json.loads(urllib.request.urlopen(req, timeout=3).read().decode())
            save_page = next((p for p in pages if "saveinvoice" in p.get("url", "")), None)
            if not save_page:
                return False
            ws_url = save_page.get("webSocketDebuggerUrl")
            async with websockets.connect(ws_url, close_timeout=3) as ws:
                # 1. LocalStorage
                await ws.send(json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": "JSON.stringify({acc: localStorage.getItem('iam-hkd-react.v1.0.0_access_token'), ref: localStorage.getItem('iam-hkd-react.v1.0.0_refresh_token')})",
                        "returnByValue": True,
                    }
                }))
                resp1 = json.loads(await ws.recv())
                val_str = resp1.get("result", {}).get("result", {}).get("value")
                tokens = json.loads(val_str) if val_str else {}

                # 2. Cookie
                await ws.send(json.dumps({
                    "id": 2,
                    "method": "Network.getCookies",
                    "params": {"urls": [SAVEINVOICE_BASE]}
                }))
                resp2 = json.loads(await ws.recv())
                cookies = resp2.get("result", {}).get("cookies", [])
                sid = next((c.get("value") for c in cookies if c.get("name") == "connect.sid"), "")

                updates = {}
                if tokens.get("acc"):
                    updates["saveinvoice_access_token"] = tokens["acc"]
                if tokens.get("ref"):
                    updates["saveinvoice_refresh_token"] = tokens["ref"]
                if sid:
                    updates["saveinvoice_connect_sid"] = sid
                if updates:
                    set_many_app_settings(updates)
                    logger.info("Successfully recovered SaveInvoice session via Chrome CDP.")
                    return True
            return False

        return asyncio.run(_read_cdp())
    except Exception as exc:
        logger.debug("CDP recovery not available: %s", exc)
        return False


def get_authenticated_client() -> Tuple[httpx.Client, Dict[str, str]]:
    """Returns an HTTP client and headers configured with valid SaveInvoice authentication."""
    cfg = get_saveinvoice_config()
    token = cfg["access_token"]
    sid = cfg["connect_sid"]

    cookies = {"connect.sid": sid} if sid else {}
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {token}" if token else "",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"{SAVEINVOICE_BASE}/",
        "Origin": SAVEINVOICE_BASE,
    }
    client = httpx.Client(timeout=25.0, cookies=cookies)
    return client, headers


def list_saveinvoice_accounts(target_tax: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetches the list of all external tax accounts linked to the user account on SaveInvoice.
    Matches and flags the currently active target company tax code.
    """
    client, headers = get_authenticated_client()
    cfg = get_saveinvoice_config()
    active_tax = (target_tax or cfg.get("target_tax_code") or "").strip()

    lazy_load = json.dumps({"filters": {"visible": {"value": True, "matchMode": "equals"}}})
    url = f"{SAVEINVOICE_BASE}/api/external_accounts"

    try:
        resp = client.get(url, headers=headers, params={"lazyLoadEvent": lazy_load})
        if resp.status_code == 401:
            # Try refreshing token once
            if refresh_saveinvoice_tokens():
                client, headers = get_authenticated_client()
                resp = client.get(url, headers=headers, params={"lazyLoadEvent": lazy_load})

        if resp.status_code != 200:
            logger.error("Failed to list SaveInvoice accounts: %s %s", resp.status_code, resp.text)
            return []

        data = resp.json()
        items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

        # Fetch local company records for automatic mapping
        db_comps = {
            (r["tax_code"] or "").strip(): r
            for r in (db._execute_query("SELECT id, tax_code, name FROM public.invoice_companies", fetchall=True, commit=False) or [])
            if r.get("tax_code")
        }

        accounts = []
        for it in items:
            tax = str(it.get("username") or it.get("tax_code") or "").strip()
            name = (it.get("profile") or {}).get("name") or it.get("name") or tax
            acc_id = it.get("_id")
            status = it.get("status")
            is_target = bool(active_tax and tax == active_tax)
            comp_match = db_comps.get(tax)

            accounts.append({
                "account_id": acc_id,
                "tax_code": tax,
                "name": name,
                "status": status,
                "is_target": is_target,
                "company_id": comp_match["id"] if comp_match else None,
                "matched_in_system": bool(comp_match),
            })

        # Ensure target company appears first
        accounts.sort(key=lambda a: 0 if a["is_target"] else 1)
        return accounts
    except Exception as exc:
        logger.error("Error calling SaveInvoice accounts API: %s", exc)
        return []
    finally:
        client.close()


def _invoice_exists_in_db(
    invoice_number: str,
    serial: str,
    inv_date: Optional[date],
    seller_tax: str,
    buyer_company_id: Optional[int] = None,
    buyer_tax_code: Optional[str] = None,
) -> bool:
    """
    Checks if an invoice has already been imported into transport_input_invoices.
    Strictly isolates check by buyer company / buyer tax code so different companies
    never interfere with or block each other.
    """
    inv_num_clean = str(invoice_number or "").strip()
    serial_clean = str(serial or "").strip()
    seller_tax_clean = str(seller_tax or "").strip()
    buyer_tax_clean = str(buyer_tax_code or "").strip()

    if not inv_num_clean:
        return False

    buyer_clauses = []
    buyer_params = []
    if buyer_company_id:
        buyer_clauses.append("buyer_company_id = %s")
        buyer_params.append(buyer_company_id)
    if buyer_tax_clean:
        buyer_clauses.append("buyer_tax_code = %s")
        buyer_params.append(buyer_tax_clean)

    buyer_where = ""
    if buyer_clauses:
        buyer_where = f" AND ({' OR '.join(buyer_clauses)})"

    # Check 1: exact match on number and serial (and seller tax code if known)
    if serial_clean:
        if seller_tax_clean:
            q = f"""
                SELECT id FROM public.transport_input_invoices
                 WHERE invoice_number = %s AND (serial = %s OR serial LIKE %s)
                   AND (seller_tax_code = %s OR seller_tax_code IS NULL)
                   {buyer_where}
                 LIMIT 1
            """
            params = [inv_num_clean, serial_clean, f"%{serial_clean}%", seller_tax_clean] + buyer_params
        else:
            q = f"""
                SELECT id FROM public.transport_input_invoices
                 WHERE invoice_number = %s AND (serial = %s OR serial LIKE %s)
                   {buyer_where}
                 LIMIT 1
            """
            params = [inv_num_clean, serial_clean, f"%{serial_clean}%"] + buyer_params
        row = db._execute_query(q, tuple(params), fetch=True, commit=False)
        if row:
            return True

    # Check 2: exact match on number and invoice date (and seller tax if known)
    if inv_date:
        if seller_tax_clean:
            q = f"""
                SELECT id FROM public.transport_input_invoices
                 WHERE invoice_number = %s AND invoice_date = %s
                   AND (seller_tax_code = %s OR seller_tax_code IS NULL)
                   {buyer_where}
                 LIMIT 1
            """
            params = [inv_num_clean, inv_date, seller_tax_clean] + buyer_params
        else:
            q = f"""
                SELECT id FROM public.transport_input_invoices
                 WHERE invoice_number = %s AND invoice_date = %s
                   {buyer_where}
                 LIMIT 1
            """
            params = [inv_num_clean, inv_date] + buyer_params
        row = db._execute_query(q, tuple(params), fetch=True, commit=False)
        if row:
            return True

    return False


def preview_saveinvoice_invoices(
    account_id: Optional[str] = None,
    month: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    buyer_company_id: Optional[int] = None,
    buyer_tax_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetches purchase invoices from SaveInvoice for the specified account,
    identifying whether each invoice is already in the database or new for this specific company.
    """
    accounts = list_saveinvoice_accounts(target_tax=buyer_tax_code)
    target_account = None
    if account_id:
        target_account = next((a for a in accounts if a["account_id"] == account_id), None)
    if not target_account:
        target_account = next((a for a in accounts if a["is_target"]), None)
    if not target_account and accounts:
        target_account = accounts[0]

    if not target_account:
        return {"ok": False, "error": "Chưa tìm thấy mã tài khoản SaveInvoice cho công ty."}

    target_account_id = target_account["account_id"]
    target_tax = target_account["tax_code"]
    target_name = target_account["name"]

    # Resolve buyer company id in DB
    resolved_cid = buyer_company_id
    if not resolved_cid and target_tax:
        c_row = db._execute_query(
            "SELECT id FROM public.invoice_companies WHERE tax_code = %s LIMIT 1",
            (target_tax,), fetch=True, commit=False,
        )
        if c_row:
            resolved_cid = c_row["id"]

    client, headers = get_authenticated_client()
    lazy_load = {
        "first": offset,
        "rows": limit,
        "sortField": "data.tdlap",
        "sortOrder": -1,
        "filters": {},
    }

    url = f"{SAVEINVOICE_BASE}/api/invoices/account/{target_account_id}/type/purchase"

    try:
        resp = client.get(url, headers=headers, params={"lazyLoadEvent": json.dumps(lazy_load)})
        if resp.status_code == 401:
            if refresh_saveinvoice_tokens():
                client, headers = get_authenticated_client()
                resp = client.get(url, headers=headers, params={"lazyLoadEvent": json.dumps(lazy_load)})

        if resp.status_code != 200:
            return {"ok": False, "error": f"Lỗi từ SaveInvoice ({resp.status_code}): {resp.text[:150]}"}

        data = resp.json()
        total_records = data.get("totalRecords", 0)
        items = data.get("items", [])

        invoices = []
        for it in items:
            raw_id = it.get("_id")
            header = it.get("header") or {}
            detail = it.get("detail") or {}
            keys = it.get("keys") or {}

            # Dates
            tdlap_raw = header.get("tdlap") or detail.get("tdlap") or ""
            parsed_date = None
            date_display = ""
            if "/" in str(tdlap_raw):
                # Format: DD/MM/YYYY
                parts = str(tdlap_raw).split("/")
                if len(parts) == 3:
                    try:
                        parsed_date = date(int(parts[2]), int(parts[1]), int(parts[0]))
                        date_display = parsed_date.strftime("%d/%m/%Y")
                    except Exception:
                        pass
            elif tdlap_raw:
                try:
                    parsed_date = datetime.fromisoformat(str(tdlap_raw).replace("Z", "+00:00")).date()
                    date_display = parsed_date.strftime("%d/%m/%Y")
                except Exception:
                    date_display = str(tdlap_raw)[:10]

            # If month filter is applied, skip invoices from other months
            if month and parsed_date:
                inv_month_str = parsed_date.strftime("%Y-%m")
                if inv_month_str != month:
                    continue

            # Invoice numbers & serial
            shdon = str(keys.get("shdon") or header.get("shdon") or detail.get("shdon") or "").strip()
            khhdon = str(keys.get("khhdon") or header.get("khhdon") or detail.get("khhdon") or "").strip()
            khmshdon = str(keys.get("khmshdon") or header.get("khmshdon") or detail.get("khmshdon") or "").strip()
            full_serial = f"{khmshdon}{khhdon}" if khmshdon and not khhdon.startswith(khmshdon) else khhdon

            # Seller info
            nbmst = str(keys.get("nbmst") or header.get("nbmst") or detail.get("nbmst") or "").strip()
            nbten = str(header.get("nbten") or detail.get("nbten") or "").strip()
            nbdchi = str(detail.get("nbdchi") or "").strip()

            # Buyer info from invoice or fallback to account company
            nmmst = str(header.get("nmmst") or detail.get("nmmst") or target_tax).strip()
            nmten = str(header.get("nmten") or detail.get("nmten") or target_name).strip()
            nmdchi = str(detail.get("nmdchi") or "").strip()

            # Monetary amounts
            subtotal = float(header.get("tgtcthue") or detail.get("tgtcthue") or 0)
            vat_amount = float(header.get("tgtthue") or detail.get("tgtthue") or 0)
            total = float(header.get("tgtttbso") or detail.get("tgtttbso") or 0)
            if not total and (subtotal or vat_amount):
                total = subtotal + vat_amount

            # Line items summary
            raw_items = detail.get("hdhhdvu") or []
            item_summaries = []
            for r_item in raw_items[:4]:
                name = r_item.get("ten") or ""
                qty = r_item.get("sluong") or ""
                unit = r_item.get("dvtinh") or ""
                price = r_item.get("dgia") or ""
                item_summaries.append(f"{name} ({qty} {unit})".strip() if qty and unit else name)
            items_text = "; ".join(item_summaries)
            if len(raw_items) > 4:
                items_text += f"; ... (+{len(raw_items) - 4} mặt hàng khác)"

            # Check if invoice exists in DB strictly for this buyer company
            already_exists = _invoice_exists_in_db(
                shdon, full_serial, parsed_date, nbmst,
                buyer_company_id=resolved_cid, buyer_tax_code=target_tax
            )

            invoices.append({
                "id": raw_id,
                "invoice_number": shdon,
                "serial": full_serial,
                "form_code": khmshdon,
                "invoice_date": parsed_date.isoformat() if parsed_date else "",
                "invoice_date_display": date_display,
                "seller_tax_code": nbmst,
                "seller_name": nbten,
                "seller_address": nbdchi,
                "buyer_tax_code": nmmst,
                "buyer_name": nmten,
                "buyer_address": nmdchi,
                "subtotal": subtotal,
                "vat_amount": vat_amount,
                "total": total,
                "items_count": len(raw_items),
                "items_summary": items_text,
                "payment_method": detail.get("thtttoan") or "Chuyển khoản",
                "status": "exists" if already_exists else "new",
                "exists": already_exists,
            })

        return {
            "ok": True,
            "total_records": total_records,
            "account_id": target_account_id,
            "account_tax_code": target_tax,
            "account_name": target_name,
            "buyer_company_id": resolved_cid,
            "invoices": invoices,
            "new_count": sum(1 for inv in invoices if not inv["exists"]),
            "exists_count": sum(1 for inv in invoices if inv["exists"]),
        }
    except Exception as exc:
        logger.error("Error previewing SaveInvoice invoices: %s", exc)
        return {"ok": False, "error": f"Lỗi khi đọc hóa đơn: {exc}"}
    finally:
        client.close()


def download_saveinvoice_xml(invoice_id: str) -> Optional[bytes]:
    """Downloads the official XML e-invoice file for an invoice from SaveInvoice."""
    client, headers = get_authenticated_client()
    url = f"{SAVEINVOICE_BASE}/api/invoices/{invoice_id}/download/xml"
    try:
        resp = client.get(url, headers=headers)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception as exc:
        logger.warning("Could not download XML for invoice %s: %s", invoice_id, exc)
    finally:
        client.close()
    return None


def import_saveinvoice_invoices(
    invoice_ids: List[str],
    account_id: Optional[str] = None,
    buyer_company_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Imports the selected invoices from SaveInvoice into public.transport_input_invoices.
    Guarantees invoices are strictly saved under the designated buyer company.
    """
    if not invoice_ids:
        return {"ok": True, "imported": 0, "existing": 0, "errors": 0, "details": []}

    # Resolve target account and company
    accounts = list_saveinvoice_accounts()
    target_account = next((a for a in accounts if a["account_id"] == account_id), None) if account_id else None
    if not target_account and buyer_company_id:
        c_row = db._execute_query("SELECT tax_code, name FROM public.invoice_companies WHERE id = %s", (buyer_company_id,), fetch=True, commit=False)
        if c_row:
            target_account = next((a for a in accounts if a["tax_code"] == c_row["tax_code"]), None)
    if not target_account and accounts:
        target_account = accounts[0]

    target_tax = target_account["tax_code"] if target_account else ""
    target_name = target_account["name"] if target_account else ""

    target_cid = buyer_company_id
    target_address = ""
    if target_cid:
        c_info = db._execute_query("SELECT id, name, tax_code, address FROM public.invoice_companies WHERE id = %s", (target_cid,), fetch=True, commit=False)
        if c_info:
            target_name = c_info.get("name") or target_name
            target_tax = c_info.get("tax_code") or target_tax
            target_address = c_info.get("address") or ""
    elif target_tax:
        # Match or auto-create company by tax code
        c_info = db._execute_query("SELECT id, name, tax_code, address FROM public.invoice_companies WHERE tax_code = %s LIMIT 1", (target_tax,), fetch=True, commit=False)
        if c_info:
            target_cid = c_info["id"]
            target_name = c_info.get("name") or target_name
            target_address = c_info.get("address") or ""
        else:
            try:
                new_c = db._execute_query("""
                    INSERT INTO public.invoice_companies (tax_code, name, address, created_at, updated_at)
                    VALUES (%s, %s, '', now(), now())
                    RETURNING id
                """, (target_tax, target_name or target_tax), fetch=True, commit=True)
                if new_c:
                    target_cid = new_c["id"]
            except Exception as exc:
                logger.warning("Could not auto-create company for %s: %s", target_tax, exc)

    # Fetch invoice preview map
    actual_account_id = target_account["account_id"] if target_account else account_id
    preview = preview_saveinvoice_invoices(
        account_id=actual_account_id,
        limit=200,
        buyer_company_id=target_cid,
        buyer_tax_code=target_tax,
    )
    if not preview.get("ok"):
        return {"ok": False, "error": preview.get("error")}

    invoices_map = {inv["id"]: inv for inv in preview.get("invoices", [])}

    imported_count = 0
    existing_count = 0
    error_count = 0
    details = []

    client, headers = get_authenticated_client()

    for inv_id in invoice_ids:
        inv = invoices_map.get(inv_id)
        if not inv:
            # If not in preview map, fetch individual invoice
            try:
                r = client.get(f"{SAVEINVOICE_BASE}/api/invoices/{inv_id}", headers=headers)
                if r.status_code == 200:
                    raw_data = r.json()
                    detail = raw_data.get("detail") or {}
                    header = raw_data.get("header") or {}
                    keys = raw_data.get("keys") or {}
                    inv = {
                        "id": inv_id,
                        "invoice_number": str(keys.get("shdon") or header.get("shdon") or detail.get("shdon") or "").strip(),
                        "serial": str(keys.get("khhdon") or header.get("khhdon") or detail.get("khhdon") or "").strip(),
                        "invoice_date": detail.get("tdlap") or "",
                        "seller_tax_code": str(keys.get("nbmst") or detail.get("nbmst") or "").strip(),
                        "seller_name": str(header.get("nbten") or detail.get("nbten") or "").strip(),
                        "seller_address": str(detail.get("nbdchi") or "").strip(),
                        "buyer_tax_code": str(header.get("nmmst") or detail.get("nmmst") or target_tax).strip(),
                        "buyer_name": str(header.get("nmten") or detail.get("nmten") or target_name).strip(),
                        "buyer_address": str(detail.get("nmdchi") or target_address).strip(),
                        "subtotal": float(header.get("tgtcthue") or detail.get("tgtcthue") or 0),
                        "vat_amount": float(header.get("tgtthue") or detail.get("tgtthue") or 0),
                        "total": float(header.get("tgtttbso") or detail.get("tgtttbso") or 0),
                        "payment_method": detail.get("thtttoan") or "Chuyển khoản",
                    }
            except Exception as e:
                logger.warning("Could not fetch invoice %s individually: %s", inv_id, e)

        if not inv:
            error_count += 1
            continue

        shdon = inv.get("invoice_number", "")
        serial = inv.get("serial", "")
        inv_date_str = inv.get("invoice_date", "")
        parsed_date = None
        if inv_date_str:
            try:
                parsed_date = date.fromisoformat(inv_date_str[:10])
            except Exception:
                parsed_date = date.today()
        else:
            parsed_date = date.today()

        seller_tax = inv.get("seller_tax_code", "")
        seller_name = inv.get("seller_name", "")
        seller_address = inv.get("seller_address", "")

        # Check deduplication strictly for this company
        if _invoice_exists_in_db(
            shdon, serial, parsed_date, seller_tax,
            buyer_company_id=target_cid, buyer_tax_code=target_tax
        ):
            existing_count += 1
            details.append({
                "id": inv_id,
                "invoice_number": shdon,
                "serial": serial,
                "seller_name": seller_name,
                "status": "exists",
            })
            continue

        # Download official XML file
        xml_bytes = download_saveinvoice_xml(inv_id)
        source_filename = f"{serial}_{shdon}.xml" if serial and shdon else f"invoice_{inv_id}.xml"
        source_type = "application/xml"

        # Resolve seller company ID in public.invoice_companies
        seller_company_id = None
        if seller_tax:
            c_row = db._execute_query(
                "SELECT id FROM public.invoice_companies WHERE tax_code = %s LIMIT 1",
                (seller_tax,), fetch=True, commit=False,
            )
            if c_row:
                seller_company_id = c_row["id"]
            else:
                try:
                    new_c = db._execute_query("""
                        INSERT INTO public.invoice_companies (tax_code, name, address, created_at, updated_at)
                        VALUES (%s, %s, %s, now(), now())
                        RETURNING id
                    """, (seller_tax, seller_name or seller_tax, seller_address), fetch=True, commit=True)
                    if new_c:
                        seller_company_id = new_c["id"]
                except Exception as exc:
                    logger.warning("Could not create invoice_companies record for %s: %s", seller_tax, exc)

        # Build items
        subtotal = Decimal(str(inv.get("subtotal") or 0))
        vat_amount = Decimal(str(inv.get("vat_amount") or 0))
        total = Decimal(str(inv.get("total") or (subtotal + vat_amount)))
        vat_percent = (vat_amount * 100 / subtotal).quantize(Decimal("1")) if subtotal > 0 else Decimal("0")

        items = [{
            "description": f"Hóa đơn {serial} {shdon} - {seller_name}",
            "unit": "hóa đơn",
            "quantity": "1",
            "unit_price": str(subtotal),
            "amount": str(subtotal),
        }]

        buyer_final_name = inv.get("buyer_name") or target_name
        buyer_final_tax = inv.get("buyer_tax_code") or target_tax
        buyer_final_address = inv.get("buyer_address") or target_address

        # Insert into database strictly with resolved buyer company
        try:
            inserted = db._execute_query("""
                INSERT INTO public.transport_input_invoices (
                    invoice_date, serial, invoice_number, seller_company_id, seller_name,
                    seller_tax_code, seller_address, buyer_name, buyer_tax_code, buyer_address,
                    payment_method, items, subtotal, vat_percent, vat_amount, total, note,
                    buyer_company_id, source_filename, source_type, source_data
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) RETURNING id
            """, (
                parsed_date, serial, shdon, seller_company_id, seller_name,
                seller_tax, seller_address,
                buyer_final_name,
                buyer_final_tax,
                buyer_final_address,
                inv.get("payment_method") or "Chuyển khoản",
                json.dumps(items, ensure_ascii=False),
                subtotal, vat_percent, vat_amount, total,
                "Đồng bộ từ SaveInvoice (Tổng cục Thuế)",
                target_cid,
                source_filename if xml_bytes else None,
                source_type if xml_bytes else None,
                Binary(xml_bytes) if xml_bytes else None,
            ), fetch=True, commit=True)

            if inserted:
                imported_count += 1
                details.append({
                    "id": inserted["id"],
                    "invoice_number": shdon,
                    "serial": serial,
                    "seller_name": seller_name,
                    "total": float(total),
                    "status": "imported",
                })
        except Exception as exc:
            logger.error("Error inserting invoice %s %s: %s", serial, shdon, exc)
            error_count += 1

    client.close()

    return {
        "ok": True,
        "imported": imported_count,
        "existing": existing_count,
        "errors": error_count,
        "details": details,
    }
