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


def get_crane_settings() -> dict:
    """Dữ liệu công ty luôn chuẩn lấy trực tiếp từ database không validate lại."""
    company = get_crane_company()

    phone = TEMPORARY_HOTLINE if TEMPORARY_HOTLINE else company["phone"].strip()
    phone_display = f"{phone[:4]} {phone[4:7]} {phone[7:]}" if len(phone) == 10 else phone
    zalo_link = f"https://zalo.me/{phone}"

    company_name = company["name"].strip()
    tax_id = company["tax_code"].strip()
    address = company["address"].strip()
    email = company["email"].strip()

    seo_config = {
        "title": f"Dịch vụ xe cẩu tự hành chuyên nghiệp tại Vĩnh Phúc & Phú Thọ — {company_name}",
        "description": f"Dịch vụ xe cẩu tự hành chuyên nghiệp tại Vĩnh Phúc, Phú Thọ và Miền Bắc — {company_name} (MST: {tax_id}). Sở hữu đội xe cẩu tự hành 8 – 10 Tấn gắn cẩu UNIC & Dongyang SS1936. Lắp dựng cầu trục, kiểm định trạm cân, cẩu máy 24/7. Hotline: {phone_display}.",
        "keywords": f"xe cẩu Vĩnh Phúc, xe cẩu Phú Thọ, thuê xe cẩu tự hành, cẩu dầm cầu trục, cẩu kiểm định trạm cân, cẩu sắt thép, {company_name}",
        "og_image": "/static/services/crane/img/crane_hero_yard.jpg",
    }

    return {
        "id": company["id"],
        "company_name": company_name,
        "brand_name": "ĐẠI LỘ XANH",
        "tax_id": tax_id,
        "address": address,
        "depot_address": DEPOT_ADDRESS,
        "email": email,
        "phone": phone_display,
        "phone_raw": phone,
        "zalo": zalo_link,
        "seo_config": seo_config,
        "fleet_items": FLEET_ITEMS,
    }


TEST_WEIGHT_ITEMS = [
    {
        "id": "qua-can-500kg",
        "name": "Quả Cân Chuẩn Gang 500KG – 1000KG",
        "grade": "Cấp chính xác M1 (Theo ĐLVN)",
        "desc": "Gang đúc nguyên khối chịu va đập, quai móc cẩu chịu lực, buồng hiệu chỉnh niêm chì. Phục vụ kiểm định trạm cân xe tải 40T – 120T.",
        "specs": ["Khối lượng: 500KG & 1000KG", "Cấp chính xác: M1", "Chuyên trạm cân ô tô, cân bồn", "Hỗ trợ trọn gói xe cẩu 8T–10T"],
        "img": "/static/services/crane/img/weights_500kg_truck_bed.jpg",
    },
    {
        "id": "qua-can-hop-go",
        "name": "Bộ Quả Cân Chuẩn Hộp Gỗ Nhung",
        "grade": "Cấp chính xác F1 / F2 (Đầy đủ tem kiểm định)",
        "desc": "Hộp gỗ tự nhiên cao cấp lót nhung chống trầy xước, khóa gài inox và kẹp gắp chuyên dụng. Dùng cho phòng thí nghiệm, KCS nhà máy.",
        "specs": ["Bộ dải: 1g – 5kg / 1kg – 20kg", "Cấp chính xác: F1, F2", "Hộp gỗ lót nhung bảo vệ", "Phòng Lab, KCS dược phẩm, điện tử"],
        "img": "/static/services/crane/img/weights_precision_wooden_boxes.jpg",
    },
    {
        "id": "qua-can-inox",
        "name": "Quả Cân Chuẩn Trụ Inox Lẻ (1KG – 20KG)",
        "grade": "Cấp chính xác F2 / M1 (Inox 304 không từ tính)",
        "desc": "Thép không gỉ Inox 304 mạ crom sáng bóng, chống oxy hóa, buồng tinh chỉnh có ren vặn niêm chì. Dùng kiểm định cân bàn, cân đếm, cân sàn.",
        "specs": ["Khối lượng: 1kg, 2kg, 5kg, 10kg, 20kg", "Inox 304 không từ tính", "Chống ăn mòn tuyệt đối", "Cân bàn, cân sàn công nghiệp"],
        "img": "/static/services/crane/img/weights_lab_calibration_set.jpg",
    },
    {
        "id": "kiem-dinh-tron-goi",
        "name": "Dịch Vụ Trọn Gói Kiểm Định Trạm Cân",
        "grade": "Xe Cẩu Tự Hành + Quả Cân + Thợ Lái",
        "desc": "Cung cấp trọn gói từ khâu vận chuyển 20–40 quả cân 500kg đến tận trạm cân, thợ cẩu dày dạn kinh nghiệm phối hợp với đoàn kiểm định đo lường.",
        "specs": ["Trọn gói: Xe cẩu + 20-40 quả cân 500kg", "Có mặt sau 15–30 phút", "Phối hợp kỹ thuật viên đo lường", "Xuất hóa đơn VAT & BB bàn giao"],
        "img": "/static/services/crane/img/weights_truck_scale_outdoor.jpg",
    },
]


def get_test_weights_settings() -> dict:
    """Cấu hình chuyên biệt cho dịch vụ cho thuê quả cân chuẩn kiểm định."""
    company = get_crane_company()

    phone = TEMPORARY_HOTLINE if TEMPORARY_HOTLINE else company["phone"].strip()
    phone_display = f"{phone[:4]} {phone[4:7]} {phone[7:]}" if len(phone) == 10 else phone
    zalo_link = f"https://zalo.me/{phone}"

    company_name = company["name"].strip()
    tax_id = company["tax_code"].strip()
    address = company["address"].strip()
    email = company["email"].strip()

    seo_config = {
        "title": f"Cho thuê quả cân chuẩn kiểm định cân điện tử 1kg – 1000kg tại Vĩnh Phúc & Phú Thọ — {company_name}",
        "description": f"Dịch vụ cho thuê quả cân chuẩn kiểm định cấp chính xác F1, F2, M1 từ 1kg đến 1000kg tại Vĩnh Phúc, Phú Thọ và Miền Bắc — {company_name} (MST: {tax_id}). Hỗ trợ xe cẩu tự hành chở và cẩu định vị hàng chục quả cân 500kg kiểm định trạm cân xe tải 60–120 tấn. Đầy đủ hóa đơn VAT, chứng chỉ đo lường. Hotline: {phone_display}.",
        "keywords": f"cho thuê quả cân, quả cân chuẩn, quả cân 500kg, quả cân kiểm định cân điện tử, kiểm định trạm cân xe tải, quả cân F1 F2 M1, {company_name}",
        "og_image": "/static/services/crane/img/weights_truck_scale_outdoor.jpg",
    }

    return {
        "id": company["id"],
        "company_name": company_name,
        "brand_name": "ĐẠI LỘ XANH",
        "tax_id": tax_id,
        "address": address,
        "depot_address": DEPOT_ADDRESS,
        "email": email,
        "phone": phone_display,
        "phone_raw": phone,
        "zalo": zalo_link,
        "seo_config": seo_config,
        "weight_items": TEST_WEIGHT_ITEMS,
    }


