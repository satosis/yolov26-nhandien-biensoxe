"""Partner trade ledger router (Sổ mua/bán hàng với đối tác)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
import ast
import logging
import math
import operator
import re
from typing import Optional
from urllib.parse import quote, urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from frontend.deps import get_menu_context, require_menu, templates
from frontend.formatting.money import parse_vnd
from frontend.modules.enterprise import partner_trade_service
from frontend.modules.enterprise.active_company import get_active_company
from frontend.modules.invoices.share_link import public_base_url
from frontend.pagination import paginate_counts

logger = logging.getLogger(__name__)


_QTY_TOKEN = re.compile(r"\d[\d.,]*|[-+*/()xX×]")
_QTY_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _plain_number(token: str) -> str:
    """'2.764' -> '2764', '1,5' -> '1.5', '1.234,5' -> '1234.5'."""
    if "." in token and "," in token:
        if token.rfind(",") > token.rfind("."):
            return token.replace(".", "").replace(",", ".")
        return token.replace(",", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token):
        return token.replace(".", "")
    return token.replace(",", ".")


def _grouped_number(token: str) -> str:
    """'2764' -> '2.764', '1.5' -> '1,5'."""
    whole, _, frac = _plain_number(token).partition(".")
    return f"{int(whole):,}".replace(",", ".") + (f",{frac}" if frac else "")


def _eval_qty(node) -> Decimal:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return Decimal(str(node.value))
    if isinstance(node, ast.BinOp) and type(node.op) in _QTY_OPS:
        return _QTY_OPS[type(node.op)](_eval_qty(node.left), _eval_qty(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _QTY_OPS:
        return _QTY_OPS[type(node.op)](_eval_qty(node.operand))
    raise ValueError


def _parse_quantity(val: Optional[str]) -> tuple[Optional[Decimal], str]:
    """Số lượng gõ thẳng hoặc phép tính ('2.764-15', '1,5*2'): (kết quả, phép tính đã đặt dấu nghìn hoặc '')."""
    raw = re.sub(r"\s+", "", str(val or "")).lstrip("=")
    if not raw:
        return None, ""
    tokens = ["*" if t in "xX×" else t for t in _QTY_TOKEN.findall(raw)]
    if "".join(tokens) != raw.replace("x", "*").replace("X", "*").replace("×", "*"):
        raise ValueError("Số lượng không hợp lệ.")
    try:
        expr = "".join(_plain_number(t) if t[0].isdigit() else t for t in tokens)
        qty = _eval_qty(ast.parse(expr, mode="eval").body)
        qty = qty.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    except (SyntaxError, ValueError, ArithmeticError):
        raise ValueError("Số lượng không hợp lệ.")
    is_formula = any(not t[0].isdigit() for t in tokens[1:])
    formula = "".join(_grouped_number(t) if t[0].isdigit() else t for t in tokens) if is_formula else ""
    return qty, formula


def _parse_optional_vnd(val: Optional[str], err_field: str = "Số tiền") -> Optional[Decimal]:
    if val is None:
        return None
    raw = str(val).strip()
    if not raw:
        return None
    try:
        return parse_vnd(raw)
    except Exception:
        raise ValueError(f"{err_field} không hợp lệ.")


def _parse_opening_balance(val: Optional[str]) -> Decimal:
    raw = str(val or "").strip()
    if not raw:
        return Decimal(0)
    is_neg = raw.startswith("-")
    core = raw[1:].strip() if is_neg else raw
    if not core:
        return Decimal(0)
    try:
        amount = parse_vnd(core)
        return -amount if is_neg else amount
    except Exception:
        raise ValueError("Số dư đầu kỳ không hợp lệ.")


def _parse_entry_date(val: Optional[str]) -> date:
    raw = str(val or "").strip()
    if not raw:
        raise ValueError("Ngày không hợp lệ.")
    try:
        return date.fromisoformat(raw)
    except Exception:
        try:
            return datetime.strptime(raw, "%d/%m/%Y").date()
        except Exception:
            raise ValueError("Ngày không hợp lệ.")


def _ledger_labels(partner: Optional[dict]) -> tuple[str, str]:
    if partner:
        owe_label = (partner.get("owe_label") or "").strip() or f"{partner['name']} nợ"
        ledger_title = (partner.get("title") or "").strip() or partner["name"]
    else:
        owe_label = "Đối tác nợ"
        ledger_title = ""
    return owe_label, ledger_title


async def partner_trades_page(
    request: Request,
    partner: Optional[int],
    q: str,
    page: Optional[int],
    message: str,
    error: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else None

    partners = partner_trade_service.list_partners(cid) if cid else []

    selected_partner = None
    if partner and cid:
        selected_partner = partner_trade_service.get_partner(cid, partner)
    if not selected_partner:
        selected_partner = partner_trade_service.default_partner(partners)

    per_page = 100
    q_val = (q or "").strip()

    if selected_partner and cid:
        pid = selected_partner["id"]
        total = partner_trade_service.count_entries(cid, pid, q=q_val)
        last_page = max(1, math.ceil(total / per_page))
        # Không chọn trang thì mở trang cuối (dòng mới nhất nằm cuối sổ).
        current_page = last_page if page is None or page < 1 else min(page, last_page)
        rows, total, totals = partner_trade_service.ledger(
            cid, pid, q=q_val, page=current_page, per_page=per_page
        )
    else:
        rows = []
        total = 0
        totals = {"take": Decimal(0), "owe": Decimal(0), "balance": Decimal(0), "opening": Decimal(0)}
        current_page = 1
        pid = None

    extra_params = {}
    if pid is not None:
        extra_params["partner"] = pid
    if q_val:
        extra_params["q"] = q_val

    pagination = paginate_counts(
        total,
        page=current_page,
        per_page=per_page,
        base_url="/admin/enterprise/partner-trades",
        extra_params=extra_params,
    )

    buyers = partner_trade_service.list_buyers(cid) if cid else []
    stt_offset = (pagination["page"] - 1) * pagination["per_page"]

    owe_label, ledger_title = _ledger_labels(selected_partner)

    ctx["current_menu"] = "sales"
    ctx["company"] = company
    ctx["partners"] = partners
    ctx["partner"] = selected_partner
    ctx["rows"] = rows
    ctx["totals"] = totals
    ctx["pagination"] = pagination
    ctx["q"] = q_val
    ctx["buyers"] = buyers
    ctx["marks"] = partner_trade_service.ledger_marks(cid, selected_partner["id"]) if (cid and selected_partner) else dict(partner_trade_service.DEFAULT_MARKS)
    ctx["today_str"] = date.today().isoformat()
    ctx["message"] = message
    ctx["error"] = error
    ctx["stt_offset"] = stt_offset
    ctx["owe_label"] = owe_label
    ctx["ledger_title"] = ledger_title

    return templates.TemplateResponse("pages/enterprise/partner_trades.html", {"request": request, **ctx})


async def save_partner(
    request: Request,
    partner_id: str = Form(""),
    name: str = Form(""),
    opening_balance: str = Form(""),
    title: str = Form(""),
    owe_label: str = Form(""),
    is_default: str = Form(""),
    user: dict = Depends(require_menu("sales")),
):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else None
    if not cid:
        return RedirectResponse("/admin/enterprise/partner-trades?error=" + quote("Không tìm thấy công ty."), status_code=303)

    clean_name = name.strip()
    if not clean_name:
        return RedirectResponse("/admin/enterprise/partner-trades?error=" + quote("Vui lòng nhập tên đối tác."), status_code=303)

    pid = int(partner_id) if partner_id and partner_id.strip().isdigit() else None
    if pid:
        existing_partner = partner_trade_service.get_partner(cid, pid)
        if not existing_partner:
            return RedirectResponse("/admin/enterprise/partner-trades?error=" + quote("Không tìm thấy đối tác."), status_code=303)

    try:
        bal = _parse_opening_balance(opening_balance)
    except ValueError as e:
        redir = f"/admin/enterprise/partner-trades?{'partner=' + str(pid) + '&' if pid else ''}error=" + quote(str(e))
        return RedirectResponse(redir, status_code=303)

    try:
        saved = partner_trade_service.save_partner(
            cid, clean_name, bal, partner_id=pid, title=title, owe_label=owe_label, is_default=bool(is_default)
        )
        target_id = saved.get("id") or pid
        return RedirectResponse(f"/admin/enterprise/partner-trades?partner={target_id}&message=" + quote("Đã lưu đối tác."), status_code=303)
    except ValueError as e:
        redir = f"/admin/enterprise/partner-trades?{'partner=' + str(pid) + '&' if pid else ''}error=" + quote(str(e))
        return RedirectResponse(redir, status_code=303)


async def delete_partner(
    partner_id: int,
    request: Request,
    user: dict = Depends(require_menu("sales")),
):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else None
    if not cid:
        return RedirectResponse("/admin/enterprise/partner-trades?error=" + quote("Không tìm thấy công ty."), status_code=303)

    partner = partner_trade_service.get_partner(cid, partner_id)
    if not partner:
        return RedirectResponse("/admin/enterprise/partner-trades?error=" + quote("Không tìm thấy đối tác."), status_code=303)

    partner_trade_service.delete_partner(cid, partner_id)
    return RedirectResponse("/admin/enterprise/partner-trades?message=" + quote("Đã xóa đối tác."), status_code=303)


async def get_or_create_share_link(partner_id: int, request: Request, renew: str, user: dict):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else None
    if not cid:
        return JSONResponse({"ok": False, "message": "Không tìm thấy công ty."}, status_code=404)

    try:
        token = partner_trade_service.get_share_token(cid, partner_id, renew=bool(renew))
        return JSONResponse({"ok": True, "url": f"{public_base_url(request)}/so-doi-tac/{token}"})
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=404)


async def save_entry(
    request: Request,
    entry_id: str = Form(""),
    partner_id: int = Form(...),
    entry_date: str = Form(...),
    side: str = Form("take"),
    item_name: str = Form(""),
    buyer: str = Form(""),
    quantity: str = Form(""),
    unit_price: str = Form(""),
    amount: str = Form(""),
    note: str = Form(""),
    page: str = Form(""),
    q: str = Form(""),
    user: dict = Depends(require_menu("sales")),
):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else None
    if not cid:
        return RedirectResponse("/admin/enterprise/partner-trades?error=" + quote("Không tìm thấy công ty."), status_code=303)

    partner = partner_trade_service.get_partner(cid, partner_id)
    if not partner:
        return RedirectResponse("/admin/enterprise/partner-trades?error=" + quote("Không tìm thấy đối tác."), status_code=303)

    eid = int(entry_id) if entry_id and entry_id.strip().isdigit() else None

    def _error_redirect(err_msg: str) -> RedirectResponse:
        params = {"partner": partner_id}
        if eid and page:
            params["page"] = page
        if q:
            params["q"] = q
        params["error"] = err_msg
        return RedirectResponse(f"/admin/enterprise/partner-trades?{urlencode(params)}", status_code=303)

    try:
        parsed_date = _parse_entry_date(entry_date)
    except ValueError as e:
        return _error_redirect(str(e))

    try:
        qty_val, qty_formula = _parse_quantity(quantity)
    except ValueError as e:
        return _error_redirect(str(e))

    try:
        price_val = _parse_optional_vnd(unit_price, "Đơn giá")
    except ValueError as e:
        return _error_redirect(str(e))

    try:
        amount_val = _parse_optional_vnd(amount, "Số tiền")
    except ValueError as e:
        return _error_redirect(str(e))

    user_id = user.get("id") if isinstance(user, dict) else None

    entry_data = {
        "entry_date": parsed_date,
        "item_name": item_name.strip(),
        "buyer": buyer.strip(),
        "quantity": qty_val,
        "qty_formula": qty_formula,
        "note": note.strip(),
        "unit_price": price_val,
        "amount": amount_val,
        "side": side,
    }

    try:
        partner_trade_service.save_entry(cid, partner_id, entry_data, user_id=user_id, entry_id=eid)
    except ValueError as e:
        return _error_redirect(str(e))

    if eid:
        params = {"partner": partner_id}
        if page:
            params["page"] = page
        if q:
            params["q"] = q
        params["message"] = "Đã lưu."
        return RedirectResponse(f"/admin/enterprise/partner-trades?{urlencode(params)}", status_code=303)
    else:
        params = {"partner": partner_id, "message": "Đã lưu."}
        return RedirectResponse(f"/admin/enterprise/partner-trades?{urlencode(params)}", status_code=303)


async def delete_entry(
    entry_id: int,
    request: Request,
    partner_id: int = Form(...),
    page: str = Form(""),
    q: str = Form(""),
    user: dict = Depends(require_menu("sales")),
):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else None
    if not cid:
        return RedirectResponse("/admin/enterprise/partner-trades?error=" + quote("Không tìm thấy công ty."), status_code=303)

    partner = partner_trade_service.get_partner(cid, partner_id)
    if not partner:
        return RedirectResponse("/admin/enterprise/partner-trades?error=" + quote("Không tìm thấy đối tác."), status_code=303)

    partner_trade_service.delete_entry(cid, entry_id)

    params = {"partner": partner_id}
    if page:
        params["page"] = page
    if q:
        params["q"] = q
    params["message"] = "Đã xóa."
    return RedirectResponse(f"/admin/enterprise/partner-trades?{urlencode(params)}", status_code=303)


async def entry_detail_page(entry_id: int, request: Request, user: dict):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else None
    if not cid:
        raise HTTPException(status_code=404, detail="Không tìm thấy công ty.")

    entry = partner_trade_service.get_entry(cid, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Không tìm thấy dòng sổ.")

    partner = partner_trade_service.get_partner(cid, entry["partner_id"])
    if not partner:
        raise HTTPException(status_code=404, detail="Không tìm thấy đối tác.")

    lines = partner_trade_service.list_details(cid, entry_id)
    totals = partner_trade_service.detail_totals(lines)
    owe_label, ledger_title = _ledger_labels(partner)

    ctx["current_menu"] = "sales"
    ctx["company"] = company
    ctx["entry"] = entry
    ctx["partner"] = partner
    ctx["lines"] = lines
    ctx["totals"] = totals
    ctx["owe_label"] = owe_label
    ctx["ledger_title"] = ledger_title

    return templates.TemplateResponse(
        "pages/enterprise/partner_trade_detail.html",
        {"request": request, **ctx},
    )


async def save_entry_details(entry_id: int, request: Request, user: dict):
    ctx = get_menu_context(request, user)
    company = get_active_company(request, ctx)
    cid = company["id"] if company else None
    if not cid:
        return JSONResponse({"ok": False, "message": "Không tìm thấy công ty."}, status_code=400)

    entry = partner_trade_service.get_entry(cid, entry_id)
    if not entry:
        return JSONResponse({"ok": False, "message": "Không tìm thấy dòng sổ."}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "Dữ liệu JSON không hợp lệ."}, status_code=400)

    raw_lines = body.get("lines")
    if not isinstance(raw_lines, list):
        return JSONResponse({"ok": False, "message": "Danh sách dòng không hợp lệ."}, status_code=400)

    parsed_lines = []
    for idx, line in enumerate(raw_lines):
        if not isinstance(line, dict):
            continue
        kind = str(line.get("kind") or "item").strip().lower()
        if kind not in ("item", "plus", "minus"):
            kind = "item"
        item_name = str(line.get("item_name") or "").strip()
        raw_qty = line.get("quantity")
        raw_price = line.get("unit_price")
        raw_amt = line.get("amount")

        try:
            qty, qty_formula = _parse_quantity(raw_qty)
        except ValueError as e:
            return JSONResponse({"ok": False, "message": f"Dòng {idx+1}: {e}"}, status_code=400)

        try:
            unit_price = _parse_optional_vnd(raw_price, err_field="Đơn giá")
        except ValueError as e:
            return JSONResponse({"ok": False, "message": f"Dòng {idx+1}: {e}"}, status_code=400)

        try:
            amount = _parse_optional_vnd(raw_amt, err_field="Thành tiền")
        except ValueError as e:
            return JSONResponse({"ok": False, "message": f"Dòng {idx+1}: {e}"}, status_code=400)

        parsed_lines.append({
            "kind": kind,
            "item_name": item_name,
            "quantity": qty,
            "qty_formula": qty_formula,
            "unit_price": unit_price,
            "amount": amount,
        })

    try:
        totals = partner_trade_service.save_details(cid, entry_id, parsed_lines)
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)

    total_val = int(totals["total"]) if totals["total"] == int(totals["total"]) else float(totals["total"])
    return JSONResponse({"ok": True, "total": total_val})


async def public_partner_ledger(token: str, request: Request):
    partner = partner_trade_service.get_partner_by_share_token(token)
    if not partner:
        raise HTTPException(status_code=404, detail="Không tìm thấy sổ.")

    rows, _, totals = partner_trade_service.ledger(
        partner["company_id"], partner["id"], page=1, per_page=100000
    )
    owe_label, ledger_title = _ledger_labels(partner)

    return templates.TemplateResponse(
        "pages/enterprise/partner_trade_public.html",
        {
            "request": request,
            "token": token,
            "partner": partner,
            "rows": rows,
            "totals": totals,
            "owe_label": owe_label,
            "ledger_title": ledger_title,
        },
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


async def public_partner_entry_detail(token: str, entry_id: int, request: Request):
    partner = partner_trade_service.get_partner_by_share_token(token)
    if not partner:
        raise HTTPException(status_code=404, detail="Không tìm thấy sổ.")

    entry = partner_trade_service.get_entry(partner["company_id"], entry_id)
    if not entry or entry.get("partner_id") != partner["id"]:
        raise HTTPException(status_code=404, detail="Không tìm thấy dòng sổ.")

    lines = partner_trade_service.list_details(partner["company_id"], entry_id)
    totals = partner_trade_service.detail_totals(lines)
    owe_label, ledger_title = _ledger_labels(partner)

    return templates.TemplateResponse(
        "pages/enterprise/partner_trade_detail_public.html",
        {
            "request": request,
            "token": token,
            "partner": partner,
            "entry": entry,
            "lines": lines,
            "totals": totals,
            "owe_label": owe_label,
            "ledger_title": ledger_title,
        },
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )
