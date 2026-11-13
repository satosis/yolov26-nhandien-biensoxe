"""Build a reviewable cargo-trip draft from selected Zalo group messages."""

from __future__ import annotations

import re
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from psycopg2.extras import Json

from backend.services import zalo_bridge
from backend.services.database import db
from frontend.formatting.money import MILLION_PRICE, million_price
from frontend.formatting.text import fold_text as _fold
from frontend.modules.invoices.companies import get_company
from frontend.modules.transport.vehicle_matching import vehicle_owner_in_text
from frontend.modules.zalo.invoice_automation import _stated_cargo
from frontend.modules.zalo.inbox import bridge_secret


_TAX = re.compile(r"(?:\bmst\b|mã\s+số\s+thuế)\s*[:：]?\s*(\d{10}(?:-\d{3})?)", re.I)
_ROUTE = re.compile(r"\b(?:từ|tu)\s+(.+?)\s+(?:đến|den|đi|di|tới|toi|sang|về|ve)\s+(.+?)(?:[.!?]|$)", re.I)
_INVOICE_DONE = re.compile(r"(?:đã|rồi|\br\b).*?(?:tạo|xuất).*?hóa\s*đơn|(?:tạo|xuất).*?hóa\s*đơn.*?(?:rồi|\br\b)", re.I)
_MAP_URL = re.compile(r"https://(?:www\.)?(?:google\.com/maps|maps\.app\.goo\.gl|goo\.gl/maps)(?:/|\?)[^\s<>]+", re.I)


def _province(value: str, provinces: list[str]) -> str:
    folded = _fold(value)
    matches = [name for name in provinces if _fold(name) in folded
               or _fold(name).removeprefix("tinh ").removeprefix("thanh pho ") in folded]
    return max(matches, key=len, default="")


def build_trip_draft(messages: list[dict], *, provinces: list[str],
                     services: list[str], directory: list[dict]) -> dict:
    if not messages:
        raise ValueError("Chọn ít nhất 1 tin nhắn cho một chuyến hàng.")
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
            prices.extend(million_price(found) for found in MILLION_PRICE.finditer(text))
    routes = [_ROUTE.search(text) for text in texts]
    route = next((match for match in routes if match), None)
    origin_province = _province(route.group(1), provinces) if route else ""
    destination_province = _province(route.group(2), provinces) if route else ""
    missing = []
    map_locations = []
    from frontend.modules.zalo.invoice_automation import message_place

    for item, text in zip(messages, texts):
        shared_url = _MAP_URL.search(text)
        attachment_url = str(item.get("attachment_url") or "").strip()
        url = attachment_url if _MAP_URL.fullmatch(attachment_url) else (shared_url.group(0) if shared_url else "")
        located = message_place(item, directory)
        coord_vals = list(located[0]) if located else None
        resolved_address = located[1] if located else ""
        if located and not url:
            url = f"https://maps.google.com/?q={located[0][0]},{located[0][1]}"
        if item.get("attachment_type") == "chat.location.new" or url or located:
            label = text.replace(url, "").strip(" -:") if shared_url else text
            if resolved_address:
                label = resolved_address
            if label or url:
                map_locations.append({
                    "label": label,
                    "address": resolved_address or label,
                    "url": url,
                    "coordinates": coord_vals,
                    "message_id": item["id"],
                })
    map_places = [item.get("url") or item.get("address") or item.get("label") for item in map_locations]
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

    cargo = next(filter(None, map(_stated_cargo, texts)), "")
    transport_requested = any("van chuyen" in _fold(text) or "cuoc" in _fold(text) for text in texts)
    service = next((name for name in services if transport_requested and "van chuyen" in _fold(name) and
                    ("cau" in _fold(name) or not any("cau" in _fold(s) for s in services))), "")

    vehicle_nickname = ""
    for t in texts:
        title, name = vehicle_owner_in_text(t)
        if title:
            vehicle_nickname = name or title
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


BRIDGE_DISABLED_FILE = zalo_bridge.DISABLED_FILE


def ensure_zalo_bridge(timeout_secs: float = 6.0) -> int | None:
    """Ensure the Zalo bridge node process is running. If stopped and session.json exists, auto-start it."""
    return zalo_bridge.ensure(timeout_secs)


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


async def _bridge_post(path: str, payload: dict, timeout: float = 30) -> dict:
    port = ensure_zalo_bridge()
    if not port:
        raise ValueError("Cầu nối Zalo chưa sẵn sàng.")
    secret = bridge_secret()
    clean_path = path if path.startswith("/") else f"/{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"http://127.0.0.1:{port}{clean_path}",
            headers={"X-Zalo-Bridge-Secret": secret},
            json=payload,
        )
    try:
        data = resp.json()
    except Exception:
        data = {"ok": resp.status_code == 200, "error": resp.text}
    if resp.status_code >= 400:
        err_msg = (data.get("error") if isinstance(data, dict) else "") or f"HTTP {resp.status_code}"
        raise ValueError(err_msg)
    return data if isinstance(data, dict) else {"data": data}


async def list_zalo_groups() -> list[dict]:
    res = await _bridge_post("/list-groups", {})
    return res.get("groups") or []


