"""Read issued BKAV invoices and attach selected invoices to cargo history."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import httpx
import base64
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID, uuid4
from xml.etree import ElementTree

from pathlib import Path
from psycopg2.extras import RealDictCursor

from backend.services.database import db
from frontend.modules.invoices.bkav_push import _send_command, configuration
from frontend.modules.invoices.companies import normalize_tax_code
from frontend.modules.invoices.export import CRANE_BY_SHIFT_SERVICE, is_crane_by_shift_service
from frontend.modules.transport.catalog import list_items as list_catalog_items


_ROUTE = re.compile(r"\btừ\s+(.+?)\s+(?:đến|đi|về|sang)\s+(.+)$", re.IGNORECASE)
_PLATE_FIVE_DIGIT = re.compile(
    r"(?<![A-Z0-9])(\d{2})\s*[- ]?\s*([A-Z]{1,2})\s*[- ]?\s*"
    r"(\d{3})\s*[. -]?\s*(\d{2})(?!\d)", re.IGNORECASE,
)
_PLATE_FOUR_DIGIT = re.compile(
    r"(?<![A-Z0-9])(\d{2})\s*[- ]?\s*([A-Z]{1,2})\s*[- ]?\s*"
    r"(\d{4})(?!\d)", re.IGNORECASE,
)
_SERVICE = re.compile(r"\s+(?:từ|tại)\s+", re.IGNORECASE)
_MAX_PAGES = 50
_REPEATED_TRIP_UNITS = {"chuyến", "lượt", "ca cẩu"}
_MAX_REPEATED_TRIPS = 100
logger = logging.getLogger(__name__)


def _find_route(value: str):
    route = _ROUTE.search(value)
    if route:
        return route
    # Accept normal Vietnamese text as well as the legacy mojibake found in old records.
    return re.search(
        r"\bt\u1eeb\s+(.+?)\s+(?:\u0111\u1ebfn|\u0111i|v\u1ec1|sang)\s+(.+)$",
        value, re.IGNORECASE,
    )


def _fold_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _is_crane_invoice_line(detail: dict, description: str) -> bool:
    folded = _fold_search_text(description)
    if any(marker in folded for marker in (
        "cuoc cau", "ca cau", "dich vu cau", "thue cau", "thue xe cau", "cong cau", "tien cau"
    )):
        return True
    # Some older catalog rows store the service label in its mojibake form.
    legacy_marker = " ".join(CRANE_BY_SHIFT_SERVICE.split()[:2]).casefold()
    if legacy_marker in description.casefold():
        return True
    unit = _fold_search_text(detail.get("UnitName") or "")
    return unit in {"ca cau", "ca"}

def _fuel_price_for_date(target_date):
    try:
        d = target_date.isoformat() if hasattr(target_date, "isoformat") else str(target_date)
        data = httpx.get(f"https://giaxanghomnay.com/api/pvdate/{d}", timeout=8).json()
        rows = data[0] if isinstance(data, list) and data and isinstance(data[0], list) else data
        rows = [r for r in (rows or []) if isinstance(r, dict) and isinstance(r.get("title"), str)]
        diesel = next((r for r in rows if "DO" in r["title"] and "0,05" in r["title"]), next((r for r in rows if "DO" in r["title"]), None))
        raw = re.sub(r"[^0-9]", "", str((diesel or {}).get("zone1_price") or ""))
        return Decimal(raw) if raw else None
    except Exception:
        return None


def _number(value) -> Decimal:
    try:
        result = Decimal(str(value or 0))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Hóa đơn trả về số tiền không hợp lệ.") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Hóa đơn trả về số tiền không hợp lệ.")
    return result


def _plate_key(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _extract_plates(value: str | None) -> list[str]:
    """Extract and normalize Vietnamese plates despite omitted separators or spaces."""
    text = str(value or "")
    found = []
    for match in _PLATE_FIVE_DIGIT.finditer(text):
        found.append(f"{match.group(1)}{match.group(2)}-{match.group(3)}.{match.group(4)}".upper())
    for match in _PLATE_FOUR_DIGIT.finditer(text):
        found.append(f"{match.group(1)}{match.group(2)}-{match.group(3)}".upper())
    unique = {}
    for plate in found:
        unique.setdefault(_plate_key(plate), plate)
    return list(unique.values())


def _clean_destination(value: str) -> str:
    return re.sub(r"\s*\((?:Biển kiểm soát|BKS)[^)]*\)\s*$", "", str(value or ""), flags=re.IGNORECASE).strip()


def _xml_name(element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _invoice_line_plates(invoice_code: str, config: dict, line_count: int,
                         descriptions: list[str] | None = None) -> list[str | None]:
    """Read the per-line transport plates from the issued invoice XML."""
    if not invoice_code:
        raise ValueError("Thiếu mã tra cứu để đọc biển số trên hóa đơn.")
    response = _send_command({"CmdType": 813, "CommandObject": invoice_code}, config)
    if response.get("Status") != 0 or not response.get("isOk", False):
        raise ValueError("Không tải được dữ liệu gốc của hóa đơn.")
    payload = response.get("Object")
    if isinstance(payload, str):
        payload = json.loads(payload)
    encoded = payload.get("XML") if isinstance(payload, dict) else None
    if not encoded:
        raise ValueError("Thiếu dữ liệu gốc của hóa đơn.")
    root = ElementTree.fromstring(base64.b64decode(encoded, validate=True))
    lines = [element for element in root.iter() if _xml_name(element) == "HHDVu"]
    if len(lines) != line_count:
        raise ValueError("Số dòng trong dữ liệu gốc không khớp chi tiết hóa đơn.")
    plates: list[str | None] = []
    for index, line in enumerate(lines):
        if descriptions is not None:
            xml_description = next((element.text or "" for element in line.iter()
                                    if _xml_name(element) == "THHDVu"), "")
            normal = lambda value: " ".join(value.split()).casefold()
            if normal(xml_description) != normal(descriptions[index]):
                raise ValueError("Thứ tự dòng trong dữ liệu gốc không khớp chi tiết hóa đơn.")
        found = []
        for info in line.iter():
            if _xml_name(info) != "TTin":
                continue
            fields = {_xml_name(child): (child.text or "").strip() for child in info}
            if fields.get("TTruong") == "BKSPTVChuyen":
                found.extend(_extract_plates(fields.get("DLieu", "")))
        if not found:
            found.extend(_extract_plates(" ".join(element.text or "" for element in line.iter())))
        unique = {_plate_key(plate): plate for plate in found}
        plates.append(next(iter(unique.values()), None))
    return plates


def parse_invoice(row: dict, *, services: list[str] | None = None,
                  cargo_types: list[str] | None = None) -> dict | None:
    """Only a published, ordinary VND invoice can become a cargo trip."""
    if not isinstance(row, dict) or not isinstance(row.get("Invoice"), dict):
        return None
    inv = row["Invoice"]
    if inv.get("InvoiceStatusID") not in (2, 6) or inv.get("InvoiceTypeID") != 1:
        return None
    if str(inv.get("CurrencyID") or "VND").upper() != "VND":
        return None
    try:
        guid = str(UUID(str(inv.get("InvoiceGUID") or "")))
        day = date.fromisoformat(str(inv["InvoiceDate"])[:10])
        invoice_no = int(inv.get("InvoiceNo") or 0)
    except (ValueError, TypeError, KeyError):
        return None
    if invoice_no <= 0:
        return None
    details = row.get("ListInvoiceDetailsWS") or []
    if not isinstance(details, list):
        return None
    detail_rows = [item for item in details if isinstance(item, dict)
                   and not item.get("IsDiscount") and item.get("ItemTypeID", 0) != 4
                   and str(item.get("ItemName") or "").strip()]
    lines = [str(item["ItemName"]).strip() for item in detail_rows]
    if not lines:
        return None
    subtotal = _number(inv.get("SumItemAmount")) - _number(inv.get("SumDiscountAmount"))
    gross = _number(inv.get("SumPaymentAmount"))
    if subtotal <= 0 or gross <= 0:
        return None
    first = lines[0]
    route = _find_route(first)
    origin, destination = (route.group(1).strip(), _clean_destination(route.group(2))) if route else ("", "")
    prefix = _SERVICE.split(first, maxsplit=1)[0].strip()
    service = max((name for name in (services or []) if prefix.casefold().startswith(name.casefold())),
                  key=len, default=prefix or "Cước vận chuyển")
    remainder = prefix[len(service):].strip() if prefix.casefold().startswith(service.casefold()) else ""
    cargo = next((name for name in (cargo_types or []) if remainder.casefold() == name.casefold()), "")
    crane_by_shift = (not any(_find_route(line) for line in lines)
                      and all(_is_crane_invoice_line(detail, line)
                              for detail, line in zip(detail_rows, lines)))
    if crane_by_shift:
        service = next((name for name in (services or []) if is_crane_by_shift_service(name)),
                       CRANE_BY_SHIFT_SERVICE)
        cargo = ""
    segments = []
    for source_line_number, (detail, line) in enumerate(zip(detail_rows, lines), 1):
        leg = _find_route(line)
        raw_amount = next((detail[key] for key in ("Amount", "ItemAmount", "TotalAmount")
                           if detail.get(key) is not None), None)
        if raw_amount is None and detail.get("Price") is not None and detail.get("Qty") is not None:
            raw_amount = _number(detail["Price"]) * _number(detail["Qty"])
        amount = _number(raw_amount) if raw_amount is not None else Decimal(0)
        is_crane_line = _is_crane_invoice_line(detail, line)
        is_crane_segment = not leg and (is_crane_line or crane_by_shift)
        if (not leg and not is_crane_segment) or amount <= 0 or amount != amount.to_integral_value():
            segments = []
            break
        qty = detail.get("Qty")
        unit = str(detail.get("UnitName") or "").strip().casefold()
        repeats = 1
        folded_unit = _fold_search_text(unit)
        repeated_unit = unit in _REPEATED_TRIP_UNITS or folded_unit in {"chuyen", "luot", "ca cau", "ca"}
        if (repeated_unit or is_crane_segment or crane_by_shift) and qty not in (None, ""):
            quantity = _number(qty)
            if quantity < 1 or quantity > _MAX_REPEATED_TRIPS or quantity != quantity.to_integral_value():
                segments = []
                break
            repeats = int(quantity)
        if amount % repeats:
            segments = []
            break
        line_plates = _extract_plates(line)
        line_prefix = _SERVICE.split(line, maxsplit=1)[0].strip()
        line_service = max((name for name in (services or []) if line_prefix.casefold().startswith(name.casefold())),
                           key=len, default=service)
        line_cargo = line_prefix[len(line_service):].strip() if line_prefix.casefold().startswith(line_service.casefold()) else ""
        cargo_name = next((name for name in (cargo_types or []) if line_cargo.casefold() == name.casefold()), "")
        tax_amount = _number(detail.get("TaxAmount")) if detail.get("TaxAmount") is not None else None
        unit_tax = ((tax_amount / repeats).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    if tax_amount is not None else None)
        for repeat in range(repeats):
            split_tax = (tax_amount - unit_tax * (repeats - 1) if repeat == repeats - 1
                         else unit_tax) if unit_tax is not None else None
            segments.append({"invoice_line_number": len(segments) + 1,
                             "source_line_number": source_line_number,
                             "origin": leg.group(1).strip() if leg else "",
                             "destination": _clean_destination(leg.group(2)) if leg else "",
                             "vehicle_plate": line_plates[0] if line_plates else None,
                             "description": line, "amount": int(amount / repeats), "distance_km": None,
                             "tax_amount": str(split_tax) if split_tax is not None else None,
                             "cargo_description": None if is_crane_segment else cargo_name or cargo or None,
                             "cargo_weight_tons": str(qty).strip() if unit in {"tấn", "tan"}
                             and qty not in (None, "") else None,
                             "is_crane": is_crane_segment})
    if segments and sum((Decimal(segment["amount"]) for segment in segments), Decimal(0)) != subtotal:
        segments = []
    if segments:
        route_legs = [s for s in segments if s.get("origin") and s.get("destination")]
        if route_legs:
            origin, destination = route_legs[0]["origin"], route_legs[-1]["destination"]
        else:
            origin, destination = "", ""
    pure_crane = bool(segments and all(s.get("is_crane") for s in segments))
    rates = {_number(item.get("TaxRate")) for item in details
             if isinstance(item, dict) and not item.get("IsDiscount")
             and item.get("ItemTypeID", 0) != 4 and item.get("TaxRate") is not None}
    vat_rate = next(iter(rates)) / 100 if len(rates) == 1 else None
    return {
        "guid": guid, "date": day, "number": invoice_no,
        "invoice_code": str(inv.get("InvoiceCode") or "").strip(),
        "serial": str(inv.get("InvoiceSerial") or "").strip(),
        "buyer_name": str(inv.get("BuyerUnitName") or inv.get("BuyerName") or "").strip(),
        "buyer_tax_code": str(inv.get("BuyerTaxCode") or "").strip(),
        "buyer_address": str(inv.get("BuyerAddress") or "").strip(),
        "buyer_email": str(inv.get("ReceiverEmail") or "").strip(),
        "seller_tax_code": str(inv.get("SellerTaxCode") or "").strip(),
        "service": service[:255], "cargo": cargo, "description": "\n".join(lines),
        "is_crane_by_shift": pure_crane,
        "first_line": first,
        "segments": segments, "line_count": len(segments) if segments else len(lines),
        "source_line_count": len(lines), "source_descriptions": lines,
        "origin": origin[:1000], "destination": destination[:1000],
        "subtotal": subtotal, "gross": gross, "vat_rate": vat_rate,
        "payment_method": "TM" if inv.get("PayMethodID") == 1 else "CK",
    }


def fetch_invoices(start: date, end: date, seller_tax: str | None = None) -> tuple[list[dict], int]:
    """BKAV CmdType 853 uses an inclusive end date and one-based pages."""
    config = configuration(seller_tax)
    if end <= start or (end - start).days > 366:
        raise ValueError("Khoảng thời gian đồng bộ hóa đơn không hợp lệ.")
    result: dict[str, dict] = {}
    services = [row["name"] for row in list_catalog_items("service")]
    cargo_types = [row["name"] for row in list_catalog_items("cargo_type")]
    ignored = 0
    for page in range(1, _MAX_PAGES + 1):
        response = _send_command({"CmdType": 853, "CommandObject": {
            "InvoiceDateFrom": start.isoformat(),
            "InvoiceDateTo": (end - timedelta(days=1)).isoformat(),
            "PageNumber": page,
        }}, config)
        if response.get("Status") != 0 or not response.get("isOk", False):
            raise ValueError(str(response.get("Object") or "Không thể lấy danh sách hóa đơn.")[:300])
        rows = response.get("Object")
        if isinstance(rows, str):
            rows = json.loads(rows)
        if rows is None or rows == []:
            break
        if not isinstance(rows, list):
            raise ValueError("Danh sách hóa đơn trả về không đúng định dạng.")
        for row in rows:
            invoice = parse_invoice(row, services=services, cargo_types=cargo_types)
            if invoice and start <= invoice["date"] < end:
                seller_tax = invoice["seller_tax_code"]
                if seller_tax and seller_tax != config["seller_tax"]:
                    ignored += 1
                    continue
                if invoice["segments"]:
                    try:
                        xml_plates = _invoice_line_plates(
                            invoice["invoice_code"], config, invoice["source_line_count"],
                            invoice["source_descriptions"])
                        for segment in invoice["segments"]:
                            xml_plate = xml_plates[segment["source_line_number"] - 1]
                            text_plate = segment.get("vehicle_plate")
                            segment["vehicle_plate"] = xml_plate or text_plate
                    except Exception as exc:
                        if invoice.get("is_crane_by_shift"):
                            invoice["plate_lookup_error"] = str(exc)
                        else:
                            invoice["plate_error"] = str(exc)
                result[invoice["guid"]] = invoice
            else:
                ignored += 1
    else:
        raise ValueError("Có quá nhiều trang hóa đơn; hãy chọn khoảng thời gian ngắn hơn.")
    return sorted(result.values(), key=lambda item: (item["date"], item["number"])), ignored




def vehicle_map() -> dict[str, dict]:
    vehicles = db.select("plate_whitelist", order_by="plate_number ASC") or []
    return {_plate_key(row.get("plate_number")): row for row in vehicles if row.get("plate_number")}


def _line_vehicle(invoice: dict, target: dict | None, line: dict,
                  vehicles: dict[str, dict]) -> dict | None:
    """Link recognized crane plates to fleet rows without treating cranes as trips."""
    if target:
        return target
    if line.get("vehicle_plate"):
        return vehicles.get(_plate_key(line["vehicle_plate"]))
    return None


def _line_vehicle_id(invoice: dict, target: dict | None, line: dict,
                     vehicles: dict[str, dict]) -> int | None:
    vehicle = _line_vehicle(invoice, target, line, vehicles)
    return vehicle["id"] if vehicle else None


_PROJECT_DIR = Path(__file__).resolve().parents[3]
_BKAV_UPLOAD_DIR = _PROJECT_DIR / "upload" / "bkav_invoices"




def _stored_bkav_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    path = (_PROJECT_DIR / raw_path.replace("\\", "/")).resolve()
    if not path.is_relative_to(_BKAV_UPLOAD_DIR.resolve()):
        return None
    return path


def stored_bkav_invoice_files(invoice_guid: str) -> tuple[Path | None, Path | None]:
    """Return only files recorded for this invoice and still present on disk."""
    guid = str(UUID(str(invoice_guid)))
    row = db._execute_query(
        "SELECT image_path, pdf_path FROM public.bkav_invoice_files WHERE invoice_guid = %s",
        (guid,), fetch=True, commit=False,
    )
    if not isinstance(row, dict):
        link = db._execute_query(
            "SELECT partner_invoice_id FROM public.bkav_saved_invoice_links WHERE invoice_guid = %s",
            (guid,), fetch=True, commit=False,
        )
        if isinstance(link, dict) and link.get("partner_invoice_id"):
            push = db._execute_query(
                "SELECT invoice_guid FROM public.bkav_invoice_pushes WHERE trip_id = %s",
                (link["partner_invoice_id"],), fetch=True, commit=False,
            )
            if isinstance(push, dict) and push.get("invoice_guid"):
                row = db._execute_query(
                    "SELECT image_path, pdf_path FROM public.bkav_invoice_files WHERE invoice_guid = %s",
                    (str(push["invoice_guid"]),), fetch=True, commit=False,
                )
        else:
            push = db._execute_query(
                "SELECT trip_id FROM public.bkav_invoice_pushes WHERE invoice_guid = %s",
                (guid,), fetch=True, commit=False,
            )
            if isinstance(push, dict) and push.get("trip_id"):
                link = db._execute_query(
                    "SELECT invoice_guid FROM public.bkav_saved_invoice_links WHERE partner_invoice_id = %s",
                    (push["trip_id"],), fetch=True, commit=False,
                )
                if isinstance(link, dict) and link.get("invoice_guid"):
                    row = db._execute_query(
                        "SELECT image_path, pdf_path FROM public.bkav_invoice_files WHERE invoice_guid = %s",
                        (str(link["invoice_guid"]),), fetch=True, commit=False,
                    )
    if not isinstance(row, dict):
        legacy_png = _BKAV_UPLOAD_DIR / f"{guid}.png"
        legacy_pdf = _BKAV_UPLOAD_DIR / f"{guid}.pdf"
        if legacy_png.is_file() and legacy_png.stat().st_size:
            db._execute_query("""
                INSERT INTO public.bkav_invoice_files (invoice_guid, image_path, pdf_path)
                VALUES (%s, %s, %s) ON CONFLICT (invoice_guid) DO NOTHING
            """, (guid, legacy_png.relative_to(_PROJECT_DIR).as_posix(),
                  legacy_pdf.relative_to(_PROJECT_DIR).as_posix() if legacy_pdf.is_file() else None),
                commit=True)
            row = db._execute_query(
                "SELECT image_path, pdf_path FROM public.bkav_invoice_files WHERE invoice_guid = %s",
                (guid,), fetch=True, commit=False,
            )
    image_path = _stored_bkav_path(row.get("image_path")) if isinstance(row, dict) else None
    pdf_path = _stored_bkav_path(row.get("pdf_path")) if isinstance(row, dict) else None
    return (image_path if image_path and image_path.is_file() and image_path.stat().st_size else None,
            pdf_path if pdf_path and pdf_path.is_file() and pdf_path.stat().st_size else None)


def stored_bkav_invoice_image_url(invoice_guid: str) -> str | None:
    image_path, _ = stored_bkav_invoice_files(invoice_guid)
    if not image_path:
        return None
    return f"/upload/bkav_invoices/{image_path.name}?v={image_path.stat().st_mtime_ns}"


def delete_stored_bkav_invoice_files(invoice_guid: str) -> None:
    guid = str(UUID(str(invoice_guid)))
    image_path, pdf_path = stored_bkav_invoice_files(guid)
    db._execute_query("DELETE FROM public.bkav_invoice_files WHERE invoice_guid = %s",
                      (guid,), commit=True)
    link = db._execute_query(
        "SELECT partner_invoice_id FROM public.bkav_saved_invoice_links WHERE invoice_guid = %s",
        (guid,), fetch=True, commit=False,
    )
    if isinstance(link, dict) and link.get("partner_invoice_id"):
        push = db._execute_query(
            "SELECT invoice_guid FROM public.bkav_invoice_pushes WHERE trip_id = %s",
            (link["partner_invoice_id"],), fetch=True, commit=False,
        )
        if isinstance(push, dict) and push.get("invoice_guid"):
            db._execute_query("DELETE FROM public.bkav_invoice_files WHERE invoice_guid = %s",
                              (str(push["invoice_guid"]),), commit=True)
    else:
        push = db._execute_query(
            "SELECT trip_id FROM public.bkav_invoice_pushes WHERE invoice_guid = %s",
            (guid,), fetch=True, commit=False,
        )
        if isinstance(push, dict) and push.get("trip_id"):
            link = db._execute_query(
                "SELECT invoice_guid FROM public.bkav_saved_invoice_links WHERE partner_invoice_id = %s",
                (push["trip_id"],), fetch=True, commit=False,
            )
            if isinstance(link, dict) and link.get("invoice_guid"):
                db._execute_query("DELETE FROM public.bkav_invoice_files WHERE invoice_guid = %s",
                                  (str(link["invoice_guid"]),), commit=True)
    for path in (image_path, pdf_path):
        if path:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove BKAV invoice file %s", path, exc_info=True)


def get_or_fetch_bkav_invoice_files(invoice_guid: str, seller_tax: str | None = None,
                                    *, force_refresh: bool = False) -> tuple[Path, Path]:
    """Retrieve or download (and render) BKAV invoice PDF and PNG for invoice_guid.
    Returns (png_path, pdf_path).
    """
    invoice_guid = str(UUID(str(invoice_guid)))
    old_png, old_pdf = stored_bkav_invoice_files(invoice_guid)
    if not force_refresh and old_png and old_pdf:
        return old_png, old_pdf

    inv = db._execute_query(
        "SELECT * FROM public.bkav_invoices WHERE invoice_guid = %s",
        (str(invoice_guid),), fetch=True, commit=False,
    )
    if not isinstance(inv, dict):
        inv = None
    push = None
    link = db._execute_query(
        "SELECT partner_invoice_id FROM public.bkav_saved_invoice_links WHERE invoice_guid = %s",
        (str(invoice_guid),), fetch=True, commit=False,
    )
    if not isinstance(link, dict):
        link = None
    if link:
        push = db._execute_query(
            "SELECT * FROM public.bkav_invoice_pushes WHERE trip_id = %s",
            (link["partner_invoice_id"],), fetch=True, commit=False,
        )
        if not isinstance(push, dict):
            push = None
    if not push:
        push = db._execute_query(
            "SELECT * FROM public.bkav_invoice_pushes WHERE invoice_guid = %s",
            (str(invoice_guid),), fetch=True, commit=False,
        )
        if not isinstance(push, dict):
            push = None
        if push and not inv:
            link = db._execute_query(
                "SELECT invoice_guid FROM public.bkav_saved_invoice_links WHERE partner_invoice_id = %s",
                (push["trip_id"],), fetch=True, commit=False,
            )
            if not isinstance(link, dict):
                link = None
            if link:
                inv = db._execute_query(
                    "SELECT * FROM public.bkav_invoices WHERE invoice_guid = %s",
                    (str(link["invoice_guid"]),), fetch=True, commit=False,
                )
                if not isinstance(inv, dict):
                    inv = None

    if not inv and not push:
        raise ValueError("Không tìm thấy thông tin hóa đơn trong hệ thống.")

    invoice_number = None
    invoice_serial = None
    invoice_date = None
    invoice_code = None

    if inv:
        invoice_number = inv.get("invoice_number")
        invoice_serial = inv.get("invoice_serial")
        invoice_date = inv.get("invoice_date")
        invoice_code = inv.get("invoice_code")
        if not seller_tax and inv.get("seller_company_id"):
            company = db._execute_query(
                "SELECT tax_code FROM public.invoice_companies WHERE id = %s",
                (inv["seller_company_id"],), fetch=True, commit=False,
            )
            if isinstance(company, dict):
                seller_tax = company.get("tax_code")

    if push:
        seller_tax = seller_tax or push.get("seller_tax_code")
        invoice_number = invoice_number or push.get("invoice_no")
        invoice_serial = invoice_serial or push.get("invoice_serial")
        invoice_date = invoice_date or push.get("attempted_at")

    partner_invoice_id = None
    if isinstance(link, dict) and link.get("partner_invoice_id"):
        partner_invoice_id = link["partner_invoice_id"]
    elif isinstance(push, dict) and push.get("trip_id"):
        partner_invoice_id = push["trip_id"]

    bkav_guid = str(push["invoice_guid"]) if (isinstance(push, dict) and push.get("invoice_guid")) else str(invoice_guid)

    config = configuration(seller_tax)

    pdf_b64 = None

    # Step 1: Attempt direct fetch via CmdType 851 using BKAV's InvoiceGUID
    if bkav_guid:
        try:
            cmd_res = _send_command({"CmdType": 851, "CommandObject": bkav_guid}, config)
            if cmd_res.get("Status") == 0 and cmd_res.get("isOk"):
                obj = cmd_res.get("Object")
                if isinstance(obj, str):
                    try:
                        obj = json.loads(obj)
                    except Exception:
                        pass
                if isinstance(obj, dict) and obj.get("PDF"):
                    pdf_b64 = obj.get("PDF")
        except Exception as exc:
            logger.debug("CmdType 851 with bkav_guid %s failed: %s", bkav_guid, exc)

    # Step 2: If CmdType 851 didn't find PDF, search via CmdType 853 across matching criteria
    if not pdf_b64:
        inv_day = invoice_date
        if hasattr(inv_day, "date"):
            inv_day = inv_day.date()
        elif isinstance(inv_day, str):
            try:
                inv_day = date.fromisoformat(str(inv_day)[:10])
            except Exception:
                inv_day = None

        search_ranges = []
        if inv_day:
            search_ranges.append((inv_day, inv_day))
            search_ranges.append((inv_day - timedelta(days=3), inv_day + timedelta(days=3)))
            search_ranges.append((inv_day - timedelta(days=14), inv_day + timedelta(days=14)))
        else:
            today = date.today()
            search_ranges.append((today - timedelta(days=7), today + timedelta(days=7)))

        matched_item = None
        for d_from, d_to in search_ranges:
            search_res = _send_command({"CmdType": 853, "CommandObject": {
                "InvoiceDateFrom": d_from.isoformat(),
                "InvoiceDateTo": d_to.isoformat(),
                "PageNumber": 1,
            }}, config)
            if search_res.get("Status") == 0 and search_res.get("isOk"):
                rows = search_res.get("Object")
                if isinstance(rows, str):
                    try:
                        rows = json.loads(rows)
                    except Exception:
                        rows = []
                for r in (rows or []):
                    item_inv = r.get("Invoice", {})
                    item_guid = str(item_inv.get("InvoiceGUID") or "").strip().lower()
                    item_no = int(item_inv.get("InvoiceNo") or 0)
                    item_serial = str(item_inv.get("InvoiceSerial") or "").strip()
                    item_partner_id = int(item_inv.get("PartnerInvoiceID") or 0)
                    item_bill_code = str(item_inv.get("BillCode") or "").strip()

                    is_match = False
                    if bkav_guid and item_guid == bkav_guid.lower():
                        is_match = True
                    elif item_guid == str(invoice_guid).lower():
                        is_match = True
                    elif partner_invoice_id and item_partner_id == int(partner_invoice_id):
                        is_match = True
                    elif invoice_number and item_bill_code and str(item_bill_code).lstrip("0") == str(invoice_number).lstrip("0") and (not invoice_serial or item_serial == invoice_serial):
                        is_match = True
                    elif invoice_number and item_no and item_no == int(invoice_number) and (not invoice_serial or item_serial == invoice_serial):
                        is_match = True

                    if is_match:
                        matched_item = item_inv
                        break
            if matched_item:
                break

        if matched_item:
            matched_guid = str(matched_item.get("InvoiceGUID") or "").strip()
            if matched_guid:
                bkav_guid = matched_guid
                if push and str(push.get("invoice_guid") or "") != bkav_guid:
                    try:
                        db._execute_query(
                            "UPDATE public.bkav_invoice_pushes SET invoice_guid = %s WHERE trip_id = %s",
                            (bkav_guid, push["trip_id"]), commit=True,
                        )
                    except Exception:
                        pass
            matched_code = str(matched_item.get("InvoiceCode") or "").strip()
            if matched_code:
                invoice_code = matched_code

            # Try CmdType 851 with newly matched bkav_guid
            if bkav_guid:
                try:
                    cmd_res = _send_command({"CmdType": 851, "CommandObject": bkav_guid}, config)
                    if cmd_res.get("Status") == 0 and cmd_res.get("isOk"):
                        obj = cmd_res.get("Object")
                        if isinstance(obj, str):
                            try:
                                obj = json.loads(obj)
                            except Exception:
                                pass
                        if isinstance(obj, dict) and obj.get("PDF"):
                            pdf_b64 = obj.get("PDF")
                except Exception as exc:
                    logger.debug("CmdType 851 with matched bkav_guid failed: %s", exc)

        # Step 3: If still no PDF and invoice_code is present, try CmdType 811
        if not pdf_b64 and invoice_code:
            cmd_res = _send_command({"CmdType": 811, "CommandObject": invoice_code}, config)
            if cmd_res.get("Status") == 0 and cmd_res.get("isOk"):
                obj = cmd_res.get("Object")
                if isinstance(obj, str):
                    try:
                        obj = json.loads(obj)
                    except Exception:
                        pass
                if isinstance(obj, dict) and obj.get("PDF"):
                    pdf_b64 = obj.get("PDF")

    if not pdf_b64:
        raise ValueError(f"Không thể tải file PDF hóa đơn từ BKAV cho hóa đơn số {invoice_number or invoice_guid}.")

    if invoice_code and inv:
        try:
            db._execute_query(
                "UPDATE public.bkav_invoices SET invoice_code = %s WHERE invoice_guid = %s",
                (invoice_code, str(inv["invoice_guid"])), commit=True,
            )
        except Exception:
            pass

    pdf_bytes = base64.b64decode(pdf_b64)

    import fitz
    import io
    from PIL import Image

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if len(doc) == 0:
        raise ValueError("File PDF hóa đơn không có trang nào.")
    elif len(doc) == 1:
        pix = doc[0].get_pixmap(dpi=150)
        png_bytes = pix.tobytes("png")
    else:
        pil_images = []
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            pil_images.append(Image.open(io.BytesIO(pix.tobytes("png"))))
        total_height = sum(img.height for img in pil_images)
        max_width = max(img.width for img in pil_images)
        combined = Image.new("RGB", (max_width, total_height), (255, 255, 255))
        y = 0
        for img in pil_images:
            combined.paste(img, (0, y))
            y += img.height
        buf = io.BytesIO()
        combined.save(buf, format="PNG")
        png_bytes = buf.getvalue()
    doc.close()

    _BKAV_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{invoice_guid}-{uuid4().hex}"
    png_path = _BKAV_UPLOAD_DIR / f"{stem}.png"
    pdf_path = _BKAV_UPLOAD_DIR / f"{stem}.pdf"
    try:
        png_path.write_bytes(png_bytes)
        pdf_path.write_bytes(pdf_bytes)

        guids_to_record = {invoice_guid}
        if inv and inv.get("invoice_guid"):
            guids_to_record.add(str(inv["invoice_guid"]))
        if bkav_guid:
            guids_to_record.add(bkav_guid)

        for gid in guids_to_record:
            db._execute_query("""
                INSERT INTO public.bkav_invoice_files (invoice_guid, image_path, pdf_path, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (invoice_guid) DO UPDATE SET
                    image_path = EXCLUDED.image_path,
                    pdf_path = EXCLUDED.pdf_path,
                    updated_at = NOW()
            """, (gid, png_path.relative_to(_PROJECT_DIR).as_posix(),
                  pdf_path.relative_to(_PROJECT_DIR).as_posix()), commit=True)
    except Exception:
        png_path.unlink(missing_ok=True)
        pdf_path.unlink(missing_ok=True)
        raise

    for old_path in (old_png, old_pdf):
        if old_path and old_path not in (png_path, pdf_path):
            try:
                old_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove old BKAV invoice file %s", old_path, exc_info=True)
    return png_path, pdf_path


def invoice_vehicle_groups(invoice: dict, vehicles: dict[str, dict],
                           default_vehicle: dict | None) -> list[tuple[dict | None, dict]]:
    """Validate and assign every original invoice line to its actual vehicle."""
    if invoice.get("plate_error"):
        raise ValueError(f"Hóa đơn {invoice['number']}: {invoice['plate_error']}")
    segments = invoice.get("segments") or []
    if len(segments) != invoice.get("line_count"):
        raise ValueError(f"Hóa đơn {invoice['number']}: nguồn đồng bộ chưa trả đủ tuyến đường hoặc số tiền từng dòng.")
    if (all(segment.get("tax_amount") is not None for segment in segments)
            and sum((Decimal(str(segment["amount"])) + Decimal(str(segment["tax_amount"]))
                     for segment in segments), Decimal(0)) != invoice["gross"]):
        raise ValueError(f"Hóa đơn {invoice['number']}: tổng tiền từng dòng không khớp tổng hóa đơn.")
    if invoice.get("is_crane_by_shift"):
        if sum((Decimal(str(segment["amount"])) for segment in segments), Decimal(0)) != invoice["subtotal"]:
            raise ValueError(f"H\u00f3a đ\u01a1n {invoice['number']}: t\u1ed5ng ti\u1ec1n d\u00f2ng c\u01b0\u1edbc c\u1ea9u kh\u00f4ng kh\u1edbp h\u00f3a đ\u01a1n.")
        grouped_crane: dict[int, tuple[dict, list[dict]]] = {}
        for segment in segments:
            plate = segment.get("vehicle_plate")
            vehicle = vehicles.get(_plate_key(plate)) if plate else None
            if not vehicle:
                plate_label = plate or "(trống)"
                raise ValueError(f"Biển số {plate_label} trên hóa đơn {invoice['number']} chưa liên kết với xe trong danh mục.")
            grouped_crane.setdefault(vehicle["id"], (vehicle, []))[1].append(segment)
        groups = []
        for vehicle, crane_lines in grouped_crane.values():
            subtotal = sum((Decimal(str(line["amount"])) for line in crane_lines), Decimal(0))
            if len(grouped_crane) > 1:
                if any(line.get("tax_amount") is None for line in crane_lines):
                    raise ValueError(f"H\u00f3a đ\u01a1n {invoice['number']} thi\u1ebfu thu\u1ebf t\u1eebng d\u00f2ng đ\u1ec3 ph\u00e2n b\u1ed5 theo xe.")
                gross = subtotal + sum((Decimal(str(line["tax_amount"])) for line in crane_lines), Decimal(0))
            else:
                gross = invoice["gross"]
            group = {**invoice, "segments": crane_lines, "line_count": len(crane_lines),
                     "origin": "", "destination": "", "distance_km": None,
                     "subtotal": subtotal, "gross": gross}
            groups.append((vehicle, group))
        if sum((group["gross"] for _, group in groups), Decimal(0)) != invoice["gross"]:
            raise ValueError(f"H\u00f3a đ\u01a1n {invoice['number']} c\u00f3 t\u1ed5ng ti\u1ec1n theo xe kh\u00f4ng kh\u1edbp.")
        return groups
    has_plate = any(segment.get("vehicle_plate") for segment in segments)
    if has_plate and any(not segment.get("vehicle_plate") for segment in segments):
        raise ValueError(f"Hóa đơn {invoice['number']} có dòng chưa ghi biển số xe.")
    if not has_plate and not default_vehicle:
        raise ValueError(f"Hóa đơn {invoice['number']} không ghi biển số; hãy chọn xe mặc định.")
    grouped: dict[int, tuple[dict, list[dict]]] = {}
    for segment in segments:
        plate = segment.get("vehicle_plate")
        vehicle = vehicles.get(_plate_key(plate)) if plate else default_vehicle
        if not vehicle:
            raise ValueError(f"Biển số {plate} trên hóa đơn {invoice['number']} chưa có trong danh sách xe.")
        grouped.setdefault(vehicle["id"], (vehicle, []))[1].append(segment)
    groups = []
    for vehicle, legs in grouped.values():
        subtotal = sum((Decimal(str(leg["amount"])) for leg in legs), Decimal(0))
        route_legs = [leg for leg in legs if not leg.get("is_crane") and leg.get("origin") and leg.get("destination")]
        distance = (sum((Decimal(str(leg["distance_km"])) for leg in route_legs), Decimal(0))
                    if route_legs and all(leg.get("distance_km") not in (None, "") for leg in route_legs)
                    else None)
        origin = route_legs[0]["origin"] if route_legs else ""
        destination = route_legs[-1]["destination"] if route_legs else ""
        first_line = route_legs[0]["description"] if route_legs else legs[0]["description"]
        cargo = next((leg.get("cargo_description") for leg in legs if leg.get("cargo_description")), None)
        group = {**invoice, "segments": legs, "line_count": len(legs),
                 "origin": origin, "destination": destination,
                 "first_line": first_line, "description": "\n".join(leg["description"] for leg in legs),
                 "cargo": cargo, "subtotal": subtotal,
                 "distance_km": distance}
        if len(grouped) == 1:
            group["gross"] = invoice["gross"]
        else:
            if any(leg.get("tax_amount") is None for leg in legs):
                raise ValueError(f"Hóa đơn {invoice['number']} thiếu thuế từng dòng để chia cho từng xe.")
            group["gross"] = subtotal + sum((Decimal(str(leg["tax_amount"])) for leg in legs), Decimal(0))
        groups.append((vehicle, group))
    if len(groups) > 1 and sum((group["gross"] for _, group in groups), Decimal(0)) != invoice["gross"]:
        raise ValueError(f"Hóa đơn {invoice['number']} có tổng tiền từng xe không khớp tổng hóa đơn.")
    return groups


def existing_imports(invoices: list[dict], seller_company_id: int,
                     default_vehicle: dict | None, vehicles: dict[str, dict]) -> dict[str, dict]:
    guids = [item["guid"] for item in invoices]
    if not guids:
        return {}
    rows = db._execute_query("""
        SELECT invoice_guid::text AS guid FROM public.bkav_invoices
        WHERE invoice_guid = ANY(%s::uuid[])
    """, (guids,), fetchall=True, commit=False) or []
    existing = {row["guid"]: {"kind": "imported"} for row in rows}
    existing.update({remote: {"kind": "imported", "local_guid": local}
                     for remote, local in _pushed_local_invoice_guids(guids).items()})
    return existing


def _pushed_local_invoice_guids(remote_guids: list[str]) -> dict[str, str]:
    """Match BKAV's remote GUID to an invoice this app already sent."""
    if not remote_guids:
        return {}
    tables = db._execute_query("""
        SELECT to_regclass('public.bkav_saved_invoice_links') AS links,
               to_regclass('public.bkav_invoice_pushes') AS pushes
    """, fetch=True, commit=False)
    if not tables or not tables.get("links") or not tables.get("pushes"):
        return {}
    rows = db._execute_query("""
        SELECT pushes.invoice_guid::text AS remote_guid,
               links.invoice_guid::text AS local_guid
        FROM public.bkav_saved_invoice_links links
        JOIN public.bkav_invoice_pushes pushes
          ON pushes.partner_invoice_id = links.partner_invoice_id
        WHERE pushes.status = 'sent' AND pushes.invoice_guid = ANY(%s::uuid[])
    """, (remote_guids,), fetchall=True, commit=False) or []
    return {row["remote_guid"]: row["local_guid"] for row in rows}


def _buyer_id(cursor, invoice: dict) -> int | None:
    tax = invoice["buyer_tax_code"]
    try:
        tax = normalize_tax_code(tax)
    except ValueError:
        return None
    cursor.execute("SELECT id FROM public.invoice_companies WHERE tax_code = %s", (tax,))
    found = cursor.fetchone()
    if found:
        return found["id"]
    if not invoice["buyer_name"] or not invoice["buyer_address"]:
        return None
    cursor.execute("""
        INSERT INTO public.invoice_companies (tax_code, name, address, email)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (tax_code) DO UPDATE SET tax_code = EXCLUDED.tax_code
        RETURNING id
    """, (tax, invoice["buyer_name"][:255], invoice["buyer_address"][:500],
          invoice["buyer_email"][:255]))
    return cursor.fetchone()["id"]


def import_invoices(invoices: list[dict], selected_guids: list[str], *,
                    vehicle: dict | None, seller_company_id: int,
                    payment_status: str = "debt") -> dict:
    """Persist one BKAV invoice with one child row for each source invoice line."""
    if payment_status not in {"debt", "paid"}:
        raise ValueError("Trạng thái thanh toán không hợp lệ.")
    if not selected_guids or len(selected_guids) > 500:
        raise ValueError("Chọn ít nhất một hóa đơn để đồng bộ.")
    by_guid = {item["guid"]: item for item in invoices}
    if len(set(selected_guids)) != len(selected_guids) or any(guid not in by_guid for guid in selected_guids):
        raise ValueError("Danh sách hóa đơn không còn hợp lệ; hãy tải lại.")
    already_sent = _pushed_local_invoice_guids(selected_guids)
    if already_sent:
        raise ValueError("Hóa đơn đã đồng bộ sang BKAV từ hệ thống này; không nhập lại để tránh trùng dữ liệu.")
    vehicles = vehicle_map()
    assignments = {}
    for guid in selected_guids:
        invoice = by_guid[guid]
        groups = invoice_vehicle_groups(invoice, vehicles, vehicle)
        by_line = {line["invoice_line_number"]: (target, line)
                   for target, group in groups for line in group["segments"]}
        if len(by_line) != invoice["line_count"]:
            raise ValueError(f"Hóa đơn {invoice['number']} thiếu dòng hoặc trùng thứ tự dòng.")
        assignments[guid] = [by_line[number] for number in range(1, invoice["line_count"] + 1)]
    fuel_dates = {by_guid[guid]["date"] for guid in selected_guids
                  if any(not seg.get("is_crane") and seg.get("origin") for seg in by_guid[guid].get("segments", []))}
    fuel_prices = {day: _fuel_price_for_date(day) for day in fuel_dates}
    legacy_table = db._execute_query(
        "SELECT to_regclass('public.bkav_invoice_imports') AS table_name",
        fetch=True, commit=False,
    )
    has_legacy = bool(legacy_table and legacy_table["table_name"])
    counts = {"created": 0, "linked": 0, "already": 0, "conflicts": 0, "repaired": 0}
    plates = []
    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            for guid in selected_guids:
                invoice = by_guid[guid]
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("bkav-import:" + guid,))
                identity = f"{seller_company_id}:{invoice['date']}:{invoice['number']}"
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("invoice:" + identity,))
                cursor.execute("""
                    SELECT seller_company_id, invoice_date, invoice_number,
                           subtotal, gross, line_count, entry_origin
                    FROM public.bkav_invoices WHERE invoice_guid = %s
                """, (guid,))
                existing = cursor.fetchone()
                if existing:
                    if existing["entry_origin"] == "edited":
                        if existing["seller_company_id"] != seller_company_id:
                            raise ValueError(f"Hóa đơn {invoice['number']} không thuộc đơn vị đang đồng bộ.")
                        counts["already"] += 1
                        continue
                    if (existing["seller_company_id"] != seller_company_id
                            or existing["invoice_date"] != invoice["date"]
                            or existing["invoice_number"] != invoice["number"]
                            or existing["subtotal"] != invoice["subtotal"]
                            or existing["gross"] != invoice["gross"]
                            or existing["line_count"] != invoice["line_count"]):
                        raise ValueError(f"Hóa đơn {invoice['number']} đã lưu với thông tin khác dữ liệu gốc.")
                    cursor.execute("""
                        SELECT line_number, vehicle_id, amount FROM public.bkav_invoice_lines
                        WHERE invoice_guid = %s ORDER BY line_number
                    """, (guid,))
                    saved_lines = cursor.fetchall()
                    expected = assignments[guid]
                    if (len(saved_lines) != len(expected)
                            or any(saved["line_number"] != line["invoice_line_number"]
                                   or saved["vehicle_id"] != _line_vehicle_id(invoice, target, line, vehicles)
                                   or saved["amount"] != Decimal(str(line["amount"]))
                                   for saved, (target, line) in zip(saved_lines, expected))):
                        raise ValueError(f"Hóa đơn {invoice['number']} có dòng xe đã lưu không khớp dữ liệu gốc.")
                    counts["already"] += 1
                    continue
                cursor.execute("""
                    SELECT invoice_guid FROM public.bkav_invoices
                    WHERE entry_origin = 'manual' AND seller_company_id = %s
                      AND invoice_date = %s AND invoice_number = %s
                      AND (invoice_serial = %s OR invoice_serial = '')
                    LIMIT 1 FOR UPDATE
                """, (seller_company_id, invoice["date"], invoice["number"], invoice["serial"]))
                manual_invoice = cursor.fetchone()
                if manual_invoice:
                    cursor.execute("DELETE FROM public.bkav_invoices WHERE invoice_guid = %s",
                                   (str(manual_invoice["invoice_guid"]),))
                    counts["repaired"] += 1
                legacy_rows = []
                if has_legacy:
                    cursor.execute("""
                        SELECT trip.id, trip.invoice_date, trip.document_number,
                               trip.seller_company_id, trip.freight_revenue,
                               trip.source_revenue_gross, trip.debt_amount,
                               trip.route_segments
                        FROM public.bkav_invoice_imports imported
                        JOIN public.cargo_trips trip ON trip.id = imported.trip_id
                        WHERE imported.invoice_guid = %s FOR UPDATE OF trip
                    """, (guid,))
                    legacy_rows = cursor.fetchall()
                if legacy_rows:
                    saved_lines = [line for trip in legacy_rows for line in trip["route_segments"]]
                    saved_lines.sort(key=lambda line: line.get("invoice_line_number") or 0)
                    expected_lines = [line for _, line in assignments[guid]]
                    if (len(saved_lines) != len(expected_lines)
                            or any(saved.get("invoice_line_number") != expected["invoice_line_number"]
                                   or saved.get("description") != expected["description"]
                                   or saved.get("origin") != expected["origin"]
                                   or saved.get("destination") != expected["destination"]
                                   or Decimal(str(saved.get("amount") or 0)) != Decimal(str(expected["amount"]))
                                   or (saved.get("vehicle_plate") and
                                       _plate_key(saved["vehicle_plate"]) != _plate_key(expected.get("vehicle_plate")))
                                   for saved, expected in zip(saved_lines, expected_lines))
                            or any(trip["seller_company_id"] != seller_company_id
                                   or trip["invoice_date"] != invoice["date"]
                                   or str(trip["document_number"]).lstrip("0") != str(invoice["number"])
                                   for trip in legacy_rows)
                            or sum((Decimal(str(trip["freight_revenue"] or 0)) for trip in legacy_rows), Decimal(0)) != invoice["subtotal"]
                            or sum((Decimal(str(trip["source_revenue_gross"] or 0)) for trip in legacy_rows), Decimal(0)) != invoice["gross"]):
                        raise ValueError(f"Hóa đơn {invoice['number']} có dữ liệu cũ không khớp dữ liệu gốc; chưa xóa dữ liệu cũ.")
                    legacy_debt = sum((Decimal(str(trip["debt_amount"] or 0)) for trip in legacy_rows), Decimal(0))
                    if legacy_debt < 0 or legacy_debt > invoice["gross"]:
                        raise ValueError(f"Hóa đơn {invoice['number']} có công nợ cũ không hợp lệ.")
                else:
                    legacy_debt = invoice["gross"] if payment_status == "debt" else Decimal(0)
                buyer_id = _buyer_id(cursor, invoice)
                cursor.execute("""
                    INSERT INTO public.bkav_invoices
                        (invoice_guid, seller_company_id, buyer_company_id, invoice_date,
                         invoice_number, invoice_serial, buyer_tax_code, service_name,
                         vat_rate, payment_method, payment_status, debt_amount,
                         subtotal, gross, line_count, invoice_code)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (guid, seller_company_id, buyer_id, invoice["date"], invoice["number"],
                      invoice["serial"], invoice["buyer_tax_code"], invoice["service"],
                      invoice["vat_rate"], invoice["payment_method"],
                      "debt" if legacy_debt else "paid", legacy_debt,
                      invoice["subtotal"], invoice["gross"], invoice["line_count"],
                      invoice.get("invoice_code") or None))
                for target, line in assignments[guid]:
                    linked_vehicle = _line_vehicle(invoice, target, line, vehicles)
                    distance = (Decimal(str(line["distance_km"]))
                                if line.get("distance_km") not in (None, "") else None)
                    rate = target.get("fuel_consumption") if target else None
                    liters = ((distance * Decimal(str(rate)) / Decimal(100)).quantize(Decimal("0.01"))
                              if distance is not None and rate not in (None, "") else None)
                    price = None if (invoice.get("is_crane_by_shift") or line.get("is_crane") or not line.get("origin")) else fuel_prices.get(invoice["date"])
                    fuel_cost = (liters * price).quantize(Decimal("0.01")) if liters is not None and price is not None else None
                    cargo_weight = line.get("cargo_weight_tons")
                    cursor.execute("""
                        INSERT INTO public.bkav_invoice_lines
                            (invoice_guid, line_number, vehicle_id,
                             cargo_description, cargo_weight_tons, description, origin,
                             destination, amount, tax_amount, distance_km, fuel_liters,
                             fuel_price_per_liter, fuel_cost_total)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (guid, line["invoice_line_number"], linked_vehicle["id"] if linked_vehicle else None,
                          line.get("cargo_description"),
                          Decimal(str(cargo_weight)) if cargo_weight not in (None, "") else None,
                          line["description"], line["origin"], line["destination"],
                          Decimal(str(line["amount"])),
                          Decimal(str(line["tax_amount"])) if line.get("tax_amount") is not None else None,
                          distance, liters, price, fuel_cost))
                    if target:
                        plates.append(target["plate_number"])
                if legacy_rows:
                    cursor.execute("DELETE FROM public.cargo_trips WHERE id = ANY(%s)",
                                   ([trip["id"] for trip in legacy_rows],))
                    counts["repaired"] += 1
                counts["created"] += 1
            conn.commit()
    counts["vehicles"] = list(dict.fromkeys(plates))
    return counts
