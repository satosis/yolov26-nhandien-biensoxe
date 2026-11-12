"""Dữ liệu khu vực và dịch vụ theo tỉnh cho các trang landing SEO xe cẩu."""

AREAS = {
    "phu-tho": {
        "name": "Phú Thọ",
        "merged_note": None,
        "zones": ["Cổ Tiết", "Thụy Vân", "Trung Hà", "Phú Hà"],
    },
    "vinh-phuc": {
        "name": "Vĩnh Phúc",
        "merged_note": "nay thuộc tỉnh Phú Thọ",
        "zones": ["Khai Quang", "Bình Xuyên", "Kim Hoa", "Bá Thiện", "Phúc Yên"],
    },
    "ha-noi": {
        "name": "Hà Nội",
        "merged_note": None,
        "zones": ["Thăng Long", "Nội Bài", "Quang Minh", "Phú Nghĩa", "Sài Đồng B"],
    },
    "bac-ninh": {
        "name": "Bắc Ninh",
        "merged_note": None,
        "zones": ["Quế Võ", "VSIP Bắc Ninh", "Yên Phong", "Tiên Sơn", "Thuận Thành"],
    },
    "bac-giang": {
        "name": "Bắc Giang",
        "merged_note": "nay thuộc tỉnh Bắc Ninh",
        "zones": ["Vân Trung", "Quang Châu", "Đình Trám", "Song Khê - Nội Hoàng"],
    },
    "thai-nguyen": {
        "name": "Thái Nguyên",
        "merged_note": None,
        "zones": ["Yên Bình", "Sông Công", "Điềm Thụy"],
    },
    "hai-duong": {
        "name": "Hải Dương",
        "merged_note": "nay thuộc thành phố Hải Phòng",
        "zones": ["Đại An", "Phúc Điền", "Tân Trường", "Nam Sách"],
    },
    "hung-yen": {
        "name": "Hưng Yên",
        "merged_note": None,
        "zones": ["Phố Nối A", "Thăng Long II", "Yên Mỹ"],
    },
}

