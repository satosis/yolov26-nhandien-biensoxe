# 🚗 Camera AI — Giám sát bãi giữ xe thông minh

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-teal.svg)](https://fastapi.tiangolo.com/)

> Hệ thống AI giám sát bãi xe trên **Orange Pi 4 Pro (ARM)**: nhận diện biển số VN, khuôn mặt, đếm người/xe, điều khiển cửa cuốn và cảnh báo Telegram.

## ✨ Tính năng

| Tính năng | Mô tả |
|---|---|
| 🔍 Nhận diện biển số | YOLOv2.6 + RapidOCR (biển số Việt Nam) |
| 👤 Nhận diện khuôn mặt | InsightFace, phát hiện người lạ (threshold 0.25) |
| 📊 Đếm người/xe | TripwireTracker (vạch ảo) |
| 🚪 Cửa cuốn | Tuya, Tasmota, Shelly, Sonoff, BroadLink, GPIO, MQTT |
| 📱 Telegram | Multi-bot, 2 kênh (quan trọng / thường) |
| 🎥 PTZ + WebRTC | ONVIF + Imou Open API, mobile view qua TURN |
| 🏢 Multi-site + RBAC | Nhiều bãi xe, phân quyền menu DB-driven |
| 🌐 Web Management | FastAPI + Jinja2 + HTMX + Tailwind CSS |

## 🏗️ Kiến trúc

```
RTSP Camera (Imou 2K)
    ├─► backend/main.py (AI core: YOLO track + OCR + TripwireTracker)
    │       ├─► backend/services/database.py (PostgreSQL)
    │       └─► backend/services/telegram_service.py (cảnh báo)
    └─► frontend/ (FastAPI — quản lý hệ thống)
            ├─► routes.py / handlers/ / modules/ / routers/
            ├─► templates/ (Jinja2)
            └─► menu_registry.py (single source of truth)
```

**Stack:** YOLOv2.6 ONNX + RapidOCR + InsightFace · FastAPI/Uvicorn · Jinja2 + HTMX + Tailwind · PostgreSQL 16 · Telegram Bot API · Tuya/Tasmota/MQTT

## 🚀 Cài đặt

```bash
git clone https://github.com/satosis/yolov26-nhandien-biensoxe.git
cd yolov26-nhandien-biensoxe
cp .env.example .env          # sửa DATABASE_URL, DB_USERNAME, DB_PASSWORD với thông tin thực
pip install -r backend/requirements.txt
python scripts/dev_stack.py   # web (cổng 8001) + AI backend + cầu nối Zalo + Cloudflare Tunnel
```

`dev_stack.py` chạy cả bốn dịch vụ trong một terminal, nhấn `Ctrl+C` để dừng. Web tự tải lại khi mã Python, template, CSS hoặc JavaScript trong `frontend/` thay đổi; AI backend tự khởi động lại khi mã Python trong `backend/` hoặc JavaScript của cầu nối Zalo thay đổi. Cầu nối Zalo chỉ chạy khi đã kết nối tài khoản Zalo (cần `npm ci` trong `scripts/zalo_bridge/`). Tunnel chỉ chạy khi `.env` có `CLOUDFLARE_TUNNEL_TOKEN` và máy có `cloudflared`.

Trên Windows có thể chạy `start_cloudflare_tunnel.bat` hoặc nhấn `Ctrl+Shift+B` trong VS Code. Truy cập: `http://localhost:8001`.

## 🗄️ Database Schema (chính)

`roles`, `users`, `sites`, `cameras`, `plate_events`, `vehicle_sessions`, `plate_whitelist`, `pending_plates`, `app_settings`, `telegram_bots`, `camera_providers`, `door_providers`, `stranger_logs`, `ocr_training_samples`, `cameras_asset`

## OCR dùng chung và phân tích video

- `backend/util/ocr/pipeline.py` dùng chung cho biển số, hóa đơn và chữ viết tay: đánh giá chất lượng, giữ ảnh gốc trước khi thử các biến thể, tiền xử lý có giới hạn bộ nhớ, chọn kết quả và tổng hợp theo thời gian. Mỗi loại OCR giữ model, quy tắc và bố cục riêng.
- Màn nhận diện mặc định chỉ trả về **người và biển số**, không tải mô hình nhận diện danh tính khuôn mặt hoặc hiển thị cửa.
- Tab **Video** tự phát và tự phân tích ngay khi chọn tệp, dùng chế độ **Theo video**. Trạng thái và nút dừng nằm gọn trong khung video; không có bảng cấu hình hoặc bước nhấn phân tích. Chọn tệp khác sẽ hủy tác vụ cũ; phát, tạm dừng và tua không tạo tác vụ trùng.
- **Theo video** tự điều chỉnh nhịp lấy mẫu theo thời gian xử lý thực tế, tối đa 8 frame/giây, để tránh tích hàng đợi khi máy chậm. OCR đọc trực tiếp các dòng biển số trên tối đa 3 crop/track, không chạy bộ tìm dòng chữ. Máy yếu có thể chỉ quét được rất ít frame và bỏ sót xe nhanh. API vẫn hỗ trợ **Chi tiết** (từng frame, thêm vùng giữa khi chưa chọn ROI) và **Nhanh** (khoảng 5 frame/giây) cho các công cụ gọi riêng.
- Worker đọc file nguyên bản, giữ timestamp và hướng xoay; mỗi track chỉ OCR tối đa 5 crop đã chọn (3 ở chế độ Theo video). Chỉ tải OCR khi có crop biển số cần đọc; các phiên OCR trong tiến trình video dùng số luồng giới hạn và tắt chờ bận. Hai lần đọc toàn biển thống nhất mới có thể ở trạng thái **Đã đọc**; một lần đọc ở trạng thái **Chưa chắc chắn**. **Không đọc được** vẫn giữ ảnh bằng chứng. Người và biển số có track riêng; người không chạy OCR. Khung vẽ trên video chỉ hiển thị gần đúng timestamp được phân tích, tránh mang khung cũ sang xe khác khi tua.
- API tác vụ: `POST /admin/detection/api/videos`, `GET /admin/detection/api/videos/{job_id}`, `GET /admin/detection/api/videos/latest`, `POST /admin/detection/api/videos/{job_id}/cancel`. Các tác vụ và ảnh kết quả được giới hạn theo người dùng và địa điểm. Khi chuyển trang rồi quay lại, tab Video tự nối với tác vụ đang chạy hoặc kết quả gần nhất. Luồng video tạo báo cáo riêng, không ghi sự kiện bãi xe/người lạ từ từng frame.
- Web xếp hàng tối đa 3 tác vụ; mỗi lần chỉ chạy một tiến trình phân tích video riêng. Trên Windows, tiến trình đó dùng ưu tiên thấp hơn web và giới hạn mặc định 2 lõi CPU/2 luồng suy luận để các trang khác vẫn phản hồi. Giới hạn file 512 MB, video 60 phút. Chạy web với **một Uvicorn worker**; trạng thái tác vụ không tiếp tục sau khi restart. File nguồn được xóa khi kết thúc/hủy; ảnh kết quả được dọn sau khoảng 1 giờ hoặc sớm hơn khi lịch sử vượt 24 tác vụ. Không cần thêm dịch vụ hàng đợi.

## 💬 Cầu nối Zalo

Cầu nối `scripts/zalo_bridge` dùng `zca-js` (thư viện không chính thức, chạy bằng phiên Zalo cá nhân) để nhận tin từ các nhóm.

- Cài lần đầu: `npm ci` trong `scripts/zalo_bridge`; vào **Hệ thống → Yêu cầu Zalo**, bấm **Kết nối Zalo** và quét QR bằng đúng tài khoản đang ở trong nhóm. Bấm **Ngắt kết nối** thì backend không tự bật lại cho đến khi kết nối lại.
- Cầu nối chạy theo backend: `python backend/main.py` tự bật khi đã có `session.json`, kiểm tra mỗi 30 giây và bật lại nếu nó chết.
- Phiên đăng nhập và dữ liệu nhận được nằm trong `frontend/.runtime/zalo_inbox/`, ngoài Git. Không chia sẻ `session.json` hay `bridge-secret`.
- **Tự gom đơn:** tin trong nhóm công ty có mã số thuế, hoặc nhắc tên khách hàng trong Danh sách đơn vị (tên đầy đủ, vài chữ cuối tên, hoặc một phần tên viết ngay sau "công ty"), mở phiên gom 3 phút. Các tin sau trong phiên (giá, địa điểm, vị trí, xe) được ghép thành hóa đơn nháp. Thiếu điểm đi hoặc điểm đến thì hệ thống hỏi lại trong nhóm, không tự điền địa chỉ mặc định.
- **Nhóm lệnh AI:** nhóm Zalo tên **AI** (đổi bằng file `ai-group-name`). Mọi tin là một yêu cầu cho Claude Code; từ khóa riêng: `status`, `stop`, `new`, `help`. Mỗi lúc chỉ chạy một lệnh, quá 60 phút tự dừng (`ZALO_AI_TIMEOUT_MS`). Mọi thành viên đều ra lệnh được, nên chỉ thêm người tin tưởng hoàn toàn.

## Kiểm tra trước khi push

```bash
python scripts/check_all.py
```

Lệnh trên chạy test của Zalo bridge, biên dịch mọi template Jinja2 và kiểm tra cú pháp các file JavaScript tự viết, rồi xuất bảng tổng kết.

## ⚙️ Thông số kỹ thuật

| Thông số | Giá trị |
|---|---|
| Python | 3.10 (prod), 3.11 (dev) |
| Model | `yolo26_plate_people_door_int8.onnx` (YOLOv2.6 INT8) |
| Face threshold | 0.25 |
| Stranger / pending cooldown | 60s |
| OCR sample range | confidence 0.3–0.7 |
| Remember token | `secrets.token_urlsafe(32)` |
| Data retention | 30 ngày (legal hold không xóa) |
