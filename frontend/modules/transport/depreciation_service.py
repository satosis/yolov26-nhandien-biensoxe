"""Depreciation calculation service and Excel exporter for fixed assets (TSCĐ).

Focuses on CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH (company_id = 33).
Calculates monthly straight-line depreciation, cumulative depreciation,
and remaining value, following the standard accounting template (MISA / Thông tư 133).
"""

from __future__ import annotations

from datetime import date, datetime
import calendar
from io import BytesIO
import json
import re
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from backend.services.database import db
from frontend.context.company_context import get_current_company_id
from frontend.formatting.text import normalize_invoice_number

DAI_LO_XANH_COMPANY_ID = 33

_OVERRIDE_TABLE = "public.transport_fixed_asset_overrides"
DEFAULT_ACCOUNT_COST = "21113"
DEFAULT_ACCOUNT_DEPRECIATION = "2141"
DEFAULT_ACCOUNT_EXPENSE = "6422"
ASSET_TYPE_OPTIONS = (
    "Phương tiện vận tải, truyền dẫn",
    "Máy móc, thiết bị",
    "Thiết bị, dụng cụ quản lý",
    "Nhà cửa, vật kiến trúc",
    "TSCĐ khác",
)


def _load_overrides(company_id: int) -> dict[tuple[str, int], dict]:
    rows = db._execute_query(
        f"SELECT * FROM {_OVERRIDE_TABLE} WHERE company_id = %s",
        (company_id,),
        fetchall=True,
        commit=False,
    ) or []
    return {(str(r["source"]), int(r["source_id"])): dict(r) for r in rows}


def _month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


def _depreciation_schedule(start_d, total_months, cost, revals, end_idx, decrease_idx=None):
    """revals: [(month_idx, new_cost, new_months)] sắp tăng dần. Trả (amounts{month_idx: int}, cost_end, monthly_end, used, months_left)."""
    amounts = {}
    months_left = total_months
    monthly = round(cost / total_months) if total_months > 0 else 0
    accum = 0
    used = 0
    pending = list(revals)
    mi = _month_index(start_d) + (1 if start_d.day > 28 else 0)
    last = end_idx if decrease_idx is None else min(end_idx, decrease_idx)
    while mi <= last:
        while pending and pending[0][0] <= mi:
            _, cost, months_left = pending.pop(0)
            monthly = round(max(0, cost - accum) / months_left) if months_left > 0 else 0
        if months_left > 0:
            amt = (cost - accum) if months_left == 1 else min(monthly, cost - accum)
            amt = max(0, amt)
            amounts[mi] = amt
            accum += amt
            months_left -= 1
            used += 1
        mi += 1
    for idx, new_cost, new_months in pending:   # đánh giá lại nằm sau tháng cuối đã duyệt nhưng vẫn trong kỳ
        if idx <= end_idx:
            cost, months_left = new_cost, new_months
    return amounts, cost, monthly, used, months_left


