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
from decimal import Decimal, ROUND_HALF_UP
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import websockets
import xml.etree.ElementTree as ET

import httpx
from psycopg2 import Binary

from backend.services.database import db
from backend.services.settings_service import get_app_setting, set_many_app_settings
from frontend.modules.invoices.companies import ensure_company_by_tax_code
from frontend.modules.invoices.service import insert_synced_input_invoice

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

    # The refresh token expires too (about a week); log in again with the saved username/password.
    if login_saveinvoice():
        return True

    # Fallback: attempt to read latest tokens from active Chrome CDP session if available
    return _try_recover_session_via_cdp()


def login_saveinvoice(save: bool = True) -> bool:
    """Logs in with the saved username/password (as the SaveInvoice web app does) and stores the new session.

    Gives up when SaveInvoice asks for a Cloudflare Turnstile captcha, which only a person can solve.
    """
    cfg = get_saveinvoice_config()
    if not cfg["username"] or not cfg["password"]:
        logger.warning("SaveInvoice username/password not configured.")
        return False
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"{SAVEINVOICE_BASE}/",
        "Origin": SAVEINVOICE_BASE,
    }
    with httpx.Client(timeout=20.0) as client:
        status = client.get(f"{SAVEINVOICE_BASE}/api/users/auth-captcha-status",
                            params={"action": "login"}, headers=headers)
        if status.status_code == 200 and (status.json() or {}).get("requireCaptcha"):
            logger.warning("SaveInvoice login needs a captcha; log in once in the browser.")
            return False
        resp = client.post(f"{SAVEINVOICE_BASE}/api/users/login", headers=headers, json={
            "username": cfg["username"], "password": cfg["password"], "turnstileToken": "",
        })
        if resp.status_code != 200:
            logger.warning("SaveInvoice login returned %s: %s", resp.status_code, resp.text[:150])
            return False
        token = (resp.json() or {}).get("token") or {}
        if not token.get("access_token"):
            logger.warning("SaveInvoice login response had no access_token.")
            return False
        if save:
            updates = {"saveinvoice_access_token": token["access_token"]}
            if token.get("refresh_token"):
                updates["saveinvoice_refresh_token"] = token["refresh_token"]
            sid = client.cookies.get("connect.sid")
            if sid:
                updates["saveinvoice_connect_sid"] = sid
            set_many_app_settings(updates)
        logger.info("Logged in to SaveInvoice again with the saved account.")
        return True


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
    finally:
        client.close()


