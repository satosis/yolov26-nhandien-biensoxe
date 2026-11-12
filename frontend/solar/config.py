# Cấu hình dịch vụ kinh doanh thiết bị điện năng lượng mặt trời: dùng chung pháp nhân, hotline và khung trang
# với các dịch vụ còn lại của Đại Lộ Xanh; nội dung trang và danh mục thiết bị nằm ở đây.
from backend.services import account_sales as sales
from frontend.crane import config as crane_config

SERVICE_KEY = "solar"
SERVICE_SLUG = "dien-mat-troi"

LEGACY_PLACEHOLDER_NAMES = {
    "Tấm pin năng lượng mặt trời",
    "Biến tần (inverter)",
    "Pin lưu trữ điện",
    "Tủ điện, dây cáp và phụ kiện",
}

SOLAR_ITEMS = [
    {
        "id": "tam-pin-mono-550w",
        "name": "Tấm pin mặt trời Mono 550W–585W",
        "group": "Điện mặt trời",
        "grade": "Tier-1 · 550W",
        "price": 1550000,
        "unit": "tấm 550W",
        "stock": 120,
        "icon": "solar_power",
        "img": "/static/services/solar/img/solar_hero.jpg",
        "desc": "Tấm pin đơn tinh thể hiệu suất > 21,5%, công nghệ Half-cell N-Type/PERC cho mái xưởng & hộ gia đình (~2.800đ/Wp).",
        "specs": [
            "Đơn giá lẻ 1.550.000đ/tấm 550W (từ 1 pallet 36 tấm: 1.450.000đ/tấm)",
            "Bảo hành hiệu suất tuyến tính 25 năm chính hãng",
            "Đủ CO/CQ và hóa đơn VAT cho công trình",
        ],
    },
    {
        "id": "bien-tan-hybrid-5kw",
        "name": "Biến tần Inverter Hybrid / Hòa lưới 5kW",
        "group": "Điện mặt trời",
        "grade": "1 Pha · 5kW",
        "price": 14500000,
        "unit": "bộ 5kW",
        "stock": 10,
        "icon": "electrical_services",
        "img": "/static/services/solar/img/solar_hero.jpg",
        "desc": "Inverter hòa lưới 5kW từ 9.500.000đ/bộ · Biến tần Hybrid 5kW có lưu trữ dự phòng mất điện: 14.500.000đ/bộ.",
        "specs": [
            "Hòa lưới bám tải 5kW: 9.500.000đ · Dòng 10kW–50kW nhà xưởng sẵn kho",
            "Hybrid 5kW chuyển mạch dự phòng < 10ms, 2 MPPT độc lập",
            "Theo dõi sản lượng điện real-time qua app điện thoại",
        ],
    },
    {
        "id": "pin-luu-tru-lifepo4-5kwh",
        "name": "Pin lưu trữ Lithium LiFePO4 5kWh (51.2V)",
        "group": "Điện mặt trời",
        "grade": "LiFePO4 · 5kWh",
        "price": 20500000,
        "unit": "module 5kWh",
        "stock": 8,
        "icon": "battery_charging_full",
        "img": "/static/services/solar/img/solar_hero.jpg",
        "desc": "Pin lưu trữ Lithium sắt phốt phát 51.2V–100Ah (5,12kWh) an toàn chống cháy, tuổi thọ > 6.000 chu kỳ nạp xả.",
        "specs": [
            "Niêm yết 20.500.000đ/module 5kWh (bản 10kWh từ 38.000.000đ)",
            "Tương thích BMS hầu hết biến tần Hybrid phổ biến",
            "Bảo hành chính hãng 5 năm",
        ],
    },
    {
        "id": "tu-dien-ac-dc-cap-solar",
        "name": "Tủ điện AC/DC, dây cáp Solar & phụ kiện",
        "group": "Điện mặt trời",
        "grade": "2 String · Đồng bộ",
        "price": 3200000,
        "unit": "bộ đồng bộ",
        "stock": 20,
        "icon": "cable",
        "img": "/static/services/solar/img/solar_hero.jpg",
        "desc": "Tủ điện đóng cắt 2 String tích hợp chống sét lan truyền DC/AC, CB DC/AC, 30m cáp Solar 4mm² & đầu nối MC4.",
        "specs": [
            "Trọn bộ tủ điện + chống sét + dây solar cơ bản: 3.200.000đ/bộ",
            "Cung cấp khung rail nhôm, kẹp pin cắt lẻ theo số lượng mái",
            "Có xe cẩu tự hành hỗ trợ nâng tấm pin & tủ điện lên mái cao",
        ],
    },
]

