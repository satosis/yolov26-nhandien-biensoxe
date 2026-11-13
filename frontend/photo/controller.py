from fastapi import Request

from frontend.catalog import catalog_items, render_landing
from . import config

GROUP_ORDER = ("Máy ảnh", "Ống kính", "Đèn & Gimbal", "Photobooth")

DEFAULT_GROUP_IMAGE = {
    "Máy ảnh": "/upload/services/photo/img/gear_cameras_lenses.jpg",
    "Ống kính": "/upload/services/photo/img/gear_lenses_closeup.jpg",
    "Đèn & Gimbal": "/upload/services/photo/img/gear_lighting_tripod.jpg",
    "Photobooth": "/upload/services/photo/img/photobooth_setup.jpg",
}


def _fmt_vnd(amount: int) -> str:
    return f"{int(amount):,}".replace(",", ".") + "đ"


def _infer_group(raw_group: str, name: str) -> str:
    if raw_group in GROUP_ORDER:
        return raw_group
    lname = (name or "").lower()
    if "photobooth" in lname or "booth" in lname or "in ảnh" in lname:
        return "Photobooth"
    if "ống kính" in lname or "lens" in lname or "sigma" in lname or "tamron" in lname or "mm f/" in lname:
        return "Ống kính"
    if any(k in lname for k in ("đèn", "flash", "gimbal", "rs3", "mic", "tripod", "softbox")):
        return "Đèn & Gimbal"
    return raw_group or "Máy ảnh"


def _infer_brand(raw_brand: str, name: str, group: str) -> str:
    if raw_brand:
        return raw_brand
    lname = (name or "").lower()
    for brand in ("Sony", "Canon", "Fujifilm", "Sigma", "Tamron", "DJI", "Godox", "Nikon"):
        if brand.lower() in lname:
            return brand
    if group == "Photobooth":
        return "Trọn gói sự kiện"
    return group


def _infer_unit(name: str, group: str) -> tuple[bool, str]:
    lname = (name or "").lower()
    is_pkg = group == "Photobooth" or "photobooth" in lname or "booth" in lname
    if not is_pkg:
        return False, "ngày"
    if "2 giờ" in lname or "2h" in lname:
        return True, "gói 2h"
    if "4 giờ" in lname or "4h" in lname:
        return True, "gói 4h"
    if "8 giờ" in lname or "8h" in lname or "cả ngày" in lname:
        return True, "gói 8h"
    return True, "gói 3h"


def _enrich_photo_catalog(raw_items: list[dict]) -> tuple[list[dict], list[dict]]:
    enriched: list[dict] = []
    groups_map: dict[str, dict] = {}

    for item in raw_items:
        name = item.get("name") or ""
        group = _infer_group(item.get("group") or "", name)
        brand = _infer_brand(item.get("grade") or "", name, group)
        price = int(item.get("price") or 0)
        is_pkg, unit = _infer_unit(name, group)
        img = item.get("img") or DEFAULT_GROUP_IMAGE.get(group, "/upload/services/photo/img/gear_cameras_lenses.jpg")
        stock = int(item.get("stock") or 0)

        day2_unit = int(round((price * 0.80) / 1000.0) * 1000) if (price and not is_pkg) else 0
        total_2d = config.calc_rental_total(price, 2) if (price and not is_pkg) else price
        total_3d = config.calc_rental_total(price, 3) if (price and not is_pkg) else price
        total_5d = config.calc_rental_total(price, 5) if (price and not is_pkg) else price

        entry = {
            **item,
            "group": group,
            "brand": brand,
            "grade": brand,
            "img": img,
            "price": price,
            "price_fmt": _fmt_vnd(price) if price > 0 else "Xem chi tiết",
            "is_package": is_pkg,
            "unit": unit,
            "stock": stock,
            "day2_unit": day2_unit,
            "day2_fmt": _fmt_vnd(day2_unit) if day2_unit else "",
            "total_2d": total_2d,
            "total_2d_fmt": _fmt_vnd(total_2d) if total_2d else "",
            "total_3d": total_3d,
            "total_3d_fmt": _fmt_vnd(total_3d) if total_3d else "",
            "total_5d": total_5d,
            "total_5d_fmt": _fmt_vnd(total_5d) if total_5d else "",
        }
        enriched.append(entry)

        grp = groups_map.setdefault(group, {"name": group, "count": 0, "brands_map": {}})
        grp["count"] += 1
        if brand:
            grp["brands_map"][brand] = grp["brands_map"].get(brand, 0) + 1

    order_index = {g: idx for idx, g in enumerate(GROUP_ORDER)}
    tree: list[dict] = []
    for grp_name, grp in sorted(groups_map.items(), key=lambda kv: (order_index.get(kv[0], 99), kv[0])):
        brands = [
            {"name": b_name, "count": b_count}
            for b_name, b_count in sorted(grp["brands_map"].items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        tree.append({"name": grp_name, "count": grp["count"], "brands": brands})

    return enriched, tree


async def landing_photo_service(request: Request):
    """Trang landing cho dịch vụ cho thuê máy ảnh và photobooth hiển thị giá trực tiếp."""
    raw_items = catalog_items("thue-may-anh")
    photo_items, photo_tree = _enrich_photo_catalog(raw_items)
    brand_totals: dict[str, int] = {}
    for it in photo_items:
        b = it["brand"]
        brand_totals[b] = brand_totals.get(b, 0) + 1
    photo_brands = [{"name": k, "count": v} for k, v in sorted(brand_totals.items(), key=lambda kv: (-kv[1], kv[0]))]

    initial_q = (request.query_params.get("q") or "").strip()
    initial_group = (request.query_params.get("group") or "").strip()
    initial_category = (request.query_params.get("category") or "").strip()

    return render_landing(
        request,
        "thue-may-anh",
        config.get_photo_settings(),
        config.PAGE,
        template_name="photo/index.html",
        extra_context={
            "items": photo_items,
            "photo_tree": photo_tree,
            "photo_brands": photo_brands,
            "initial_q": initial_q,
            "initial_group": initial_group,
            "initial_category": initial_category,
            "total_catalog_count": len(photo_items),
        },
    )
