"""Trang landing dùng chung cho các dịch vụ dạng danh mục (container, điện mặt trời...).

Sản phẩm của các trang lấy từ danh mục sản phẩm chung (bảng account_products, cột `service`), sửa ở
/admin/account-sales/products. Danh sách viết trong config chỉ còn là dữ liệu ban đầu và dự phòng khi không đọc được DB.
"""
import logging

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.services import account_sales as sales
from frontend.deps import get_optional_user, templates
from frontend.routers.cms_controller import get_landing_settings
from frontend.container import config as container_config
from frontend.crane import config as crane_config
from frontend.crane.locations import AREAS, SERVICES, page_meta
from frontend.registry import install_template_globals, page_for
from frontend.photo import config as photo_config
from frontend.solar import config as solar_config
from frontend.weights import config as weights_config

logger = logging.getLogger(__name__)

install_template_globals(templates.env)

# Dịch vụ trên trang khu vực (/van-chuyen-qua-can/...) -> dịch vụ trong danh mục sản phẩm chung.
SERVICE_ALIASES = {"van-chuyen-qua-can": "qua-can"}

# Danh mục ban đầu của từng trang, chép vào bảng ở lần khởi động đầu tiên.
SEED_ITEMS = {
    "thue-container": container_config.CONTAINER_ITEMS,
    "qua-can": weights_config.TEST_WEIGHT_ITEMS,
    "dien-mat-troi": solar_config.SOLAR_ITEMS,
    "thue-may-anh": photo_config.PHOTO_ITEMS,
}


def _fmt_vnd(amount: int) -> str:
    return f"{int(amount):,}".replace(",", ".") + "đ"


def _infer_catalog_unit(service: str, item: dict) -> str:
    explicit = (item.get("unit") or "").strip()
    if explicit:
        return explicit
    lname = (item.get("name") or "").lower()
    lgrade = (item.get("grade") or "").lower()
    if service == "thue-container":
        if any(k in lname or k in lgrade for k in ("cẩu hạ", "chuyến", "thu hồi", "vận chuyển")):
            return "chuyến"
        return "tháng"
    if service == "qua-can":
        if any(k in lname for k in ("500kg", "1000kg", "gang")):
            return "quả / đợt"
        if "hộp gỗ" in lname:
            return "bộ / ngày"
        if "inox" in lname:
            return "quả / ngày"
        if "trạm cân" in lname or "trọn gói" in lname:
            return "trọn gói trạm"
        return "ngày"
    if service == "dien-mat-troi":
        if "tấm pin" in lname:
            return "tấm 550W"
        if "biến tần" in lname or "inverter" in lname:
            return "bộ 5kW"
        if "pin lưu trữ" in lname or "lifepo4" in lname or "5kwh" in lname:
            return "module 5kWh"
        if "tủ điện" in lname or "phụ kiện" in lname:
            return "bộ đồng bộ"
        return "bộ"
    return "ngày"