def _store_items(invoice_id: int, buyer_company_id: int, items: list) -> None:
    db._execute_query(
        """
        UPDATE public.transport_input_invoices
           SET items = %s::jsonb, updated_at = now()
         WHERE id = %s AND buyer_company_id = %s
        """,
        (json.dumps(items, ensure_ascii=False), invoice_id, buyer_company_id),
        commit=True,
    )


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
                 WHERE ltrim(invoice_number, '0') = ltrim(%s, '0') AND (serial = %s OR serial LIKE %s)
                   AND (seller_tax_code = %s OR seller_tax_code IS NULL)
                   {buyer_where}
                 LIMIT 1
            """
            params = [inv_num_clean, serial_clean, f"%{serial_clean}%", seller_tax_clean] + buyer_params
        else:
            q = f"""
                SELECT id FROM public.transport_input_invoices
                 WHERE ltrim(invoice_number, '0') = ltrim(%s, '0') AND (serial = %s OR serial LIKE %s)
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
                 WHERE ltrim(invoice_number, '0') = ltrim(%s, '0') AND invoice_date = %s
                   AND (seller_tax_code = %s OR seller_tax_code IS NULL)
                   {buyer_where}
                 LIMIT 1
            """
            params = [inv_num_clean, inv_date, seller_tax_clean] + buyer_params
        else:
            q = f"""
                SELECT id FROM public.transport_input_invoices
                 WHERE ltrim(invoice_number, '0') = ltrim(%s, '0') AND invoice_date = %s
                   {buyer_where}
                 LIMIT 1
            """
            params = [inv_num_clean, inv_date] + buyer_params
        row = db._execute_query(q, tuple(params), fetch=True, commit=False)
        if row:
            return True

    return False


def _dec(value: Any) -> Decimal:
    """Chuyển "1.000,5" / "1000.5" / 1000 / None -> Decimal; lỗi -> 0."""
    if value is None:
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal(0)
    s = str(value).strip()
    if not s:
        return Decimal(0)
    if re.fullmatch(r"-?\d+(\.\d+)?", s):  # số máy đọc trong XML/JSON: dấu chấm là thập phân ("1.000" = 1)
        return Decimal(s)
    try:
        if "." in s and "," in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            parts = s.split(",")
            if len(parts) > 2:
                s = s.replace(",", "")
            else:
                s = s.replace(",", ".")
        elif s.count(".") > 1:
            s = s.replace(".", "")
        return Decimal(s)
    except Exception:
        return Decimal(0)


def _num_str(value: Any) -> str:
    """Decimal -> chuỗi không mũ, bỏ ".0"/số 0 thừa sau dấu chấm (ví dụ "1", "327273", "1.5")."""
    d = _dec(value)
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s == "-0" or s == "":
        return "0"
    return s


def _item_dict(tchat: Any, name: Any, unit: Any, qty: Any, price: Any, amount: Any) -> dict | None:
    name_str = str(name or "").strip()
    if not name_str:
        return None

    unit_str = str(unit or "").strip()
    tchat_str = str(tchat or "").strip()

    dec_qty = _dec(qty)
    dec_price = _dec(price)
    dec_amount = _dec(amount)

    if tchat_str == "4" and dec_amount == 0:
        return None

    if dec_amount == 0 and dec_qty > 0 and dec_price > 0:
        dec_amount = (dec_qty * dec_price).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    if tchat_str == "3":
        dec_amount = -abs(dec_amount)
        base_price = dec_price if dec_price != 0 else dec_amount
        dec_price = -abs(base_price)
        dec_qty = dec_qty if dec_qty != 0 else Decimal(1)

    if dec_qty == 0:
        dec_qty = Decimal(1)

    if dec_price == 0:
        dec_price = dec_amount / dec_qty

    return {
        "description": name_str[:500],
        "unit": unit_str[:50],
        "quantity": _num_str(dec_qty),
        "unit_price": _num_str(dec_price),
        "amount": _num_str(dec_amount),
    }


def _items_from_saveinvoice_json(raw_items: Any) -> list[dict]:
    """từ detail.hdhhdvu, sắp theo stt"""
    if not raw_items or not isinstance(raw_items, list):
        return []

    def _stt_key(it: dict) -> int:
        try:
            return int(it.get("stt"))
        except (ValueError, TypeError):
            return 999999

    sorted_raw = sorted(raw_items, key=_stt_key)
    res: list[dict] = []
    for it in sorted_raw:
        if not isinstance(it, dict):
            continue
        d = _item_dict(
            tchat=it.get("tchat"),
            name=it.get("ten"),
            unit=it.get("dvtinh"),
            qty=it.get("sluong"),
            price=it.get("dgia"),
            amount=it.get("thtien"),
        )
        if d is not None:
            res.append(d)
    return res


def _items_from_xml(xml_bytes: bytes | None) -> tuple[list[dict], list[dict]]:
    """
    (items, fees). ElementTree, bỏ BOM, so khớp tên thẻ theo localname (bỏ namespace), duyệt mọi HHDVu.
    fees: mỗi LPhi -> {"description": TLPhi, "unit": "", "quantity": "1", "unit_price": TPhi, "amount": TPhi} (TPhi != 0)
    XML lỗi/zip -> ([], [])
    """
    if not xml_bytes:
        return ([], [])
    if isinstance(xml_bytes, memoryview):
        xml_bytes = bytes(xml_bytes)
    if xml_bytes.startswith(b"\xef\xbb\xbf"):
        xml_bytes = xml_bytes[3:]
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return ([], [])

    items: list[dict] = []
    fees: list[dict] = []

    raw_hhdvu = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()
        if tag == "hhdvu":
            raw_hhdvu.append(elem)
        elif tag == "lphi":
            cmap = {c.tag.split("}")[-1].lower(): (c.text or "").strip() for c in elem}
            tlphi = cmap.get("tlphi") or ""
            tphi = _dec(cmap.get("tphi"))
            if tphi != 0:
                fees.append({
                    "description": (tlphi or "Lệ phí")[:500],
                    "unit": "",
                    "quantity": "1",
                    "unit_price": _num_str(tphi),
                    "amount": _num_str(tphi),
                })

    def _stt_key(elem) -> int:
        for c in elem:
            if c.tag.split("}")[-1].lower() == "stt":
                try:
                    return int((c.text or "").strip())
                except (ValueError, TypeError):
                    pass
        return 999999

    raw_hhdvu.sort(key=_stt_key)

    for elem in raw_hhdvu:
        cmap = {c.tag.split("}")[-1].lower(): (c.text or "").strip() for c in elem}
        d = _item_dict(
            tchat=cmap.get("tchat"),
            name=cmap.get("thhdvu") or cmap.get("ten"),
            unit=cmap.get("dvtinh"),
            qty=cmap.get("sluong"),
            price=cmap.get("dgia"),
            amount=cmap.get("thtien"),
        )
        if d is not None:
            items.append(d)

    return (items, fees)


def _line_notes_from_xml(xml_bytes: bytes | None) -> list[str]:
    """Text of every "Ghi chú dòng" field of the invoice lines (an insurance invoice puts the vehicle plate there)."""
    if not xml_bytes:
        return []
    raw = bytes(xml_bytes)
    try:
        root = ET.fromstring(raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw)
    except Exception:
        return []
    notes = []
    for line in root.iter():
        if line.tag.split("}")[-1].lower() != "hhdvu":
            continue
        for field in line.iter():
            if field.tag.split("}")[-1].lower() != "ttin":
                continue
            cmap = {c.tag.split("}")[-1].lower(): (c.text or "").strip() for c in field}
            if "ghi chú dòng" in cmap.get("ttruong", "").lower() and cmap.get("dlieu"):
                notes.append(cmap["dlieu"])
    return notes


def _link_vehicle_from_line_notes(invoice_id: int, xml_bytes: bytes | None) -> None:
    """A plate in a line note says which vehicle the invoice is for: link it, so its vehicle expense is booked."""
    from frontend.modules.invoices import fuel_expenses
    notes = _line_notes_from_xml(xml_bytes)
    vehicle_id = fuel_expenses.guess_vehicle_id([], notes) if notes else None
    if vehicle_id:
        fuel_expenses.set_invoice_vehicle(invoice_id, vehicle_id)


def _fit_items(items: list[dict], fees: list[dict], subtotal: Any) -> list[dict] | None:
    """
    tổng amount của items khớp subtotal (lệch <= 1 đồng) -> items
    items + fees khớp -> items + fees
    còn lại / items rỗng -> None  (KHÔNG thay dòng nếu không khớp tiền)
    """
    if not items:
        return None
    sub_dec = _dec(subtotal)
    sum_items = sum(_dec(it.get("amount")) for it in items)
    if abs(sum_items - sub_dec) <= Decimal("1"):
        return items
    if fees:
        combined = list(items) + list(fees)
        sum_comb = sum(_dec(it.get("amount")) for it in combined)
        if abs(sum_comb - sub_dec) <= Decimal("1"):
            return combined
    return None


def _placeholder_items(serial: str, shdon: str, seller_name: str, subtotal: Any) -> list[dict]:
    """đúng dòng gộp cũ (dùng khi không lấy được)"""
    sub_str = _num_str(subtotal)
    return [{
        "description": f"Hóa đơn {serial} {shdon} - {seller_name}",
        "unit": "hóa đơn",
        "quantity": "1",
        "unit_price": sub_str,
        "amount": sub_str,
    }]


def _is_placeholder_items(items: Any) -> bool:
    """list 1 phần tử, unit == "hóa đơn", description bắt đầu "Hóa đơn """
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            return False
    if not isinstance(items, list) or len(items) != 1:
        return False
    first = items[0]
    if not isinstance(first, dict):
        return False
    unit = str(first.get("unit") or "").strip().lower()
    desc = str(first.get("description") or "").strip()
    return unit == "hóa đơn" and desc.startswith("Hóa đơn ")


