"""Router soạn tin rao vặt và theo dõi danh bạ nơi đăng."""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import RedirectResponse

from backend.services import listings as listings_service
from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate_counts
from frontend.redirects import redirect_with_query
from frontend.crane.config import get_crane_settings
from frontend.crane.locations import AREAS, SERVICES
from frontend.weights.config import get_test_weights_settings

logger = logging.getLogger(__name__)

BASE_URL = "/admin/advertising/listings"
PER_PAGE = 20


def _redirect(service: str = "", area: str = "", msg: str = "", error: str = "") -> RedirectResponse:
    return redirect_with_query(BASE_URL, service=service, area=area, msg=msg, error=error)


def list_page(request: Request, service: str, area: str, page: int, user: dict):
    clean_service = service.strip() if service else "thue-xe-cau"
    if clean_service not in SERVICES:
        clean_service = "thue-xe-cau"

    clean_area = area.strip() if area else "phu-tho"
    if clean_area not in AREAS:
        clean_area = "phu-tho"

    page_num = max(1, int(page or 1))
    offset = (page_num - 1) * PER_PAGE

    company_name = "Đại Lộ Xanh"
    hotline = "0919 497 568"
    if clean_service == "van-chuyen-qua-can":
        cfg = get_test_weights_settings()
    else:
        cfg = get_crane_settings()
    if cfg:
        company_name = cfg.get("brand_name") or cfg.get("company_name") or company_name
        hotline = cfg.get("phone") or hotline

    ads = listings_service.build_ads(
        service=clean_service,
        area_slug=clean_area,
        company=company_name,
        hotline=hotline,
    )

    directories = listings_service.list_directories(limit=PER_PAGE, offset=offset)
    total = listings_service.count_directories()

    pagination = paginate_counts(
        total=total,
        page=page_num,
        per_page=PER_PAGE,
        base_url=BASE_URL,
        extra_params={"service": clean_service, "area": clean_area},
    )

    context = {
        "request": request,
        "services": SERVICES,
        "areas": AREAS,
        "selected_service": clean_service,
        "selected_area": clean_area,
        "ads": ads,
        "directories": directories,
        "pagination": pagination,
        "current_menu": "listings",
        "msg": request.query_params.get("msg", ""),
        "error": request.query_params.get("error", ""),
        **get_menu_context(request, user),
    }
    return templates.TemplateResponse("pages/advertising/listings.html", context)


def create_directory(name: str, url: str, note: str, service: str, area: str, user: dict):
    clean_name = name.strip()
    if not clean_name:
        return _redirect(service=service, area=area, error="Không lưu được nơi đăng.")

    dir_id = listings_service.create(name=clean_name, url=url.strip(), note=note.strip())
    if not dir_id:
        return _redirect(service=service, area=area, error="Không lưu được nơi đăng.")
    return _redirect(service=service, area=area, msg="Đã lưu.")


def update_directory(
    directory_id: int,
    name: str,
    url: str,
    posted_url: str,
    posted_on: str,
    note: str,
    service: str,
    area: str,
    user: dict,
):
    clean_name = name.strip()
    if not clean_name:
        return _redirect(service=service, area=area, error="Không lưu được nơi đăng.")

    data = {
        "name": clean_name,
        "url": url.strip(),
        "posted_url": posted_url.strip(),
        "posted_on": posted_on.strip(),
        "note": note.strip(),
    }
    ok = listings_service.update(directory_id, data)
    if not ok:
        return _redirect(service=service, area=area, error="Không lưu được nơi đăng.")
    return _redirect(service=service, area=area, msg="Đã lưu.")


def delete_directory(directory_id: int, service: str, area: str, user: dict):
    listings_service.delete(directory_id)
    return _redirect(service=service, area=area, msg="Đã xóa.")


def mark_directory(
    directory_id: int,
    status: str,
    posted_url: str,
    posted_on: str,
    service: str,
    area: str,
    user: dict,
):
    ok = listings_service.mark(
        directory_id=directory_id,
        status=status.strip(),
        posted_url=posted_url.strip(),
        posted_on=posted_on.strip() or None,
    )
    if not ok:
        return _redirect(service=service, area=area, error="Không lưu được trạng thái.")
    return _redirect(service=service, area=area, msg="Đã lưu.")
