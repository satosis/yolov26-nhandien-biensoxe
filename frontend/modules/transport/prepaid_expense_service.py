"""Service for Prepaid Expenses & CCDC Allocation (TK 242) matching Phuong Nam Excel format."""

from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import json
import logging
import math
import re
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from backend.services.database import db
from frontend.context.company_context import get_current_company_id
from frontend.modules.transport.asset_vouchers import load_vouchers, vouchers_by_item
from frontend.formatting.text import fold_text, normalize_invoice_number

logger = logging.getLogger(__name__)

DAI_LO_XANH_COMPANY_ID = 33

PREPAID_CATEGORY = "Chi phí trả trước"
_PREPAID_WORDS = ("bao hiem", "duong bo", "chu ky so", "phan mem", "ten mien", "hosting",
                  "thue kho", "thue van phong", "thue mat bang")


def is_prepaid_expense(category: str | None, name: str | None) -> bool:
    """A prepaid expense (insurance, road fee, digital signature, software, rent) as against a tool (CCDC)."""
    if str(category or "").strip() == PREPAID_CATEGORY:
        return True
    folded = fold_text(name or "")
    return any(word in folded for word in _PREPAID_WORDS)


def _month_index(d: date | datetime) -> int:
    return d.year * 12 + d.month - 1


def _allocation_schedule(
    s_date: date,
    orig: Decimal,
    m_alloc: Decimal,
    adjustments: list[tuple[int, int | float | Decimal, int]],
    end_idx: int,
    decrease_idx: int | None = None,
    acc_reset: Decimal | None = None,
    reset_idx: int | None = None,
) -> tuple[dict[int, Decimal], Decimal, Decimal]:
    """Decimal. adjustments: [(month_idx, new_cost, new_months)] tăng dần.
    acc_reset: lũy kế nhập tay (accumulated_prev) áp tại tháng reset_idx (tháng 1 năm đang xem).
    Trả (amounts{month_idx: Decimal}, orig_end, m_alloc_end)."""
    amounts = {}
    acc = Decimal(0)
    pending = list(adjustments)
    mi = _month_index(s_date)
    first_idx = mi
    last = end_idx if decrease_idx is None else min(end_idx, decrease_idx)
    while mi <= last:
        if acc_reset is not None and mi == reset_idx:
            acc = min(orig, acc_reset)
        while pending and pending[0][0] <= mi:
            _, orig, months = pending.pop(0)
            orig = Decimal(orig)
            m_alloc = Decimal(str(round(float(max(Decimal(0), orig - acc)) / months, 2))) if months > 0 else Decimal(0)
        remaining = orig - acc
        if remaining <= 0:
            amt = Decimal(0)
        elif decrease_idx is not None and mi == decrease_idx:
            amt = remaining  # ghi giảm: phân bổ nốt giá trị còn lại
        elif mi == first_idx:
            dim = calendar.monthrange(s_date.year, s_date.month)[1]
            used_days = max(1, dim - s_date.day + 1)
            amt = min(remaining, Decimal(str(round(float(m_alloc) * used_days / dim, 0))))
        else:
            amt = min(remaining, m_alloc)
        amounts[mi] = amt
        acc += amt
        mi += 1
    return amounts, orig, m_alloc


