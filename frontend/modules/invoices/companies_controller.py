"""Company / unit management routes under transport catalog."""

from decimal import Decimal
import logging
from pathlib import Path
import re
from typing import Optional
import urllib.parse
import uuid
from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from backend.services import config as cfg
from frontend.deps import get_current_company_id, get_menu_context, templates
from frontend.pagination import paginate_counts
from frontend.formatting.http_headers import content_disposition
from frontend.formatting.money import format_vnd_rounded as _fmt_currency
from frontend.redirects import redirect_with_query
from frontend.modules.invoices.companies import (
    TaxLookupError,
    count_companies,
    delete_company,
    get_company,
    get_company_bank_accounts,
    list_bank_names,
    list_business_activities,
    list_companies_page,
    normalize_tax_code,
    refresh_company_from_tax,
    replace_company_bank_accounts,
    save_company,
    update_company,
    validate_company_bank_accounts,
)
from frontend.modules.invoices.debt_service import (
    adjust_company_debt,
    get_company_debt_logs,
    delete_company_debt_log,
    get_company_debt,
)
from frontend.modules.invoices.company_images import (
    transparent_company_image,
)
from frontend.modules.transport.contract import (
    _remove_accents,
    get_contract_context,
    render_contract_html,
    render_contract_pdf,
    render_contract_docx,
)
import frontend.modules.invoices.companies_queries as queries

logger = logging.getLogger(__name__)


UPLOAD_DIR = Path(cfg.BASE_DIR) / "upload"
COMPANIES_UPLOAD_DIR = UPLOAD_DIR / "companies"
COMPANIES_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


async def _save_company_image(upload_file: UploadFile | None, prefix: str) -> str | None:
    if not upload_file or not upload_file.filename:
        return None
    content = await upload_file.read()
    if not content:
        raise ValueError("Ảnh chữ ký hoặc con dấu rỗng.")
    processed = await run_in_threadpool(
        transparent_company_image, content, stamp=prefix.startswith("stamp")
    )
    safe_name = f"{prefix}_{uuid.uuid4().hex[:10]}.png"
    target_path = COMPANIES_UPLOAD_DIR / safe_name
    target_path.write_bytes(processed)
    return f"/upload/companies/{safe_name}"


def _remove_unused_company_image(url: str | None) -> None:
    """Delete a local company image from storage only after no company references it."""
    if not url:
        return
    clean_url = str(url).split("?", 1)[0].strip().replace("\\", "/")
    if not re.fullmatch(
        r"/?upload/(?:companies/)?[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:png|jpe?g|webp|gif)",
        clean_url, re.IGNORECASE,
    ):
        return
    filename = Path(clean_url).name
    referenced = queries.remove_unused_company_image_select_invoice_companies(filename)
    if not referenced:
        for base_dir in (COMPANIES_UPLOAD_DIR, UPLOAD_DIR):
            target = base_dir / filename
            if target.is_file():
                target.unlink(missing_ok=True)
                logger.info("Deleted company asset from storage: %s", target)


async def _bank_accounts_from_form(request: Request) -> list[dict]:
    form = await request.form()
    numbers = form.getlist("bank_account[]")
    names = form.getlist("bank_name[]")
    branches = form.getlist("bank_branch[]")
    if not numbers and (form.get("bank_account") or form.get("bank_name")):
        numbers = [form.get("bank_account", "")]
        names = [form.get("bank_name", "")]
        branches = [""]
    if not (len(numbers) == len(names) == len(branches)):
        raise ValueError("Danh sách tài khoản ngân hàng không hợp lệ.")
    return [dict(account_number=number, bank_name=name, branch=branch)
            for number, name, branch in zip(numbers, names, branches)
            if number.strip() or name.strip() or branch.strip()]


def _catalog_redirect(tab: str, back: str = "", message: str = "", error: str = "") -> RedirectResponse:
    target = back if back.startswith("/") else "/admin/companies"
    return redirect_with_query(target, tab=tab if "catalog" in target else "", message=message, error=error)