def preview_saveinvoice_invoices(
    account_id: Optional[str] = None,
    month: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    buyer_company_id: Optional[int] = None,
    buyer_tax_code: Optional[str] = None,
    fetch_all: bool = False,
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
    url = f"{SAVEINVOICE_BASE}/api/invoices/account/{target_account_id}/type/purchase"

    def fetch_page(first: int, rows: int):
        nonlocal client, headers
        lazy_load = {"first": first, "rows": rows, "sortField": "data.tdlap", "sortOrder": -1, "filters": {}}
        response = client.get(url, headers=headers, params={"lazyLoadEvent": json.dumps(lazy_load)})
        if response.status_code == 401 and refresh_saveinvoice_tokens():
            client, headers = get_authenticated_client()
            response = client.get(url, headers=headers, params={"lazyLoadEvent": json.dumps(lazy_load)})
        return response

    try:
        resp = fetch_page(offset, 100 if (month or fetch_all) else limit)
        if resp.status_code != 200:
            return {"ok": False, "error": f"Lỗi từ SaveInvoice ({resp.status_code}): {resp.text[:150]}"}

        data = resp.json()
        total_records = data.get("totalRecords", 0)
        items = data.get("items", [])
        if month or fetch_all:
            # The portal lists newest first and cannot filter by month: read on until the list ends, so an older
            # month (e.g. May while the newest 100 are October and November) is not cut off.
            read = len(items)
            while items and read < total_records and read < 3000:
                resp = fetch_page(offset + read, 100)
                if resp.status_code != 200:
                    return {"ok": False, "error": f"Lỗi từ SaveInvoice ({resp.status_code}): {resp.text[:150]}"}
                more = resp.json().get("items", [])
                if not more:
                    break
                items.extend(more)
                read += len(more)

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
                r_item.get("dgia") or ""
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
                "seller_phone": str(detail.get("nbsdthoai") or "").strip(),
                "seller_email": str(detail.get("nbdctdtu") or "").strip(),
                "seller_bank_account": str(detail.get("nbstkhoan") or "").strip(),
                "seller_bank_name": str(detail.get("nbtnhang") or "").strip(),
                "buyer_tax_code": nmmst,
                "buyer_name": nmten,
                "buyer_address": nmdchi,
                "subtotal": subtotal,
                "vat_amount": vat_amount,
                "total": total,
                "items_count": len(raw_items),
                "items_summary": items_text,
                "line_items": _items_from_saveinvoice_json(raw_items),
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
            target_cid = ensure_company_by_tax_code(target_tax, target_name)

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
    if any(inv_id not in invoices_map for inv_id in invoice_ids):
        # A selected invoice older than the newest 200 (an older month): read the whole list for it
        full = preview_saveinvoice_invoices(
            account_id=actual_account_id, limit=200, buyer_company_id=target_cid,
            buyer_tax_code=target_tax, fetch_all=True)
        if full.get("ok"):
            invoices_map = {inv["id"]: inv for inv in full.get("invoices", [])}

    imported_count = 0
    existing_count = 0
    error_count = 0
    details = []

    client, headers = get_authenticated_client()

    for inv_id in invoice_ids:
        inv = invoices_map.get(inv_id)
        if not inv:
            # If not in preview map, fetch individual invoice
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
                    "seller_phone": str(detail.get("nbsdthoai") or "").strip(),
                    "seller_email": str(detail.get("nbdctdtu") or "").strip(),
                    "seller_bank_account": str(detail.get("nbstkhoan") or "").strip(),
                    "seller_bank_name": str(detail.get("nbtnhang") or "").strip(),
                    "buyer_tax_code": str(header.get("nmmst") or detail.get("nmmst") or target_tax).strip(),
                    "buyer_name": str(header.get("nmten") or detail.get("nmten") or target_name).strip(),
                    "buyer_address": str(detail.get("nmdchi") or target_address).strip(),
                    "subtotal": float(header.get("tgtcthue") or detail.get("tgtcthue") or 0),
                    "vat_amount": float(header.get("tgtthue") or detail.get("tgtthue") or 0),
                    "total": float(header.get("tgtttbso") or detail.get("tgtttbso") or 0),
                    "line_items": _items_from_saveinvoice_json(detail.get("hdhhdvu") or []),
                    "payment_method": detail.get("thtttoan") or "Chuyển khoản",
                }

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
            seller_company_id = ensure_company_by_tax_code(
                seller_tax, seller_name, seller_address,
                email=str(inv.get("seller_email") or "").strip(),
                phone=str(inv.get("seller_phone") or "").strip(),
                bank_account=str(inv.get("seller_bank_account") or "").strip(),
                bank_name=str(inv.get("seller_bank_name") or "").strip(),
            )

        # Build items
        subtotal = Decimal(str(inv.get("subtotal") or 0))
        vat_amount = Decimal(str(inv.get("vat_amount") or 0))
        total = Decimal(str(inv.get("total") or (subtotal + vat_amount)))
        vat_percent = (vat_amount * 100 / subtotal).quantize(Decimal("1")) if subtotal > 0 else Decimal("0")

        items = _fit_items(inv.get("line_items") or [], [], subtotal)
        if items is None and xml_bytes:
            x_items, x_fees = _items_from_xml(xml_bytes)
            items = _fit_items(x_items, x_fees, subtotal)
        if items is None:
            items = _placeholder_items(serial, shdon, seller_name, subtotal)

        buyer_final_name = inv.get("buyer_name") or target_name
        buyer_final_tax = inv.get("buyer_tax_code") or target_tax
        buyer_final_address = inv.get("buyer_address") or target_address

        # Insert into database strictly with resolved buyer company
        inserted_id = insert_synced_input_invoice(
            invoice_date=parsed_date, serial=serial, invoice_number=shdon, seller_company_id=seller_company_id,
            seller_name=seller_name, seller_tax_code=seller_tax, seller_address=seller_address,
            buyer_name=buyer_final_name, buyer_tax_code=buyer_final_tax, buyer_address=buyer_final_address,
            payment_method=inv.get("payment_method") or "Chuyển khoản", items=items,
            subtotal=subtotal, vat_percent=vat_percent, vat_amount=vat_amount, total=total,
            note="Đồng bộ từ SaveInvoice (Tổng cục Thuế)", buyer_company_id=target_cid,
            source_filename=source_filename if xml_bytes else None,
            source_type=source_type if xml_bytes else None, source_data=xml_bytes)

        if inserted_id:
            imported_count += 1
            _link_vehicle_from_line_notes(inserted_id, xml_bytes)
            details.append({
                "id": inserted_id,
                "invoice_number": shdon,
                "serial": serial,
                "seller_name": seller_name,
                "total": float(total),
                "status": "imported",
            })

    client.close()

    return {
        "ok": True,
        "imported": imported_count,
        "existing": existing_count,
        "errors": error_count,
        "details": details,
    }


