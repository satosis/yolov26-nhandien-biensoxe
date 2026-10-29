# 📢 THƯ MỤC QUẢN LÝ QUẢNG CÁO GOOGLE (XE CẨU & QUẢ CÂN)

Thư mục này được tạo riêng để lưu trữ toàn bộ tài liệu, kế hoạch, bộ từ khóa, mẫu nội dung quảng cáo và tài liệu hướng dẫn kỹ thuật phục vụ cho việc chạy quảng cáo Google Ads của công ty.

---

## 📂 Cấu Trúc Thư Mục `quang_cao/`

```text
quang_cao/
├── README.md                                # Tổng quan và sơ đồ vận hành quảng cáo
├── ke_hoach_chien_dich/                    # Các kế hoạch và chiến lược chạy quảng cáo
│   └── ke_hoach_tong_the.md                 # Chiến lược 2 mũi nhọn: Cố định Phú Thọ & Động theo chuyến xa
├── bo_tu_khoa/                              # Bộ từ khóa chuẩn Google Ads (Khớp cụm từ & Khớp chính xác)
│   ├── tu_khoa_xe_cau.txt                  # Từ khóa thuê xe cẩu, cẩu tự hành, cẩu chuyên dụng
│   ├── tu_khoa_qua_can.txt                 # Từ khóa quả cân kiểm định trạm cân, thử tải cầu trục
│   ├── tu_khoa_chuyen_ve_cac_tinh.txt      # Từ khóa đón xe cẩu chiều về (Lai Châu, Sơn La, Hải Phòng...)
│   └── tu_khoa_phu_dinh.txt                # Danh sách từ khóa phủ định (loại bỏ click rác, tiết kiệm tiền)
├── mau_noi_dung_ad_copy/                   # Mẫu tiêu đề và mô tả quảng cáo chuyển đổi cao
│   └── mau_quang_cao_xe_cau_qua_can.md     # 15 tiêu đề + 4 mô tả + tiện ích cuộc gọi & vị trí
├── huong_dan_api_console/                  # Tài liệu kết nối kỹ thuật (Làm sau khi rảnh)
│   └── huong_dan_mo_api_google_ads.md      # Hướng dẫn từng bước kích hoạt API & lấy token trên Google Console
└── hinh_anh/                               # Kho hình ảnh xe cẩu, quả cân thực tế để gắn vào quảng cáo
    └── .gitkeep
```

---

## 🎯 2 Trụ Cột Chiến Lược Quảng Cáo Của Công Ty

### 1. Trụ Cột Cố Định (Bán kính quanh Phú Thọ):
- **Đối tượng**: Nhà xưởng, khu công nghiệp (Thụy Vân, Phú Hà, Cẩm Khê...), công trình xây dựng dân dụng & công nghiệp tại Phú Thọ, Vĩnh Phúc, Tuyên Quang, Yên Bái.
- **Dịch vụ**:
  - Cho thuê xe cẩu tự hành (2.5 tấn - 15 tấn), cẩu Kato chuyên dụng.
  - Cho thuê quả cân chuẩn kiểm định trạm cân xe tải (từ 20 tấn - 150 tấn), thử tải sàn, cầu trục.
- **Thời gian chạy**: Chạy liên tục hàng ngày với ngân sách ổn định (ví dụ: 100.000đ - 150.000đ/ngày).

### 2. Trụ Cột Động Theo Chuyến Đi Xa (Đón Hàng Chiều Về):
- **Bài toán**: Xe chở hàng/đi cẩu từ Phú Thọ lên Lai Châu, Điện Biên, Sơn La, Lào Cai hoặc xuống Hải Phòng, Quảng Ninh. Nếu quay về xe rỗng sẽ rất lãng phí chi phí nhiên liệu và khấu hao.
- **Giải pháp**:
  - Khi chốt lịch có chuyến đi Lai Châu (ví dụ ngày 05/11 xuất phát, ngày 07/11 về):
  - Trước 2 - 3 ngày, vào hệ thống bật chiến dịch động nhắm mục tiêu **chỉ riêng khu vực Lai Châu** (hoặc bán kính 30km quanh thị xã/thành phố đó).
  - Nội dung quảng cáo: *"Xe cẩu kết hợp chiều về Lai Châu - Phú Thọ - Hà Nội cước siêu rẻ"*, *"Thuê cẩu tại Lai Châu giá ưu đãi trong tuần"*.
  - Sau khi xong chuyến hoặc đủ hàng chiều về -> Tắt chiến dịch ngay trên trang quản trị.

---

## 🖥️ Quản Trị Trực Tiếp Trên Phần Mềm

Hệ thống đã tích hợp sẵn trang quản trị quảng cáo tại đường dẫn:
👉 **`/admin/advertising`** (Nằm trong menu **Vận chuyển -> 📢 Quảng cáo Google**)

Tại đây, anh có thể:
1. Tạo nhanh chiến dịch đón chuyến xa: Chọn tỉnh (Lai Châu, Sơn La, Hải Phòng...), ngày bắt đầu, ngày kết thúc, ngân sách.
2. Bật / Tắt chiến dịch tức thì bằng nút gạt.
3. Cấu hình thông số Google Ads API khi nào cần kết nối tự động.
