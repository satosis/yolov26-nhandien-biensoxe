"""Build a one-trip Bkav invoice import workbook from the clean template."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
import re

from openpyxl import load_workbook
from frontend.modules.transport.route_segments import invoice_items


TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "assets" / "bkav_invoice_blank.xlsx"
DEFAULT_INVOICE_VAT_RATE = Decimal("0.08")
ALLOWED_PAYMENT_METHODS = frozenset({"CK", "TM"})
DEFAULT_SERVICE_NAME = "Cước vận chuyển"
CRANE_BY_SHIFT_SERVICE = "Cước cẩu theo ca"
CRANE_INSTALLATION_SERVICE = "Cước cẩu lắp dựng kết cấu nhà xưởng"
REMOVED_INVOICE_SERVICES = frozenset({CRANE_INSTALLATION_SERVICE, "Dựng kết cấu nhà xưởng"})
SERVICE_PRESETS = (
    DEFAULT_SERVICE_NAME,
    "Cước cẩu, vận chuyển",
    CRANE_BY_SHIFT_SERVICE,
)
CARGO_TYPE_KEYWORDS = (
    ("quả chuẩn", "Quả chuẩn"),
    ("bàn cân", "Bàn cân"),
    ("quả cân", "Quả cân"),
)


def is_crane_by_shift_service(service_name: str | None) -> bool:
    """Identify crane-by-shift services, including the old mislabeled value."""
    service = " ".join(str(service_name or "").split()).casefold()
    return service in {
        CRANE_BY_SHIFT_SERVICE.casefold(),
        CRANE_INSTALLATION_SERVICE.casefold(),
        "c\u01b0\u1edbc c\u1ea9u theo ca",
        "c\u01b0\u1edbc c\u1ea9u l\u1eafp d\u1ef1ng k\u1ebft c\u1ea5u nh\u00e0 x\u01b0\u1edfng",
    }


def cargo_description_from_service(service_name: str) -> str | None:
    service = service_name.casefold()
    return next((label for keyword, label in CARGO_TYPE_KEYWORDS if keyword in service), None)


def parse_vat_rate(value: object) -> Decimal | None:
    raw = str(value).strip() if value is not None else ""
    if not raw:
        return None
    try:
        rate = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Thuế suất GTGT không hợp lệ.") from exc
    if not rate.is_finite() or not 0 <= rate <= 1 or rate != rate.quantize(Decimal("0.0001")):
        raise ValueError("Thuế suất GTGT phải từ 0% đến 100%, tối đa 2 chữ số thập phân.")
    return rate


def parse_vat_percent(value: object) -> Decimal:
    raw = str(value).strip().replace(",", ".") if value is not None else ""
    if not raw:
        return DEFAULT_INVOICE_VAT_RATE
    try:
        percent = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Thuế suất GTGT không hợp lệ.") from exc
    return parse_vat_rate(percent / 100)


def format_vat_percent(rate: Decimal | None) -> str:
    percent = (rate if rate is not None else DEFAULT_INVOICE_VAT_RATE) * 100
    return format(percent.normalize(), "f")


def format_vat_rate(rate: Decimal) -> str:
    return "0" if rate == 0 else f"{rate:.2f}"


def parse_payment_method(value: object, *, allow_unknown: bool = False) -> str:
    method = str(value or "CK").strip().upper()
    if allow_unknown and method == "UNKNOWN":
        return method
    if method not in ALLOWED_PAYMENT_METHODS:
        raise ValueError("Hình thức thanh toán không hợp lệ.")
    return method


def _service_description(origin: str | None, destination: str | None,
                         service_name: str = DEFAULT_SERVICE_NAME) -> str:
    origin = (origin or "").strip()
    destination = (destination or "").strip()
    if is_crane_by_shift_service(service_name):
        location = destination or origin
        return f"{service_name} tại {location}" if location else service_name
    if origin and destination:
        return f"{service_name} từ {origin} đến {destination}"
    if origin:
        return f"{service_name} từ {origin}"
    if destination:
        return f"{service_name} đến {destination}"
    return service_name


def _set_text(sheet, row: int, column: int, value: str) -> None:
    cell = sheet.cell(row, column, value)
    cell.data_type = "s"


def build_bkav_invoice(trip: dict, company: dict) -> bytes:
    """Return an .xlsx with one invoice row using the supplied Bkav column layout."""
    if not company or not all(company.get(field) for field in ("name", "address", "tax_code")):
        raise ValueError("Chuyến hàng chưa có đủ tên, địa chỉ và mã số thuế đơn vị.")
    if not trip.get("invoice_date"):
        raise ValueError("Chuyến hàng chưa có ngày hóa đơn.")
    bill_number = trip.get("invoice_bill_number")
    if bill_number is None and trip.get("document_number"):
        digits = re.sub(r"[^0-9]", "", str(trip["document_number"]))
        if digits:
            bill_number = int(digits)
    if isinstance(bill_number, bool) or not isinstance(bill_number, int) or bill_number < 1:
        raise ValueError("Chuyến hàng chưa được cấp IDChungTu/MaBill.")

    tax_rate = parse_vat_rate(trip.get("vat_rate"))
    if tax_rate is None:
        tax_rate = DEFAULT_INVOICE_VAT_RATE
    method_raw = trip.get("payment_method")
    payment_method = parse_payment_method(method_raw if method_raw in ALLOWED_PAYMENT_METHODS else "CK")

    invoice_day = trip["invoice_date"]
    if isinstance(invoice_day, str):
        invoice_day = date.fromisoformat(invoice_day[:10])
    elif isinstance(invoice_day, datetime):
        invoice_day = invoice_day.date()

    revenue = Decimal(str(trip.get("freight_revenue") or 0))
    if not revenue.is_finite() or revenue < 0:
        raise ValueError("Cước vận chuyển không hợp lệ.")

    workbook = load_workbook(TEMPLATE_PATH, keep_links=False, rich_text=True)
    if "Country" in workbook.defined_names:
        del workbook.defined_names["Country"]
    sheet = workbook.worksheets[0]
    doc_display = trip.get("document_number")
    service_name = str(trip.get("service_name") or DEFAULT_SERVICE_NAME).strip() or DEFAULT_SERVICE_NAME
    work_content = str(trip.get("trip_note") or "").strip()
    if work_content.startswith("Nội dung hóa đơn gốc:"):
        work_content = work_content[len("Nội dung hóa đơn gốc:"):].strip()
    if not work_content:
        work_content = str(trip.get("cargo_description") or "").strip()
    if not work_content:
        work_content = _service_description(trip.get("origin"), trip.get("destination"), service_name)
    for index, item in enumerate(invoice_items(trip, work_content)):
        row = 6 + index
        sheet.cell(row, 1, index + 1)
        if doc_display:
            _set_text(sheet, row, 2, str(doc_display))
        else:
            sheet.cell(row, 2, bill_number)
        _set_text(sheet, row, 3, str(trip.get("plate_number") or ""))
        _set_text(sheet, row, 4, item["description"])
        _set_text(sheet, row, 5, "Ca cẩu" if is_crane_by_shift_service(service_name) else "Chuyến")
        sheet.cell(row, 6, 1)
        sheet.cell(row, 7, item["amount"])
        sheet.cell(row, 8, item["amount"])
        sheet.cell(row, 9, tax_rate)
        sheet.cell(row, 10, (item["amount"] * tax_rate).quantize(Decimal("0.01")))
        sheet.cell(row, 11, datetime.combine(invoice_day, time.min))
        sheet.cell(row, 11).number_format = "dd/mm/yyyy"
        _set_text(sheet, row, 16, str(company["name"]).strip())
        _set_text(sheet, row, 17, str(company["tax_code"]).strip())
        sheet.cell(row, 17).number_format = "@"
        _set_text(sheet, row, 18, str(company["address"]).strip())
        _set_text(sheet, row, 20, payment_method)
        email = (company.get("email") or "").strip()
        if email:
            _set_text(sheet, row, 21, "Có")
            _set_text(sheet, row, 22, email)

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def build_saved_invoice_workbook(invoice: dict, lines: list[dict], company: dict) -> bytes:
    """Export repeated identical trips as one quantity-priced worksheet row."""
    if not all(company.get(field) for field in ("name", "address", "tax_code")):
        raise ValueError("Đơn vị khách hàng thiếu tên, địa chỉ hoặc mã số thuế.")
    if not lines:
        raise ValueError("Hóa đơn chưa có dòng xe.")
    invoice_day = invoice["invoice_date"]
    if isinstance(invoice_day, datetime):
        invoice_day = invoice_day.date()
    elif isinstance(invoice_day, str):
        invoice_day = date.fromisoformat(invoice_day[:10])
    tax_rate = parse_vat_rate(invoice.get("vat_rate"))
    if tax_rate is None:
        raise ValueError("Hóa đơn chưa có thuế suất GTGT.")
    method = parse_payment_method(invoice.get("payment_method"))
    book = load_workbook(TEMPLATE_PATH, keep_links=False, rich_text=True)
    if "Country" in book.defined_names:
        del book.defined_names["Country"]
    sheet = book.worksheets[0]
    unit_name = "Ca cẩu" if is_crane_by_shift_service(invoice.get("service_name")) else "Chuyến"
    grouped = []
    for index, line in enumerate(lines):
        amount = Decimal(str(line.get("amount") or 0))
        if amount < 0 or amount != amount.to_integral_value():
            raise ValueError(f"Cước dòng {index + 1} phải là số nguyên không âm.")
        plate = str(line.get("plate_number") or "")
        description = str(line.get("description") or "")
        if grouped and (grouped[-1]["plate"], grouped[-1]["description"], grouped[-1]["unit_price"]) == (plate, description, amount):
            grouped[-1]["quantity"] += 1
        else:
            grouped.append({"plate": plate, "description": description,
                            "unit_price": amount, "quantity": 1})
    for index, item in enumerate(grouped):
        amount = item["unit_price"] * item["quantity"]
        row = 6 + index
        sheet.cell(row, 1, index + 1)
        sheet.cell(row, 2, int(invoice["invoice_number"]))
        _set_text(sheet, row, 3, item["plate"])
        _set_text(sheet, row, 4, item["description"])
        _set_text(sheet, row, 5, unit_name)
        sheet.cell(row, 6, item["quantity"])
        sheet.cell(row, 7, item["unit_price"])
        sheet.cell(row, 8, amount)
        sheet.cell(row, 9, tax_rate)
        sheet.cell(row, 10, (amount * tax_rate).quantize(Decimal("0.01")))
        sheet.cell(row, 11, datetime.combine(invoice_day, time.min))
        sheet.cell(row, 11).number_format = "dd/mm/yyyy"
        _set_text(sheet, row, 16, str(company["name"]).strip())
        _set_text(sheet, row, 17, str(company["tax_code"]).strip())
        sheet.cell(row, 17).number_format = "@"
        _set_text(sheet, row, 18, str(company["address"]).strip())
        _set_text(sheet, row, 20, method)
        email = str(company.get("email") or "").strip()
        if email:
            _set_text(sheet, row, 21, "Có")
            _set_text(sheet, row, 22, email)
    output = BytesIO()
    book.save(output)
    return output.getvalue()