def _add_form_lists(ctx: dict) -> None:
    """The lists the company add and edit forms offer: accounts for the director, bank names."""
    ctx["users_list"] = queries.add_form_lists_select_users() or []
    ctx["bank_names"] = list_bank_names()


async def companies_management_page(
    request: Request,
    back: str,
    page: int,
    q: str,
    debt: bool,
    message: str,
    error: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "companies"
    ctx["back_url"] = back or "/admin/companies"
    ctx["message"] = message
    ctx["error"] = error
    ctx["q"] = q
    ctx["debt_only"] = debt
    active_cid = ctx.get("active_company_id")

    paginated = paginate_counts(
        count_companies(q, debt_only=debt, seller_company_id=active_cid), page=page, per_page=100,
        base_url="/admin/companies",
        extra_params={"back": back, "q": q.strip(), "debt": "true" if debt else None},
    )
    companies = list_companies_page(
        q, limit=100, offset=(paginated["page"] - 1) * 100,
        debt_only=debt, seller_company_id=active_cid,
    )
    company_ids = [company["id"] for company in companies]
    trip_counts = queries.companies_management_page_select_cargo_trips(active_cid, company_ids) or [] if active_cid and company_ids else []
    trip_map = {r["company_id"]: r["cnt"] for r in trip_counts}

    for company in companies:
        company["trip_count"] = trip_map.get(company["id"], 0)
        debt_val = Decimal(str(company.get("current_debt") or 0))
        company["total_debt"] = float(debt_val)
        company["total_debt_fmt"] = _fmt_currency(debt_val)
        company["debt_trip_count"] = 0

    total_companies_count = count_companies("", debt_only=False, seller_company_id=active_cid)
    debt_company_count = count_companies("", debt_only=True, seller_company_id=active_cid)
    debt_sum_row = queries.companies_management_page_select_company_debt_accounts(active_cid) or {}
    total_debt_all = Decimal(str(debt_sum_row.get("total_debt_all", 0)))

    ctx["companies"] = companies
    ctx["page"] = paginated["page"]
    ctx["total_pages"] = paginated["total_pages"]
    ctx["total"] = paginated["total"]
    ctx["total_companies_count"] = total_companies_count
    ctx["debt_company_count"] = debt_company_count
    ctx["total_debt_all_fmt"] = _fmt_currency(total_debt_all)

    return templates.TemplateResponse("pages/companies/index.html", {"request": request, **ctx})


async def add_company_page(
    request: Request,
    tax_code: str,
    partner_type: str,
    back: str,
    message: str,
    error: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    ctx.update(
        back_url=back or "/admin/transport/catalog?tab=companies",
        message=message,
        error=error,
        tax_code=tax_code.strip(),
        partner_type=partner_type if partner_type in ("individual", "organization") else "organization",
    )
    _add_form_lists(ctx)
    ctx["business_activities"] = list_business_activities()
    return templates.TemplateResponse("pages/companies/add.html", {"request": request, **ctx})


async def create_catalog_company(
    request: Request,
    name: str,
    tax_code: str,
    address: str,
    email: str,
    phone: str,
    bank_account: str,
    bank_name: str,
    business_activity: str,
    director_user_id: str,
    partner_type: str,
    signature_file: Optional[UploadFile],
    stamp_file: Optional[UploadFile],
    back: str,
    _: dict,
):
    signature_url = stamp_url = None
    try:
        bank_accounts = await _bank_accounts_from_form(request)
        validate_company_bank_accounts(bank_accounts)
        p_type = "individual" if partner_type in ("individual", "cá nhân", "ca_nhan") else "organization"
        clean_code = tax_code.strip() if tax_code else ""
        code = None
        if p_type == "individual":
            if clean_code:
                code = normalize_tax_code(clean_code)
                exists = queries.select_company_by_tax_code(code)
                if exists:
                    raise ValueError("Mã số thuế đã có trong danh mục.")
        else:
            if not clean_code:
                raise ValueError("Mã số thuế không được để trống đối với Tổ chức.")
            code = normalize_tax_code(clean_code)
            exists = queries.select_company_by_tax_code(code)
            if exists:
                raise ValueError("Mã số thuế đã có trong danh mục.")
        signature_url = await _save_company_image(signature_file, "signature")
        stamp_url = await _save_company_image(stamp_file, "stamp")
        first = bank_accounts[0] if bank_accounts else {}
        saved = save_company(code, name, address, email, phone=phone,
                             bank_account=first.get("account_number", ""),
                             bank_name=first.get("bank_name", ""), director_user_id=director_user_id,
                             signature_url=signature_url,
                             stamp_url=stamp_url,
                             business_activity=business_activity,
                             partner_type=p_type)
        replace_company_bank_accounts(saved["id"], bank_accounts)
    except ValueError as exc:
        return RedirectResponse(
            f"/admin/transport/catalog/companies/add?back={urllib.parse.quote(back, safe='')}&error={urllib.parse.quote(str(exc), safe='')}&partner_type={partner_type}",
            status_code=303,
        )
    finally:
        _remove_unused_company_image(signature_url)
        _remove_unused_company_image(stamp_url)
    return _catalog_redirect("companies", back, message="Đã thêm đơn vị.")


async def edit_company_page(
    company_id: int,
    request: Request,
    back: str,
    message: str,
    error: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    company = get_company(company_id)
    if not company:
        return _catalog_redirect("companies", back, error="Không tìm thấy đơn vị.")
    ctx.update(
        company=company,
        back_url=back or "/admin/transport/catalog?tab=companies",
        message=message,
        error=error,
    )
    _add_form_lists(ctx)
    ctx["bank_accounts"] = get_company_bank_accounts(company_id)
    ctx["business_activities"] = list_business_activities()
    return templates.TemplateResponse("pages/companies/edit.html", {"request": request, **ctx})


async def edit_catalog_company(
    company_id: int,
    request: Request,
    name: str,
    tax_code: str,
    address: str,
    email: str,
    phone: str,
    bank_account: str,
    bank_name: str,
    business_activity: str,
    director_user_id: str,
    partner_type: str,
    signature_file: Optional[UploadFile],
    stamp_file: Optional[UploadFile],
    remove_signature: Optional[str],
    remove_stamp: Optional[str],
    back: str,
    _: dict,
):
    previous = get_company(company_id)
    signature_url = stamp_url = None
    try:
        bank_accounts = await _bank_accounts_from_form(request)
        validate_company_bank_accounts(bank_accounts)
        signature_url = await _save_company_image(signature_file, "signature")
        stamp_url = await _save_company_image(stamp_file, "stamp")
        first = bank_accounts[0] if bank_accounts else {}
        update_company(company_id, tax_code, name, address, email, phone=phone,
                       bank_account=first.get("account_number", ""),
                       bank_name=first.get("bank_name", ""), director_user_id=director_user_id,
                       signature_url=signature_url,
                       stamp_url=stamp_url,
                       remove_signature=bool(remove_signature),
                       remove_stamp=bool(remove_stamp),
                       business_activity=business_activity,
                       partner_type=partner_type)
        replace_company_bank_accounts(company_id, bank_accounts)
    except ValueError as exc:
        return RedirectResponse(
            f"/admin/transport/catalog/companies/{company_id}/edit?back={urllib.parse.quote(back, safe='')}&error={urllib.parse.quote(str(exc), safe='')}",
            status_code=303,
        )
    finally:
        _remove_unused_company_image(signature_url)
        _remove_unused_company_image(stamp_url)
        if previous:
            _remove_unused_company_image(previous.get("signature_url"))
            _remove_unused_company_image(previous.get("stamp_url"))
    return _catalog_redirect("companies", back, message="Đã cập nhật đơn vị.")


async def refresh_catalog_company(company_id: int, back: str, _: dict):
    try:
        updated = await refresh_company_from_tax(company_id)
    except (ValueError, TaxLookupError) as exc:
        return _catalog_redirect("companies", back, error=str(exc))
    message = "Đã cập nhật thông tin đơn vị và đồng bộ giám đốc từ MaSoThue."
    if updated.get("director_user_created"):
        message += " Đã tạo tài khoản người dùng mới."
    return _catalog_redirect("companies", back, message=message)


async def remove_catalog_company(request: Request, company_id: int, back: str, _: dict):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    previous = get_company(company_id)
    try:
        deleted = delete_company(company_id)
    except ValueError as exc:
        if is_ajax:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        return _catalog_redirect("companies", back, error=str(exc))
    if not deleted:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Không tìm thấy đơn vị."}, status_code=404)
        return _catalog_redirect("companies", back, error="Không tìm thấy đơn vị.")
    if previous:
        _remove_unused_company_image(previous.get("signature_url"))
        _remove_unused_company_image(previous.get("stamp_url"))
    if is_ajax:
        return JSONResponse({"ok": True, "message": "Đã xóa đơn vị."})
    return _catalog_redirect("companies", back, message="Đã xóa đơn vị.")


async def bulk_delete_catalog_companies(request: Request, _: dict):
    ids = []
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            ids = [int(x) for x in body.get("ids", []) if str(x).isdigit()]
        except Exception:
            ids = []
    else:
        form = await request.form()
        raw_ids = form.getlist("ids") or form.getlist("company_ids")
        ids = [int(x) for x in raw_ids if str(x).isdigit()]

    if not ids:
        return JSONResponse({"ok": False, "message": "Vui lòng chọn ít nhất một đơn vị để xóa."}, status_code=400)

    deleted_count = 0
    failed = []
    for cid in dict.fromkeys(ids):
        comp = get_company(cid)
        cname = comp.get("name") if comp else f"ID {cid}"
        try:
            if delete_company(cid):
                deleted_count += 1
            else:
                failed.append({"id": cid, "name": cname, "reason": "Không tìm thấy đơn vị."})
        except ValueError as exc:
            failed.append({"id": cid, "name": cname, "reason": str(exc)})
        except Exception as exc:
            failed.append({"id": cid, "name": cname, "reason": f"Lỗi: {exc}"})

    msg_parts = []
    if deleted_count > 0:
        msg_parts.append(f"Đã xóa thành công {deleted_count} đơn vị.")
    if failed:
        reasons = [f"• {item['name']}: {item['reason']}" for item in failed[:5]]
        if len(failed) > 5:
            reasons.append(f"...và {len(failed) - 5} đơn vị khác.")
        msg_parts.append(f"Có {len(failed)} đơn vị không thể xóa:\n" + "\n".join(reasons))

    final_message = "\n".join(msg_parts) if msg_parts else "Không có đơn vị nào được xóa."
    return JSONResponse({
        "ok": deleted_count > 0,
        "deleted_count": deleted_count,
        "failed_count": len(failed),
        "failed": failed,
        "message": final_message,
    })


async def bulk_refresh_catalog_companies(request: Request, _: dict):
    ids = []
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            ids = [int(x) for x in body.get("ids", []) if str(x).isdigit()]
        except Exception:
            ids = []
    else:
        form = await request.form()
        raw_ids = form.getlist("ids") or form.getlist("company_ids")
        ids = [int(x) for x in raw_ids if str(x).isdigit()]

    if not ids:
        return JSONResponse({"ok": False, "message": "Vui lòng chọn ít nhất một đơn vị để cập nhật."}, status_code=400)

    success_count = 0
    created_users_count = 0
    failed = []

    for cid in dict.fromkeys(ids):
        comp = get_company(cid)
        cname = comp.get("name") if comp else f"ID {cid}"
        try:
            updated = await refresh_company_from_tax(cid)
            success_count += 1
            if updated.get("director_user_created"):
                created_users_count += 1
        except (ValueError, TaxLookupError) as exc:
            failed.append({"id": cid, "name": cname, "reason": str(exc)})
        except Exception as exc:
            failed.append({"id": cid, "name": cname, "reason": f"Lỗi kết nối: {exc}"})

    msg_parts = []
    if success_count > 0:
        txt = f"Đã cập nhật thành công {success_count} đơn vị từ MST."
        if created_users_count > 0:
            txt += f" Đã tự động tạo {created_users_count} tài khoản giám đốc mới."
        msg_parts.append(txt)
    if failed:
        reasons = [f"• {item['name']}: {item['reason']}" for item in failed[:5]]
        if len(failed) > 5:
            reasons.append(f"...và {len(failed) - 5} đơn vị khác.")
        msg_parts.append(f"Có {len(failed)} đơn vị cập nhật thất bại:\n" + "\n".join(reasons))

    final_message = "\n".join(msg_parts) if msg_parts else "Không có đơn vị nào được cập nhật."
    return JSONResponse({
        "ok": success_count > 0,
        "success_count": success_count,
        "failed_count": len(failed),
        "failed": failed,
        "message": final_message,
    })


def _sync_decrease_trip_debts(company_id: int, owner_company_id: int, payment_amount: Decimal) -> None:
    """Best-effort update of individual cargo_trips and bkav_invoices if any exist."""
    trips = queries.sync_decrease_trip_debts_select_cargo_trips(company_id, owner_company_id) or []
    remaining = payment_amount
    for t in trips:
        if remaining <= 0:
            break
        trip_debt = Decimal(str(t["debt_amount"]))
        trip_id = t["id"]
        table = "public.bkav_invoices" if t["source_type"] == "invoice" else "public.cargo_trips"
        key = "invoice_guid" if t["source_type"] == "invoice" else "id"
        if remaining >= trip_debt:
            queries.sync_decrease_trip_debts_update_set(trip_id, owner_company_id, table, key, )
            remaining -= trip_debt
        else:
            new_trip_debt = trip_debt - remaining
            queries.sync_decrease_trip_debts_update_set_2(new_trip_debt, trip_id, owner_company_id, table, key)
            break


def pay_company_debt(company_id: int, payment_amount: Decimal, note: str = "", recorded_date: str = "", reference_doc: str = "") -> tuple[bool, str, Decimal]:
    owner_company_id = get_current_company_id()
    if payment_amount <= 0:
        return False, "Số tiền thanh toán phải lớn hơn 0.", Decimal("0")
    ok, msg, new_balance = adjust_company_debt(
        company_id=company_id,
        amount=-payment_amount,
        debt_type="payment",
        note=note.strip() or "Thanh toán trừ nợ",
        reference_doc=reference_doc.strip() if reference_doc else "",
        recorded_date=recorded_date or None,
    )
    if ok and owner_company_id:
        _sync_decrease_trip_debts(company_id, owner_company_id, payment_amount)
    return ok, msg, new_balance


async def pay_company_debt_endpoint(
    request: Request,
    company_id: int,
    amount: str,
    note: str,
    recorded_date: str,
    reference_doc: str,
    back: str,
    user: dict,
):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    company = get_company(company_id)
    if not company:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Không tìm thấy đơn vị."}, status_code=404)
        return _catalog_redirect("companies", back, error="Không tìm thấy đơn vị.")

    cleaned = re.sub(r"[^\d]", "", amount or "")
    if not cleaned:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Vui lòng nhập số tiền thanh toán hợp lệ."}, status_code=400)
        return _catalog_redirect("companies", back, error="Vui lòng nhập số tiền thanh toán hợp lệ.")

    payment_amount = Decimal(cleaned)
    if payment_amount <= 0:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Số tiền thanh toán phải lớn hơn 0."}, status_code=400)
        return _catalog_redirect("companies", back, error="Số tiền thanh toán phải lớn hơn 0.")

    ok, msg, new_balance = pay_company_debt(company_id, payment_amount, note=note, recorded_date=recorded_date, reference_doc=reference_doc)
    if not ok:
        if is_ajax:
            return JSONResponse({"ok": False, "message": msg}, status_code=400)
        return _catalog_redirect("companies", back, error=msg)

    success_msg = f"Đã thanh toán trừ nợ {_fmt_currency(payment_amount)}. Dư nợ còn lại: {_fmt_currency(new_balance)}."
    if is_ajax:
        return JSONResponse({
            "ok": True,
            "message": success_msg,
            "new_balance": float(new_balance),
            "new_balance_fmt": _fmt_currency(new_balance),
        })
    return _catalog_redirect("companies", back, message=success_msg)


async def add_company_debt_endpoint(
    request: Request,
    company_id: int,
    amount: str,
    note: str,
    recorded_date: str,
    reference_doc: str,
    back: str,
    user: dict,
):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    company = get_company(company_id)
    if not company:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Không tìm thấy đơn vị."}, status_code=404)
        return _catalog_redirect("companies", back, error="Không tìm thấy đơn vị.")

    cleaned = re.sub(r"[^\d]", "", amount or "")
    if not cleaned:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Vui lòng nhập số tiền nợ hợp lệ."}, status_code=400)
        return _catalog_redirect("companies", back, error="Vui lòng nhập số tiền nợ hợp lệ.")

    debt_amount = Decimal(cleaned)
    if debt_amount <= 0:
        if is_ajax:
            return JSONResponse({"ok": False, "message": "Số tiền nợ phải lớn hơn 0."}, status_code=400)
        return _catalog_redirect("companies", back, error="Số tiền nợ phải lớn hơn 0.")

    ok, msg, new_balance = adjust_company_debt(
        company_id=company_id,
        amount=debt_amount,
        debt_type="manual_add",
        note=note.strip() or "Ghi nhận nợ ngoài",
        reference_doc=reference_doc.strip(),
        recorded_date=recorded_date or None,
        user_id=user.get("id"),
    )
    if not ok:
        if is_ajax:
            return JSONResponse({"ok": False, "message": msg}, status_code=400)
        return _catalog_redirect("companies", back, error=msg)

    success_msg = f"Đã ghi thêm nợ {_fmt_currency(debt_amount)} cho {company['name']}. Dư nợ hiện tại: {_fmt_currency(new_balance)}."
    if is_ajax:
        return JSONResponse({
            "ok": True,
            "message": success_msg,
            "new_balance": float(new_balance),
            "new_balance_fmt": _fmt_currency(new_balance),
        })
    return _catalog_redirect("companies", back, message=success_msg)


async def get_company_debts_endpoint(company_id: int, request: Request, user: dict):
    company = get_company(company_id)
    if not company:
        return JSONResponse({"ok": False, "message": "Không tìm thấy đơn vị."}, status_code=404)
    logs = get_company_debt_logs(company_id)
    curr_debt = get_company_debt(company_id)
    return JSONResponse({
        "ok": True,
        "company": {
            "id": company["id"],
            "name": company["name"],
            "tax_code": company.get("tax_code") or "",
            "current_debt": float(curr_debt),
            "current_debt_fmt": _fmt_currency(curr_debt),
        },
        "logs": logs,
    })


async def delete_company_debt_log_endpoint(log_id: int, request: Request, user: dict):
    ok, msg, new_balance = delete_company_debt_log(log_id)
    if not ok:
        return JSONResponse({"ok": False, "message": msg}, status_code=400)
    return JSONResponse({
        "ok": True,
        "message": msg,
        "new_balance": float(new_balance),
        "new_balance_fmt": _fmt_currency(new_balance),
    })


async def upload_company_signature(company_id: int, file: UploadFile, _: dict):
    """Upload signature image for a company and save to upload/companies."""
    previous = get_company(company_id)
    url = None
    try:
        url = await _save_company_image(file, f"sig_{company_id}")
        if not url:
            return JSONResponse({"success": False, "error": "Tệp tin ảnh không hợp lệ hoặc rỗng."}, status_code=400)
        updated = queries.upload_company_signature_update_invoice_companies(url, company_id)
        if not updated:
            return JSONResponse({"success": False, "error": "Không tìm thấy đơn vị."}, status_code=404)
        return JSONResponse({"success": True, "url": url, "company_id": company_id})
    except ValueError as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=422)
    finally:
        _remove_unused_company_image(url)
        if previous:
            _remove_unused_company_image(previous.get("signature_url"))


async def upload_company_stamp(company_id: int, file: UploadFile, _: dict):
    """Upload stamp image for a company and save to upload/companies."""
    previous = get_company(company_id)
    url = None
    try:
        url = await _save_company_image(file, f"stamp_{company_id}")
        if not url:
            return JSONResponse({"success": False, "error": "Tệp tin ảnh không hợp lệ hoặc rỗng."}, status_code=400)
        updated = queries.upload_company_stamp_update_invoice_companies(url, company_id)
        if not updated:
            return JSONResponse({"success": False, "error": "Không tìm thấy đơn vị."}, status_code=404)
        return JSONResponse({"success": True, "url": url, "company_id": company_id})
    except ValueError as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=422)
    finally:
        _remove_unused_company_image(url)
        if previous:
            _remove_unused_company_image(previous.get("stamp_url"))


async def remove_company_signature(company_id: int, _: dict):
    """Remove signature image of a company."""
    previous = get_company(company_id)
    queries.remove_company_signature_update_invoice_companies(company_id)
    if previous:
        _remove_unused_company_image(previous.get("signature_url"))
    return JSONResponse({"success": True, "company_id": company_id})


async def remove_company_stamp(company_id: int, _: dict):
    """Remove stamp image of a company."""
    previous = get_company(company_id)
    queries.remove_company_stamp_update_invoice_companies(company_id)
    if previous:
        _remove_unused_company_image(previous.get("stamp_url"))
    return JSONResponse({"success": True, "company_id": company_id})


async def export_company_contract(
    company_id: int,
    request: Request,
    download: str | None,
    format: str | None,
    view: str | None,
    contract_date: str | None,
    contract_no: str | None,
    back: str,
    user: dict,
):
    company = get_company(company_id)
    if not company:
        return _catalog_redirect("companies", back, error="Không tìm thấy đơn vị.")

    active_cid = get_current_company_id() or 33
    seller = get_company(int(active_cid))
    if not seller:
        msg = "Không tìm thấy thông tin công ty đang làm việc trong hệ thống."
        if view == "html" or (download or format or "").lower() == "html":
            return HTMLResponse(f"<h3>{msg}</h3>", status_code=400)
        return _catalog_redirect("companies", back, error=msg)

    company["bank_accounts"] = get_company_bank_accounts(company_id)
    seller["bank_accounts"] = get_company_bank_accounts(seller["id"])
    ctx = get_contract_context(
        customer_or_trip=company,
        seller=seller,
        contract_date=contract_date,
        contract_no=contract_no,
    )

    req_format = (download or format or "").lower()
    if view == "html" or req_format == "html":
        html_content = render_contract_html(ctx, preview=True)
        return HTMLResponse(html_content)

    safe_no = re.sub(r"[^A-Za-z0-9_-]", "_", _remove_accents(ctx["contract_no"]))
    if req_format == "docx":
        docx_bytes = render_contract_docx(ctx)
        filename = f"Hop_dong_{safe_no}.docx"
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": content_disposition(filename)},
        )

    # Default: PDF
    pdf_bytes = render_contract_pdf(ctx)
    filename = f"Hop_dong_{safe_no}.pdf"
    disposition = "attachment" if download == "pdf" else "inline"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(filename, disposition)},
    )

