"""Advertising management router for dynamic trip Google Ads."""

from datetime import date, timedelta
import logging
import urllib.parse
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from frontend.deps import get_menu_context, require_menu, templates
from frontend.modules.advertising import service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/advertising", tags=["advertising"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def advertising_dashboard_page(
    request: Request,
    status: str = "all",
    user: dict = Depends(require_menu("advertising")),
):
    """Render the Advertising & Google Ads trip campaign dashboard."""
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "advertising"
    ctx["page_title"] = "Quản lý Quảng cáo Google — Chiến dịch chuyến xe"
    
    # Load campaigns and metrics
    ctx["campaigns"] = service.list_campaigns(status_filter=status)
    ctx["kpis"] = service.get_advertising_kpi_summary()
    ctx["provinces"] = service.VIETNAM_PROVINCES
    ctx["settings"] = service.get_advertising_settings()
    ctx["status_filter"] = status
    
    # Default form dates (Tomorrow to 3 days after)
    today = date.today()
    ctx["default_start_date"] = (today + timedelta(days=1)).isoformat()
    ctx["default_end_date"] = (today + timedelta(days=3)).isoformat()
    
    return templates.TemplateResponse("pages/advertising/index.html", {"request": request, **ctx})


@router.get("/keywords", response_class=HTMLResponse)
async def advertising_keywords_page(
    request: Request,
    user: dict = Depends(require_menu("advertising")),
):
    """Render the keyword plans, ad copies and negative keywords page."""
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "advertising_keywords"
    ctx["page_title"] = "Kế hoạch Từ khóa & Mẫu Quảng cáo Google Ads"
    ctx["resources"] = service.get_advertising_resources()
    return templates.TemplateResponse("pages/advertising/keywords.html", {"request": request, **ctx})


@router.post("/api/campaigns")
async def api_create_campaign(
    request: Request,
    title: str = Form(""),
    target_province: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    daily_budget: str = Form("100000"),
    radius_km: int = Form(25),
    service_type: str = Form("crane_weights"),
    notes: str = Form(""),
    user: dict = Depends(require_menu("advertising")),
):
    """API endpoint to create a dynamic trip campaign."""
    cleaned_budget = daily_budget.replace(".", "").replace(",", "").strip()
    try:
        budget_val = float(cleaned_budget) if cleaned_budget else 100000
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


@router.post("/api/campaigns/{campaign_id}/toggle")
async def api_toggle_campaign(
    campaign_id: int,
    request: Request,
    user: dict = Depends(require_menu("advertising")),
):
    """API endpoint to quickly toggle a campaign ON / OFF."""
    ok, msg, new_status = service.toggle_campaign_status(campaign_id)
    return JSONResponse({"ok": ok, "message": msg, "new_status": new_status})


@router.post("/api/campaigns/{campaign_id}/delete")
async def api_delete_campaign(
    campaign_id: int,
    request: Request,
    user: dict = Depends(require_menu("advertising")),
):
    """API endpoint to remove a campaign."""
    ok, msg = service.delete_campaign(campaign_id)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    if is_ajax:
        return JSONResponse({"ok": ok, "message": msg})
    return RedirectResponse("/admin/advertising?message=" + urllib.parse.quote(msg), status_code=303)


@router.post("/api/settings")
async def api_save_settings(
    request: Request,
    developer_token: str = Form(""),
    customer_id: str = Form(""),
    login_customer_id: str = Form(""),
    user: dict = Depends(require_menu("advertising")),
):
    """API endpoint to save Google Ads API Credentials."""
    settings = {
        "developer_token": developer_token.strip(),
        "customer_id": customer_id.strip(),
        "login_customer_id": login_customer_id.strip(),
    }
    ok = service.save_advertising_settings(settings)
    msg = "Đã lưu thông tin cấu hình Google Ads API thành công!" if ok else "Lỗi khi lưu cấu hình."
    
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    if is_ajax:
        return JSONResponse({"ok": ok, "message": msg})
    status_key = "message" if ok else "error"
    return RedirectResponse(f"/admin/advertising?{status_key}=" + urllib.parse.quote(msg), status_code=303)
