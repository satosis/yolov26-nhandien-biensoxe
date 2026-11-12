"""Tỉnh/thành Việt Nam kèm mã địa lý Google Ads (geo target constant).

Nguồn mã: bảng geotargets-2026-10-06 của Google (https://developers.google.com/google-ads/api/data/geotargets),
cột Criteria ID, các dòng cấp tỉnh có trạng thái Active. Từ 1/7/2025 cả nước còn 34 tỉnh/thành; 29 tỉnh cũ đã sáp nhập
nằm trong MERGED_PROVINCES để tên cũ vẫn tra được, mã địa lý lấy của tỉnh mới đang chứa nó.
Không sửa tay mã số: đối chiếu lại với bảng mới nhất của Google rồi sửa cả hai chỗ cùng lúc.
"""

CURRENT_PROVINCES = [
    {"code": "ha_noi", "name": "Hà Nội", "geo_id": "9040331", "region": "Đồng bằng sông Hồng"},
    {"code": "hai_phong", "name": "Hải Phòng", "geo_id": "9040353", "region": "Đồng bằng sông Hồng"},
    {"code": "bac_ninh", "name": "Bắc Ninh", "geo_id": "9047164", "region": "Đồng bằng sông Hồng"},
    {"code": "hung_yen", "name": "Hưng Yên", "geo_id": "9047177", "region": "Đồng bằng sông Hồng"},
    {"code": "ninh_binh", "name": "Ninh Bình", "geo_id": "9040338", "region": "Đồng bằng sông Hồng"},
    {"code": "phu_tho", "name": "Phú Thọ", "geo_id": "9047182", "region": "Đông Bắc"},
    {"code": "thai_nguyen", "name": "Thái Nguyên", "geo_id": "9040330", "region": "Đông Bắc"},
    {"code": "tuyen_quang", "name": "Tuyên Quang", "geo_id": "9047187", "region": "Đông Bắc"},
    {"code": "quang_ninh", "name": "Quảng Ninh", "geo_id": "9075989", "region": "Đông Bắc"},
    {"code": "lang_son", "name": "Lạng Sơn", "geo_id": "9047179", "region": "Đông Bắc"},
    {"code": "cao_bang", "name": "Cao Bằng", "geo_id": "9047169", "region": "Đông Bắc"},
    {"code": "lao_cai", "name": "Lào Cai", "geo_id": "9047180", "region": "Tây Bắc"},
    {"code": "lai_chau", "name": "Lai Châu", "geo_id": "9047178", "region": "Tây Bắc"},
    {"code": "dien_bien", "name": "Điện Biên", "geo_id": "9047172", "region": "Tây Bắc"},
    {"code": "son_la", "name": "Sơn La", "geo_id": "9047184", "region": "Tây Bắc"},
    {"code": "thanh_hoa", "name": "Thanh Hóa", "geo_id": "9040340", "region": "Bắc Trung Bộ"},
    {"code": "nghe_an", "name": "Nghệ An", "geo_id": "9040345", "region": "Bắc Trung Bộ"},
    {"code": "ha_tinh", "name": "Hà Tĩnh", "geo_id": "9040342", "region": "Bắc Trung Bộ"},
    {"code": "quang_tri", "name": "Quảng Trị", "geo_id": "9040347", "region": "Bắc Trung Bộ"},
    {"code": "hue", "name": "Huế", "geo_id": "9040349", "region": "Bắc Trung Bộ"},
    {"code": "da_nang", "name": "Đà Nẵng", "geo_id": "9047170", "region": "Nam Trung Bộ"},
    {"code": "quang_ngai", "name": "Quảng Ngãi", "geo_id": "9040355", "region": "Nam Trung Bộ"},
    {"code": "khanh_hoa", "name": "Khánh Hòa", "geo_id": "9040364", "region": "Nam Trung Bộ"},
    {"code": "gia_lai", "name": "Gia Lai", "geo_id": "9040359", "region": "Tây Nguyên"},
    {"code": "dak_lak", "name": "Đắk Lắk", "geo_id": "9040370", "region": "Tây Nguyên"},
    {"code": "lam_dong", "name": "Lâm Đồng", "geo_id": "9040368", "region": "Tây Nguyên"},
    {"code": "dong_nai", "name": "Đồng Nai", "geo_id": "9040372", "region": "Đông Nam Bộ"},
    {"code": "tay_ninh", "name": "Tây Ninh", "geo_id": "9047185", "region": "Đông Nam Bộ"},
    {"code": "ho_chi_minh", "name": "TP. Hồ Chí Minh", "geo_id": "9040373", "region": "Đông Nam Bộ"},
    {"code": "can_tho", "name": "Cần Thơ", "geo_id": "9040377", "region": "Đồng bằng sông Cửu Long"},
    {"code": "vinh_long", "name": "Vĩnh Long", "geo_id": "9047188", "region": "Đồng bằng sông Cửu Long"},
    {"code": "dong_thap", "name": "Đồng Tháp", "geo_id": "9040326", "region": "Đồng bằng sông Cửu Long"},
    {"code": "ca_mau", "name": "Cà Mau", "geo_id": "9047168", "region": "Đồng bằng sông Cửu Long"},
    {"code": "an_giang", "name": "An Giang", "geo_id": "9040324", "region": "Đồng bằng sông Cửu Long"},
]

