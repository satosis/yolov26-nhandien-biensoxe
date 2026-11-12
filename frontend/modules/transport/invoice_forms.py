"""Validate one invoice header and its vehicle lines from the shared form."""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from frontend.formatting.text import strip_marks
from frontend.modules.invoices.export import is_crane_by_shift_service, parse_vat_percent
from frontend.modules.transport.invoice_accounting import parse_accounts


def _decimal(value, label: str, *, required: bool = False) -> Decimal | None:
    if value is None:
        if required:
            raise ValueError(f"Nhập {label}.")
        return None
    raw = str(value).strip().replace(",", ".")
    if not raw:
        if required:
            raise ValueError(f"Nhập {label}.")
        return None
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{label.capitalize()} không hợp lệ.") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{label.capitalize()} không hợp lệ.")
    return number


def _money(value, label: str, *, required: bool = False) -> Decimal | None:
    if value is None:
        if required:
            raise ValueError(f"Nhập {label}.")
        return None
    raw = str(value).strip()
    if not raw:
        if required:
            raise ValueError(f"Nhập {label}.")
        return None
    # Xử lý các chuỗi số từ DB/JSON có đuôi thập phân .00, ,00, .0 hoặc ,0
    raw = re.sub(r"[.,]0{1,2}$", "", raw)  # chỉ đuôi thập phân; nhóm nghìn luôn có 3 chữ số
    if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:[., ][0-9]{3})+)", raw):
        raise ValueError(f"{label.capitalize()} phải là số nguyên đồng.")
    return Decimal(re.sub(r"[., ]", "", raw))


def liters_for_distance(distance: Decimal, fuel_consumption) -> Decimal:
    """Litres of fuel a vehicle burns over `distance` km, from its consumption per 100 km."""
    return (distance * Decimal(str(fuel_consumption)) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_invoice_form(form: dict, *, vehicles: list[dict], buyer: dict,
                       seller_company_id: int, default_fuel_price: Decimal | None = None) -> dict:
    try:
        invoice_date = date.fromisoformat(str(form.get("invoice_date") or ""))
    except ValueError as exc:
        raise ValueError("Ngày hóa đơn không hợp lệ.") from exc
    raw_number = str(form.get("invoice_number") or "").strip()
    if not re.fullmatch(r"\d{1,10}", raw_number) or not 0 < int(raw_number) <= 2147483647:
        raise ValueError("Số hóa đơn phải là số nguyên dương.")
    serial = str(form.get("invoice_serial") or "").strip()
    service = str(form.get("service_name") or "").strip()
    crane_by_shift = is_crane_by_shift_service(service)
    if not service:
        raise ValueError("Chọn dịch vụ.")
    vat_rate = parse_vat_percent(form.get("vat_percent"))
    payment_method = str(form.get("payment_method") or "CK").strip().upper()
    payment_status = str(form.get("payment_status") or "").strip().lower()
    if payment_method not in ("CK", "TM", "UNKNOWN") or payment_status not in ("paid", "debt"):
        raise ValueError("Thông tin thanh toán không hợp lệ.")
    try:
        raw_lines = json.loads(str(form.get("lines_json") or ""))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Các dòng hóa đơn không hợp lệ.") from exc
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValueError("Hóa đơn cần ít nhất một dòng xe.")

    by_id = {int(vehicle["id"]): vehicle for vehicle in vehicles}
    lines = []
    for number, item in enumerate(raw_lines, 1):
        try:
            vehicle = by_id[int(item.get("vehicle_id"))]
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(f"Chọn xe cho dòng {number}.") from exc
        description = str(item.get("description") or "").strip()
        origin = str(item.get("origin") or "").strip()
        destination = str(item.get("destination") or "").strip()
        via_points = [str(point).strip() for point in item.get("via_points") or [] if str(point).strip()]
        cargo = str(item.get("cargo_description") or "").strip()
        is_line_crane = crane_by_shift or (not origin and not destination and any(m in strip_marks(description) for m in ("theo ca", "ca cau", "tai cho", "tai xuong")))
        if not description or (not is_line_crane and (not origin or not destination)):
            raise ValueError(f"Dòng {number} cần đủ nội dung hóa đơn" +
                             ("." if is_line_crane else ", điểm lấy và điểm giao."))
        weight = _decimal(item.get("cargo_weight_tons"), f"khối lượng dòng {number}")
        amount = _money(item.get("amount"), f"cước dòng {number}", required=True)
        if amount <= 0:
            raise ValueError(f"Cước dòng {number} phải lớn hơn 0.")
        distance = (None if is_line_crane and not item.get("distance_km") else
                    _decimal(item.get("distance_km"), f"quãng đường dòng {number}", required=False))
        liters = (None if is_line_crane and not item.get("fuel_liters") else
                  _decimal(item.get("fuel_liters"), f"số lít dầu dòng {number}"))
        if liters is None and distance is not None and vehicle.get("fuel_consumption") is not None:
            liters = liters_for_distance(distance, vehicle["fuel_consumption"])
        price = (None if is_line_crane and not item.get("fuel_price_per_liter") else
                 _money(item.get("fuel_price_per_liter"), f"giá dầu dòng {number}"))
        if price is None and not is_line_crane and default_fuel_price is not None:
            price = Decimal(str(default_fuel_price))
        if liters and not price and not is_line_crane:
            raise ValueError(f"Nhập giá dầu dòng {number}.")
        fuel_cost = (liters * price).quantize(Decimal("0.01")) if liters is not None and price is not None else None
        lines.append({
            "line_number": number, "vehicle_id": vehicle["id"],
            "plate_number": vehicle["plate_number"],
            "cargo_description": cargo or None,
            "cargo_weight_tons": weight,
            "description": description, "origin": origin, "destination": destination,
            "via_points": via_points,
            "amount": amount, "distance_km": distance, "fuel_liters": liters,
            "fuel_price_per_liter": price,
            "fuel_cost_total": fuel_cost,
        })

    subtotal = sum((line["amount"] for line in lines), Decimal(0))
    gross = (subtotal * (Decimal(1) + vat_rate)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    tax_remaining = gross - subtotal
    for line in lines[:-1]:
        tax_amount = (line["amount"] * vat_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        line["tax_amount"] = tax_amount
        tax_remaining -= tax_amount
    lines[-1]["tax_amount"] = tax_remaining
    debt = _money(form.get("debt_amount"), "số tiền còn nợ") if payment_status == "debt" else Decimal(0)
    if payment_status == "debt":
        if debt is None or debt <= 0:
            debt = gross
        elif debt > gross:
            raise ValueError("Số tiền còn nợ không được vượt tổng thanh toán hóa đơn.")
    return {
        "seller_company_id": seller_company_id,
        "buyer_company_id": buyer["id"], "buyer_tax_code": buyer.get("tax_code") or "",
        "invoice_date": invoice_date, "invoice_number": int(raw_number),
        "invoice_serial": serial, "service_name": service, "vat_rate": vat_rate,
        "payment_method": payment_method, "payment_status": payment_status,
        "debt_amount": debt, "subtotal": subtotal, "gross": gross,
        "line_count": len(lines), "lines": lines,
        "accounting": parse_accounts(form, payment_status, payment_method),
    }