SERVICES = {
    "thue-xe-cau": {
        "label": "Thuê xe cẩu",
        "title": "Thuê xe cẩu tự hành 8–10 tấn",
        "h1": "Thuê xe cẩu tự hành 8–10 tấn tại {area}",
        "lead": "Dịch vụ cho thuê xe cẩu tự hành 8–10 tấn chuyên nghiệp tại {area}. Phục vụ cẩu thiết bị, lắp dựng cầu trục, cẩu sắt thép và cẩu máy 24/7.",
        "jobs": [
            "Cẩu thiết bị máy móc công nghiệp",
            "Lắp dựng dầm cầu trục & pa lăng nhà xưởng",
            "Cẩu vận chuyển sắt thép & kết cấu dài",
            "Cẩu hạ máy móc vào vị trí định vị",
        ],
        "template_key": "fleet",
        "faq": [
            {
                "q": "Đại Lộ Xanh có cho thuê xe cẩu tự hành tại {area} không?",
                "a": "Chúng tôi cung cấp dịch vụ thuê xe cẩu tự hành 8–10 tấn tại {area}; liên hệ hotline để được báo giá.",
            },
            {
                "q": "Giá thuê xe cẩu tự hành tại {area} tính như thế nào?",
                "a": "Giá thuê linh hoạt theo ca làm việc, theo chuyến hoặc trọn gói công trình; liên hệ hotline để được báo giá.",
            },
            {
                "q": "Xe cẩu có nhận lắp dựng dầm cầu trục tại {area} không?",
                "a": "Chúng tôi nhận cẩu nâng và căn chỉnh dầm cầu trục tại {area}, thợ lái có chứng chỉ vận hành thiết bị nâng TCVN 4244; liên hệ hotline để được báo giá.",
            },
            {
                "q": "Thời gian điều xe cẩu đến công trình tại {area} là bao lâu?",
                "a": "Đội xe trực 24/7; thời gian điều xe đến công trình tại {area} tùy vị trí, liên hệ hotline để được báo giá.",
            },
        ],
    },
    "van-chuyen-qua-can": {
        "label": "Vận chuyển quả cân",
        "title": "Chở và cho thuê quả cân chuẩn kiểm định trạm cân",
        "h1": "Chở và cho thuê quả cân chuẩn kiểm định trạm cân tại {area}",
        "lead": "Dịch vụ cho thuê và vận chuyển quả cân chuẩn kiểm định trạm cân xe tải, cân điện tử tại {area} kèm xe cẩu tự hành trọn gói.",
        "jobs": [
            "Quả cân chuẩn gang 500KG – 1000KG",
            "Bộ quả cân chuẩn hộp gỗ lót nhung (F1/F2)",
            "Quả cân chuẩn trụ inox lẻ (1KG – 20KG)",
            "Dịch vụ trọn gói xe cẩu & quả cân kiểm định",
        ],
        "template_key": "weights",
        "faq": [
            {
                "q": "Đại Lộ Xanh có cho thuê quả cân chuẩn tại {area} không?",
                "a": "Có. Chúng tôi luôn sẵn 40+ quả cân chuẩn gang 500kg (cấp M1), bộ quả cân hộp gỗ F1/F2 và quả inox 1–20kg có chứng nhận ĐLVN để giao tận nơi tại {area}.",
            },
            {
                "q": "Dịch vụ kiểm định trạm cân tại {area} có kèm xe cẩu tự hành không?",
                "a": "Có. Chúng tôi phục vụ trọn gói xe cẩu tự hành 8–10 tấn + 20–40 quả cân 500kg + thợ lái có chứng chỉ cẩu định vị từng quả lên góc bàn cân tại {area}.",
            },
            {
                "q": "Quả cân chuẩn tại {area} có giấy chứng nhận kiểm định ĐLVN không?",
                "a": "Toàn bộ quả cân đều đạt chuẩn đo lường ĐLVN, có buồng niêm chì và giấy chứng nhận/tem hiệu chuẩn định kỳ còn hiệu lực tại Quatest.",
            },
            {
                "q": "Chi phí thuê quả cân kiểm định tại {area} là bao nhiêu?",
                "a": "Giá được niêm yết công khai: quả cân inox lẻ 250.000đ/quả/ngày, bộ hộp gỗ F1/F2 600.000đ/bộ/ngày, quả gang 500kg 400.000đ/quả/đợt (thuê từ 20 quả còn 350.000đ/quả) và gói trọn bộ xe cẩu trạm 60–80T từ 4.500.000đ.",
            },
        ],
    },
    "cau-lap-dat-thiet-bi": {
        "label": "Cẩu lắp đặt thiết bị",
        "contact_variant": "install",
        "title": "Cẩu, lắp đặt thiết bị nhà xưởng, container văn phòng, máy móc",
        "h1": "Cẩu, lắp đặt thiết bị nhà xưởng, container văn phòng, máy móc tại {area}",
        "lead": "Dịch vụ cẩu, di dời và lắp đặt máy móc thiết bị nhà xưởng, container văn phòng tại {area} bằng xe cẩu tự hành 8–10 tấn.",
        "jobs": [
            "Cẩu hạ và định vị máy móc thiết bị nhà xưởng",
            "Cẩu hạ và lắp đặt container văn phòng",
            "Di dời máy móc thiết bị trong nhà xưởng",
            "Cẩu nâng hạ hàng nặng trong không gian hẹp",
        ],
        "template_key": "fleet",
        "faq": [
            {
                "q": "Đại Lộ Xanh có nhận cẩu hạ máy móc thiết bị tại {area} không?",
                "a": "Chúng tôi nhận cẩu hạ, di dời máy móc thiết bị an toàn tại {area}; liên hệ hotline để được báo giá.",
            },
            {
                "q": "Có nhận cẩu lắp đặt container văn phòng tại {area} không?",
                "a": "Chúng tôi nhận cẩu và căn chỉnh container văn phòng tại {area}; liên hệ hotline để được báo giá.",
            },
            {
                "q": "Lái xe cẩu có chứng chỉ an toàn khi vào nhà máy tại {area} không?",
                "a": "Thợ lái có chứng chỉ vận hành thiết bị nâng TCVN 4244, xe có sổ kiểm định an toàn kỹ thuật còn hạn; liên hệ hotline để được báo giá.",
            },
            {
                "q": "Chi phí cẩu lắp đặt thiết bị tại {area} tính như thế nào?",
                "a": "Báo giá trực tiếp theo hiện trường và trọng lượng thiết bị; liên hệ hotline để được báo giá.",
            },
        ],
    },
    "thue-container": {
        "label": "Cho thuê container",
        "active_page": "container",
        "title": "Cho thuê container văn phòng, container kho kèm xe cẩu cẩu hạ",
        "h1": "Cho thuê container văn phòng, container kho tại {area}",
        "lead": "Dịch vụ cho thuê container văn phòng, container kho tại {area}, có xe cẩu tự hành 8–10 tấn cẩu hạ và đặt container tại chỗ.",
        "jobs_lead": "Bảng giá thuê niêm yết trực tiếp từ 1,5tr/tháng, sẵn xe cẩu hạ tận nơi.",
        "meta_title": "Cho thuê container {area} | {brand}",
        "meta_desc": (
            "Cho thuê container kho từ 1,5tr/tháng, container văn phòng từ 2,5tr/tháng tại {area}, cẩu hạ tận nơi bằng xe cẩu 8–10 tấn. "
            "Phục vụ KCN {zones}. Hotline: {phone}."
        ),
        "catalog_title": "Các Loại Container Cho Thuê (Giá Niêm Yết)",
        "catalog_lead": "Đơn giá niêm yết trực tiếp theo tháng và cước cẩu hạ trọn gói bằng xe cẩu chính chủ.",
        "hero_image": "/static/services/container/img/container_hero.jpg",
        "ad_voice": {
            "need": "thuê container",
            "trust": "Cẩu hạ và đặt container đúng vị trí bằng xe cẩu tự hành của công ty.",
            "survey": "Báo giá theo loại container và thời gian thuê tại khu vực {area}.",
        },
        "jobs": [
            "Container văn phòng 20ft (2,5tr/th) & 40ft (4,2tr/th)",
            "Container kho 20ft (1,5tr/th) & 40ft (2,6tr/th)",
            "Thuê từ 3 tháng giảm 200k–300k/tháng",
            "Cẩu hạ, đặt & thu hồi bằng xe cẩu 8T–10T",
        ],
        "template_key": "container",
        "faq": [
            {
                "q": "Đại Lộ Xanh có cho thuê container tại {area} không?",
                "a": "Có. Chúng tôi luôn có sẵn container văn phòng 20ft/40ft và container kho giao trực tiếp bằng xe cẩu của công ty đến các công trình và KCN tại {area}.",
            },
            {
                "q": "Giá thuê container tại {area} là bao nhiêu mỗi tháng?",
                "a": "Giá niêm yết trực tiếp: Container kho 20ft 1.500.000đ/tháng, kho 40ft 2.600.000đ/tháng; Container văn phòng 20ft (điều hòa) 2.500.000đ/tháng, văn phòng 40ft 4.200.000đ/tháng. Thuê từ 3 tháng được giảm 200.000–300.000đ/tháng; cước cẩu hạ 2 chiều từ 1.800.000đ/chuyến.",
            },
            {
                "q": "Có xe cẩu hạ và đặt container tận nơi tại {area} không?",
                "a": "Có. Đại Lộ Xanh sở hữu đội xe cẩu tự hành 8–10 tấn trực chiến, cẩu hạ, căn chỉnh ngay khi giao và thu hồi trọn gói chỉ từ 1.800.000đ/chuyến.",
            },
            {
                "q": "Thủ tục thuê và giao container tại {area} mất bao lâu?",
                "a": "Có thể điều xe giao ngay trong ngày hoặc trong vòng 24h tại {area}, đầy đủ hợp đồng thuê và hóa đơn GTGT (VAT) cho doanh nghiệp, nhà thầu.",
            },
        ],
    },
    "dien-mat-troi": {
        "label": "Thiết bị điện mặt trời",
        "active_page": "solar",
        "title": "Cung cấp thiết bị điện năng lượng mặt trời cho nhà xưởng, công trình",
        "h1": "Thiết bị điện năng lượng mặt trời tại {area}",
        "lead": "Bảng giá bán trực tiếp tấm pin Mono 550W (1,55tr/tấm), biến tần 5kW (9,5–14,5tr) và pin lưu trữ LiFePO4 5kWh (20,5tr) tại {area}.",
        "jobs_lead": "Thiết bị điện mặt trời chính hãng cho nhà xưởng và công trình, giá công khai từng nhóm.",
        "meta_title": "Thiết bị điện mặt trời {area} | {brand}",
        "meta_desc": (
            "Cung cấp tấm pin 550W (1,55tr/tấm), biến tần Hybrid 5kW (14,5tr), pin lưu trữ 5kWh (20,5tr) tại {area}. "
            "Phục vụ KCN {zones}. Hotline: {phone}."
        ),
        "catalog_title": "Bảng Giá Thiết Bị Điện Mặt Trời Tại Kho",
        "catalog_lead": "Đơn giá niêm yết công khai cho tấm pin, biến tần, pin lưu trữ, tủ điện & phụ kiện.",
        "hero_icon": "",
        "hero_image": "/static/services/solar/img/solar_hero.jpg",
        "ad_voice": {
            "need": "mua thiết bị điện mặt trời",
            "offer": "{company} cung cấp thiết bị điện năng lượng mặt trời tại {area}.",
            "jobs_heading": "Các nhóm thiết bị cung cấp:",
            "title_2": "Bán thiết bị điện mặt trời tại {area}",
            "offer_detail": "{company} kinh doanh thiết bị điện năng lượng mặt trời tại {area}.",
            "trust": "Tư vấn chọn thiết bị theo công suất và nhu cầu sử dụng.",
            "list_heading": "Danh mục thiết bị tại {area}:",
            "close": "Báo giá theo công suất và số lượng, xuất hóa đơn VAT đầy đủ.",
            "question": "Bạn đang cần mua thiết bị điện mặt trời tại {area}?",
            "do_line": "{company} cung cấp thiết bị điện mặt trời tại {area}.\nChúng tôi cung cấp:",
            "survey": "Báo giá theo công suất và số lượng tại khu vực {area}.",
        },
        "jobs": [
            "Tấm pin Mono 550W–585W (1.550.000đ/tấm)",
            "Biến tần 5kW hòa lưới / Hybrid (9,5tr – 14,5tr)",
            "Pin lưu trữ LiFePO4 5kWh (20.500.000đ/module)",
            "Tủ điện AC/DC, dây solar & phụ kiện (3,2tr/bộ)",
        ],
        "template_key": "catalog",
        "faq": [
            {
                "q": "Đại Lộ Xanh có bán lẻ và sỉ thiết bị điện mặt trời tại {area} không?",
                "a": "Có. Chúng tôi bán lẻ từng tấm pin/biến tần cũng như cung cấp theo pallet và combo cho công trình nhà xưởng tại {area}, kèm hóa đơn VAT và CO/CQ.",
            },
            {
                "q": "Giá thiết bị điện mặt trời tại {area} là bao nhiêu?",
                "a": "Giá niêm yết trực tiếp tại kho: Tấm pin Mono 550W–585W giá 1.550.000đ/tấm (từ 1 pallet 36 tấm: 1.450.000đ/tấm); Biến tần hòa lưới 5kW từ 9.500.000đ/bộ, Hybrid 5kW 14.500.000đ/bộ; Pin lưu trữ LiFePO4 5kWh 20.500.000đ/module; Bộ tủ điện AC/DC & dây phụ kiện 3.200.000đ/bộ.",
            },
            {
                "q": "Có tư vấn chọn thiết bị theo công suất tại {area} không?",
                "a": "Có. Kỹ sư của chúng tôi tính toán sẵn cấu hình tấm pin – biến tần – pin lưu trữ theo hóa đơn tiền điện thực tế của gia đình hoặc nhà xưởng tại {area}.",
            },
            {
                "q": "Có hỗ trợ nâng hạ pallet pin lên mái xưởng bằng xe cẩu tại {area} không?",
                "a": "Có. Đại Lộ Xanh sở hữu đội xe cẩu tự hành 8–10 tấn cần vươn 19,8m hỗ trợ cẩu chuyển pallet tấm pin và tủ điện lên mái nhà xưởng nhanh gọn tại {area}.",
            },
        ],
    },
    "thue-may-anh": {
        "label": "Cho thuê máy ảnh và photobooth",
        "active_page": "photo",
        "title": "Cho thuê máy ảnh, ống kính và photobooth cho sự kiện",
        "h1": "Cho thuê máy ảnh và photobooth tại {area}",
        "lead": "Cho thuê máy ảnh, ống kính, phụ kiện và photobooth cho sự kiện, chụp ảnh sản phẩm và quay phim tại {area}.",
        "jobs_lead": "Máy ảnh và photobooth cho thuê theo ngày hoặc theo sự kiện, báo giá theo thiết bị và thời gian thuê.",
        "meta_title": "Cho thuê máy ảnh, photobooth {area} | {brand}",
        "meta_desc": (
            "Cho thuê máy ảnh, ống kính, phụ kiện và photobooth cho sự kiện tại {area}. "
            "Báo giá theo thiết bị và thời gian thuê. Hotline: {phone}."
        ),
        "catalog_title": "Thiết Bị Cho Thuê",
        "catalog_lead": "Máy ảnh, ống kính, phụ kiện và photobooth cho sự kiện.",
        "hero_icon": "photo_camera",
        "ad_voice": {
            "need": "thuê máy ảnh, photobooth",
            "offer": "{company} cho thuê máy ảnh và photobooth tại {area}.",
            "jobs_heading": "Thiết bị cho thuê:",
            "title_2": "Cho thuê máy ảnh, photobooth tại {area}",
            "offer_detail": "{company} cho thuê máy ảnh, ống kính, phụ kiện và photobooth sự kiện tại {area}.",
            "trust": "Tư vấn chọn thiết bị theo mục đích chụp và quy mô sự kiện.",
            "list_heading": "Thiết bị cho thuê tại {area}:",
            "close": "Báo giá theo thiết bị và thời gian thuê.",
            "question": "Bạn đang cần thuê máy ảnh hoặc photobooth tại {area}?",
            "do_line": "{company} cho thuê máy ảnh và photobooth tại {area}.\nChúng tôi cho thuê:",
            "survey": "Báo giá theo thiết bị và thời gian thuê tại khu vực {area}.",
        },
        "jobs": [
            "Máy ảnh mirrorless và DSLR",
            "Ống kính",
            "Chân máy, đèn và phụ kiện",
            "Photobooth cho sự kiện",
        ],
        "template_key": "catalog",
        "faq": [
            {
                "q": "Thủ tục thuê máy ảnh và ống kính tại {area} cần giấy tờ gì?",
                "a": "Khách hàng cá nhân chuẩn bị CCCD gắn chip chính chủ và tiền đặt cọc theo thiết bị (hoặc giấy tờ thay thế theo thỏa thuận). Doanh nghiệp hỗ trợ hợp đồng và hóa đơn VAT đầy đủ; thiết bị luôn được kiểm tra cùng phụ kiện trước khi bàn giao.",
            },
            {
                "q": "Dịch vụ Photobooth sự kiện tại {area} bao gồm những gì?",
                "a": "Gói Photobooth trọn gói bao gồm hệ thống máy ảnh studio & đèn ánh sáng chuyên nghiệp, máy in ảnh nhiệt lấy liền trong 8–12 giây không giới hạn lượt in trong khung giờ thuê, thiết kế khung ảnh riêng theo chủ đề, phông nền backdrop, đạo cụ chụp hình, kỹ thuật viên trực và link QR tải file gốc.",
            },
            {
                "q": "Nên đặt lịch Photobooth trước bao lâu và thời gian lắp đặt mất bao lâu?",
                "a": "Quý khách nên đặt lịch trước 3–7 ngày (hoặc sớm hơn vào dịp lễ, mùa cưới) để thiết kế khung in và chuẩn bị backdrop. Đội ngũ kỹ thuật đến địa điểm tại {area} lắp đặt, test ánh sáng và chạy thử trước giờ đón khách 45–60 phút.",
            },
            {
                "q": "Giá thuê máy ảnh và gói Photobooth tại {area} được tính như thế nào?",
                "a": "Mọi thiết bị và gói Photobooth tại {area} đều niêm yết giá trực tiếp trên trang: body máy ảnh từ 220.000đ/ngày, ống kính từ 150.000đ/ngày, đèn/gimbal từ 120.000đ/ngày (tính theo 24h; ngày 2 giảm 20%, ngày 3 giảm 25%, từ ngày 4 giảm 30–50%). Photobooth trọn gói in không giới hạn giá từ 4.200.000đ (2h), 5.600.000đ (3h) đến 6.800.000đ (4h).",
            },
            {
                "q": "Chưa rành về thiết bị có được tư vấn chọn máy ảnh và ống kính phù hợp không?",
                "a": "Có. Đội ngũ kỹ thuật sẽ tư vấn combo thân máy, tiêu cự ống kính và đèn đúng với mục đích chụp sự kiện, chân dung, du lịch, kỷ yếu hay quay sản phẩm tại {area}, kèm hướng dẫn thiết lập nhanh khi nhận máy.",
            },
        ],
    },
}


