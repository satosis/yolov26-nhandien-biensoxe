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
_ROUTE = re.compile(r"\btừ\s+(.+?)\s+(?:đến|đi|tới)\s+(.+?)(?:[.!?]|$)", re.I)
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
    tax_codes = list(dict.fromkeys(code for text in texts for code in _TAX.findall(text)))
    prices = []
    for text in texts:
        if "cuoc" in _fold(text):
            prices.extend(int(float(match.replace(",", ".")) * 1_000_000) for match in _MILLION.findall(text))
    routes = [_ROUTE.search(text) for text in texts]
    route = next((match for match in routes if match), None)
    origin_province = _province(route.group(1), provinces) if route else ""
    destination_province = _province(route.group(2), provinces) if route else ""
    missing = []
    map_locations = []
    for item, text in zip(messages, texts):
        shared_url = _MAP_URL.search(text)
        attachment_url = str(item.get("attachment_url") or "").strip()
        url = attachment_url if _MAP_URL.fullmatch(attachment_url) else (shared_url.group(0) if shared_url else "")
        if item.get("attachment_type") == "chat.location.new" or url:
            label = text.replace(url, "").strip(" -:") if shared_url else text
            if label or url:
                map_locations.append({"label": label, "url": url, "message_id": item["id"]})
    map_places = [item["url"] or item["label"] for item in map_locations]
    origin = map_places[0] if len(map_places) >= 2 else origin_province
    destination = map_places[-1] if len(map_places) >= 2 else destination_province
    if len(map_places) == 1:
        place = map_places[0]
        location_index = next(index for index, item in enumerate(messages)
                              if item["id"] == map_locations[0]["message_id"])
        nearby = _fold(" ".join(texts[max(0, location_index - 1):location_index + 3]))
        if "diem giao" in nearby or "cho tra" in nearby or "tra hang" in nearby:
            destination = place
        elif "diem lay" in nearby or "cho xuong lay" in nearby or "lay hang" in nearby:
            origin = place
        elif destination_province and _fold(destination_province).split()[-1] in _fold(place):
            destination = place
        elif origin_province and _fold(origin_province).split()[-1] in _fold(place):
            origin = place
        else:
            missing.append("Xác định vị trí Maps là điểm lấy hay điểm giao")
    cargo = next((name for name in sorted(cargo_types, key=len, reverse=True)
                  if any(_fold(name) in _fold(text) for text in texts)), "")
    transport_requested = any("van chuyen" in _fold(text) or "cuoc" in _fold(text) for text in texts)
    service = next((name for name in services if transport_requested and "van chuyen" in _fold(name) and
                    ("cau" in _fold(name) or not any("cau" in _fold(s) for s in services))), "")
    driver_reference = next((text for text in texts if re.search(r"\bxe\s+chú\s+\w+", text, re.I)), "")
    nickname = re.search(r"\bxe\s+chú\s+(\w+)", driver_reference, re.I)
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
    elif len(set(prices)) > 1:
        missing.append("Xác nhận cước trước VAT (có nhiều mức)")
    if not nickname:
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
    return {
        "group_id": next(iter(groups)), "message_ids": [item["id"] for item in messages],
        "tax_code": tax_codes[0] if len(tax_codes) == 1 else "",
        "amount": prices[0] if len(set(prices)) == 1 and prices else 0,
        "vehicle_nickname": nickname.group(1) if nickname else "",
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

    if is_running() and port_file.exists():
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
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
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
        post_data = {"group_id": request_row["group_id"], "text": caption}
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
            return "sent", "Đã gửi thông báo hóa đơn vào nhóm Zalo Đại Lộ Xanh."
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
    """Send ONLY the invoice image and payment QR image to the 'Đại Lộ Xanh' Zalo group with NO text."""
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

    # 2. Ảnh QR chuyển khoản thanh toán
    valid_qr_path = ""
    try:
        seller = get_company(invoice.get("seller_company_id"))
        if seller and seller.get("bank_account") and seller.get("bank_name"):
            bank_name = str(seller["bank_name"]).strip().upper()
            bank_code = BANK_CODE_MAP.get(bank_name, bank_name)
            account_no = str(seller["bank_account"]).strip()
            amount = (int(round(float(invoice["debt_amount"])))
                      if invoice.get("payment_status") == "debt" and invoice.get("debt_amount")
                      else int(round(float(invoice.get("gross") or 0))))
            num = int(invoice["invoice_number"]) if invoice.get("invoice_number") else 0
            info = f"HD {num:08d}"
            params = {
                "amount": str(amount),
                "addInfo": info,
                "accountName": _remove_accents(str(seller["name"])).upper(),
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
        "text": "",  # KHÔNG THÊM TEXT THÔNG BÁO, CHỈ GỬI ẢNH
        "images": images_to_send,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"http://127.0.0.1:{port}/send-invoice-notice",
                headers={"X-Zalo-Bridge-Secret": secret},
                json=payload,
            )
        if resp.status_code == 200 and resp.json().get("ok") is True:
            return True, "Đã gửi ảnh hóa đơn và mã QR vào nhóm Zalo Đại Lộ Xanh thành công!"
        err = resp.json().get("error") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        return False, f"Cầu nối Zalo phản hồi lỗi: {err or resp.status_code}"
    except Exception as exc:
        logger.exception("Error sending invoice to Zalo: %s", exc)
        return False, f"Lỗi kết nối tới cầu nối Zalo: {exc}"

