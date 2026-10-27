"""Send a saved cargo trip as a draft invoice to Bkav eHoadon.

Protocol: Bkav's sample RemoteCommand.dll, JSON + GZip + AES-CBC (mode 6).
The only network destination is Bkav's published SOAP service.
"""

from __future__ import annotations

import base64
import hashlib
import gzip
import json
import logging
import re
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from xml.etree import ElementTree
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from backend.services.database import db
from frontend.modules.invoices.export import (
    ALLOWED_PAYMENT_METHODS, DEFAULT_INVOICE_VAT_RATE, DEFAULT_SERVICE_NAME,
    _service_description, is_crane_by_shift_service, parse_vat_rate,
)
from frontend.modules.transport.route_segments import invoice_items

logger = logging.getLogger(__name__)
BKAV_ENDPOINT = "https://ws.ehoadon.vn/WSPublicEHoaDon.asmx"
_TAX_IDS = {Decimal("0"): 1, Decimal("0.05"): 2,
            Decimal("0.08"): 9, Decimal("0.10"): 3}


class BkavConfigurationError(ValueError):
    pass


class BkavUncertainError(RuntimeError):
    """The request may have arrived; automatic retry would risk another invoice."""


def configuration(seller_tax: str | None = None) -> dict:
    """Build BKAV settings using the seller tax code from the company profile."""
    from backend.services.settings_service import get_app_setting
    guid = (get_app_setting("BKAV_PARTNER_GUID") or "").strip()
    token = (get_app_setting("BKAV_PARTNER_TOKEN") or "").strip()
    seller_tax = str(seller_tax or "").strip()
    if not guid or not token or not seller_tax:
        raise BkavConfigurationError(
            "Chưa lưu BKAV_PARTNER_GUID, BKAV_PARTNER_TOKEN hoặc mã số thuế của công ty bên bán."
        )
    try:
        guid = str(UUID(guid))
        key64, iv64 = token.split(":")
        key = base64.b64decode(key64, validate=True)
        iv = base64.b64decode(iv64, validate=True)
    except (ValueError, TypeError) as exc:
        raise BkavConfigurationError("PartnerGUID hoặc PartnerToken BKAV không hợp lệ.") from exc
    if len(key) != 32 or len(iv) != 16:
        raise BkavConfigurationError("PartnerToken BKAV không hợp lệ.")
    if not seller_tax.isdigit() or not 10 <= len(seller_tax) <= 14:
        raise BkavConfigurationError("Mã số thuế của công ty bên bán không hợp lệ.")
    return {"guid": guid, "key": key, "iv": iv, "seller_tax": seller_tax,
            "endpoint": BKAV_ENDPOINT}


def is_configured(seller_tax: str | None = None) -> bool:
    try:
        configuration(seller_tax)
        return True
    except BkavConfigurationError:
        return False


def push_state(trip_id: int) -> dict | None:
    return db._execute_query(
        "SELECT status, invoice_guid, invoice_form, invoice_serial, invoice_no, message, attempted_at "
        "FROM public.bkav_invoice_pushes WHERE trip_id=%s", (trip_id,), fetch=True, commit=False,
    )




def _saved_invoice_partner_id(invoice_guid: str) -> int:
    created = db._execute_query("""
        INSERT INTO public.bkav_saved_invoice_links (invoice_guid)
        VALUES (%s) ON CONFLICT (invoice_guid) DO NOTHING
        RETURNING partner_invoice_id
    """, (invoice_guid,), fetch=True, commit=True)
    if created:
        return int(created["partner_invoice_id"])
    existing = db._execute_query(
        "SELECT partner_invoice_id FROM public.bkav_saved_invoice_links WHERE invoice_guid = %s",
        (invoice_guid,), fetch=True, commit=False,
    )
    if not existing:
        raise BkavUncertainError("Không xác định được mã đồng bộ hóa đơn; hãy thử lại.")
    return int(existing["partner_invoice_id"])


def saved_invoice_push_state(invoice_guid: str) -> dict | None:
    link = db._execute_query(
        "SELECT partner_invoice_id FROM public.bkav_saved_invoice_links WHERE invoice_guid = %s",
        (str(invoice_guid),), fetch=True, commit=False,
    )
    return push_state(int(link["partner_invoice_id"])) if link else None