def page_meta(service: str, area_slug: str, company: dict) -> dict:
    """Tạo metadata SEO (title <= 65 ký tự, description <= 158 ký tự, h1, canonical, source_page)."""
    area = AREAS[area_slug]
    svc = SERVICES[service]
    area_name = area["name"]
    brand = company.get("brand_name") or "Đại Lộ Xanh"
    phone = company.get("phone") or "0919 497 568"

    if "meta_title" in svc:
        title = svc["meta_title"].format(area=area_name, brand=brand)
        desc = svc["meta_desc"].format(area=area_name, zones=", ".join(area["zones"][:2]), phone=phone)
    elif service == "thue-xe-cau":
        title = f"Thuê xe cẩu {area_name} | {brand}"
        desc = (
            f"Dịch vụ xe cẩu tự hành 8–10 tấn tại {area_name}. Cẩu thiết bị, lắp dựng cầu trục, cẩu máy tại các KCN "
            f"{', '.join(area['zones'][:2])}. Hotline: {phone}."
        )
    elif service == "van-chuyen-qua-can":
        title = f"Cho thuê quả cân {area_name} | {brand}"
        desc = (
            f"Cho thuê quả cân chuẩn kiểm định trạm cân xe tải tại {area_name}. Có sẵn xe cẩu 8T–10T phục vụ KCN "
            f"{', '.join(area['zones'][:2])}. Hotline: {phone}."
        )
    else:
        title = f"Cẩu lắp đặt thiết bị {area_name} | {brand}"
        desc = (
            f"Dịch vụ cẩu lắp đặt máy móc, kết cấu xưởng, container tại {area_name}. Phục vụ KCN "
            f"{', '.join(area['zones'][:2])}. Hotline: {phone}."
        )

    return {
        "title": title,
        "description": desc,
        "h1": svc["h1"].format(area=area_name),
        "canonical_path": f"/{service}/{area_slug}",
        "source_page": f"area_{service}_{area_slug}",
    }


def all_pages() -> list[tuple[str, str]]:
    """Trả về toàn bộ các cặp (service, area)."""
    return [(s, a) for s in SERVICES for a in AREAS]