def get_depreciation_data(
    company_id: int | None = None,
    year: int = 2026,
    as_of_month: int | None = None,
    full_year: bool = False,
) -> dict[str, Any]:
    """Calculate depreciation schedule for all vehicles and fixed assets of the company."""
    if company_id is None:
        company_id = get_current_company_id()

    if as_of_month is None:
        today = date.today()
        as_of_month = today.month if today.year == year else 12

    if not company_id or int(company_id) <= 0:
        return {
            "year": year,
            "as_of_month": as_of_month,
            "full_year": full_year,
            "company": None,
            "company_name": "",
            "rows": [],
            "totals": {
                "original_cost": 0,
                "accumulated_depreciation_prev": 0,
                "monthly_depreciation": 0,
                "depreciation_current_period": 0,
                "depreciation_current_year": 0,
                "accumulated_depreciation_end": 0,
                "remaining_book_value": 0,
                "original_price": 0,
                "calculation_value": 0,
                "usage_months_total": 0,
                "prior_usage_months": 0,
                "prior_depreciation_value": 0,
                "monthly_kh": [0] * 12,
                "year_depreciation_total": 0,
                "total_usage_months": 0,
                "cumulative_depreciation": 0,
                "remaining_usage_months": 0,
                "remaining_value": 0,
            },
            "row_count": 0,
            "hidden_vehicles": [],
            "all_items": [],
            "vouchers": [],
        }

    # Query company info
    company = db._execute_query(
        "SELECT * FROM public.invoice_companies WHERE id = %s",
        (company_id,), fetch=True, commit=False,
    ) or {}
    company_name = company.get("name") or ""

    # Load vouchers
    from frontend.modules.transport.asset_vouchers import load_vouchers, vouchers_by_item
    vouchers_list = load_vouchers(company_id, "tscd")
    vouchers = vouchers_by_item(company_id, "tscd")

    # Period bounds
    year_start = year * 12
    end_idx = year * 12 + (11 if full_year else as_of_month - 1)
    if full_year:
        period_end = date(year, 12, 31)
    else:
        _, last_day = calendar.monthrange(year, as_of_month)
        period_end = date(year, as_of_month, last_day)

    # Query vehicles from plate_whitelist
    vehicles = db._execute_query("""
        SELECT id, plate_number, vehicle_type, owner_name, purchase_price,
               depreciation_start_date, depreciation_years, company_id, note
        FROM public.plate_whitelist
        WHERE company_id = %s
        ORDER BY id DESC
    """, (company_id,), fetchall=True, commit=False) or []

    # Also query enterprise_assets if any
    assets = db._execute_query("""
        SELECT id, asset_code, name, category, serial_number, acquired_date,
               purchase_cost, assigned_to, note, COALESCE(depreciation_years, 5) as depreciation_years
        FROM public.enterprise_assets
        WHERE company_id = %s AND purchase_cost > 0
        ORDER BY id DESC
    """, (company_id,), fetchall=True, commit=False) or []

    overrides = _load_overrides(company_id)
    hidden_vehicles = []
    all_items: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    totals = {
        "original_price": 0,
        "calculation_value": 0,
        "usage_months_total": 0,
        "monthly_depreciation": 0,
        "prior_usage_months": 0,
        "prior_depreciation_value": 0,
        "monthly_kh": [0] * 12,
        "year_depreciation_total": 0,
        "total_usage_months": 0,
        "cumulative_depreciation": 0,
        "remaining_usage_months": 0,
        "remaining_value": 0,
    }

    # Process vehicles
    for v in vehicles:
        vid = int(v["id"])
        plate = str(v.get("plate_number") or "").strip()
        ov = overrides.get(("vehicle", vid)) or {}
        if ov.get("is_deleted"):
            hidden_vehicles.append({"id": vid, "plate_number": plate})
            continue

        base_price = int(v.get("purchase_price") or 0)
        orig_price = base_price
        dur_years = int(v.get("depreciation_years") or 5)
        total_months = int(ov["usage_months"]) if ov.get("usage_months") else (dur_years * 12 if dur_years > 0 else 60)

        start_d = v.get("depreciation_start_date")
        if isinstance(start_d, str):
            try:
                start_d = date.fromisoformat(start_d[:10])
            except Exception:
                start_d = date(year, 1, 1)
        elif not isinstance(start_d, date):
            start_d = date(year, 1, 1)

        asset_code = ov.get("asset_code") or plate
        asset_name = ov.get("asset_name") or plate
        asset_type = ov.get("asset_type") or "Phương tiện vận tải, truyền dẫn"
        department = ov.get("department") or "BAN GIÁM ĐỐC"
        base_department = department

        item_vouchers = vouchers.get(("vehicle", vid), [])
        decrease = next((it for it in item_vouchers if it["kind"] == "decrease"), None)
        decrease_idx = _month_index(decrease["voucher_date"]) if decrease else None

        transfers = [it for it in item_vouchers if it["kind"] == "transfer" and it["voucher_date"] <= period_end]
        if transfers:
            latest_transfer = max(transfers, key=lambda it: (it["voucher_date"], it["id"]))
            if latest_transfer.get("to_dept"):
                department = latest_transfer["to_dept"]

        label = f"{asset_code} - {asset_name}" if asset_code != asset_name else asset_code

        if decrease_idx is not None and decrease_idx < year_start:
            all_items.append({
                "key": f"vehicle:{vid}",
                "label": label,
                "code": asset_code,
                "name": asset_name,
                "department": department,
                "cost": orig_price,
                "months_left": 0,
                "base_cost": base_price,
            })
            continue

        revals = [(_month_index(it["voucher_date"]), int(it["new_cost"]), int(it["new_months"])) for it in item_vouchers if it["kind"] == "revalue" and it["voucher_date"] <= period_end]
        revals.sort(key=lambda x: x[0])

        amounts, cost_end, monthly_end, used, months_left = _depreciation_schedule(start_d, total_months, orig_price, revals, end_idx, decrease_idx)
        prior_val = sum(a for mi, a in amounts.items() if mi < year_start)
        prior_months = sum(1 for mi in amounts if mi < year_start)
        m_kh = [amounts.get(year_start + k, 0) for k in range(12)]
        total_yr = sum(m_kh)
        used_months = used
        accum_kh = prior_val + total_yr
        rem_months = months_left
        rem_val = max(0, cost_end - accum_kh)

        all_items.append({
            "key": f"vehicle:{vid}",
            "label": label,
            "code": asset_code,
            "name": asset_name,
            "department": department,
            "cost": cost_end,
            "months_left": rem_months,
            "base_cost": base_price,
        })

        increase_date = ov.get("increase_date") or start_d
        if isinstance(increase_date, str):
            try:
                increase_date = date.fromisoformat(increase_date[:10])
            except Exception:
                increase_date = start_d
        account_cost = ov.get("account_cost") or DEFAULT_ACCOUNT_COST
        account_depreciation = ov.get("account_depreciation") or DEFAULT_ACCOUNT_DEPRECIATION
        account_expense = ov.get("account_expense") or DEFAULT_ACCOUNT_EXPENSE
        expense_item = ov.get("expense_item") or ""

        v_no = ov.get("voucher_no")
        if not v_no:
            note_str = str(v.get("note") or "")
            m_search = re.search(r"chứng từ\s+(\S+)", note_str, re.IGNORECASE)
            if m_search:
                v_no = m_search.group(1).rstrip("),.")
            else:
                v_no = f"GTXE{vid:05d}"
        increase_reason = ov.get("increase_reason") or "Mua mới"

        decreased_on = decrease["voucher_date"] if (decrease_idx is not None and decrease_idx <= end_idx) else None
        status = "Đã ghi giảm" if decreased_on else "Đang sử dụng"

        row_item = {
            "item_key": f"vehicle:{vid}",
            "asset_code": asset_code,
            "asset_name": asset_name,
            "asset_type": asset_type,
            "department": department,
            "increase_date": increase_date,
            "depreciation_start_date": start_d,
            "original_price": cost_end,
            "calculation_value": cost_end,
            "usage_months_total": used + months_left,
            "monthly_depreciation": monthly_end,
            "prior_usage_months": prior_months,
            "prior_depreciation_value": prior_val,
            "monthly_kh": m_kh,
            "year_depreciation_total": total_yr,
            "total_usage_months": used_months,
            "cumulative_depreciation": accum_kh,
            "remaining_usage_months": rem_months,
            "remaining_value": rem_val,
            "account_cost": account_cost,
            "account_depreciation": account_depreciation,
            "account_expense": account_expense,
            "expense_item": expense_item,
            "source": "vehicle",
            "source_id": vid,
            "vehicle_id": vid,
            "voucher_no": v_no,
            "increase_reason": increase_reason,
            "decreased_on": decreased_on,
            "status": status,
            "edit": {
                "source": "vehicle",
                "source_id": vid,
                "asset_code": asset_code,
                "asset_name": asset_name,
                "asset_type": asset_type,
                "department": base_department,
                "increase_date": increase_date.isoformat() if isinstance(increase_date, (date, datetime)) else (str(increase_date)[:10] if increase_date else ""),
                "start_date": start_d.isoformat() if isinstance(start_d, (date, datetime)) else (str(start_d)[:10] if start_d else ""),
                "original_price": base_price,
                "usage_months": total_months,
                "account_cost": account_cost,
                "account_depreciation": account_depreciation,
                "account_expense": account_expense,
                "expense_item": expense_item,
                "voucher_no": v_no,
                "increase_reason": increase_reason,
            },
        }
        rows.append(row_item)

        totals["original_price"] += cost_end
        totals["calculation_value"] += cost_end
        totals["usage_months_total"] += used + months_left
        totals["monthly_depreciation"] += monthly_end
        totals["prior_usage_months"] += prior_months
        totals["prior_depreciation_value"] += prior_val
        for idx in range(12):
            totals["monthly_kh"][idx] += m_kh[idx]
        totals["year_depreciation_total"] += total_yr
        totals["total_usage_months"] += used_months
        totals["cumulative_depreciation"] += accum_kh
        totals["remaining_usage_months"] += rem_months
        totals["remaining_value"] += rem_val

    # Process additional enterprise_assets if any
    for a in assets:
        aid = int(a["id"])
        ov = overrides.get(("asset", aid)) or {}
        if ov.get("is_deleted"):
            continue

        code = str(a.get("asset_code") or f"TS-{aid}").strip()
        name = str(a.get("name") or "").strip()
        base_price = int(a.get("purchase_cost") or 0)
        orig_price = base_price
        dur_years = int(a.get("depreciation_years") or 5)
        total_months = int(ov["usage_months"]) if ov.get("usage_months") else (dur_years * 12 if dur_years > 0 else 60)
        start_d = a.get("acquired_date") or date(year, 1, 1)
        if isinstance(start_d, str):
            try:
                start_d = date.fromisoformat(start_d[:10])
            except Exception:
                start_d = date(year, 1, 1)

        asset_code = ov.get("asset_code") or code
        asset_name = ov.get("asset_name") or name
        default_asset_type = "Thiết bị, dụng cụ quản lý" if a.get("category") == "equipment" else "Phương tiện vận tải, truyền dẫn"
        asset_type = ov.get("asset_type") or default_asset_type
        department = ov.get("department") or str(a.get("assigned_to") or "BAN GIÁM ĐỐC").strip()
        base_department = department

        item_vouchers = vouchers.get(("asset", aid), [])
        decrease = next((it for it in item_vouchers if it["kind"] == "decrease"), None)
        decrease_idx = _month_index(decrease["voucher_date"]) if decrease else None

        transfers = [it for it in item_vouchers if it["kind"] == "transfer" and it["voucher_date"] <= period_end]
        if transfers:
            latest_transfer = max(transfers, key=lambda it: (it["voucher_date"], it["id"]))
            if latest_transfer.get("to_dept"):
                department = latest_transfer["to_dept"]

        label = f"{asset_code} - {asset_name}" if asset_code != asset_name else asset_code

        if decrease_idx is not None and decrease_idx < year_start:
            all_items.append({
                "key": f"asset:{aid}",
                "label": label,
                "code": asset_code,
                "name": asset_name,
                "department": department,
                "cost": orig_price,
                "months_left": 0,
                "base_cost": base_price,
            })
            continue

        revals = [(_month_index(it["voucher_date"]), int(it["new_cost"]), int(it["new_months"])) for it in item_vouchers if it["kind"] == "revalue" and it["voucher_date"] <= period_end]
        revals.sort(key=lambda x: x[0])

        amounts, cost_end, monthly_end, used, months_left = _depreciation_schedule(start_d, total_months, orig_price, revals, end_idx, decrease_idx)
        prior_val = sum(a for mi, a in amounts.items() if mi < year_start)
        prior_months = sum(1 for mi in amounts if mi < year_start)
        m_kh = [amounts.get(year_start + k, 0) for k in range(12)]
        total_yr = sum(m_kh)
        used_months = used
        accum_kh = prior_val + total_yr
        rem_months = months_left
        rem_val = max(0, cost_end - accum_kh)

        all_items.append({
            "key": f"asset:{aid}",
            "label": label,
            "code": asset_code,
            "name": asset_name,
            "department": department,
            "cost": cost_end,
            "months_left": rem_months,
            "base_cost": base_price,
        })

        increase_date = ov.get("increase_date") or start_d
        if isinstance(increase_date, str):
            try:
                increase_date = date.fromisoformat(increase_date[:10])
            except Exception:
                increase_date = start_d
        account_cost = ov.get("account_cost") or DEFAULT_ACCOUNT_COST
        account_depreciation = ov.get("account_depreciation") or DEFAULT_ACCOUNT_DEPRECIATION
        account_expense = ov.get("account_expense") or DEFAULT_ACCOUNT_EXPENSE
        expense_item = ov.get("expense_item") or ""

        v_no = ov.get("voucher_no")
        if not v_no:
            note_str = str(a.get("note") or "")
            m_search = re.search(r"chứng từ\s+(\S+)", note_str, re.IGNORECASE)
            if m_search:
                v_no = m_search.group(1).rstrip("),.")
            else:
                v_no = f"GTTS{aid:05d}"
        increase_reason = ov.get("increase_reason") or "Mua mới"

        decreased_on = decrease["voucher_date"] if (decrease_idx is not None and decrease_idx <= end_idx) else None
        status = "Đã ghi giảm" if decreased_on else "Đang sử dụng"

        row_item = {
            "item_key": f"asset:{aid}",
            "asset_code": asset_code,
            "asset_name": asset_name,
            "asset_type": asset_type,
            "department": department,
            "increase_date": increase_date,
            "depreciation_start_date": start_d,
            "original_price": cost_end,
            "calculation_value": cost_end,
            "usage_months_total": used + months_left,
            "monthly_depreciation": monthly_end,
            "prior_usage_months": prior_months,
            "prior_depreciation_value": prior_val,
            "monthly_kh": m_kh,
            "year_depreciation_total": total_yr,
            "total_usage_months": used_months,
            "cumulative_depreciation": accum_kh,
            "remaining_usage_months": rem_months,
            "remaining_value": rem_val,
            "account_cost": account_cost,
            "account_depreciation": account_depreciation,
            "account_expense": account_expense,
            "expense_item": expense_item,
            "source": "asset",
            "source_id": aid,
            "asset_id": aid,
            "voucher_no": v_no,
            "increase_reason": increase_reason,
            "decreased_on": decreased_on,
            "status": status,
            "edit": {
                "source": "asset",
                "source_id": aid,
                "asset_code": asset_code,
                "asset_name": asset_name,
                "asset_type": asset_type,
                "department": base_department,
                "increase_date": increase_date.isoformat() if isinstance(increase_date, (date, datetime)) else (str(increase_date)[:10] if increase_date else ""),
                "start_date": start_d.isoformat() if isinstance(start_d, (date, datetime)) else (str(start_d)[:10] if start_d else ""),
                "original_price": base_price,
                "usage_months": total_months,
                "account_cost": account_cost,
                "account_depreciation": account_depreciation,
                "account_expense": account_expense,
                "expense_item": expense_item,
                "voucher_no": v_no,
                "increase_reason": increase_reason,
            },
        }
        rows.append(row_item)

        totals["original_price"] += cost_end
        totals["calculation_value"] += cost_end
        totals["usage_months_total"] += used + months_left
        totals["monthly_depreciation"] += monthly_end
        totals["prior_usage_months"] += prior_months
        totals["prior_depreciation_value"] += prior_val
        for idx in range(12):
            totals["monthly_kh"][idx] += m_kh[idx]
        totals["year_depreciation_total"] += total_yr
        totals["total_usage_months"] += used_months
        totals["cumulative_depreciation"] += accum_kh
        totals["remaining_usage_months"] += rem_months
        totals["remaining_value"] += rem_val

    def _as_date(value: Any) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except Exception:
            return date.max

    rows.sort(key=lambda r: (
        _as_date(r.get("increase_date")),
        _as_date(r.get("depreciation_start_date")),
        0 if r.get("source") == "vehicle" else 1,
        int(r.get("source_id") or 0),
    ))

    return {
        "company_id": company_id,
        "company_name": company_name,
        "company": company,
        "year": year,
        "as_of_month": as_of_month,
        "full_year": full_year,
        "rows": rows,
        "totals": totals,
        "row_count": len(rows),
        "hidden_vehicles": hidden_vehicles,
        "all_items": all_items,
        "vouchers": vouchers_list,
    }