def build_command(trip: dict, buyer: dict) -> dict:
    if not buyer or not all(str(buyer.get(k) or "").strip() for k in ("name", "address", "tax_code")):
        raise ValueError("Chọn đơn vị mua có đủ tên, địa chỉ và mã số thuế.")
    if not trip.get("invoice_date"):
        raise ValueError("Chuyến hàng chưa có ngày hóa đơn.")
    invoice_day = trip["invoice_date"]
    if isinstance(invoice_day, datetime):
        invoice_day = invoice_day.date()
    if isinstance(invoice_day, str):
        invoice_day = date.fromisoformat(invoice_day[:10])
    if not isinstance(invoice_day, date):
        raise ValueError("Ngày hóa đơn không hợp lệ.")
    revenue = Decimal(str(trip.get("freight_revenue") or 0))
    if not revenue.is_finite() or revenue <= 0:
        raise ValueError("Cước vận chuyển phải lớn hơn 0.")
    vat_rate = parse_vat_rate(trip.get("vat_rate")) or DEFAULT_INVOICE_VAT_RATE
    if vat_rate not in _TAX_IDS:
        raise ValueError("Thuế suất chưa được BKAV hỗ trợ; hãy dùng 0%, 5%, 8% hoặc 10%.")
    service = str(trip.get("service_name") or DEFAULT_SERVICE_NAME).strip() or DEFAULT_SERVICE_NAME
    content = str(trip.get("trip_note") or "").strip()
    if content.startswith("Nội dung hóa đơn gốc:"):
        content = content[len("Nội dung hóa đơn gốc:"):].strip()
    content = content or str(trip.get("cargo_description") or "").strip()
    content = content or _service_description(trip.get("origin"), trip.get("destination"), service)
    method = str(trip.get("payment_method") or "CK").upper()
    if method not in ALLOWED_PAYMENT_METHODS:
        method = "CK"
    email = str(buyer.get("email") or "").strip()
    items = []
    segments = trip.get("route_segments") or []
    grouped_items = {}
    for index, line in enumerate(invoice_items(trip, content)):
        if len(line["description"]) > 500:
            raise ValueError("Nội dung hóa đơn vượt quá 500 ký tự BKAV cho phép.")
        amount = line["amount"]
        tax_amount = (amount * vat_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        segment = segments[index] if index < len(segments) else {}
        plate = str(segment.get("plate_number") or trip.get("plate_number") or "").strip()
        item_desc = str(line["description"] or "").strip()
        # Loại bỏ bất kỳ hậu tố biển kiểm soát/BKS nếu có để chỉ gửi duy nhất nội dung công việc
        item_desc = re.sub(
            r"\s*\((?:Biển kiểm soát|BKS|Biển số)[^)]*\)\s*$", "", item_desc, flags=re.IGNORECASE
        ).strip()
        if len(item_desc) > 500:
            raise ValueError("Nội dung hóa đơn vượt quá 500 ký tự BKAV cho phép.")
        item = {
            "ItemTypeID": 0,
            "IsDiscount": False,
            "ItemName": item_desc,
            "UnitName": "Ca cẩu" if is_crane_by_shift_service(service) else "Chuyến",
            "Qty": 1,
            "Price": float(amount),
            "Amount": float(amount),
            "TaxRateID": _TAX_IDS[vat_rate],
            "TaxRate": float(vat_rate * 100),
            "TaxAmount": float(tax_amount),
            "DiscountRate": 0,
            "DiscountAmount": 0,
            "UserDefineDetails": "",
            "SpecialtyItems": "",
        }
        group_key = (plate.upper(),
                     str(segment.get("origin") or "").strip().casefold(),
                     str(segment.get("destination") or "").strip().casefold(),
                     json.dumps(segment.get("via_points") or [], ensure_ascii=False, sort_keys=True),
                     item["ItemTypeID"], item["ItemName"],
                     item["UnitName"], item["Price"], item["TaxRateID"],
                     item["TaxRate"], item["SpecialtyItems"])
        existing = grouped_items.get(group_key) if plate else None
        if existing is not None:
            existing["Qty"] += 1
            existing["Amount"] += item["Amount"]
            existing["TaxAmount"] += item["TaxAmount"]
        else:
            items.append(item)
            if plate:
                grouped_items[group_key] = item
    invoice_timestamp = datetime.combine(invoice_day, time.min, ZoneInfo("Asia/Ho_Chi_Minh"))
    invoice = {"InvoiceTypeID": 1, "InvoiceDate": invoice_timestamp.isoformat(),
               "BuyerName": "", "BuyerTaxCode": str(buyer["tax_code"]).strip(),
               "BuyerUnitName": str(buyer["name"]).strip(),
               "BuyerAddress": str(buyer["address"]).strip(),
               "BuyerBankAccount": str(buyer.get("bank_account") or "").strip(),
               "PayMethodID": 2 if method == "CK" else 1,
               "ReceiveTypeID": 1 if email else 3, "ReceiverEmail": email,
               "ReceiverMobile": "", "ReceiverAddress": "", "ReceiverName": "",
               "Note": "", "BillCode": str(trip.get("document_number") or "")[:36],
               "CurrencyID": "VND", "ExchangeRate": 1,
               "InvoiceForm": "", "InvoiceSerial": "", "InvoiceNo": 0,
               "UserDefine": ""}
    return {"CmdType": 100, "CommandObject": [{"Invoice": invoice,
            "ListInvoiceDetailsWS": items, "ListInvoiceAttachFileWS": [],
            "PartnerInvoiceID": int(trip["id"]), "PartnerInvoiceStringID": ""}]}


def push_form_invoice(invoice: dict, buyer: dict, seller: dict) -> dict:
    """Send an unsaved invoice form to BKAV without creating local invoice history."""
    seller_tax = str((seller or {}).get("tax_code") or "").strip()
    config = configuration(seller_tax)
    if not seller or seller_tax != config["seller_tax"]:
        raise ValueError("Mã số thuế bên bán không khớp tài khoản BKAV đã cấu hình.")

    identity = "|".join((seller_tax, str(invoice["invoice_date"]),
                         str(invoice.get("invoice_serial") or "").strip(),
                         str(invoice["invoice_number"])))
    partner_invoice_id = int.from_bytes(hashlib.sha256(identity.encode("utf-8")).digest()[:8], "big")
    partner_invoice_id &= (1 << 63) - 1
    partner_invoice_id = partner_invoice_id or 1

    lines = invoice.get("lines") or []
    trip = {
        "id": partner_invoice_id,
        "invoice_date": invoice["invoice_date"],
        "invoice_bill_number": int(invoice["invoice_number"]),
        "document_number": f"{invoice.get('invoice_serial') or ''}{int(invoice['invoice_number']):08d}",
        "service_name": invoice.get("service_name") or DEFAULT_SERVICE_NAME,
        "freight_revenue": invoice["subtotal"],
        "vat_rate": invoice["vat_rate"],
        "payment_method": invoice.get("payment_method") or "CK",
        "cargo_description": next((str(line.get("cargo_description") or "").strip()
                                    for line in lines if line.get("cargo_description")), ""),
        "route_segments": [{
            "plate_number": line.get("plate_number") or "",
            "origin": line.get("origin") or "",
            "destination": line.get("destination") or "",
            "via_points": line.get("via_points") or [],
            "description": line.get("description") or "",
            "amount": line["amount"],
        } for line in lines],
    }
    command = build_command(trip, buyer)
    response = _send_command(command, config)
    if response.get("Status") != 0 or not response.get("isOk", False):
        detail = str(response.get("Object") or "BKAV từ chối lệnh gửi hóa đơn.")
        raise ValueError(detail[:300])
    items = response.get("Object")
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except ValueError as exc:
            raise BkavUncertainError("BKAV trả về dữ liệu không đọc được; hãy kiểm tra hóa đơn trên BKAV.") from exc
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise BkavUncertainError("BKAV trả về kết quả không hợp lệ; hãy kiểm tra hóa đơn trên BKAV.")
    result = items[0]
    if int(result.get("PartnerInvoiceID") or 0) != partner_invoice_id:
        raise BkavUncertainError("BKAV trả về mã hóa đơn không khớp; hãy kiểm tra trên BKAV.")
    if int(result.get("Status") or 0) != 0:
        raise ValueError(str(result.get("MessLog") or "BKAV từ chối hóa đơn.")[:300])
    return {"status": "sent", "partner_invoice_id": partner_invoice_id,
            "invoice_guid": result.get("InvoiceGUID"),
            "invoice_form": result.get("InvoiceForm"),
            "invoice_serial": result.get("InvoiceSerial"),
            "invoice_no": result.get("InvoiceNo")}


def _cipher(data: bytes, config: dict, *, decrypt: bool = False) -> bytes:
    cipher = Cipher(algorithms.AES(config["key"]), modes.CBC(config["iv"]))
    if decrypt:
        decryptor = cipher.decryptor()
        padded = decryptor.update(data) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    padder = PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _send_command(command: dict, config: dict) -> dict:
    compressed = gzip.compress(json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    encrypted = base64.b64encode(_cipher(compressed, config)).decode("ascii")
    body = ("<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            "<soap:Envelope xmlns:soap=\"http://schemas.xmlsoap.org/soap/envelope/\">"
            "<soap:Body><ExecuteCommand xmlns=\"http://tempuri.org/\">"
            f"<PartnerGUID>{escape(config['guid'])}</PartnerGUID>"
            f"<EncryptedCommandData>{encrypted}</EncryptedCommandData>"
            "</ExecuteCommand></soap:Body></soap:Envelope>")
    with httpx.Client(timeout=25, follow_redirects=False) as client:
        response = client.post(config["endpoint"], content=body.encode("utf-8"),
                               headers={"Content-Type": "text/xml; charset=utf-8",
                                        "SOAPAction": '"http://tempuri.org/ExecuteCommand"'})
        response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    result = root.find(".//{http://tempuri.org/}ExecuteCommandResult")
    if result is None or not result.text:
        raise ValueError("BKAV không trả về kết quả lệnh hợp lệ.")
    decoded = _cipher(base64.b64decode(result.text.strip(), validate=True), config, decrypt=True)
    return json.loads(gzip.decompress(decoded).decode("utf-8"))


def _reserve(trip_id: int, seller_tax: str) -> dict | None:
    inserted = db._execute_query("""
        INSERT INTO public.bkav_invoice_pushes (trip_id,seller_tax_code,partner_invoice_id,status)
        VALUES (%s,%s,%s,'processing') ON CONFLICT (trip_id) DO NOTHING RETURNING trip_id
    """, (trip_id, seller_tax, trip_id), fetch=True, commit=True)
    if inserted:
        return None
    state = push_state(trip_id)
    if state and state["status"] == "sent":
        return state
    if state and state["status"] == "failed":
        retry = db._execute_query("""
            UPDATE public.bkav_invoice_pushes SET status='processing', message=NULL,
                   attempted_at=now(), completed_at=NULL
            WHERE trip_id=%s AND seller_tax_code=%s AND status='failed' RETURNING trip_id
        """, (trip_id, seller_tax), fetch=True, commit=True)
        if retry:
            return None
    raise BkavUncertainError(
        "Lệnh gửi trước đang xử lý hoặc chưa rõ kết quả. Kiểm tra trên BKAV trước khi gửi lại."
    )


def _finish(trip_id: int, status: str, *, item: dict | None = None, message: str = "") -> dict:
    item = item or {}
    guid = item.get("InvoiceGUID") or None
    row = db._execute_query("""
        UPDATE public.bkav_invoice_pushes SET status=%s, invoice_guid=%s,
            invoice_form=%s, invoice_serial=%s, invoice_no=%s, message=%s,
            completed_at=now() WHERE trip_id=%s RETURNING status,invoice_guid,invoice_form,
            invoice_serial,invoice_no,message
    """, (status, guid, item.get("InvoiceForm"), item.get("InvoiceSerial"),
          item.get("InvoiceNo"), message[:1000], trip_id), fetch=True, commit=True)
    return row


def push_invoice(trip: dict, buyer: dict, seller: dict) -> dict:
    seller_tax = str(seller.get("tax_code") or "").strip() if seller else None
    config = configuration(seller_tax)
    if not seller or str(seller.get("tax_code") or "").strip() != config["seller_tax"]:
        raise ValueError("Mã số thuế bên bán của chuyến hàng không khớp tài khoản BKAV đã cấu hình.")
    imported = db._execute_query(
        "SELECT 1 FROM public.bkav_invoice_imports WHERE trip_id = %s",
        (trip["id"],), fetch=True, commit=False,
    )
    if imported:
        raise ValueError("Hóa đơn của chuyến hàng này đã có trên BKAV; không thể gửi lại.")
    command = build_command(trip, buyer)
    trip_id = int(trip["id"])
    previous = _reserve(trip_id, config["seller_tax"])
    if previous:
        return {**previous, "already_sent": True}
    try:
        response = _send_command(command, config)
    except Exception as exc:
        logger.exception("BKAV response unknown for cargo trip %s", trip_id)
        _finish(trip_id, "uncertain", message="Không nhận được kết quả rõ ràng từ BKAV.")
        raise BkavUncertainError(
            "Không xác định được BKAV đã nhận hóa đơn hay chưa. Kiểm tra trên BKAV trước khi thao tác tiếp."
        ) from exc
    if response.get("Status") != 0 or not response.get("isOk", False):
        detail = str(response.get("Object") or "BKAV từ chối lệnh gửi hóa đơn.")
        _finish(trip_id, "failed", message=detail)
        raise ValueError(detail[:300])
    items = response.get("Object")
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except ValueError as exc:
            _finish(trip_id, "uncertain", message="BKAV trả về dữ liệu không đọc được.")
            raise BkavUncertainError("BKAV trả về dữ liệu không đọc được; kiểm tra hóa đơn trên BKAV.") from exc
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        _finish(trip_id, "uncertain", message="BKAV trả về kết quả hóa đơn không hợp lệ.")
        raise BkavUncertainError("BKAV trả về kết quả không hợp lệ; kiểm tra hóa đơn trên BKAV.")
    item = items[0]
    if int(item.get("PartnerInvoiceID") or 0) != trip_id:
        _finish(trip_id, "uncertain", message="BKAV trả về mã hóa đơn nội bộ không khớp.")
        raise BkavUncertainError("BKAV trả về mã hóa đơn không khớp; kiểm tra trên BKAV.")
    if int(item.get("Status") or 0) != 0:
        detail = str(item.get("MessLog") or "BKAV từ chối hóa đơn.")
        _finish(trip_id, "failed", message=detail)
        raise ValueError(detail[:300])
    if not item.get("InvoiceGUID"):
        _finish(trip_id, "uncertain", message="BKAV không trả về InvoiceGUID.")
        raise BkavUncertainError("BKAV chưa trả về mã hóa đơn; kiểm tra trên BKAV.")
    return {**_finish(trip_id, "sent", item=item,
                      message="Đã tạo hóa đơn nháp trên BKAV."),
            "already_sent": False}


def push_saved_invoice(invoice: dict, lines: list[dict], buyer: dict, seller: dict) -> dict:
    """Send one saved invoice, grouping identical route legs into BKAV quantities."""
    if not seller:
        raise ValueError("Không tìm thấy công ty bên bán.")
    seller_tax = str(seller.get("tax_code") or "").strip()
    config = configuration(seller_tax)
    if invoice.get("entry_origin") not in ("manual", "edited", None):
        raise ValueError("Hóa đơn này đã có trên BKAV; không gửi lại.")
    if not lines:
        raise ValueError("Hóa đơn chưa có dòng xe để đồng bộ.")
    rate = parse_vat_rate(invoice.get("vat_rate"))
    if rate is None:
        raise ValueError("Hóa đơn chưa có thuế suất GTGT.")

    segments = []
    subtotal = Decimal(0)
    total_tax = Decimal(0)
    for index, line in enumerate(lines, 1):
        description = str(line.get("description") or "").strip()
        plate = str(line.get("plate_number") or "").strip()
        if not plate and line.get("vehicle_id"):
            v_row = db._execute_query(
                "SELECT plate_number FROM public.plate_whitelist WHERE id = %s",
                (line["vehicle_id"],), fetch=True, commit=False,
            )
            if v_row:
                plate = str(v_row.get("plate_number") or "").strip()
        if not plate:
            plate = str(invoice.get("plate_number") or "Xe").strip()
        amount = Decimal(str(line.get("amount") or 0))
        if not description or not plate or not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
            raise ValueError(f"Dòng xe {index} thiếu biển số, nội dung hoặc cước hợp lệ.")
        tax = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if line.get("tax_amount") is not None and Decimal(str(line["tax_amount"])) != tax:
            raise ValueError(f"Thuế dòng xe {index} không khớp số tiền đã lưu.")
        subtotal += amount
        total_tax += tax
        segments.append({"description": description, "plate_number": plate, "amount": amount,
                         "origin": line.get("origin"), "destination": line.get("destination"),
                         "via_points": line.get("via_points") or []})
    if subtotal != Decimal(str(invoice.get("subtotal") or 0)) or subtotal + total_tax != Decimal(str(invoice.get("gross") or 0)):
        raise ValueError("Tổng tiền hóa đơn không khớp các dòng xe đã lưu.")

    virtual_trip = {
        "id": 1,
        "invoice_date": invoice.get("invoice_date"),
        "document_number": str(invoice.get("invoice_number") or "").zfill(8),
        "freight_revenue": subtotal,
        "vat_rate": rate,
        "payment_method": invoice.get("payment_method"),
        "service_name": invoice.get("service_name"),
        "route_segments": segments,
    }
    build_command(virtual_trip, buyer)  # Validate the complete payload before reserving an ID.
    virtual_trip["id"] = _saved_invoice_partner_id(str(invoice["invoice_guid"]))
    return push_invoice(virtual_trip, buyer, seller)
