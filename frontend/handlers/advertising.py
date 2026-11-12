"""Cầu nối HTTP của frontend.modules.advertising.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.modules.advertising.controller as controller


async def advertising_dashboard_page(
    request: Request,
    status: str = 'all',
    q: str = '',
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('advertising')),
):
    'Render the Advertising & Google Ads trip campaign dashboard.'
    return await controller.advertising_dashboard_page(request=request, status=status, q=q, page=page, user=user)


async def advertising_keywords_page(
    request: Request,
    kw_set: str = Query('xe_cau', alias='set'),
    group: str = '',
    q: str = '',
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('advertising')),
):
    'Render the keyword plans, ad copies and negative keywords as a filterable list.'
    return await controller.advertising_keywords_page(request=request, kw_set=kw_set, group=group, q=q, page=page, user=user)


async def api_create_campaign(
    request: Request,
    title: str = Form(''),
    target_province: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    daily_budget: str = Form('100000'),
    radius_km: int = Form(25),
    service_type: str = Form('crane_weights'),
    notes: str = Form(''),
    user: dict = Depends(require_menu('advertising')),
):
    'API endpoint to create a dynamic trip campaign.'
    return await controller.api_create_campaign(request=request, title=title, target_province=target_province, start_date=start_date, end_date=end_date, daily_budget=daily_budget, radius_km=radius_km, service_type=service_type, notes=notes, user=user)


async def api_toggle_campaign(
    campaign_id: int,
    request: Request,
    user: dict = Depends(require_menu('advertising')),
):
    'API endpoint to quickly toggle a campaign ON / OFF.'
    return await controller.api_toggle_campaign(campaign_id=campaign_id, request=request, user=user)


async def api_delete_campaign(
    campaign_id: int,
    request: Request,
    user: dict = Depends(require_menu('advertising')),
):
    'API endpoint to remove a campaign.'
    return await controller.api_delete_campaign(campaign_id=campaign_id, request=request, user=user)


async def api_update_campaign_metrics(
    campaign_id: int,
    request: Request,
    impressions: int = Form(0),
    clicks: int = Form(0),
    calls: int = Form(0),
    spent_amount: str = Form('0'),
    user: dict = Depends(require_menu('advertising')),
):
    'API endpoint to manually update performance metrics for a campaign.'
    return await controller.api_update_campaign_metrics(campaign_id=campaign_id, request=request, impressions=impressions, clicks=clicks, calls=calls, spent_amount=spent_amount, user=user)


async def api_set_campaign_google_id(
    campaign_id: int,
    request: Request,
    google_campaign_id: str = Form(''),
    user: dict = Depends(require_menu('advertising')),
):
    'Set or unlink google_campaign_id for an internal campaign.'
    return await controller.api_set_campaign_google_id(campaign_id=campaign_id, request=request, google_campaign_id=google_campaign_id, user=user)


async def api_google_test_connection(request: Request, user: dict = Depends(require_menu('advertising'))):
    'Kiểm tra kết nối tới Google Ads API.'
    return await controller.api_google_test_connection(request=request, user=user)


async def api_google_sync(request: Request, user: dict = Depends(require_menu('advertising'))):
    'Thực hiện quy trình đồng bộ Google Ads (link, pull metrics, push statuses).'
    return await controller.api_google_sync(request=request, user=user)


async def api_google_set_live(
    request: Request,
    enabled: str = Form('0'),
    confirm: str = Form(''),
    user: dict = Depends(require_menu('advertising')),
):
    'Bật/tắt chế độ tự động thật của Google Ads API.'
    return await controller.api_google_set_live(request=request, enabled=enabled, confirm=confirm, user=user)


async def api_save_settings(
    request: Request,
    developer_token: str = Form(''),
    customer_id: str = Form(''),
    login_customer_id: str = Form(''),
    refresh_token: str = Form(''),
    client_id: str = Form(''),
    client_secret: str = Form(''),
    user: dict = Depends(require_menu('advertising')),
):
    'API endpoint to save Google Ads API Credentials.'
    return await controller.api_save_settings(request=request, developer_token=developer_token, customer_id=customer_id, login_customer_id=login_customer_id, refresh_token=refresh_token, client_id=client_id, client_secret=client_secret, user=user)
