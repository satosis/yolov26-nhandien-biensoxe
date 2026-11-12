# Cấu hình chuyên biệt cho Dịch vụ Xe cẩu — CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH
from backend.services.database import db

SERVICE_KEY = "crane"
DEPOT_ADDRESS = "Bãi xe Phú Thọ: Khu Đồng Dâu, KCN Cổ Tiết / Vĩnh Phúc: Tề Lỗ, Yên Lạc"

FLEET_ITEMS = [
    {
        "name": "Hyundai HD210 · Cần UNIC",
        "model": "Sức nâng 8 tấn · Thùng dài 8.5m",
        "type": "Xe cẩu tự hành thùng dài 8.5m",
        "img": "/static/services/crane/img/crane_truck_88c_unic.jpg",
    },
    {
        "name": "Hyundai HD210 · Cần Dongyang",
        "model": "Sức nâng 10 tấn · Cần vươn 19.8m",
        "type": "Xe cẩu tự hành tải nặng chuyên dụng",
        "img": "/static/services/crane/img/crane_truck_88h_dongyang.jpg",
    },
]


def get_crane_company() -> dict:
    """Lấy trực tiếp thông tin đơn vị Đại Lộ Xanh từ bảng invoice_companies theo MST 2500774911."""
    return db._execute_query(
        "SELECT id, name, tax_code, phone, email, address FROM public.invoice_companies WHERE tax_code = '2500774911' OR id = 33 LIMIT 1",
        fetch=True,
    )


# Tạm thời fix cứng số hotline theo yêu cầu (sau này sẽ dùng lại company["phone"])
TEMPORARY_HOTLINE = "0919497568"


def crane_profile() -> dict:
    """The company details every crane page shows, straight from the database."""
    company = get_crane_company()
    phone = TEMPORARY_HOTLINE if TEMPORARY_HOTLINE else company["phone"].strip()
    return {
        "id": company["id"],
        "company_name": company["name"].strip(),
        "brand_name": "ĐẠI LỘ XANH",
        "tax_id": company["tax_code"].strip(),
        "address": company["address"].strip(),
        "depot_address": DEPOT_ADDRESS,
        "email": company["email"].strip(),
        "phone": f"{phone[:4]} {phone[4:7]} {phone[7:]}" if len(phone) == 10 else phone,
        "phone_raw": phone,
        "zalo": f"https://zalo.me/{phone}",
    }


def get_crane_settings() -> dict:
    """Dữ liệu công ty luôn chuẩn lấy trực tiếp từ database không validate lại."""
    profile = crane_profile()
    company_name, tax_id, phone_display = profile["company_name"], profile["tax_id"], profile["phone"]

    seo_config = {
        "title": f"Dịch vụ xe cẩu tự hành chuyên nghiệp tại Vĩnh Phúc & Phú Thọ — {company_name}",
        "description": f"Dịch vụ xe cẩu tự hành chuyên nghiệp tại Vĩnh Phúc, Phú Thọ và Miền Bắc — {company_name} (MST: {tax_id}). Sở hữu đội xe cẩu tự hành 8 – 10 Tấn gắn cẩu UNIC & Dongyang SS1936. Lắp dựng cầu trục, kiểm định trạm cân, cẩu máy 24/7. Hotline: {phone_display}.",
        "keywords": f"xe cẩu Vĩnh Phúc, xe cẩu Phú Thọ, thuê xe cẩu tự hành, cẩu dầm cầu trục, cẩu kiểm định trạm cân, cẩu sắt thép, {company_name}",
        "og_image": "/static/services/crane/img/crane_hero_yard.jpg",
    }

    return {**profile, "seo_config": seo_config, "fleet_items": FLEET_ITEMS}
