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
from decimal import Decimal
from io import BytesIO
import json
import os
from typing import Any

import openpyxl
from backend.services.database import db
from frontend.context.company_context import get_current_company_id

DAI_LO_XANH_COMPANY_ID = 33
SAMPLE_EXCEL_PATH = r"C:\Users\admin\Downloads\F01_DNN_Bang_can_doi_tai_khoan.xls"


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


def read_f01_dnn_sample(file_path: str = SAMPLE_EXCEL_PATH) -> dict[str, Any] | None:
    """Read sample F01-DNN Excel file from Downloads if present."""
    if not os.path.exists(file_path):
        return None
    try:
        import xlrd
        wb = xlrd.open_workbook(file_path)
        sheet = wb.sheet_by_index(0)

        company_name = str(sheet.cell_value(0, 0)).strip()
        address = str(sheet.cell_value(2, 0)).strip()
        period = str(sheet.cell_value(8, 0)).strip()

        sample_rows = []
        for r in range(15, sheet.nrows):
            code_val = str(sheet.cell_value(r, 0)).strip()
            if not code_val or code_val.lower().startswith("cộng") or code_val == "1/2":
                break
            code = code_val.replace(".0", "")
            name = str(sheet.cell_value(r, 1)).strip()
            open_dr = float(sheet.cell_value(r, 2) or 0)
            open_cr = float(sheet.cell_value(r, 3) or 0)
            ps_dr = float(sheet.cell_value(r, 4) or 0)
            ps_cr = float(sheet.cell_value(r, 5) or 0)
            close_dr = float(sheet.cell_value(r, 6) or 0)
            close_cr = float(sheet.cell_value(r, 7) or 0)

            sample_rows.append({
                "code": code,
                "name": name,
                "open_dr": open_dr,
                "open_cr": open_cr,
                "ps_dr": ps_dr,
                "ps_cr": ps_cr,
                "close_dr": close_dr,
                "close_cr": close_cr,
            })

        return {
            "file_path": file_path,
            "exists": True,
            "company_name": company_name,
            "address": address,
            "period": period,
            "total_accounts": len(sample_rows),
            "rows": sample_rows,
        }
    except Exception as e:
        return {
            "file_path": file_path,
            "exists": True,
            "error": str(e),
        }


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

        if tk.startswith("153"):
            total_cost_153 += sub
            count_inv_153 += cnt
        elif tk.startswith("642"):
            total_cost_642_inv += sub
        else:
            total_cost_154 += sub

    company = db._execute_query(
        "SELECT * FROM public.invoice_companies WHERE id = %s",
        (company_id,), fetch=True, commit=False,
    ) or {}
    company_name = company.get("name") or "CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH"
    director_name = company.get("director_name") or "Chủ sở hữu"

    # 3. Cash, banking & CCDC 242 flows
    from frontend.modules.transport.cash_bank_service import get_cash_book_data, get_bank_book_data
    from frontend.modules.transport.prepaid_expense_service import get_prepaid_expenses_data
    from frontend.modules.transport.depreciation_service import get_depreciation_data

    selected_year = year or (date_from.year if date_from else date.today().year)
    cash_data = get_cash_book_data(company_id=company_id, year=selected_year, date_from=date_from, date_to=date_to)
    bank_data = get_bank_book_data(company_id=company_id, year=selected_year, date_from=date_from, date_to=date_to)
    pe_data = get_prepaid_expenses_data(company_id=company_id, year=selected_year)
    pe_tot = pe_data["totals"]

    depr_data = get_depreciation_data(company_id=company_id, year=selected_year)
    total_original_211 = depr_data["totals"]["original_price"]
    total_accum_depr_214 = depr_data["totals"]["cumulative_depreciation"]

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

    supplier_paid_cash = int(round(sum(float(e.get("payment_amount") or 0) for e in cash_data.get("entries", []) if str(e.get("offset_account") or "").startswith("331") or str(e.get("offset_account") or "") in ["154", "642"])))
    supplier_paid_bank = int(round(sum(float(e.get("withdraw_amount") or 0) for e in bank_data.get("entries", []) if str(e.get("offset_account") or "").startswith("331") or str(e.get("offset_account") or "") in ["154", "642"])))
    total_supplier_paid = supplier_paid_cash + supplier_paid_bank

    prepaid_242_rem_start = pe_tot["rem_start"]
    prepaid_242_alloc = pe_tot["alloc_year"]
    prepaid_242_rem_end = pe_tot["rem_end"]
    other_admin_exp = 0

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
    db_toll = vexp_map.get("toll", 0) + vexp_map.get("bot", 0) + vexp_map.get("other", 0)

    pay_row = db._execute_query('''
        SELECT COALESCE(SUM(trip_wage + bonus_amount + overtime_pay), 0) as total_driver_wage
        FROM public.staff_payroll_months m
        JOIN public.users u ON u.id = m.user_id
        JOIN public.invoice_companies c ON c.id = ANY(u.company_ids)
        WHERE c.id = %s AND m.month >= %s AND m.month <= %s
    ''', (company_id, date_from, date_to), fetch=True, commit=False) or {}
    db_driver = int(round(float(pay_row.get("total_driver_wage") or 0)))

    if total_cost_154 > 0:
        cost_fuel_1541 = db_fuel
        cost_driver_1542 = db_driver
        cost_repair_1543 = db_repair
        allocated = cost_fuel_1541 + cost_driver_1542 + cost_repair_1543
        if allocated <= total_cost_154:
            cost_other_1547 = total_cost_154 - allocated
        else:
            ratio = float(total_cost_154) / float(allocated)
            cost_fuel_1541 = int(round(cost_fuel_1541 * ratio))
            cost_driver_1542 = int(round(cost_driver_1542 * ratio))
            cost_repair_1543 = int(round(cost_repair_1543 * ratio))
            cost_other_1547 = total_cost_154 - (cost_fuel_1541 + cost_driver_1542 + cost_repair_1543)
    else:
        cost_fuel_1541 = 0
        cost_driver_1542 = 0
        cost_repair_1543 = 0
        cost_other_1547 = 0

    # 3b. Phân bổ Tài sản cố định 211 theo ngày phát sinh (đầu kỳ vs trong kỳ)
    orig_211_open = 0
    orig_211_ps = 0
    sub_211 = []
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
            "code": "2113",
            "name": f"Phương tiện vận tải: {r.get('asset_code') or r.get('plate_number') or r.get('name') or 'Xe tải'}",
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

    prev_capital_rows = db._execute_query('''
        SELECT 
            COALESCE(SUM(CASE WHEN voucher_type = 'receipt' THEN amount ELSE 0 END), 0) as prev_cr,
            COALESCE(SUM(CASE WHEN voucher_type = 'payment' THEN amount ELSE 0 END), 0) as prev_dr
        FROM public.transport_cash_book
        WHERE company_id = %s 
          AND (offset_account ILIKE %s OR description ILIKE %s OR description ILIKE %s)
          AND entry_date < %s
    ''', (company_id, '%411%', '%góp vốn%', '%VĐL%', date_from), fetch=True, commit=False) or {}
    prev_cap_cr = int(round(float(prev_capital_rows.get("prev_cr") or 0)))
    prev_cap_dr = int(round(float(prev_capital_rows.get("prev_dr") or 0)))

    initial_capital = cash_open + bank_open + orig_211_open + prepaid_242_rem_start
    open_cr_411 = initial_capital + prev_cap_cr - prev_cap_dr
    cap_ps_cr = int(round(sum(float(r["amount"] or 0) for r in capital_rows if r["voucher_type"] == "receipt")))
    cap_ps_dr = int(round(sum(float(r["amount"] or 0) for r in capital_rows if r["voucher_type"] == "payment")))
    close_cr_411 = open_cr_411 + cap_ps_cr - cap_ps_dr

    partner_cap = {}
    partner_cap[director_name] = {
        "name": f"Vốn góp của {director_name} (Chủ sở hữu)",
        "open_cr": open_cr_411,
        "ps_dr": 0,
        "ps_cr": 0,
        "close_cr": open_cr_411,
    }
    def _norm_str(s: str) -> str:
        import unicodedata
        return unicodedata.normalize("NFC", str(s or "").strip().lower())

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

    if orig_211_open > 0 or orig_211_ps > 0:
        sub_411.append({
            "code": "4111",
            "name": f"Vốn góp bằng TSCĐ ({len(depr_data.get('rows', []))} xe tải)",
            "open_dr": 0,
            "open_cr": orig_211_open,
            "ps_dr": 0,
            "ps_cr": orig_211_ps,
            "close_dr": 0,
            "close_cr": orig_211_open + orig_211_ps,
            "source_note": "Tài sản cố định góp vốn ban đầu",
            "source_url": "/admin/transport/depreciation",
        })

    total_cap_open_cr = open_cr_411 + orig_211_open
    total_cap_ps_cr = cap_ps_cr + orig_211_ps
    total_cap_ps_dr = cap_ps_dr
    total_cap_close_cr = total_cap_open_cr + total_cap_ps_cr - total_cap_ps_dr

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
            "source_note": "Bảng kê 175 HĐ đầu vào",
            "source_url": "/admin/transport/input-invoices",
            "sub_accounts": [
                {
                    "code": "1331",
                    "name": "Thuế GTGT được khấu trừ của HHDV (175 HĐ mua vào)",
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
                    "source_note": "Bảng lương lái xe tải",
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
            "ps_cr": 0,
            "close_dr": total_cost_153,
            "close_cr": 0,
            "source_note": f"Hóa đơn mua CCDC ({count_inv_153} HĐ)",
            "source_url": "/admin/transport/input-invoices",
            "sub_accounts": [
                {
                    "code": "1531",
                    "name": "Công cụ, dụng cụ (Quả chuẩn 500kg, thiết bị)",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": total_cost_153, "ps_cr": 0,
                    "close_dr": total_cost_153, "close_cr": 0,
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
            "open_cr": 0,
            "ps_dr": 0,
            "ps_cr": total_accum_depr_214,
            "close_dr": 0,
            "close_cr": total_accum_depr_214,
            "source_note": "Bảng tính khấu hao TSCĐ",
            "source_url": "/admin/transport/depreciation",
            "sub_accounts": [
                {
                    "code": "2141",
                    "name": f"Hao mòn TSCĐ hữu hình: {r.get('plate_number') or r.get('name') or 'Xe tải'}",
                    "open_dr": 0,
                    "open_cr": 0,
                    "ps_dr": 0,
                    "ps_cr": int(r.get("cumulative_depreciation") or 0),
                    "close_dr": 0,
                    "close_cr": int(r.get("cumulative_depreciation") or 0),
                    "source_note": f"Khấu hao lũy kế {r.get('plate_number') or ''}".strip(),
                    "source_url": "/admin/transport/depreciation",
                }
                for r in depr_data.get("rows", [])
            ]
        },
        {
            "code": "242",
            "name": "Chi phí trả trước",
            "open_dr": prepaid_242_rem_start,
            "open_cr": 0,
            "ps_dr": 0,
            "ps_cr": prepaid_242_alloc,
            "close_dr": prepaid_242_rem_end,
            "close_cr": 0,
            "source_note": "Bảng phân bổ CCDC (quản lý quả cân, máy tính...)",
            "source_url": "/admin/transport/prepaid-expenses",
            "sub_accounts": [
                {
                    "code": "2421",
                    "name": "CCDC phân bổ (quả cân, máy tính, máy in, điều hòa, tủ lạnh...)",
                    "open_dr": prepaid_242_rem_start, "open_cr": 0,
                    "ps_dr": 0, "ps_cr": prepaid_242_alloc,
                    "close_dr": prepaid_242_rem_end, "close_cr": 0,
                    "source_note": "Sổ phân bổ CCDC Phương Nam",
                    "source_url": "/admin/transport/prepaid-expenses",
                }
            ]
        },
        {
            "code": "331",
            "name": "Phải trả cho người bán",
            "open_dr": 0,
            "open_cr": 0,
            "ps_dr": total_supplier_paid,
            "ps_cr": total_payable_331,
            "close_dr": max(0, total_supplier_paid - total_payable_331),
            "close_cr": max(0, total_payable_331 - total_supplier_paid),
            "source_note": f"Công nợ {count_inv_in} HĐ mua vào",
            "source_url": "/admin/transport/input-invoices",
            "sub_accounts": [
                {
                    "code": "3311",
                    "name": f"Phải trả người bán ngắn hạn ({count_inv_in} HĐ mua vào)",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": total_supplier_paid, "ps_cr": total_payable_331,
                    "close_dr": max(0, total_supplier_paid - total_payable_331), "close_cr": max(0, total_payable_331 - total_supplier_paid),
                    "source_note": "Sổ chi tiết công nợ nhà cung cấp",
                    "source_url": "/admin/transport/input-invoices",
                }
            ]
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
            "ps_dr": (abs(total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) < 0 else 0),
            "ps_cr": ((total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) >= 0 else 0),
            "close_dr": (abs(total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) < 0 else 0),
            "close_cr": ((total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) >= 0 else 0),
            "source_note": "Kết chuyển kết quả kinh doanh",
            "source_url": None,
            "sub_accounts": [
                {
                    "code": "4212",
                    "name": "Lợi nhuận sau thuế chưa phân phối năm nay",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": (abs(total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) < 0 else 0),
                    "ps_cr": ((total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) >= 0 else 0),
                    "close_dr": (abs(total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) < 0 else 0),
                    "close_cr": ((total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) if (total_revenue_511 - (total_cost_154 + total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv)) >= 0 else 0),
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
            "ps_dr": total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv,
            "ps_cr": total_accum_depr_214 + prepaid_242_alloc + other_admin_exp + total_cost_642_inv,
            "close_dr": 0,
            "close_cr": 0,
            "source_note": "Khấu hao, quản lý, phân bổ (K/C sang 911)",
            "source_url": "/admin/transport/depreciation",
            "sub_accounts": [
                {
                    "code": "6422",
                    "name": f"Chi phí khấu hao phương tiện vận tải ({len(depr_data.get('rows', []))} xe tải)",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": total_accum_depr_214, "ps_cr": total_accum_depr_214,
                    "close_dr": 0, "close_cr": 0,
                    "source_note": "Từ Bảng tính khấu hao TSCĐ (K/C sang 911)",
                    "source_url": "/admin/transport/depreciation",
                },
                {
                    "code": "6422",
                    "name": "Chi phí phân bổ CCDC, bảo hiểm xe",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": prepaid_242_alloc, "ps_cr": prepaid_242_alloc,
                    "close_dr": 0, "close_cr": 0,
                    "source_note": "Phân bổ từ TK 242 (K/C sang 911)",
                    "source_url": None,
                },
                {
                    "code": "6428",
                    "name": "Chi phí quản lý điều hành & dịch vụ mua ngoài (CKS BKAV...)",
                    "open_dr": 0, "open_cr": 0,
                    "ps_dr": other_admin_exp + total_cost_642_inv, "ps_cr": other_admin_exp + total_cost_642_inv,
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

    # Áp dụng Tùy chỉnh (Sửa tài khoản / Thêm tài khoản theo yêu cầu người dùng)
    ovs = get_tb_overrides()
    custom_accs = get_tb_custom_accounts()

    # 1. Áp dụng Overrides cho các tài khoản hiện có
    if ovs:
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
            for s in p.get("sub_accounts", []):
                s_code = s["code"]
                if s_code in ovs:
                    if "name" in ovs[s_code]:
                        s["name"] = ovs[s_code]["name"]
                    if "open_dr" in ovs[s_code]:
                        s["open_dr"] = ovs[s_code]["open_dr"]
                    if "open_cr" in ovs[s_code]:
                        s["open_cr"] = ovs[s_code]["open_cr"]
                    net_s = (s["open_dr"] - s["open_cr"]) + (s["ps_dr"] - s["ps_cr"])
                    s["close_dr"] = max(0, net_s)
                    s["close_cr"] = max(0, -net_s)

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
            }
            flattened_rows.append(s_row)

    sample_info = read_f01_dnn_sample()

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
        "sample_info": sample_info,
        "is_balanced": (
            totals["open_dr"] == totals["open_cr"]
            and totals["ps_dr"] == totals["ps_cr"]
            and totals["close_dr"] == totals["close_cr"]
        ),
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
    rows_to_export = data["all_rows"] if include_sub_accounts else [r for r in data["all_rows"] if r["is_parent"]]
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

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


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
    is_parent = account_row.get("is_parent", False) if account_row else True

    director_name = tb_data.get("director_name") or "Chủ sở hữu"
    tax_code = tb_data.get("tax_code") or ""

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

    if code_clean in ("131", "1311"):
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

    elif code_clean in ("331", "3311"):
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

    elif code_clean.startswith("133") or code_clean.startswith("333"):
        title = "BẢNG KÊ THUẾ GIÁ TRỊ GIA TĂNG (GTGT)"
        external_link = "/admin/transport/tax-report"
        external_label = "Mở Báo cáo thuế theo quý"
        row_133 = next((p for p in tb_data.get("parent_rows", []) if p["code"] == "133"), {})
        row_333 = next((p for p in tb_data.get("parent_rows", []) if p["code"] == "333"), {})
        items = [
            {"code": "1331", "name": f"Thuế GTGT đầu vào được khấu trừ của HHDV mua vào ({count_inv_in} HĐ đầu vào)", "account_code": "1331", "open_dr": 0, "open_cr": 0, "ps_dr": row_133.get("ps_dr", 0), "ps_cr": 0, "close_dr": row_133.get("close_dr", 0), "close_cr": 0, "inv_count": f"{count_inv_in} HĐ"},
            {"code": "33311", "name": f"Thuế GTGT đầu ra phải nộp của dịch vụ vận tải ({count_inv_out} HĐ bán ra)", "account_code": "33311", "open_dr": 0, "open_cr": 0, "ps_dr": row_333.get("ps_dr", 0), "ps_cr": row_333.get("ps_cr", 0), "close_dr": 0, "close_cr": row_333.get("close_cr", 0), "inv_count": f"{count_inv_out} HĐ"},
        ]

    elif code_clean.startswith("411"):
        title = "SỔ THEO DÕI VỐN GÓP CHỦ SỞ HỮU"
        external_link = "/admin/transport/cash-book"
        external_label = "Mở Sổ quỹ tiền mặt (Lập phiếu góp vốn)"
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

        initial_cap = int(round(account_row.get("open_cr", 0) if account_row else 0))

        partner_cap = {}
        partner_cap[director_name] = {
            "name": f"Vốn góp của {director_name} (Chủ sở hữu)",
            "open_cr": initial_cap,
            "ps_dr": 0,
            "ps_cr": 0,
            "close_cr": initial_cap,
        }
        def _norm_str(s: str) -> str:
            import unicodedata
            return unicodedata.normalize("NFC", str(s or "").strip().lower())

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

            transactions.append({
                "date": r["entry_date"].strftime("%d/%m/%Y") if r["entry_date"] else "",
                "number": r["voucher_no"] or "—",
                "serial": r["voucher_date"].strftime("%d/%m/%Y") if r["voucher_date"] else "",
                "partner": pname,
                "description": r["description"] or "Góp vốn điều lệ",
                "debit_acc": "1111" if r["voucher_type"] == "receipt" else (r["offset_account"] or "4111"),
                "credit_acc": (r["offset_account"] or "4111") if r["voucher_type"] == "receipt" else "1111",
                "amount": amt,
                "status": "Góp vốn" if r["voucher_type"] == "receipt" else "Rút vốn",
                "link": "/admin/transport/cash-book",
            })

        items = []
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
