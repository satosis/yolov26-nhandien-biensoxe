# Cấu hình dịch vụ cho thuê máy ảnh và photobooth: dùng chung pháp nhân và hotline
# với hệ thống Đại Lộ Xanh; dữ liệu thiết bị, giá thuê theo ngày & gói Photobooth đồng bộ cùng account_products.
from backend.services import account_sales as sales
from frontend.crane import config as crane_config

SERVICE_KEY = "photo"
SERVICE_SLUG = "thue-may-anh"

# Tên giữ chỗ cũ trước khi nâng cấp lên danh mục có giá niêm yết chi tiết
LEGACY_PLACEHOLDER_NAMES = {
    "Máy ảnh mirrorless và DSLR",
    "Ống kính",
    "Chân máy, đèn và phụ kiện",
    "Photobooth sự kiện",
}

# Quy tắc giảm giá thuê nhiều ngày áp dụng cho máy ảnh, ống kính & phụ kiện (tính theo ngày 24h)
# Tham khảo chính sách thực tế thị trường cho thuê Hà Nội / Miền Bắc:
# Ngày 1: 100%, Ngày 2: -20%, Ngày 3: -25%, Ngày 4: -30%, Từ ngày 5 trở đi: -35%..-50%
DAY_RATE_FACTORS = (1.0, 0.80, 0.75, 0.70, 0.65, 0.60, 0.55)

MULTI_DAY_POLICY = [
    {"day": "Ngày 1 (24h)", "discount": "100% giá niêm yết", "note": "Giao đủ 2 pin, sạc, thẻ nhớ & túi"},
    {"day": "Ngày thứ 2", "discount": "Giảm 20%", "note": "Còn 80% giá ngày đầu"},
    {"day": "Ngày thứ 3", "discount": "Giảm 25%", "note": "Còn 75% giá ngày đầu"},
    {"day": "Ngày thứ 4", "discount": "Giảm 30%", "note": "Còn 70% giá ngày đầu"},
    {"day": "Từ ngày thứ 5+", "discount": "Giảm 35% – 50%", "note": "Hợp đồng dài ngày & VAT cho doanh nghiệp"},
]


def calc_rental_total(daily_price: int, days: int) -> int:
    """Tính tổng tiền thuê cho N ngày theo bậc giảm giá minh bạch."""
    if daily_price <= 0 or days <= 0:
        return 0
    total = 0.0
    for idx in range(days):
        factor = DAY_RATE_FACTORS[idx] if idx < len(DAY_RATE_FACTORS) else 0.50
        total += daily_price * factor
    return int(round(total / 1000.0) * 1000)


