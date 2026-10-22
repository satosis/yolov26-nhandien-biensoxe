# Nhận yêu cầu từ nhóm Zalo cá nhân

Cầu nối dùng `zca-js` để nhận tin từ nhóm **Đại Lộ xanh** bằng tài khoản Zalo cá nhân. Đây là thư viện không chính thức dùng phiên Zalo Web; Zalo có thể hạn chế tài khoản. Chỉ có một phiên lắng nghe Web cho mỗi tài khoản, nên mở Zalo Web ở nơi khác có thể ngắt kết nối.

1. Chạy `npm ci` trong thư mục này sau khi cài code lần đầu.
2. Mở **Hệ thống → Yêu cầu Zalo** trong Parking Camera, bấm **Kết nối Zalo** và quét QR bằng đúng tài khoản tham gia nhóm.
3. Tin nhắn mới sẽ vào hộp chờ. Ảnh/PDF tải được từ Zalo có nút **Đọc nội dung tệp**.
4. Với tin nhắn mới nêu rõ người nhận, số tiền và tiền lương (ví dụ “Bích chuyển cho chú Tư 3 triệu tiền lương được không?”), hệ thống tìm đúng một nhân sự theo tên hoặc nickname và lưu tạm ứng vào kỳ lương của ngày gửi tin. Hệ thống không gửi mã QR tạm ứng lên Zalo. Mọi thành viên trong nhóm đều có thể kích hoạt luồng này. Khoản tạm ứng bị từ chối khi chưa có hồ sơ lương tháng, thiếu thông tin ngân hàng, số tiền vượt lương còn lại hoặc tên trùng nhiều người.
5. Đặt nickname trong **Hệ thống → Người dùng**; người được nhận cần thuộc công ty và có hồ sơ lương. Trạng thái xử lý hiện trên **Yêu cầu Zalo**. Khoản tạm ứng đã lưu không bị tạo lại khi bấm xử lý tin cũ.

Luồng tự động chỉ áp dụng cho tin nhắn nhận **sau khi ứng dụng và cầu nối chạy mã mới**. Tin cũ trong hộp thư không được xử lý lại tự động.

Tin do chính tài khoản Zalo đang kết nối gửi vào nhóm cũng được xử lý. Nếu gửi tin lúc cầu nối không chạy, hãy gửi lại tin sau khi trạng thái hiển thị **đang nhận tin**; Zalo không cung cấp lịch sử nhóm qua cầu nối hiện tại.

Phiên đăng nhập và dữ liệu nhận được nằm trong `frontend/.runtime/zalo_inbox/`, ngoài Git. Không chia sẻ file `session.json` hoặc `bridge-secret`. Số điện thoại mong đợi nằm trong `expected-phone` tại cùng thư mục; cầu nối đối chiếu nếu Zalo trả về số đầy đủ.

Để ngừng nhận tin, bấm **Ngắt kết nối**. Trạng thái và QR được hiển thị tại trang quản trị; log cục bộ là `frontend/.runtime/zalo_inbox/bridge.log`.
