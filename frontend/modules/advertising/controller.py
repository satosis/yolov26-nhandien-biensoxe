"""Advertising management router for dynamic trip Google Ads."""

from datetime import date, timedelta
from decimal import Decimal
import logging
import urllib.parse
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from backend.services.google_ads_api import is_configured, is_live, load_config
from backend.services.marketing_readiness import collect_checks
from frontend.deps import get_menu_context, templates
from frontend.formatting.money import parse_vnd
from frontend.modules.advertising import service
from frontend.pagination import paginate

logger = logging.getLogger(__name__)


async def advertising_dashboard_page(request: Request, status: str, q: str, page: int, user: dict):
    """Render the Advertising & Google Ads trip campaign dashboard."""
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "advertising"
    ctx["page_title"] = "Quản lý Quảng cáo Google — Chiến dịch chuyến xe"

    # Load campaigns and metrics
    campaigns = service.list_campaigns(status_filter=status)
    needle = q.strip().lower()
    if needle:
        campaigns = [c for c in campaigns if needle in f'{c.get("title") or ""} {c.get("target_province") or ""}'.lower()]
    ctx["pagination"] = paginate(campaigns, page=page, per_page=20, base_url="/admin/advertising",
                                 extra_params={"status": status, "q": q.strip()})
    ctx["campaigns"] = ctx["pagination"].items
    ctx["q"] = q.strip()
    ctx["kpis"] = service.get_advertising_kpi_summary()
    ctx["provinces"] = service.VIETNAM_PROVINCES
    ctx["settings"] = service.get_advertising_settings_view()
    ctx["status_filter"] = status
    ctx["readiness"] = await run_in_threadpool(collect_checks)

    # Google Ads API status & sync logs
    g_cfg = load_config()
    ctx["google_is_configured"] = is_configured(g_cfg)
    ctx["google_is_live"] = is_live(g_cfg)
    ctx["google_api_version"] = g_cfg.get("google_ads_api_version", "v25")
    ctx["google_config_summary"] = {
        "customer_id": bool(g_cfg.get("customer_id")),
        "client_id": bool(g_cfg.get("client_id")),
        "client_secret": bool(g_cfg.get("client_secret")),
        "refresh_token": bool(g_cfg.get("refresh_token")),
        "login_customer_id": bool(g_cfg.get("login_customer_id")),
    }
    ctx["sync_logs"] = service.get_recent_sync_logs(10)

    # Default form dates (Tomorrow to 3 days after)
    today = date.today()
    ctx["default_start_date"] = (today + timedelta(days=1)).isoformat()
    ctx["default_end_date"] = (today + timedelta(days=3)).isoformat()

    ctx["service_types"] = service.SERVICE_TYPES
    ctx["service_type_badges"] = {value: (badge, color) for value, _label, badge, color in service.SERVICE_TYPES}

    return templates.TemplateResponse("pages/advertising/index.html", {"request": request, **ctx})


async def advertising_keywords_page(request: Request, kw_set: str, group: str, q: str, page: int, user: dict):
    """Render the keyword plans, ad copies and negative keywords as a filterable list."""
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "advertising_keywords"
    ctx["page_title"] = "Kế hoạch Từ khóa & Mẫu Quảng cáo Google Ads"
    resources = service.get_advertising_resources()
    sets = {key: service.get_keyword_items(key, resources) for key, _label, _resource in service.KEYWORD_SETS}
    view = kw_set if kw_set in sets else service.KEYWORD_SETS[0][0]
    items = sets[view]
    groups = list(dict.fromkeys(item["group"] for item in items))
    group_view = group if group in groups else ""
    needle = q.strip().lower()
    matched = [item for item in items if (not group_view or item["group"] == group_view)
               and (not needle or needle in item["text"].lower())]
    ctx.update(kw_sets=[(key, label, len(sets[key])) for key, label, _resource in service.KEYWORD_SETS],
               kw_view=view, kw_groups=groups, kw_group=group_view, q=q.strip(), is_copy_set=view.startswith("ad_copy"),
               copy_text="\n".join(item["raw"] for item in matched))
    ctx["pagination"] = paginate(matched, page=page, per_page=25, base_url="/admin/advertising/keywords",
                                 extra_params={"set": view, "group": group_view, "q": q.strip()})
    ctx["keyword_items"] = ctx["pagination"].items
    return templates.TemplateResponse("pages/advertising/keywords.html", {"request": request, **ctx})