PHOTO_ITEMS = [
    # ══════════════════════════════════════════════
    # 1. MÁY ẢNH MIRRORLESS, DSLR & COMPACT / VLOG
    # ══════════════════════════════════════════════
    {
        "id": "sony-a7iv",
        "name": "Sony Alpha A7 IV (Body)",
        "group": "Máy ảnh",
        "grade": "Sony",
        "price": 490000,
        "stock": 3,
        "icon": "photo_camera",
        "img": "/static/services/photo/img/gear_cameras_lenses.jpg",
        "desc": "Mirrorless Full-frame 33MP thế hệ mới, lấy nét mắt AI cực nhanh, quay 4K 60fps 10-bit S-Log3.",
        "specs": [
            "Kèm 2 pin NP-FZ100, sạc, thẻ SD V60 128GB & túi",
            "Ngày 2: 392.000đ (-20%) · Từ ngày 3: 368.000đ/ngày",
            "Cọc CCCD gắn chip + 5.000.000đ (hoặc tài sản tương đương)",
        ],
    },
    {
        "id": "sony-a7iii",
        "name": "Sony Alpha A7 III (Body)",
        "group": "Máy ảnh",
        "grade": "Sony",
        "price": 380000,
        "stock": 4,
        "icon": "photo_camera",
        "img": "/static/services/photo/img/gear_cameras_lenses.jpg",
        "desc": "Body Full-frame 24MP quốc dân cho chụp sự kiện, phóng sự cưới, kỷ yếu & quay 4K thiếu sáng.",
        "specs": [
            "Kèm 2 pin NP-FZ100, sạc đôi, thẻ 128GB & dây đeo",
            "Ngày 2: 304.000đ (-20%) · Ngày 3: 285.000đ (-25%)",
            "Cọc CCCD gắn chip + 3.500.000đ",
        ],
    },
    {
        "id": "sony-a7c",
        "name": "Sony Alpha A7C (Body gọn nhẹ)",
        "group": "Máy ảnh",
        "grade": "Sony",
        "price": 390000,
        "stock": 2,
        "icon": "photo_camera",
        "img": "/static/services/photo/img/gear_cameras_lenses.jpg",
        "desc": "Full-frame nhỏ gọn có màn hình xoay lật cảm ứng, tối ưu du lịch, vlog và gắn gimbal RS3.",
        "specs": [
            "Kèm 2 pin NP-FZ100, thẻ nhớ 128GB & túi đeo chéo",
            "Ngày 2: 312.000đ (-20%) · Chuyển ảnh Wi-Fi sang ĐT",
            "Cọc CCCD gắn chip + 3.500.000đ",
        ],
    },
    {
        "id": "sony-a6400",
        "name": "Sony Alpha A6400 (Body Crop 4K)",
        "group": "Máy ảnh",
        "grade": "Sony",
        "price": 250000,
        "stock": 4,
        "icon": "photo_camera",
        "img": "/static/services/photo/img/gear_cameras_lenses.jpg",
        "desc": "Gọn nhẹ dễ dùng, Real-time Eye AF bắt nét cực nhạy cho du lịch, vlog, chụp áo dài & kỷ yếu.",
        "specs": [
            "Kèm 2 pin, sạc, thẻ SD 64GB & đầu đọc thẻ nhanh",
            "Ngày 2: 200.000đ (-20%) · Ghép Sigma 16/56mm chỉ +150k",
            "Cọc CCCD gắn chip + 2.000.000đ (HSSV hỗ trợ giảm cọc)",
        ],
    },
    {
        "id": "sony-zv-e10",
        "name": "Sony ZV-E10 (Chuyên Vlog & Livestream)",
        "group": "Máy ảnh",
        "grade": "Sony",
        "price": 220000,
        "stock": 3,
        "icon": "photo_camera",
        "img": "/static/services/photo/img/gear_cameras_lenses.jpg",
        "desc": "Quay video 4K sắc nét, micro định hướng kèm lọc gió, cắm cáp livestream trực tiếp không cần card.",
        "specs": [
            "Kèm 2 pin, lọc gió Deadcat, thẻ 64GB & cáp Type-C",
            "Ngày 2: 176.000đ (-20%) · Ngày 3: 165.000đ (-25%)",
            "Cọc CCCD gắn chip + 2.000.000đ",
        ],
    },
    {
        "id": "canon-r6-ii",
        "name": "Canon EOS R6 Mark II (Body)",
        "group": "Máy ảnh",
        "grade": "Canon",
        "price": 550000,
        "stock": 2,
        "icon": "photo_camera",
        "img": "/static/services/photo/img/gear_cameras_lenses.jpg",
        "desc": "Flagship chụp tiệc cưới & sân khấu tốc độ cao 40fps, quay 4K 60p không crop, màu da nịnh mắt.",
        "specs": [
            "Kèm 2 pin LP-E6NH zin, sạc, thẻ UHS-II 128GB",
            "Ngày 2: 440.000đ (-20%) · Tặng mượn ngàm EF–EOS R",
            "Cọc CCCD gắn chip + 5.500.000đ",
        ],
    },
    {
        "id": "canon-r8",
        "name": "Canon EOS R8 (Full-frame 4K)",
        "group": "Máy ảnh",
        "grade": "Canon",
        "price": 400000,
        "stock": 3,
        "icon": "photo_camera",
        "img": "/static/services/photo/img/gear_cameras_lenses.jpg",
        "desc": "Full-frame 24.2MP siêu nhẹ hệ RF, thừa hưởng AF thông minh từ R6 II, tông da hồng hào chuẩn Canon.",
        "specs": [
            "Kèm 3 pin LP-E17, sạc đôi, thẻ 128GB & túi đựng",
            "Ngày 2: 320.000đ (-20%) · Có ngàm EF–EOS R kèm theo",
            "Cọc CCCD gắn chip + 3.500.000đ",
        ],
    },
    {
        "id": "canon-r50-kit",
        "name": "Canon EOS R50 + Lens Kit 18-45mm",
        "group": "Máy ảnh",
        "grade": "Canon",
        "price": 250000,
        "stock": 4,
        "icon": "photo_camera",
        "img": "/static/services/photo/img/gear_cameras_lenses.jpg",
        "desc": "Combo sẵn sàng chụp cho người mới, du lịch, kỷ yếu & review — chụp đẹp ngay file JPEG không cần hậu kỳ.",
        "specs": [
            "Trọn bộ Body + Kit + 2 pin + thẻ 64GB + túi đeo",
            "Ngày 2: 200.000đ (-20%) · Thuê 3 ngày chỉ 638.000đ",
            "Hướng dẫn cài đặt thông số cơ bản 5 phút khi nhận máy",
        ],
    },
    {
        "id": "fujifilm-x-t4",
        "name": "Fujifilm X-T4 (Body Film Simulation)",
        "group": "Máy ảnh",
        "grade": "Fujifilm",
        "price": 380000,
        "stock": 2,
        "icon": "photo_camera",
        "img": "/static/services/photo/img/gear_cameras_lenses.jpg",
        "desc": "Thiết kế cổ điển kèm chống rung 5 trục IBIS, 18 giả lập màu phim Classic Neg / Eterna chụp ăn ngay.",
        "specs": [
            "Kèm 2 pin NP-W235, sạc đôi, thẻ 128GB & dây da",
            "Cài sẵn công thức màu phim đẹp cho ngoại cảnh & tiệc",
            "Ngày 2: 304.000đ (-20%) · Cọc CCCD + 3.000.000đ",
        ],
    },
    {
        "id": "fujifilm-x100vi",
        "name": "Fujifilm X100VI (Compact 40MP Film)",
        "group": "Máy ảnh",
        "grade": "Fujifilm",
        "price": 550000,
        "stock": 2,
        "icon": "photo_camera",
        "img": "/static/services/photo/img/gear_cameras_lenses.jpg",
        "desc": "Siêu phẩm compact du lịch & lifestyle 40MP chống rung IBIS, ống kính 23mm f/2 & 20 màu phim Kodak/Fuji.",
        "specs": [
            "Kèm filter UV bảo vệ, hood vuông, 2 pin & thẻ 128GB",
            "Xuất ảnh JPEG thẳng về iPhone/Android qua app XApp",
            "Ngày 2: 440.000đ (-20%) · Đặt trước giữ lịch cuối tuần",
        ],
    },
    {
        "id": "dji-pocket-3",
        "name": "DJI Osmo Pocket 3 Creator Combo",
        "group": "Máy ảnh",
        "grade": "DJI",
        "price": 220000,
        "stock": 3,
        "icon": "videocam",
        "img": "/static/services/photo/img/gear_lighting_tripod.jpg",
        "desc": "Camera gimbal bỏ túi cảm biến 1 inch quay 4K 120fps siêu mượt, kèm sẵn micro không dây DJI Mic 2.",
        "specs": [
            "Full Creator Combo: DJI Mic 2, pin nối dài, lens góc rộng",
            "Tặng kèm thẻ microSD 128GB tốc độ cao",
            "Ngày 2: 176.000đ (-20%) · Thuê 3 ngày chỉ 561.000đ",
        ],
    },

    # ══════════════════════════════════════════════
    # 2. ỐNG KÍNH (LENS) SONY E, CANON RF/EF, SIGMA, FUJIFILM
    # ══════════════════════════════════════════════
    {
        "id": "sony-24-70-gm2",
        "name": "Sony FE 24-70mm f/2.8 GM II",
        "group": "Ống kính",
        "grade": "Sony",
        "price": 450000,
        "stock": 2,
        "icon": "camera",
        "img": "/static/services/photo/img/gear_lenses_closeup.jpg",
        "desc": "Ống kính zoom chuẩn mực cao cấp nhất cho sự kiện, hội nghị, phóng sự cưới & TVC quảng cáo.",
        "specs": [
            "Kèm filter B+W UV, hood zin hoa sen & túi chống sốc",
            "Ghép thuê cùng Body Sony A7 IV / A7 III giảm thêm 50.000đ",
            "Ngày 2: 360.000đ (-20%) · Thấu kính sạch chuẩn 100%",
        ],
    },
    {
        "id": "sony-70-200-gm2",
        "name": "Sony FE 70-200mm f/2.8 GM OSS II",
        "group": "Ống kính",
        "grade": "Sony",
        "price": 480000,
        "stock": 2,
        "icon": "camera",
        "img": "/static/services/photo/img/gear_lenses_closeup.jpg",
        "desc": "Tele khẩu f/2.8 siêu nhẹ cho chụp sân khấu, gala, thể thao & chân dung xóa phông bokeh mượt mà.",
        "specs": [
            "Kèm collar tripod, filter UV 77mm, hood & hộp chống sốc",
            "Ngày 2: 384.000đ (-20%) · Ngày 3: 360.000đ (-25%)",
            "Bắt nét cực nhanh trên các dòng body Alpha",
        ],
    },
    {
        "id": "sony-fe-85-f18",
        "name": "Sony FE 85mm f/1.8 (Chân dung)",
        "group": "Ống kính",
        "grade": "Sony",
        "price": 180000,
        "stock": 4,
        "icon": "camera",
        "img": "/static/services/photo/img/gear_lenses_closeup.jpg",
        "desc": "Tiêu cự vàng chân dung Full-frame: gọn nhẹ, bokeh tròn mịn và lấy nét mắt siêu êm.",
        "specs": [
            "Đủ hood zin, filter bảo vệ UV, nắp trước sau",
            "Ngày 2: 144.000đ (-20%) · Sinh viên thuê chụp kỷ yếu giảm 10%",
            "Cực hợp chụp áo dài, couple ngoại cảnh",
        ],
    },
    {
        "id": "sigma-24-70-art",
        "name": "Sigma 24-70mm f/2.8 DG DN Art (Ngàm E)",
        "group": "Ống kính",
        "grade": "Sigma",
        "price": 260000,
        "stock": 3,
        "icon": "camera",
        "img": "/static/services/photo/img/gear_lenses_closeup.jpg",
        "desc": "Zoom đa dụng khẩu f/2.8 sắc nét tiệm cận GM với mức chi phí thuê tối ưu cho chụp tiệc & du lịch.",
        "specs": [
            "Kèm filter UV 82mm, hood khóa & túi Sigma zin",
            "Ngày 2: 208.000đ (-20%) · Ngày 3: 195.000đ (-25%)",
            "Đáp ứng 90% góc chụp chỉ với 1 ống kính",
        ],
    },
    {
        "id": "sigma-crop-f14",
        "name": "Sigma 16mm / 30mm / 56mm f/1.4 DC DN",
        "group": "Ống kính",
        "grade": "Sigma",
        "price": 150000,
        "stock": 4,
        "icon": "camera",
        "img": "/static/services/photo/img/gear_lenses_closeup.jpg",
        "desc": "Bộ ống fix khẩu lớn f/1.4 cho Sony A6400/ZV-E10 hoặc Fujifilm X: góc rộng 16mm hoặc chân dung 56mm.",
        "specs": [
            "Xóa phông mượt, thu sáng tốt trong quán café & tiệc tối",
            "Ngày 2: 120.000đ (-20%) · Đồng giá cả 3 tiêu cự 16/30/56mm",
            "Kèm hood zin và filter UV đầy đủ",
        ],
    },
    {
        "id": "canon-rf-24-70",
        "name": "Canon RF 24-70mm f/2.8L IS USM",
        "group": "Ống kính",
        "grade": "Canon",
        "price": 480000,
        "stock": 2,
        "icon": "camera",
        "img": "/static/services/photo/img/gear_lenses_closeup.jpg",
        "desc": "Ống kính L-Series cao cấp chống rung quang học 5-stop cho Canon R5 / R6 / R8, cực nét cả quay và chụp.",
        "specs": [
            "Filter UV 82mm, hood Canon zin & túi da đựng lens",
            "Ngày 2: 384.000đ (-20%) · Lấy nét Nano USM siêu êm",
            "Thuê kèm Body R6 II hoặc R8 giảm ngay 50.000đ/ngày",
        ],
    },
    {
        "id": "canon-ef-24-70-ii",
        "name": "Canon EF 24-70mm f/2.8L II USM",
        "group": "Ống kính",
        "grade": "Canon",
        "price": 300000,
        "stock": 3,
        "icon": "camera",
        "img": "/static/services/photo/img/gear_lenses_closeup.jpg",
        "desc": "Huyền thoại sự kiện & phóng sự của Canon, dùng trực tiếp trên 5D4/6D2 hoặc R6/R8 qua ngàm chuyển.",
        "specs": [
            "Cho mượn kèm ngàm EF–EOS R miễn phí nếu dùng body EOS R",
            "Ngày 2: 240.000đ (-20%) · Ngày 3: 225.000đ (-25%)",
            "Đủ hood EW-88C và filter bảo vệ 82mm",
        ],
    },
    {
        "id": "fuji-16-55-f28",
        "name": "Fujifilm XF 16-55mm f/2.8 R LM WR",
        "group": "Ống kính",
        "grade": "Fujifilm",
        "price": 250000,
        "stock": 2,
        "icon": "camera",
        "img": "/static/services/photo/img/gear_lenses_closeup.jpg",
        "desc": "Ống zoom Red Badge tương đương 24-84mm f/2.8 toàn dải cho máy Fujifilm X-T4 / X-S20 / X-T5.",
        "specs": [
            "Filter UV 77mm, hood kim loại & nắp trước sau",
            "Ngày 2: 200.000đ (-20%) · Chống bụi ẩm thời tiết WR",
            "Chất lượng ảnh sắc nét tương đương ống fix ở mọi tiêu cự",
        ],
    },

    # ══════════════════════════════════════════════
    # 3. ĐÈN ÁNH SÁNG, GIMBAL & MICRO THU ÂM
    # ══════════════════════════════════════════════
    {
        "id": "dji-rs3",
        "name": "Gimbal DJI RS3 / RS3 Mini (3 Trục)",
        "group": "Đèn & Gimbal",
        "grade": "DJI",
        "price": 250000,
        "stock": 4,
        "icon": "flash_on",
        "img": "/static/services/photo/img/gear_lighting_tripod.jpg",
        "desc": "Chống rung 3 trục khóa tự động, tải tới 3kg, chuyển quay dọc Reels/TikTok nhanh trong 10 giây.",
        "specs": [
            "Full hộp: tay cầm vali, đế tháo nhanh, đủ cáp điều khiển",
            "Hỗ trợ cân sẵn theo body và ống kính của bạn khi nhận",
            "Ngày 2: 200.000đ (-20%) · Pin dùng liên tục 12 giờ",
        ],
    },
    {
        "id": "godox-v1",
        "name": "Đèn Flash Godox V1 Pro / V860 III",
        "group": "Đèn & Gimbal",
        "grade": "Godox",
        "price": 120000,
        "stock": 5,
        "icon": "flash_on",
        "img": "/static/services/photo/img/gear_lighting_tripod.jpg",
        "desc": "Flash TTL pin Lithium hồi đèn 1.5s, đánh 500+ phát; đầu tròn V1 cho ánh sáng tản đều rất mịn.",
        "specs": [
            "Kèm 1 pin VB26, bộ sạc, chân vịt & dome tản sáng nam châm",
            "Có sẵn chân riêng cho Sony, Canon, Fujifilm",
            "Thuê kèm Trigger Godox XPro II chỉ thêm 50.000đ/ngày",
        ],
    },
    {
        "id": "godox-led-sl150",
        "name": "Bộ 2 Đèn LED Studio Godox SL150 II + Softbox",
        "group": "Đèn & Gimbal",
        "grade": "Godox",
        "price": 300000,
        "stock": 3,
        "icon": "flash_on",
        "img": "/static/services/photo/img/gear_lighting_tripod.jpg",
        "desc": "Combo 2 đèn LED ánh sáng liên tục CRI 96+ quay phỏng vấn, livestream & chụp ảnh sản phẩm chuyên nghiệp.",
        "specs": [
            "2 thân đèn 150W siêu êm + 2 chân đèn inox 2.8m",
            "2 Softbox bát giác tản sáng mềm + Remote điều khiển",
            "Ngày 2: 240.000đ (-20%) · Lắp gọn trong 1 túi kéo",
        ],
    },
    {
        "id": "dji-mic-2",
        "name": "Micro Không Dây DJI Mic 2 (2 Kênh)",
        "group": "Đèn & Gimbal",
        "grade": "DJI",
        "price": 150000,
        "stock": 4,
        "icon": "mic",
        "img": "/static/services/photo/img/gear_lighting_tripod.jpg",
        "desc": "2 micro cài áo không dây ghi âm 32-bit float chống vỡ tiếng, lọc ồn AI — gắn được cho máy ảnh & iPhone.",
        "specs": [
            "Hộp sạc 18h + 2 bông lọc gió + đầu nối Type-C/Lightning/3.5mm",
            "Ghi âm sao lưu dự phòng 8GB trực tiếp bên trong từng mic",
            "Ngày 2: 120.000đ (-20%) · Test âm thanh tại chỗ trước khi nhận",
        ],
    },

    # ══════════════════════════════════════════════
    # 4. DỊCH VỤ PHOTOBOOTH SỰ KIỆN TRỌN GÓI
    # ══════════════════════════════════════════════
    {
        "id": "booth-mini-2h",
        "name": "Photobooth Classic · Gói Mini 2 Giờ",
        "group": "Photobooth",
        "grade": "Trọn gói 2h",
        "price": 4200000,
        "stock": 3,
        "icon": "add_a_photo",
        "img": "/static/services/photo/img/photobooth_setup.jpg",
        "desc": "Gói trọn gói cho tiệc sinh nhật, thôi nôi, tiệc trà, workshop 50–120 khách. In lấy liền không giới hạn 2h.",
        "specs": [
            "Máy ảnh DSLR + Đèn studio + Máy in nhiệt DNP (10s/ảnh)",
            "In không giới hạn số lượng 2×6 hoặc 4×6 trong 2 giờ",
            "Thiết kế 1 khung in riêng + Phông nền + Props + 1 KTV trực",
            "Giờ phát sinh thêm (nếu có): 1.300.000đ/giờ",
        ],
    },
    {
        "id": "booth-wedding-3h",
        "name": "Photobooth Tiệc Cưới & Gala · Gói 3 Giờ",
        "group": "Photobooth",
        "grade": "Trọn gói 3h",
        "price": 5600000,
        "stock": 3,
        "icon": "add_a_photo",
        "img": "/static/services/photo/img/photobooth_setup.jpg",
        "desc": "Gói phổ biến nhất cho tiệc cưới & Gala Dinner (120–350 khách). Setup sớm 60 phút, in lấy liền + quét QR.",
        "specs": [
            "In ảnh nhiệt không giới hạn 3 giờ + Thiết kế 2–3 mẫu khung",
            "Quét mã QR lấy file ảnh ngay tại quầy + Link Drive toàn album",
            "Đủ backdrop chuẩn, props tiệc cưới & 2 kỹ thuật viên hỗ trợ",
            "Đặt cùng gói chụp sự kiện được giảm ngay 10% tổng hóa đơn",
        ],
    },
    {
        "id": "booth-corporate-4h",
        "name": "Photobooth Doanh Nghiệp & Activation · Gói 4 Giờ",
        "group": "Photobooth",
        "grade": "Trọn gói 4h",
        "price": 6800000,
        "stock": 2,
        "icon": "add_a_photo",
        "img": "/static/services/photo/img/photobooth_setup.jpg",
        "desc": "Tối ưu cho khai trương, hội nghị khách hàng & Year-End Party doanh nghiệp. Xuất hợp đồng & hóa đơn VAT đủ.",
        "specs": [
            "In không giới hạn 4 giờ + Tùy biến giao diện màn hình theo Brand",
            "Gắn logo & QR sự kiện lên từng tấm ảnh và link tải ảnh trực tuyến",
            "Trọn gói cả ngày (8 giờ): 9.800.000đ · Hỗ trợ xuất hóa đơn VAT",
        ],
    },
    {
        "id": "booth-360-mirror-3h",
        "name": "360 Video Booth / Mirror Booth Cảm Ứng (3 Giờ)",
        "group": "Photobooth",
        "grade": "Booth Cao Cấp 3h",
        "price": 8500000,
        "stock": 2,
        "icon": "add_a_photo",
        "img": "/static/services/photo/img/photobooth_setup.jpg",
        "desc": "Trải nghiệm quay slow-motion 360° ghép nhạc viral hoặc Gương Thần cảm ứng toàn thân sang trọng cho VIP event.",
        "specs": [
            "Bục 360 đường kính 110cm hoặc Gương cảm ứng Full HD in lấy liền",
            "Gửi video nhạc slow-motion / ảnh in qua QR & AirDrop tức thì",
            "Kèm trụ thảm đỏ, đèn LED studio viền RGB & 2 KTV điều phối",
        ],
    },
]

