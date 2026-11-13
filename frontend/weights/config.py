# Cấu hình chuyên biệt cho dịch vụ cho thuê quả cân chuẩn: cùng pháp nhân, hotline và trang web với dịch vụ xe cẩu (Đại Lộ Xanh).
from backend.services import account_sales as sales
from frontend.crane.config import crane_profile

SERVICE_KEY = "weights"
SERVICE_SLUG = "qua-can"

LEGACY_PLACEHOLDER_NAMES = {
    "Quả Cân Chuẩn Gang 500KG – 1000KG",
    "Bộ Quả Cân Chuẩn Hộp Gỗ Nhung",
    "Quả Cân Chuẩn Trụ Inox Lẻ (1KG – 20KG)",
    "Dịch Vụ Trọn Gói Kiểm Định Trạm Cân",
}

TEST_WEIGHT_ITEMS = [
    {
        "id": "qua-can-500kg",
        "name": "Quả Cân Chuẩn Gang 500KG – 1000KG",
        "group": "Quả cân",
        "grade": "Cấp M1 (ĐLVN)",
        "price": 400000,
        "unit": "quả / đợt",
        "stock": 40,
        "icon": "scale",
        "desc": "Gang đúc nguyên khối chịu va đập, quai móc cẩu chịu lực, buồng hiệu chỉnh niêm chì. Kiểm định trạm cân xe tải 40T – 120T.",
        "specs": [
            "Đơn giá niêm yết 400.000đ/quả/đợt (thuê từ 20 quả còn 350.000đ/quả)",
            "Cấp chính xác M1 còn hạn kiểm định Quatest",
            "Sẵn 40+ quả tại kho · Hỗ trợ ghép xe cẩu 8T–10T",
        ],
        "img": "/upload/services/crane/img/weights_500kg_truck_bed.jpg",
    },
    {
        "id": "qua-can-hop-go",
        "name": "Bộ Quả Cân Chuẩn Hộp Gỗ Nhung",
        "group": "Quả cân",
        "grade": "Cấp F1 / F2 (Có tem)",
        "price": 600000,
        "unit": "bộ / ngày",
        "stock": 8,
        "icon": "inventory",
        "desc": "Hộp gỗ tự nhiên lót nhung đỏ chống trầy xước, kèm kẹp gắp chuyên dụng cho phòng thí nghiệm, KCS nhà máy.",
        "specs": [
            "Bộ dải 1g – 5kg hoặc 1kg – 20kg · Giá thuê 600.000đ/bộ/ngày",
            "Đủ tem và giấy hiệu chuẩn cấp F1, F2 hợp lệ",
            "Thuê từ 3 ngày giảm 20% đơn giá",
        ],
        "img": "/upload/services/crane/img/weights_precision_wooden_boxes.jpg",
    },
    {
        "id": "qua-can-inox",
        "name": "Quả Cân Chuẩn Trụ Inox Lẻ (1KG – 20KG)",
        "group": "Quả cân",
        "grade": "Cấp F2 / M1 (Inox 304)",
        "price": 250000,
        "unit": "quả / ngày",
        "stock": 30,
        "icon": "fitness_center",
        "desc": "Thép không gỉ Inox 304 không từ tính, buồng tinh chỉnh niêm chì chuẩn mực để kiểm định cân bàn, cân đếm, cân sàn.",
        "specs": [
            "Giá thuê 250.000đ/quả/ngày (thuê bộ từ 5 quả còn 200.000đ/quả)",
            "Đủ mức khối lượng: 1kg, 2kg, 5kg, 10kg, 20kg",
            "Giao nhận nhanh tại KCN Phú Thọ & Vĩnh Phúc",
        ],
        "img": "/upload/services/crane/img/weights_lab_calibration_set.jpg",
    },
    {
        "id": "kiem-dinh-tron-goi",
        "name": "Dịch Vụ Trọn Gói Kiểm Định Trạm Cân",
        "group": "Quả cân",
        "grade": "Xe Cẩu 10T + Quả Cân",
        "price": 4500000,
        "unit": "trọn gói trạm",
        "stock": 2,
        "icon": "local_shipping",
        "desc": "Trọn gói chở 20–40 quả cân 500kg đến trạm cân, thợ cẩu chính chủ hạ quả đúng góc tải phối hợp đoàn đo lường.",
        "specs": [
            "Gói tiêu chuẩn trạm 60T–80T: từ 4.500.000đ (trạm 100T–120T: 6.500.000đ)",
            "Có mặt sau 15–30 phút tại Vĩnh Phúc, Phú Thọ",
            "Xuất hóa đơn VAT & biên bản bàn giao đầy đủ",
        ],
        "img": "/upload/services/crane/img/weights_truck_scale_outdoor.jpg",
    },
]


def ensure_weights_catalog() -> bool:
    """Nâng cấp danh mục quả cân 0đ giữ chỗ cũ thành bảng giá niêm yết 1 lần nếu chưa có giá."""
    return sales.upgrade_placeholder_landing_items(
        SERVICE_SLUG,
        TEST_WEIGHT_ITEMS,
        LEGACY_PLACEHOLDER_NAMES,
    )


def get_test_weights_settings() -> dict:
    """Cấu hình chuyên biệt cho dịch vụ cho thuê quả cân chuẩn kiểm định."""
    profile = crane_profile()
    company_name, tax_id, phone_display = profile["company_name"], profile["tax_id"], profile["phone"]

    seo_config = {
        "title": f"Bảng giá thuê quả cân chuẩn từ 250k & gói trạm cân 4,5tr — {company_name}",
        "description": f"Bảng giá thuê quả cân chuẩn kiểm định cấp F1, F2, M1 tại Vĩnh Phúc, Phú Thọ — {company_name} (MST: {tax_id}). Quả cân Inox từ 250k/ngày, gang 500kg 400k/quả, trọn gói xe cẩu trạm cân từ 4,5 triệu. Hotline: {phone_display}.",
        "keywords": f"giá thuê quả cân, quả cân chuẩn, quả cân 500kg, quả cân kiểm định cân điện tử, kiểm định trạm cân xe tải, quả cân F1 F2 M1, {company_name}",
        "og_image": "/upload/services/crane/img/weights_truck_scale_outdoor.jpg",
    }

    return {**profile, "seo_config": seo_config, "weight_items": TEST_WEIGHT_ITEMS}