def parse_fixed_asset_form(form: dict) -> tuple[dict | None, str | None]:
    """Parse and validate fixed asset form submission."""
    asset_name = str(form.get("asset_name") or "").strip()
    if not asset_name:
        return None, "Vui lòng nhập tên tài sản."
    asset_name = asset_name[:255]

    asset_code = str(form.get("asset_code") or "").strip()[:100]
    asset_type = str(form.get("asset_type") or "").strip()[:255]
    department = str(form.get("department") or "").strip()[:255]
    expense_item = str(form.get("expense_item") or "").strip()[:255]

    raw_price = str(form.get("original_price") or "").replace(".", "").replace(",", "").replace(" ", "").strip()
    try:
        price = int(raw_price)
        if price <= 0:
            return None, "Nguyên giá phải là số lớn hơn 0."
    except Exception:
        return None, "Nguyên giá phải là số lớn hơn 0."

    raw_start = str(form.get("start_date") or "").strip()
    if not raw_start:
        return None, "Ngày bắt đầu tính khấu hao không hợp lệ."
    try:
        start_d = date.fromisoformat(raw_start[:10])
    except Exception:
        return None, "Ngày bắt đầu tính khấu hao không hợp lệ."

    raw_inc = str(form.get("increase_date") or "").strip()
    if not raw_inc:
        inc_d = start_d
    else:
        try:
            inc_d = date.fromisoformat(raw_inc[:10])
        except Exception:
            return None, "Ngày ghi tăng không hợp lệ."

    raw_months = str(form.get("usage_months") or "").strip()
    try:
        months = int(raw_months)
        if months < 1:
            return None, "Thời gian sử dụng phải là số tháng lớn hơn 0."
    except ValueError:
        return None, "Thời gian sử dụng phải là số tháng lớn hơn 0."

    account_cost = str(form.get("account_cost") or "").strip() or DEFAULT_ACCOUNT_COST
    account_depreciation = str(form.get("account_depreciation") or "").strip() or DEFAULT_ACCOUNT_DEPRECIATION
    account_expense = str(form.get("account_expense") or "").strip() or DEFAULT_ACCOUNT_EXPENSE

    if not (re.fullmatch(r"[0-9]{3,6}", account_cost) and account_cost.startswith("211")):
        return None, "TK nguyên giá phải thuộc nhóm 211 (ví dụ 21113)."

    if not (re.fullmatch(r"[0-9]{3,6}", account_depreciation) and account_depreciation.startswith("214")):
        return None, "TK khấu hao phải thuộc nhóm 214 (ví dụ 2141)."

    if not (re.fullmatch(r"[0-9]{3,6}", account_expense) and any(account_expense.startswith(p) for p in ("154", "241", "642", "811"))):
        return None, "TK chi phí phải thuộc nhóm 154, 241, 642 hoặc 811 (ví dụ 6422)."

    voucher_no = str(form.get("voucher_no") or "").strip()[:50]
    increase_reason = str(form.get("increase_reason") or "").strip()[:255]

    values = {
        "asset_code": asset_code,
        "asset_name": asset_name,
        "asset_type": asset_type,
        "department": department,
        "original_price": price,
        "start_date": start_d,
        "increase_date": inc_d,
        "usage_months": months,
        "account_cost": account_cost,
        "account_depreciation": account_depreciation,
        "account_expense": account_expense,
        "expense_item": expense_item,
        "voucher_no": voucher_no,
        "increase_reason": increase_reason,
    }
    return values, None