MERGED_PROVINCES = [
    {"code": "vinh_phuc", "name": "Vĩnh Phúc", "region": "Đồng bằng sông Hồng", "merged_into": "phu_tho"},
    {"code": "hoa_binh", "name": "Hòa Bình", "region": "Tây Bắc", "merged_into": "phu_tho"},
    {"code": "bac_giang", "name": "Bắc Giang", "region": "Đông Bắc", "merged_into": "bac_ninh"},
    {"code": "ha_giang", "name": "Hà Giang", "region": "Đông Bắc", "merged_into": "tuyen_quang"},
    {"code": "yen_bai", "name": "Yên Bái", "region": "Tây Bắc", "merged_into": "lao_cai"},
    {"code": "bac_kan", "name": "Bắc Kạn", "region": "Đông Bắc", "merged_into": "thai_nguyen"},
    {"code": "thai_binh", "name": "Thái Bình", "region": "Đồng bằng sông Hồng", "merged_into": "hung_yen"},
    {"code": "hai_duong", "name": "Hải Dương", "region": "Đồng bằng sông Hồng", "merged_into": "hai_phong"},
    {"code": "ha_nam", "name": "Hà Nam", "region": "Đồng bằng sông Hồng", "merged_into": "ninh_binh"},
    {"code": "nam_dinh", "name": "Nam Định", "region": "Đồng bằng sông Hồng", "merged_into": "ninh_binh"},
    {"code": "quang_binh", "name": "Quảng Bình", "region": "Bắc Trung Bộ", "merged_into": "quang_tri"},
    {"code": "quang_nam", "name": "Quảng Nam", "region": "Nam Trung Bộ", "merged_into": "da_nang"},
    {"code": "kon_tum", "name": "Kon Tum", "region": "Tây Nguyên", "merged_into": "quang_ngai"},
    {"code": "binh_dinh", "name": "Bình Định", "region": "Nam Trung Bộ", "merged_into": "gia_lai"},
    {"code": "ninh_thuan", "name": "Ninh Thuận", "region": "Nam Trung Bộ", "merged_into": "khanh_hoa"},
    {"code": "dak_nong", "name": "Đắk Nông", "region": "Tây Nguyên", "merged_into": "lam_dong"},
    {"code": "binh_thuan", "name": "Bình Thuận", "region": "Nam Trung Bộ", "merged_into": "lam_dong"},
    {"code": "phu_yen", "name": "Phú Yên", "region": "Nam Trung Bộ", "merged_into": "dak_lak"},
    {"code": "binh_duong", "name": "Bình Dương", "region": "Đông Nam Bộ", "merged_into": "ho_chi_minh"},
    {"code": "ba_ria_vung_tau", "name": "Bà Rịa - Vũng Tàu", "region": "Đông Nam Bộ", "merged_into": "ho_chi_minh"},
    {"code": "binh_phuoc", "name": "Bình Phước", "region": "Đông Nam Bộ", "merged_into": "dong_nai"},
    {"code": "long_an", "name": "Long An", "region": "Đồng bằng sông Cửu Long", "merged_into": "tay_ninh"},
    {"code": "soc_trang", "name": "Sóc Trăng", "region": "Đồng bằng sông Cửu Long", "merged_into": "can_tho"},
    {"code": "hau_giang", "name": "Hậu Giang", "region": "Đồng bằng sông Cửu Long", "merged_into": "can_tho"},
    {"code": "ben_tre", "name": "Bến Tre", "region": "Đồng bằng sông Cửu Long", "merged_into": "vinh_long"},
    {"code": "tra_vinh", "name": "Trà Vinh", "region": "Đồng bằng sông Cửu Long", "merged_into": "vinh_long"},
    {"code": "tien_giang", "name": "Tiền Giang", "region": "Đồng bằng sông Cửu Long", "merged_into": "dong_thap"},
    {"code": "bac_lieu", "name": "Bạc Liêu", "region": "Đồng bằng sông Cửu Long", "merged_into": "ca_mau"},
    {"code": "kien_giang", "name": "Kiên Giang", "region": "Đồng bằng sông Cửu Long", "merged_into": "an_giang"},
]


def build_province_list() -> list[dict]:
    """Tỉnh hiện hành trước, tỉnh cũ sau; mỗi mục có `geo_id` dùng được ngay (tỉnh cũ mang mã của tỉnh mới)."""
    current = {p["code"]: p for p in CURRENT_PROVINCES}
    merged = [{**old, "geo_id": current[old["merged_into"]]["geo_id"],
               "merged_into_name": current[old["merged_into"]]["name"]} for old in MERGED_PROVINCES]
    return [dict(p) for p in CURRENT_PROVINCES] + merged
