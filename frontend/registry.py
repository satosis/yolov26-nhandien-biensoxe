"""Các dịch vụ công khai của Đại Lộ Xanh.

Mỗi dịch vụ khai báo một lần ở đây; menu, chân trang, sitemap và biểu mẫu báo giá đều đọc từ danh sách này.
Thêm dịch vụ mới = thêm một dòng vào SERVICE_PAGES (và nội dung trang ở frontend/<tên>/),
không phải sửa từng template.
"""
from frontend.crane.locations import AREAS

SERVICE_PAGES = (
    {
        "key": "crane", "path": "/xe-cau", "area_service": "thue-xe-cau", "icon": "local_shipping",
        "label": "Xe Cẩu 8T–10T", "short": "Xe Cẩu", "mobile": "Xe Cẩu 8T–10T", "footer": "Dịch vụ Xe Cẩu",
        "area_label": "Khu Vực Phục Vụ", "tagline": "Xe Cẩu Tự Hành 24/7", "gallery": True,
        "anchor": "Đội Xe", "anchor_target": "doi-xe", "anchor_mobile": "2 Dòng Xe Cẩu Chủ Lực",
    },
    {
        "key": "weights", "path": "/qua-can", "area_service": "van-chuyen-qua-can", "icon": "scale",
        "label": "Quả Cân Chuẩn", "short": "Quả Cân", "mobile": "Cho Thuê Quả Cân", "footer": "Cho Thuê Quả Cân",
        "tagline": "Quả Cân Chuẩn ĐLVN · Xe Cẩu 24/7", "gallery": True,
        "anchor": "Sản Phẩm", "anchor_target": "danh-muc", "anchor_mobile": "Danh Mục Quả Cân",
    },
    {
        "key": "container", "path": "/thue-container", "area_service": "thue-container", "icon": "inventory_2",
        "label": "Cho Thuê Container", "short": "Container", "mobile": "Cho Thuê Container",
        "footer": "Cho Thuê Container", "tsl_label": "Cho thuê container", "tsl_menu": True,
        "tagline": "Cho Thuê Container · Xe Cẩu 24/7", "gallery": False, "crane_footer": False,
        "anchor": "Loại Container", "anchor_target": "danh-muc", "anchor_mobile": "Các Loại Container",
    },
    {
        "key": "solar", "path": "/dien-mat-troi", "area_service": "dien-mat-troi", "icon": "solar_power",
        "label": "Điện Mặt Trời", "short": "Điện Mặt Trời", "mobile": "Thiết Bị Điện Mặt Trời",
        "footer": "Thiết Bị Điện Mặt Trời", "tsl_label": "Thiết bị điện mặt trời", "tsl_menu": True,
        "tagline": "Thiết Bị Điện Mặt Trời", "gallery": False, "crane_footer": False,
        "anchor": "Nhóm Thiết Bị", "anchor_target": "danh-muc", "anchor_mobile": "Các Nhóm Thiết Bị",
    },
    {
        "key": "photo", "path": "/thue-may-anh", "area_service": "thue-may-anh", "icon": "photo_camera",
        "label": "Máy Ảnh & Photobooth", "short": "Máy Ảnh", "mobile": "Cho Thuê Máy Ảnh & Photobooth",
        "footer": "Cho Thuê Máy Ảnh & Photobooth",
        "tagline": "Cho Thuê Máy Ảnh & Photobooth", "gallery": False, "crane_footer": False,
        "anchor": "Thiết Bị", "anchor_target": "danh-muc", "anchor_mobile": "Thiết Bị Cho Thuê",
    },
)