def _upsert_override(
    cur,
    company_id: int,
    source: str,
    source_id: int,
    fields: dict,
    is_deleted: bool = False,
) -> None:
    cols = ["company_id", "source", "source_id", "is_deleted", "updated_at"] + list(fields.keys())
    placeholders = ["%s", "%s", "%s", "%s", "now()"] + ["%s"] * len(fields)
    values = [company_id, source, source_id, is_deleted] + list(fields.values())

    update_clauses = [f"{col} = EXCLUDED.{col}" for col in fields.keys()]
    update_clauses.append("is_deleted = EXCLUDED.is_deleted")
    update_clauses.append("updated_at = now()")

    sql = f"""
        INSERT INTO {_OVERRIDE_TABLE} ({", ".join(cols)})
        VALUES ({", ".join(placeholders)})
        ON CONFLICT (company_id, source, source_id) DO UPDATE SET
            {", ".join(update_clauses)}
    """
    cur.execute(sql, tuple(values))


def _category_for(asset_type: str) -> str:
    at = asset_type or ""
    if "Phương tiện" in at:
        return "vehicle"
    if "Máy móc" in at:
        return "equipment"
    if "dụng cụ" in at:
        return "tool"
    return "other"


def _years_for(months: int) -> int | None:
    return (months // 12) if (months > 0 and months % 12 == 0) else None


def add_fixed_asset(values: dict, company_id: int | None = None) -> tuple[bool, str]:
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return False, "Chưa chọn công ty làm việc."

    cid = int(company_id)
    m = int(values.get("usage_months") or 60)
    dep_years = _years_for(m) or max(1, round(m / 12))
    cat = _category_for(values.get("asset_type") or "")
    code = values.get("asset_code") or None

    with db.transaction() as cur:
        cur.execute("""
            INSERT INTO public.enterprise_assets
                (company_id, asset_code, name, category, serial_number, acquired_date,
                 purchase_cost, assigned_to, status, note, depreciation_years)
            VALUES (%s, %s, %s, %s, '', %s, %s, %s, 'in_use', 'Thêm từ Bảng tính khấu hao TSCĐ', %s)
            RETURNING id
        """, (
            cid,
            code,
            values.get("asset_name"),
            cat,
            values.get("start_date"),
            values.get("original_price"),
            values.get("department") or "",
            dep_years,
        ))
        row = cur.fetchone()
        new_id = row["id"]

        _upsert_override(
            cur,
            cid,
            "asset",
            new_id,
            {
                "voucher_no": values.get("voucher_no") or None,
                "increase_reason": values.get("increase_reason") or None,
                "asset_type": values.get("asset_type") or "",
                "increase_date": values.get("increase_date"),
                "usage_months": m,
                "account_cost": values.get("account_cost") or DEFAULT_ACCOUNT_COST,
                "account_depreciation": values.get("account_depreciation") or DEFAULT_ACCOUNT_DEPRECIATION,
                "account_expense": values.get("account_expense") or DEFAULT_ACCOUNT_EXPENSE,
                "expense_item": values.get("expense_item") or "",
            },
            is_deleted=False,
        )

    return True, "Đã thêm tài sản vào bảng khấu hao."


def update_fixed_asset(source: str, source_id: int, values: dict, company_id: int | None = None) -> tuple[bool, str]:
    if source not in ("vehicle", "asset"):
        return False, "Loại tài sản không hợp lệ."
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return False, "Chưa chọn công ty làm việc."

    cid = int(company_id)
    sid = int(source_id)
    m = int(values.get("usage_months") or 60)
    dep_years = _years_for(m)

    with db.transaction() as cur:
        if source == "vehicle":
            cur.execute("""
                UPDATE public.plate_whitelist
                SET purchase_price = %s,
                    depreciation_start_date = %s,
                    depreciation_years = COALESCE(%s, depreciation_years),
                    updated_at = now()
                WHERE id = %s AND company_id = %s
                RETURNING id
            """, (values.get("original_price"), values.get("start_date"), dep_years, sid, cid))
            row = cur.fetchone()
            if not row:
                return False, "Không tìm thấy tài sản để sửa."

            _upsert_override(
                cur,
                cid,
                "vehicle",
                sid,
                {
                    "voucher_no": values.get("voucher_no") or None,
                    "increase_reason": values.get("increase_reason") or None,
                    "asset_code": values.get("asset_code") or "",
                    "asset_name": values.get("asset_name") or "",
                    "asset_type": values.get("asset_type") or "",
                    "department": values.get("department") or "",
                    "increase_date": values.get("increase_date"),
                    "usage_months": m,
                    "account_cost": values.get("account_cost") or DEFAULT_ACCOUNT_COST,
                    "account_depreciation": values.get("account_depreciation") or DEFAULT_ACCOUNT_DEPRECIATION,
                    "account_expense": values.get("account_expense") or DEFAULT_ACCOUNT_EXPENSE,
                    "expense_item": values.get("expense_item") or "",
                },
                is_deleted=False,
            )
        else:
            code = values.get("asset_code") or None
            cur.execute("""
                UPDATE public.enterprise_assets
                SET asset_code = %s,
                    name = %s,
                    assigned_to = %s,
                    acquired_date = %s,
                    purchase_cost = %s,
                    depreciation_years = COALESCE(%s, depreciation_years),
                    updated_at = now()
                WHERE id = %s AND company_id = %s
                RETURNING id
            """, (code, values.get("asset_name"), values.get("department") or "", values.get("start_date"), values.get("original_price"), dep_years, sid, cid))
            row = cur.fetchone()
            if not row:
                return False, "Không tìm thấy tài sản để sửa."

            _upsert_override(
                cur,
                cid,
                "asset",
                sid,
                {
                    "voucher_no": values.get("voucher_no") or None,
                    "increase_reason": values.get("increase_reason") or None,
                    "asset_code": None,
                    "asset_name": None,
                    "department": None,
                    "asset_type": values.get("asset_type") or "",
                    "increase_date": values.get("increase_date"),
                    "usage_months": m,
                    "account_cost": values.get("account_cost") or DEFAULT_ACCOUNT_COST,
                    "account_depreciation": values.get("account_depreciation") or DEFAULT_ACCOUNT_DEPRECIATION,
                    "account_expense": values.get("account_expense") or DEFAULT_ACCOUNT_EXPENSE,
                    "expense_item": values.get("expense_item") or "",
                },
                is_deleted=False,
            )

    return True, "Đã lưu thay đổi."


def delete_fixed_asset(source: str, source_id: int, company_id: int | None = None) -> tuple[bool, str]:
    if source not in ("vehicle", "asset"):
        return False, "Loại tài sản không hợp lệ."
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return False, "Chưa chọn công ty làm việc."

    cid = int(company_id)
    sid = int(source_id)

    with db.transaction() as cur:
        if source == "vehicle":
            cur.execute("SELECT id FROM public.plate_whitelist WHERE id = %s AND company_id = %s", (sid, cid))
            row = cur.fetchone()
            if not row:
                return False, "Không tìm thấy tài sản để xoá."
            _upsert_override(cur, cid, "vehicle", sid, {}, is_deleted=True)
            return True, "Đã xoá xe khỏi bảng khấu hao (xe vẫn còn trong danh sách xe)."
        else:
            cur.execute("DELETE FROM public.enterprise_assets WHERE id = %s AND company_id = %s RETURNING id", (sid, cid))
            row = cur.fetchone()
            if not row:
                return False, "Không tìm thấy tài sản để xoá."
            cur.execute(f"DELETE FROM {_OVERRIDE_TABLE} WHERE company_id = %s AND source = 'asset' AND source_id = %s", (cid, sid))
            from frontend.modules.transport.asset_vouchers import delete_item_vouchers
            delete_item_vouchers(cur, cid, "tscd", "asset", sid)
            return True, "Đã xoá tài sản."


def restore_vehicle(vehicle_id: int, company_id: int | None = None) -> tuple[bool, str]:
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return False, "Chưa chọn công ty làm việc."

    cid = int(company_id)
    vid = int(vehicle_id)

    row = db._execute_query(
        f"UPDATE {_OVERRIDE_TABLE} SET is_deleted = false, updated_at = now() WHERE company_id = %s AND source = 'vehicle' AND source_id = %s AND is_deleted = true RETURNING id",
        (cid, vid),
        fetch=True,
        commit=True,
    )
    if not row:
        return False, "Không tìm thấy xe đã xoá."
    return True, "Đã đưa xe trở lại bảng khấu hao."


def build_tscd_tab_data(depr_data: dict, year: int) -> dict:
    from frontend.modules.transport.asset_vouchers import voucher_edit_payload

    items_by_key = {r["item_key"]: r for r in depr_data.get("rows", [])}
    all_items = {it["key"]: it for it in depr_data.get("all_items", [])}
    vouchers = depr_data.get("vouchers", [])

    voucher_rows: dict[str, list[dict]] = {
        "revalue": [],
        "transfer": [],
        "decrease": [],
        "inventory": [],
    }

    item_revalue_history: dict[str, list[dict]] = {}

    for v in vouchers:
        kind = v.get("kind")
        if kind not in voucher_rows:
            continue
        key = f"{v['item_source']}:{v['item_id']}"
        it_info = all_items.get(key) or {}
        item_label = it_info.get("label") or "—"
        item_code = it_info.get("code") or "—"
        item_name = it_info.get("name") or "—"

        v_dict = dict(v)
        v_dict["item_label"] = item_label
        v_dict["item_code"] = item_code
        v_dict["item_name"] = item_name
        v_dict["edit"] = voucher_edit_payload(v)

        if kind == "revalue":
            prev_revals = item_revalue_history.setdefault(key, [])
            if prev_revals:
                old_cost = int(prev_revals[-1]["new_cost"])
            else:
                old_cost = int(it_info.get("base_cost") or (v.get("new_cost") or 0))
            new_cost = int(v.get("new_cost") or 0)
            v_dict["old_cost"] = old_cost
            v_dict["diff"] = new_cost - old_cost
            prev_revals.append(v)
        elif kind == "inventory":
            b_qty = float(v.get("book_qty") or 0)
            a_qty = float(v.get("actual_qty") or 0)
            v_dict["qty_diff"] = a_qty - b_qty
        elif kind == "decrease":
            row = items_by_key.get(key)
            if row:
                v_dict["cost"] = row["original_price"]
                v_dict["accum"] = row["cumulative_depreciation"]
                v_dict["remaining"] = row["remaining_value"]
            else:
                v_dict["cost"] = None
                v_dict["accum"] = None
                v_dict["remaining"] = None

        voucher_rows[kind].append(v_dict)

    increase_rows = sorted(
        depr_data.get("rows", []),
        key=lambda r: (r.get("increase_date") or date.min),
        reverse=True,
    )

    rows = depr_data.get("rows", [])

    by_type_map: dict[str, dict] = {}
    for r in rows:
        t = r.get("asset_type") or "Khác"
        if t not in by_type_map:
            by_type_map[t] = {
                "label": t,
                "count": 0,
                "cost": 0,
                "depreciation_year": 0,
                "accum": 0,
                "remaining": 0,
            }
        entry = by_type_map[t]
        entry["count"] += 1
        entry["cost"] += r["original_price"]
        entry["depreciation_year"] += r["year_depreciation_total"]
        entry["accum"] += r["cumulative_depreciation"]
        entry["remaining"] += r["remaining_value"]

    by_type_list = list(by_type_map.values())
    by_type_total = {
        "label": "Tổng cộng",
        "count": sum(x["count"] for x in by_type_list),
        "cost": sum(x["cost"] for x in by_type_list),
        "depreciation_year": sum(x["depreciation_year"] for x in by_type_list),
        "accum": sum(x["accum"] for x in by_type_list),
        "remaining": sum(x["remaining"] for x in by_type_list),
    }

    by_dept_map: dict[str, dict] = {}
    for r in rows:
        d = r.get("department") or "Khác"
        if d not in by_dept_map:
            by_dept_map[d] = {
                "label": d,
                "count": 0,
                "cost": 0,
                "depreciation_year": 0,
                "accum": 0,
                "remaining": 0,
            }
        entry = by_dept_map[d]
        entry["count"] += 1
        entry["cost"] += r["original_price"]
        entry["depreciation_year"] += r["year_depreciation_total"]
        entry["accum"] += r["cumulative_depreciation"]
        entry["remaining"] += r["remaining_value"]

    by_dept_list = list(by_dept_map.values())
    by_dept_total = {
        "label": "Tổng cộng",
        "count": sum(x["count"] for x in by_dept_list),
        "cost": sum(x["cost"] for x in by_dept_list),
        "depreciation_year": sum(x["depreciation_year"] for x in by_dept_list),
        "accum": sum(x["accum"] for x in by_dept_list),
        "remaining": sum(x["remaining"] for x in by_dept_list),
    }

    inc_in_yr = [r for r in rows if r.get("increase_date") and getattr(r.get("increase_date"), "year", None) == year]
    dec_in_yr = [v for v in voucher_rows["decrease"] if getattr(v.get("voucher_date"), "year", None) == year]
    reval_in_yr = [v for v in voucher_rows["revalue"] if getattr(v.get("voucher_date"), "year", None) == year]
    trans_in_yr = [v for v in voucher_rows["transfer"] if getattr(v.get("voucher_date"), "year", None) == year]
    inv_in_yr = [v for v in voucher_rows["inventory"] if getattr(v.get("voucher_date"), "year", None) == year]

    movements = {
        "increase": {
            "count": len(inc_in_yr),
            "value": sum(r["original_price"] for r in inc_in_yr),
        },
        "decrease": {
            "count": len(dec_in_yr),
        },
        "revalue": {
            "count": len(reval_in_yr),
            "diff": sum(v.get("diff", 0) for v in reval_in_yr),
        },
        "transfer": {
            "count": len(trans_in_yr),
        },
        "inventory": {
            "count": len(inv_in_yr),
            "diff_count": sum(1 for v in inv_in_yr if v.get("qty_diff", 0) != 0),
        },
    }

    return {
        "voucher_rows": voucher_rows,
        "increase_rows": increase_rows,
        "report": {
            "by_type": by_type_list,
            "by_type_total": by_type_total,
            "by_department": by_dept_list,
            "by_dept_total": by_dept_total,
            "movements": movements,
        },
    }


def export_depreciation_excel(
    company_id: int | None = None,
    year: int = 2026,
    as_of_month: int | None = None,
    full_year: bool = False,
) -> bytes:
    """Build depreciation schedule Excel workbook following the exact accounting template."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return b""
    data = get_depreciation_data(company_id=company_id, year=year, as_of_month=as_of_month, full_year=full_year)
    rows = data["rows"]
    totals = data["totals"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Báo cáo"
    ws.views.sheetView[0].showGridLines = True

    # Column widths matching the standard template
    col_widths = {
        "A": 12.0, "B": 14.0, "C": 24.0, "D": 16.0, "E": 14.0, "F": 14.0,
        "G": 16.0, "H": 16.0, "I": 12.0, "J": 14.0, "K": 12.0, "L": 16.0,
        "M": 13.0, "N": 13.0, "O": 13.0, "P": 13.0, "Q": 13.0, "R": 13.0,
        "S": 13.0, "T": 13.0, "U": 13.0, "V": 13.0, "W": 13.0, "X": 13.0,
        "Y": 16.0, "Z": 12.0, "AA": 16.0, "AB": 12.0, "AC": 16.0,
        "AD": 12.0, "AE": 12.0, "AF": 12.0, "AG": 14.0,
    }
    for col_letter, width in col_widths.items():
        ws.column_dimensions[col_letter].width = width

    ws.row_dimensions[1].height = 24.0
    ws.row_dimensions[2].height = 18.0
    ws.row_dimensions[3].height = 28.0
    ws.row_dimensions[4].height = 20.0

    # Fonts & Styles
    font_title = Font(name="Times New Roman", size=14, bold=True, color="000000")
    font_sub = Font(name="Times New Roman", size=11, bold=True, color="000000")
    font_header = Font(name="Microsoft Sans Serif", size=8.5, bold=True, color="000000")
    font_data = Font(name="Microsoft Sans Serif", size=8.5, bold=False, color="000000")
    font_total = Font(name="Microsoft Sans Serif", size=8.5, bold=True, color="000000")

    fill_header = PatternFill(start_color="ADC7E7", end_color="ADC7E7", fill_type="solid")

    border_thin = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )
    border_header = Border(
        left=Side(style="thin", color="8FAADC"),
        right=Side(style="thin", color="8FAADC"),
        top=Side(style="thin", color="8FAADC"),
        bottom=Side(style="thin", color="8FAADC"),
    )
    border_total = Border(
        left=Side(style="thin", color="A0A0A0"),
        right=Side(style="thin", color="A0A0A0"),
        top=Side(style="thin", color="000000"),
        bottom=Side(style="double", color="000000"),
    )

    fmt_money = "#,##0_);[Red](#,##0)"
    fmt_date = "dd/MM/yyyy"

    # Row 1: Title
    ws.cell(1, 1, "BẢNG TÍNH KHẤU HAO TÀI SẢN CỐ ĐỊNH THEO NĂM")
    ws.cell(1, 1).font = font_title
    ws.cell(1, 1).alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells("A1:AG1")

    # Row 2: Year
    mode_text = f"Năm: {year}" + (" (Đến tháng hiện tại)" if not full_year else " (Cả năm 12 tháng)")
    ws.cell(2, 1, mode_text)
    ws.cell(2, 1).font = font_sub
    ws.cell(2, 1).alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells("A2:AG2")

    # Row 3 & 4: Headers
    headers_r3 = [
        ("Mã TSCĐ", 1, 1),
        ("Tên TSCĐ", 2, 2),
        ("Loại TSCĐ", 3, 3),
        ("Đơn vị sử dụng", 4, 4),
        ("Ngày ghi tăng", 5, 5),
        ("Ngày bắt đầu tính khấu hao", 6, 6),
        ("Nguyên giá TSCĐ", 7, 7),
        ("Giá trị tính KH", 8, 8),
        ("Thời gian sử dụng (tháng)", 9, 9),
        ("Giá trị khấu hao tháng", 10, 10),
        ("Thời gian đã sử dụng trước đó (tháng)", 11, 11),
        ("Giá trị đã khấu hao trước đó", 12, 12),
        ("Giá trị khấu hao theo tháng", 13, 25),  # Spans M3:Y3
        ("Tổng thời gian đã sử dụng (tháng)", 26, 26),
        ("Hao mòn lũy kế", 27, 27),
        ("Thời gian SD còn lại (tháng)", 28, 28),
        ("Giá trị còn lại", 29, 29),
        ("TK nguyên giá", 30, 30),
        ("TK khấu hao", 31, 31),
        ("TK chi phí", 32, 32),
        ("Khoản mục chi phí", 33, 33),
    ]

    for title, col_start, col_end in headers_r3:
        cell = ws.cell(3, col_start, title)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border_header
        if col_start != col_end:
            for c in range(col_start + 1, col_end + 1):
                c_cell = ws.cell(3, c)
                c_cell.font = font_header
                c_cell.fill = fill_header
                c_cell.border = border_header
            ws.merge_cells(start_row=3, start_column=col_start, end_row=3, end_column=col_end)
        else:
            # Merge down to row 4
            d_cell = ws.cell(4, col_start)
            d_cell.font = font_header
            d_cell.fill = fill_header
            d_cell.border = border_header
            ws.merge_cells(start_row=3, start_column=col_start, end_row=4, end_column=col_end)

    # Sub-headers in Row 4 for M..Y
    sub_headers_m_y = [
        "Tháng 1", "Tháng 2", "Tháng 3", "Tháng 4", "Tháng 5", "Tháng 6",
        "Tháng 7", "Tháng 8", "Tháng 9", "Tháng 10", "Tháng 11", "Tháng 12", "Tổng cộng",
    ]
    for idx, sub_title in enumerate(sub_headers_m_y):
        c_idx = 13 + idx
        cell = ws.cell(4, c_idx, sub_title)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border_header

    # Data rows
    row_idx = 5
    for r in rows:
        ws.row_dimensions[row_idx].height = 20.0
        start_d = r["increase_date"]
        d_val = datetime(start_d.year, start_d.month, start_d.day) if isinstance(start_d, date) else None

        row_data = [
            r["asset_code"],
            r["asset_name"],
            r["asset_type"],
            r["department"],
            d_val,
            d_val,
            r["original_price"],
            r["calculation_value"],
            r["usage_months_total"],
            r["monthly_depreciation"],
            r["prior_usage_months"],
            r["prior_depreciation_value"],
            *r["monthly_kh"],
            r["year_depreciation_total"],
            r["total_usage_months"],
            r["cumulative_depreciation"],
            r["remaining_usage_months"],
            r["remaining_value"],
            r["account_cost"],
            r["account_depreciation"],
            r["account_expense"],
            r["expense_item"] or None,
        ]

        for c_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row_idx, c_idx, val)
            cell.font = font_data
            cell.border = border_thin
            if c_idx in [1, 2, 3, 4, 30, 31, 32, 33]:
                cell.alignment = Alignment(horizontal="left", vertical="center")
            elif c_idx in [5, 6]:
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.number_format = fmt_date
            elif c_idx in [9, 11, 26, 28]:
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.number_format = "#,##0"
            else:
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.number_format = fmt_money

        row_idx += 1

    # Total row
    ws.row_dimensions[row_idx].height = 22.0
    tot_row_data = [
        f"Số dòng = {len(rows)}", None, None, None, None, None,
        totals["original_price"],
        totals["calculation_value"],
        totals["usage_months_total"],
        totals["monthly_depreciation"],
        totals["prior_usage_months"],
        totals["prior_depreciation_value"],
        *totals["monthly_kh"],
        totals["year_depreciation_total"],
        totals["total_usage_months"],
        totals["cumulative_depreciation"],
        totals["remaining_usage_months"],
        totals["remaining_value"],
        None, None, None, None,
    ]

    for c_idx, val in enumerate(tot_row_data, 1):
        cell = ws.cell(row_idx, c_idx, val)
        cell.font = font_total
        cell.border = border_total
        if c_idx == 1:
            cell.alignment = Alignment(horizontal="left", vertical="center")
        elif c_idx in [5, 6]:
            cell.alignment = Alignment(horizontal="center", vertical="center")
        elif c_idx in [9, 11, 26, 28]:
            cell.alignment = Alignment(horizontal="right", vertical="center")
            cell.number_format = "#,##0"
        elif val is not None:
            cell.alignment = Alignment(horizontal="right", vertical="center")
            cell.number_format = fmt_money

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _as_iso_date(val: Any) -> str:
    if isinstance(val, datetime):
        return val.date().isoformat()
    if isinstance(val, date):
        return val.isoformat()
    return str(val or "")[:10]


def _as_int_amount(val: Any) -> int:
    try:
        return int(round(float(val or 0)))
    except Exception:
        return 0


def match_pending_tscd_invoices(invoices: list[dict], voucher_nos: list[str],
                                assets: list[dict] | None = None) -> list[dict]:
    """Trả về các hóa đơn CHƯA có TSCĐ tương ứng.

    Đã có TSCĐ khi Số chứng từ của TSCĐ trùng số hóa đơn, hoặc (khi người dùng thêm TSCĐ mà không nhập Số chứng từ)
    có TSCĐ cùng nguyên giá và cùng ngày với hóa đơn."""
    vouchers = set()
    for v in voucher_nos:
        norm_v = normalize_invoice_number(v)
        if norm_v:
            vouchers.add(norm_v)
    cost_dates = {
        (_as_int_amount(a.get("purchase_cost")), _as_iso_date(a.get("acquired_date")))
        for a in (assets or []) if _as_int_amount(a.get("purchase_cost")) > 0
    }

    pending = []
    for inv in invoices:
        inv_num_raw = str(inv.get("invoice_number") or "").strip()
        inv_num = normalize_invoice_number(inv_num_raw)
        if inv_num and inv_num in vouchers:
            continue
        if (_as_int_amount(inv.get("subtotal")), _as_iso_date(inv.get("invoice_date"))) in cost_dates:
            continue
        pending.append(inv)

    return pending


def list_pending_tscd_invoices(company_id: int | None = None) -> list[dict]:
    """Retrieve input invoices debited to TK 211 not yet recorded as fixed assets (TSCĐ)."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return []
    company_id = int(company_id)

    inv_sql = """
        SELECT id, invoice_date, serial, invoice_number, seller_name,
               items, subtotal, total, note, tk_no_cost
        FROM public.transport_input_invoices
        WHERE buyer_company_id = %s
          AND COALESCE(NULLIF(TRIM(tk_no_cost), ''), '154') LIKE '211%%'
        ORDER BY invoice_date DESC, id DESC
    """
    invoices = db._execute_query(inv_sql, (company_id,), fetchall=True, commit=False) or []

    ov_sql = """
        SELECT voucher_no
        FROM public.transport_fixed_asset_overrides
        WHERE company_id = %s
          AND COALESCE(is_deleted, false) = false
          AND COALESCE(NULLIF(TRIM(voucher_no), ''), NULL) IS NOT NULL
    """
    ov_rows = db._execute_query(ov_sql, (company_id,), fetchall=True, commit=False) or []
    voucher_nos = [r.get("voucher_no") for r in ov_rows if r.get("voucher_no")]

    asset_rows = db._execute_query(
        "SELECT purchase_cost, acquired_date FROM public.enterprise_assets WHERE company_id = %s",
        (company_id,), fetchall=True, commit=False,
    ) or []

    unmatched = match_pending_tscd_invoices(invoices, voucher_nos, asset_rows)

    results = []
    for inv in unmatched:
        inv_d_raw = inv.get("invoice_date")
        if isinstance(inv_d_raw, str):
            try:
                inv_date = date.fromisoformat(inv_d_raw[:10])
            except Exception:
                inv_date = date.today()
        elif isinstance(inv_d_raw, datetime):
            inv_date = inv_d_raw.date()
        elif isinstance(inv_d_raw, date):
            inv_date = inv_d_raw
        else:
            inv_date = date.today()

        inv_num = str(inv.get("invoice_number") or "").strip()
        serial = str(inv.get("serial") or "").strip()
        seller = str(inv.get("seller_name") or "").strip()

        items_raw = inv.get("items")
        if isinstance(items_raw, str):
            try:
                items_list = json.loads(items_raw)
            except Exception:
                items_list = []
        elif isinstance(items_raw, list):
            items_list = items_raw
        else:
            items_list = []

        desc_parts = []
        for it in items_list:
            if isinstance(it, dict):
                part = str(it.get("description") or it.get("name") or "").strip()
                if part:
                    desc_parts.append(part)

        if desc_parts:
            description = "; ".join(desc_parts)[:200]
            name = desc_parts[0][:255]
        else:
            note_str = str(inv.get("note") or "").strip()
            description = note_str[:200]
            name = (note_str[:255] if note_str else "") or (f"TSCĐ HĐ {inv_num}" if inv_num else "TSCĐ")

        try:
            subtotal = int(round(float(inv.get("subtotal") or 0)))
        except Exception:
            subtotal = 0

        try:
            total = int(round(float(inv.get("total") or 0)))
        except Exception:
            total = 0

        raw_tk = str(inv.get("tk_no_cost") or "").strip()
        if re.match(r"^211[0-9]{0,3}$", raw_tk) and len(raw_tk) >= 5:
            account_cost = raw_tk
        else:
            account_cost = DEFAULT_ACCOUNT_COST

        results.append({
            "id": inv.get("id"),
            "date_iso": inv_date.strftime("%Y-%m-%d"),
            "date_display": inv_date.strftime("%d/%m/%Y"),
            "month": inv_date.strftime("%Y-%m"),
            "invoice_number": inv_num,
            "serial": serial,
            "seller_name": seller,
            "description": description,
            "subtotal": subtotal,
            "total": total,
            "name": name,
            "account_cost": account_cost,
        })

    return results