PHOTOBOOTH_TIERS = [
    {
        "id": "booth-mini-2h",
        "badge": "2 Giờ · Tiết kiệm",
        "audience": "50 – 120 khách",
        "title": "Gói Mini · Tiệc Ấm Cúng",
        "price": 4200000,
        "unit": "/ gói 2 giờ",
        "extra_note": "Phát sinh thêm: 1.300.000đ/giờ",
        "desc": "Gọn nhẹ cho tiệc sinh nhật, thôi nôi, họp lớp, khai trương nhỏ.",
        "features": [
            "Máy ảnh DSLR + Đèn studio mềm mịn",
            "In ảnh nhiệt lấy liền không giới hạn (2h)",
            "Thiết kế 1 mẫu khung ảnh riêng theo tiệc",
            "Phông nền + props có sẵn + 1 KTV trực suốt tiệc",
            "Quét QR nhận ảnh + Link tải full album gốc",
        ],
        "demand": "Photobooth Classic · Gói Mini 2 Giờ — 4.200.000đ",
        "featured": False,
        "cta": "Chọn Gói 2h · 4.200.000đ",
    },
    {
        "id": "booth-wedding-3h",
        "badge": "3 Giờ · Phổ biến nhất",
        "audience": "120 – 350+ khách",
        "title": "Gói Tiệc Cưới & Gala",
        "price": 5600000,
        "unit": "/ gói 3 giờ",
        "extra_note": "Đặt kèm chụp phóng sự giảm 10%",
        "desc": "Đáp ứng trọn vẹn khung đón khách và tiệc tối cưới hỏi, YEP công ty.",
        "features": [
            "Setup sớm 60 phút trước giờ đón khách",
            "In ảnh nhiệt tốc độ cao không giới hạn trong 3h",
            "Thiết kế 2–3 mẫu khung riêng (Strip 2×6 & Postcard 4×6)",
            "Quét QR tải ảnh HD & ảnh động tức thì tại quầy",
            "Backdrop, bộ props vui nhộn & 2 KTV hỗ trợ",
        ],
        "demand": "Photobooth Tiệc Cưới & Gala · Gói 3 Giờ — 5.600.000đ",
        "featured": True,
        "cta": "Chọn Gói 3h · 5.600.000đ",
    },
    {
        "id": "booth-corporate-4h",
        "badge": "4 Giờ · Doanh Nghiệp",
        "audience": "Hội nghị & Activation",
        "title": "Gói Activation & Sự Kiện Lớn",
        "price": 6800000,
        "unit": "/ gói 4 giờ",
        "extra_note": "Gói cả ngày 8h: 9.800.000đ · Hỗ trợ xuất VAT",
        "desc": "Phù hợp hội thảo, triển lãm KCN, lễ kỷ niệm & sự kiện thương hiệu.",
        "features": [
            "Đồng bộ khung in & giao diện màn hình theo Brand",
            "Tùy chọn nâng cấp in backdrop kích thước lớn (+1.2tr)",
            "Landing QR tải ảnh mang logo & thông tin sự kiện",
            "Dễ dàng ghép thêm bộ máy ảnh Sony A7 IV / Canon R6 II",
            "Hợp đồng pháp nhân & hóa đơn điện tử VAT đầy đủ",
        ],
        "demand": "Photobooth Doanh Nghiệp · Gói 4 Giờ — 6.800.000đ",
        "featured": False,
        "cta": "Chọn Gói 4h · 6.800.000đ",
    },
]