def get_prepaid_expenses_data(
    company_id: int | None = None,
    year: int | None = None,
    as_of_month: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Retrieve full allocation schedule for TK 242 across 12 months of the year.

    `kind` "prepaid" keeps only the prepaid expenses, "ccdc" only the tools; anything else keeps both."""
    kind = kind if kind in ("ccdc", "prepaid") else None
    if company_id is None:
        company_id = get_current_company_id()

    today = date.today()
    selected_year = int(year) if year and str(year).isdigit() and 2000 <= int(year) <= 2100 else today.year
    active_month = as_of_month if as_of_month and 1 <= as_of_month <= 12 else (today.month if today.year == selected_year else 12)

    if not company_id or int(company_id) <= 0:
        return {
            "selected_year": selected_year,
            "active_month": active_month,
            "date_from": (date_from or date(selected_year, 1, 1)).strftime("%Y-%m-%d"),
            "date_to": (date_to or date(selected_year, 12, 31)).strftime("%Y-%m-%d"),
            "date_from_display": (date_from or date(selected_year, 1, 1)).strftime("%d/%m/%Y"),
            "date_to_display": (date_to or date(selected_year, 12, 31)).strftime("%d/%m/%Y"),
            "period_months": [],
            "items": [],
            "totals": {
                "original_cost": 0,
                "acc_start": 0,
                "rem_start": 0,
                "months": [0] * 12,
                "alloc_year": 0,
                "period_alloc": 0,
                "period_months": [],
                "m_start": 1,
                "m_end": 12,
                "acc_end": 0,
                "rem_end": 0,
                "count": 0,
            },
            "all_items": [],
            "vouchers": [],
        }


    vouchers = vouchers_by_item(int(company_id), "ccdc")
    all_vouchers = load_vouchers(int(company_id), "ccdc")

    rows_db = db._execute_query('''
        SELECT id, item_code, name, unit, quantity, start_date, total_months,
               original_cost, accumulated_prev, monthly_allocation, offset_account, category, note,
               voucher_no, increase_reason
        FROM public.transport_prepaid_expenses
        WHERE company_id = %s
        ORDER BY start_date ASC, id ASC
    ''', (company_id,), fetchall=True, commit=False) or []

    items = []
    all_items = []
    tot_original = Decimal(0)
    tot_acc_start = Decimal(0)
    tot_rem_start = Decimal(0)
    tot_alloc_year = Decimal(0)
    tot_acc_end = Decimal(0)
    tot_rem_end = Decimal(0)
    tot_month = [Decimal(0) for _ in range(12)]
    by_account: dict[str, list[Decimal]] = {}  # Nợ TK đối ứng (154, 642...) -> phân bổ từng tháng

    year_start = selected_year * 12
    end_idx = year_start + 11

    for idx, r in enumerate(rows_db, 1):
        if kind and is_prepaid_expense(r["category"], r["name"]) != (kind == "prepaid"):
            continue
        category = PREPAID_CATEGORY if is_prepaid_expense(r["category"], r["name"]) else (r["category"] or "CCDC")
        orig = Decimal(str(r["original_cost"] or 0))
        tot_m = max(1, int(r["total_months"] or 36))
        s_date_raw = r["start_date"]
        if isinstance(s_date_raw, str):
            try:
                s_date = date.fromisoformat(s_date_raw[:10])
            except Exception:
                s_date = date.today()
        elif isinstance(s_date_raw, (date, datetime)):
            s_date = s_date_raw if isinstance(s_date_raw, date) else s_date_raw.date()
        else:
            s_date = date.today()

        unit = r["unit"] or "Chiếc"
        qty = r["quantity"] or 1
        off_acc = r["offset_account"] or "642"
        code_display = r["item_code"] or f"CCDC-{idx:02d}"

        if r["monthly_allocation"] and float(r["monthly_allocation"]) > 0:
            m_alloc = Decimal(str(round(float(r["monthly_allocation"]), 2)))
        else:
            m_alloc = Decimal(str(round(float(orig) / tot_m, 2)))

        item_v = vouchers.get(("ccdc", int(r["id"])), [])
        transfers = [v for v in item_v if v["kind"] == "transfer" and v["voucher_date"] <= date(selected_year, 12, 31)]
        transfers.sort(key=lambda v: (v["voucher_date"], v.get("id", 0)))
        dept = transfers[-1]["to_dept"] if transfers else ""

        decrease = next((v for v in item_v if v["kind"] == "decrease"), None)
        decrease_idx = _month_index(decrease["voucher_date"]) if decrease else None

        if decrease_idx is not None and decrease_idx < year_start:
            all_items.append({
                "key": f"ccdc:{r['id']}",
                "label": f"{code_display} - {r['name']}",
                "code": code_display,
                "name": r["name"],
                "department": dept,
                "cost": int(round(orig)),
                "months_left": 0,
                "base_cost": int(round(orig)),
            })
            continue

        adjustments = [(_month_index(v["voucher_date"]), int(v["new_cost"]), int(v["new_months"])) for v in item_v if v["kind"] == "revalue"]
        adjustments.sort(key=lambda x: x[0])

        use_reset = s_date < date(selected_year, 1, 1) and r["accumulated_prev"] and float(r["accumulated_prev"]) > 0
        amounts, orig_end, m_alloc_end = _allocation_schedule(
            s_date, orig, m_alloc, adjustments, end_idx, decrease_idx,
            Decimal(str(r["accumulated_prev"])) if use_reset else None, year_start,
        )

        prior_adjs = [adj for adj in adjustments if adj[0] < year_start]
        orig_start = Decimal(str(prior_adjs[-1][1])) if prior_adjs else orig

        acc_start = Decimal(str(r["accumulated_prev"])) if use_reset else sum((a for mi, a in amounts.items() if mi < year_start), Decimal(0))
        acc_start = min(orig_start, acc_start)

        m_vals = [amounts.get(year_start + k, Decimal(0)) for k in range(12)]
        alloc_year = sum(m_vals)
        acc_end = acc_start + alloc_year
        rem_start = max(Decimal(0), orig_start - acc_start)
        rem_end = max(Decimal(0), orig_end - acc_end)

        tot_original += orig_end
        tot_acc_start += acc_start
        tot_rem_start += rem_start
        tot_alloc_year += alloc_year
        tot_acc_end += acc_end
        tot_rem_end += rem_end
        for k in range(12):
            tot_month[k] += m_vals[k]
            by_account.setdefault(off_acc, [Decimal(0)] * 12)[k] += m_vals[k]

        m_left = math.ceil(float(rem_end) / float(m_alloc_end)) if (m_alloc_end and m_alloc_end > 0 and rem_end > 0) else 0

        all_items.append({
            "key": f"ccdc:{r['id']}",
            "label": f"{code_display} - {r['name']}",
            "code": code_display,
            "name": r["name"],
            "department": dept,
            "cost": int(round(orig_end)),
            "months_left": m_left,
            "base_cost": int(round(orig)),
        })

        v_no = (r.get("voucher_no") or "").strip()
        if not v_no:
            note_str = str(r.get("note") or "")
            m_search = re.search(r"chứng từ\s+(\S+)", note_str, re.IGNORECASE)
            if m_search:
                v_no = m_search.group(1).rstrip("),.")
            else:
                v_no = f"GTCC{int(r['id']):05d}"
        inc_reason = (r.get("increase_reason") or "").strip() or "Mua mới"

        decreased_on = decrease["voucher_date"] if (decrease_idx is not None and year_start <= decrease_idx <= end_idx) else None
        status = "Đã ghi giảm" if decreased_on else "Đang sử dụng"

        items.append({
            "id": r["id"],
            "stt": len(items) + 1 if kind else idx,
            "item_code": code_display,
            "name": r["name"],
            "unit": unit,
            "quantity": qty,
            "start_date": s_date.strftime("%d/%m/%Y") if s_date else "",
            "start_date_raw": s_date,
            "start_date_iso": s_date.isoformat() if isinstance(s_date, (date, datetime)) else str(s_date)[:10],
            "total_months": tot_m,
            "total_years": round(tot_m / 12, 1),
            "original_cost": int(round(orig_end)),
            "acc_start": int(round(acc_start)),
            "rem_start": int(round(rem_start)),
            "monthly_alloc": int(round(m_alloc_end)),
            "months": [int(round(v)) for v in m_vals],
            "alloc_year": int(round(alloc_year)),
            "acc_end": int(round(acc_end)),
            "rem_end": int(round(rem_end)),
            "offset_account": off_acc,
            "category": category,
            "note": r["note"] or "",
            "department": dept,
            "item_key": f"ccdc:{r['id']}",
            "decreased_on": decreased_on,
            "status": status,
            "voucher_no": v_no,
            "increase_reason": inc_reason,
            "edit": {
                "id": r["id"],
                "item_code": r["item_code"] or "",
                "name": r["name"] or "",
                "unit": unit,
                "quantity": float(qty),
                "start_date": s_date.isoformat() if isinstance(s_date, (date, datetime)) else str(s_date)[:10],
                "total_months": tot_m,
                "original_cost": int(round(orig)),
                "accumulated_prev": int(round(Decimal(str(r["accumulated_prev"] or 0)))),
                "offset_account": off_acc,
                "category": category,
                "note": r["note"] or "",
                "voucher_no": v_no,
                "increase_reason": inc_reason,
            },
        })

    # Tính toán kỳ phân bổ được lọc theo date_from -> date_to
    m_start = max(1, date_from.month) if (date_from and date_from.year == selected_year) else 1
    m_end = min(12, date_to.month) if (date_to and date_to.year == selected_year) else 12
    if m_start > m_end:
        m_start, m_end = 1, 12
    period_months = list(range(m_start, m_end + 1))

    for item in items:
        item["period_alloc"] = sum(item["months"][m - 1] for m in period_months)

    tot_period_alloc = sum(int(round(tot_month[m - 1])) for m in period_months)

    totals = {
        "original_cost": int(round(tot_original)),
        "acc_start": int(round(tot_acc_start)),
        "rem_start": int(round(tot_rem_start)),
        "months": [int(round(v)) for v in tot_month],
        "by_account": [
            {"account": acc, "months": [int(round(v)) for v in vals], "alloc_year": int(round(sum(vals)))}
            for acc, vals in sorted(by_account.items())
        ],
        "alloc_year": int(round(tot_alloc_year)),
        "period_alloc": tot_period_alloc,
        "period_months": period_months,
        "m_start": m_start,
        "m_end": m_end,
        "acc_end": int(round(tot_acc_end)),
        "rem_end": int(round(tot_rem_end)),
        "count": len(items),
    }

    d_from_eff = date_from or date(selected_year, m_start, 1)
    last_d = 31 if m_end in (1, 3, 5, 7, 8, 10, 12) else (30 if m_end in (4, 6, 9, 11) else 28)
    d_to_eff = date_to or date(selected_year, m_end, last_d)

    from frontend.modules.transport.cash_bank_service import get_company_info
    company_info = get_company_info(int(company_id))

    if kind:
        kept = {it["key"] for it in all_items}
        all_vouchers = [v for v in all_vouchers if f"{v['item_source']}:{v['item_id']}" in kept]
    title = {
        "prepaid": f"BẢNG PHÂN BỔ CHI PHÍ TRẢ TRƯỚC (TK 242) NĂM {selected_year}",
        "ccdc": f"BẢNG PHÂN BỔ CÔNG CỤ DỤNG CỤ NĂM {selected_year}",
    }.get(kind, f"BẢNG PHÂN BỔ CHI PHÍ TRẢ TRƯỚC DÀI HẠN (TK 242) NĂM {selected_year}")

    return {
        "kind": kind or "all",
        "title": title,
        "company_name": company_info["name"],
        "company_address": company_info["address"],
        "tax_code": company_info["tax_code"],
        "selected_year": selected_year,
        "active_month": active_month,
        "date_from": d_from_eff.strftime("%Y-%m-%d"),
        "date_to": d_to_eff.strftime("%Y-%m-%d"),
        "date_from_display": d_from_eff.strftime("%d/%m/%Y"),
        "date_to_display": d_to_eff.strftime("%d/%m/%Y"),
        "period_months": period_months,
        "items": items,
        "totals": totals,
        "all_items": all_items,
        "vouchers": all_vouchers,
    }


def parse_ccdc_form(form: dict) -> tuple[dict | None, str | None]:
    name = str(form.get("name") or "").strip()
    if not name:
        return None, "Vui lòng nhập tên CCDC."
    name = name[:255]

    item_code = str(form.get("item_code") or "").strip()[:50]
    unit = str(form.get("unit") or "").strip()[:50] or "Chiếc"

    raw_qty = str(form.get("quantity") or "1").replace(",", ".").strip()
    try:
        qty = float(raw_qty)
        if qty <= 0:
            return None, "Số lượng phải lớn hơn 0."
    except Exception:
        return None, "Số lượng phải lớn hơn 0."

    raw_start = str(form.get("start_date") or "").strip()
    if not raw_start:
        return None, "Ngày ghi tăng không hợp lệ."
    try:
        start_d = date.fromisoformat(raw_start[:10])
    except Exception:
        return None, "Ngày ghi tăng không hợp lệ."

    raw_months = str(form.get("total_months") or "").strip()
    try:
        months = int(raw_months)
        if months < 1:
            return None, "Số kỳ phân bổ phải là số lớn hơn 0."
    except ValueError:
        return None, "Số kỳ phân bổ phải là số lớn hơn 0."

    raw_cost = str(form.get("original_cost") or "").replace(".", "").replace(",", "").replace(" ", "").strip()
    try:
        cost = int(raw_cost)
        if cost <= 0:
            return None, "Giá trị CCDC phải lớn hơn 0."
    except Exception:
        return None, "Giá trị CCDC phải lớn hơn 0."

    raw_acc = str(form.get("accumulated_prev") or "0").replace(".", "").replace(",", "").replace(" ", "").strip()
    if not raw_acc:
        acc = 0
    else:
        try:
            acc = int(raw_acc)
            if not (0 <= acc <= cost):
                return None, "Lũy kế đầu năm không hợp lệ."
        except Exception:
            return None, "Lũy kế đầu năm không hợp lệ."

    offset_account = str(form.get("offset_account") or "").strip() or "642"
    if not (re.fullmatch(r"[0-9]{3,6}", offset_account) and any(offset_account.startswith(p) for p in ("154", "241", "642", "811"))):
        return None, "TK chi phí phải thuộc nhóm 154, 241, 642 hoặc 811."

    category = str(form.get("category") or "").strip()[:100] or "CCDC"
    note = str(form.get("note") or "").strip()[:500]
    voucher_no = str(form.get("voucher_no") or "").strip()[:50]
    increase_reason = str(form.get("increase_reason") or "").strip()[:255]

    values = {
        "item_code": item_code,
        "name": name,
        "unit": unit,
        "quantity": qty,
        "start_date": start_d,
        "total_months": months,
        "original_cost": cost,
        "accumulated_prev": acc,
        "offset_account": offset_account,
        "category": category,
        "note": note,
        "voucher_no": voucher_no,
        "increase_reason": increase_reason,
    }
    return values, None


def add_prepaid_expense(
    company_id: int,
    item_code: str,
    name: str,
    unit: str,
    quantity: float,
    start_date: str | date,
    total_months: int,
    original_cost: float | Decimal,
    accumulated_prev: float | Decimal = 0,
    offset_account: str = "642",
    category: str = "CCDC",
    note: str = "",
    voucher_no: str = "",
    increase_reason: str = "",
) -> tuple[bool, str, int]:
    """Add a new prepaid expense/CCDC."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return False, "Chưa chọn công ty làm việc.", 0
    try:
        orig_clean = str(original_cost).replace(".", "").replace(",", "").replace(" ", "").strip()
        orig = Decimal(orig_clean)
        acc_clean = str(accumulated_prev or 0).replace(".", "").replace(",", "").replace(" ", "").strip() or "0"
        acc = Decimal(acc_clean)
    except Exception:
        return False, "Nguyên giá không hợp lệ.", 0

    if not name.strip():
        return False, "Tên công cụ dụng cụ không được để trống.", 0

    s_date = date.today()
    if isinstance(start_date, str) and start_date.strip():
        try:
            s_date = datetime.strptime(start_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            s_date = date.today()
    elif isinstance(start_date, date):
        s_date = start_date

    tot_m = max(1, int(total_months or 36))
    m_alloc = Decimal(str(round(float(orig) / tot_m, 2)))


    row = db._execute_query('''
        INSERT INTO public.transport_prepaid_expenses
        (company_id, item_code, name, unit, quantity, start_date, total_months,
         original_cost, accumulated_prev, monthly_allocation, offset_account, category, note,
         voucher_no, increase_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    ''', (
        company_id, item_code.strip() or "CCDC-MỚI", name.strip(), unit.strip() or "Chiếc",
        quantity or 1, s_date, tot_m, orig, acc, m_alloc, offset_account.strip() or "642",
        category.strip() or "CCDC", note.strip(),
        (voucher_no or "").strip(), (increase_reason or "").strip(),
    ), fetch=True, commit=True)

    ins_id = row["id"] if row else 0
    return True, "Đã thêm CCDC phân bổ 242 thành công.", ins_id


def update_prepaid_expense(item_id: int, values: dict, company_id: int | None = None) -> tuple[bool, str]:
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return False, "Chưa chọn công ty làm việc."
    cid = int(company_id)
    iid = int(item_id)
    orig = Decimal(str(values.get("original_cost") or 0))
    tot_m = max(1, int(values.get("total_months") or 36))
    m_alloc = Decimal(str(round(float(orig) / tot_m, 2)))
    s_date = values.get("start_date")
    if isinstance(s_date, str):
        try:
            s_date = date.fromisoformat(s_date[:10])
        except Exception:
            s_date = date.today()

    row = db._execute_query('''
        UPDATE public.transport_prepaid_expenses
        SET item_code = %s,
            name = %s,
            unit = %s,
            quantity = %s,
            start_date = %s,
            total_months = %s,
            original_cost = %s,
            accumulated_prev = %s,
            monthly_allocation = %s,
            offset_account = %s,
            category = %s,
            note = %s,
            voucher_no = %s,
            increase_reason = %s
        WHERE id = %s AND company_id = %s
        RETURNING id
    ''', (
        (values.get("item_code") or "").strip(),
        (values.get("name") or "").strip(),
        (values.get("unit") or "Chiếc").strip(),
        float(values.get("quantity") or 1),
        s_date,
        tot_m,
        orig,
        Decimal(str(values.get("accumulated_prev") or 0)),
        m_alloc,
        (values.get("offset_account") or "642").strip(),
        (values.get("category") or "CCDC").strip(),
        (values.get("note") or "").strip(),
        (values.get("voucher_no") or "").strip(),
        (values.get("increase_reason") or "").strip(),
        iid,
        cid,
    ), fetch=True, commit=True)
    if not row:
        return False, "Không tìm thấy CCDC để sửa."
    return True, "Đã lưu CCDC."


def delete_prepaid_expense(item_id: int, company_id: int | None = None) -> tuple[bool, str]:
    """Delete a prepaid expense item and its related vouchers."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return False, "Chưa chọn công ty làm việc."
    cid = int(company_id)
    iid = int(item_id)
    with db.transaction() as cur:
        cur.execute(
            "DELETE FROM public.transport_prepaid_expenses WHERE id = %s AND company_id = %s RETURNING id",
            (iid, cid),
        )
        row = cur.fetchone()
        if not row:
            return False, "Không tìm thấy CCDC để xóa."
        from frontend.modules.transport.asset_vouchers import delete_item_vouchers
        delete_item_vouchers(cur, cid, "ccdc", "ccdc", iid)
    return True, "Đã xóa CCDC thành công."


def build_ccdc_tab_data(pe_data: dict, year: int) -> dict:
    from frontend.modules.transport.asset_vouchers import voucher_edit_payload

    items_by_key = {r["item_key"]: r for r in pe_data.get("items", [])}
    all_items = {it["key"]: it for it in pe_data.get("all_items", [])}
    vouchers = pe_data.get("vouchers", [])

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
                v_dict["cost"] = row["original_cost"]
                v_dict["accum"] = row["acc_end"]
                v_dict["remaining"] = row["rem_end"]
            else:
                v_dict["cost"] = None
                v_dict["accum"] = None
                v_dict["remaining"] = None

        voucher_rows[kind].append(v_dict)

    increase_rows = sorted(
        pe_data.get("items", []),
        key=lambda r: (r.get("start_date_raw") or date.min),
        reverse=True,
    )

    items = pe_data.get("items", [])

    by_cat_map: dict[str, dict] = {}
    for r in items:
        cat = r.get("category") or "CCDC"
        if cat not in by_cat_map:
            by_cat_map[cat] = {
                "label": cat,
                "count": 0,
                "cost": 0,
                "alloc_year": 0,
                "acc_end": 0,
                "rem_end": 0,
            }
        entry = by_cat_map[cat]
        entry["count"] += 1
        entry["cost"] += r["original_cost"]
        entry["alloc_year"] += r["alloc_year"]
        entry["acc_end"] += r["acc_end"]
        entry["rem_end"] += r["rem_end"]

    by_cat_list = list(by_cat_map.values())
    by_cat_total = {
        "label": "Tổng cộng",
        "count": sum(x["count"] for x in by_cat_list),
        "cost": sum(x["cost"] for x in by_cat_list),
        "alloc_year": sum(x["alloc_year"] for x in by_cat_list),
        "acc_end": sum(x["acc_end"] for x in by_cat_list),
        "rem_end": sum(x["rem_end"] for x in by_cat_list),
    }

    by_dept_map: dict[str, dict] = {}
    for r in items:
        d = (r.get("department") or "").strip() or "Chưa phân bộ phận"
        if d not in by_dept_map:
            by_dept_map[d] = {
                "label": d,
                "count": 0,
                "cost": 0,
                "alloc_year": 0,
                "acc_end": 0,
                "rem_end": 0,
            }
        entry = by_dept_map[d]
        entry["count"] += 1
        entry["cost"] += r["original_cost"]
        entry["alloc_year"] += r["alloc_year"]
        entry["acc_end"] += r["acc_end"]
        entry["rem_end"] += r["rem_end"]

    by_dept_list = list(by_dept_map.values())
    by_dept_total = {
        "label": "Tổng cộng",
        "count": sum(x["count"] for x in by_dept_list),
        "cost": sum(x["cost"] for x in by_dept_list),
        "alloc_year": sum(x["alloc_year"] for x in by_dept_list),
        "acc_end": sum(x["acc_end"] for x in by_dept_list),
        "rem_end": sum(x["rem_end"] for x in by_dept_list),
    }

    inc_in_yr = [r for r in items if r.get("start_date_raw") and getattr(r.get("start_date_raw"), "year", None) == year]
    dec_in_yr = [v for v in voucher_rows["decrease"] if getattr(v.get("voucher_date"), "year", None) == year]
    reval_in_yr = [v for v in voucher_rows["revalue"] if getattr(v.get("voucher_date"), "year", None) == year]
    trans_in_yr = [v for v in voucher_rows["transfer"] if getattr(v.get("voucher_date"), "year", None) == year]
    inv_in_yr = [v for v in voucher_rows["inventory"] if getattr(v.get("voucher_date"), "year", None) == year]

    movements = {
        "increase": {
            "count": len(inc_in_yr),
            "value": sum(r["original_cost"] for r in inc_in_yr),
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
            "by_category": by_cat_list,
            "by_category_total": by_cat_total,
            "by_department": by_dept_list,
            "by_dept_total": by_dept_total,
            "movements": movements,
        },
    }


def export_prepaid_expenses_excel(
    company_id: int | None = None,
    year: int = 2026,
    date_from: date | None = None,
    date_to: date | None = None,
    kind: str | None = None,
) -> bytes:
    """Export TK 242 allocation schedule to Excel matching Phuong Nam layout."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return b""
    data = get_prepaid_expenses_data(company_id=company_id, year=year, date_from=date_from, date_to=date_to, kind=kind)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{'CPTT' if data['kind'] == 'prepaid' else '242'}-{year}"
    ws.views.sheetView[0].showGridLines = True

    font_title = Font(name="Times New Roman", size=13, bold=True, color="1E3A8A")
    font_bold = Font(name="Times New Roman", size=9.5, bold=True)
    font_regular = Font(name="Times New Roman", size=9.5)
    Font(name="Times New Roman", size=8.5, italic=True)

    border_thin = Side(border_style="thin", color="000000")
    border_double = Side(border_style="double", color="000000")
    box_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

    fill_header1 = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    fill_total = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")

    # Header info
    ws["A1"] = data["company_name"]
    ws["A1"].font = font_bold
    ws["A2"] = data["company_address"]
    ws["A2"].font = font_regular
    ws["A3"] = f"MST: {data['tax_code']}"
    ws["A3"].font = font_regular

    ws.merge_cells("A5:W5")
    ws["A5"] = data["title"]
    ws["A5"].font = font_title
    ws["A5"].alignment = Alignment(horizontal="center")

    # Table Header Rows 7-9
    # Cols:
    # 1: STT
    # 2: Tên CCDC / Thiết bị
    # 3: Đơn vị tính
    # 4: Ngày đưa vào SD
    # 5: Số lượng
    # 6: Thời gian PB (tháng)
    # 7: Nguyên giá
    # 8: Phân bổ lũy kế đầu năm
    # 9: Giá trị còn lại đầu năm
    # 10: Phân bổ 1 tháng
    # 11-22: Tháng 1 -> Tháng 12
    # 23: Lũy kế phân bổ năm nay
    # 24: Giá trị còn lại cuối năm
    # 25: TK Hạch toán

    headers = [
        "STT", "Khoản chi phí trả trước" if data["kind"] == "prepaid" else "Tên CCDC / Thiết bị", "ĐVT", "Ngày đưa vào SD", "SL", "Thời gian PB (tháng)",
        "Nguyên giá", "Lũy kế đầu năm", "Còn lại đầu năm", "Phân bổ 1 tháng",
        "Tháng 1", "Tháng 2", "Tháng 3", "Tháng 4", "Tháng 5", "Tháng 6",
        "Tháng 7", "Tháng 8", "Tháng 9", "Tháng 10", "Tháng 11", "Tháng 12",
        "Tổng PB năm nay", "Còn lại cuối năm", "TK Nợ"
    ]

    for col_idx, h_text in enumerate(headers, 1):
        cell = ws.cell(row=7, column=col_idx, value=h_text)
        cell.font = font_bold
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.fill = fill_header1
        cell.border = box_border

    curr_r = 8
    for item in data["items"]:
        ws.cell(row=curr_r, column=1, value=item["stt"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_r, column=2, value=item["name"]).alignment = Alignment(horizontal="left")
        ws.cell(row=curr_r, column=3, value=item["unit"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_r, column=4, value=item["start_date"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_r, column=5, value=item["quantity"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_r, column=6, value=item["total_months"]).alignment = Alignment(horizontal="center")

        ws.cell(row=curr_r, column=7, value=item["original_cost"]).number_format = "#,##0"
        ws.cell(row=curr_r, column=8, value=item["acc_start"]).number_format = "#,##0"
        ws.cell(row=curr_r, column=9, value=item["rem_start"]).number_format = "#,##0"
        ws.cell(row=curr_r, column=10, value=item["monthly_alloc"]).number_format = "#,##0"

        for m_idx in range(12):
            val = item["months"][m_idx]
            c = ws.cell(row=curr_r, column=11 + m_idx, value=val if val > 0 else "")
            c.number_format = "#,##0"
            c.alignment = Alignment(horizontal="right")

        ws.cell(row=curr_r, column=23, value=item["alloc_year"]).number_format = "#,##0"
        ws.cell(row=curr_r, column=24, value=item["rem_end"]).number_format = "#,##0"
        ws.cell(row=curr_r, column=25, value=item["offset_account"]).alignment = Alignment(horizontal="center")

        for c in range(1, 26):
            ws.cell(row=curr_r, column=c).border = box_border
            ws.cell(row=curr_r, column=c).font = font_regular

        curr_r += 1

    # Dòng Tổng cộng
    ws.merge_cells(f"A{curr_r}:F{curr_r}")
    ws[f"A{curr_r}"] = "Tổng cộng"
    ws[f"A{curr_r}"].font = font_bold
    ws[f"A{curr_r}"].alignment = Alignment(horizontal="center")

    t = data["totals"]
    ws.cell(row=curr_r, column=7, value=t["original_cost"]).number_format = "#,##0"
    ws.cell(row=curr_r, column=8, value=t["acc_start"]).number_format = "#,##0"
    ws.cell(row=curr_r, column=9, value=t["rem_start"]).number_format = "#,##0"
    ws.cell(row=curr_r, column=10, value="")

    for m_idx in range(12):
        c = ws.cell(row=curr_r, column=11 + m_idx, value=t["months"][m_idx])
        c.number_format = "#,##0"
        c.font = font_bold
        c.alignment = Alignment(horizontal="right")

    ws.cell(row=curr_r, column=23, value=t["alloc_year"]).number_format = "#,##0"
    ws.cell(row=curr_r, column=24, value=t["rem_end"]).number_format = "#,##0"
    ws.cell(row=curr_r, column=25, value="")

    for c in range(1, 26):
        ws.cell(row=curr_r, column=c).border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_double)
        ws.cell(row=curr_r, column=c).fill = fill_total
        ws.cell(row=curr_r, column=c).font = font_bold

    curr_r += 2
    # Dòng hạch toán kế toán Nợ 642 / Có 242 (Giống hệt dòng 19-20 trong Excel Phương Nam)
    ws[f"B{curr_r}"] = "Định khoản kế toán:"
    ws[f"B{curr_r}"].font = font_bold
    for acc in t.get("by_account") or [{"account": "642", "months": t["months"]}]:
        label = {"154": " (Chi phí SXKD dịch vụ)", "642": " (Chi phí QLDN)"}.get(acc["account"], "")
        ws[f"C{curr_r}"] = f"Nợ {acc['account']}{label}"
        ws[f"C{curr_r}"].font = font_bold
        for m_idx in range(12):
            c = ws.cell(row=curr_r, column=11 + m_idx, value=acc["months"][m_idx])
            c.number_format = "#,##0"
            c.font = font_bold
        curr_r += 1
    ws[f"C{curr_r}"] = "Có 242 (Chi phí trả trước)"
    ws[f"C{curr_r}"].font = font_bold
    for m_idx in range(12):
        c = ws.cell(row=curr_r, column=11 + m_idx, value=t["months"][m_idx])
        c.number_format = "#,##0"
        c.font = font_bold

    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 15
    ws.column_dimensions["E"].width = 8
    ws.column_dimensions["F"].width = 14
    ws.column_dimensions["G"].width = 17
    ws.column_dimensions["H"].width = 17
    ws.column_dimensions["I"].width = 17
    ws.column_dimensions["J"].width = 15
    for m in range(11, 23):
        ws.column_dimensions[get_column_letter(m)].width = 13
    ws.column_dimensions["W"].width = 18
    ws.column_dimensions["X"].width = 18
    ws.column_dimensions["Y"].width = 10

    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def match_pending_ccdc_invoices(invoices: list[dict], ccdc_rows: list[dict]) -> list[dict]:
    """Trả về các hóa đơn CHƯA có CCDC tương ứng."""
    ccdc_vouchers = set()
    ccdc_note_digit_tokens = set()

    for c in ccdc_rows:
        v = normalize_invoice_number(c.get("voucher_no"))
        if v:
            ccdc_vouchers.add(v)
        note = str(c.get("note") or "")
        for d in re.findall(r"\d+", note):
            clean_d = d.lstrip("0") or "0"
            ccdc_note_digit_tokens.add(clean_d)

    pending = []
    for inv in invoices:
        inv_num_raw = str(inv.get("invoice_number") or "").strip()
        inv_num = normalize_invoice_number(inv_num_raw)
        if not inv_num:
            pending.append(inv)
            continue

        is_matched = False
        if inv_num in ccdc_vouchers:
            is_matched = True
        elif inv_num.isdigit() and len(inv_num) >= 3 and inv_num in ccdc_note_digit_tokens:
            is_matched = True

        if not is_matched:
            pending.append(inv)

    return pending


def list_pending_ccdc_invoices(company_id: int | None = None) -> list[dict]:
    """Retrieve input invoices debited to TK 153 not yet recorded as CCDC."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return []
    company_id = int(company_id)


    inv_sql = """
        SELECT id, invoice_date, serial, invoice_number, seller_name,
               items, subtotal, total, note
        FROM public.transport_input_invoices
        WHERE buyer_company_id = %s
          AND (COALESCE(NULLIF(TRIM(tk_no_cost), ''), '154') LIKE '153%%'
               OR COALESCE(NULLIF(TRIM(tk_no_cost), ''), '154') LIKE '242%%')
        ORDER BY invoice_date DESC, id DESC
    """
    invoices = db._execute_query(inv_sql, (company_id,), fetchall=True, commit=False) or []

    ccdc_sql = """
        SELECT voucher_no, note
        FROM public.transport_prepaid_expenses
        WHERE company_id = %s
    """
    ccdc_rows = db._execute_query(ccdc_sql, (company_id,), fetchall=True, commit=False) or []

    unmatched = match_pending_ccdc_invoices(invoices, ccdc_rows)

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
            first_name = desc_parts[0][:255]
        else:
            fallback = str(inv.get("note") or "").strip()
            description = fallback[:200]
            first_name = description[:255] or (f"CCDC HĐ {inv_num}" if inv_num else "CCDC")

        if len(items_list) == 1 and isinstance(items_list[0], dict):
            first_item = items_list[0]
            unit = str(first_item.get("unit") or "").strip() or "Chiếc"
            raw_qty = first_item.get("quantity")
            try:
                qty_num = float(raw_qty) if raw_qty is not None else 1.0
                if qty_num <= 0:
                    quantity: int | float = 1
                elif qty_num.is_integer():
                    quantity = int(qty_num)
                else:
                    quantity = qty_num
            except Exception:
                quantity = 1
        else:
            unit = "Chiếc"
            quantity = 1

        try:
            subtotal = int(round(float(inv.get("subtotal") or 0)))
        except Exception:
            subtotal = 0

        try:
            total = int(round(float(inv.get("total") or 0)))
        except Exception:
            total = 0

        note = f"HĐ {inv_num} - {seller}"[:500]

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
            "name": first_name,
            "unit": unit,
            "quantity": quantity,
            "note": note,
        })

    return results
