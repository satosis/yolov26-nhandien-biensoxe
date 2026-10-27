# 🚗 Camera AI — Giám sát bãi giữ xe thông minh

![Parking AI Hero](assets/hero.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-teal.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-blue.svg)](https://docs.docker.com/compose/)

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
    ├─► main.py (AI core: YOLO track + OCR + TripwireTracker)
    │       ├─► backend/core/database.py (PostgreSQL)
    │       └─► backend/core/telegram_service.py (cảnh báo)
    └─► frontend/ (FastAPI — quản lý hệ thống)
            ├─► routers/ (17 route handlers)
            ├─► templates/ (Jinja2)
            └─► menu_registry.py (single source of truth)
```

**Stack:** YOLOv2.6 ONNX + RapidOCR + InsightFace · FastAPI/Uvicorn · Jinja2 + HTMX + Tailwind · PostgreSQL 16 · Telegram Bot API · Tuya/Tasmota/MQTT · Docker Compose

## 🚀 Cài đặt

```bash
git clone https://github.com/satosis/yolov26-nhandien-biensoxe.git
cd yolov26-nhandien-biensoxe
cp .env.example .env          # sửa DATABASE_URL, POSTGRES_* với thông tin thực
```

**Chạy với Docker (khuyến nghị):**

```bash
docker compose up -d                          # khởi chạy
docker compose logs -f ai_core                # xem logs
docker compose --profile db_admin up -d       # kèm Adminer
```

**Chạy thủ công (không Docker):**

```bash
pip install -r requirements.txt
cd frontend
uvicorn app:app --host 0.0.0.0 --port 8000   
export PYTHONPATH=$PYTHONPATH:$(pwd)
python main.py       
```

**Truy cập:** Web Management `http://localhost:8080` · Adminer `http://localhost:8888`

## 🗄️ Database Schema (chính)

`roles`, `users`, `sites`, `cameras`, `plate_events`, `vehicle_sessions`, `plate_whitelist`, `pending_plates`, `app_settings`, `telegram_bots`, `camera_providers`, `door_providers`, `stranger_logs`, `ocr_training_samples`, `cameras_asset`

## OCR dùng chung và phân tích video

- `backend/util/ocr_pipeline.py` dùng chung cho biển số, hóa đơn và chữ viết tay: đánh giá chất lượng, giữ ảnh gốc trước khi thử các biến thể, tiền xử lý có giới hạn bộ nhớ, chọn kết quả và tổng hợp theo thời gian. Mỗi loại OCR giữ model, quy tắc và bố cục riêng.
- Màn nhận diện mặc định chỉ trả về **người và biển số**, không tải mô hình nhận diện danh tính khuôn mặt hoặc hiển thị cửa.
- Tab **Video** tự phát và tự phân tích ngay khi chọn tệp, dùng chế độ **Theo video**. Trạng thái và nút dừng nằm gọn trong khung video; không có bảng cấu hình hoặc bước nhấn phân tích. Chọn tệp khác sẽ hủy tác vụ cũ; phát, tạm dừng và tua không tạo tác vụ trùng.
- **Theo video** tự điều chỉnh nhịp lấy mẫu theo thời gian xử lý thực tế, tối đa 8 frame/giây, để tránh tích hàng đợi khi máy chậm. OCR đọc trực tiếp các dòng biển số trên tối đa 3 crop/track, không chạy bộ tìm dòng chữ. Máy yếu có thể chỉ quét được rất ít frame và bỏ sót xe nhanh. API vẫn hỗ trợ **Chi tiết** (từng frame, thêm vùng giữa khi chưa chọn ROI) và **Nhanh** (khoảng 5 frame/giây) cho các công cụ gọi riêng.
- Worker đọc file nguyên bản, giữ timestamp và hướng xoay; mỗi track chỉ OCR tối đa 5 crop đã chọn (3 ở chế độ Theo video). Chỉ tải OCR khi có crop biển số cần đọc; các phiên OCR trong tiến trình video dùng số luồng giới hạn và tắt chờ bận. Hai lần đọc toàn biển thống nhất mới có thể ở trạng thái **Đã đọc**; một lần đọc ở trạng thái **Chưa chắc chắn**. **Không đọc được** vẫn giữ ảnh bằng chứng. Người và biển số có track riêng; người không chạy OCR. Khung vẽ trên video chỉ hiển thị gần đúng timestamp được phân tích, tránh mang khung cũ sang xe khác khi tua.
- API tác vụ: `POST /admin/detection/api/videos`, `GET /admin/detection/api/videos/{job_id}`, `GET /admin/detection/api/videos/latest`, `POST /admin/detection/api/videos/{job_id}/cancel`. Các tác vụ và ảnh kết quả được giới hạn theo người dùng và địa điểm. Khi chuyển trang rồi quay lại, tab Video tự nối với tác vụ đang chạy hoặc kết quả gần nhất. Luồng video tạo báo cáo riêng, không ghi sự kiện bãi xe/người lạ từ từng frame.
- Web xếp hàng tối đa 3 tác vụ; mỗi lần chỉ chạy một tiến trình phân tích video riêng. Trên Windows, tiến trình đó dùng ưu tiên thấp hơn web và giới hạn mặc định 2 lõi CPU/2 luồng suy luận để các trang khác vẫn phản hồi. Giới hạn file 512 MB, video 60 phút. Chạy web với **một Uvicorn worker**; trạng thái tác vụ không tiếp tục sau khi restart. File nguồn được xóa khi kết thúc/hủy; ảnh kết quả được dọn sau khoảng 1 giờ hoặc sớm hơn khi lịch sử vượt 24 tác vụ. Không cần thêm dịch vụ hàng đợi.

Kiểm thử không tải model hoặc ghi dữ liệu nghiệp vụ:

```bash
python -m unittest discover -s tests -v
```

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
