"""Router for managing external service and transport portal accounts (VETC, ePass, fuel, registry, GPS)."""

from decimal import Decimal
import logging
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from frontend.deps import get_menu_context, require_menu, templates
from frontend.pagination import paginate_counts
from frontend.modules.service_accounts.service import (
    CATEGORIES,
    PORTAL_PRESETS,
    delete_service_account,
    get_available_plates,
    get_service_account,
    list_service_accounts,
    count_service_accounts,
    service_account_stats,
    save_service_account,
    update_account_balance,
)

logger = logging.getLogger("service_accounts_router")
router = APIRouter(prefix="/service-accounts")


@router.get("")
async def service_accounts_page(
    request: Request,
    category: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    edit_id: Optional[int] = None,
    user: dict = Depends(require_menu("service_accounts")),
):
    ctx = get_menu_context(request, user)
    active_cid = ctx.get("active_company_id")

    total_filtered = count_service_accounts(category=category, search=search, status=status, company_id=active_cid)
    paginated = paginate_counts(
        total_filtered, page=page, per_page=20,
        base_url="/admin/service-accounts",
        extra_params={"category": category, "search": search, "status": status},
    )
    accounts = list_service_accounts(
        category=category, search=search, status=status, company_id=active_cid,
        limit=paginated["per_page"], offset=(paginated["page"] - 1) * paginated["per_page"],
    )
    stats = service_account_stats(company_id=active_cid)

    edit_account = get_service_account(edit_id) if edit_id else None
    available_plates = get_available_plates()

    ctx.update(
        current_menu="service_accounts",
        transport_section="service_accounts",
        accounts=accounts,
        page=paginated["page"],
        total_pages=paginated["total_pages"],
        total=paginated["total"],
        page_base_url=paginated["page_base_url"],
        per_page=paginated["per_page"],
        categories=CATEGORIES,
        presets=PORTAL_PRESETS,
        available_plates=available_plates,
        active_category=category or "",
        search_query=search or "",
        active_status=status or "",
        edit_account=edit_account,
        stats={
            "total_count": stats.get("total_count", 0),
            "active_count": stats.get("active_count", 0),
            "toll_count": stats.get("toll_count", 0),
            "fuel_count": stats.get("fuel_count", 0),
            "total_balance": Decimal(str(stats.get("total_balance") or "0")),
        },
        saved=request.query_params.get("saved") == "1",
        deleted=request.query_params.get("deleted") == "1",
    )
    return templates.TemplateResponse("pages/service_accounts.html", {"request": request, **ctx})


@router.post("")
async def save_account_endpoint(
    request: Request,
    portal_url: str = Form(...),
    account_username: str = Form(...),
    account_password: str = Form(...),
    account_id: Optional[int] = Form(None),
    service_name: Optional[str] = Form(""),
    category: Optional[str] = Form("other"),
    note: Optional[str] = Form(""),
    user: dict = Depends(require_menu("service_accounts")),
):
    active_cid = request.session.get("active_company_id")
    data = {
        "service_name": (service_name or "").strip(),
        "category": (category or "other").strip(),
        "portal_url": portal_url.strip(),
        "account_username": account_username.strip(),
        "account_password": account_password.strip(),
        "note": (note or "").strip(),
        "company_id": active_cid,
    }
    try:
        saved_id = save_service_account(data, account_id=account_id)
        logger.info("Saved service account ID %d by user %s", saved_id, user.get("username"))
        return RedirectResponse("/admin/service-accounts?saved=1", status_code=303)
    except Exception as exc:
        logger.warning("Error saving service account: %s", exc)
        ctx = get_menu_context(request, user)
        accounts = list_service_accounts(company_id=active_cid)
        all_accounts = accounts
        ctx.update(
            current_menu="service_accounts",
            transport_section="service_accounts",
            accounts=accounts,
            categories=CATEGORIES,
            presets=PORTAL_PRESETS,
            available_plates=get_available_plates(),
            active_category="",
            search_query="",
            active_status="",
            edit_account=data if not account_id else {**data, "id": account_id},
            error_message=str(exc),
            stats={
                "total_count": len(all_accounts),
                "active_count": sum(1 for a in all_accounts if a.get("status") == "active"),
                "toll_count": sum(1 for a in all_accounts if a.get("category") == "toll"),
                "fuel_count": sum(1 for a in all_accounts if a.get("category") == "fuel"),
                "total_balance": sum(
                    (Decimal(str(a.get("current_balance") or "0")) for a in all_accounts if a.get("category") == "toll"),
                    Decimal("0"),
                ),
            },
        )
        return templates.TemplateResponse("pages/service_accounts.html", {"request": request, **ctx})


@router.post("/{account_id}/delete")
async def delete_account_endpoint(
    account_id: int,
    request: Request,
    user: dict = Depends(require_menu("service_accounts")),
):
    success = delete_service_account(account_id)
    logger.info("Deleted service account %d: %s by %s", account_id, success, user.get("username"))
    if request.headers.get("HX-Request"):
        return HTMLResponse("", status_code=200)
    if "application/json" in request.headers.get("Accept", ""):
        return JSONResponse({"ok": success})
    return RedirectResponse("/admin/service-accounts?deleted=1", status_code=303)


@router.post("/{account_id}/quick-balance")
async def quick_balance_endpoint(
    account_id: int,
    balance: str = Form(...),
    _: dict = Depends(require_menu("service_accounts")),
):
    success = update_account_balance(account_id, balance)
    if not success:
        return JSONResponse({"ok": False, "message": "Số dư không hợp lệ"}, status_code=400)
    account = get_service_account(account_id)
    return JSONResponse(
        {
            "ok": True,
            "current_balance": float(account.get("current_balance", 0)),
            "formatted": f"{float(account.get('current_balance', 0)):,.0f}".replace(",", "."),
        }
    )


# Standalone alias router to redirect old /admin/transport/service-accounts to /admin/service-accounts
alias_router = APIRouter(prefix="/transport/service-accounts")


@alias_router.get("")
async def alias_service_accounts_page():
    return RedirectResponse("/admin/service-accounts", status_code=303)


@alias_router.post("")
async def alias_save_account(request: Request):
    return RedirectResponse("/admin/service-accounts", status_code=307)
