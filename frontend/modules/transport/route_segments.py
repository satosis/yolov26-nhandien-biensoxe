"""Validate route legs stored with one cargo trip and one invoice."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation


MAX_SEGMENTS = 20


def parse_route_segments(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Danh sách đoạn đường không hợp lệ.") from exc
    if not isinstance(value, list) or not 2 <= len(value) <= MAX_SEGMENTS:
        raise ValueError("Một chuyến nhiều đoạn cần từ 2 đến 20 đoạn đường.")
    result = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Đoạn {index} không hợp lệ.")
        origin = str(item.get("origin") or "").strip()
        destination = str(item.get("destination") or "").strip()
        description = str(item.get("description") or "").strip()
        cargo_description = str(item.get("cargo_description") or "").strip()
        cargo_weight_tons = item.get("cargo_weight_tons")
        if not origin or not destination or len(origin) > 500 or len(destination) > 500 or not description or len(description) > 4000:
            raise ValueError(f"Đoạn {index} cần đủ điểm đi, điểm đến và nội dung hóa đơn (tối đa 4000 ký tự).")
        try:
            amount = Decimal(str(item.get("amount")))
            distance_raw = item.get("distance_km")
            distance = Decimal(str(distance_raw)) if distance_raw is not None and str(distance_raw).strip() else None
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Số tiền hoặc quãng đường của đoạn {index} không hợp lệ.") from exc
        if not amount.is_finite() or amount <= 0 or amount > 10**14 or amount != amount.to_integral_value():
            raise ValueError(f"Số tiền của đoạn {index} phải là số nguyên đồng lớn hơn 0.")
        if distance is not None and (not distance.is_finite() or distance < 0 or distance > 100000):
            raise ValueError(f"Quãng đường của đoạn {index} không hợp lệ.")
        result.append({
            "origin": origin, "destination": destination,
            "description": description, "amount": int(amount),
            "distance_km": str(distance) if distance is not None else None,
            "cargo_description": cargo_description[:200] or None,
            "cargo_weight_tons": str(cargo_weight_tons).strip() if cargo_weight_tons not in (None, "") else None,
        })
    return result


def route_totals(segments: list[dict]) -> tuple[Decimal, Decimal | None]:
    revenue = sum((Decimal(str(item["amount"])) for item in segments), Decimal(0))
    distances = [item.get("distance_km") for item in segments]
    distance = sum((Decimal(str(value)) for value in distances), Decimal(0)) if all(value is not None for value in distances) else None
    return revenue, distance


def segment_description(segment: dict, service: str, cargo: str) -> str:
    explicit = str(segment.get("description") or "").strip()
    if explicit:
        return explicit
    base = " ".join(part for part in (service.strip(), cargo.strip()) if part)
    return f"{base} từ {segment['origin']} đến {segment['destination']}".strip()


def invoice_items(trip: dict, fallback_description: str) -> list[dict]:
    segments = trip.get("route_segments") or []
    if not segments:
        return [{"description": fallback_description, "amount": Decimal(str(trip.get("freight_revenue") or 0))}]
    total, _ = route_totals(segments)
    if total != Decimal(str(trip.get("freight_revenue") or 0)):
        raise ValueError("Tổng cước chuyến hàng không khớp các đoạn đường.")
    service = str(trip.get("service_name") or "")
    cargo = str(trip.get("cargo_description") or "")
    return [{"description": segment_description(segment, service, cargo),
             "amount": Decimal(str(segment["amount"]))} for segment in segments]