async def api_create_campaign(
    request: Request,
    title: str,
    target_province: str,
    start_date: str,
    end_date: str,
    daily_budget: str,
    radius_km: int,
    service_type: str,
    notes: str,
    user: dict,
):
    """API endpoint to create a dynamic trip campaign."""
    try:
        budget_val = float(parse_vnd(daily_budget)) if daily_budget.strip() else 100000
    except ValueError:
        budget_val = 100000

    ok, msg, camp_id = service.create_campaign(
        title=title,
        target_province=target_province,
        start_date=start_date,
        end_date=end_date,
        daily_budget=budget_val,
        radius_km=radius_km,
        service_type=service_type,
        notes=notes,
    )

    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    if is_ajax:
        return JSONResponse({"ok": ok, "message": msg, "campaign_id": camp_id})
    status_key = "message" if ok else "error"
    return RedirectResponse(f"/admin/advertising?{status_key}=" + urllib.parse.quote(msg), status_code=303)


async def api_toggle_campaign(campaign_id: int, request: Request, user: dict):
    """API endpoint to quickly toggle a campaign ON / OFF."""
    ok, msg, new_status = service.toggle_campaign_status(campaign_id)
    return JSONResponse({"ok": ok, "message": msg, "new_status": new_status})


async def api_delete_campaign(campaign_id: int, request: Request, user: dict):
    """API endpoint to remove a campaign."""
    ok, msg = service.delete_campaign(campaign_id)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    if is_ajax:
        return JSONResponse({"ok": ok, "message": msg})
    return RedirectResponse("/admin/advertising?message=" + urllib.parse.quote(msg), status_code=303)


async def api_update_campaign_metrics(
    campaign_id: int,
    request: Request,
    impressions: int,
    clicks: int,
    calls: int,
    spent_amount: str,
    user: dict,
):
    """API endpoint to manually update performance metrics for a campaign."""
    try:
        spent = Decimal(str(parse_vnd(spent_amount))) if spent_amount.strip() else Decimal("0")
    except ValueError:
        return JSONResponse({"ok": False, "message": "Số tiền chi tiêu không hợp lệ."})

    ok, msg = await run_in_threadpool(
        service.update_campaign_metrics,
        campaign_id,
        impressions,
        clicks,
        calls,
        spent,
    )
    return JSONResponse({"ok": ok, "message": msg})


async def api_set_campaign_google_id(campaign_id: int, request: Request, google_campaign_id: str, user: dict):
    """Set or unlink google_campaign_id for an internal campaign."""
    ok, msg = await run_in_threadpool(
        service.set_campaign_google_id,
        campaign_id,
        google_campaign_id,
    )
    return JSONResponse({"ok": ok, "message": msg})


async def api_google_test_connection(request: Request, user: dict):
    """Kiểm tra kết nối tới Google Ads API."""
    from backend.services.google_ads_api import GoogleAdsClient
    client = GoogleAdsClient()
    res = await run_in_threadpool(client.test_connection)
    msg = res.get("message")
    if res.get("ok"):
        name = res.get("name") or "Khách hàng"
        currency = res.get("currency") or "VND"
        msg = f"Kết nối Google Ads thành công! Tài khoản: {name} ({currency})."
        if res.get("warning"):
            msg += f" {res.get('warning')}"
    return JSONResponse({"ok": bool(res.get("ok")), "message": msg, **res})


async def api_google_sync(request: Request, user: dict):
    """Thực hiện quy trình đồng bộ Google Ads (link, pull metrics, push statuses)."""
    from backend.services.google_ads_api import sync_all
    res = await run_in_threadpool(sync_all)
    return JSONResponse(res)


async def api_google_set_live(request: Request, enabled: str, confirm: str, user: dict):
    """Bật/tắt chế độ tự động thật của Google Ads API."""
    is_live_requested = str(enabled).strip() in ("1", "true", "True")
    if is_live_requested and confirm.strip() != "BAT":
        return JSONResponse({
            "ok": False,
            "message": "Vui lòng nhập xác nhận BAT để kích hoạt chế độ tự động.",
        })
    ok, msg = await run_in_threadpool(service.set_google_ads_live, is_live_requested, (user or {}).get("username", ""))
    return JSONResponse({"ok": ok, "message": msg, "live": is_live_requested})


async def api_save_settings(
    request: Request,
    developer_token: str,
    customer_id: str,
    login_customer_id: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    user: dict,
):
    """API endpoint to save Google Ads API Credentials."""
    settings = {
        "developer_token": developer_token.strip(),
        "customer_id": customer_id.strip(),
        "login_customer_id": login_customer_id.strip(),
        "refresh_token": refresh_token.strip(),
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
    }
    ok, msg = service.save_advertising_settings(settings)

    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    if is_ajax:
        return JSONResponse({"ok": ok, "message": msg})
    status_key = "message" if ok else "error"
    return RedirectResponse(f"/admin/advertising?{status_key}=" + urllib.parse.quote(msg), status_code=303)
