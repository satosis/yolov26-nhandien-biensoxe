"""
Gmail and Email Invoice Synchronization Service.
Supports:
  1. Gmail API via Google OAuth2 (Official Google Cloud Project).
  2. Gmail IMAP via App Password (Direct SSL connection).
  3. XML E-Invoice Parser (Circular 78 / Circular 32 / MISA / VNPT / Viettel / Petrolimex).
  4. Zip and PDF attachment extraction.
  5. Email body & subject invoice metadata parser.
  6. Deduplication and auto-import into transport_input_invoices.
"""

from __future__ import annotations

import base64
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import email
from email.header import decode_header
import imaplib
import io
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

from dotenv import find_dotenv, load_dotenv
import httpx
from psycopg2 import Binary

from backend.services.database import db
from backend.services.settings_service import get_app_setting, set_many_app_settings
from frontend.modules.invoices.service import money

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Credentials & Settings Helpers
# ---------------------------------------------------------------------------

def _get_env_or_setting(key: str, default: str = "") -> str:
    val = os.getenv(key)
    if not val:
        load_dotenv(find_dotenv() or None)
        val = os.getenv(key)
    if val and val.strip():
        return val.strip()
    return get_app_setting(key.lower(), default).strip()


def get_google_client_id() -> str:
    return _get_env_or_setting("GOOGLE_CLIENT_ID")


def get_google_client_secret() -> str:
    return _get_env_or_setting("GOOGLE_CLIENT_SECRET")


def get_gmail_refresh_token() -> str:
    return _get_env_or_setting("GMAIL_REFRESH_TOKEN") or get_app_setting("gmail_refresh_token", "")


def get_connected_gmail_email() -> str:
    return _get_env_or_setting("GMAIL_SYNC_EMAIL") or get_app_setting("gmail_sync_email", "")


def get_company_by_enterprise_email(email_address: str) -> Optional[dict]:
    """Look up an enterprise in invoice_companies whose registered email matches."""
    if not email_address or not email_address.strip():
        return None
    clean_email = email_address.strip().lower()
    return db._execute_query(
        "SELECT id, name, tax_code, address, email FROM public.invoice_companies WHERE LOWER(TRIM(COALESCE(email, ''))) = %s LIMIT 1",
        (clean_email,), fetch=True, commit=False,
    )


def save_gmail_tokens(refresh_token: str, access_token: str = "", email_address: str = ""):
    settings = {}
    if refresh_token:
        settings["gmail_refresh_token"] = refresh_token
    if access_token:
        settings["gmail_access_token"] = access_token
    if email_address:
        settings["gmail_sync_email"] = email_address
    if settings:
        set_many_app_settings(settings)


# ---------------------------------------------------------------------------
# Google OAuth2 Flow for Gmail API
# ---------------------------------------------------------------------------

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]


def build_gmail_oauth_url(redirect_uri: str, state: str = "gmail_sync") -> str:
    client_id = get_google_client_id()
    if not client_id:
        raise ValueError("Chưa cấu hình GOOGLE_CLIENT_ID trong hệ thống.")

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


