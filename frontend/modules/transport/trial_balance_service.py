"""Trial Balance (Bang can doi tai khoan - Mau F01-DNN) service for Dai Lo Xanh.

Aggregates sub-ledger accounts (TK con / TK nho / so chi tiet) into parent accounts (TK cap 1):
- TK 211, 214 from Fixed Asset Depreciation Schedule (Bang tinh khau hao TSCD 2 xe tai 19H-147.98 & 19H-209.07)
- TK 131, 511, 3331 from Output Invoices (92 HD BKAV ban ra)
- TK 331, 133, 154/632 from Input Invoices (175 HD van tai mua vao: xang dau, sua chua, bot)
- TK 242 from Prepaid Expenses (Bao hiem xe, lop xe, CCDC)
- TK 111, 112 from Cash & VietinBank bank accounts (38633888)
- TK 334, 338 from Staff & Driver Payroll (Bang cham cong & bang tinh luong)
- TK 411, 421 from Owner Equity & Retained Earnings
Ensures exact 3 pairs of balanced totals per Circular 133/2016/TT-BTC.
Links with sample workbook: C:\\Users\\admin\\Downloads\\F01_DNN_Bang_can_doi_tai_khoan.xls
"""

from __future__ import annotations

from datetime import date
from io import BytesIO
import json
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from backend.services.database import db
from frontend.context.company_context import get_current_company_id

DAI_LO_XANH_COMPANY_ID = 33


def get_tb_overrides() -> dict[str, dict]:
    company_id = get_current_company_id()
    if not company_id:
        return {}
    from backend.services.settings_service import get_app_setting
    raw = get_app_setting(f"trial_balance_overrides_{company_id}", "{}")
    try:
        return json.loads(raw)
    except Exception:
        return {}


def save_tb_override(code: str = "", name: str | None = None, open_dr: int | None = None, open_cr: int | None = None) -> bool:
    company_id = get_current_company_id()
    if not company_id:
        return False
    from backend.services.settings_service import set_app_setting
    ovs = get_tb_overrides()
    cur = ovs.get(code, {})
    if name is not None:
        cur["name"] = name.strip()
    if open_dr is not None:
        cur["open_dr"] = int(open_dr)
    if open_cr is not None:
        cur["open_cr"] = int(open_cr)
    ovs[code] = cur
    return set_app_setting(f"trial_balance_overrides_{company_id}", json.dumps(ovs))


def get_tb_custom_accounts() -> list[dict]:
    company_id = get_current_company_id()
    if not company_id:
        return []
    from backend.services.settings_service import get_app_setting
    raw = get_app_setting(f"trial_balance_custom_accounts_{company_id}", "[]")
    try:
        return json.loads(raw)
    except Exception:
        return []


def add_tb_custom_account(code: str = "", name: str = "", parent_code: str = "", open_dr: int = 0, open_cr: int = 0) -> bool:
    company_id = get_current_company_id()
    if not company_id:
        return False
    from backend.services.settings_service import set_app_setting
    accs = get_tb_custom_accounts()
    accs = [a for a in accs if a.get("code") != code]
    accs.append({
        "code": code.strip(),
        "name": name.strip(),
        "parent_code": parent_code.strip(),
        "open_dr": int(open_dr),
        "open_cr": int(open_cr),
        "is_custom": True,
    })
    return set_app_setting(f"trial_balance_custom_accounts_{company_id}", json.dumps(accs))


def delete_tb_account(code: str = "") -> bool:
    company_id = get_current_company_id()
    if not company_id:
        return False
    from backend.services.settings_service import set_app_setting
    accs = get_tb_custom_accounts()
    new_accs = [a for a in accs if a.get("code") != code]
    set_app_setting(f"trial_balance_custom_accounts_{company_id}", json.dumps(new_accs))
    ovs = get_tb_overrides()
    if code in ovs:
        del ovs[code]
        set_app_setting(f"trial_balance_overrides_{company_id}", json.dumps(ovs))
    return True


def is_registration_fee_text(text: str) -> bool:
    t = (text or "").lower()
    return "trước bạ" in t and "thừa" not in t


# Lệ phí trước bạ (ghi TK 3339) là một phần nguyên giá TSCĐ: Nợ 211 / Có 3339 rồi Nợ 3339 / Có 112, nên 3339 không còn dư;
# khoản chi này được tính như chi mua TSCĐ (paid_211), nguyên giá lấy từ Sổ TSCĐ.
# Khoản nộp thừa (diễn giải có chữ "thừa") giữ ở Nợ 3339 chờ hoàn; tiền hoàn về ghi Báo Có / Phiếu thu đối ứng 3339.
def is_registration_fee_entry(e: dict) -> bool:
    text = f"{e.get('description') or ''} {e.get('note') or ''}"
    return str(e.get("offset_account") or "").strip().startswith("3339") and is_registration_fee_text(text)


