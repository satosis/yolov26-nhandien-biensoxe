"""Build a reviewable cargo-trip draft from selected Zalo group messages."""

from __future__ import annotations

import re
import unicodedata
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from psycopg2.extras import Json

from backend.services.database import db
from frontend.modules.zalo.inbox import INBOX_DIR, bridge_secret


_TAX = re.compile(r"(?:\bmst\b|mã\s+số\s+thuế)\s*[:：]?\s*(\d{10}(?:-\d{3})?)", re.I)
_MILLION = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:triệu|tr\b)", re.I)
_ROUTE = re.compile(r"\b(?:từ|tu)\s+(.+?)\s+(?:đến|den|đi|di|tới|toi|sang|về|ve)\s+(.+?)(?:[.!?]|$)", re.I)
_INVOICE_DONE = re.compile(r"(?:đã|rồi|\br\b).*?(?:tạo|xuất).*?hóa\s*đơn|(?:tạo|xuất).*?hóa\s*đơn.*?(?:rồi|\br\b)", re.I)
_MAP_URL = re.compile(r"https://(?:www\.)?(?:google\.com/maps|maps\.app\.goo\.gl|goo\.gl/maps)(?:/|\?)[^\s<>]+", re.I)


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFD", value.casefold())
    return re.sub(r"\s+", " ", "".join(c for c in value if unicodedata.category(c) != "Mn").replace("đ", "d")).strip()


def _province(value: str, provinces: list[str]) -> str:
    folded = _fold(value)
    matches = [name for name in provinces if _fold(name) in folded
               or _fold(name).removeprefix("tinh ").removeprefix("thanh pho ") in folded]
    return max(matches, key=len, default="")


