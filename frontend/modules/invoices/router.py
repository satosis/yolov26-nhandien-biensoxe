import logging
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from backend.services.database import db
from frontend.deps import get_menu_context, require_menu, templates
from frontend.pagination import paginate_counts
from frontend.modules.invoices.companies import get_company, list_companies, save_company
from frontend.modules.invoices.extract import extract_invoice_text, parse_invoice_text
from frontend.modules.invoices.service import (
    ALLOWED_FILES, MAX_FILE_BYTES, get_invoice, invoice_list_stats, list_invoices, parse_invoice, save_invoice,
)
from frontend.modules.transport.date_utils import month_bounds
from frontend.modules.invoices.gmail_sync import (
    build_gmail_oauth_url,
    exchange_gmail_code_for_tokens,
    get_connected_gmail_email,
    get_company_by_enterprise_email,
    get_gmail_refresh_token,
    get_google_client_id,
    save_gmail_tokens,
    sync_invoices_from_gmail_api,
    sync_invoices_from_imap,
)
from frontend.modules.invoices.saveinvoice_sync import (
    get_saveinvoice_config,
    import_saveinvoice_invoices,
    list_saveinvoice_accounts,
    preview_saveinvoice_invoices,
    refresh_saveinvoice_tokens,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transport/input-invoices")


async def _read_attachment(upload):
    filename = str(upload.filename).replace("\\", "/").split("/")[-1][:255]
    content_type = upload.content_type
    suffixes = {"application/pdf": (".pdf",), "image/jpeg": (".jpg", ".jpeg"),
                "image/png": (".png",)}
    if content_type not in suffixes or not filename.lower().endswith(suffixes[content_type]):
        raise ValueError("Chỉ nhận PDF, JPG hoặc PNG.")
    content = await upload.read(MAX_FILE_BYTES + 1)
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError("Tệp phải nhỏ hơn 10 MB.")
    signatures = {"application/pdf": (b"%PDF-",), "image/jpeg": (b"\xff\xd8\xff",),
                  "image/png": (b"\x89PNG\r\n\x1a\n",)}
    if not any(content.startswith(signature) for signature in signatures[content_type]):
        raise ValueError("Nội dung tệp không đúng định dạng.")
    return filename, content_type, content


def _page(request, user, month, edit=None, error=None, form=None, show_form=False):
    start, end = month_bounds(month)
    ctx = get_menu_context(request, user)
    active_company = ctx.get("active_company")
    active_cid = ctx.get("active_company_id")

    # Kiểm tra doanh nghiệp khớp theo Email đã liên kết Gmail
    connected_email = get_connected_gmail_email()
    email_comp = get_company_by_enterprise_email(connected_email) if connected_email else None

    # Support company filter: specific company id or 'all'
    company_filter = str(request.query_params.get("company_id") or "").strip()
    if company_filter == "all":
        target_cid = None
    elif company_filter.isdigit():
        target_cid = int(company_filter)
    else:
        # Nếu chưa lọc thủ công và công ty hiện tại chưa có hóa đơn,
        # tự động chọn công ty khớp với Email liên kết Gmail của doanh nghiệp
        if email_comp and (not active_cid or active_cid != email_comp["id"]):
            curr_stats = invoice_list_stats(start, end, buyer_company_id=active_cid) if active_cid else {}
            if not curr_stats.get("total") or int(curr_stats.get("total")) == 0:
                target_cid = email_comp["id"]
                active_cid = email_comp["id"]
                active_company = email_comp
                request.session["active_company_id"] = email_comp["id"]
            else:
                target_cid = active_cid
        else:
            target_cid = active_cid

    if active_company:
        buyer = {
            "name": active_company.get("name", ""),
            "tax_code": active_company.get("tax_code", ""),
            "address": active_company.get("address", "")
        }
    else:
        buyer = {}
    q = str(request.query_params.get("q") or "").strip()
    page = int(request.query_params.get("page", 1) or 1)
    stats = invoice_list_stats(start, end, buyer_company_id=target_cid, q=q)

    # Detect if another company has invoices if current target_cid has none
    other_company_info = None
    if target_cid and (not stats.get("total") or int(stats.get("total")) == 0) and not q:
        other_row = db._execute_query("""
            SELECT c.id, c.name, COUNT(i.id) as count, COALESCE(SUM(i.total), 0) as total_amount
            FROM public.transport_input_invoices i
            JOIN public.invoice_companies c ON c.id = i.buyer_company_id
            WHERE i.invoice_date >= %s AND i.invoice_date < %s AND i.buyer_company_id <> %s
            GROUP BY c.id, c.name
            ORDER BY count DESC
            LIMIT 1
        """, (start, end, target_cid), fetch=True, commit=False)
        if other_row:
            other_company_info = other_row

    extra_params = {"month": month}
    if company_filter:
        extra_params["company_id"] = company_filter
    if q:
        extra_params["q"] = q
    raw_per_page = request.query_params.get("per_page")
    try:
        per_page = int(raw_per_page) if raw_per_page and int(raw_per_page) > 0 else 100
    except Exception:
        per_page = 100
    if raw_per_page and raw_per_page != "100":
        extra_params["per_page"] = str(per_page)
    paginated = paginate_counts(
        int(stats.get("total") or 0), page=page, per_page=per_page,
        base_url="/admin/transport/input-invoices",
        extra_params=extra_params,
    )
    rows = list_invoices(
        start, end, buyer_company_id=target_cid,
        limit=paginated["per_page"],
        offset=(paginated["page"] - 1) * paginated["per_page"],
        q=q,
    )
    companies = list_companies()
    values = form if form is not None else edit or {}
    if form is not None:
        lines = [dict(description=d, unit=u, quantity=q, unit_price=p)
                 for d, u, q, p in zip(form.getlist("description"), form.getlist("unit"),
                                       form.getlist("quantity"), form.getlist("unit_price"))]
    else:
        lines = edit["items"] if edit else []
    gmail_email = get_connected_gmail_email()
    gmail_connected = bool(get_gmail_refresh_token())
    gmail_oauth_url = ""
    try:
        base = str(request.base_url).rstrip("/")
        redirect_uri = f"{base}/admin/transport/input-invoices/gmail/callback"
        gmail_oauth_url = build_gmail_oauth_url(redirect_uri)
    except Exception:
        pass
    ctx.update(current_menu="vehicles", transport_section="input_invoices", month=month,
               invoices=rows,
               pagination=paginated,
               page=paginated["page"],
               total_pages=paginated["total_pages"],
               total=paginated["total"],
               per_page=paginated["per_page"],
               page_base_url=paginated["page_base_url"],
               stats=stats,
               total_subtotal=stats.get("total_subtotal") or 0,
               total_vat=stats.get("total_vat") or 0,
               total_amount=stats.get("total_amount") or 0,
               buyer=buyer, companies=companies, edit=edit, error=error, values=values,
               today=date.today().isoformat(),
               lines=lines or [dict(description="", unit="", quantity="", unit_price="")],
               saved=request.query_params.get("saved") == "1",
               deleted=request.query_params.get("deleted") == "1",
               gmail_connected=request.query_params.get("gmail_connected") == "1",
               gmail_email=request.query_params.get("email") or gmail_email,
               gmail_is_connected=gmail_connected,
               gmail_oauth_url=gmail_oauth_url,
               gmail_error=request.query_params.get("gmail_error"),
               show_form=show_form,
               company_filter=company_filter,
               q=q,
               target_company_id=target_cid,
               matched_company=email_comp,
               other_company_info=other_company_info)
    return templates.TemplateResponse("pages/input_invoices.html", {"request": request, **ctx})


@router.get("")
async def input_invoices_page(request: Request, month: str | None = None,
                              edit: int | None = None,
                              user: dict = Depends(require_menu("vehicles"))):
    month = month or date.today().strftime("%Y-%m")
    try:
        month_bounds(month)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    if edit:
        return RedirectResponse(f"/admin/transport/input-invoices/{edit}/edit?month={quote(month)}",
                                status_code=303)
    return _page(request, user, month)


@router.get("/add")
async def add_input_invoice_page(request: Request, month: str | None = None,
                                 user: dict = Depends(require_menu("vehicles"))):
    month = month or date.today().strftime("%Y-%m")
    try:
        month_bounds(month)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    return _page(request, user, month, show_form=True)


@router.get("/{invoice_id}/edit")
async def edit_input_invoice_page(request: Request, invoice_id: int,
                                  month: str | None = None,
                                  user: dict = Depends(require_menu("vehicles"))):
    month = month or date.today().strftime("%Y-%m")
    try:
        month_bounds(month)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    invoice = get_invoice(invoice_id)
    if not invoice:
        return HTMLResponse("Không tìm thấy hóa đơn.", status_code=404)
    return _page(request, user, month, edit=invoice, show_form=True)


@router.post("/extract")
async def extract_input_invoice(request: Request, _: dict = Depends(require_menu("vehicles"))):
    form = await request.form()
    upload = form.get("source_file")
    if not upload or not getattr(upload, "filename", ""):
        return JSONResponse({"error": "Vui lòng chọn tệp hóa đơn."}, status_code=400)
    try:
        _, content_type, content = await _read_attachment(upload)
        details = parse_invoice_text(extract_invoice_text(content, content_type))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception:
        return JSONResponse({"error": "Không đọc được hóa đơn. Vui lòng nhập thông tin thủ công."}, status_code=422)
    seller_tax = details.get("seller", {}).get("tax_code")
    if seller_tax:
        company = db._execute_query("SELECT id FROM public.invoice_companies WHERE tax_code = %s LIMIT 1",
                                    (seller_tax,), fetch=True, commit=False)
        if company:
            details["seller"]["company_id"] = company["id"]
    buyer_tax = details.get("buyer", {}).get("tax_code")
    if buyer_tax:
        buyer_company = db._execute_query(
            "SELECT name, address FROM public.invoice_companies WHERE tax_code = %s LIMIT 1",
            (buyer_tax,), fetch=True, commit=False)
        if buyer_company:
            details["buyer"]["name"] = buyer_company["name"]
            details["buyer"]["address"] = buyer_company["address"] or ""
    return JSONResponse(details)


@router.post("")
async def save_input_invoice(request: Request, user: dict = Depends(require_menu("vehicles"))):
    form = await request.form()
    month = str(form.get("month", ""))
    try:
        month_bounds(month)
        invoice_id = int(str(form.get("invoice_id") or "0"))
        if invoice_id < 0 or (invoice_id and not get_invoice(invoice_id)):
            raise ValueError("Không tìm thấy hóa đơn.")
        seller_id = int(str(form.get("seller_company_id") or "0"))
        seller = get_company(seller_id) if seller_id > 0 else None
        is_new_seller = not seller and str(form.get("seller_new") or "") == "1"
        if not seller and not is_new_seller:
            raise ValueError("Vui lòng chọn đơn vị bán từ Danh mục đơn vị.")
        values = parse_invoice(form)
        attachment = None
        upload = form.get("source_file")
        if upload and getattr(upload, "filename", ""):
            attachment = await _read_attachment(upload)
        if is_new_seller:
            seller = save_company(str(form.get("seller_tax_code") or ""),
                                  str(form.get("seller_name") or ""),
                                  str(form.get("seller_address") or ""))
        active_cid = request.session.get("active_company_id")
        values.update(seller_company_id=seller["id"], seller_name=seller["name"],
                      seller_tax_code=seller["tax_code"] or "",
                      seller_address=seller["address"] or "",
                      buyer_company_id=active_cid)
        save_invoice(values, invoice_id or None, attachment)
    except (ValueError, TypeError) as exc:
        try:
            view_month = month if month else date.today().strftime("%Y-%m")
            month_bounds(view_month)
        except ValueError:
            view_month = date.today().strftime("%Y-%m")
        existing = get_invoice(invoice_id) if 'invoice_id' in locals() and invoice_id > 0 else None
        return _page(request, user, view_month, edit=existing, error=str(exc),
                     form=form, show_form=True)
    return RedirectResponse(f"/admin/transport/input-invoices?month={quote(values['invoice_date'].strftime('%Y-%m'))}&saved=1",
                            status_code=303)


@router.get("/{invoice_id}/file")
async def download_input_invoice(invoice_id: int, _: dict = Depends(require_menu("vehicles"))):
    row = get_invoice(invoice_id)
    if not row or not row["source_data"]:
        return HTMLResponse("Không tìm thấy tệp hóa đơn.", status_code=404)
    suffix = ALLOWED_FILES.get(row["source_type"], ".bin")
    return Response(bytes(row["source_data"]), media_type=row["source_type"],
                    headers={"Content-Disposition": f'attachment; filename="hoa-don-dau-vao-{invoice_id}{suffix}"',
                             "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.post("/{invoice_id}/delete")
async def delete_input_invoice(invoice_id: int, month: str = Form(...),
                               _: dict = Depends(require_menu("vehicles"))):
    try:
        month_bounds(month)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    deleted = db._execute_query("DELETE FROM public.transport_input_invoices WHERE id = %s RETURNING id",
                                (invoice_id,), fetch=True)
    if not deleted:
        return HTMLResponse("Không tìm thấy hóa đơn.", status_code=404)
    return RedirectResponse(f"/admin/transport/input-invoices?month={quote(month)}&deleted=1", status_code=303)


# ---------------------------------------------------------------------------
# Gmail & Email Invoice Synchronization Endpoints
# ---------------------------------------------------------------------------

@router.get("/gmail/status")
async def gmail_status_api(request: Request, user: dict = Depends(require_menu("vehicles"))):
    client_id = get_google_client_id()
    refresh_token = get_gmail_refresh_token()
    connected_email = get_connected_gmail_email()
    base = str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/admin/transport/input-invoices/gmail/callback"
    auth_url = ""
    if client_id:
        try:
            auth_url = build_gmail_oauth_url(redirect_uri)
        except Exception:
            pass

    ctx = get_menu_context(request, user)
    user_comps = ctx.get("user_companies") or []
    matched_c = get_company_by_enterprise_email(connected_email) if connected_email else None

    return JSONResponse({
        "client_id_configured": bool(client_id),
        "connected": bool(refresh_token),
        "email": connected_email,
        "matched_company": matched_c,
        "active_company_id": ctx.get("active_company_id"),
        "user_companies": [{"id": c["id"], "name": c["name"], "tax_code": c.get("tax_code") or ""} for c in user_comps],
        "auth_url": auth_url,
        "redirect_uri": redirect_uri,
        "enable_url": "https://console.cloud.google.com/apis/library/gmail.googleapis.com?project=686766750544",
        "consent_url": "https://console.cloud.google.com/apis/credentials/consent?project=686766750544",
        "credentials_url": "https://console.cloud.google.com/apis/credentials?project=686766750544",
    })


@router.get("/gmail/auth")
async def gmail_auth_redirect(request: Request, _: dict = Depends(require_menu("vehicles"))):
    base = str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/admin/transport/input-invoices/gmail/callback"
    try:
        url = build_gmail_oauth_url(redirect_uri)
        return RedirectResponse(url, status_code=303)
    except Exception as exc:
        return HTMLResponse(f"Lỗi khởi tạo đăng nhập Google: {exc}", status_code=400)


@router.get("/gmail/callback")
async def gmail_oauth_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    user: dict = Depends(require_menu("vehicles")),
):
    if error:
        return RedirectResponse(f"/admin/transport/input-invoices?gmail_error={quote(error)}", status_code=303)
    if not code:
        return RedirectResponse("/admin/transport/input-invoices?gmail_error=No+code", status_code=303)

    base = str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/admin/transport/input-invoices/gmail/callback"
    try:
        res = await exchange_gmail_code_for_tokens(code, redirect_uri)
        email_str = res.get("email", "")
        # Tự động gán và chọn công ty làm việc theo Email doanh nghiệp đã liên kết
        matched_c = get_company_by_enterprise_email(email_str) if email_str else None
        if matched_c:
            request.session["active_company_id"] = matched_c["id"]
            logger.info("Gmail OAuth: Tự động chuyển công ty %s (id: %s) theo email %s",
                        matched_c["name"], matched_c["id"], email_str)
        return RedirectResponse(f"/admin/transport/input-invoices?gmail_connected=1&email={quote(email_str)}", status_code=303)
    except Exception as exc:
        logger.error("Gmail OAuth callback failed: %s", exc)
        return RedirectResponse(f"/admin/transport/input-invoices?gmail_error={quote(str(exc))}", status_code=303)


@router.post("/gmail/disconnect")
async def gmail_disconnect_api(_: dict = Depends(require_menu("vehicles"))):
    save_gmail_tokens("", "", "")
    return JSONResponse({"ok": True, "message": "Đã ngắt kết nối Gmail."})


@router.post("/gmail/sync")
async def gmail_sync_api(
    request: Request,
    max_messages: int = Form(40),
    keyword: str = Form(""),
    target_company_id: int | None = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    connected_email = get_connected_gmail_email()
    matched_comp = get_company_by_enterprise_email(connected_email) if connected_email else None
    buyer_company_id = target_company_id or (matched_comp["id"] if matched_comp else None) or request.session.get("active_company_id")

    if matched_comp and not target_company_id:
        request.session["active_company_id"] = matched_comp["id"]

    try:
        res = await sync_invoices_from_gmail_api(
            max_messages=max_messages,
            search_query=keyword,
            buyer_company_id=buyer_company_id,
        )
        return JSONResponse(res)
    except Exception as exc:
        logger.error("Gmail sync failed: %s", exc)
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)


@router.post("/imap/sync")
async def imap_sync_api(
    request: Request,
    email_address: str = Form(...),
    app_password: str = Form(...),
    max_messages: int = Form(30),
    keyword: str = Form(""),
    target_company_id: int | None = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    matched_comp = get_company_by_enterprise_email(email_address) if email_address else None
    buyer_company_id = target_company_id or (matched_comp["id"] if matched_comp else None) or request.session.get("active_company_id")

    if matched_comp and not target_company_id:
        request.session["active_company_id"] = matched_comp["id"]

    try:
        res = sync_invoices_from_imap(
            email_address=email_address,
            app_password=app_password,
            max_messages=max_messages,
            search_keyword=keyword,
            buyer_company_id=buyer_company_id,
        )
        return JSONResponse(res)
    except Exception as exc:
        logger.error("IMAP sync failed: %s", exc)
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)


# ---------------------------------------------------------------------------
# SaveInvoice (Tổng cục Thuế) Synchronization Endpoints
# ---------------------------------------------------------------------------

@router.get("/saveinvoice/status")
async def saveinvoice_status_api(request: Request, user: dict = Depends(require_menu("vehicles"))):
    ctx = get_menu_context(request, user)
    active_company = ctx.get("active_company") or {}
    active_tax = (active_company.get("tax_code") or "").strip()
    active_name = active_company.get("name") or ""
    active_cid = ctx.get("active_company_id")

    cfg = get_saveinvoice_config()
    accounts = list_saveinvoice_accounts(target_tax=active_tax)

    # Resolve target account matching the active company
    matched_acc = next((a for a in accounts if a["tax_code"].strip() == active_tax), None) if active_tax else None
    if not matched_acc and accounts:
        matched_acc = next((a for a in accounts if a["is_target"]), accounts[0])

    target_acc_id = matched_acc["account_id"] if matched_acc else ""
    target_tax_code = active_tax or (matched_acc["tax_code"] if matched_acc else "")

    return JSONResponse({
        "ok": True,
        "username": cfg["username"],
        "active_company_id": active_cid,
        "active_company_name": active_name,
        "active_tax_code": active_tax,
        "target_tax_code": target_tax_code,
        "target_account_id": target_acc_id,
        "has_session": bool(cfg["access_token"]),
        "accounts": accounts,
    })


@router.post("/saveinvoice/preview")
async def saveinvoice_preview_api(
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    active_company = ctx.get("active_company") or {}
    active_tax = (active_company.get("tax_code") or "").strip()
    active_cid = ctx.get("active_company_id")

    try:
        body = await request.json()
    except Exception:
        body = {}
    account_id = body.get("account_id")
    month = body.get("month")
    limit = int(body.get("limit") or 50)
    res = preview_saveinvoice_invoices(
        account_id=account_id,
        month=month,
        limit=limit,
        buyer_company_id=active_cid,
        buyer_tax_code=active_tax,
    )
    return JSONResponse(res)


@router.post("/saveinvoice/import")
async def saveinvoice_import_api(
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    active_cid = ctx.get("active_company_id") or request.session.get("active_company_id")

    try:
        body = await request.json()
    except Exception:
        body = {}
    invoice_ids = body.get("invoice_ids") or []
    account_id = body.get("account_id")
    res = import_saveinvoice_invoices(
        invoice_ids=invoice_ids,
        account_id=account_id,
        buyer_company_id=active_cid,
    )
    return JSONResponse(res)


@router.post("/saveinvoice/refresh")
async def saveinvoice_refresh_api(user: dict = Depends(require_menu("vehicles"))):
    success = refresh_saveinvoice_tokens()
    return JSONResponse({"ok": success, "message": "Làm mới phiên thành công" if success else "Không thể làm mới phiên SaveInvoice."})