async def exchange_gmail_code_for_tokens(code: str, redirect_uri: str) -> dict:
    client_id = get_google_client_id()
    client_secret = get_google_client_secret()
    if not client_id or not client_secret:
        raise ValueError("Thiếu GOOGLE_CLIENT_ID hoặc GOOGLE_CLIENT_SECRET.")

    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(token_url, data=data)
        if resp.status_code != 200:
            logger.error("OAuth token exchange failed: %s", resp.text)
            raise ValueError(f"Không thể đổi token với Google: {resp.text}")
        token_data = resp.json()

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")

    email_address = ""
    if access_token:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                u_resp = await client.get(
                    "https://www.googleapis.com/oauth2/v2/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if u_resp.status_code == 200:
                    email_address = u_resp.json().get("email", "")
        except Exception as exc:
            logger.warning("Could not fetch user email: %s", exc)

    save_gmail_tokens(refresh_token, access_token, email_address)
    return {
        "email": email_address,
        "has_refresh_token": bool(refresh_token),
        "access_token": access_token,
    }


async def get_valid_gmail_access_token() -> str | None:
    refresh_token = get_gmail_refresh_token()
    if not refresh_token:
        return None

    client_id = get_google_client_id()
    client_secret = get_google_client_secret()
    if not client_id or not client_secret:
        return None

    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(token_url, data=data)
            if resp.status_code == 200:
                token_data = resp.json()
                return token_data.get("access_token")
            logger.warning("Token refresh returned %s: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.warning("Token refresh exception: %s", exc)
    return None


# ---------------------------------------------------------------------------
# E-Invoice XML Parser (Circular 78, Circular 32, MISA, Petrolimex, etc.)
# ---------------------------------------------------------------------------

def _parse_xml_text(elem: ET.Element, *tag_names: str) -> str:
    for target in tag_names:
        target_lower = target.lower()
        for node in elem.iter():
            local = node.tag.split("}")[-1].lower()
            if local == target_lower and node.text:
                text = node.text.strip()
                if text:
                    return text
    return ""


def _parse_xml_money(elem: ET.Element, *tag_names: str) -> Decimal:
    raw = _parse_xml_text(elem, *tag_names)
    if not raw:
        return Decimal(0)
    raw = re.sub(r"[^0-9,.-]", "", raw)
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".") if len(raw.rsplit(",", 1)[1]) in (1, 2) else raw.replace(",", "")
    try:
        return Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal(0)


def _parse_xml_date(elem: ET.Element, *tag_names: str) -> Optional[date]:
    raw = _parse_xml_text(elem, *tag_names)
    if not raw:
        return None
    raw = raw.split("T")[0].split(" ")[0].strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def parse_einvoice_xml(xml_bytes: bytes) -> Optional[Dict[str, Any]]:
    """Parse standard Vietnamese electronic invoice XML document."""
    try:
        # Strip potential BOM and leading garbage
        if xml_bytes.startswith(b"\xef\xbb\xbf"):
            xml_bytes = xml_bytes[3:]
        root = ET.fromstring(xml_bytes)
    except Exception as exc:
        logger.debug("XML parse error: %s", exc)
        return None

    # Check if this resembles an invoice document
    has_invoice_keyword = any(
        k in root.tag.lower()
        for k in ("hdon", "invoice", "hoadon", "dlhdon", "ttchung", "ndhdon")
    ) or bool(_parse_xml_text(root, "SHDon", "InvoiceNo", "SoHoaDon"))
    if not has_invoice_keyword:
        return None

    # Invoice Number
    invoice_number = _parse_xml_text(root, "SHDon", "InvoiceNo", "SoHoaDon", "InvNum", "So")
    if invoice_number:
        # Normalize: keep up to 8-10 digits without spaces
        invoice_number = invoice_number.strip().zfill(7) if invoice_number.isdigit() and len(invoice_number) < 7 else invoice_number.strip()

    # Serial
    khms = _parse_xml_text(root, "KHMSHDon", "InvoicePattern", "MauSo")
    khhd = _parse_xml_text(root, "KHHDon", "SerialNo", "KyHieu", "Serial")
    serial = f"{khms}{khhd}" if (khms and khhd and khhd not in khms) else (khhd or khms or "")

    # Date
    invoice_date = _parse_xml_date(root, "NLap", "InvoiceDate", "NgayLap", "NgayHD", "Date") or date.today()

    # Seller Information
    nban_node = None
    for node in root.iter():
        if node.tag.split("}")[-1].lower() in ("nban", "seller", "donviban"):
            nban_node = node
            break

    seller_name = _parse_xml_text(nban_node or root, "Ten", "SellerName", "TenNBan", "Name") if nban_node is not None else _parse_xml_text(root, "SellerName", "TenNBan")
    seller_tax_code = _parse_xml_text(nban_node or root, "MST", "SellerTaxCode", "MSTNBan", "TaxCode") if nban_node is not None else _parse_xml_text(root, "SellerTaxCode", "MSTNBan")
    seller_address = _parse_xml_text(nban_node or root, "DChi", "SellerAddress", "DChiNBan", "Address") if nban_node is not None else _parse_xml_text(root, "SellerAddress", "DChiNBan")

    # Buyer Information
    nmua_node = None
    for node in root.iter():
        if node.tag.split("}")[-1].lower() in ("nmua", "buyer", "donvimua"):
            nmua_node = node
            break

    buyer_name = _parse_xml_text(nmua_node or root, "Ten", "BuyerName", "TenNMua", "Name") if nmua_node is not None else _parse_xml_text(root, "BuyerName", "TenNMua")
    buyer_tax_code = _parse_xml_text(nmua_node or root, "MST", "BuyerTaxCode", "MSTNMua", "TaxCode") if nmua_node is not None else _parse_xml_text(root, "BuyerTaxCode", "MSTNMua")
    buyer_address = _parse_xml_text(nmua_node or root, "DChi", "BuyerAddress", "DChiNMua", "Address") if nmua_node is not None else _parse_xml_text(root, "BuyerAddress", "DChiNMua")

    # Payment Method
    payment_raw = _parse_xml_text(root, "HTTToan", "PaymentMethod", "HinhThucTT")
    payment_method = "Chuyển khoản"
    if "TM" in payment_raw and "CK" not in payment_raw:
        payment_method = "Tiền mặt"
    elif "CK" in payment_raw or "TM/CK" in payment_raw:
        payment_method = "Chuyển khoản"

    # Line Items
    items = []
    subtotal = Decimal(0)
    for node in root.iter():
        tag = node.tag.split("}")[-1].lower()
        if tag in ("hhdvu", "product", "item"):
            desc = _parse_xml_text(node, "THHDVu", "ProdName", "TenHang", "Description")
            if not desc:
                continue
            unit = _parse_xml_text(node, "DVTinh", "ProdUnit", "DVT", "Unit")
            qty_raw = _parse_xml_text(node, "SLuong", "ProdQuantity", "SoLuong", "Quantity")
            price_raw = _parse_xml_text(node, "DGia", "ProdPrice", "DonGia", "Price")
            amount_val = _parse_xml_money(node, "ThTien", "Tien", "Total", "Amount")

            qty = Decimal(str(qty_raw).replace(",", ".")) if qty_raw and re.match(r"^\d+(\.\d+)?$", str(qty_raw).replace(",", ".")) else Decimal(1)
            price = Decimal(str(price_raw).replace(",", ".")) if price_raw and re.match(r"^\d+(\.\d+)?$", str(price_raw).replace(",", ".")) else amount_val

            if amount_val == Decimal(0) and qty > 0 and price > 0:
                amount_val = (qty * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            subtotal += amount_val
            items.append({
                "description": desc,
                "unit": unit,
                "quantity": str(qty),
                "unit_price": str(price),
                "amount": str(amount_val),
            })

    # Totals
    ttoan_subtotal = _parse_xml_money(root, "TgTCThue", "TotalAmount", "TongTienChuaThue")
    if ttoan_subtotal > Decimal(0):
        subtotal = ttoan_subtotal

    total = _parse_xml_money(root, "TgTTTBSo", "Amount", "TongTienThanhToan", "Total")
    vat_amount = _parse_xml_money(root, "TgTThue", "VATAmount", "TienThue")

    vat_rate_text = _parse_xml_text(root, "TSuat", "VATRate", "ThueSuat")
    vat_percent = Decimal(10)
    if vat_rate_text:
        vat_match = re.search(r"(\d+(?:\.\d+)?)", vat_rate_text)
        if vat_match:
            try:
                vat_percent = Decimal(vat_match.group(1))
            except Exception:
                pass
        elif any(k in vat_rate_text.upper() for k in ("KKKNT", "KCT", "KHONG", "KHAC", "X")):
            vat_percent = Decimal(0)
            vat_amount = Decimal(0)

    if vat_amount == Decimal(0) and subtotal > Decimal(0) and (total == subtotal or total == Decimal(0)):
        vat_percent = Decimal(0)

    if total == Decimal(0) and subtotal > Decimal(0):
        if vat_amount == Decimal(0) and vat_percent > Decimal(0):
            vat_amount = (subtotal * vat_percent / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total = subtotal + vat_amount
    elif subtotal == Decimal(0) and total > Decimal(0):
        subtotal = total
        vat_amount = Decimal(0)
        vat_percent = Decimal(0)

    if not items:
        items = [{
            "description": f"Hóa đơn {serial} {invoice_number} từ {seller_name}" if seller_name else "Chi phí dịch vụ",
            "unit": "gói",
            "quantity": "1",
            "unit_price": str(subtotal),
            "amount": str(subtotal),
        }]

    return {
        "invoice_date": invoice_date,
        "serial": serial[:50],
        "invoice_number": invoice_number[:80],
        "seller_name": (seller_name or "Chưa rõ đơn vị bán")[:255],
        "seller_tax_code": seller_tax_code[:30],
        "seller_address": seller_address[:1000],
        "buyer_name": (buyer_name or "")[:255],
        "buyer_tax_code": buyer_tax_code[:30],
        "buyer_address": buyer_address[:1000],
        "payment_method": payment_method,
        "items": items,
        "subtotal": subtotal,
        "vat_percent": vat_percent,
        "vat_amount": vat_amount,
        "total": total,
        "source_type": "application/xml",
    }


# ---------------------------------------------------------------------------
# Email Subject & Body Metadata Extractor (for notifications like MISA meInvoice)
# ---------------------------------------------------------------------------

def parse_invoice_from_email_text(subject: str, body: str, sender: str = "") -> Optional[Dict[str, Any]]:
    """
    Extract invoice metadata from subject line & body text.
    Handles MISA meInvoice, Petrolimex, Kenda, etc.
    """
    combined = f"{subject}\n{body}"

    # Search for invoice number
    num_match = re.search(r"(?:Số|Số hóa đơn|Hóa đơn số|HĐ số|No\.)\s*[:：]?\s*(\d{5,10})", combined, re.IGNORECASE)
    if not num_match:
        num_match = re.search(r"\b(?:hóa đơn điện tử số|hđ số)\s+(\d{5,10})\b", combined, re.IGNORECASE)
    if not num_match:
        return None

    invoice_number = num_match.group(1).strip()

    # Serial (Ký hiệu)
    serial_match = re.search(r"(?:Ký hiệu|Mẫu số - Ký hiệu|Ký hiệu HĐ)\s*[:：]?\s*([A-Za-z0-9/]+)", combined, re.IGNORECASE)
    serial = serial_match.group(1).strip() if serial_match else ""

    # Date
    date_match = re.search(r"(?:Ngày|Ngày lập|Ngày ký|Date)\s*[:：]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})", combined, re.IGNORECASE)
    invoice_date = date.today()
    if date_match:
        raw_d = date_match.group(1).replace("-", "/")
        try:
            invoice_date = datetime.strptime(raw_d, "%d/%m/%Y").date()
        except ValueError:
            pass

    # Seller Name
    seller_name = ""
    # Look in subject: "CÔNG TY ... gửi hóa đơn điện tử"
    subj_seller = re.search(r"^(.*?)(?:\s+gửi hóa đơn|\s+thông báo phát hành|\s+thông báo gửi)", subject, re.IGNORECASE)
    if subj_seller and len(subj_seller.group(1).strip()) > 3:
        seller_name = subj_seller.group(1).strip()
    elif "petrolimex" in subject.lower():
        seller_name = subject.strip()
    elif "thông báo phát hành" in subject.lower():
        p_match = re.search(r"Thông báo phát hành\s+(.*)", subject, re.IGNORECASE)
        if p_match:
            seller_name = p_match.group(1).strip()

    if not seller_name:
        # Check body: "CÔNG TY ... xin gửi cho Quý khách Hóa đơn"
        body_seller = re.search(r"([A-ZÀ-Ỹ0-9\s.,-]+)\s+(?:xin gửi cho Quý khách|thông báo gửi Hóa đơn)", body)
        if body_seller and len(body_seller.group(1).strip()) > 4:
            seller_name = body_seller.group(1).strip()

    if not seller_name:
        seller_name = sender.split("<")[0].strip(' "') if sender else "Đơn vị bán hàng qua Email"

    # Tax code
    tax_match = re.search(r"(?:Mã số thuế|MST|Tax Code)\s*[:：]?\s*([0-9]{10}(?:-[0-9]{3})?)", combined, re.IGNORECASE)
    seller_tax_code = tax_match.group(1).strip() if tax_match else ""

    # Look for amount in body if present
    total_match = re.search(r"(?:Tổng tiền|Tổng thanh toán|Số tiền|Thành tiền)\s*[:：]?\s*([0-9.,]+)\s*(?:đ|vnd|đồng)?", combined, re.IGNORECASE)
    total = Decimal(0)
    if total_match:
        try:
            raw_t = total_match.group(1).replace(".", "").replace(",", ".")
            total = Decimal(raw_t).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except Exception:
            pass

    # Extract Lookup link or Tax Authority Code (Mã CQT)
    note_parts = []
    cqt_match = re.search(r"(?:Mã cơ quan thuế|Mã CQT)\s*[:：]?\s*([A-Za-z0-9-]+)", combined, re.IGNORECASE)
    if cqt_match:
        note_parts.append(f"Mã CQT: {cqt_match.group(1).strip()}")

    lookup_match = re.search(r"(?:mã tra cứu|nhập mã số)\s*[:：]?\s*([A-Za-z0-9]+)", combined, re.IGNORECASE)
    if lookup_match:
        note_parts.append(f"Mã tra cứu: {lookup_match.group(1).strip()}")

    link_match = re.search(r"(https?://\S+(?:tra-cuu|meinvoice|vnpt|viettel|sinvoice|bkav)\S*)", combined, re.IGNORECASE)
    if link_match:
        note_parts.append(f"Link: {link_match.group(1).strip()}")

    note = " · ".join(note_parts)

    items = [{
        "description": f"Hóa đơn điện tử số {invoice_number} ({serial}) - {seller_name}",
        "unit": "gói",
        "quantity": "1",
        "unit_price": str(total),
        "amount": str(total),
    }]

    return {
        "invoice_date": invoice_date,
        "serial": serial[:50],
        "invoice_number": invoice_number[:80],
        "seller_name": seller_name[:255],
        "seller_tax_code": seller_tax_code[:30],
        "seller_address": "",
        "buyer_name": "",
        "buyer_tax_code": "",
        "buyer_address": "",
        "payment_method": "Chuyển khoản",
        "items": items,
        "subtotal": total,
        "vat_percent": Decimal(0),
        "vat_amount": Decimal(0),
        "total": total,
        "note": note,
        "source_type": "text/email",
    }


# ---------------------------------------------------------------------------
# Attachment Unpacker & Extractor
# ---------------------------------------------------------------------------

def extract_invoices_from_attachments(
    filename: str,
    content: bytes,
) -> List[Tuple[str, str, bytes, Dict[str, Any]]]:
    """
    Examines file content (XML, ZIP containing XML, or PDF).
    Returns list of: (source_filename, mime_type, raw_bytes, parsed_invoice_dict).
    """
    results = []
    fname_lower = filename.lower()

    # 1. XML File
    if fname_lower.endswith(".xml") or content.startswith(b"<?xml") or b"<HDon" in content[:300]:
        parsed = parse_einvoice_xml(content)
        if parsed:
            results.append((filename, "application/xml", content, parsed))
            return results

    # 2. ZIP Archive (e.g. MISA or VNPT zip containing .xml and .pdf)
    if fname_lower.endswith(".zip") or content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                # Prioritize XML files in zip
                for inner_name in zf.namelist():
                    if inner_name.lower().endswith(".xml"):
                        inner_data = zf.read(inner_name)
                        parsed = parse_einvoice_xml(inner_data)
                        if parsed:
                            results.append((f"{filename} > {inner_name}", "application/xml", inner_data, parsed))
                # If no XML found in zip, check for PDF
                if not results:
                    for inner_name in zf.namelist():
                        if inner_name.lower().endswith(".pdf"):
                            inner_data = zf.read(inner_name)
                            pdf_res = _try_parse_pdf(inner_name, inner_data)
                            if pdf_res:
                                results.append(pdf_res)
        except Exception as exc:
            logger.warning("Failed to unpack zip %s: %s", filename, exc)

        if results:
            return results

    # 3. PDF File
    if fname_lower.endswith(".pdf") or content.startswith(b"%PDF"):
        pdf_res = _try_parse_pdf(filename, content)
        if pdf_res:
            results.append(pdf_res)

    return results


def _try_parse_pdf(filename: str, content: bytes) -> Optional[Tuple[str, str, bytes, Dict[str, Any]]]:
    try:
        from frontend.modules.invoices.extract import extract_invoice_text, parse_invoice_text
        text = extract_invoice_text(content, "application/pdf")
        parsed = parse_invoice_text(text)
        if parsed and parsed.get("invoice_number"):
            # Map into standard dict structure
            seller = parsed.get("seller") or {}
            buyer = parsed.get("buyer") or {}
            items = parsed.get("items") or []
            subtotal = sum((money(it.get("amount") or (money(it.get("quantity", 0), "q") * money(it.get("unit_price", 0), "p")), "a") for it in items), Decimal(0))
            vat_raw = parsed.get("vat_percent")
            vat_p = Decimal(str(vat_raw if vat_raw is not None else 10))
            vat_a = Decimal(str(parsed.get("vat_amount"))) if parsed.get("vat_amount") is not None else ((subtotal * vat_p / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if vat_p > 0 else Decimal(0))
            inv_dict = {
                "invoice_date": parsed.get("invoice_date") or date.today(),
                "serial": (parsed.get("serial") or "")[:50],
                "invoice_number": str(parsed.get("invoice_number"))[:80],
                "seller_name": (seller.get("name") or "Đơn vị bán trên hóa đơn PDF")[:255],
                "seller_tax_code": (seller.get("tax_code") or "")[:30],
                "seller_address": (seller.get("address") or "")[:1000],
                "buyer_name": (buyer.get("name") or "")[:255],
                "buyer_tax_code": (buyer.get("tax_code") or "")[:30],
                "buyer_address": (buyer.get("address") or "")[:1000],
                "payment_method": parsed.get("payment_method") or "Chuyển khoản",
                "items": items or [{"description": "Hóa đơn PDF", "unit": "gói", "quantity": "1", "unit_price": str(subtotal), "amount": str(subtotal)}],
                "subtotal": subtotal,
                "vat_percent": vat_p,
                "vat_amount": vat_a,
                "total": subtotal + vat_a,
                "source_type": "application/pdf",
            }
            return (filename, "application/pdf", content, inv_dict)
    except Exception as exc:
        logger.debug("PDF parse attempt failed for %s: %s", filename, exc)
    return None


# ---------------------------------------------------------------------------
# Database Persist & Deduplication
# ---------------------------------------------------------------------------

def _invoice_already_exists(
    invoice_number: str,
    serial: str,
    invoice_date: date,
    seller_name: str,
    buyer_company_id: Optional[int] = None,
) -> bool:
    """Check whether this invoice is already saved in the database for the given buyer company."""
    if not invoice_number:
        return False

    b_cond = "AND (buyer_company_id = %s OR buyer_company_id IS NULL)" if buyer_company_id else ""
    b_params = (buyer_company_id,) if buyer_company_id else ()

    row = db._execute_query(f"""
        SELECT id FROM public.transport_input_invoices
        WHERE invoice_number = %s
          AND (serial = %s OR %s = '' OR serial = '')
          AND (invoice_date = %s OR invoice_date IS NULL)
          {b_cond}
        LIMIT 1
    """, (invoice_number, serial, serial, invoice_date) + b_params, fetch=True, commit=False)
    if row:
        return True

    # Secondary check by invoice_number and seller_name if serial is blank
    if seller_name:
        row2 = db._execute_query(f"""
            SELECT id FROM public.transport_input_invoices
            WHERE invoice_number = %s AND seller_name = %s
              {b_cond}
            LIMIT 1
        """, (invoice_number, seller_name) + b_params, fetch=True, commit=False)
        if row2:
            return True
    return False


def save_synced_invoice(
    invoice_data: Dict[str, Any],
    attachment: Optional[Tuple[str, str, bytes]] = None,
    buyer_company_id: Optional[int] = None,
) -> Optional[int]:
    """Save an extracted invoice into transport_input_invoices if not already present."""

    inv_num = str(invoice_data.get("invoice_number", "")).strip()
    serial = str(invoice_data.get("serial", "")).strip()
    inv_date = invoice_data.get("invoice_date") or date.today()
    seller_name = str(invoice_data.get("seller_name", "")).strip()

    if not inv_num:
        return None

    buyer_name = str(invoice_data.get("buyer_name") or "").strip()
    buyer_tax = str(invoice_data.get("buyer_tax_code") or "").strip()
    buyer_address = str(invoice_data.get("buyer_address") or "").strip()

    # 1. Ưu tiên khớp công ty theo Mã số thuế người mua trên hóa đơn
    clean_inv_tax = buyer_tax.replace("-", "").strip() if buyer_tax else ""
    if clean_inv_tax:
        b_match = db._execute_query(
            "SELECT id, name, tax_code, address, email FROM public.invoice_companies WHERE REPLACE(tax_code, '-', '') = %s LIMIT 1",
            (clean_inv_tax,), fetch=True, commit=False,
        )
        if b_match:
            buyer_company_id = b_match["id"]
            active_comp = b_match
            if not buyer_name:
                buyer_name = b_match.get("name") or ""
            if not buyer_address:
                buyer_address = b_match.get("address") or ""

    # 2. Khớp đúng theo Email doanh nghiệp liên kết hộp thư Gmail
    connected_email = get_connected_gmail_email()
    email_company = get_company_by_enterprise_email(connected_email) if connected_email else None
    if not buyer_company_id and email_company:
        buyer_company_id = email_company["id"]
        active_comp = email_company

    # 3. Tải thông tin công ty nếu buyer_company_id đã được chỉ định
    if buyer_company_id and not active_comp:
        active_comp = db._execute_query(
            "SELECT id, name, tax_code, address, email FROM public.invoice_companies WHERE id = %s",
            (buyer_company_id,), fetch=True, commit=False,
        )

    # 4. Kiểm tra cách ly công ty: Nếu hóa đơn có MST người mua khác với active_comp,
    # kiểm tra xem MST đó có thuộc công ty nào khác trong hệ thống để tự chuyển về đúng công ty
    if active_comp and clean_inv_tax:
        clean_target_tax = (active_comp.get("tax_code") or "").replace("-", "").strip()
        if clean_target_tax and clean_inv_tax != clean_target_tax:
            other_target = db._execute_query(
                "SELECT id, name, tax_code, address FROM public.invoice_companies WHERE REPLACE(tax_code, '-', '') = %s LIMIT 1",
                (clean_inv_tax,), fetch=True, commit=False
            )
            if other_target:
                buyer_company_id = other_target["id"]
                active_comp = other_target
                logger.info(
                    "Tự động chuyển hóa đơn %s %s về đúng doanh nghiệp %s (MST: %s).",
                    serial, inv_num, other_target["name"], clean_inv_tax
                )
            else:
                logger.info(
                    "Bỏ qua hóa đơn %s %s: MST người mua %s không khớp công ty %s (MST: %s).",
                    serial, inv_num, clean_inv_tax, clean_target_tax
                )
                return None

    if _invoice_already_exists(inv_num, serial, inv_date, seller_name, buyer_company_id=buyer_company_id):
        logger.info("Invoice %s (%s) from '%s' already exists for company %s; skipping.",
                    inv_num, serial, seller_name, buyer_company_id)
        return None

    # Link or auto-create seller company
    seller_tax = str(invoice_data.get("seller_tax_code", "")).strip()
    seller_address = str(invoice_data.get("seller_address", "")).strip()
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
                logger.warning("Could not auto-insert company %s: %s", seller_tax, exc)

    items = invoice_data.get("items") or [{
        "description": f"Hóa đơn {serial} {inv_num}",
        "unit": "gói",
        "quantity": "1",
        "unit_price": str(invoice_data.get("total", 0)),
        "amount": str(invoice_data.get("total", 0)),
    }]

    subtotal = Decimal(str(invoice_data.get("subtotal", 0)))
    vat_percent = Decimal(str(invoice_data.get("vat_percent", 0)))
    vat_amount = Decimal(str(invoice_data.get("vat_amount", 0)))
    total = Decimal(str(invoice_data.get("total", 0)))

    att_filename = attachment[0] if attachment else invoice_data.get("source_filename")
    att_mimetype = attachment[1] if attachment else invoice_data.get("source_type")
    att_data = attachment[2] if attachment else None

    if buyer_tax and not buyer_company_id:
        b_match = db._execute_query(
            "SELECT id, name, tax_code, address FROM public.invoice_companies WHERE tax_code = %s LIMIT 1",
            (buyer_tax,), fetch=True, commit=False,
        )
        if b_match:
            buyer_company_id = b_match["id"]
            if not buyer_name:
                buyer_name = b_match.get("name") or ""
            if not buyer_address:
                buyer_address = b_match.get("address") or ""

    if active_comp:
        if not buyer_name:
            buyer_name = active_comp.get("name") or ""
        if not buyer_tax:
            buyer_tax = active_comp.get("tax_code") or ""
        if not buyer_address:
            buyer_address = active_comp.get("address") or ""
    elif buyer_company_id and (not buyer_name or not buyer_tax):
        b_comp = db._execute_query(
            "SELECT name, tax_code, address FROM public.invoice_companies WHERE id = %s",
            (buyer_company_id,), fetch=True, commit=False,
        )
        if b_comp:
            if not buyer_name:
                buyer_name = b_comp.get("name") or ""
            if not buyer_tax:
                buyer_tax = b_comp.get("tax_code") or ""
            if not buyer_address:
                buyer_address = b_comp.get("address") or ""

    row = db._execute_query("""
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
        inv_date, serial, inv_num, seller_company_id, seller_name,
        seller_tax, seller_address,
        buyer_name,
        buyer_tax,
        buyer_address,
        invoice_data.get("payment_method", "Chuyển khoản"),
        json.dumps(items, ensure_ascii=False),
        subtotal, vat_percent, vat_amount, total,
        invoice_data.get("note", "Đồng bộ từ Email"),
        buyer_company_id,
        att_filename, att_mimetype,
        Binary(att_data) if att_data else None,
    ), fetch=True, commit=True)

    if row:
        logger.info("Successfully imported input invoice id=%s (%s %s)", row["id"], serial, inv_num)
        return row["id"]
    return None


# ---------------------------------------------------------------------------
# Gmail API Synchronization (Official Google Cloud)
# ---------------------------------------------------------------------------

async def sync_invoices_from_gmail_api(
    max_messages: int = 40,
    search_query: str = "",
    buyer_company_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Sync invoices using the official Gmail REST API with OAuth2 access token.
    """
    access_token = await get_valid_gmail_access_token()
    if not access_token:
        raise ValueError("Chưa kết nối tài khoản Gmail qua Google OAuth (thiếu Refresh Token).")

    # Tự động gán buyer_company_id theo Email doanh nghiệp đã đăng ký nếu chưa có
    connected_email = get_connected_gmail_email()
    email_company = get_company_by_enterprise_email(connected_email) if connected_email else None
    if not buyer_company_id and email_company:
        buyer_company_id = email_company["id"]
        logger.info("Gmail Sync: Khớp doanh nghiệp %s (id: %s) theo email %s",
                    email_company["name"], email_company["id"], connected_email)

    headers = {"Authorization": f"Bearer {access_token}"}

    # Build smart query for invoices in Vietnam
    base_q = 'has:attachment (filename:pdf OR filename:xml OR filename:zip) OR "hóa đơn" OR "hoá đơn" OR "meInvoice" OR "PETROLIMEX" OR "GTGT"'
    q = f"({search_query}) AND ({base_q})" if search_query else base_q

    async with httpx.AsyncClient(timeout=20.0) as client:
        # 1. List messages
        list_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={urllib.parse.quote(q)}&maxResults={max_messages}"
        list_resp = await client.get(list_url, headers=headers)
        if list_resp.status_code != 200:
            raise ValueError(f"Lỗi gọi Gmail API: {list_resp.text}")

        data = list_resp.json()
        messages = data.get("messages", [])

        scanned_count = len(messages)
        imported_count = 0
        existing_count = 0
        details = []

        for msg_item in messages:
            msg_id = msg_item["id"]
            msg_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=full"
            msg_resp = await client.get(msg_url, headers=headers)
            if msg_resp.status_code != 200:
                continue

            msg_data = msg_resp.json()
            payload = msg_data.get("payload", {})
            headers_list = payload.get("headers", [])

            subject = next((h["value"] for h in headers_list if h["name"].lower() == "subject"), "")
            sender = next((h["value"] for h in headers_list if h["name"].lower() == "from"), "")

            # Extract body text
            body_text = _extract_body_text_from_gmail_payload(payload)

            # Find attachments
            attachments = _collect_gmail_attachments(payload)

            invoices_found = []

            # 1. Process attachments
            for att_info in attachments:
                att_id = att_info["id"]
                att_name = att_info["filename"]
                att_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}/attachments/{att_id}"
                att_resp = await client.get(att_url, headers=headers)
                if att_resp.status_code != 200:
                    continue
                att_data_b64 = att_resp.json().get("data", "")
                att_bytes = base64.urlsafe_b64decode(att_data_b64.encode("utf-8"))

                extracted_list = extract_invoices_from_attachments(att_name, att_bytes)
                for src_fname, src_mime, raw_b, inv_d in extracted_list:
                    invoices_found.append((src_fname, src_mime, raw_b, inv_d))

            # 2. If no attachment had an invoice, check body text
            if not invoices_found:
                body_inv = parse_invoice_from_email_text(subject, body_text, sender)
                if body_inv and body_inv.get("invoice_number"):
                    invoices_found.append(("email_body.txt", "text/plain", body_text.encode("utf-8"), body_inv))

            # 3. Save to database
            for src_fname, src_mime, raw_b, inv_d in invoices_found:
                inv_id = save_synced_invoice(inv_d, attachment=(src_fname, src_mime, raw_b), buyer_company_id=buyer_company_id)
                num = inv_d.get("invoice_number")
                seller = inv_d.get("seller_name")
                tot = inv_d.get("total")
                if inv_id:
                    imported_count += 1
                    details.append({
                        "status": "new",
                        "id": inv_id,
                        "invoice_number": num,
                        "seller_name": seller,
                        "total": float(tot) if tot else 0,
                        "subject": subject,
                    })
                else:
                    existing_count += 1
                    details.append({
                        "status": "exists",
                        "invoice_number": num,
                        "seller_name": seller,
                        "total": float(tot) if tot else 0,
                        "subject": subject,
                    })

    return {
        "ok": True,
        "scanned": scanned_count,
        "new_saved": imported_count,
        "existing": existing_count,
        "details": details,
        "provider": "gmail_api",
    }


def _extract_body_text_from_gmail_payload(payload: dict) -> str:
    parts = [payload]
    texts = []
    while parts:
        curr = parts.pop(0)
        mime = curr.get("mimeType", "")
        if mime == "text/plain" and "data" in curr.get("body", {}):
            try:
                b = base64.urlsafe_b64decode(curr["body"]["data"].encode("utf-8"))
                texts.append(b.decode("utf-8", errors="ignore"))
            except Exception:
                pass
        elif mime == "text/html" and "data" in curr.get("body", {}):
            try:
                b = base64.urlsafe_b64decode(curr["body"]["data"].encode("utf-8"))
                clean_html = re.sub(r"<[^>]+>", " ", b.decode("utf-8", errors="ignore"))
                texts.append(clean_html)
            except Exception:
                pass
        if "parts" in curr:
            parts.extend(curr["parts"])
    return "\n".join(texts)


def _collect_gmail_attachments(payload: dict) -> list:
    results = []
    parts = [payload]
    while parts:
        curr = parts.pop(0)
        fname = curr.get("filename", "")
        att_id = curr.get("body", {}).get("attachmentId")
        if fname and att_id:
            results.append({"id": att_id, "filename": fname, "mimeType": curr.get("mimeType", "")})
        if "parts" in curr:
            parts.extend(curr["parts"])
    return results


# ---------------------------------------------------------------------------
# Gmail IMAP Synchronization (App Password Connection)
# ---------------------------------------------------------------------------

def _decode_mime_header(header_val: str) -> str:
    if not header_val:
        return ""
    decoded_parts = decode_header(header_val)
    text = ""
    for b_or_s, enc in decoded_parts:
        if isinstance(b_or_s, bytes):
            text += b_or_s.decode(enc or "utf-8", errors="ignore")
        else:
            text += str(b_or_s)
    return text


def sync_invoices_from_imap(
    email_address: str,
    app_password: str,
    max_messages: int = 30,
    search_keyword: str = "",
    buyer_company_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Sync invoices directly using Gmail IMAP over SSL with an App Password.
    """
    clean_pwd = app_password.replace(" ", "").strip()
    clean_user = email_address.strip()
    if not clean_user or not clean_pwd:
        raise ValueError("Vui lòng nhập Email và Mật khẩu ứng dụng (App Password).")

    # Tự động gán buyer_company_id theo Email doanh nghiệp đã đăng ký nếu chưa có
    email_company = get_company_by_enterprise_email(clean_user) if clean_user else None
    if not buyer_company_id and email_company:
        buyer_company_id = email_company["id"]
        logger.info("IMAP Sync: Khớp doanh nghiệp %s (id: %s) theo email %s",
                    email_company["name"], email_company["id"], clean_user)

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(clean_user, clean_pwd)
    except Exception as exc:
        logger.error("IMAP login failed: %s", exc)
        raise ValueError(f"Đăng nhập Gmail IMAP thất bại: {exc}. Vui lòng kiểm tra lại Email và Mật khẩu ứng dụng.")

    try:
        mail.select("INBOX")
        status, search_data = mail.search(None, "ALL")
        if status != "OK" or not search_data or not search_data[0]:
            return {"ok": True, "scanned": 0, "new_saved": 0, "existing": 0, "details": []}

        msg_ids = search_data[0].split()
        target_ids = msg_ids[-max_messages:]
        target_ids.reverse()

        scanned_count = len(target_ids)
        imported_count = 0
        existing_count = 0
        details = []

        for m_id in target_ids:
            status, data = mail.fetch(m_id, "(RFC822)")
            if status != "OK" or not data or not data[0]:
                continue

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject = _decode_mime_header(msg.get("Subject", ""))
            sender = _decode_mime_header(msg.get("From", ""))

            # Filter if search keyword specified
            if search_keyword and search_keyword.lower() not in (subject + sender).lower():
                continue

            # Extract body & attachments
            body_parts = []
            invoices_found = []

            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition") or "")
                filename = part.get_filename()

                if filename:
                    filename = _decode_mime_header(filename)
                    payload = part.get_payload(decode=True)
                    if payload:
                        extracted = extract_invoices_from_attachments(filename, payload)
                        invoices_found.extend(extracted)
                elif content_type in ("text/plain", "text/html") and "attachment" not in disposition:
                    payload = part.get_payload(decode=True)
                    if payload:
                        t = payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
                        if content_type == "text/html":
                            t = re.sub(r"<[^>]+>", " ", t)
                        body_parts.append(t)

            full_body = "\n".join(body_parts)

            # If no attachment produced an invoice, parse from body
            if not invoices_found:
                inv_from_body = parse_invoice_from_email_text(subject, full_body, sender)
                if inv_from_body and inv_from_body.get("invoice_number"):
                    invoices_found.append(("email_body.txt", "text/plain", full_body.encode("utf-8"), inv_from_body))

            # Save extracted invoices
            for src_fname, src_mime, raw_b, inv_d in invoices_found:
                inv_id = save_synced_invoice(inv_d, attachment=(src_fname, src_mime, raw_b), buyer_company_id=buyer_company_id)
                num = inv_d.get("invoice_number")
                seller = inv_d.get("seller_name")
                tot = inv_d.get("total")
                if inv_id:
                    imported_count += 1
                    details.append({
                        "status": "new",
                        "id": inv_id,
                        "invoice_number": num,
                        "seller_name": seller,
                        "total": float(tot) if tot else 0,
                        "subject": subject,
                    })
                else:
                    existing_count += 1
                    details.append({
                        "status": "exists",
                        "invoice_number": num,
                        "seller_name": seller,
                        "total": float(tot) if tot else 0,
                        "subject": subject,
                    })

        # Save email address for convenience
        set_many_app_settings({"gmail_sync_email": clean_user})

        return {
            "ok": True,
            "scanned": scanned_count,
            "new_saved": imported_count,
            "existing": existing_count,
            "details": details,
            "provider": "imap",
        }
    finally:
        try:
            mail.close()
            mail.logout()
        except Exception:
            pass
