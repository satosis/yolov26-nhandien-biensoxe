# 🚗 Camera AI — Giám sát bãi giữ xe thông minh

[![License: Proprietary](https://img.shields.io/badge/License-Proprietary-red.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-teal.svg)](https://fastapi.tiangolo.com/)

> Hệ thống AI giám sát bãi xe trên **Orange Pi 4 Pro (ARM)**: nhận diện biển số VN, khuôn mặt, đếm người/xe, điều khiển cửa cuốn và cảnh báo Telegram.

## ✨ Tính năng

| Tính năng | Mô tả |
|---|---|
| 🔍 Nhận diện biển số | YOLOv2.6 + AsterOCR (asternet ONNX, biển số Việt Nam) |
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

**Stack:** YOLOv2.6 ONNX + AsterOCR + InsightFace · FastAPI/Uvicorn · Jinja2 + HTMX + Tailwind · PostgreSQL 16 · Telegram Bot API · Tuya/Tasmota/MQTT

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

## OCR dùng chung và nhận diện biển số

- `backend/util/ocr/pipeline.py` dùng chung cho biển số, hóa đơn và chữ viết tay: đánh giá chất lượng, giữ ảnh gốc trước khi thử các biến thể, tiền xử lý có giới hạn bộ nhớ, chọn kết quả và tổng hợp theo thời gian. Mỗi loại OCR giữ model, quy tắc và bố cục riêng.
- Biển số được đọc theo từng dòng chữ: khoanh mặt biển trong crop, nắn thẳng, cắt sát từng dòng rồi giải mã theo cấu trúc biển số Việt Nam (mã tỉnh, seri, 4–5 số) trong `backend/util/ocr/plate_grammar.py`. Nhiều khung của cùng một xe được gộp bằng chứng; biển quá nhỏ thì đọc thêm số kẻ trên thân xe ngay quanh biển.
- Màn nhận diện mặc định chỉ trả về **người và biển số**, không tải mô hình nhận diện danh tính khuôn mặt hoặc hiển thị cửa.
- Bốn tab **Camera / Webcam / Ảnh / Video** của màn nhận diện dùng chung một cấu hình (`PROFILES["page"]` trong `backend/ai/plate_analysis.py`), một đường xử lý (`detect_frame` trong `frontend/routers/detection/plate_helpers.py`) và một danh sách thẻ kết quả; tab chỉ khác nhau ở nơi lấy khung hình. Ảnh là luồng một khung đứng yên, giống video đang tạm dừng.
- Tab **Video** tự phát và phân tích khung hình đang xem ngay khi chọn tệp; trạng thái và nút dừng nằm gọn trong khung video. Phát, tạm dừng và tua dùng chung một luồng (tạm dừng chốt các track đang theo dõi); khung vẽ trên video chỉ hiển thị gần đúng thời điểm đã phân tích, tránh mang khung cũ sang xe khác khi tua.

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

Thêm `--ui` (cần ứng dụng đang chạy ở cổng 8001) để mở từng trang bằng Chrome thật và báo lỗi JavaScript, request lỗi, ảnh hỏng; chạy riêng bằng `python scripts/ui_check.py` (thêm `--click` để bấm thử modal/tab/menu, `--page /đường-dẫn` để chọn trang). `python scripts/ui_check.py --goal "mô tả việc cần làm"` cho Claude (API, cần `UI_CHECK_ANTHROPIC_API_KEY` trong `.env`) tự thao tác theo từng bước; mọi request ghi đều bị chặn.

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