def _enrich_catalog_items(service: str, raw_items: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for item in raw_items:
        price = int(item.get("price") or 0)
        unit = _infer_catalog_unit(service, item)
        enriched.append({
            **item,
            "price": price,
            "has_price": price > 0,
            "price_fmt": _fmt_vnd(price) if price > 0 else "Theo quy mô",
            "unit": unit,
        })
    return enriched


def migrate_product_catalog() -> None:
    """Khi khởi động: thêm cột dịch vụ cho bảng sản phẩm/đơn hàng và chép danh mục cũ của các trang dịch vụ vào (chỉ lần đầu)."""
    try:
        if sales.ensure_catalog_columns():
            for service, items in SEED_ITEMS.items():
                sales.seed_landing_items(service, items)
            logger.info("Đã chuyển danh mục các trang dịch vụ vào danh mục sản phẩm chung.")
        sales.ensure_order_columns()
        upgraded = []
        if container_config.ensure_container_catalog():
            upgraded.append("container")
        if weights_config.ensure_weights_catalog():
            upgraded.append("quả cân")
        if solar_config.ensure_solar_catalog():
            upgraded.append("điện mặt trời")
        if photo_config.ensure_photo_catalog():
            upgraded.append("máy ảnh & photobooth")
        if upgraded:
            logger.info("Đã nâng cấp danh mục (%s) lên bảng giá niêm yết chi tiết.", ", ".join(upgraded))
    except Exception:
        logger.exception("Không chuyển được danh mục sản phẩm chung")


def catalog_items(service: str) -> list[dict]:
    """Sản phẩm đang bật của dịch vụ; dịch vụ không có danh mục (xe cẩu...) trả về danh sách rỗng."""
    key = SERVICE_ALIASES.get(service, service)
    if key not in SEED_ITEMS:
        return []
    try:
        items = sales.landing_items(key)
        if not items or all(int(it.get("price") or 0) == 0 for it in items):
            upgraded = False
            if key == "thue-container":
                upgraded = container_config.ensure_container_catalog()
            elif key == "qua-can":
                upgraded = weights_config.ensure_weights_catalog()
            elif key == "dien-mat-troi":
                upgraded = solar_config.ensure_solar_catalog()
            elif key == "thue-may-anh":
                upgraded = photo_config.ensure_photo_catalog()
            if upgraded:
                items = sales.landing_items(key)
        return _enrich_catalog_items(key, items or SEED_ITEMS[key])
    except Exception:
        logger.exception("Không đọc được danh mục sản phẩm của %s, dùng danh sách mặc định", key)
        return _enrich_catalog_items(key, SEED_ITEMS[key])


def render_landing(
    request: Request,
    service: str,
    info: dict,
    page: dict,
    template_name: str = "catalog_landing.html",
    extra_context: dict | None = None,
):
    """Dựng trang landing của một dịch vụ dạng danh mục từ cấu hình `info` và nội dung `page`."""
    raw_settings = get_landing_settings()
    settings = {
        "phone": info["phone"],
        "phone_raw": info["phone_raw"],
        "zalo": info["zalo"],
        "gtm_id": raw_settings.get("GTM_ID", ""),
        "fb_pixel_id": raw_settings.get("FB_PIXEL_ID", ""),
    }
    entry = page_for(service)
    region = page["region"]
    ctx = {
        "request": request,
        "settings": settings,
        "company": info,
        "page": page,
        "items": catalog_items(service),
        "faq_items": [
            {"q": item["q"].format(area=region), "a": item["a"].format(area=region)}
            for item in SERVICES[service]["faq"]
        ],
        "area_links": [
            {"name": data["name"], "url": f"{entry['path']}/{slug}", "label": page["area_link"].format(area=data["name"])}
            for slug, data in AREAS.items()
        ],
        "canonical_path": entry["path"],
        "source_page": f"{entry['key']}_landing",
        "source_page_form": f"{entry['key']}_landing",
        "contact_variant": entry["key"],
        "active_page": entry["key"],
        "user": get_optional_user(request),
    }
    if extra_context:
        ctx.update(extra_context)
    return templates.TemplateResponse(template_name, ctx)


# Trang khu vực của một dịch vụ (/<dịch-vụ>/<khu-vực>), dùng chung cho mọi dịch vụ Đại Lộ Xanh.
async def render_area_service(request: Request, service: str, area: str) -> HTMLResponse:
    if area not in AREAS or service not in SERVICES:
        raise HTTPException(status_code=404, detail="Khu vực không tồn tại")

    area_info = AREAS[area]
    service_info = SERVICES[service]

    crane_info = crane_config.get_crane_settings()
    raw_settings = get_landing_settings()
    settings = {
        "phone": crane_info["phone"],
        "phone_raw": crane_info["phone_raw"],
        "zalo": crane_info["zalo"],
        "gtm_id": raw_settings.get("GTM_ID", ""),
        "fb_pixel_id": raw_settings.get("FB_PIXEL_ID", ""),
    }

    meta = page_meta(service, area, crane_info)
    meta["canonical_url"] = str(request.base_url).rstrip("/") + meta["canonical_path"]

    other_areas = [
        {"slug": a_slug, "name": a_data["name"], "url": f"/{service}/{a_slug}"}
        for a_slug, a_data in AREAS.items()
        if a_slug != area
    ]
    context = {
        "request": request,
        "settings": settings,
        "company": crane_info,
        "fleet_items": crane_info["fleet_items"],
        "weight_items": catalog_items("qua-can"),
        "catalog_items": catalog_items(service),
        "contact_variant": page_for(service).get("key") or service_info.get("contact_variant", ""),
        "source_page": "crane_landing",
        "source_page_form": meta["source_page"],
        "active_page": service_info.get("active_page", "crane"),
        "user": get_optional_user(request),
        "service": service_info,
        "service_slug": service,
        "home_path": page_for(service).get("path") or f"/{service}/{area}",
        "area": area_info,
        "area_slug": area,
        "meta": meta,
        "other_areas": other_areas,
    }

    return templates.TemplateResponse("crane/area.html", context)


def redirect_route(target_url: str):
    """Tạo route chuyển hướng 301 trực tiếp tới `target_url` (không cần file handler riêng)."""
    async def _redirect():
        return RedirectResponse(url=target_url, status_code=301)

    _redirect.__name__ = "redirect_" + (target_url.strip("/").replace("-", "_") or "root")
    return _redirect


def landing_route(service: str, settings_getter, page: dict):
    """Tạo route cho trang landing dịch vụ dạng danh mục dùng chung."""
    async def _landing(request: Request):
        return render_landing(request, service, settings_getter(), page)

    _landing.__name__ = "landing_" + service.replace("-", "_")
    return _landing


def area_route(service: str):
    """Tạo route trang SEO khu vực `/<dịch-vụ>/{area}` dùng chung cho mọi dịch vụ."""
    async def _area(request: Request, area: str) -> HTMLResponse:
        return await render_area_service(request, service, area)

    _area.__name__ = "area_" + service.replace("-", "_")
    return _area
