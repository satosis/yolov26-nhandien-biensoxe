# 🛠️ HƯỚNG DẪN KẾT NỐI GOOGLE ADS API VÀ CẤP QUYỀN CONSOLE
*(Tài liệu lưu trữ: Khi nào rảnh anh mở tài liệu này ra làm từng bước theo hướng dẫn bên dưới)*

---

## 1. THÔNG TIN HIỆN CÓ CỦA CÔNG TY

Hệ thống đã kiểm tra database và file cấu hình `.env` của dự án:
- **Tài khoản Google đang kết nối**: `vantaidailoxanh2026@gmail.com`
- **Google Cloud Project Number**: `686766750544`
- **Google Client ID**: `686766750544-0dvl780p5993if0614cvfhr1m8t1tftj.apps.googleusercontent.com`
- **Google Client Secret**: *(Đã được cấu hình trong file `.env` của hệ thống)*
- **Google Maps API**: Đã kích hoạt và đang hoạt động tốt.

👉 **Kết luận**: Anh **không cần tạo lại Client ID và Secret**, mà chỉ cần mở thêm dịch vụ Google Ads API và lấy Developer Token từ tài khoản Google Ads MCC.

---

## 2. CÁC BƯỚC THỰC HIỆN KHI ANH LÀM (LÀM SAU)

### Bước 1: Mở (Enable) Google Ads API trong Google Cloud Console
1. Đăng nhập vào trình duyệt bằng tài khoản: `vantaidailoxanh2026@gmail.com`.
2. Truy cập trực tiếp link bật thư viện Google Ads API:
   👉 **[Bật Google Ads API](https://console.cloud.google.com/apis/library/googleads.googleapis.com?project=686766750544)**
3. Nhấn nút xanh **"ENABLE" (Bật)**.

---

### Bước 2: Lấy Developer Token từ Google Ads MCC
*(Developer Token là mã do Google cấp cho nhà phát triển để gửi lệnh tạo/bật/tắt chiến dịch qua phần mềm)*

1. Truy cập trang Google Ads: 👉 **[https://ads.google.com](https://ads.google.com)**
2. Đăng ký một tài khoản Quản lý (MCC - Manager Account) nếu chưa có (rất nhanh, miễn phí).
3. Vào mục **Công cụ và cài đặt (Tools and Settings)** -> **Trung tâm API (API Center)**.
4. Điền form yêu cầu cấp quyền Developer Token (chọn mức quyền cơ bản - Test Account hoặc Basic Access).
5. Copy chuỗi **Developer Token** được cấp.

---

### Bước 3: Lấy Customer ID tài khoản chạy quảng cáo
1. Đăng nhập vào tài khoản Google Ads cá nhân hoặc tài khoản con mà anh nạp tiền chạy quảng cáo.
2. Nhìn góc trên bên phải màn hình sẽ có mã định dạng: `XXX-XXX-XXXX` (10 chữ số).
3. Đó chính là **Customer ID** (Mã khách hàng Google Ads).

---

### Bước 4: Nhập thông tin vào phần mềm quản lý
1. Mở phần mềm quản trị: 👉 **`/admin/advertising`** (Menu **Vận chuyển -> 📢 Quảng cáo Google**).
2. Nhấn nút **"Cấu hình Google Ads API"** (ở góc trên bên phải bảng danh sách chiến dịch).
3. Dán các thông số:
   - **Google Developer Token**: (Lấy ở Bước 2).
   - **Google Ads Customer ID**: (Lấy ở Bước 3).
   - **Google Refresh Token**: (Tự động tạo hoặc cấp quyền 1 lần).
4. Nhấn **"Lưu Cấu Hình"**.

Từ thời điểm này, mọi chiến dịch anh tạo trên phần mềm sẽ tự động đẩy sang Google Ads và đồng bộ trạng thái bật/tắt tức thì.