def _named_invoice_pdf(guid_str: str, pdf_path: Path) -> Path:
    """Copy of the PDF named like BKAV's download (C26TAA-00000099-MZ4I5GPFNBD-DPH.pdf); Zalo shows the file name."""
    import shutil

    invoice = db._execute_query(
        "SELECT invoice_serial, invoice_number, invoice_code FROM public.bkav_invoices WHERE invoice_guid = %s",
        (guid_str,), fetch=True, commit=False,
    ) or {}
    serial = re.sub(r"[^A-Za-z0-9]", "", str(invoice.get("invoice_serial") or ""))
    number = int(invoice.get("invoice_number") or 0)
    code = re.sub(r"[^A-Za-z0-9]", "", str(invoice.get("invoice_code") or ""))
    if not serial or not number:
        return pdf_path
    name = f"{serial}-{number:08d}-{code}-DPH.pdf" if code else f"{serial}-{number:08d}.pdf"
    target = pdf_path.parent / "zalo" / guid_str / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_mtime < pdf_path.stat().st_mtime:
        shutil.copyfile(pdf_path, target)
    return target


async def send_invoice_pdf_to_zalo(
    invoice_guid: str,
    group_id: str,
    group_name: str = "",
    sent_by: str = "",
) -> tuple[bool, str]:
    import asyncio
    from uuid import UUID
    from frontend.modules.invoices.bkav_sync import get_or_fetch_bkav_invoice_files

    guid_str = str(UUID(str(invoice_guid)))
    try:
        _, pdf_path = await asyncio.to_thread(get_or_fetch_bkav_invoice_files, guid_str)
    except Exception as exc:
        return False, f"Không lấy được file PDF hóa đơn: {exc}"

    if not pdf_path or not Path(pdf_path).exists():
        return False, "Không tìm thấy file PDF hóa đơn."

    send_path = Path(pdf_path).resolve()
    try:
        send_path = _named_invoice_pdf(guid_str, send_path)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Không đặt được tên file PDF %s: %s", guid_str, exc)

    res = await _bridge_post("/send-invoice-file", {
        "group_id": str(group_id),
        "file_path": str(send_path),
    })

    if not res.get("ok"):
        return False, res.get("error") or "Không gửi được file hóa đơn."

    msg_ids = res.get("msg_ids") or []
    db._execute_query("""
        INSERT INTO public.zalo_invoice_messages (invoice_guid, group_id, group_name, msg_ids, sent_by)
        VALUES (%s, %s, %s, %s, %s)
    """, (guid_str, str(group_id), group_name or "", Json(msg_ids), sent_by or ""), commit=True)

    target_name = f"nhóm {group_name}" if group_name else "nhóm Zalo"
    return True, f"Đã gửi PDF hóa đơn vào {target_name}."


def _mark_recalled(record_id: int) -> None:
    db._execute_query("UPDATE public.zalo_invoice_messages SET recalled_at = now() WHERE id = %s", (record_id,), commit=True)


async def recall_invoice_zalo(invoice_guid: str) -> tuple[bool, str]:
    from uuid import UUID

    guid_str = str(UUID(str(invoice_guid)))
    records = db._execute_query("""
        SELECT id, group_id, msg_ids
        FROM public.zalo_invoice_messages
        WHERE invoice_guid = %s AND recalled_at IS NULL
        ORDER BY sent_at DESC, id DESC
    """, (guid_str,), fetchall=True, commit=False)

    if not records:
        return False, "Chưa có tin hóa đơn nào để thu hồi."

    first_error = None
    all_ok = True

    for rec in records:
        rec_id = rec["id"]
        group_id = rec["group_id"]
        raw_ids = rec.get("msg_ids") or []
        if isinstance(raw_ids, str):
            try:
                raw_ids = json.loads(raw_ids)
            except Exception:
                raw_ids = []
        msg_ids = [str(m) for m in raw_ids if m]
        if not msg_ids:
            _mark_recalled(rec_id)
            continue

        try:
            res = await _bridge_post("/recall-messages", {
                "group_id": group_id,
                "msg_ids": msg_ids,
            })
            if res.get("ok"):
                _mark_recalled(rec_id)
            else:
                all_ok = False
                results = res.get("results") or []
                err_item = next((r.get("error") for r in results if r and not r.get("ok")), None)
                err_msg = err_item or res.get("error") or "Thu hồi tin nhắn thất bại"
                if not first_error:
                    first_error = err_msg
        except Exception as exc:
            all_ok = False
            if not first_error:
                first_error = str(exc)

    if all_ok:
        return True, "Đã thu hồi tin hóa đơn trên Zalo."
    return False, first_error or "Không thể thu hồi tin nhắn trên Zalo."


def invoice_zalo_status(invoice_guids: list[str]) -> dict[str, dict]:
    if not invoice_guids:
        return {}
    clean_guids = [str(g) for g in invoice_guids if g]
    if not clean_guids:
        return {}

    rows = db._execute_query("""
        SELECT invoice_guid::text AS guid, group_name, recalled_at, sent_at
        FROM public.zalo_invoice_messages
        WHERE invoice_guid = ANY(%s::uuid[])
        ORDER BY sent_at DESC, id DESC
    """, (clean_guids,), fetchall=True, commit=False)

    result = {g: {"sent": False, "group_name": ""} for g in clean_guids}
    if not rows:
        return result

    for row in rows:
        guid = str(row["guid"])
        if guid not in result:
            continue
        if not result[guid]["group_name"] and row.get("group_name"):
            result[guid]["group_name"] = row["group_name"]
        if row.get("recalled_at") is None:
            result[guid]["sent"] = True

    return result