PHOTOBOOTH_ADDONS = [
    {
        "name": "360 Video Booth (Slow-motion ghép nhạc)",
        "price": 8500000,
        "unit": "gói 3 giờ",
        "desc": "Bục quay 360° tải 3–5 khách + đèn RGB + xuất clip QR & AirDrop trong 15 giây.",
    },
    {
        "name": "Mirror Booth Cảm Ứng (Gương Thần Toàn Thân)",
        "price": 8500000,
        "unit": "gói 3 giờ",
        "desc": "Gương soi cảm ứng tương tác chữ ký, hiệu ứng animation & máy in nhiệt không giới hạn.",
    },
    {
        "name": "In & dựng Backdrop riêng theo Concept (2.4m × 2.4m)",
        "price": 1200000,
        "unit": "lắp đặt tận nơi",
        "desc": "Thiết kế theo mood tiệc / Brand Guideline, khung bạt căng phẳng không hắt đèn.",
    },
    {
        "name": "Giờ phục vụ Photobooth phát sinh thêm",
        "price": 1300000,
        "unit": "mỗi giờ thêm",
        "desc": "Tính linh hoạt nếu chương trình kéo dài ngoài khung giờ gói đã chọn, vẫn in không giới hạn.",
    },
]

PAGE = {
    "h1": ("Thuê Máy Ảnh Đời Mới", "& Photobooth Sự Kiện"),
    "intro": (
        "Bảng giá niêm yết công khai từng dòng <strong class=\"text-stone-900 font-semibold\">Sony, Canon, Fujifilm, Ống kính, Đèn</strong> "
        "từ <strong class=\"text-amber-700 font-extrabold\">120.000đ/ngày</strong> và dịch vụ "
        "<strong class=\"text-stone-900 font-semibold\">Photobooth in lấy liền trọn gói</strong> từ "
        "<strong class=\"text-amber-700 font-extrabold\">4.200.000đ</strong> — xem giá trực tiếp, đặt giữ máy ngay."
    ),
    "cta": "GỌI GIỮ LỊCH NGAY",
    "hero_caption": "Cho Thuê Máy Ảnh Studio & Dịch Vụ Photobooth Sự Kiện",
    "hero_icon": "photo_camera",
    "hero_image": "/static/services/photo/img/hero_studio.jpg",
    "catalog_kicker": "Bảng Giá Niêm Yết Trực Tiếp",
    "catalog_title": "Chọn Thiết Bị Hoặc Gói Photobooth (Hiện Giá Ngay)",
    "price_title": "Bảng Giá Gói Photobooth & Bảng Giá Thuê Máy Theo Ngày",
    "price_lead": "Giá công khai từng gói sự kiện & từng mã máy theo 24h — ngày 2 giảm 20%, ngày 3 giảm 25%.",
    "price_head": "Hạng Mục / Gói",
    "price_badge": "Niêm yết theo ngày / Gói sự kiện",
    "faq_title": "Câu Hỏi Thường Gặp",
    "area_title": "Phục Vụ Thuê Máy Ảnh & Photobooth Tại Các Khu Vực",
    "area_link": "Cho thuê máy ảnh & photobooth tại {area}",
    "region": "Vĩnh Phúc và Phú Thọ",
}