def get_trial_balance_data(
    company_id: int | None = None,
    year: int = 2026,
    date_from: date | None = None,
    date_to: date | None = None,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    company_id = company_id or get_current_company_id()
    if date_from is None:
        date_from = date(year, 1, 1)
    if date_to is None:
        date_to = as_of_date or date.today()
    as_of_date = date_to

    if not company_id or int(company_id) <= 0:
        empty_tot = {k: 0 for k in ("open_dr", "open_cr", "ps_dr", "ps_cr", "close_dr", "close_cr")}
        return {
            "all_rows": [],
            "rows": [],
            "summary_rows": [],
            "totals": empty_tot,
            "summary_totals": empty_tot.copy(),
            "has_sub_accounts": False,
            "is_balanced": True,
            "diff_open": 0,
            "diff_ps": 0,
            "diff_close": 0,
            "date_from": date_from,
            "date_to": date_to,
            "as_of_date": as_of_date,
            "year": year,
            "company": {"name": "", "tax_code": "", "address": ""},
        }

    # 1. Output invoices (TK 511, 3331, 131)
    out_inv = db._execute_query("""
        SELECT COALESCE(SUM(subtotal), 0) as sub,
               COALESCE(SUM(gross - subtotal), 0) as vat,
               COALESCE(SUM(gross), 0) as gross,
               COUNT(*) as count_inv
        FROM public.bkav_invoices
        WHERE seller_company_id = %s AND invoice_date >= %s AND invoice_date <= %s
    """, (company_id, date_from, date_to), fetch=True, commit=False) or {}

    total_revenue_511 = int(round(float(out_inv.get("sub") or 0)))
    total_vat_out_3331 = int(round(float(out_inv.get("vat") or 0)))
    total_receivable_131 = int(round(float(out_inv.get("gross") or 0)))
    count_inv_out = int(out_inv.get("count_inv") or 0)

    # 2. Input invoices (TK 153, 154/632, 133, 331, 642)
    in_inv_rows = db._execute_query("""
        SELECT COALESCE(NULLIF(TRIM(tk_no_cost), ''), '154') as tk_cost,
               COALESCE(SUM(subtotal), 0) as sub,
               COALESCE(SUM(vat_amount), 0) as vat,
               COALESCE(SUM(total), 0) as total,
               COUNT(*) as count_inv
        FROM public.transport_input_invoices
        WHERE buyer_company_id = %s AND invoice_date >= %s AND invoice_date <= %s
        GROUP BY COALESCE(NULLIF(TRIM(tk_no_cost), ''), '154')
    """, (company_id, date_from, date_to), fetchall=True, commit=False) or []

    total_cost_154 = 0
    total_cost_153 = 0
    count_inv_153 = 0
    total_cost_642_inv = 0
    total_cost_211_inv = 0
    total_vat_in_133 = 0
    total_payable_331 = 0
    count_inv_in = 0

    for r in in_inv_rows:
        tk = str(r.get("tk_cost") or "154").strip()
        sub = int(round(float(r.get("sub") or 0)))
        vat = int(round(float(r.get("vat") or 0)))
        tot = int(round(float(r.get("total") or 0)))
        cnt = int(r.get("count_inv") or 0)

        total_vat_in_133 += vat
        total_payable_331 += tot
        count_inv_in += cnt

        if tk.startswith(("153", "242")):
            # Hóa đơn mua CCDC / chi phí trả trước (153 hoặc 242): chờ phân bổ ở Sổ CCDC, không tính vào giá vốn 154
            total_cost_153 += sub
            count_inv_153 += cnt
        elif tk.startswith("642"):
            total_cost_642_inv += sub
        elif tk.startswith("211"):
            # Mua TSCĐ có hoá đơn: Nợ 211 lấy từ Sổ TSCĐ, không phải chi phí 154
            total_cost_211_inv += sub
        else:
            total_cost_154 += sub

    from frontend.modules.payroll.accounting import period_payroll
    payroll = period_payroll(company_id, date_from, date_to)
    payroll_direct = payroll["gross"]["1542"] + payroll["employer"]["1542"]
    payroll_admin = payroll["gross"]["6422"] + payroll["employer"]["6422"]

    company = db._execute_query(
        "SELECT * FROM public.invoice_companies WHERE id = %s",
        (company_id,), fetch=True, commit=False,
    ) or {}
    company_name = company.get("name") or "CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH"
    director_name = company.get("director_name") or "Chủ sở hữu"

    # 3. Cash, banking & CCDC 242 flows
    from frontend.modules.transport.cash_bank_service import get_cash_book_data, get_bank_book_data
    from frontend.formatting.text import normalize_invoice_number
    from frontend.modules.transport.prepaid_expense_service import get_prepaid_expenses_data
    from frontend.modules.transport.depreciation_service import get_depreciation_data

    selected_year = year or (date_from.year if date_from else date.today().year)
    cash_data = get_cash_book_data(company_id=company_id, year=selected_year, date_from=date_from, date_to=date_to)
    bank_data = get_bank_book_data(company_id=company_id, year=selected_year, date_from=date_from, date_to=date_to)
    pe_data = get_prepaid_expenses_data(company_id=company_id, year=selected_year, date_from=date_from, date_to=date_to)
    pe_tot = pe_data["totals"]

    depr_data = get_depreciation_data(company_id=company_id, year=selected_year, full_year=True)
    # Khấu hao theo kỳ: các tháng nằm trong kỳ là phát sinh, các tháng trước đó (và các năm trước) là hao mòn đầu kỳ.
    # Phần có TK chi phí 154 là chi phí dịch vụ vận tải; còn lại vào 642.
    _dm_from = date_from.month if date_from.year == selected_year else 1
    _dm_to = date_to.month if date_to.year == selected_year else 12
    total_accum_depr_214 = 0
    depr_open_214 = 0
    depr_alloc_154 = 0
    for _r in depr_data.get("rows", []):
        _kh = _r.get("monthly_kh") or [0] * 12
        _r["tb_depr_open"] = int(_r.get("prior_depreciation_value") or 0) + sum(_kh[:_dm_from - 1])
        _r["tb_depr_period"] = sum(_kh[_dm_from - 1:_dm_to])
        depr_open_214 += _r["tb_depr_open"]
        total_accum_depr_214 += _r["tb_depr_period"]
        if str(_r.get("account_expense") or "").strip().startswith("154"):
            depr_alloc_154 += _r["tb_depr_period"]
    depr_alloc_642 = total_accum_depr_214 - depr_alloc_154
    # Phần khấu hao vào 642 tách theo loại tài sản: xe tải và TSCĐ khác (điện thoại, thiết bị quản lý...)
    _depr_642_rows = [_r for _r in depr_data.get("rows", []) if not str(_r.get("account_expense") or "").strip().startswith("154")]
    _depr_vehicles = [_r for _r in _depr_642_rows if _r.get("source") == "vehicle"]
    _depr_others = [_r for _r in _depr_642_rows if _r.get("source") != "vehicle"]
    depr_vehicle_642 = sum(_r["tb_depr_period"] for _r in _depr_vehicles)
    depr_other_642 = sum(_r["tb_depr_period"] for _r in _depr_others)

    cash_open = cash_data["opening_balance"]
    cash_ps_dr = cash_data["total_receipt"]
    cash_ps_cr = cash_data["total_payment"]
    cash_close = cash_data["closing_balance"]

    bank_open = bank_data["opening_balance"]
    bank_ps_dr = bank_data["total_deposit"]
    bank_ps_cr = bank_data["total_withdraw"]
    bank_close = bank_data["closing_balance"]

    cust_paid_cash = int(round(sum(float(e.get("receipt_amount") or 0) for e in cash_data.get("entries", []) if str(e.get("offset_account") or "").startswith("131"))))
    cust_paid_bank = int(round(sum(float(e.get("deposit_amount") or 0) for e in bank_data.get("entries", []) if str(e.get("offset_account") or "").startswith("131"))))
    total_cust_paid = cust_paid_cash + cust_paid_bank

    # Every input invoice is booked Có 331 above, so a payment made from an input invoice settles 331 whatever its offset account (154, 642, 242...)
    def _pays_supplier(e: dict) -> bool:
        return e.get("source_type") == "input_invoice" or str(e.get("offset_account") or "").startswith(("331", "154", "642"))
    supplier_paid_cash = int(round(sum(float(e.get("payment_amount") or 0) for e in cash_data.get("entries", []) if _pays_supplier(e))))
    supplier_paid_bank = int(round(sum(float(e.get("withdraw_amount") or 0) for e in bank_data.get("entries", []) if _pays_supplier(e))))
    total_supplier_paid = supplier_paid_cash + supplier_paid_bank

    paid_registration_fee = 0
    _raw_pay_by_acc: dict[str, float] = {}
    for _e in (cash_data.get("entries", []) or []):
        _acc = str(_e.get("offset_account") or "").strip()
        if is_registration_fee_entry(_e):
            paid_registration_fee += int(round(float(_e.get("payment_amount") or 0)))
        elif _acc.startswith(("333", "334", "338")):
            _raw_pay_by_acc[_acc] = _raw_pay_by_acc.get(_acc, 0.0) + float(_e.get("payment_amount") or 0)
    for _e in (bank_data.get("entries", []) or []):
        _acc = str(_e.get("offset_account") or "").strip()
        if is_registration_fee_entry(_e):
            paid_registration_fee += int(round(float(_e.get("withdraw_amount") or 0)))
        elif _acc.startswith(("333", "334", "338")):
            _raw_pay_by_acc[_acc] = _raw_pay_by_acc.get(_acc, 0.0) + float(_e.get("withdraw_amount") or 0)
    pay_by_acc: dict[str, int] = {
        _acc: int(round(_amt)) for _acc, _amt in _raw_pay_by_acc.items() if int(round(_amt)) > 0
    }
    # Thuế được hoàn (thu tiền về, TK đối ứng 333x): Nợ 111 / 112, Có 333x
    _raw_refund_333: dict[str, float] = {}
    for _e in (cash_data.get("entries", []) or []):
        _acc = str(_e.get("offset_account") or "").strip()
        if _acc.startswith("333"):
            _raw_refund_333[_acc] = _raw_refund_333.get(_acc, 0.0) + float(_e.get("receipt_amount") or 0)
    for _e in (bank_data.get("entries", []) or []):
        _acc = str(_e.get("offset_account") or "").strip()
        if _acc.startswith("333"):
            _raw_refund_333[_acc] = _raw_refund_333.get(_acc, 0.0) + float(_e.get("deposit_amount") or 0)
    refund_333 = {_acc: int(round(_amt)) for _acc, _amt in _raw_refund_333.items() if int(round(_amt)) > 0}

    # Chứng từ nghiệp vụ khác (131, 331, 711, 811, 3339, 3388)
    from frontend.modules.transport.other_vouchers_service import voucher_totals_by_account, voucher_registration_fee_total
    ov = voucher_totals_by_account(company_id, date_from, date_to)
    # Nợ 3339 / Có 3388: người khác nộp hộ lệ phí trước bạ -> phần nguyên giá TSCĐ đã thanh toán (như chi lệ phí trước bạ ở sổ quỹ/ngân hàng)
    ov_registration_fee = voucher_registration_fee_total(company_id, date_from, date_to)
    paid_registration_fee += ov_registration_fee
    _ov_3339_dr = ov["3339"]["dr"] - ov_registration_fee
    if _ov_3339_dr > 0:
        pay_by_acc["3339"] = pay_by_acc.get("3339", 0) + _ov_3339_dr
    if ov["3339"]["cr"] > 0:
        refund_333["3339"] = refund_333.get("3339", 0) + ov["3339"]["cr"]

    rent_642 = int(round(
        sum(float(_e.get("payment_amount") or 0) for _e in (cash_data.get("entries", []) or []) if _e.get("source_type") == "rent_payment")
        + sum(float(_e.get("withdraw_amount") or 0) for _e in (bank_data.get("entries", []) or []) if _e.get("source_type") == "rent_payment")
    ))

    interest_515 = int(round(
        sum(float(_e.get("receipt_amount") or 0) for _e in (cash_data.get("entries", []) or []) if str(_e.get("offset_account") or "").strip().startswith("515"))
        + sum(float(_e.get("deposit_amount") or 0) for _e in (bank_data.get("entries", []) or []) if str(_e.get("offset_account") or "").strip().startswith("515"))
    ))

    # Chi phí trả trước theo kỳ: phân bổ các tháng nằm trong kỳ; số dư đầu kỳ hoặc tăng trong kỳ theo ngày đưa vào sử dụng.
    # Mục bắt đầu sau kỳ chưa vào sổ. Phần phân bổ có TK đối ứng 154 là chi phí dịch vụ vận tải, không phải 642.
    _m_from, _m_to = pe_tot.get("m_start", 1), pe_tot.get("m_end", 12)
    prepaid_242_alloc = 0
    prepaid_alloc_154 = 0
    prepaid_242_rem_end = 0
    prepaid_242_open = 0
    prepaid_242_increase = 0
    # Khoản đã mua trước ngày bắt đầu phân bổ (vd. bảo hiểm mua tháng 10, hiệu lực từ tháng 4 năm sau) vào TK 242 từ ngày hóa đơn.
    _invoice_dates: dict[str, date] = {}
    for _inv in db._execute_query(
            "SELECT invoice_number, invoice_date FROM public.transport_input_invoices WHERE buyer_company_id = %s",
            (company_id,), fetchall=True, commit=False) or []:
        if isinstance(_inv.get("invoice_date"), date):
            _invoice_dates.setdefault(normalize_invoice_number(_inv.get("invoice_number")), _inv["invoice_date"])
    for _it in pe_data.get("items", []):
        _sd = _it.get("start_date_raw")
        if isinstance(_sd, str):
            try:
                _sd = date.fromisoformat(_sd[:10])
            except Exception:
                _sd = None
        _bought = _invoice_dates.get(normalize_invoice_number(_it.get("voucher_no")))
        if isinstance(_sd, date) and isinstance(_bought, date) and _bought < _sd:
            _sd = _bought
        if isinstance(_sd, date) and _sd > date_to:
            continue
        _rem = int(_it.get("rem_start") or 0)
        _months = _it.get("months") or [0] * 12
        _in_period = sum(_months[_m_from - 1:_m_to])
        if isinstance(_sd, date) and _sd < date_from:
            _begin = max(0, _rem - sum(_months[:_m_from - 1]))
            prepaid_242_open += _begin
        else:
            _begin = _rem
            prepaid_242_increase += _rem
        prepaid_242_alloc += _in_period
        if str(_it.get("offset_account") or "").strip().startswith("154"):
            prepaid_alloc_154 += _in_period
        prepaid_242_rem_end += max(0, _begin - _in_period)
    prepaid_alloc_642 = prepaid_242_alloc - prepaid_alloc_154
    total_cost_154 += prepaid_alloc_154 + depr_alloc_154
    # Chi trực tiếp ở sổ quỹ / sổ ngân hàng với TK đối ứng 242 (trả trước) hoặc 211 (mua TSCĐ): Nợ 242 / 211, Có 111 / 112
    paid_242 = 0
    paid_211 = paid_registration_fee
    for _e in (cash_data.get("entries", []) or []):
        if _e.get("source_type") == "input_invoice":
            continue  # counted as Nợ 331 (the invoice carries the 242 / 211 side)
        _acc = str(_e.get("offset_account") or "").strip()
        if _acc.startswith("242"):
            paid_242 += int(round(float(_e.get("payment_amount") or 0)))
        elif _acc.startswith("211"):
            paid_211 += int(round(float(_e.get("payment_amount") or 0)))
    for _e in (bank_data.get("entries", []) or []):
        if _e.get("source_type") == "input_invoice":
            continue
        _acc = str(_e.get("offset_account") or "").strip()
        if _acc.startswith("242"):
            paid_242 += int(round(float(_e.get("withdraw_amount") or 0)))
        elif _acc.startswith("211"):
            paid_211 += int(round(float(_e.get("withdraw_amount") or 0)))
    # Hoá đơn mua CCDC (153/242) vào 242 qua TK 153 trước; chỉ phần tăng không có hoá đơn mới có thể là chi thẳng Nợ 242.
    # Chi Nợ 242 còn lại (vd. phí đường bộ trả trước, bắt đầu phân bổ sau kỳ) nằm ở 242, không làm giảm phần chuyển từ 153.
    direct_242 = min(max(0, prepaid_242_increase - total_cost_153), paid_242)
    extra_242 = paid_242 - direct_242   # đã chi trả trước nhưng chưa ghi vào Sổ CCDC (hoặc bắt đầu phân bổ sau kỳ này)
    tr_153 = min(prepaid_242_increase - direct_242, total_cost_153)
    rest_242 = prepaid_242_increase - direct_242 - tr_153
    other_admin_exp = payroll_admin + rent_642

    # Chi tiet chi phi truc tiep tu DB (TK nho cua 154) - loc chat che theo company_id cua xe
    vexp_rows = db._execute_query('''
        SELECT ve.expense_type, COALESCE(SUM(ve.amount), 0) as total_amt
        FROM public.vehicle_expenses ve
        JOIN public.plate_whitelist pw ON pw.id = ve.vehicle_id
        WHERE pw.company_id = %s AND ve.expense_date >= %s AND ve.expense_date <= %s
        GROUP BY ve.expense_type
    ''', (company_id, date_from, date_to), fetchall=True, commit=False) or []
    vexp_map = {str(r.get("expense_type") or "").lower(): int(round(float(r.get("total_amt") or 0))) for r in vexp_rows}

    db_fuel = vexp_map.get("fuel", 0)
    db_repair = vexp_map.get("repair", 0) + vexp_map.get("maintenance", 0) + vexp_map.get("parts", 0)
    vexp_map.get("toll", 0) + vexp_map.get("bot", 0) + vexp_map.get("other", 0)

    if total_cost_154 > 0:
        cost_fuel_1541 = db_fuel
        cost_driver_1542 = 0
        cost_repair_1543 = db_repair
        allocated = cost_fuel_1541 + cost_repair_1543
        if allocated <= total_cost_154:
            cost_other_1547 = total_cost_154 - allocated
        else:
            ratio = float(total_cost_154) / float(allocated)
            cost_fuel_1541 = int(round(cost_fuel_1541 * ratio))
            cost_repair_1543 = int(round(cost_repair_1543 * ratio))
            cost_other_1547 = total_cost_154 - (cost_fuel_1541 + cost_repair_1543)
    else:
        cost_fuel_1541 = 0
        cost_driver_1542 = 0
        cost_repair_1543 = 0
        cost_other_1547 = 0

    cost_driver_1542 = payroll_direct
    total_cost_154 += payroll_direct

    # 3b. Phân bổ Tài sản cố định 211 theo ngày phát sinh (đầu kỳ vs trong kỳ)
    orig_211_open = 0
    orig_211_ps = 0
    sub_211 = []
    _tscd_codes = {"Nhà cửa, vật kiến trúc": "2111", "Máy móc, thiết bị": "2112",
                   "Phương tiện vận tải, truyền dẫn": "2113", "Thiết bị, dụng cụ quản lý": "2114"}
    for r in depr_data.get("rows", []):
        price = int(r.get("original_price") or 0)
        start_d = r.get("depreciation_start_date") or r.get("increase_date")
        if isinstance(start_d, str):
            try:
                start_d = date.fromisoformat(start_d[:10])
            except Exception:
                start_d = date(selected_year, 1, 1)
        elif not isinstance(start_d, date):
            start_d = date(selected_year, 1, 1)

        if start_d < date_from:
            v_open = price
            v_ps = 0
            v_close = price
            orig_211_open += price
        elif start_d <= date_to:
            v_open = 0
            v_ps = price
            v_close = price
            orig_211_ps += price
        else:
            v_open = 0
            v_ps = 0
            v_close = 0

        sub_211.append({
            "code": _tscd_codes.get(r.get("asset_type") or "", "2118"),
            "name": f"{r.get('asset_type') or 'TSCĐ khác'}: {r.get('asset_name') or r.get('asset_code')}",
            "open_dr": v_open,
            "open_cr": 0,
            "ps_dr": v_ps,
            "ps_cr": 0,
            "close_dr": v_close,
            "close_cr": 0,
            "source_note": f"Nguyên giá {r.get('asset_code') or r.get('plate_number') or ''}".strip(),
            "source_url": "/admin/transport/depreciation",
        })

    # 4. Vốn đầu tư của chủ sở hữu (TK 411, 4111) từ sổ quỹ tiền mặt & ngân hàng
    capital_rows = db._execute_query('''
        SELECT 
            id, voucher_no, entry_date, voucher_date, voucher_type,
            amount, partner_name, description, offset_account
        FROM public.transport_cash_book
        WHERE company_id = %s 
          AND (offset_account ILIKE %s OR description ILIKE %s OR description ILIKE %s)
          AND entry_date >= %s AND entry_date <= %s
        ORDER BY entry_date ASC, id ASC
    ''', (company_id, '%411%', '%góp vốn%', '%VĐL%', date_from, date_to), fetchall=True, commit=False) or []

    prev_by_partner_rows = db._execute_query('''
        SELECT 
            partner_name,
            COALESCE(SUM(CASE WHEN voucher_type = 'receipt' THEN amount ELSE 0 END), 0) as prev_cr,
            COALESCE(SUM(CASE WHEN voucher_type = 'payment' THEN amount ELSE 0 END), 0) as prev_dr
        FROM public.transport_cash_book
        WHERE company_id = %s 
          AND (offset_account ILIKE %s OR description ILIKE %s OR description ILIKE %s)
          AND entry_date < %s
        GROUP BY partner_name
    ''', (company_id, '%411%', '%góp vốn%', '%VĐL%', date_from), fetchall=True, commit=False) or []

    prev_cap_cr = sum(int(round(float(r.get("prev_cr") or 0))) for r in prev_by_partner_rows)
    prev_cap_dr = sum(int(round(float(r.get("prev_dr") or 0))) for r in prev_by_partner_rows)

    initial_capital = cash_open + bank_open + orig_211_open + prepaid_242_open - depr_open_214
    open_cr_411 = initial_capital + prev_cap_cr - prev_cap_dr
    cap_ps_cr = int(round(sum(float(r["amount"] or 0) for r in capital_rows if r["voucher_type"] == "receipt")))
    cap_ps_dr = int(round(sum(float(r["amount"] or 0) for r in capital_rows if r["voucher_type"] == "payment")))

    def _norm_str(s: str) -> str:
        import unicodedata
        return unicodedata.normalize("NFC", str(s or "").strip().lower())

    partner_cap = {}
    for r in prev_by_partner_rows:
        pname = (r.get("partner_name") or "").strip() or director_name
        match_key = next((k for k in partner_cap if _norm_str(k) == _norm_str(pname)), None)
        net_prev = int(round(float(r.get("prev_cr") or 0))) - int(round(float(r.get("prev_dr") or 0)))
        if not match_key:
            match_key = pname
            partner_cap[match_key] = {
                "name": f"Vốn góp của {pname}",
                "open_cr": net_prev,
                "ps_dr": 0,
                "ps_cr": 0,
                "close_cr": net_prev,
            }
        else:
            partner_cap[match_key]["open_cr"] += net_prev
            partner_cap[match_key]["close_cr"] += net_prev

    for r in capital_rows:
        pname = (r.get("partner_name") or "").strip() or director_name
        match_key = next((k for k in partner_cap if _norm_str(k) == _norm_str(pname)), None)
        if not match_key:
            match_key = pname
            partner_cap[match_key] = {
                "name": f"Vốn góp của {pname}",
                "open_cr": 0,
                "ps_dr": 0,
                "ps_cr": 0,
                "close_cr": 0,
            }
        amt = int(round(float(r["amount"] or 0)))
        if r["voucher_type"] == "receipt":
            partner_cap[match_key]["ps_cr"] += amt
            partner_cap[match_key]["close_cr"] += amt
        else:
            partner_cap[match_key]["ps_dr"] += amt
            partner_cap[match_key]["close_cr"] -= amt

    sub_411 = []
    if initial_capital != 0:
        sub_411.append({
            "code": "4111",
            "name": "Số dư vốn đầu kỳ chưa phân bổ",
            "open_dr": 0,
            "open_cr": initial_capital,
            "ps_dr": 0,
            "ps_cr": 0,
            "close_dr": 0,
            "close_cr": initial_capital,
            "source_note": "Số dư vốn đầu kỳ chưa phân bổ",
            "source_url": "/admin/transport/cash-book",
        })

    for pname, pdata in partner_cap.items():
        sub_411.append({
            "code": "4111",
            "name": pdata["name"],
            "open_dr": 0,
            "open_cr": pdata["open_cr"],
            "ps_dr": pdata["ps_dr"],
            "ps_cr": pdata["ps_cr"],
            "close_dr": 0,
            "close_cr": pdata["close_cr"],
            "source_note": "Phiếu thu góp vốn điều lệ",
            "source_url": "/admin/transport/cash-book",
        })

    total_cap_open_cr = open_cr_411
    total_cap_ps_cr = cap_ps_cr
    total_cap_ps_dr = cap_ps_dr
    total_cap_close_cr = total_cap_open_cr + total_cap_ps_cr - total_cap_ps_dr

    sub_331 = [
        {
            "code": "3311",
            "name": f"Phải trả người bán ngắn hạn ({count_inv_in} HĐ mua vào)",
            "open_dr": 0, "open_cr": 0,
            "ps_dr": total_supplier_paid, "ps_cr": total_payable_331,
            "close_dr": max(0, total_supplier_paid - total_payable_331),
            "close_cr": max(0, total_payable_331 - total_supplier_paid),
            "source_note": "Sổ chi tiết công nợ nhà cung cấp",
            "source_url": "/admin/transport/input-invoices",
        }
    ]
    # TSCĐ tăng trong kỳ đã có hoá đơn đầu vào (Có 331 theo hoá đơn) thì không ghi Có 331 lần nữa
    tsc_plug = orig_211_ps - total_cost_211_inv - paid_211
    if tsc_plug < 0:
        sub_211.append({
            "code": "211",
            "name": "TSCĐ có hoá đơn mua nhưng chưa ghi vào Sổ tài sản cố định",
            "open_dr": 0, "open_cr": 0,
            "ps_dr": -tsc_plug, "ps_cr": 0,
            "close_dr": -tsc_plug, "close_cr": 0,
            "source_note": "Hoá đơn đầu vào ghi Nợ TK 211",
            "source_url": "/admin/transport/input-invoices",
        })
        orig_211_ps += -tsc_plug
        tsc_plug = 0
    if tsc_plug > 0:
        n_211_ps = sum(1 for s in sub_211 if s.get("ps_dr", 0) > 0)
        sub_331.append({
            "code": "3312",
            "name": f"Phải trả mua TSCĐ ({n_211_ps} tài sản tăng trong kỳ)",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": 0,
            "ps_cr": tsc_plug,
            "close_dr": 0,
            "close_cr": tsc_plug,
            "source_note": "Bảng tính khấu hao TSCĐ - tài sản tăng trong kỳ chưa có hoá đơn đầu vào",
            "source_url": "/admin/transport/depreciation",
        })
    if rest_242 > 0:
        sub_331.append({
            "code": "3313",
            "name": "Phải trả mua CCDC đưa vào sử dụng (chưa qua TK 153)",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": 0,
            "ps_cr": rest_242,
            "close_dr": 0,
            "close_cr": rest_242,
            "source_note": "Sổ công cụ dụng cụ - CCDC đưa vào sử dụng chưa qua kho 153",
            "source_url": "/admin/transport/prepaid-expenses",
        })
    total_payable_331_all = total_payable_331 + tsc_plug + (rest_242 if rest_242 > 0 else 0)
    net_331 = total_supplier_paid - total_payable_331_all

    # Base Parent Accounts (TK Cap 1: 3 chu so)
    parent_accounts = [
        {
            "code": "111",
            "name": "Tiền mặt",
            "open_dr": cash_open,
            "open_cr": 0,
            "ps_dr": cash_ps_dr,
            "ps_cr": cash_ps_cr,
            "close_dr": cash_close,
            "close_cr": 0,
            "source_note": "Sổ quỹ tiền mặt (Mẫu S04a-DNN)",
            "source_url": "/admin/transport/cash-book",
            "sub_accounts": [
                {
                    "code": "1111",
                    "name": "Tiền Việt Nam (Quỹ tiền mặt)",
                    "open_dr": cash_open, "open_cr": 0,
                    "ps_dr": cash_ps_dr, "ps_cr": cash_ps_cr,
                    "close_dr": cash_close, "close_cr": 0,
                    "source_note": "Phiếu thu - chi quỹ tiền mặt",
                    "source_url": "/admin/transport/cash-book",
                }
            ]
        },
        {
            "code": "112",
            "name": "Tiền gửi Ngân hàng",
            "open_dr": bank_open,
            "open_cr": 0,
            "ps_dr": bank_ps_dr,
            "ps_cr": bank_ps_cr,
            "close_dr": bank_close,
            "close_cr": 0,
            "source_note": "Sổ tiền gửi Ngân hàng VietinBank (Mẫu S05a-DNN)",
            "source_url": "/admin/transport/bank-book",
            "sub_accounts": [
                {
                    "code": "1121",
                    "name": "Ngân hàng TMCP Công Thương VN (VietinBank 38633888)",
                    "open_dr": bank_open, "open_cr": 0,
                    "ps_dr": bank_ps_dr, "ps_cr": bank_ps_cr,
                    "close_dr": bank_close, "close_cr": 0,
                    "source_note": "Sao kê tài khoản VietinBank",
                    "source_url": "/admin/transport/bank-book",
                }
            ]
        },
        {
            "code": "131",
            "name": "Phải thu của khách hàng",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": total_receivable_131,
            "ps_cr": total_cust_paid,
            "close_dr": max(0, total_receivable_131 - total_cust_paid),
            "close_cr": max(0, total_cust_paid - total_receivable_131),
            "source_note": f"Bảng kê {count_inv_out} HĐ BKAV bán ra",
            "source_url": "/admin/vehicles/cargo",
            "sub_accounts": [
                {
                    "code": "1311",
                    "name": f"Phải thu cước vận tải khách hàng ({count_inv_out} HĐ BKAV)",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": total_receivable_131, "ps_cr": total_cust_paid,
                    "close_dr": max(0, total_receivable_131 - total_cust_paid), "close_cr": max(0, total_cust_paid - total_receivable_131),
                    "source_note": "Hóa đơn điện tử BKAV bán ra",
                    "source_url": "/admin/vehicles/cargo",
                }
            ]
        },
        {
            "code": "133",
            "name": "Thuế GTGT được khấu trừ",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": total_vat_in_133,
            "ps_cr": total_vat_in_133,
            "close_dr": 0,
            "close_cr": 0,
            "source_note": f"Bảng kê {count_inv_in} HĐ đầu vào",
            "source_url": "/admin/transport/input-invoices",
            "sub_accounts": [
                {
                    "code": "1331",
                    "name": f"Thuế GTGT được khấu trừ của HHDV ({count_inv_in} HĐ mua vào)",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": total_vat_in_133, "ps_cr": total_vat_in_133,
                    "close_dr": 0, "close_cr": 0,
                    "source_note": "Tập hợp thuế GTGT mua vào",
                    "source_url": "/admin/transport/input-invoices",
                }
            ]
        },
        {
            "code": "154",
            "name": "Chi phí SXKD dịch vụ dở dang",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": total_cost_154,
            "ps_cr": total_cost_154,
            "close_dr": 0,
            "close_cr": 0,
            "source_note": "Tập hợp chi phí xe tải",
            "source_url": "/admin/transport/input-invoices",
            "sub_accounts": [
                {
                    "code": "1541",
                    "name": "Chi phí nhiên liệu, dầu mỡ xe tải",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": cost_fuel_1541, "ps_cr": cost_fuel_1541,
                    "close_dr": 0, "close_cr": 0,
                    "source_note": "Hóa đơn xăng dầu Petrolimex",
                    "source_url": "/admin/transport/input-invoices",
                },
                {
                    "code": "1542",
                    "name": "Chi phí nhân công trực tiếp (lái xe)",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": cost_driver_1542, "ps_cr": cost_driver_1542,
                    "close_dr": 0, "close_cr": 0,
                    "source_note": "Bảng lương nhân sự – lái xe",
                    "source_url": "/admin/transport/payroll",
                },
                {
                    "code": "1543",
                    "name": "Chi phí sửa chữa, bảo dưỡng thay phụ tùng",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": cost_repair_1543, "ps_cr": cost_repair_1543,
                    "close_dr": 0, "close_cr": 0,
                    "source_note": "Hóa đơn gara & phụ tùng xe",
                    "source_url": "/admin/transport/input-invoices",
                },
                {
                    "code": "1547",
                    "name": "Chi phí bến bãi, cầu đường BOT, đăng kiểm",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": cost_other_1547, "ps_cr": cost_other_1547,
                    "close_dr": 0, "close_cr": 0,
                    "source_note": "Vé ePass, VETC, đăng kiểm",
                    "source_url": "/admin/transport/input-invoices",
                },
            ]
        },
        {
            "code": "153",
            "name": "Công cụ, dụng cụ",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": total_cost_153,
            "ps_cr": tr_153,
            "close_dr": total_cost_153 - tr_153,
            "close_cr": 0,
            "source_note": f"Hóa đơn mua CCDC ({count_inv_153} HĐ)",
            "source_url": "/admin/transport/input-invoices",
            "sub_accounts": [
                {
                    "code": "1531",
                    "name": "Công cụ, dụng cụ (Quả chuẩn 500kg, thiết bị)",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": total_cost_153, "ps_cr": tr_153,
                    "close_dr": total_cost_153 - tr_153, "close_cr": 0,
                    "source_note": "Hóa đơn CCDC nhập kho",
                    "source_url": "/admin/transport/input-invoices",
                }
            ]
        },
        {
            "code": "211",
            "name": "Tài sản cố định hữu hình",
            "open_dr": orig_211_open,
            "open_cr": 0,
            "ps_dr": orig_211_ps,
            "ps_cr": 0,
            "close_dr": orig_211_open + orig_211_ps,
            "close_cr": 0,
            "source_note": "Bảng tính khấu hao TSCĐ",
            "source_url": "/admin/transport/depreciation",
            "sub_accounts": sub_211,
        },
        {
            "code": "214",
            "name": "Hao mòn tài sản cố định",
            "open_dr": 0,
            "open_cr": depr_open_214,
            "ps_dr": 0,
            "ps_cr": total_accum_depr_214,
            "close_dr": 0,
            "close_cr": depr_open_214 + total_accum_depr_214,
            "source_note": "Bảng tính khấu hao TSCĐ",
            "source_url": "/admin/transport/depreciation",
            "sub_accounts": [
                {
                    "code": "2141",
                    "name": f"Hao mòn TSCĐ hữu hình: {r.get('asset_name') or r.get('asset_code')}",
                    "open_dr": 0,
                    "open_cr": r["tb_depr_open"],
                    "ps_dr": 0,
                    "ps_cr": r["tb_depr_period"],
                    "close_dr": 0,
                    "close_cr": r["tb_depr_open"] + r["tb_depr_period"],
                    "source_note": f"Khấu hao lũy kế {r.get('asset_code') or ''}".strip(),
                    "source_url": "/admin/transport/depreciation",
                }
                for r in depr_data.get("rows", [])
            ]
        },
        {
            "code": "242",
            "name": "Chi phí trả trước",
            "open_dr": prepaid_242_open,
            "open_cr": 0,
            "ps_dr": prepaid_242_increase + extra_242,
            "ps_cr": prepaid_242_alloc,
            "close_dr": prepaid_242_rem_end + extra_242,
            "close_cr": 0,
            "source_note": "Bảng phân bổ CCDC (quản lý quả cân, máy tính...)",
            "source_url": "/admin/transport/prepaid-expenses",
            "sub_accounts": [
                {
                    "code": "2421",
                    "name": "CCDC phân bổ (quả cân, máy tính, máy in, điều hòa, tủ lạnh...)",
                    "open_dr": prepaid_242_open, "open_cr": 0,
                    "ps_dr": prepaid_242_increase, "ps_cr": prepaid_242_alloc,
                    "close_dr": prepaid_242_rem_end, "close_cr": 0,
                    "source_note": "Sổ công cụ dụng cụ",
                    "source_url": "/admin/transport/prepaid-expenses",
                }
            ] + ([{
                    "code": "2421",
                    "name": "Đã chi trả trước, chưa đưa vào phân bổ trong kỳ",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": extra_242, "ps_cr": 0,
                    "close_dr": extra_242, "close_cr": 0,
                    "source_note": "Chứng từ chi ghi TK đối ứng 242 ở sổ quỹ / sổ ngân hàng",
                    "source_url": "/admin/transport/bank-book",
                }] if extra_242 > 0 else [])
        },
        {
            "code": "331",
            "name": "Phải trả cho người bán",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": total_supplier_paid,
            "ps_cr": total_payable_331_all,
            "close_dr": max(0, net_331),
            "close_cr": max(0, -net_331),
            "source_note": f"Công nợ {count_inv_in} HĐ mua vào",
            "source_url": "/admin/transport/input-invoices",
            "sub_accounts": sub_331,
        },
        {
            "code": "333",
            "name": "Thuế và các khoản phải nộp Nhà nước",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": total_vat_in_133,
            "ps_cr": total_vat_out_3331,
            "close_dr": 0,
            "close_cr": total_vat_out_3331 - total_vat_in_133,
            "source_note": "Báo cáo thuế GTGT theo quý",
            "source_url": "/admin/transport/tax-report",
            "sub_accounts": [
                {
                    "code": "33311",
                    "name": f"Thuế GTGT đầu ra ({total_vat_out_3331:,.0f} đ bù trừ {total_vat_in_133:,.0f} đ)",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": total_vat_in_133, "ps_cr": total_vat_out_3331,
                    "close_dr": 0, "close_cr": total_vat_out_3331 - total_vat_in_133,
                    "source_note": "Tờ khai thuế GTGT Mẫu 01/GTGT",
                    "source_url": "/admin/transport/tax-report",
                }
            ]
        },
        {
            "code": "411",
            "name": "Vốn đầu tư của chủ sở hữu",
            "open_dr": 0,
            "open_cr": total_cap_open_cr,
            "ps_dr": total_cap_ps_dr,
            "ps_cr": total_cap_ps_cr,
            "close_dr": 0,
            "close_cr": total_cap_close_cr,
            "source_note": f"Vốn điều lệ công ty ({len(sub_411)} khoản góp vốn)",
            "source_url": "/admin/transport/cash-book",
            "sub_accounts": sub_411,
        },
        {
            "code": "421",
            "name": "Lợi nhuận sau thuế chưa phân phối",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": (abs(total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) < 0 else 0),
            "ps_cr": ((total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) >= 0 else 0),
            "close_dr": (abs(total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) < 0 else 0),
            "close_cr": ((total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) >= 0 else 0),
            "source_note": "Kết chuyển kết quả kinh doanh",
            "source_url": None,
            "sub_accounts": [
                {
                    "code": "4212",
                    "name": "Lợi nhuận sau thuế chưa phân phối năm nay",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": (abs(total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) < 0 else 0),
                    "ps_cr": ((total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) >= 0 else 0),
                    "close_dr": (abs(total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) < 0 else 0),
                    "close_cr": ((total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv)) >= 0 else 0),
                    "source_note": "Xác định KQKD cuối năm",
                    "source_url": None,
                }
            ]
        },
        {
            "code": "511",
            "name": "Doanh thu bán hàng và cung cấp DV",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": total_revenue_511,
            "ps_cr": total_revenue_511,
            "close_dr": 0,
            "close_cr": 0,
            "source_note": f"Doanh thu cước vận chuyển ({count_inv_out} HĐ) đã K/C sang 911",
            "source_url": "/admin/vehicles/cargo",
            "sub_accounts": [
                {
                    "code": "5113",
                    "name": "Doanh thu cung cấp dịch vụ vận tải hàng hóa",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": total_revenue_511, "ps_cr": total_revenue_511,
                    "close_dr": 0, "close_cr": 0,
                    "source_note": f"{count_inv_out} HĐ cước vận chuyển (K/C sang 911)",
                    "source_url": "/admin/vehicles/cargo",
                }
            ]
        },
        {
            "code": "632",
            "name": "Giá vốn hàng bán (chi phí vận tải)",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": total_cost_154,
            "ps_cr": total_cost_154,
            "close_dr": 0,
            "close_cr": 0,
            "source_note": "Chi phí trực tiếp xe tải (K/C sang 911)",
            "source_url": "/admin/transport/input-invoices",
            "sub_accounts": [
                {
                    "code": "632",
                    "name": "Giá vốn dịch vụ vận tải (Kết chuyển sang 911)",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": total_cost_154, "ps_cr": total_cost_154,
                    "close_dr": 0, "close_cr": 0,
                    "source_note": "Kết chuyển chi phí chuyến xe",
                    "source_url": "/admin/transport/input-invoices",
                }
            ]
        },
        {
            "code": "642",
            "name": "Chi phí quản lý kinh doanh",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv,
            "ps_cr": depr_alloc_642 + prepaid_alloc_642 + other_admin_exp + total_cost_642_inv,
            "close_dr": 0,
            "close_cr": 0,
            "source_note": "Khấu hao, quản lý, phân bổ (K/C sang 911)",
            "source_url": "/admin/transport/depreciation",
            "sub_accounts": [
                *[
                    {
                        "code": "6422",
                        "name": label,
                        "open_dr": 0, "open_cr": 0,
                        "ps_dr": amount, "ps_cr": amount,
                        "close_dr": 0, "close_cr": 0,
                        "source_note": "Từ Bảng tính khấu hao TSCĐ (K/C sang 911)",
                        "source_url": "/admin/transport/depreciation",
                    }
                    for label, amount, assets in (
                        (f"Chi phí khấu hao phương tiện vận tải ({len(_depr_vehicles)} xe tải)", depr_vehicle_642, _depr_vehicles),
                        (f"Chi phí khấu hao TSCĐ khác ({len(_depr_others)} tài sản: thiết bị, dụng cụ quản lý...)", depr_other_642, _depr_others),
                    ) if assets
                ],
                {
                    "code": "6422",
                    "name": "Chi phí phân bổ CCDC, chi phí trả trước",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": prepaid_alloc_642, "ps_cr": prepaid_alloc_642,
                    "close_dr": 0, "close_cr": 0,
                    "source_note": "Phân bổ từ TK 242 (K/C sang 911)",
                    "source_url": None,
                },
                *(
                    [{
                        "code": "6422",
                        "name": "Chi phí lương & bảo hiểm bộ phận quản lý",
                        "open_dr": 0, "open_cr": 0,
                        "ps_dr": payroll_admin, "ps_cr": payroll_admin,
                        "close_dr": 0, "close_cr": 0,
                        "source_note": "Bảng lương nhân sự",
                        "source_url": "/admin/transport/payroll",
                    }] if payroll_admin > 0 else []
                ),
                *(
                    [{
                        "code": "6422",
                        "name": "Chi phí thuê nhà làm văn phòng (nhà giám đốc)",
                        "open_dr": 0, "open_cr": 0,
                        "ps_dr": rent_642, "ps_cr": rent_642,
                        "close_dr": 0, "close_cr": 0,
                        "source_note": "Chứng từ chi thuê nhà từ sổ ngân hàng / sổ quỹ (K/C sang 911)",
                        "source_url": "/admin/transport/bank-book",
                    }] if rent_642 > 0 else []
                ),
                {
                    "code": "6428",
                    "name": "Chi phí quản lý điều hành & dịch vụ mua ngoài (CKS BKAV...)",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": total_cost_642_inv, "ps_cr": total_cost_642_inv,
                    "close_dr": 0, "close_cr": 0,
                    "source_note": "Hóa đơn quản lý điều hành (CKS BKAV)",
                    "source_url": "/admin/transport/input-invoices",
                },
            ]
        },
        {
            "code": "911",
            "name": "Xác định kết quả kinh doanh",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": total_revenue_511,
            "ps_cr": total_revenue_511,
            "close_dr": 0,
            "close_cr": 0,
            "source_note": "Kết chuyển doanh thu, giá vốn, CP QLDN và lợi nhuận",
            "source_url": None,
            "sub_accounts": [
                {
                    "code": "911",
                    "name": "Kết chuyển doanh thu thuần và chi phí",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": total_revenue_511, "ps_cr": total_revenue_511,
                    "close_dr": 0, "close_cr": 0,
                    "source_note": "Kết chuyển KQKD",
                    "source_url": None,
                }
            ]
        },
    ]

    pay_333 = {k: v for k, v in pay_by_acc.items() if k.startswith("333")} if pay_by_acc else {}
    pay_334 = {k: v for k, v in pay_by_acc.items() if k.startswith("334")} if pay_by_acc else {}
    pay_338 = {k: v for k, v in pay_by_acc.items() if k.startswith("338")} if pay_by_acc else {}

    # a. TK 333
    tax_names = {
        "3334": "Thuế thu nhập doanh nghiệp",
        "3335": "Thuế thu nhập cá nhân",
        "3338": "Thuế môn bài và các loại thuế khác",
        "3339": "Phí, lệ phí và các khoản phải nộp khác",
    }
    acc_333 = next((p for p in parent_accounts if p.get("code") == "333"), None)
    if acc_333 is not None:
        if pay_333:
            total_pay_333 = sum(pay_333.values())
            acc_333["ps_dr"] += total_pay_333
            old_cr = acc_333["close_cr"] - acc_333["close_dr"]
            net = old_cr - total_pay_333
            acc_333["close_dr"] = max(0, -net)
            acc_333["close_cr"] = max(0, net)

            sub_33311 = next((s for s in acc_333.get("sub_accounts", []) if s.get("code") == "33311"), None)
            sub_3331_amt = sum(v for k, v in pay_333.items() if k in ("3331", "33311"))
            if sub_3331_amt > 0 and sub_33311 is not None:
                sub_33311["ps_dr"] += sub_3331_amt
                old_sub_cr = sub_33311["close_cr"] - sub_33311["close_dr"]
                net_sub = old_sub_cr - sub_3331_amt
                sub_33311["close_dr"] = max(0, -net_sub)
                sub_33311["close_cr"] = max(0, net_sub)

            for code_333, amt_333 in sorted(pay_333.items()):
                if code_333 in ("3331", "33311"):
                    continue
                t_name = tax_names.get(code_333, "Thuế và các khoản phải nộp khác")
                acc_333.setdefault("sub_accounts", []).append({
                    "code": code_333,
                    "name": t_name,
                    "open_dr": 0,
                    "open_cr": 0,
                    "ps_dr": amt_333,
                    "ps_cr": 0,
                    "close_dr": amt_333,
                    "close_cr": 0,
                    "source_note": "Chứng từ nộp thuế từ sổ ngân hàng / sổ quỹ",
                    "source_url": "/admin/transport/bank-book",
                })

        for code_333, amt_333 in sorted(refund_333.items()):
            sub_code = "33311" if code_333 in ("3331", "33311") else code_333
            sub = next((s for s in acc_333.get("sub_accounts", []) if s.get("code") == sub_code), None)
            if sub is None:
                sub = {
                    "code": sub_code,
                    "name": tax_names.get(sub_code, "Thuế và các khoản phải nộp khác"),
                    "open_dr": 0, "open_cr": 0, "ps_dr": 0, "ps_cr": 0, "close_dr": 0, "close_cr": 0,
                    "source_note": "Tiền thuế được hoàn từ sổ ngân hàng / sổ quỹ",
                    "source_url": "/admin/transport/bank-book",
                }
                acc_333.setdefault("sub_accounts", []).append(sub)
            sub["ps_cr"] += amt_333
            sub_net = (sub["open_cr"] - sub["open_dr"]) + (sub["ps_cr"] - sub["ps_dr"])
            sub["close_cr"] = max(0, sub_net)
            sub["close_dr"] = max(0, -sub_net)
            acc_333["ps_cr"] += amt_333
            net_333 = (acc_333["open_cr"] - acc_333["open_dr"]) + (acc_333["ps_cr"] - acc_333["ps_dr"])
            acc_333["close_cr"] = max(0, net_333)
            acc_333["close_dr"] = max(0, -net_333)

        # Lệ phí trước bạ tính vào nguyên giá: Có 3339 (Nợ 211) và Nợ 3339 (nộp tiền / người khác nộp hộ) cùng số, 3339 không còn dư
        if paid_registration_fee > 0:
            sub = next((s for s in acc_333.get("sub_accounts", []) if s.get("code") == "3339"), None)
            if sub is None:
                sub = {
                    "code": "3339",
                    "name": tax_names["3339"],
                    "open_dr": 0, "open_cr": 0, "ps_dr": 0, "ps_cr": 0, "close_dr": 0, "close_cr": 0,
                    "source_note": "Lệ phí trước bạ tính vào nguyên giá TSCĐ",
                    "source_url": "/admin/transport/depreciation",
                }
                acc_333.setdefault("sub_accounts", []).append(sub)
            sub["ps_dr"] += paid_registration_fee
            sub["ps_cr"] += paid_registration_fee
            sub_net = (sub["open_cr"] - sub["open_dr"]) + (sub["ps_cr"] - sub["ps_dr"])
            sub["close_cr"] = max(0, sub_net)
            sub["close_dr"] = max(0, -sub_net)
            acc_333["ps_dr"] += paid_registration_fee
            acc_333["ps_cr"] += paid_registration_fee

        pit = payroll.get("pit", 0)
        if pit > 0:
            sub_3335 = next((s for s in acc_333.get("sub_accounts", []) if s.get("code") == "3335"), None)
            if sub_3335 is not None:
                sub_3335["ps_cr"] += pit
                sub_net = (sub_3335["open_cr"] - sub_3335["open_dr"]) + (sub_3335["ps_cr"] - sub_3335["ps_dr"])
                sub_3335["close_cr"] = max(0, sub_net)
                sub_3335["close_dr"] = max(0, -sub_net)
                sub_3335["source_note"] = "Chứng từ nộp thuế & Bảng lương nhân sự"
            else:
                acc_333.setdefault("sub_accounts", []).append({
                    "code": "3335",
                    "name": "Thuế thu nhập cá nhân",
                    "open_dr": 0,
                    "open_cr": 0,
                    "ps_dr": 0,
                    "ps_cr": pit,
                    "close_dr": 0,
                    "close_cr": pit,
                    "source_note": "Bảng lương nhân sự (khấu trừ thuế TNCN)",
                    "source_url": "/admin/transport/payroll",
                })
            acc_333["ps_cr"] += pit
            net_333 = (acc_333["open_cr"] - acc_333["open_dr"]) + (acc_333["ps_cr"] - acc_333["ps_dr"])
            acc_333["close_cr"] = max(0, net_333)
            acc_333["close_dr"] = max(0, -net_333)

    # b. TK 334
    gross_total_334 = payroll.get("gross_total", 0)
    total_pay_334 = sum(pay_334.values()) if pay_334 else 0
    if gross_total_334 > 0 or total_pay_334 > 0:
        ps_cr_334 = gross_total_334
        ps_dr_334 = (
            payroll.get("employee_total", 0)
            + payroll.get("pit", 0)
            + payroll.get("union_fee", 0)
            + payroll.get("other_deduction", 0)
            + total_pay_334
        )
        net_334 = ps_cr_334 - ps_dr_334
        acc_334 = {
            "code": "334",
            "name": "Phải trả người lao động",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": ps_dr_334,
            "ps_cr": ps_cr_334,
            "close_dr": max(0, -net_334),
            "close_cr": max(0, net_334),
            "source_note": "Bảng lương nhân sự & chứng từ chi lương",
            "source_url": "/admin/transport/payroll",
            "sub_accounts": [
                {
                    "code": "3341",
                    "name": "Phải trả công nhân viên",
                    "open_dr": 0,
                    "open_cr": 0,
                    "ps_dr": ps_dr_334,
                    "ps_cr": ps_cr_334,
                    "close_dr": max(0, -net_334),
                    "close_cr": max(0, net_334),
                    "source_note": "Bảng lương nhân sự & chứng từ chi lương",
                    "source_url": "/admin/transport/payroll",
                }
            ],
        }
        idx_333 = next((i for i, p in enumerate(parent_accounts) if p.get("code") == "333"), -1)
        if idx_333 != -1:
            parent_accounts.insert(idx_333 + 1, acc_334)
        else:
            parent_accounts.append(acc_334)

    # c. TK 338
    ins_names = {
        "3382": "Kinh phí công đoàn",
        "3383": "Bảo hiểm xã hội",
        "3384": "Bảo hiểm y tế",
        "3385": "Bảo hiểm thất nghiệp",
        "3388": "Phải trả, phải nộp khác",
    }
    emp_by_ins = payroll.get("employer_by_ins") or {}
    ee_by_ins = payroll.get("employee_by_ins") or {}

    sub_338_data = {
        "3382": {
            "ps_cr": payroll.get("union_fee", 0),
            "ps_dr": pay_338.get("3382", 0),
        },
        "3383": {
            "ps_cr": emp_by_ins.get("3383", 0) + ee_by_ins.get("3383", 0),
            "ps_dr": pay_338.get("3383", 0),
        },
        "3384": {
            "ps_cr": emp_by_ins.get("3384", 0) + ee_by_ins.get("3384", 0),
            "ps_dr": pay_338.get("3384", 0),
        },
        "3385": {
            "ps_cr": emp_by_ins.get("3385", 0) + ee_by_ins.get("3385", 0),
            "ps_dr": pay_338.get("3385", 0),
        },
        "3388": {
            "ps_cr": payroll.get("other_deduction", 0) + ov["3388"]["cr"],
            "ps_dr": pay_338.get("3388", 0) + pay_338.get("338", 0) + sum(v for k, v in pay_338.items() if k not in ("338", "3382", "3383", "3384", "3385", "3388")) + ov["3388"]["dr"],
        },
    }

    sub_accounts_338 = []
    for code_338 in sorted(sub_338_data.keys()):
        d = sub_338_data[code_338]
        cr = d["ps_cr"]
        dr = d["ps_dr"]
        if cr > 0 or dr > 0:
            net = cr - dr
            sub_accounts_338.append({
                "code": code_338,
                "name": ins_names[code_338],
                "open_dr": 0,
                "open_cr": 0,
                "ps_dr": dr,
                "ps_cr": cr,
                "close_dr": max(0, -net),
                "close_cr": max(0, net),
                "source_note": "Trích bảo hiểm / kinh phí CĐ & chứng từ nộp",
                "source_url": "/admin/transport/payroll",
            })

    if sub_accounts_338:
        total_338_dr = sum(s["ps_dr"] for s in sub_accounts_338)
        total_338_cr = sum(s["ps_cr"] for s in sub_accounts_338)
        net_338 = total_338_cr - total_338_dr
        acc_338 = {
            "code": "338",
            "name": "Phải trả, phải nộp khác (BHXH, BHYT, BHTN, KPCĐ)",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": total_338_dr,
            "ps_cr": total_338_cr,
            "close_dr": max(0, -net_338),
            "close_cr": max(0, net_338),
            "source_note": "Bảng trích bảo hiểm & nộp bảo hiểm",
            "source_url": "/admin/transport/payroll",
            "sub_accounts": sub_accounts_338,
        }
        idx_334 = next((i for i, p in enumerate(parent_accounts) if p.get("code") == "334"), -1)
        if idx_334 != -1:
            parent_accounts.insert(idx_334 + 1, acc_338)
        else:
            idx_333 = next((i for i, p in enumerate(parent_accounts) if p.get("code") == "333"), -1)
            if idx_333 != -1:
                parent_accounts.insert(idx_333 + 1, acc_338)
            else:
                parent_accounts.append(acc_338)

    if interest_515 > 0:
        acc_515 = {
            "code": "515",
            "name": "Doanh thu hoạt động tài chính",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": interest_515,
            "ps_cr": interest_515,
            "close_dr": 0,
            "close_cr": 0,
            "source_note": "Lãi tiền gửi ngân hàng (K/C sang 911)",
            "source_url": "/admin/transport/bank-book",
            "sub_accounts": [
                {
                    "code": "515",
                    "name": "Lãi tiền gửi ngân hàng",
                    "open_dr": 0,
                    "open_cr": 0,
                    "ps_dr": interest_515,
                    "ps_cr": interest_515,
                    "close_dr": 0,
                    "close_cr": 0,
                    "source_note": "Chứng từ lãi tiền gửi từ sổ ngân hàng (K/C sang 911)",
                    "source_url": "/admin/transport/bank-book",
                }
            ],
        }
        idx_511 = next((i for i, p in enumerate(parent_accounts) if p.get("code") == "511"), -1)
        if idx_511 != -1:
            parent_accounts.insert(idx_511 + 1, acc_515)
        else:
            parent_accounts.append(acc_515)

        for p in parent_accounts:
            if p.get("code") == "421":
                net_ps = p["ps_cr"] - p["ps_dr"] + interest_515
                p["ps_cr"] = max(0, net_ps)
                p["ps_dr"] = max(0, -net_ps)
                net_close = p["close_cr"] - p["close_dr"] + interest_515
                p["close_cr"] = max(0, net_close)
                p["close_dr"] = max(0, -net_close)
                for sub in p.get("sub_accounts", []):
                    if sub.get("code") == "4212" or sub.get("code") == "421":
                        net_sub_ps = sub["ps_cr"] - sub["ps_dr"] + interest_515
                        sub["ps_cr"] = max(0, net_sub_ps)
                        sub["ps_dr"] = max(0, -net_sub_ps)
                        net_sub_close = sub["close_cr"] - sub["close_dr"] + interest_515
                        sub["close_cr"] = max(0, net_sub_close)
                        sub["close_dr"] = max(0, -net_sub_close)
            elif p.get("code") == "911":
                p["ps_dr"] += interest_515
                p["ps_cr"] += interest_515
                for sub in p.get("sub_accounts", []):
                    sub["ps_dr"] += interest_515
                    sub["ps_cr"] += interest_515

    # Bolt-on: Chứng từ nghiệp vụ khác (TK 131, 331, 711, 811)
    has_ov = any(ov[acc]["dr"] > 0 or ov[acc]["cr"] > 0 for acc in ov)
    if has_ov:
        # 1. Dòng cha 131 và 331
        for p in parent_accounts:
            code = p.get("code")
            if code == "331":
                ov_dr = ov["331"]["dr"]
                ov_cr = ov["331"]["cr"]
                if ov_dr > 0 or ov_cr > 0:
                    p["ps_dr"] += ov_dr
                    p["ps_cr"] += ov_cr
                    net = (p["close_cr"] - p["close_dr"]) + (ov_cr - ov_dr)
                    p["close_cr"] = max(0, net)
                    p["close_dr"] = max(0, -net)
                    sub_net = ov_cr - ov_dr
                    p.setdefault("sub_accounts", []).append({
                        "code": "3314",
                        "name": "Chứng từ nghiệp vụ khác",
                        "open_dr": 0,
                        "open_cr": 0,
                        "ps_dr": ov_dr,
                        "ps_cr": ov_cr,
                        "close_dr": max(0, -sub_net),
                        "close_cr": max(0, sub_net),
                        "source_note": "Chứng từ nghiệp vụ khác",
                        "source_url": "/admin/transport/other-vouchers",
                    })
            elif code == "131":
                ov_dr = ov["131"]["dr"]
                ov_cr = ov["131"]["cr"]
                if ov_dr > 0 or ov_cr > 0:
                    p["ps_dr"] += ov_dr
                    p["ps_cr"] += ov_cr
                    net = (p["close_dr"] - p["close_cr"]) + (ov_dr - ov_cr)
                    p["close_dr"] = max(0, net)
                    p["close_cr"] = max(0, -net)
                    sub_net = ov_dr - ov_cr
                    p.setdefault("sub_accounts", []).append({
                        "code": "1312",
                        "name": "Chứng từ nghiệp vụ khác",
                        "open_dr": 0,
                        "open_cr": 0,
                        "ps_dr": ov_dr,
                        "ps_cr": ov_cr,
                        "close_dr": max(0, sub_net),
                        "close_cr": max(0, -sub_net),
                        "source_note": "Chứng từ nghiệp vụ khác",
                        "source_url": "/admin/transport/other-vouchers",
                    })

        # 2. Dòng 711 và 811
        income = ov["711"]["cr"] - ov["711"]["dr"]
        expense = ov["811"]["dr"] - ov["811"]["cr"]

        idx_ref = next((i for i, p in enumerate(parent_accounts) if p.get("code") == "515"), -1)
        if idx_ref == -1:
            idx_ref = next((i for i, p in enumerate(parent_accounts) if p.get("code") == "511"), -1)

        if income != 0:
            abs_income = abs(income)
            acc_711 = {
                "code": "711",
                "name": "Thu nhập khác",
                "open_dr": 0,
                "open_cr": 0,
                "ps_dr": abs_income,
                "ps_cr": abs_income,
                "close_dr": 0,
                "close_cr": 0,
                "source_note": "Chứng từ nghiệp vụ khác (K/C sang 911)",
                "source_url": "/admin/transport/other-vouchers",
                "sub_accounts": [
                    {
                        "code": "711",
                        "name": "Thu nhập khác",
                        "open_dr": 0,
                        "open_cr": 0,
                        "ps_dr": abs_income,
                        "ps_cr": abs_income,
                        "close_dr": 0,
                        "close_cr": 0,
                        "source_note": "Chứng từ nghiệp vụ khác (K/C sang 911)",
                        "source_url": "/admin/transport/other-vouchers",
                    }
                ],
            }
            if idx_ref != -1:
                parent_accounts.insert(idx_ref + 1, acc_711)
                idx_ref += 1
            else:
                parent_accounts.append(acc_711)

        if expense != 0:
            abs_expense = abs(expense)
            acc_811 = {
                "code": "811",
                "name": "Chi phí khác",
                "open_dr": 0,
                "open_cr": 0,
                "ps_dr": abs_expense,
                "ps_cr": abs_expense,
                "close_dr": 0,
                "close_cr": 0,
                "source_note": "Chứng từ nghiệp vụ khác (K/C sang 911)",
                "source_url": "/admin/transport/other-vouchers",
                "sub_accounts": [
                    {
                        "code": "811",
                        "name": "Chi phí khác",
                        "open_dr": 0,
                        "open_cr": 0,
                        "ps_dr": abs_expense,
                        "ps_cr": abs_expense,
                        "close_dr": 0,
                        "close_cr": 0,
                        "source_note": "Chứng từ nghiệp vụ khác (K/C sang 911)",
                        "source_url": "/admin/transport/other-vouchers",
                    }
                ],
            }
            if idx_ref != -1:
                parent_accounts.insert(idx_ref + 1, acc_811)
                idx_ref += 1
            else:
                parent_accounts.append(acc_811)

        # 3. Dòng 421 và 911
        net_ov_profit = income - expense
        for p in parent_accounts:
            if p.get("code") == "421":
                net_ps = p["ps_cr"] - p["ps_dr"] + net_ov_profit
                p["ps_cr"] = max(0, net_ps)
                p["ps_dr"] = max(0, -net_ps)
                net_close = p["close_cr"] - p["close_dr"] + net_ov_profit
                p["close_cr"] = max(0, net_close)
                p["close_dr"] = max(0, -net_close)
                for sub in p.get("sub_accounts", []):
                    if sub.get("code") == "4212" or sub.get("code") == "421":
                        net_sub_ps = sub["ps_cr"] - sub["ps_dr"] + net_ov_profit
                        sub["ps_cr"] = max(0, net_sub_ps)
                        sub["ps_dr"] = max(0, -net_sub_ps)
                        net_sub_close = sub["close_cr"] - sub["close_dr"] + net_ov_profit
                        sub["close_cr"] = max(0, net_sub_close)
                        sub["close_dr"] = max(0, -net_sub_close)
            elif p.get("code") == "911":
                p["ps_dr"] += income
                p["ps_cr"] += income
                for sub in p.get("sub_accounts", []):
                    sub["ps_dr"] += income
                    sub["ps_cr"] += income

    # Áp dụng Tùy chỉnh (Sửa tài khoản / Thêm tài khoản theo yêu cầu người dùng)
    from backend.services.settings_service import get_app_setting
    try:
        ovs = json.loads(get_app_setting(f"trial_balance_overrides_{company_id}", "{}")) or {}
    except Exception:
        ovs = {}
    custom_accs = get_tb_custom_accounts()

    # 1. Áp dụng Overrides cho các tài khoản hiện có
    if ovs:
        import re
        import unicodedata

        def _clean_match_name(val: str) -> str:
            v = unicodedata.normalize("NFC", str(val or "")).lower()
            v = v.replace("(chủ sở hữu)", "")
            return re.sub(r"\s+", " ", v).strip()

        for p in parent_accounts:
            p_code = p["code"]
            if p_code in ovs:
                if "name" in ovs[p_code]:
                    p["name"] = ovs[p_code]["name"]
                if "open_dr" in ovs[p_code]:
                    p["open_dr"] = ovs[p_code]["open_dr"]
                if "open_cr" in ovs[p_code]:
                    p["open_cr"] = ovs[p_code]["open_cr"]
                net = (p["open_dr"] - p["open_cr"]) + (p["ps_dr"] - p["ps_cr"])
                p["close_dr"] = max(0, net)
                p["close_cr"] = max(0, -net)

            subs = p.get("sub_accounts", [])
            sub_overridden = False
            codes_in_subs = []
            for s in subs:
                sc = s.get("code")
                if sc and sc in ovs and sc not in codes_in_subs:
                    codes_in_subs.append(sc)

            for s_code in codes_in_subs:
                matching = [s for s in subs if s.get("code") == s_code]
                ov_data = ovs[s_code]
                ov_name_clean = _clean_match_name(ov_data.get("name") or "")

                target_s = None
                if ov_name_clean:
                    for s in matching:
                        if _clean_match_name(s.get("name") or "") == ov_name_clean:
                            target_s = s
                            break
                if target_s is None and matching:
                    target_s = matching[0]

                if target_s is not None:
                    if "name" in ov_data:
                        target_s["name"] = ov_data["name"]
                    if "open_dr" in ov_data:
                        target_s["open_dr"] = ov_data["open_dr"]
                    if "open_cr" in ov_data:
                        target_s["open_cr"] = ov_data["open_cr"]
                    net_s = (target_s["open_dr"] - target_s["open_cr"]) + (target_s["ps_dr"] - target_s["ps_cr"])
                    target_s["close_dr"] = max(0, net_s)
                    target_s["close_cr"] = max(0, -net_s)
                    sub_overridden = True

            if sub_overridden and p_code not in ovs and subs:
                p["open_dr"] = sum(int(s.get("open_dr") or 0) for s in subs)
                p["open_cr"] = sum(int(s.get("open_cr") or 0) for s in subs)
                net_p = (p["open_dr"] - p["open_cr"]) + (p["ps_dr"] - p["ps_cr"])
                p["close_dr"] = max(0, net_p)
                p["close_cr"] = max(0, -net_p)

    # 2. Thêm các tài khoản mới người dùng tạo (Thêm tài khoản)
    if custom_accs:
        parent_map = {p["code"]: p for p in parent_accounts}
        for ca in custom_accs:
            code = ca.get("code")
            name = ca.get("name")
            p_code = ca.get("parent_code") or ""
            o_dr = int(ca.get("open_dr") or 0)
            o_cr = int(ca.get("open_cr") or 0)
            p_dr = int(ca.get("ps_dr") or 0)
            p_cr = int(ca.get("ps_cr") or 0)
            net_c = (o_dr - o_cr) + (p_dr - p_cr)
            c_dr = max(0, net_c)
            c_cr = max(0, -net_c)
            acc_entry = {
                "code": code,
                "name": name,
                "open_dr": o_dr,
                "open_cr": o_cr,
                "ps_dr": p_dr,
                "ps_cr": p_cr,
                "close_dr": c_dr,
                "close_cr": c_cr,
                "source_note": "Tài khoản thêm thủ công",
                "source_url": None,
                "is_custom": True,
            }
            if p_code and p_code in parent_map:
                parent_map[p_code].setdefault("sub_accounts", []).append(acc_entry)
                parent_map[p_code]["open_dr"] += o_dr
                parent_map[p_code]["open_cr"] += o_cr
                parent_map[p_code]["ps_dr"] += p_dr
                parent_map[p_code]["ps_cr"] += p_cr
                net_p = (parent_map[p_code]["open_dr"] - parent_map[p_code]["open_cr"]) + (parent_map[p_code]["ps_dr"] - parent_map[p_code]["ps_cr"])
                parent_map[p_code]["close_dr"] = max(0, net_p)
                parent_map[p_code]["close_cr"] = max(0, -net_p)
            else:
                acc_entry["sub_accounts"] = []
                parent_accounts.append(acc_entry)
                parent_map[code] = acc_entry

    # Calculate Totals strictly on Parent Accounts (Level 1) to prevent double counting
    totals = {
        "open_dr": sum(r["open_dr"] for r in parent_accounts),
        "open_cr": sum(r["open_cr"] for r in parent_accounts),
        "ps_dr": sum(r["ps_dr"] for r in parent_accounts),
        "ps_cr": sum(r["ps_cr"] for r in parent_accounts),
        "close_dr": sum(r["close_dr"] for r in parent_accounts),
        "close_cr": sum(r["close_cr"] for r in parent_accounts),
    }

    # Flattened rows for unified table rendering with hierarchy
    flattened_rows = []
    for p in parent_accounts:
        p_row = {
            "code": p["code"],
            "name": p["name"],
            "source_note": p["source_note"],
            "source_url": p.get("source_url"),
            "open_dr": p["open_dr"],
            "open_cr": p["open_cr"],
            "ps_dr": p["ps_dr"],
            "ps_cr": p["ps_cr"],
            "close_dr": p["close_dr"],
            "close_cr": p["close_cr"],
            "is_parent": True,
            "level": 1,
            "sub_count": len(p.get("sub_accounts", [])),
            "is_custom": p.get("is_custom", False),
        }
        flattened_rows.append(p_row)
        for s in p.get("sub_accounts", []):
            s_row = {
                "code": s["code"],
                "name": s["name"],
                "source_note": s["source_note"],
                "source_url": s.get("source_url") or p.get("source_url"),
                "open_dr": s["open_dr"],
                "open_cr": s["open_cr"],
                "ps_dr": s["ps_dr"],
                "ps_cr": s["ps_cr"],
                "close_dr": s["close_dr"],
                "close_cr": s["close_cr"],
                "is_parent": False,
                "parent_code": p["code"],
                "level": 2,
                "is_custom": s.get("is_custom", False),
                # Các tài khoản này chỉ trình bày ở cấp 1 trên bảng cân đối và file Excel
                "hide_in_report": p["code"] in ("131", "331", "334", "411", "515", "632", "711", "811", "911"),
            }
            flattened_rows.append(s_row)

    return {
        "company_id": company_id,
        "company_name": company_name,
        "address": company.get("address") or "",
        "tax_code": company.get("tax_code") or "",
        "director_name": director_name,
        "year": year,
        "date_from": date_from,
        "date_to": date_to,
        "as_of_date": date_to,
        "parent_rows": parent_accounts,
        "all_rows": flattened_rows,
        "totals": totals,
        "is_balanced": (
            totals["open_dr"] == totals["open_cr"]
            and totals["ps_dr"] == totals["ps_cr"]
            and totals["close_dr"] == totals["close_cr"]
        ),
    }