# Biểu mẫu báo giá của từng dịch vụ; trang không khai báo thì dùng chữ của dịch vụ xe cẩu trong template.
CONTACT_FORMS = {
    "container": {
        "heading": "Đặt Thuê Container Theo Giá Niêm Yết",
        "badge": "Giá Công Khai Từ 1,5tr/tháng · Không Trung Gian",
        "intro": "Chọn loại container văn phòng hoặc container kho theo đơn giá niêm yết ở trên rồi gửi thông tin để chốt lịch giao & cẩu hạ tận nơi.",
        "submit": "XÁC NHẬN ĐẶT THUÊ CONTAINER",
        "form_lead": "Kiểm tra container trống & báo cước cẩu hạ theo vị trí đặt.",
        "hotline_label": "Hotline Điều Vận Container",
        "aside_title": "Cần Thuê Container Gấp? Gọi Ngay",
        "form_title": "Điền Thông Tin Đặt Thuê Container",
        "demand_label": "Loại Container & Giá Tham Chiếu",
        "demand_options": (
            ("Container văn phòng 20ft (có điều hòa) — 2.500.000đ / tháng", "Container văn phòng 20ft — 2.500.000đ/tháng"),
            ("Container văn phòng 40ft (rộng 30m²) — 4.200.000đ / tháng", "Container văn phòng 40ft — 4.200.000đ/tháng"),
            ("Container kho 20ft (bản 40ft: 2,6tr/tháng) — 1.500.000đ / tháng", "Container kho 20ft (1,5tr) / Kho 40ft (2,6tr/tháng)"),
            ("Cẩu hạ & vận chuyển Container 2 chiều — 1.800.000đ / chuyến", "Cẩu hạ & vận chuyển 2 chiều — từ 1.800.000đ/chuyến"),
            ("Cần tư vấn chọn kích thước container theo mặt bằng", "Chưa rõ kích thước, cần tư vấn nhanh"),
        ),
        "cta_mobile": "GỌI NGAY",
    },
    "solar": {
        "heading": "Đặt Mua Thiết Bị Điện Mặt Trời Giá Kho",
        "badge": "Giá Bán Công Khai · Đủ CO/CQ & VAT",
        "intro": "Chọn nhóm tấm pin 550W, biến tần Hybrid hoặc pin lưu trữ LiFePO4 từ bảng giá ở trên để giữ hàng tại kho và nhận chiết khấu theo số lượng.",
        "submit": "GỬI ĐƠN GIỮ HÀNG GIÁ NIÊM YẾT",
        "form_lead": "Xác nhận tồn kho & báo chiết khấu theo số lượng / pallet.",
        "hotline_label": "Hotline Thiết Bị Điện Mặt Trời",
        "aside_title": "Cần Báo Chiết Khấu Công Trình? Gọi Ngay",
        "form_title": "Thông Tin Đặt Mua Thiết Bị Điện Mặt Trời",
        "demand_label": "Thiết Bị & Đơn Giá Niêm Yết",
        "demand_options": (
            ("Tấm pin mặt trời Mono 550W–585W — 1.550.000đ / tấm 550W", "Tấm pin Mono 550W–585W — 1.550.000đ/tấm"),
            ("Biến tần Inverter Hybrid / Hòa lưới 5kW — 14.500.000đ / bộ 5kW", "Biến tần Hybrid 5kW — 14.500.000đ/bộ (hòa lưới từ 9,5tr)"),
            ("Pin lưu trữ Lithium LiFePO4 5kWh (51.2V) — 20.500.000đ / module 5kWh", "Pin lưu trữ LiFePO4 5kWh — 20.500.000đ/module"),
            ("Tủ điện AC/DC, dây cáp Solar & phụ kiện — 3.200.000đ / bộ đồng bộ", "Tủ điện AC/DC & phụ kiện — 3.200.000đ/bộ"),
            ("Combo trọn bộ Hybrid 5kWp + pin 5kWh hoặc lấy số lượng pallet", "Tư vấn combo trọn bộ / giá sỉ theo pallet"),
        ),
        "cta_mobile": "GỌI NGAY",
    },
    "photo": {
        "heading": "Đặt Lịch Giữ Máy & Chốt Gói Photobooth",
        "badge": "Giá Niêm Yết Công Khai · Không Phí Ẩn",
        "intro": "Chọn thiết bị hoặc gói Photobooth từ bảng giá niêm yết ở trên rồi gửi thông tin để giữ máy đúng lịch hẹn (phản hồi trong 5–15 phút).",
        "submit": "XÁC NHẬN ĐẶT LỊCH THUÊ NGAY",
        "form_lead": "Kiểm tra lịch trống & giữ thiết bị theo đúng đơn giá niêm yết trên trang.",
        "hotline_label": "Hotline Giữ Lịch Studio & Sự Kiện",
        "aside_title": "Cần Giao Máy Gấp? Gọi Hotline Ngay",
        "form_title": "Thông Tin Đặt Thuê Thiết Bị / Photobooth",
        "demand_label": "Thiết Bị / Gói Photobooth Đã Chọn",
        "demand_options": (
            ("Sony Alpha A7 IV (Body) — 490.000đ/ngày", "Sony Alpha A7 IV (Body) — 490.000đ/ngày"),
            ("Sony Alpha A7 III (Body) — 380.000đ/ngày", "Sony Alpha A7 III (Body) — 380.000đ/ngày"),
            ("Canon EOS R6 Mark II (Body) — 550.000đ/ngày", "Canon EOS R6 Mark II (Body) — 550.000đ/ngày"),
            ("Fujifilm X100VI (Compact 40MP) — 550.000đ/ngày", "Fujifilm X100VI (Compact 40MP) — 550.000đ/ngày"),
            ("Combo Body + Lens 24-70mm f/2.8 + Flash", "Combo Sự Kiện (Body + 24-70mm f/2.8 + Flash)"),
            ("Photobooth Classic · Gói Mini 2 Giờ — 4.200.000đ", "Photobooth Gói Mini 2 Giờ — 4.200.000đ"),
            ("Photobooth Tiệc Cưới & Gala · Gói 3 Giờ — 5.600.000đ", "Photobooth Gói Tiệc Cưới 3 Giờ — 5.600.000đ"),
            ("Photobooth Doanh Nghiệp · Gói 4 Giờ (+VAT) — 6.800.000đ", "Photobooth Gói Doanh Nghiệp 4 Giờ — 6.800.000đ"),
            ("360 Video Booth / Mirror Booth 3 Giờ — 8.500.000đ", "360 Video Booth / Mirror Booth 3h — 8.500.000đ"),
        ),
        "cta_mobile": "GỌI GIỮ MÁY",
    },
    "weights": {
        "heading": "Đặt Thuê Quả Cân & Lịch Kiểm Định Trạm Cân",
        "badge": "Giá Công Khai Từ 250k · Trọn Gói Trạm Từ 4,5tr",
        "intro": "Chọn loại quả cân F1/F2/M1 hoặc gói xe cẩu kiểm định trạm cân theo bảng giá niêm yết ở trên để chốt lịch điều xe đúng giờ đoàn đo lường.",
        "submit": "XÁC NHẬN ĐẶT LỊCH QUẢ CÂN",
        "form_lead": "Xác nhận số lượng quả cân & sắp xếp xe cẩu 8T–10T có mặt sau 15–30 phút.",
        "hotline_label": "Hotline Điều Quả Cân & Xe Cẩu",
        "aside_title": "Cần Kiểm Định Trạm Cân Gấp? Gọi Ngay",
        "form_title": "Thông Tin Đặt Thuê Quả Cân Chuẩn",
        "demand_label": "Loại Quả Cân & Đơn Giá Niêm Yết",
        "demand_options": (
            ("Quả Cân Chuẩn Gang 500KG – 1000KG — 400.000đ / quả / đợt", "Quả cân gang 500KG – 1000KG — 400.000đ/quả/đợt"),
            ("Bộ Quả Cân Chuẩn Hộp Gỗ Nhung — 600.000đ / bộ / ngày", "Bộ quả cân hộp gỗ nhung F1/F2 — 600.000đ/bộ/ngày"),
            ("Quả Cân Chuẩn Trụ Inox Lẻ (1KG – 20KG) — 250.000đ / quả / ngày", "Quả cân inox lẻ 1KG–20KG — 250.000đ/quả/ngày"),
            ("Dịch Vụ Trọn Gói Kiểm Định Trạm Cân — 4.500.000đ / trọn gói trạm", "Trọn gói xe cẩu + 20–40 quả 500kg — từ 4.500.000đ/trạm"),
            ("Tư vấn số lượng quả cân phù hợp cho trạm cân 40T – 120T", "Cần tư vấn số lượng quả & lịch kiểm định"),
        ),
        "cta_mobile": "GỌI NGAY",
    },
    "install": {
        "heading": "Yêu Cầu Báo Giá Cẩu Lắp Đặt Thiết Bị",
        "badge": "Báo Giá Cẩu Lắp Đặt",
        "intro": "Liên hệ để được tư vấn và báo giá theo hiện trường và trọng lượng thiết bị.",
        "submit": "GỬI YÊU CẦU BÁO GIÁ",
        "form_lead": "Chúng tôi sẽ liên hệ lại để báo giá.",
        "aside_title": "Cần Cẩu Lắp Đặt Thiết Bị? Gọi Ngay",
        "form_title": "Điền Thông Tin Yêu Cầu Cẩu Lắp Đặt",
        "demand_label": "Hạng Mục Cẩu Lắp Đặt",
        "demand_options": (
            ("Cẩu hạ và định vị máy móc thiết bị nhà xưởng", "Cẩu hạ, định vị máy móc thiết bị"),
            ("Cẩu hạ và lắp đặt container văn phòng", "Cẩu hạ, lắp đặt container văn phòng"),
            ("Di dời máy móc thiết bị trong nhà xưởng", "Di dời máy móc thiết bị trong nhà xưởng"),
            ("Cẩu nâng hạ hàng nặng trong không gian hẹp", "Cẩu nâng hạ hàng nặng trong không gian hẹp"),
            ("Cần tư vấn cẩu lắp đặt thiết bị", "Chưa rõ hạng mục, cần tư vấn"),
        ),
    },
}

SERVICE_AREAS = tuple({"slug": slug, "name": data["name"]} for slug, data in AREAS.items())


def install_template_globals(env) -> None:
    """Cho mọi template của trang công khai đọc được danh sách dịch vụ, khu vực và biểu mẫu báo giá."""
    env.globals["service_pages"] = SERVICE_PAGES
    env.globals["service_areas"] = SERVICE_AREAS
    env.globals["contact_forms"] = CONTACT_FORMS


def page_for(area_service: str) -> dict:
    """Mục của SERVICE_PAGES ứng với khóa dịch vụ theo khu vực (ví dụ "thue-container")."""
    return next((page for page in SERVICE_PAGES if page["area_service"] == area_service), {})
