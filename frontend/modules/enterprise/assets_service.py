"""Company-scoped enterprise asset inventory."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.services.database import db




def _asset_filters(company_id: int, q: str = "", category: str = "", status: str = "") -> tuple[str, list]:
    filters = ["company_id = %s"]
    params: list = [company_id]
    term = (q or "").strip()
    if term:
        wildcard = f"%{term}%"
        filters.append("(name ILIKE %s OR asset_code ILIKE %s OR serial_number ILIKE %s OR assigned_to ILIKE %s OR note ILIKE %s)")
        params.extend([wildcard] * 5)
    if category in {"vehicle", "equipment", "tool", "other"}:
        filters.append("category = %s")
        params.append(category)
    if status in {"available", "in_use", "repair", "liquidated"}:
        filters.append("status = %s")
        params.append(status)
    return " AND ".join(filters), params


def count_assets(company_id: int, q: str = "", category: str = "", status: str = "") -> int:
    where, params = _asset_filters(company_id, q, category, status)
    row = db._execute_query(
        f"SELECT COUNT(*) AS total FROM public.enterprise_assets WHERE {where}",
        tuple(params), fetch=True, commit=False,
    ) or {}
    return int(row.get("total") or 0)


def list_assets(company_id: int, q: str = "", category: str = "", status: str = "",
               limit: int | None = None, offset: int | None = None) -> list[dict]:
    where, params = _asset_filters(company_id, q, category, status)
    query = f"""
        SELECT id, asset_code, name, category, serial_number, acquired_date,
               purchase_cost, assigned_to, status, note, created_at, updated_at
        FROM public.enterprise_assets
        WHERE {where}
        ORDER BY name ASC, id DESC
    """
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)
    return db._execute_query(
        query,
        tuple(params),
        fetchall=True,
        commit=False,
    ) or []


def create_asset(company_id: int, values: dict, user_id: int | None) -> dict | None:
    return db._execute_query(
        """
        INSERT INTO public.enterprise_assets (
            company_id, asset_code, name, category, serial_number, acquired_date,
            purchase_cost, assigned_to, status, note, created_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, asset_code, name, category, serial_number, acquired_date,
                  purchase_cost, assigned_to, status, note, created_at, updated_at
        """,
        (
            company_id, values["asset_code"], values["name"], values["category"],
            values["serial_number"], values["acquired_date"], values["purchase_cost"],
            values["assigned_to"], values["status"], values["note"], user_id,
        ),
        fetch=True,
        commit=True,
    )


def update_asset(company_id: int, asset_id: int, values: dict) -> dict | None:
    return db._execute_query(
        """
        UPDATE public.enterprise_assets
        SET asset_code = %s, name = %s, category = %s, serial_number = %s,
            acquired_date = %s, purchase_cost = %s, assigned_to = %s,
            status = %s, note = %s, updated_at = NOW()
        WHERE company_id = %s AND id = %s
        RETURNING id, asset_code, name, category, serial_number, acquired_date,
                  purchase_cost, assigned_to, status, note, created_at, updated_at
        """,
        (
            values["asset_code"], values["name"], values["category"],
            values["serial_number"], values["acquired_date"], values["purchase_cost"],
            values["assigned_to"], values["status"], values["note"], company_id, asset_id,
        ),
        fetch=True,
        commit=True,
    )


def delete_asset(company_id: int, asset_id: int) -> bool:
    row = db._execute_query(
        "DELETE FROM public.enterprise_assets WHERE company_id = %s AND id = %s RETURNING id",
        (company_id, asset_id),
        fetch=True,
        commit=True,
    )
    return bool(row)


def parse_asset_values(payload: dict) -> tuple[dict | None, str | None]:
    name = str(payload.get("name") or "").strip()
    if not name:
        return None, "Vui lòng nhập tên tài sản."

    category = str(payload.get("category") or "other").strip()
    if category not in {"vehicle", "equipment", "tool", "other"}:
        return None, "Loại tài sản không hợp lệ."
    status = str(payload.get("status") or "available").strip()
    if status not in {"available", "in_use", "repair", "liquidated"}:
        return None, "Trạng thái tài sản không hợp lệ."

    acquired_raw = str(payload.get("acquired_date") or "").strip()
    try:
        acquired_date = date.fromisoformat(acquired_raw) if acquired_raw else None
    except ValueError:
        return None, "Ngày mua không hợp lệ."
    try:
        purchase_cost = Decimal(str(payload.get("purchase_cost") or "0").replace(",", "").strip())
        if not purchase_cost.is_finite() or purchase_cost < 0:
            raise ValueError
    except Exception:
        return None, "Nguyên giá phải là số không âm."

    values = {
        "asset_code": str(payload.get("asset_code") or "").strip() or None,
        "name": name,
        "category": category,
        "serial_number": str(payload.get("serial_number") or "").strip(),
        "acquired_date": acquired_date,
        "purchase_cost": purchase_cost,
        "assigned_to": str(payload.get("assigned_to") or "").strip(),
        "status": status,
        "note": str(payload.get("note") or "").strip(),
    }
    return values, None