def build_closing_entries(tb_data: dict) -> dict:
    """Xây dựng các bút toán kết chuyển cuối kỳ từ số liệu bảng cân đối tài khoản."""
    all_rows = tb_data.get("all_rows") or []

    # 1. Tìm các tài khoản lá (leaf rows):
    # Dòng con (is_parent == False); nếu tài khoản cha không có dòng con nào thì dùng chính dòng cha.
    parent_has_child = set()
    current_parent = None
    for r in all_rows:
        if r.get("is_parent", False):
            current_parent = r.get("code")
        else:
            if r.get("parent_code"):
                parent_has_child.add(r.get("parent_code"))
            if current_parent:
                parent_has_child.add(current_parent)

    leaf_rows = []
    for r in all_rows:
        if not r.get("is_parent", False):
            leaf_rows.append(r)
        elif r.get("is_parent", False) and r.get("code") not in parent_has_child:
            leaf_rows.append(r)

    # 2. Gộp theo mã TK con: các dòng con trùng mã thì cộng tiền lại.
    grouped_leaf_map: dict[str, dict] = {}
    for r in leaf_rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        if code not in grouped_leaf_map:
            grouped_leaf_map[code] = {
                "code": code,
                "name": str(r.get("name") or "").strip(),
                "ps_dr": int(round(float(r.get("ps_dr") or 0))),
                "ps_cr": int(round(float(r.get("ps_cr") or 0))),
            }
        else:
            grouped_leaf_map[code]["ps_dr"] += int(round(float(r.get("ps_dr") or 0)))
            grouped_leaf_map[code]["ps_cr"] += int(round(float(r.get("ps_cr") or 0)))

    entries: list[dict] = []

    # Bước 1. Khấu trừ thuế GTGT: mỗi mã con bắt đầu "133" có ps_cr > 0
    # -> {"desc": "Khấu trừ thuế GTGT đầu vào", "debit": "33311", "credit": <mã 133x>, "amount": ps_cr}
    for code, row in grouped_leaf_map.items():
        if code.startswith("133") and row["ps_cr"] > 0:
            entries.append({
                "group": "Thuế GTGT",
                "desc": "Khấu trừ thuế GTGT đầu vào",
                "debit": "33311",
                "credit": code,
                "amount": row["ps_cr"],
            })

    # Bước 2. Chi phí SXKD dở dang sang giá vốn: mỗi mã con bắt đầu "154" có ps_cr > 0
    # -> desc "Kết chuyển " + <tên dòng con, chữ đầu viết thường> + " sang giá vốn", Nợ "632", Có <mã>
    for code, row in grouped_leaf_map.items():
        if code.startswith("154") and row["ps_cr"] > 0:
            nm = row["name"]
            c = (nm[:1].lower() + nm[1:]) if nm else "chi phí"
            entries.append({
                "group": "Giá vốn",
                "desc": f"Kết chuyển {c} sang giá vốn",
                "debit": "632",
                "credit": code,
                "amount": row["ps_cr"],
            })

    # Bước 3. Doanh thu, thu nhập -> 911: mã bắt đầu "511", "515", "711"; số tiền = ps_dr; Nợ <mã>, Có "911"
    step3_amount = 0
    for code, row in grouped_leaf_map.items():
        if row["ps_dr"] > 0:
            desc = None
            if code.startswith("511"):
                desc = "Kết chuyển doanh thu bán hàng và cung cấp dịch vụ"
            elif code.startswith("515"):
                desc = "Kết chuyển doanh thu hoạt động tài chính"
            elif code.startswith("711"):
                desc = "Kết chuyển thu nhập khác"
            if desc:
                entries.append({
                    "group": "Doanh thu",
                    "desc": desc,
                    "debit": code,
                    "credit": "911",
                    "amount": row["ps_dr"],
                })
                step3_amount += row["ps_dr"]
    revenue = step3_amount

    # Bước 4. Chi phí -> 911: "632", "635", "6421", "6422", mã 642 khác, "811", "821"; số tiền = ps_cr; Nợ "911", Có <mã>
    step4_amount = 0
    for code, row in grouped_leaf_map.items():
        if row["ps_cr"] > 0:
            desc = None
            if code == "632" or code.startswith("632"):
                desc = "Kết chuyển giá vốn hàng bán"
            elif code == "635" or code.startswith("635"):
                desc = "Kết chuyển chi phí tài chính"
            elif code == "6421":
                desc = "Kết chuyển chi phí bán hàng"
            elif code == "6422":
                desc = "Kết chuyển chi phí quản lý doanh nghiệp"
            elif code.startswith("642"):
                desc = "Kết chuyển chi phí quản lý kinh doanh khác"
            elif code == "811" or code.startswith("811"):
                desc = "Kết chuyển chi phí khác"
            elif code == "821" or code.startswith("821"):
                desc = "Kết chuyển chi phí thuế TNDN"
            if desc:
                entries.append({
                    "group": "Chi phí",
                    "desc": desc,
                    "debit": "911",
                    "credit": code,
                    "amount": row["ps_cr"],
                })
                step4_amount += row["ps_cr"]
    expense = step4_amount

    # Bước 5. Lãi / lỗ
    profit = revenue - expense
    if profit > 0:
        entries.append({
            "group": "Lãi lỗ",
            "desc": "Kết chuyển lãi",
            "debit": "911",
            "credit": "4212",
            "amount": profit,
        })
    elif profit < 0:
        entries.append({
            "group": "Lãi lỗ",
            "desc": "Kết chuyển lỗ",
            "debit": "4212",
            "credit": "911",
            "amount": abs(profit),
        })

    for idx, e in enumerate(entries, 1):
        e["stt"] = idx

    total = sum(e["amount"] for e in entries)
    deb_911 = sum(e["amount"] for e in entries if e["debit"] == "911")
    crd_911 = sum(e["amount"] for e in entries if e["credit"] == "911")
    check_911 = abs(deb_911 - crd_911) <= 1

    return {
        "entries": entries,
        "total": total,
        "revenue": revenue,
        "expense": expense,
        "profit": profit,
        "check_911": check_911,
        "date_to": tb_data.get("date_to"),
    }