PAGE = {
    "h1": ("Thiết Bị Điện", "Năng Lượng Mặt Trời"),
    "intro": (
        "Bảng giá bán trực tiếp <strong class=\"text-slate-900 font-bold\">tấm pin Mono 550W giá 1.550.000đ/tấm</strong>, "
        "<strong class=\"text-slate-900 font-bold\">biến tần 5kW từ 9,5–14,5tr</strong> và <strong class=\"text-red-700 font-bold\">pin lưu trữ 5kWh giá 20,5tr</strong> "
        "tại Vĩnh Phúc, Phú Thọ &amp; Miền Bắc — công khai giá, đủ CO/CQ &amp; VAT."
    ),
    "cta": "GỌI ĐẶT THIẾT BỊ",
    "hero_caption": "Thiết Bị Điện Mặt Trời — Giá Niêm Yết Kho",
    "hero_icon": "solar_power",
    "hero_image": "/static/services/solar/img/solar_hero.jpg",
    "catalog_kicker": "Bảng Giá Niêm Yết Trực Tiếp",
    "catalog_title": "Chọn Thiết Bị Điện Mặt Trời Theo Đơn Giá",
    "price_title": "Bảng Giá Thiết Bị Điện Mặt Trời Niêm Yết",
    "price_lead": "Đơn giá bán lẻ trực tiếp tại kho; đơn hàng từ 1 pallet tấm pin hoặc trọn bộ công trình được chiết khấu thêm.",
    "price_head": "Nhóm Thiết Bị",
    "price_badge": "Giá bán niêm yết",
    "faq_title": "Câu Hỏi Thường Gặp Về Thiết Bị Điện Mặt Trời",
    "area_title": "Thiết Bị Điện Mặt Trời Theo Khu Vực",
    "area_link": "Thiết bị điện mặt trời tại {area}",
    "region": "Vĩnh Phúc và Phú Thọ",
}


def ensure_solar_catalog() -> bool:
    """Nâng cấp danh mục điện mặt trời 0đ giữ chỗ cũ thành bảng giá niêm yết 1 lần nếu chưa có giá."""
    return sales.upgrade_placeholder_landing_items(
        SERVICE_SLUG,
        SOLAR_ITEMS,
        LEGACY_PLACEHOLDER_NAMES,
    )


def get_solar_settings() -> dict:
    """Thông tin công ty và hotline lấy từ cấu hình xe cẩu; SEO và danh mục là của dịch vụ điện mặt trời."""
    info = crane_config.get_crane_settings()
    company_name, tax_id, phone = info["company_name"], info["tax_id"], info["phone"]
    seo_config = {
        "title": f"Bảng giá thiết bị điện mặt trời tại Vĩnh Phúc & Phú Thọ — {company_name}",
        "description": (
            f"Bảng giá thiết bị điện mặt trời: tấm pin 550W từ 1.550.000đ, biến tần 5kW từ 9,5–14,5tr, pin lưu trữ 5kWh từ 20,5tr tại Vĩnh Phúc, "
            f"Phú Thọ và Miền Bắc — {company_name} (MST: {tax_id}). Đủ VAT, CO/CQ. Hotline: {phone}."
        ),
        "keywords": (
            "giá tấm pin năng lượng mặt trời, thiết bị điện mặt trời, biến tần hybrid 5kw, pin lưu trữ lifepo4, "
            f"thiết bị điện mặt trời Phú Thọ, thiết bị điện mặt trời Vĩnh Phúc, {company_name}"
        ),
        "og_image": "/static/services/solar/img/solar_hero.jpg",
    }
    return {**info, "seo_config": seo_config, "catalog_items": SOLAR_ITEMS}