def ensure_photo_catalog() -> bool:
    """Đảm bảo bảng account_products (service='thue-may-anh') có đủ danh mục giá niêm yết thật.

    Nếu DB chỉ còn 4 dòng giữ chỗ cũ giá 0đ (chưa được admin chỉnh sửa), nâng cấp tự động 1 lần.
    """
    return sales.upgrade_placeholder_landing_items(
        SERVICE_SLUG,
        PHOTO_ITEMS,
        LEGACY_PLACEHOLDER_NAMES,
    )


def get_photo_settings() -> dict:
    """Thông tin công ty và hotline lấy từ cấu hình Đại Lộ Xanh; SEO và danh mục riêng cho máy ảnh & photobooth."""
    info = crane_config.get_crane_settings()
    company_name, tax_id, phone = info["company_name"], info["tax_id"], info["phone"]
    seo_config = {
        "title": f"Bảng giá thuê máy ảnh & Photobooth sự kiện trọn gói — {company_name}",
        "description": (
            f"Bảng giá thuê máy ảnh Sony A7IV, Canon R6, Fujifilm X100VI từ 220k/ngày, ống kính từ 150k/ngày, "
            f"Photobooth in lấy liền trọn gói từ 4,2 triệu tại Vĩnh Phúc, Phú Thọ & Hà Nội — {company_name}. Hotline: {phone}."
        ),
        "keywords": (
            "bảng giá thuê máy ảnh, thuê máy ảnh Sony A7IV, thuê Canon R6, thuê ống kính, cho thuê photobooth giá rẻ, "
            "photobooth tiệc cưới, thuê máy ảnh Vĩnh Phúc, thuê máy ảnh Phú Thọ, "
            f"{company_name}"
        ),
        "og_image": "/static/services/photo/img/hero_studio.jpg",
    }
    return {
        **info,
        "seo_config": seo_config,
        "catalog_items": PHOTO_ITEMS,
        "photobooth_tiers": PHOTOBOOTH_TIERS,
        "photobooth_addons": PHOTOBOOTH_ADDONS,
        "multi_day_policy": MULTI_DAY_POLICY,
    }