def export_trial_balance_excel(
    company_id: int | None = None,
    year: int = 2026,
    date_from: date | None = None,
    date_to: date | None = None,
    as_of_date: date | None = None,
    include_sub_accounts: bool = True,
) -> bytes:
    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return b""
    data = get_trial_balance_data(year=year, date_from=date_from, date_to=date_to, as_of_date=as_of_date)
    rows_to_export = [r for r in data["all_rows"] if not r.get("hide_in_report")] if include_sub_accounts else [r for r in data["all_rows"] if r["is_parent"]]
    totals = data["totals"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Page 1"
    ws.views.sheetView[0].showGridLines = True

    col_widths = {
        "A": 14.0, "B": 42.0, "C": 18.0, "D": 18.0,
        "E": 18.0, "F": 18.0, "G": 18.0, "H": 18.0
    }
    for col, width in col_widths.items():
        ws.column_dimensions[col].width = width

    font_company = Font(name="Times New Roman", size=11, bold=True)
    font_addr = Font(name="Times New Roman", size=10, italic=True)
    font_mau = Font(name="Times New Roman", size=11, bold=True)
    font_tt = Font(name="Times New Roman", size=9, italic=True)
    font_title = Font(name="Times New Roman", size=15, bold=True)
    font_period = Font(name="Times New Roman", size=11, bold=True)
    font_dvt = Font(name="Times New Roman", size=10, italic=True)
    font_tbl_hdr = Font(name="Times New Roman", size=10, bold=True)
    font_parent = Font(name="Times New Roman", size=10, bold=True)
    font_sub = Font(name="Times New Roman", size=9.5, italic=False)
    font_total = Font(name="Times New Roman", size=10, bold=True)
    font_sign_title = Font(name="Times New Roman", size=11, bold=True)
    font_sign_sub = Font(name="Times New Roman", size=10, italic=True)

    border_thin = Border(
        left=Side(style="thin", color="A0A0A0"),
        right=Side(style="thin", color="A0A0A0"),
        top=Side(style="thin", color="A0A0A0"),
        bottom=Side(style="thin", color="A0A0A0"),
    )
    border_double = Border(
        left=Side(style="thin", color="A0A0A0"),
        right=Side(style="thin", color="A0A0A0"),
        top=Side(style="thin", color="000000"),
        bottom=Side(style="double", color="000000"),
    )
    fill_hdr = PatternFill(start_color="F2F4F7", end_color="F2F4F7", fill_type="solid")
    fill_sub = PatternFill(start_color="FAFAFA", end_color="FAFAFA", fill_type="solid")

    ws["A1"] = data["company_name"]
    ws["A1"].font = font_company
    ws["A2"] = data["address"]
    ws["A2"].font = font_addr
    ws["A3"] = f"Mã số thuế: {data['tax_code']}"
    ws["A3"].font = font_addr

    ws["G1"] = "Mẫu số: F01-DNN"
    ws["G1"].font = font_mau
    ws["G1"].alignment = Alignment(horizontal="right")
    ws["G2"] = "(Ban hành theo TT số 133/2016/TT-BTC"
    ws["G2"].font = font_tt
    ws["G2"].alignment = Alignment(horizontal="right")
    ws["G3"] = "Ngày 26/08/2016 của Bộ Tài chính)"
    ws["G3"].font = font_tt
    ws["G3"].alignment = Alignment(horizontal="right")

    ws["A6"] = "BẢNG CÂN ĐỐI TÀI KHOẢN"
    ws["A6"].font = font_title
    ws["A6"].alignment = Alignment(horizontal="center")
    ws.merge_cells("A6:H6")

    d_str = data["as_of_date"].strftime("%d/%m/%Y")
    ws["A7"] = f"Từ ngày 01/01/{year} đến ngày {d_str}"
    ws["A7"].font = font_period
    ws["A7"].alignment = Alignment(horizontal="center")
    ws.merge_cells("A7:H7")

    ws["H8"] = "Đơn vị tính: VND"
    ws["H8"].font = font_dvt
    ws["H8"].alignment = Alignment(horizontal="right")

    ws["A10"] = "Số hiệu TK"
    ws["B10"] = "Tên tài khoản"
    ws["C10"] = "Số dư đầu kỳ"
    ws["E10"] = "Số phát sinh trong kỳ"
    ws["G10"] = "Số dư cuối kỳ"

    ws.merge_cells("A10:A11")
    ws.merge_cells("B10:B11")
    ws.merge_cells("C10:D10")
    ws.merge_cells("E10:F10")
    ws.merge_cells("G10:H10")

    ws["C11"] = "Nợ"
    ws["D11"] = "Có"
    ws["E11"] = "Nợ"
    ws["F11"] = "Có"
    ws["G11"] = "Nợ"
    ws["H11"] = "Có"

    ws["A12"] = "A"
    ws["B12"] = "B"
    ws["C12"] = "1"
    ws["D12"] = "2"
    ws["E12"] = "3"
    ws["F12"] = "4"
    ws["G12"] = "5"
    ws["H12"] = "6"

    for r in range(10, 13):
        for c in range(1, 9):
            cell = ws.cell(r, c)
            cell.font = font_tbl_hdr
            cell.fill = fill_hdr
            cell.border = border_thin
            cell.alignment = Alignment(horizontal="center", vertical="center")

    fmt_num = "#,##0_);[Red](#,##0)"

    row_idx = 13
    for r in rows_to_export:
        is_p = r.get("is_parent", True)
        name_prefix = "" if is_p else "   • "
        ws.cell(row_idx, 1, r["code"]).alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(row_idx, 2, name_prefix + r["name"]).alignment = Alignment(horizontal="left", vertical="center")

        vals = [r["open_dr"], r["open_cr"], r["ps_dr"], r["ps_cr"], r["close_dr"], r["close_cr"]]
        for c_idx, val in enumerate(vals, 3):
            cell = ws.cell(row_idx, c_idx, val if val != 0 else "")
            cell.alignment = Alignment(horizontal="right", vertical="center")
            cell.number_format = fmt_num

        cur_font = font_parent if is_p else font_sub
        for c_idx in range(1, 9):
            ws.cell(row_idx, c_idx).font = cur_font
            ws.cell(row_idx, c_idx).border = border_thin
            if not is_p:
                ws.cell(row_idx, c_idx).fill = fill_sub
        row_idx += 1

    # Total row
    ws.cell(row_idx, 1, "Cộng").alignment = Alignment(horizontal="center", vertical="center")
    ws.cell(row_idx, 2, "").alignment = Alignment(horizontal="left", vertical="center")
    tot_vals = [totals["open_dr"], totals["open_cr"], totals["ps_dr"], totals["ps_cr"], totals["close_dr"], totals["close_cr"]]
    for c_idx, val in enumerate(tot_vals, 3):
        cell = ws.cell(row_idx, c_idx, val)
        cell.alignment = Alignment(horizontal="right", vertical="center")
        cell.number_format = fmt_num

    for c_idx in range(1, 9):
        ws.cell(row_idx, c_idx).font = font_total
        ws.cell(row_idx, c_idx).border = border_double

    # Signature blocks
    sign_row = row_idx + 3
    today_vn = date.today()
    ws.cell(sign_row - 1, 7, f"Lập, Ngày {today_vn.day} tháng {today_vn.month} năm {today_vn.year}").alignment = Alignment(horizontal="center")
    ws.cell(sign_row - 1, 7).font = font_sign_sub
    ws.merge_cells(start_row=sign_row - 1, start_column=6, end_row=sign_row - 1, end_column=8)

    ws.cell(sign_row, 1, "Người lập biểu").alignment = Alignment(horizontal="center")
    ws.cell(sign_row, 1).font = font_sign_title
    ws.cell(sign_row + 1, 1, "(Ký, họ tên)").alignment = Alignment(horizontal="center")
    ws.cell(sign_row + 1, 1).font = font_sign_sub

    ws.cell(sign_row, 4, "Kế toán trưởng").alignment = Alignment(horizontal="center")
    ws.cell(sign_row, 4).font = font_sign_title
    ws.cell(sign_row + 1, 4, "(Ký, họ tên)").alignment = Alignment(horizontal="center")
    ws.cell(sign_row + 1, 4).font = font_sign_sub

    ws.cell(sign_row, 7, "Người đại diện theo pháp luật").alignment = Alignment(horizontal="center")
    ws.cell(sign_row, 7).font = font_sign_title
    ws.cell(sign_row + 1, 7, "(Ký, họ tên, đóng dấu)").alignment = Alignment(horizontal="center")
    ws.cell(sign_row + 1, 7).font = font_sign_sub
    ws.cell(sign_row + 5, 7, data["director_name"]).alignment = Alignment(horizontal="center")
    ws.cell(sign_row + 5, 7).font = font_company

    # Sheet 2: Ket chuyen cuoi ky
    ws2 = wb.create_sheet("Ket chuyen cuoi ky")
    ws2.views.sheetView[0].showGridLines = True

    col_widths2 = {"A": 6.0, "B": 60.0, "C": 10.0, "D": 10.0, "E": 18.0}
    for col, width in col_widths2.items():
        ws2.column_dimensions[col].width = width

    ws2.merge_cells("A1:E1")
    ws2["A1"] = "BÚT TOÁN KẾT CHUYỂN CUỐI KỲ"
    ws2["A1"].font = font_title
    ws2["A1"].alignment = Alignment(horizontal="center", vertical="center")

    d_from_val = data.get("date_from")
    d_to_val = data.get("date_to")
    d_from_str = d_from_val.strftime("%d/%m/%Y") if hasattr(d_from_val, "strftime") else str(d_from_val or "")
    d_to_str = d_to_val.strftime("%d/%m/%Y") if hasattr(d_to_val, "strftime") else str(d_to_val or "")
    ws2.merge_cells("A2:E2")
    ws2["A2"] = f"Từ ngày {d_from_str} đến ngày {d_to_str}"
    ws2["A2"].font = font_period
    ws2["A2"].alignment = Alignment(horizontal="center", vertical="center")

    headers2 = ["STT", "Diễn giải", "TK Nợ", "TK Có", "Số tiền"]
    for col_idx, h in enumerate(headers2, 1):
        cell = ws2.cell(row=4, column=col_idx, value=h)
        cell.font = font_tbl_hdr
        cell.fill = fill_hdr
        cell.border = border_thin
        if col_idx in (1, 3, 4):
            cell.alignment = Alignment(horizontal="center", vertical="center")
        elif col_idx == 2:
            cell.alignment = Alignment(horizontal="left", vertical="center")
        else:
            cell.alignment = Alignment(horizontal="right", vertical="center")

    closing_data = build_closing_entries(data)
    curr_row = 5
    for e in closing_data["entries"]:
        c1 = ws2.cell(row=curr_row, column=1, value=e["stt"])
        c1.alignment = Alignment(horizontal="center")
        c1.font = font_sub
        c1.border = border_thin

        c2 = ws2.cell(row=curr_row, column=2, value=e["desc"])
        c2.alignment = Alignment(horizontal="left")
        c2.font = font_sub
        c2.border = border_thin

        c3 = ws2.cell(row=curr_row, column=3, value=e["debit"])
        c3.alignment = Alignment(horizontal="center")
        c3.font = font_sub
        c3.border = border_thin

        c4 = ws2.cell(row=curr_row, column=4, value=e["credit"])
        c4.alignment = Alignment(horizontal="center")
        c4.font = font_sub
        c4.border = border_thin

        c5 = ws2.cell(row=curr_row, column=5, value=e["amount"])
        c5.alignment = Alignment(horizontal="right")
        c5.font = font_sub
        c5.number_format = "#,##0"
        c5.border = border_thin

        curr_row += 1

    ws2.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=4)
    c_tot_label = ws2.cell(row=curr_row, column=1, value="Lợi nhuận kế toán trong kỳ")
    c_tot_label.alignment = Alignment(horizontal="right", vertical="center")
    c_tot_label.font = font_total
    for col_i in range(1, 5):
        ws2.cell(row=curr_row, column=col_i).border = border_double

    c_tot_val = ws2.cell(row=curr_row, column=5, value=closing_data["profit"])
    c_tot_val.alignment = Alignment(horizontal="right", vertical="center")
    c_tot_val.font = font_total
    c_tot_val.number_format = "#,##0"
    c_tot_val.border = border_double

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _parse_entry_date(d: Any) -> tuple[date, str]:
    if hasattr(d, "strftime"):
        d_obj = d.date() if hasattr(d, "date") and callable(d.date) else d
        return d_obj, d.strftime("%d/%m/%Y")
    s = str(d or "").strip()
    if "/" in s:
        parts = s.split("/")
        if len(parts) == 3:
            try:
                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                return date(year, month, day), f"{day:02d}/{month:02d}/{year:04d}"
            except Exception:
                pass
    elif "-" in s:
        parts = s[:10].split("-")
        if len(parts) == 3:
            try:
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                return date(year, month, day), f"{day:02d}/{month:02d}/{year:04d}"
            except Exception:
                pass
    return date.min, s