def build_trip_draft(messages: list[dict], *, provinces: list[str],
                     services: list[str], cargo_types: list[str]) -> dict:
    if not messages or len(messages) > 20:
        raise ValueError("Chọn từ 1 đến 20 tin nhắn cho một chuyến hàng.")
    groups = {str(item.get("group_id") or "") for item in messages}
    if len(groups) != 1 or not next(iter(groups)):
        raise ValueError("Các tin đã chọn phải thuộc cùng một nhóm Zalo.")
    messages = sorted(messages, key=lambda item: item.get("sent_at") or item.get("received_at") or "")
    texts = [str(item.get("text") or "").strip() for item in messages]
    all_texts = list(texts)
    for item in messages:
        for line in item.get("extracted_lines") or []:
            line_str = str(line.get("text") or "").strip()
            if line_str:
                all_texts.append(line_str)

    tax_codes = list(dict.fromkeys(code for text in all_texts for code in _TAX.findall(text)))
    if not tax_codes:
        tax_codes = list(dict.fromkeys(
            code for text in all_texts for code in re.findall(r"\b\d{10}(?:-\d{3})?\b", text)
        ))

    prices = []
    for text in texts:
        folded = _fold(text)
        if any(w in folded for w in ("cuoc", "gia", "vat", "van chuyen", "cau", "chuyen", "xe", "tr", "trieu")):
            prices.extend(int(float(match.replace(",", ".")) * 1_000_000) for match in _MILLION.findall(text))
    routes = [_ROUTE.search(text) for text in texts]
    route = next((match for match in routes if match), None)
    origin_province = _province(route.group(1), provinces) if route else ""
    destination_province = _province(route.group(2), provinces) if route else ""
    missing = []
    map_locations = []
    _coord_re = re.compile(r"(?:tọa\s*độ\s*[:：]?\s*\(?|\b)([0-9]{1,2}\.[0-9]{4,8})[\s,]+([0-9]{2,3}\.[0-9]{4,8})\)?", re.I)
    from frontend.modules.zalo.invoice_automation import resolve_coordinates_address

    for item, text in zip(messages, texts):
        shared_url = _MAP_URL.search(text)
        attachment_url = str(item.get("attachment_url") or "").strip()
        url = attachment_url if _MAP_URL.fullmatch(attachment_url) else (shared_url.group(0) if shared_url else "")
        coord_m = _coord_re.search(text)
        resolved_address = ""
        coord_vals = None
        if coord_m:
            try:
                lat, lon = float(coord_m.group(1)), float(coord_m.group(2))
                coord_vals = [lat, lon]
                resolved_address = resolve_coordinates_address(lat, lon)
            except Exception:
                pass
            if not url:
                url = f"https://maps.google.com/?q={coord_m.group(1)},{coord_m.group(2)}"
        if item.get("attachment_type") == "chat.location.new" or url or coord_m:
            label = text.replace(url, "").strip(" -:") if shared_url else text
            if resolved_address:
                label = resolved_address
            elif not label and coord_m:
                label = f"Tọa độ ({coord_m.group(1)}, {coord_m.group(2)})"
            if label or url:
                map_locations.append({
                    "label": label,
                    "address": resolved_address or label,
                    "url": url,
                    "coordinates": coord_vals,
                    "message_id": item["id"],
                })
    map_places = [item.get("address") or item.get("label") or item.get("url") for item in map_locations]
    origin = map_places[0] if len(map_places) >= 2 else (origin_province or (route.group(1).strip() if route else ""))
    destination = map_places[-1] if len(map_places) >= 2 else (destination_province or (route.group(2).strip() if route else ""))

    # Resolve "công ty này" if company address is present in texts
    if origin and any(phrase in _fold(origin) for phrase in ("cong ty nay", "cty nay", "doanh nghiep nay")):
        company_addr_candidate = ""
        for t in texts:
            if any(k in _fold(t) for k in ("phu tho", "vinh phuc", "ha noi", "khu", "phuong", "xa", "huyen", "tinh")):
                # Extract address portion after company name or comma
                sub = re.sub(r"(?i)^công ty\s+[^,]+,\s*", "", t)
                sub = re.sub(r"(?i)\s*mã\s+số\s+thuế.*$", "", sub).strip(" .,-")
                if len(sub) > 10:
                    company_addr_candidate = sub
                    break
        origin = company_addr_candidate or "Công ty khách hàng"
    if destination and any(phrase in _fold(destination) for phrase in ("dia diem tren", "noi tren", "diem tren")):
        clean_dest = re.sub(r"(?i)\s*\d+.*(?:triệu|tr|chưa\s*vat).*$", "", destination).strip(" .,-")
        if map_places:
            destination = map_places[0]
        elif clean_dest:
            destination = clean_dest

    if len(map_places) == 1:
        place = map_places[0]
        location_index = next(index for index, item in enumerate(messages)
                              if item["id"] == map_locations[0]["message_id"])
        nearby = _fold(" ".join(texts[max(0, location_index - 1):location_index + 3]))
        if "diem giao" in nearby or "cho tra" in nearby or "tra hang" in nearby or "den" in nearby or "toi" in nearby:
            destination = place
        elif "diem lay" in nearby or "cho xuong lay" in nearby or "lay hang" in nearby or "tu" in nearby:
            origin = place
        elif destination_province and _fold(destination_province).split()[-1] in _fold(place):
            destination = place
        elif origin_province and _fold(origin_province).split()[-1] in _fold(place):
            origin = place
        elif not destination:
            destination = place

    if not destination_province and destination:
        destination_province = _province(destination, provinces)
    if not origin_province and origin:
        origin_province = _province(origin, provinces)

    cargo = next((name for name in sorted(cargo_types, key=len, reverse=True)
                  if any(_fold(name) in _fold(text) for text in texts)), "")
    transport_requested = any("van chuyen" in _fold(text) or "cuoc" in _fold(text) for text in texts)
    service = next((name for name in services if transport_requested and "van chuyen" in _fold(name) and
                    ("cau" in _fold(name) or not any("cau" in _fold(s) for s in services))), "")

    vehicle_nickname = ""
    for t in texts:
        m = re.search(r"\bxe\s+(chú|bác|anh|em|ông|bố|mẹ)(?:\s+([A-Za-z0-9_À-ỹ]+))?", t, re.I)
        if m:
            title = m.group(1).strip()
            name = (m.group(2) or "").strip()
            if name.lower() in {"xuat", "chua", "roi", "di", "ve", "nay", "do", "can", "chuyen", "vat"}:
                name = ""
            vehicle_nickname = f"{title} {name}".strip() if name else title
            break
    if not vehicle_nickname:
        plate_match = re.search(r"\b(\d{2}[A-Z]-?\d{3}\.?\d{2})\b", " ".join(texts), re.I)
        if plate_match:
            vehicle_nickname = plate_match.group(1).upper()

    sent_dates = [item.get("sent_at") for item in messages if item.get("sent_at")]
    invoice_date = ""
    if sent_dates:
        try:
            invoice_date = datetime.fromisoformat(max(sent_dates).replace("Z", "+00:00")).astimezone(
                ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()
        except ValueError:
            pass
    if not tax_codes:
        missing.append("Mã số thuế bên mua")
    elif len(tax_codes) > 1:
        missing.append("Xác nhận mã số thuế bên mua (có nhiều MST)")
    if not prices:
        missing.append("Cước trước VAT")
    if not vehicle_nickname:
        missing.append("Xe vận chuyển")
    if not cargo:
        missing.append("Loại hàng hóa")
    if not service:
        missing.append("Dịch vụ")
    if not origin:
        missing.append("Điểm lấy hàng")
    if not destination:
        missing.append("Điểm giao hàng")
    source_note = next((text.split(":", 1)[1].strip() for text in texts
                 if "nội dung hóa đơn:" in text.casefold()), "")
    note = source_note
    if route and service:
        note = (service + (" " + cargo.casefold() if cargo else "") + " từ "
                + (origin_province or route.group(1).strip()) + " đến "
                + (destination_province or route.group(2).strip()))

    seller_company_id = messages[0].get("company_id")
    seller_company_name = messages[0].get("company_name")

    buyer_company_id = None
    buyer_company_name = ""
    if tax_codes:
        try:
            matched_tax = db._execute_query(
                "SELECT id, name, address FROM public.invoice_companies WHERE tax_code = %s",
                (tax_codes[0],),
                fetch=True,
            )
            if matched_tax:
                buyer_company_id = matched_tax["id"]
                buyer_company_name = matched_tax["name"]
                if not origin or any(phrase in _fold(origin) for phrase in ("cong ty nay", "cty nay", "khach hang")):
                    origin = matched_tax.get("address") or origin
        except Exception:
            pass

    if not buyer_company_id:
        buyer_company_id = seller_company_id
        buyer_company_name = seller_company_name

    return {
        "group_id": next(iter(groups)), "message_ids": [item["id"] for item in messages],
        "company_id": buyer_company_id, "company_name": buyer_company_name or "",
        "buyer_company_id": buyer_company_id, "buyer_company_name": buyer_company_name or "",
        "seller_company_id": seller_company_id, "seller_company_name": seller_company_name or "",
        "tax_code": tax_codes[0] if len(tax_codes) == 1 else "",
        "amount": prices[0] if prices else 0,
        "prices": prices,
        "vehicle_nickname": vehicle_nickname,
        "service": service, "cargo": cargo, "note": note,
        "origin": origin, "destination": destination,
        "origin_province": origin_province, "destination_province": destination_province,
        "invoice_date": invoice_date, "vat_percent": 8,
        "map_places": map_places, "map_locations": map_locations,
        "source_note": source_note, "missing": missing,
        "possible_existing_invoice": any(_INVOICE_DONE.search(text) for text in texts),
    }




def register_trip_request(trip_id: int, draft: dict) -> None:
    db._execute_query("""
        INSERT INTO public.zalo_trip_requests
            (trip_id, group_id, message_ids, map_locations, possible_existing_invoice)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (trip_id) DO NOTHING
    """, (trip_id, draft["group_id"], Json(draft["message_ids"]),
          Json(draft.get("map_locations") or []),
          draft.get("possible_existing_invoice", False)), commit=True)


def get_trip_request(trip_id: int) -> dict | None:
    return db._execute_query(
        "SELECT * FROM public.zalo_trip_requests WHERE trip_id = %s",
        (trip_id,), fetch=True, commit=False,
    )


def confirm_invoice_not_created(trip_id: int) -> bool:
    return bool(db._execute_query("""
        UPDATE public.zalo_trip_requests
        SET possible_existing_invoice = false
        WHERE trip_id = %s AND possible_existing_invoice = true
        RETURNING trip_id
    """, (trip_id,), fetch=True, commit=True))


def ensure_zalo_bridge(timeout_secs: float = 6.0) -> int | None:
    """Ensure the Zalo bridge node process is running. If stopped and session.json exists, auto-start it."""
    import psutil
    import subprocess
    import os
    import time
    from pathlib import Path

    bridge_dir = Path(__file__).resolve().parents[3] / "scripts" / "zalo_bridge"
    pid_file = INBOX_DIR / "bridge.pid"
    port_file = INBOX_DIR / "bridge-port"
    session_file = INBOX_DIR / "session.json"

    def is_running():
        if not pid_file.exists():
            return False
        try:
            p = psutil.Process(int(pid_file.read_text(encoding="ascii").strip()))
            return p.is_running() and p.cwd() == str(bridge_dir) and "bridge.js" in p.cmdline()
        except Exception:
            return False

    status_file = INBOX_DIR / "bridge-status.json"
    status_connected = False
    if status_file.exists():
        try:
            status_data = json.loads(status_file.read_text(encoding="utf-8"))
            status_connected = status_data.get("state") == "connected"
        except Exception:
            pass

    if (is_running() or status_connected) and port_file.exists():
        try:
            port = int(port_file.read_text(encoding="ascii").strip())
            if 1 <= port <= 65535:
                return port
        except Exception:
            pass

    if not session_file.exists():
        return None

    port_file.unlink(missing_ok=True)
    logfile = (INBOX_DIR / "bridge.log").open("ab")
    try:
        process = subprocess.Popen(
            ["node", "bridge.js"], cwd=bridge_dir,
            stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT,
            creationflags=(subprocess.CREATE_NO_WINDOW | 0x00000008) if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        pid_file.write_text(str(process.pid), encoding="ascii")
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Failed to auto-start Zalo bridge: %s", exc)
        return None
    finally:
        logfile.close()

    start_t = time.time()
    while time.time() - start_t < timeout_secs:
        if port_file.exists():
            try:
                port = int(port_file.read_text(encoding="ascii").strip())
                if 1 <= port <= 65535:
                    return port
            except Exception:
                pass
        time.sleep(0.3)
    return None


def _set_notice_state(trip_id: int, state: str, error: str = "") -> None:
    db._execute_query("""
        UPDATE public.zalo_trip_requests SET status = %s, last_error = %s,
            notified_at = CASE WHEN %s = 'sent' THEN now() ELSE notified_at END
        WHERE trip_id = %s
    """, (state, error[:500], state, trip_id), commit=True)


async def send_invoice_notice(trip_id: int, *, trip: dict, buyer: dict,
                              invoice: dict, retry_uncertain: bool = False,
                              image_path: str = "") -> tuple[str, str]:
    """Send notice and invoice image to Zalo group after BKAV confirms."""
    allowed = ["pending_invoice", "send_failed"]
    if retry_uncertain:
        allowed.append("send_uncertain")
    request_row = db._execute_query("""
        UPDATE public.zalo_trip_requests SET status = 'sending', last_error = ''
        WHERE trip_id = %s AND status = ANY(%s)
          AND possible_existing_invoice = false
        RETURNING group_id
    """, (trip_id, allowed), fetch=True, commit=True)
    if not request_row:
        return "skipped", "Thông báo đã gửi, đang gửi, hoặc hóa đơn cần đối chiếu trước khi gửi."

    port = ensure_zalo_bridge()
    if not port:
        _set_notice_state(trip_id, "send_failed", "Kết nối Zalo chưa sẵn sàng.")
        return "send_failed", "Đã tạo hóa đơn; kết nối Zalo chưa sẵn sàng."

    try:
        amount = f"{trip['freight_revenue']:,.0f}".replace(",", ".")
        invoice_no = invoice.get("invoice_no") or trip.get("document_number") or ""
        caption = (
            f"🧾 Đã tạo hóa đơn nháp trên BKAV cho xe {trip['plate_number']}.\n"
            f"• Khách hàng: {buyer['name']}\n"
            f"• Cước trước VAT: {amount} đ\n"
            f"• Tuyến: {trip.get('origin') or ''} → {trip.get('destination') or ''}\n"
            f"• Số hóa đơn: {invoice_no or 'chưa cấp'}"
        )
        seller = get_company(trip.get("seller_company_id")) if trip.get("seller_company_id") else None
        if seller and seller.get("bank_account") and seller.get("bank_name"):
            caption += (
                f"\n\n💳 THÔNG TIN CHUYỂN KHOẢN:\n"
                f"• Ngân hàng: {seller['bank_name']}\n"
                f"• Số tài khoản: {seller['bank_account']}\n"
                f"• Chủ tài khoản: {seller.get('name') or ''}"
            )

        post_data = {
            "group_id": request_row["group_id"],
            "company_id": trip.get("seller_company_id"),
            "text": caption,
        }
        if image_path and Path(image_path).exists():
            post_data["image_path"] = str(Path(image_path).resolve())

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"http://127.0.0.1:{port}/send-invoice-notice",
                headers={"X-Zalo-Bridge-Secret": bridge_secret()},
                json=post_data,
            )
        if response.status_code == 200 and response.json().get("ok") is True:
            _set_notice_state(trip_id, "sent")
            return "sent", "Đã gửi thông báo hóa đơn vào nhóm Zalo."
        if response.status_code in (400, 404):
            _set_notice_state(trip_id, "send_failed", "Cầu nối Zalo từ chối tin hoặc chưa nạp mã mới.")
            return "send_failed", "Hóa đơn đã tạo; cầu nối Zalo từ chối tin hoặc chưa nạp mã mới."
        _set_notice_state(trip_id, "send_uncertain", "Chưa xác nhận Zalo đã nhận tin; kiểm tra nhóm trước khi gửi lại.")
        return "send_uncertain", "Hóa đơn đã tạo; chưa xác nhận thông báo Zalo đã gửi."
    except (OSError, ValueError, json.JSONDecodeError, httpx.HTTPError) as err:
        _set_notice_state(trip_id, "send_uncertain", f"Chưa xác nhận Zalo đã nhận tin ({err}).")
        return "send_uncertain", "Hóa đơn đã tạo; chưa xác nhận thông báo Zalo đã gửi."


async def send_saved_invoice_to_zalo(
    invoice_guid: str,
    image_path: str = "",
) -> tuple[bool, str]:
    """Send invoice image, payment QR image and bank transfer info to the Zalo group."""
    import asyncio
    import logging
    import urllib.parse
    from pathlib import Path
    import fitz
    from frontend.modules.invoices.companies import get_company
    from frontend.modules.bank_transfer import BANK_CODE_MAP, remove_accents as _remove_accents
    from backend.services import config as cfg
    logger = logging.getLogger(__name__)

    guid_str = str(invoice_guid)
    invoice = db._execute_query("""
        SELECT invoice.*, buyer.name AS buyer_name, seller.name AS seller_name
        FROM public.bkav_invoices invoice
        LEFT JOIN public.invoice_companies buyer ON buyer.id = invoice.buyer_company_id
        LEFT JOIN public.invoice_companies seller ON seller.id = invoice.seller_company_id
        WHERE invoice.invoice_guid = %s
    """, (guid_str,), fetch=True, commit=False)

    if not invoice:
        return False, "Không tìm thấy thông tin hóa đơn."

    # 1. Ảnh hóa đơn BKAV
    valid_image_path = ""
    if image_path and Path(image_path).exists() and Path(image_path).is_file():
        valid_image_path = str(Path(image_path).resolve())
    else:
        try:
            from frontend.modules.invoices.bkav_sync import get_or_fetch_bkav_invoice_files
            png_path, _ = await asyncio.to_thread(get_or_fetch_bkav_invoice_files, guid_str)
            if png_path and png_path.exists():
                valid_image_path = str(png_path.resolve())
        except Exception as e:
            logger.warning("Could not fetch invoice image for Zalo notice: %s", e)

    # 2. Ảnh QR chuyển khoản thanh toán & Thông tin chuyển khoản
    valid_qr_path = ""
    transfer_text = ""
    seller = get_company(invoice.get("seller_company_id"))
    seller_name = seller.get("name") if seller else (invoice.get("seller_name") or "")

    if seller and seller.get("bank_account") and seller.get("bank_name"):
        bank_name = str(seller["bank_name"]).strip()
        account_no = str(seller["bank_account"]).strip()
        account_holder = str(seller.get("name") or "").strip()
        amount = (int(round(float(invoice["debt_amount"])))
                  if invoice.get("payment_status") == "debt" and invoice.get("debt_amount")
                  else int(round(float(invoice.get("gross") or 0))))
        num = int(invoice["invoice_number"]) if invoice.get("invoice_number") else 0
        info = f"HD {num:08d}" if num else (invoice.get("invoice_serial") or "Thanh toan hoa don")
        amount_str = f"{amount:,.0f}".replace(",", ".") + " đ" if amount > 0 else "Theo hóa đơn"

        transfer_text = (
            f"💳 THÔNG TIN CHUYỂN KHOẢN:\n"
            f"• Ngân hàng: {bank_name}\n"
            f"• Số tài khoản: {account_no}\n"
            f"• Chủ tài khoản: {account_holder}\n"
            f"• Số tiền: {amount_str}\n"
            f"• Nội dung CK: {info}"
        )

        try:
            bank_code = BANK_CODE_MAP.get(bank_name.upper(), bank_name.upper())
            params = {
                "amount": str(amount),
                "addInfo": info,
                "accountName": _remove_accents(account_holder).upper(),
            }
            qr_url = f"https://img.vietqr.io/image/{bank_code}-{account_no}-compact2.png?{urllib.parse.urlencode(params)}"
            qr_file = Path(cfg.BASE_DIR) / "upload" / "bkav_invoices" / f"{guid_str}_qr.png"

            if not qr_file.exists():
                qr_file.parent.mkdir(parents=True, exist_ok=True)
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    qr_resp = await client.get(qr_url)
                if qr_resp.status_code == 200:
                    if b"<svg" in qr_resp.content[:100]:
                        doc = fitz.open(stream=qr_resp.content, filetype="svg")
                        pix = doc[0].get_pixmap(dpi=150)
                        qr_file.write_bytes(pix.tobytes("png"))
                    else:
                        qr_file.write_bytes(qr_resp.content)
            if qr_file.exists():
                valid_qr_path = str(qr_file.resolve())
        except Exception as qr_err:
            logger.warning("Could not generate QR image for Zalo notice: %s", qr_err)

    images_to_send = [p for p in (valid_image_path, valid_qr_path) if p]
    if not images_to_send:
        return False, "Không tìm thấy ảnh hóa đơn hoặc ảnh QR để gửi."

    port = ensure_zalo_bridge()
    if not port:
        return False, "Cầu nối Zalo chưa sẵn sàng. Hãy kiểm tra kết nối tài khoản Zalo."

    secret = bridge_secret()
    payload = {
        "text": transfer_text,
        "images": images_to_send,
        "company_id": invoice.get("seller_company_id"),
        "company_name": seller_name,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"http://127.0.0.1:{port}/send-invoice-notice",
                headers={"X-Zalo-Bridge-Secret": secret},
                json=payload,
            )
        if resp.status_code == 200 and resp.json().get("ok") is True:
            cname = seller_name or "công ty"
            return True, f"Đã gửi ảnh hóa đơn, mã QR và thông tin chuyển khoản vào nhóm Zalo {cname} thành công!"
        err = resp.json().get("error") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        return False, f"Cầu nối Zalo phản hồi lỗi: {err or resp.status_code}"
    except Exception as exc:
        logger.exception("Error sending invoice to Zalo: %s", exc)
        return False, f"Lỗi kết nối tới cầu nối Zalo: {exc}"