def repair_saveinvoice_items(buyer_company_id: int) -> dict:
    """
    Sửa các hoá đơn đã lấy về trước đây: lấy lại dòng hàng hoá, dịch vụ từ SaveInvoice
    thay vì dòng gộp 'Hóa đơn <ký hiệu> <số> - <người bán>'.
    """
    candidate_rows = db._execute_query(
        """
        SELECT id, serial, invoice_number, seller_tax_code, subtotal, source_data, items
          FROM public.transport_input_invoices
         WHERE buyer_company_id = %s
           AND jsonb_typeof(items) = 'array'
           AND jsonb_array_length(items) = 1
           AND items->0->>'unit' = 'hóa đơn'
         ORDER BY id
        """,
        (buyer_company_id,),
        fetchall=True,
        commit=False,
    ) or []

    rows = [r for r in candidate_rows if _is_placeholder_items(r.get("items"))]
    total_checked = len(rows)
    if total_checked == 0:
        return {
            "ok": True,
            "checked": 0,
            "updated": 0,
            "not_found": [],
            "mismatch": [],
            "error": "",
        }

    updated_count = 0
    mismatch_numbers: list[str] = []
    not_found_numbers: list[str] = []
    remaining_rows: list[dict] = []

    # Bước 1 (không cần mạng): có source_data -> _items_from_xml -> _fit_items -> nếu được thì UPDATE
    for r in rows:
        src = r.get("source_data")
        fit = None
        if src:
            raw_bytes = bytes(src) if isinstance(src, (bytes, memoryview)) else src
            x_items, x_fees = _items_from_xml(raw_bytes)
            fit = _fit_items(x_items, x_fees, r.get("subtotal"))

        if fit is not None:
            _store_items(r["id"], buyer_company_id, fit)
            updated_count += 1
        else:
            remaining_rows.append(r)

    # Bước 2: các hoá đơn còn lại -> tải danh sách SaveInvoice theo trang
    step2_error = ""
    if remaining_rows:
        c_row = db._execute_query(
            "SELECT tax_code FROM public.invoice_companies WHERE id = %s LIMIT 1",
            (buyer_company_id,),
            fetch=True,
            commit=False,
        )
        buyer_tax_code = c_row.get("tax_code") if c_row else None

        def _norm(s):
            return re.sub(r"\s+", "", str(s or "")).upper()

        def _norm_no(s):
            s_clean = re.sub(r"\s+", "", str(s or "")).upper()
            if s_clean.isdigit():
                return str(int(s_clean))
            return s_clean

        remaining_by_key = {}
        for r in remaining_rows:
            k = (_norm(r.get("serial")), _norm_no(r.get("invoice_number")), _norm(r.get("seller_tax_code")))
            remaining_by_key.setdefault(k, []).append(r)

        offset = 0
        limit = 200
        page = 0
        total_records = 999999

        while offset < total_records and page < 30 and remaining_by_key:
            preview = preview_saveinvoice_invoices(
                limit=limit,
                offset=offset,
                buyer_company_id=buyer_company_id,
                buyer_tax_code=buyer_tax_code,
            )
            if not preview.get("ok"):
                step2_error = preview.get("error") or "Lỗi kết nối SaveInvoice"
                break

            inv_list = preview.get("invoices") or []
            if not inv_list:
                break
            total_records = preview.get("total_records", 0)

            for inv in inv_list:
                inv_key = (_norm(inv.get("serial")), _norm_no(inv.get("invoice_number")), _norm(inv.get("seller_tax_code")))
                if inv_key in remaining_by_key:
                    matched_rows = remaining_by_key[inv_key]
                    for r in list(matched_rows):
                        subtotal = r.get("subtotal")
                        line_items = inv.get("line_items") or []
                        fit = _fit_items(line_items, [], subtotal)
                        dl_xml = None
                        if fit is None:
                            inv_id = inv.get("id")
                            if inv_id:
                                dl_xml = download_saveinvoice_xml(inv_id)
                                if dl_xml:
                                    x_items, x_fees = _items_from_xml(dl_xml)
                                    fit = _fit_items(x_items, x_fees, subtotal)

                        if fit is not None:
                            if dl_xml and not r.get("source_data"):
                                db._execute_query(
                                    """
                                    UPDATE public.transport_input_invoices
                                       SET items = %s::jsonb,
                                           source_data = %s,
                                           source_filename = %s,
                                           source_type = %s,
                                           updated_at = now()
                                     WHERE id = %s AND buyer_company_id = %s
                                    """,
                                    (
                                        json.dumps(fit, ensure_ascii=False),
                                        Binary(dl_xml),
                                        f"{r.get('serial') or ''}_{r.get('invoice_number') or ''}.xml",
                                        "application/xml",
                                        r["id"],
                                        buyer_company_id,
                                    ),
                                    commit=True,
                                )
                            else:
                                _store_items(r["id"], buyer_company_id, fit)
                            updated_count += 1
                            matched_rows.remove(r)
                        else:
                            mismatch_numbers.append(str(r.get("invoice_number") or ""))
                            matched_rows.remove(r)

                    if not matched_rows:
                        del remaining_by_key[inv_key]

            offset += limit
            page += 1

        for rows_left in remaining_by_key.values():
            for r in rows_left:
                if r.get("source_data"):
                    mismatch_numbers.append(str(r.get("invoice_number") or ""))
                else:
                    not_found_numbers.append(str(r.get("invoice_number") or ""))

    return {
        "ok": True,
        "checked": total_checked,
        "updated": updated_count,
        "not_found": not_found_numbers,
        "mismatch": mismatch_numbers,
        "error": step2_error,
    }