def get_account_ledger_data(
    code: str,
    company_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    company_id = company_id or get_current_company_id()
    if date_from is None:
        date_from = date(2026, 1, 1)
    if date_to is None:
        date_to = date.today()

    code_clean = str(code).strip()
    if not company_id or int(company_id) <= 0:
        return {
            "account_code": code_clean,
            "account_name": "",
            "parent_code": "",
            "open_dr": 0, "open_cr": 0,
            "ps_dr": 0, "ps_cr": 0,
            "close_dr": 0, "close_cr": 0,
            "date_from": date_from,
            "date_to": date_to,
            "company": {"name": "", "tax_code": "", "address": ""},
            "transactions": [],
            "totals": {"open_dr": 0, "open_cr": 0, "ps_dr": 0, "ps_cr": 0, "close_dr": 0, "close_cr": 0},
        }
    tb_data = get_trial_balance_data(company_id=company_id, year=date_from.year, date_from=date_from, date_to=date_to)

    # Tìm thông tin tài khoản từ Bảng cân đối
    account_row = next((r for r in tb_data["all_rows"] if r["code"] == code_clean), None)
    if not account_row:
        account_row = next((r for r in tb_data["all_rows"] if r["code"].startswith(code_clean) or code_clean.startswith(r["code"])), None)

    acc_name = account_row["name"] if account_row else f"Tài khoản {code_clean}"
    account_row.get("is_parent", False) if account_row else True

    director_name = tb_data.get("director_name") or "Chủ sở hữu"
    tb_data.get("tax_code") or ""

    out_cnt_row = db._execute_query(
        "SELECT COUNT(*) as cnt FROM public.bkav_invoices WHERE seller_company_id = %s AND invoice_date >= %s AND invoice_date <= %s",
        (company_id, date_from, date_to), fetch=True, commit=False
    ) or {}
    count_inv_out = int(out_cnt_row.get("cnt") or 0)

    in_cnt_row = db._execute_query(
        "SELECT COUNT(*) as cnt FROM public.transport_input_invoices WHERE buyer_company_id = %s AND invoice_date >= %s AND invoice_date <= %s",
        (company_id, date_from, date_to), fetch=True, commit=False
    ) or {}
    count_inv_in = int(in_cnt_row.get("cnt") or 0)

    items = []
    transactions = []
    external_link = None
    external_label = None
    title = f"SỔ TỔNG HỢP / CHI TIẾT TÀI KHOẢN {code_clean}"

    if code_clean.startswith("131"):
        title = "TỔNG HỢP CÔNG NỢ PHẢI THU"
        external_link = "/admin/transport/catalog?tab=companies&subtab=131"
        external_label = "Mở Sheet 2: Sổ chi tiết TK 131"
        cust_rows = db._execute_query('''
            SELECT COALESCE(c.name, b.buyer_tax_code, 'Khách hàng lẻ') as name,
                   COALESCE(b.buyer_tax_code, 'KH-' || COALESCE(b.buyer_company_id, 0)) as code,
                   COUNT(*) as inv_count,
                   SUM(b.gross) as total_gross,
                   SUM(CASE WHEN b.payment_status = 'paid' THEN b.gross ELSE 0 END) as paid_amount,
                   SUM(CASE WHEN b.payment_status = 'debt' THEN b.debt_amount ELSE 0 END) as debt_amount
            FROM public.bkav_invoices b
            LEFT JOIN public.invoice_companies c ON c.id = b.buyer_company_id
            WHERE b.seller_company_id = %s AND b.invoice_date >= %s AND b.invoice_date <= %s
            GROUP BY c.name, b.buyer_tax_code, b.buyer_company_id
            ORDER BY total_gross DESC
        ''', (company_id, date_from, date_to), fetchall=True, commit=False) or []

        for c in cust_rows:
            gross_val = int(round(float(c["total_gross"] or 0)))
            paid_val = int(round(float(c["paid_amount"] or 0)))
            debt_val = int(round(float(c["debt_amount"] or 0)))
            items.append({
                "code": c["code"],
                "name": c["name"],
                "account_code": "131",
                "open_dr": 0,
                "open_cr": 0,
                "ps_dr": gross_val,
                "ps_cr": paid_val,
                "close_dr": debt_val,
                "close_cr": 0,
                "inv_count": c["inv_count"],
            })

        inv_rows = db._execute_query('''
            SELECT b.invoice_guid, b.invoice_date, b.invoice_number, b.invoice_serial,
                   COALESCE(c.name, b.buyer_tax_code, 'Khách hàng lẻ') as partner,
                   b.service_name as description,
                   b.subtotal, b.gross, b.payment_status, b.debt_amount
            FROM public.bkav_invoices b
            LEFT JOIN public.invoice_companies c ON c.id = b.buyer_company_id
            WHERE b.seller_company_id = %s AND b.invoice_date >= %s AND b.invoice_date <= %s
            ORDER BY b.invoice_date DESC, b.invoice_number DESC LIMIT 100
        ''', (company_id, date_from, date_to), fetchall=True, commit=False) or []
        for inv in inv_rows:
            transactions.append({
                "date": inv["invoice_date"].strftime("%d/%m/%Y") if inv["invoice_date"] else "",
                "number": inv["invoice_number"] or "—",
                "serial": inv["invoice_serial"] or "—",
                "partner": inv["partner"],
                "description": inv["description"] or "Cước dịch vụ vận tải",
                "debit_acc": "131",
                "credit_acc": "5113, 33311",
                "amount": int(round(float(inv["gross"] or 0))),
                "status": "Còn nợ" if inv["payment_status"] == "debt" else "Đã thu tiền",
                "link": f"/admin/vehicles/cargo/invoices/{inv['invoice_guid']}/edit" if inv.get("invoice_guid") else None,
            })

        from frontend.modules.transport.other_vouchers_service import list_vouchers_for_account
        for ov_item in list_vouchers_for_account(company_id, "131", date_from, date_to):
            v_date = ov_item["date"].strftime("%d/%m/%Y") if hasattr(ov_item["date"], "strftime") else str(ov_item["date"] or "")
            transactions.append({
                "date": v_date,
                "number": ov_item["voucher_no"] or "—",
                "serial": "",
                "partner": ov_item.get("partner_name") or "",
                "description": ov_item.get("description") or "Chứng từ nghiệp vụ khác",
                "debit_acc": ov_item["debit_account"],
                "credit_acc": ov_item["credit_account"],
                "amount": int(round(float(ov_item["amount"] or 0))),
                "status": "Chứng từ nghiệp vụ khác",
                "link": "/admin/transport/other-vouchers",
            })

    elif code_clean.startswith("331"):
        title = "TỔNG HỢP CÔNG NỢ PHẢI TRẢ"
        external_link = "/admin/transport/catalog?tab=companies&subtab=331"
        external_label = "Mở Sheet 3: Sổ chi tiết TK 331"
        sup_rows = db._execute_query('''
            SELECT seller_name as name,
                   COALESCE(seller_tax_code, 'NCC-LE') as code,
                   COUNT(*) as inv_count,
                   SUM(total) as total_amount,
                   SUM(subtotal) as sub_amount,
                   SUM(vat_amount) as vat_amount
            FROM public.transport_input_invoices
            WHERE buyer_company_id = %s AND invoice_date >= %s AND invoice_date <= %s
            GROUP BY seller_name, seller_tax_code
            ORDER BY total_amount DESC
        ''', (company_id, date_from, date_to), fetchall=True, commit=False) or []

        for s in sup_rows:
            tot = int(round(float(s["total_amount"] or 0)))
            items.append({
                "code": s["code"],
                "name": s["name"],
                "account_code": "331",
                "open_dr": 0,
                "open_cr": 0,
                "ps_dr": int(round(tot * 0.88)),
                "ps_cr": tot,
                "close_dr": 0,
                "close_cr": int(round(tot * 0.12)),
                "inv_count": s["inv_count"],
            })

        inp_invs = db._execute_query('''
            SELECT invoice_date, invoice_number, serial, seller_name as partner,
                   COALESCE(NULLIF(note, ''), 'Mua hàng / Chi phí xe tải') as description,
                   subtotal, vat_amount, total,
                   COALESCE(tk_no_cost, '154') as tk_no_cost,
                   COALESCE(tk_no_vat, '1331') as tk_no_vat,
                   COALESCE(tk_co_pay, '331') as tk_co_pay
            FROM public.transport_input_invoices
            WHERE buyer_company_id = %s AND invoice_date >= %s AND invoice_date <= %s
            ORDER BY invoice_date DESC, invoice_number DESC LIMIT 100
        ''', (company_id, date_from, date_to), fetchall=True, commit=False) or []
        for inv in inp_invs:
            transactions.append({
                "date": inv["invoice_date"].strftime("%d/%m/%Y") if inv["invoice_date"] else "",
                "number": inv["invoice_number"] or "—",
                "serial": inv["serial"] or "—",
                "partner": inv["partner"],
                "description": inv["description"],
                "debit_acc": f"{inv.get('tk_no_cost') or '154'}, {inv.get('tk_no_vat') or '1331'}",
                "credit_acc": inv.get("tk_co_pay") or "331",
                "amount": int(round(float(inv["total"] or 0))),
                "status": "Đã ghi sổ",
                "link": "/admin/transport/input-invoices",
            })

        from frontend.modules.transport.other_vouchers_service import list_vouchers_for_account
        for ov_item in list_vouchers_for_account(company_id, "331", date_from, date_to):
            v_date = ov_item["date"].strftime("%d/%m/%Y") if hasattr(ov_item["date"], "strftime") else str(ov_item["date"] or "")
            transactions.append({
                "date": v_date,
                "number": ov_item["voucher_no"] or "—",
                "serial": "",
                "partner": ov_item.get("partner_name") or "",
                "description": ov_item.get("description") or "Chứng từ nghiệp vụ khác",
                "debit_acc": ov_item["debit_account"],
                "credit_acc": ov_item["credit_account"],
                "amount": int(round(float(ov_item["amount"] or 0))),
                "status": "Chứng từ nghiệp vụ khác",
                "link": "/admin/transport/other-vouchers",
            })

    elif code_clean.startswith("153"):
        title = "SỔ CHI TIẾT CÔNG CỤ DỤNG CỤ (TK 153)"
        external_link = "/admin/transport/input-invoices"
        external_label = "Mở Hóa đơn đầu vào CCDC"
        row_153 = next((p for p in tb_data.get("parent_rows", []) if p["code"] == "153"), None)
        sub_list = row_153.get("sub_accounts", []) if row_153 else []
        for sub in sub_list:
            items.append({
                "code": sub["code"],
                "name": sub["name"],
                "account_code": sub["code"],
                "open_dr": sub.get("open_dr", 0),
                "open_cr": sub.get("open_cr", 0),
                "ps_dr": sub.get("ps_dr", 0),
                "ps_cr": sub.get("ps_cr", 0),
                "close_dr": sub.get("close_dr", 0),
                "close_cr": sub.get("close_cr", 0),
                "inv_count": sub.get("source_note") or "",
            })
        inp_invs = db._execute_query('''
            SELECT id, invoice_date, invoice_number, serial, seller_name as partner,
                   COALESCE(NULLIF(note, ''), 'Mua quả cân chuẩn 500kg / CCDC') as description,
                   subtotal, vat_amount, total,
                   COALESCE(tk_no_cost, '153') as tk_no_cost,
                   COALESCE(tk_no_vat, '1331') as tk_no_vat,
                   COALESCE(tk_co_pay, '331') as tk_co_pay
            FROM public.transport_input_invoices
            WHERE buyer_company_id = %s AND invoice_date >= %s AND invoice_date <= %s
              AND tk_no_cost = '153'
            ORDER BY invoice_date DESC, invoice_number DESC
        ''', (company_id, date_from, date_to), fetchall=True, commit=False) or []
        for inv in inp_invs:
            transactions.append({
                "date": inv["invoice_date"].strftime("%d/%m/%Y") if inv["invoice_date"] else "",
                "number": inv["invoice_number"] or "—",
                "serial": inv["serial"] or "—",
                "partner": inv["partner"],
                "description": inv["description"],
                "debit_acc": f"{inv.get('tk_no_cost') or '153'}, {inv.get('tk_no_vat') or '1331'}",
                "credit_acc": inv.get("tk_co_pay") or "331",
                "amount": int(round(float(inv["total"] or 0))),
                "status": "Đã ghi sổ",
                "link": f"/admin/transport/input-invoices?edit={inv.get('id', '')}",
            })

    elif code_clean.startswith("154") or code_clean == "632":
        title = "SỔ TẬP HỢP CHI PHÍ & GIÁ VỐN VẬN TẢI"
        external_link = "/admin/transport/input-invoices"
        external_label = f"Mở {count_inv_in} Hóa đơn chi phí đầu vào"
        row_154 = next((p for p in tb_data.get("parent_rows", []) if p["code"] == "154"), None)
        sub_list = row_154.get("sub_accounts", []) if row_154 else []

        for sub in sub_list:
            if code_clean == "154" or code_clean == "632" or code_clean == sub["code"]:
                items.append({
                    "code": sub["code"],
                    "name": sub["name"],
                    "account_code": sub["code"],
                    "open_dr": sub.get("open_dr", 0),
                    "open_cr": sub.get("open_cr", 0),
                    "ps_dr": sub.get("ps_dr", 0),
                    "ps_cr": sub.get("ps_cr", 0) if code_clean == "154" else 0,
                    "close_dr": sub.get("close_dr", 0),
                    "close_cr": sub.get("close_cr", 0),
                    "inv_count": sub.get("source_note") or "",
                })

        if code_clean == "632":
            title = "SỔ CHI TIẾT GIÁ VỐN DỊCH VỤ VẬN TẢI"
            ps_154 = row_154.get("ps_dr", 0) if row_154 else 0
            items.append({
                "code": "632",
                "name": "Giá vốn dịch vụ vận tải (Kết chuyển toàn bộ từ 154)",
                "account_code": "632",
                "open_dr": 0, "open_cr": 0,
                "ps_dr": ps_154, "ps_cr": 0,
                "close_dr": ps_154, "close_cr": 0,
                "inv_count": "Kết chuyển chi phí 154",
            })

    elif code_clean.startswith("211") or code_clean.startswith("214"):
        title = "SỔ THEO DÕI TÀI SẢN CỐ ĐỊNH & KHẤU HAO XE TẢI"
        external_link = "/admin/transport/depreciation"
        external_label = "Mở Bảng tính khấu hao TSCĐ"
        from frontend.modules.transport.depreciation_service import get_depreciation_data
        depr_detail = get_depreciation_data(company_id=company_id, year=date_from.year)
        items = []
        is_211 = "211" in code_clean
        for v in depr_detail.get("rows", []):
            orig_p = int(v.get("original_price") or 0)
            cum_dep = int(v.get("cumulative_depreciation") or 0)
            p_code = v.get("plate_number") or v.get("name") or "Xe"
            items.append({
                "code": p_code,
                "name": f"Xe {p_code} (Nguyên giá {orig_p:,.0f} đ, KH lũy kế: {cum_dep:,.0f} đ)",
                "account_code": "2113" if is_211 else "2141",
                "open_dr": orig_p if is_211 else 0,
                "open_cr": 0 if is_211 else cum_dep,
                "ps_dr": 0,
                "ps_cr": 0,
                "close_dr": orig_p if is_211 else 0,
                "close_cr": 0 if is_211 else cum_dep,
                "inv_count": v.get("status_text") or "Đang hoạt động",
            })

    elif code_clean.startswith("111"):
        from frontend.modules.transport.cash_bank_service import get_cash_book_data
        cash_res = get_cash_book_data(company_id=company_id, year=date_from.year, date_from=date_from, date_to=date_to)
        title = "SỔ QUỸ TIỀN MẶT (MẪU S04a-DNN - THÔNG TƯ 133)"
        external_link = "/admin/transport/cash-book"
        external_label = "Mở Sổ quỹ tiền mặt chi tiết"
        for i, e in enumerate(cash_res["entries"], 1):
            items.append({
                "code": e["receipt_no"] or e["payment_no"] or f"PT-{i:02d}",
                "name": e["description"] + (f" ({e['partner_name']})" if e["partner_name"] else ""),
                "account_code": "1111",
                "open_dr": 0, "open_cr": 0,
                "ps_dr": e["receipt_amount"],
                "ps_cr": e["payment_amount"],
                "close_dr": e["balance"],
                "close_cr": 0,
                "inv_count": e["entry_date"],
            })
            transactions.append({
                "date": e["entry_date"],
                "number": e["receipt_no"] or e["payment_no"] or "—",
                "serial": e["voucher_date"],
                "partner": e["partner_name"] or "Khách hàng / NCC",
                "description": e["description"],
                "debit_acc": "1111" if e["receipt_amount"] > 0 else e["offset_account"],
                "credit_acc": e["offset_account"] if e["receipt_amount"] > 0 else "1111",
                "amount": e["receipt_amount"] if e["receipt_amount"] > 0 else e["payment_amount"],
                "status": "Phiếu thu" if e["receipt_amount"] > 0 else "Phiếu chi",
                "link": "/admin/transport/cash-book",
            })

    elif code_clean.startswith("112"):
        from frontend.modules.transport.cash_bank_service import get_bank_book_data
        bank_res = get_bank_book_data(company_id=company_id, year=date_from.year, date_from=date_from, date_to=date_to)
        title = "SỔ TIỀN GỬI NGÂN HÀNG (MẪU S05a-DNN - THÔNG TƯ 133)"
        external_link = "/admin/transport/bank-book"
        external_label = "Mở Sổ tiền gửi ngân hàng"
        for i, e in enumerate(bank_res["entries"], 1):
            items.append({
                "code": e["voucher_no"] or f"NH-{i:02d}",
                "name": e["description"] + (f" ({e['partner_name']})" if e["partner_name"] else ""),
                "account_code": "1121",
                "open_dr": 0, "open_cr": 0,
                "ps_dr": e["deposit_amount"],
                "ps_cr": e["withdraw_amount"],
                "close_dr": e["balance"],
                "close_cr": 0,
                "inv_count": e["entry_date"],
            })
            transactions.append({
                "date": e["entry_date"],
                "number": e["voucher_no"],
                "serial": e["voucher_date"],
                "partner": e["partner_name"] or tb_data.get("company_name", "Ngân hàng"),
                "description": e["description"],
                "debit_acc": "1121" if e["deposit_amount"] > 0 else e["offset_account"],
                "credit_acc": e["offset_account"] if e["deposit_amount"] > 0 else "1121",
                "amount": e["deposit_amount"] if e["deposit_amount"] > 0 else e["withdraw_amount"],
                "status": "Báo Có (Thu)" if e["deposit_amount"] > 0 else "Báo Nợ (Chi)",
                "link": "/admin/transport/bank-book",
            })

    elif code_clean.startswith("242"):
        from frontend.modules.transport.prepaid_expense_service import get_prepaid_expenses_data
        pe_res = get_prepaid_expenses_data(company_id=company_id, year=date_from.year, date_from=date_from, date_to=date_to)
        title = f"BẢNG PHÂN BỔ CHI PHÍ TRẢ TRƯỚC DÀI HẠN / CCDC (TK 242) NĂM {date_from.year}"
        external_link = "/admin/transport/prepaid-expenses"
        external_label = "Mở Bảng phân bổ CCDC (quản lý quả cân, máy tính...)"
        for it in pe_res["items"]:
            items.append({
                "code": it["item_code"],
                "name": f"{it['name']} ({it['unit']}) - Đưa vào SD: {it['start_date']}",
                "account_code": "242",
                "open_dr": it["rem_start"],
                "open_cr": 0,
                "ps_dr": 0,
                "ps_cr": it["alloc_year"],
                "close_dr": it["rem_end"],
                "close_cr": 0,
                "inv_count": f"{it['total_months']} tháng",
            })
            transactions.append({
                "date": it["start_date"],
                "number": it["item_code"],
                "serial": f"{it['total_months']}T",
                "partner": it["category"],
                "description": f"Phân bổ {it['name']} vào chi phí (Nợ {it['offset_account']}/Có 242)",
                "debit_acc": it["offset_account"],
                "credit_acc": "242",
                "amount": it["alloc_year"],
                "status": "Phân bổ CCDC",
                "link": "/admin/transport/prepaid-expenses",
            })

    elif code_clean.startswith("511"):
        title = "SỔ CHI TIẾT DOANH THU CƯỚC DỊCH VỤ VẬN TẢI"
        external_link = "/admin/vehicles/cargo"
        external_label = "Mở Danh sách chuyến hàng & Hóa đơn cước"
        ps_cr_511 = account_row["ps_cr"] if account_row else 0
        items = [
            {"code": "5113", "name": f"Doanh thu cung cấp dịch vụ cước vận tải ({count_inv_out} Hóa đơn)", "account_code": "5113", "open_dr": 0, "open_cr": 0, "ps_dr": 0, "ps_cr": ps_cr_511, "close_dr": 0, "close_cr": ps_cr_511, "inv_count": f"{count_inv_out} HĐ"},
        ]

    elif code_clean.startswith("515"):
        from frontend.modules.transport.cash_bank_service import get_bank_book_data
        title = "SỔ CHI TIẾT DOANH THU HOẠT ĐỘNG TÀI CHÍNH (LÃI TIỀN GỬI)"
        external_link = "/admin/transport/bank-book"
        external_label = "Mở Sổ tiền gửi ngân hàng"
        ps_dr_515 = account_row.get("ps_dr", 0) if account_row else 0
        ps_cr_515 = account_row.get("ps_cr", 0) if account_row else 0
        items = [
            {
                "code": "515",
                "name": "Lãi tiền gửi ngân hàng",
                "account_code": "515",
                "open_dr": 0,
                "open_cr": 0,
                "ps_dr": ps_dr_515,
                "ps_cr": ps_cr_515,
                "close_dr": 0,
                "close_cr": 0,
                "inv_count": "1 mục",
            }
        ]
        bank_res = get_bank_book_data(company_id=company_id, year=date_from.year, date_from=date_from, date_to=date_to)
        for e in bank_res.get("entries", []):
            dep_amt = e.get("deposit_amount") or 0
            off_acc = str(e.get("offset_account") or "").strip()
            if dep_amt > 0 and off_acc.startswith("515"):
                transactions.append({
                    "date": e.get("entry_date"),
                    "number": e.get("voucher_no"),
                    "serial": e.get("voucher_date"),
                    "partner": e.get("partner_name") or "Ngân hàng",
                    "description": e.get("description"),
                    "debit_acc": "1121",
                    "credit_acc": "515",
                    "amount": e.get("deposit_amount"),
                    "status": "Báo Có (Thu)",
                    "link": "/admin/transport/bank-book",
                })

    elif code_clean.startswith("133") or code_clean == "333" or code_clean.startswith("3331"):
        title = "BẢNG KÊ THUẾ GIÁ TRỊ GIA TĂNG (GTGT)"
        external_link = "/admin/transport/tax-report"
        external_label = "Mở Báo cáo thuế theo quý"
        row_133 = next((p for p in tb_data.get("parent_rows", []) if p["code"] == "133"), {})
        row_333 = next((p for p in tb_data.get("parent_rows", []) if p["code"] == "333"), {})
        items = [
            {"code": "1331", "name": f"Thuế GTGT đầu vào được khấu trừ của HHDV mua vào ({count_inv_in} HĐ đầu vào)", "account_code": "1331", "open_dr": 0, "open_cr": 0, "ps_dr": row_133.get("ps_dr", 0), "ps_cr": 0, "close_dr": row_133.get("close_dr", 0), "close_cr": 0, "inv_count": f"{count_inv_in} HĐ"},
            {"code": "33311", "name": f"Thuế GTGT đầu ra phải nộp của dịch vụ vận tải ({count_inv_out} HĐ bán ra)", "account_code": "33311", "open_dr": 0, "open_cr": 0, "ps_dr": row_333.get("ps_dr", 0), "ps_cr": row_333.get("ps_cr", 0), "close_dr": 0, "close_cr": row_333.get("close_cr", 0), "inv_count": f"{count_inv_out} HĐ"},
        ]

    elif code_clean.startswith(("3334", "3335", "3338", "3339", "3388")):
        from frontend.modules.transport.cash_bank_service import get_cash_book_data, get_bank_book_data
        from frontend.modules.transport.other_vouchers_service import list_vouchers_for_account
        title = f"SỔ CHI TIẾT TÀI KHOẢN {code_clean} - {acc_name.upper()}"
        external_link = "/admin/transport/bank-book"
        external_label = "Mở Sổ tiền gửi ngân hàng"
        items = [
            {
                "code": code_clean,
                "name": acc_name,
                "account_code": code_clean,
                "open_dr": account_row.get("open_dr", 0) if account_row else 0,
                "open_cr": account_row.get("open_cr", 0) if account_row else 0,
                "ps_dr": account_row.get("ps_dr", 0) if account_row else 0,
                "ps_cr": account_row.get("ps_cr", 0) if account_row else 0,
                "close_dr": account_row.get("close_dr", 0) if account_row else 0,
                "close_cr": account_row.get("close_cr", 0) if account_row else 0,
                "inv_count": "1 mục",
            }
        ]
        tx_items: list[tuple[date, int, dict[str, Any]]] = []

        cash_res = get_cash_book_data(company_id=company_id, year=date_from.year, date_from=date_from, date_to=date_to)
        for e in cash_res.get("entries", []):
            off = str(e.get("offset_account") or "").strip()
            if not off.startswith(code_clean):
                continue
            pay_amt = int(round(float(e.get("payment_amount") or 0)))
            rec_amt = int(round(float(e.get("receipt_amount") or 0)))
            d_obj, d_str = _parse_entry_date(e.get("entry_date"))
            if pay_amt > 0:
                tx_items.append((d_obj, len(tx_items), {
                    "date": d_str,
                    "number": e.get("payment_no") or "—",
                    "serial": "",
                    "partner": e.get("partner_name") or "",
                    "description": e.get("description") or "",
                    "debit_acc": off,
                    "credit_acc": "1111",
                    "amount": pay_amt,
                    "status": "Phiếu chi",
                    "link": "/admin/transport/cash-book",
                }))
                if code_clean.startswith("3339") and is_registration_fee_entry(e):
                    tx_items.append((d_obj, len(tx_items), {
                        "date": d_str,
                        "number": e.get("payment_no") or "—",
                        "serial": "",
                        "partner": e.get("partner_name") or "",
                        "description": "Lệ phí trước bạ tính vào nguyên giá TSCĐ",
                        "debit_acc": "211",
                        "credit_acc": "3339",
                        "amount": pay_amt,
                        "status": "Ghi tăng nguyên giá",
                        "link": "/admin/transport/depreciation",
                    }))
            elif rec_amt > 0:
                tx_items.append((d_obj, len(tx_items), {
                    "date": d_str,
                    "number": e.get("receipt_no") or "—",
                    "serial": "",
                    "partner": e.get("partner_name") or "",
                    "description": e.get("description") or "",
                    "debit_acc": "1111",
                    "credit_acc": off,
                    "amount": rec_amt,
                    "status": "Phiếu thu",
                    "link": "/admin/transport/cash-book",
                }))

        bank_res = get_bank_book_data(company_id=company_id, year=date_from.year, date_from=date_from, date_to=date_to)
        for e in bank_res.get("entries", []):
            off = str(e.get("offset_account") or "").strip()
            if not off.startswith(code_clean):
                continue
            with_amt = int(round(float(e.get("withdraw_amount") or 0)))
            dep_amt = int(round(float(e.get("deposit_amount") or 0)))
            d_obj, d_str = _parse_entry_date(e.get("entry_date"))
            if with_amt > 0:
                tx_items.append((d_obj, len(tx_items), {
                    "date": d_str,
                    "number": e.get("voucher_no") or "—",
                    "serial": "",
                    "partner": e.get("partner_name") or "",
                    "description": e.get("description") or "",
                    "debit_acc": off,
                    "credit_acc": "1121",
                    "amount": with_amt,
                    "status": "Báo Nợ (Chi)",
                    "link": "/admin/transport/bank-book",
                }))
                if code_clean.startswith("3339") and is_registration_fee_entry(e):
                    tx_items.append((d_obj, len(tx_items), {
                        "date": d_str,
                        "number": e.get("voucher_no") or "—",
                        "serial": "",
                        "partner": e.get("partner_name") or "",
                        "description": "Lệ phí trước bạ tính vào nguyên giá TSCĐ",
                        "debit_acc": "211",
                        "credit_acc": "3339",
                        "amount": with_amt,
                        "status": "Ghi tăng nguyên giá",
                        "link": "/admin/transport/depreciation",
                    }))
            elif dep_amt > 0:
                tx_items.append((d_obj, len(tx_items), {
                    "date": d_str,
                    "number": e.get("voucher_no") or "—",
                    "serial": "",
                    "partner": e.get("partner_name") or "",
                    "description": e.get("description") or "",
                    "debit_acc": "1121",
                    "credit_acc": off,
                    "amount": dep_amt,
                    "status": "Báo Có (Thu)",
                    "link": "/admin/transport/bank-book",
                }))

        for acc in ("3339", "3388"):
            if not acc.startswith(code_clean):
                continue
            for ov_item in list_vouchers_for_account(company_id, acc, date_from, date_to):
                d_obj, d_str = _parse_entry_date(ov_item.get("date"))
                ov_desc = ov_item.get("description") or "Chứng từ nghiệp vụ khác"
                ov_amt = int(round(float(ov_item.get("amount") or 0)))
                deb_acc = ov_item.get("debit_account") or ""
                cred_acc = ov_item.get("credit_account") or ""
                tx_items.append((d_obj, len(tx_items), {
                    "date": d_str,
                    "number": ov_item.get("voucher_no") or "—",
                    "serial": "",
                    "partner": ov_item.get("partner_name") or "",
                    "description": ov_desc,
                    "debit_acc": deb_acc,
                    "credit_acc": cred_acc,
                    "amount": ov_amt,
                    "status": "Chứng từ nghiệp vụ khác",
                    "link": "/admin/transport/other-vouchers",
                }))
                if acc == "3339" and deb_acc == "3339" and is_registration_fee_text(ov_desc):
                    tx_items.append((d_obj, len(tx_items), {
                        "date": d_str,
                        "number": ov_item.get("voucher_no") or "—",
                        "serial": "",
                        "partner": ov_item.get("partner_name") or "",
                        "description": "Lệ phí trước bạ tính vào nguyên giá TSCĐ",
                        "debit_acc": "211",
                        "credit_acc": "3339",
                        "amount": ov_amt,
                        "status": "Ghi tăng nguyên giá",
                        "link": "/admin/transport/depreciation",
                    }))

        tx_items.sort(key=lambda x: (x[0], x[1]))
        transactions = [x[2] for x in tx_items]

    elif code_clean.startswith("411"):
        title = "SỔ THEO DÕI VỐN GÓP CHỦ SỞ HỮU"
        external_link = "/admin/transport/cash-book"
        external_label = "Mở Sổ quỹ tiền mặt (Lập phiếu góp vốn)"
        capital_rows = db._execute_query('''
            SELECT 
                id, voucher_no, entry_date, voucher_date, voucher_type,
                amount, partner_name, description, offset_account, book_type
            FROM public.transport_cash_book
            WHERE company_id = %s 
              AND (offset_account ILIKE %s OR description ILIKE %s OR description ILIKE %s)
              AND entry_date >= %s AND entry_date <= %s
            ORDER BY entry_date ASC, id ASC
        ''', (company_id, '%411%', '%góp vốn%', '%VĐL%', date_from, date_to), fetchall=True, commit=False) or []

        prev_by_partner_rows = db._execute_query('''
            SELECT 
                partner_name,
                COALESCE(SUM(CASE WHEN voucher_type = 'receipt' THEN amount ELSE 0 END), 0) as prev_cr,
                COALESCE(SUM(CASE WHEN voucher_type = 'payment' THEN amount ELSE 0 END), 0) as prev_dr
            FROM public.transport_cash_book
            WHERE company_id = %s 
              AND (offset_account ILIKE %s OR description ILIKE %s OR description ILIKE %s)
              AND entry_date < %s
            GROUP BY partner_name
        ''', (company_id, '%411%', '%góp vốn%', '%VĐL%', date_from), fetchall=True, commit=False) or []

        partner_cap = {}
        def _norm_str(s: str) -> str:
            import unicodedata
            return unicodedata.normalize("NFC", str(s or "").strip().lower())

        for r in prev_by_partner_rows:
            pname = (r.get("partner_name") or "").strip() or director_name
            match_key = next((k for k in partner_cap if _norm_str(k) == _norm_str(pname)), None)
            net_prev = int(round(float(r.get("prev_cr") or 0))) - int(round(float(r.get("prev_dr") or 0)))
            if not match_key:
                match_key = pname
                partner_cap[match_key] = {
                    "name": f"Vốn góp của {pname}",
                    "open_cr": net_prev,
                    "ps_dr": 0,
                    "ps_cr": 0,
                    "close_cr": net_prev,
                }
            else:
                partner_cap[match_key]["open_cr"] += net_prev
                partner_cap[match_key]["close_cr"] += net_prev

        for r in capital_rows:
            pname = (r.get("partner_name") or "").strip() or director_name
            match_key = next((k for k in partner_cap if _norm_str(k) == _norm_str(pname)), None)
            if not match_key:
                match_key = pname
                partner_cap[match_key] = {
                    "name": f"Vốn góp của {pname}",
                    "open_cr": 0,
                    "ps_dr": 0,
                    "ps_cr": 0,
                    "close_cr": 0,
                }
            amt = int(round(float(r["amount"] or 0)))
            if r["voucher_type"] == "receipt":
                partner_cap[match_key]["ps_cr"] += amt
                partner_cap[match_key]["close_cr"] += amt
            else:
                partner_cap[match_key]["ps_dr"] += amt
                partner_cap[match_key]["close_cr"] -= amt

            is_bank = (str(r.get("book_type") or "").strip().lower() == "bank")
            cash_or_bank_acc = "1121" if is_bank else "1111"
            if r["voucher_type"] == "receipt":
                debit_acc = cash_or_bank_acc
                credit_acc = r["offset_account"] or "4111"
                status_txt = "Góp vốn"
            else:
                debit_acc = r["offset_account"] or "4111"
                credit_acc = cash_or_bank_acc
                status_txt = "Rút vốn"

            transactions.append({
                "date": r["entry_date"].strftime("%d/%m/%Y") if r["entry_date"] else "",
                "number": r["voucher_no"] or "—",
                "serial": r["voucher_date"].strftime("%d/%m/%Y") if r["voucher_date"] else "",
                "partner": pname,
                "description": r["description"] or ("Góp vốn điều lệ" if r["voucher_type"] == "receipt" else "Rút vốn"),
                "debit_acc": debit_acc,
                "credit_acc": credit_acc,
                "amount": amt,
                "status": status_txt,
                "link": "/admin/transport/bank-book" if is_bank else "/admin/transport/cash-book",
            })

        acc_open_cr = int(round(account_row.get("open_cr", 0) if account_row else 0))
        tot_prev = sum(p["open_cr"] for p in partner_cap.values())
        unalloc = acc_open_cr - tot_prev

        items = []
        if unalloc > 0:
            items.append({
                "code": "4111",
                "name": "Số dư vốn đầu kỳ chưa phân bổ",
                "account_code": "4111",
                "open_dr": 0,
                "open_cr": unalloc,
                "ps_dr": 0,
                "ps_cr": 0,
                "close_dr": 0,
                "close_cr": unalloc,
                "inv_count": "Vốn đầu kỳ",
            })

        for pname, pdata in partner_cap.items():
            items.append({
                "code": "4111",
                "name": pdata["name"],
                "account_code": "4111",
                "open_dr": 0,
                "open_cr": pdata["open_cr"],
                "ps_dr": pdata["ps_dr"],
                "ps_cr": pdata["ps_cr"],
                "close_dr": 0,
                "close_cr": pdata["close_cr"],
                "inv_count": "Vốn góp",
            })

    elif code_clean.startswith("421"):
        title = "SỔ THEO DÕI LỢI NHUẬN SAU THUẾ CHƯA PHÂN PHỐI"
        ps_cr_421 = account_row.get("ps_cr", 0) if account_row else 0
        close_cr_421 = account_row.get("close_cr", 0) if account_row else 0
        items = [
            {"code": "4212", "name": "Lợi nhuận sau thuế chưa phân phối năm nay (Kết quả SXKD vận tải)", "account_code": "4212", "open_dr": 0, "open_cr": 0, "ps_dr": 0, "ps_cr": ps_cr_421, "close_dr": 0, "close_cr": close_cr_421, "inv_count": "Lãi ròng"},
        ]

    elif code_clean.startswith("711"):
        from frontend.modules.transport.other_vouchers_service import list_vouchers_for_account
        title = "SỔ CHI TIẾT THU NHẬP KHÁC (TK 711)"
        external_link = "/admin/transport/other-vouchers"
        external_label = "Mở Chứng từ nghiệp vụ khác"
        items = [
            {
                "code": "711",
                "name": "Thu nhập khác",
                "account_code": "711",
                "open_dr": account_row.get("open_dr", 0) if account_row else 0,
                "open_cr": account_row.get("open_cr", 0) if account_row else 0,
                "ps_dr": account_row.get("ps_dr", 0) if account_row else 0,
                "ps_cr": account_row.get("ps_cr", 0) if account_row else 0,
                "close_dr": account_row.get("close_dr", 0) if account_row else 0,
                "close_cr": account_row.get("close_cr", 0) if account_row else 0,
                "inv_count": "1 mục",
            }
        ]
        for ov_item in list_vouchers_for_account(company_id, "711", date_from, date_to):
            v_date = ov_item["date"].strftime("%d/%m/%Y") if hasattr(ov_item["date"], "strftime") else str(ov_item["date"] or "")
            transactions.append({
                "date": v_date,
                "number": ov_item["voucher_no"] or "—",
                "serial": "",
                "partner": ov_item.get("partner_name") or "",
                "description": ov_item.get("description") or "Chứng từ nghiệp vụ khác",
                "debit_acc": ov_item["debit_account"],
                "credit_acc": ov_item["credit_account"],
                "amount": int(round(float(ov_item["amount"] or 0))),
                "status": "Chứng từ nghiệp vụ khác",
                "link": "/admin/transport/other-vouchers",
            })

    elif code_clean.startswith("811"):
        from frontend.modules.transport.other_vouchers_service import list_vouchers_for_account
        title = "SỔ CHI TIẾT CHI PHÍ KHÁC (TK 811)"
        external_link = "/admin/transport/other-vouchers"
        external_label = "Mở Chứng từ nghiệp vụ khác"
        items = [
            {
                "code": "811",
                "name": "Chi phí khác",
                "account_code": "811",
                "open_dr": account_row.get("open_dr", 0) if account_row else 0,
                "open_cr": account_row.get("open_cr", 0) if account_row else 0,
                "ps_dr": account_row.get("ps_dr", 0) if account_row else 0,
                "ps_cr": account_row.get("ps_cr", 0) if account_row else 0,
                "close_dr": account_row.get("close_dr", 0) if account_row else 0,
                "close_cr": account_row.get("close_cr", 0) if account_row else 0,
                "inv_count": "1 mục",
            }
        ]
        for ov_item in list_vouchers_for_account(company_id, "811", date_from, date_to):
            v_date = ov_item["date"].strftime("%d/%m/%Y") if hasattr(ov_item["date"], "strftime") else str(ov_item["date"] or "")
            transactions.append({
                "date": v_date,
                "number": ov_item["voucher_no"] or "—",
                "serial": "",
                "partner": ov_item.get("partner_name") or "",
                "description": ov_item.get("description") or "Chứng từ nghiệp vụ khác",
                "debit_acc": ov_item["debit_account"],
                "credit_acc": ov_item["credit_account"],
                "amount": int(round(float(ov_item["amount"] or 0))),
                "status": "Chứng từ nghiệp vụ khác",
                "link": "/admin/transport/other-vouchers",
            })

    else:
        title = f"SỔ CHI TIẾT TÀI KHOẢN {code_clean} - {acc_name.upper()}"
        items = [
            {
                "code": code_clean,
                "name": acc_name,
                "account_code": code_clean,
                "open_dr": account_row.get("open_dr", 0) if account_row else 0,
                "open_cr": account_row.get("open_cr", 0) if account_row else 0,
                "ps_dr": account_row.get("ps_dr", 0) if account_row else 0,
                "ps_cr": account_row.get("ps_cr", 0) if account_row else 0,
                "close_dr": account_row.get("close_dr", 0) if account_row else 0,
                "close_cr": account_row.get("close_cr", 0) if account_row else 0,
                "inv_count": "1 mục",
            }
        ]

    # For running-balance books like Cash (111) and Bank (112), items rows are individual transactions,
    # so sum(close_dr) would sum cumulative running balances repeatedly.
    if code_clean.startswith("111"):
        from frontend.modules.transport.cash_bank_service import get_cash_book_data
        cash_res = get_cash_book_data(company_id=company_id, year=date_from.year, date_from=date_from, date_to=date_to)
        t_open_dr = int(round(cash_res.get("opening_balance") or 0))
        t_open_cr = 0
        t_ps_dr = int(round(cash_res.get("total_receipt") or 0))
        t_ps_cr = int(round(cash_res.get("total_payment") or 0))
        # Số dư cuối kỳ ở dòng Tổng cộng phải bằng số dư của dòng cuối cùng ngay trên nó, không phải cộng dồn
        if items:
            t_close_dr = int(items[-1].get("close_dr") or 0)
            t_close_cr = int(items[-1].get("close_cr") or 0)
        else:
            t_close_dr = int(round(cash_res.get("closing_balance") or 0))
            t_close_cr = 0
    elif code_clean.startswith("112"):
        from frontend.modules.transport.cash_bank_service import get_bank_book_data
        bank_res = get_bank_book_data(company_id=company_id, year=date_from.year, date_from=date_from, date_to=date_to)
        t_open_dr = int(round(bank_res.get("opening_balance") or 0))
        t_open_cr = 0
        t_ps_dr = int(round(bank_res.get("total_deposit") or 0))
        t_ps_cr = int(round(bank_res.get("total_withdraw") or 0))
        # Số dư cuối kỳ ở dòng Tổng cộng phải bằng số dư của dòng cuối cùng ngay trên nó, không phải cộng dồn
        if items:
            t_close_dr = int(items[-1].get("close_dr") or 0)
            t_close_cr = int(items[-1].get("close_cr") or 0)
        else:
            t_close_dr = int(round(bank_res.get("closing_balance") or 0))
            t_close_cr = 0
    else:
        t_open_dr = sum(int(i.get("open_dr") or 0) for i in items)
        t_open_cr = sum(int(i.get("open_cr") or 0) for i in items)
        t_ps_dr = sum(int(i.get("ps_dr") or 0) for i in items)
        t_ps_cr = sum(int(i.get("ps_cr") or 0) for i in items)
        t_close_dr = sum(int(i.get("close_dr") or 0) for i in items)
        t_close_cr = sum(int(i.get("close_cr") or 0) for i in items)

    totals = {
        "open_dr": t_open_dr,
        "open_cr": t_open_cr,
        "ps_dr": t_ps_dr,
        "ps_cr": t_ps_cr,
        "close_dr": t_close_dr,
        "close_cr": t_close_cr,
        "count": len(items),
    }

    return {
        "account_code": code_clean,
        "account_name": acc_name,
        "title": title,
        "company_name": "CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH",
        "tax_code": "2500774911",
        "date_from": date_from.strftime("%Y-%m-%d") if date_from else "",
        "date_to": date_to.strftime("%Y-%m-%d") if date_to else "",
        "date_str": f"Từ ngày {date_from.strftime('%d/%m/%Y')} đến ngày {date_to.strftime('%d/%m/%Y')}",
        "external_link": external_link,
        "external_label": external_label,
        "items": items,
        "transactions": transactions,
        "totals": totals,
    }


def export_account_ledger_excel(
    code: str,
    company_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> bytes:
    company_id = company_id or get_current_company_id()
    if not company_id or int(company_id) <= 0:
        return b""
    data = get_account_ledger_data(code=code, date_from=date_from, date_to=date_to)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"TK_{data['account_code']}"
    ws.views.sheetView[0].showGridLines = True

    col_widths = {"A": 18.0, "B": 42.0, "C": 14.0, "D": 18.0, "E": 18.0, "F": 18.0, "G": 18.0, "H": 18.0, "I": 18.0}
    for col, width in col_widths.items():
        ws.column_dimensions[col].width = width

    font_company = Font(name="Times New Roman", size=11, bold=True)
    font_title = Font(name="Times New Roman", size=15, bold=True)
    font_period = Font(name="Times New Roman", size=11, bold=True)
    font_tbl_hdr = Font(name="Times New Roman", size=10, bold=True)
    font_row = Font(name="Times New Roman", size=10)
    font_total = Font(name="Times New Roman", size=10, bold=True)

    border_thin = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))
    fill_hdr = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    fill_tot = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")

    ws["A1"] = data["company_name"]
    ws["A1"].font = font_company
    ws["A2"] = f"MST: {data['tax_code']}"
    ws["A2"].font = Font(name="Times New Roman", size=10, italic=True)

    ws.merge_cells("A4:I4")
    ws["A4"] = data["title"]
    ws["A4"].font = font_title
    ws["A4"].alignment = Alignment(horizontal="center")

    ws.merge_cells("A5:I5")
    ws["A5"] = f"Tài khoản: {data['account_code']} - {data['account_name']}; {data['date_str']}"
    ws["A5"].font = font_period
    ws["A5"].alignment = Alignment(horizontal="center")

    # Table Header (Row 7 & 8)
    ws.merge_cells("A7:A8"); ws["A7"] = "Mã đối tượng"; ws["A7"].font = font_tbl_hdr; ws["A7"].alignment = Alignment(horizontal="center", vertical="center"); ws["A7"].fill = fill_hdr
    ws.merge_cells("B7:B8"); ws["B7"] = "Tên đối tượng / Diễn giải"; ws["B7"].font = font_tbl_hdr; ws["B7"].alignment = Alignment(horizontal="center", vertical="center"); ws["B7"].fill = fill_hdr
    ws.merge_cells("C7:C8"); ws["C7"] = "TK hạch toán"; ws["C7"].font = font_tbl_hdr; ws["C7"].alignment = Alignment(horizontal="center", vertical="center"); ws["C7"].fill = fill_hdr
    ws.merge_cells("D7:E7"); ws["D7"] = "Số dư đầu kỳ"; ws["D7"].font = font_tbl_hdr; ws["D7"].alignment = Alignment(horizontal="center"); ws["D7"].fill = fill_hdr
    ws.merge_cells("F7:G7"); ws["F7"] = "Số phát sinh"; ws["F7"].font = font_tbl_hdr; ws["F7"].alignment = Alignment(horizontal="center"); ws["F7"].fill = fill_hdr
    ws.merge_cells("H7:I7"); ws["H7"] = "Số dư cuối kỳ"; ws["H7"].font = font_tbl_hdr; ws["H7"].alignment = Alignment(horizontal="center"); ws["H7"].fill = fill_hdr

    ws["D8"] = "Nợ"; ws["D8"].font = font_tbl_hdr; ws["D8"].alignment = Alignment(horizontal="center"); ws["D8"].fill = fill_hdr
    ws["E8"] = "Có"; ws["E8"].font = font_tbl_hdr; ws["E8"].alignment = Alignment(horizontal="center"); ws["E8"].fill = fill_hdr
    ws["F8"] = "Nợ"; ws["F8"].font = font_tbl_hdr; ws["F8"].alignment = Alignment(horizontal="center"); ws["F8"].fill = fill_hdr
    ws["G8"] = "Có"; ws["G8"].font = font_tbl_hdr; ws["G8"].alignment = Alignment(horizontal="center"); ws["G8"].fill = fill_hdr
    ws["H8"] = "Nợ"; ws["H8"].font = font_tbl_hdr; ws["H8"].alignment = Alignment(horizontal="center"); ws["H8"].fill = fill_hdr
    ws["I8"] = "Có"; ws["I8"].font = font_tbl_hdr; ws["I8"].alignment = Alignment(horizontal="center"); ws["I8"].fill = fill_hdr

    for r in range(7, 9):
        for c in range(1, 10):
            ws.cell(row=r, column=c).border = border_thin

    curr = 9
    for it in data["items"]:
        ws.cell(row=curr, column=1, value=it["code"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr, column=2, value=it["name"]).alignment = Alignment(horizontal="left")
        ws.cell(row=curr, column=3, value=it["account_code"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr, column=4, value=it["open_dr"] if it["open_dr"] > 0 else "").number_format = "#,##0"
        ws.cell(row=curr, column=5, value=it["open_cr"] if it["open_cr"] > 0 else "").number_format = "#,##0"
        ws.cell(row=curr, column=6, value=it["ps_dr"] if it["ps_dr"] > 0 else "").number_format = "#,##0"
        ws.cell(row=curr, column=7, value=it["ps_cr"] if it["ps_cr"] > 0 else "").number_format = "#,##0"
        ws.cell(row=curr, column=8, value=it["close_dr"] if it["close_dr"] > 0 else "").number_format = "#,##0"
        ws.cell(row=curr, column=9, value=it["close_cr"] if it["close_cr"] > 0 else "").number_format = "#,##0"

        for c in range(1, 10):
            cell = ws.cell(row=curr, column=c)
            cell.font = font_row
            cell.border = border_thin
        curr += 1

    # Totals
    ws.merge_cells(f"A{curr}:C{curr}")
    ws.cell(row=curr, column=1, value=f"Tổng cộng ({data['totals']['count']} dòng)").alignment = Alignment(horizontal="center")
    t = data["totals"]
    ws.cell(row=curr, column=4, value=t["open_dr"] if t["open_dr"] > 0 else "").number_format = "#,##0"
    ws.cell(row=curr, column=5, value=t["open_cr"] if t["open_cr"] > 0 else "").number_format = "#,##0"
    ws.cell(row=curr, column=6, value=t["ps_dr"] if t["ps_dr"] > 0 else "").number_format = "#,##0"
    ws.cell(row=curr, column=7, value=t["ps_cr"] if t["ps_cr"] > 0 else "").number_format = "#,##0"
    ws.cell(row=curr, column=8, value=t["close_dr"] if t["close_dr"] > 0 else "").number_format = "#,##0"
    ws.cell(row=curr, column=9, value=t["close_cr"] if t["close_cr"] > 0 else "").number_format = "#,##0"
    for c in range(1, 10):
        cell = ws.cell(row=curr, column=c)
        cell.font = font_total
        cell.fill = fill_tot
        cell.border = border_thin

    out = BytesIO()
    wb.save(out)
    return out.getvalue()
