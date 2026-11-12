# Cấu hình dịch vụ cho thuê container: cùng pháp nhân, hotline và trang web với dịch vụ xe cẩu (Đại Lộ Xanh),
# vì khách thuê container thường cần cẩu hạ và đặt container tại chỗ.
from backend.services import account_sales as sales
from frontend.crane import config as crane_config

SERVICE_KEY = "container"
SERVICE_SLUG = "thue-container"

LEGACY_PLACEHOLDER_NAMES = {
    "Container văn phòng",
    "Container kho",
    "Trọn gói giao, cẩu hạ và thu hồi",
}

CONTAINER_ITEMS = [
    {
        "id": "container-van-phong-20ft",
        "name": "Container văn phòng 20ft (có điều hòa)",
        "group": "Container",
        "grade": "20 feet · 15m²",
        "price": 2500000,
        "unit": "tháng",
        "stock": 8,
        "icon": "apartment",
        "img": "/static/services/container/img/container_hero.jpg",
        "desc": "Văn phòng di động 20ft (6m × 2,4m) lắp sẵn điều hòa 12.000BTU, đèn LED, cửa kính lùa cách nhiệt dùng ngay.",
        "specs": [
            "Thuê từ 3 tháng giảm còn 2.300.000đ/tháng",
            "Tùy chọn thêm phòng WC khép kín (+400.000đ/tháng)",
            "Cẩu hạ đặt đúng vị trí bằng xe cẩu tự hành của công ty",
        ],
    },
    {
        "id": "container-van-phong-40ft",
        "name": "Container văn phòng 40ft (rộng 30m²)",
        "group": "Container",
        "grade": "40 feet · 30m²",
        "price": 4200000,
        "unit": "tháng",
        "stock": 5,
        "icon": "domain",
        "img": "/static/services/container/img/container_hero.jpg",
        "desc": "Văn phòng rộng 40ft (12m × 2,4m) cho 8–12 nhân sự, trang bị 2 điều hòa, hệ điện âm tường & vách xốp cách nhiệt.",
        "specs": [
            "Thuê từ 3 tháng giảm còn 3.900.000đ/tháng",
            "Phù hợp phòng điều hành công trường, phòng KCS nhà máy",
            "Hợp đồng pháp nhân rõ ràng, xuất hóa đơn VAT đầy đủ",
        ],
    },
    {
        "id": "container-kho-20ft-40ft",
        "name": "Container kho 20ft (bản 40ft: 2,6tr/tháng)",
        "group": "Container",
        "grade": "Kho 20ft / 40ft",
        "price": 1500000,
        "unit": "tháng",
        "stock": 12,
        "icon": "inventory_2",
        "img": "/static/services/crane/img/crane_hero_yard.jpg",
        "desc": "Kho thép nguyên bản kín mưa nắng 100%, khóa bảo vệ chắc chắn cho vật tư & thiết bị công trường.",
        "specs": [
            "Container kho 20ft (33m³): 1.500.000đ/tháng",
            "Container kho 40ft (67m³): 2.600.000đ/tháng",
            "Hỗ trợ thuê ngắn hạn theo tháng hoặc dài hạn công trình",
        ],
    },
    {
        "id": "container-tron-goi-cau-ha",
        "name": "Cẩu hạ & vận chuyển Container 2 chiều",
        "group": "Container",
        "grade": "Xe cẩu 8T–10T",
        "price": 1800000,
        "unit": "chuyến",
        "stock": 4,
        "icon": "local_shipping",
        "img": "/static/services/crane/img/crane_truck_88c_unic.jpg",
        "desc": "Vận chuyển, cẩu hạ đặt container tận nơi và thu hồi bằng xe cẩu tự hành chính chủ 8–10 tấn.",
        "specs": [
            "Container 20ft nội vùng Phú Thọ – Vĩnh Phúc: từ 1.800.000đ/lượt",
            "Nhận cẩu dời vị trí, đặt chồng tầng hoặc rút container 40ft",
            "Một đầu mối cho cả container và xe cẩu tự hành",
        ],
    },
]

PAGE = {
    "h1": ("Cho Thuê Container", "Văn Phòng & Container Kho"),
    "intro": (
        "Bảng giá thuê công khai <strong class=\"text-slate-900 font-bold\">container kho chỉ từ 1.500.000đ/tháng</strong> "
        "và <strong class=\"text-slate-900 font-bold\">container văn phòng từ 2.500.000đ/tháng</strong> tại Vĩnh Phúc, Phú Thọ &amp; Miền Bắc. "
        "Sẵn <strong class=\"text-red-700 font-bold\">xe cẩu tự hành 8T–10T</strong> cẩu hạ tận nơi không qua trung gian."
    ),
    "cta": "GỌI THUÊ CONTAINER",
    "hero_caption": "Cho Thuê Container Văn Phòng & Kho — Giá Niêm Yết",
    "hero_icon": "",
    "hero_image": "/static/services/container/img/container_hero.jpg",
    "catalog_kicker": "Bảng Giá Niêm Yết Trực Tiếp",
    "catalog_title": "Chọn Loại Container Theo Nhu Cầu & Đơn Giá",
    "price_title": "Bảng Giá Cho Thuê Container Niêm Yết",
    "price_lead": "Giá niêm yết công khai theo tháng; thuê dài hạn từ 3 tháng trở lên được giảm thêm và tối ưu cước xe cẩu chính chủ.",
    "price_head": "Hạng Mục Container",
    "price_badge": "Niêm yết theo tháng / chuyến",
    "faq_title": "Câu Hỏi Thường Gặp Về Thuê Container",
    "area_title": "Cho Thuê Container Theo Khu Vực",
    "area_link": "Cho thuê container tại {area}",
    "region": "Vĩnh Phúc và Phú Thọ",
}


def ensure_container_catalog() -> bool:
    """Nâng cấp danh mục container 0đ giữ chỗ cũ thành bảng giá niêm yết 1 lần nếu chưa có giá."""
    return sales.upgrade_placeholder_landing_items(
        SERVICE_SLUG,
        CONTAINER_ITEMS,
        LEGACY_PLACEHOLDER_NAMES,
    )


def get_container_settings() -> dict:
    """Thông tin công ty và hotline lấy từ cấu hình xe cẩu; SEO và danh mục là của dịch vụ container."""
    info = crane_config.get_crane_settings()
    company_name, tax_id, phone = info["company_name"], info["tax_id"], info["phone"]
    seo_config = {
        "title": f"Bảng giá thuê container văn phòng & kho từ 1,5tr/tháng — {company_name}",
        "description": (
            f"Cho thuê container kho từ 1.500.000đ/tháng, container văn phòng từ 2.500.000đ/tháng tại Vĩnh Phúc, Phú Thọ và Miền Bắc — {company_name} "
            f"(MST: {tax_id}). Sẵn xe cẩu 8–10 tấn cẩu hạ tại chỗ. Hotline: {phone}."
        ),
        "keywords": (
            "giá thuê container, cho thuê container văn phòng, thuê container kho, container Phú Thọ, "
            f"container Vĩnh Phúc, cẩu hạ container, {company_name}"
        ),
        "og_image": "/static/services/container/img/container_hero.jpg",
    }
    return {**info, "seo_config": seo_config, "catalog_items": CONTAINER_ITEMS}
