# 🚗 Camera AI — Hệ thống giám sát bãi giữ xe thông minh

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-teal.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-blue.svg)](https://docs.docker.com/compose/)

> Hệ thống giám sát bãi giữ xe thông minh chạy trên **Orange Pi 4 Pro (ARM)**, tích hợp AI nhận diện biển số xe Việt Nam, nhận diện khuôn mặt, đếm người/xe, điều khiển cửa cuốn tự động và cảnh báo Telegram.

---

## ✨ Tính năng chính

| Tính năng | Mô tả |
|---|---|
| 🔍 **Nhận diện biển số** | YOLOv2.6 + PaddleOCR, hỗ trợ biển số Việt Nam |
| 👤 **Nhận diện khuôn mặt** | InsightFace buffalo_sc, phát hiện người lạ (threshold 0.25) |
| 📊 **Đếm người/xe** | TripwireTracker — vạch ảo đếm qua camera |
| 📸 **Phát hiện camera lệch** | ORB feature matching + RANSAC |
| 🚪 **Điều khiển cửa cuốn** | Tuya, Tasmota, Shelly, Sonoff, BroadLink, GPIO, MQTT |
| 📱 **Cảnh báo Telegram** | Multi-bot, 2 kênh (quan trọng / thường) |
| 🎥 **PTZ Camera** | ONVIF + Imou Open API |
| 📲 **Mobile WebRTC** | Camera view qua TURN server, hỗ trợ camera sau |
| 🏢 **Multi-site** | Quản lý nhiều bãi xe, mỗi site có zone/camera riêng |
| 🔐 **RBAC** | Phân quyền menu theo role (DB-driven) |
| 🌐 **Web Management** | FastAPI + Jinja2 + HTMX + Tailwind CSS |

## 🏗️ Kiến trúc hệ thống

```
RTSP Camera (Imou 2K)
    │
    ├─► main.py (AI core: YOLO track + OCR + TripwireTracker)
    │       ├─► backend/core/database.py (PostgreSQL)
    │       └─► backend/core/telegram_service.py (cảnh báo)
    │
    └─► frontend/ (FastAPI app — quản lý toàn bộ hệ thống)
            ├─► frontend/routers/ (17 route handlers)
            ├─► frontend/templates/ (Jinja2 HTML templates)
            └─► frontend/menu_registry.py (single source of truth cho menus)
```

## 🛠️ Stack công nghệ

| Thành phần | Công nghệ |
|---|---|
| AI Inference | YOLOv2.6 (Ultralytics), PaddleOCR, InsightFace |
| Backend | FastAPI + Uvicorn |
| Frontend | Jinja2 + HTMX + Tailwind CSS + Alpine.js |
| Camera | OpenCV RTSP, ONVIF, Imou Open API |
| Database | PostgreSQL 16 |
| Messaging | Telegram Bot API (multi-bot) |
| IoT | Tuya (tinytuya), Tasmota, MQTT |
| Container | Docker Compose |

## 🚀 Cài đặt & Chạy

### Yêu cầu

- Docker & Docker Compose
- Python 3.10+ (nếu chạy không Docker)

### 1. Clone repo

```bash
git clone https://github.com/satosis/yolov26-nhandien-biensoxe.git
cd yolov26-nhandien-biensoxe
```

### 2. Cấu hình môi trường

```bash
cp .env.example .env
# Sửa .env với thông tin thực tế
```

**Biến môi trường chính:**

```bash
# Database
DATABASE_URL=postgresql://camera_user:password@localhost:5432/camera_ai
POSTGRES_DB=camera_ai
POSTGRES_USER=camera_user
POSTGRES_PASSWORD=your_secure_password
```

### 3. Chạy với Docker Compose

```bash
# Khởi chạy hệ thống
docker compose up -d

# Xem logs
docker compose logs -f ai_core

# Với Adminer (quản lý DB)
docker compose --profile db_admin up -d
```

### 4. Chạy thủ công (không Docker)
Nếu muốn cài đặt thủ công:
```bash
# 1. Cài đặt Python 3.10+ và các thư viện cần thiết
pip install -r requirements.txt

# 2. Chạy Frontend (Web Management Interface)
cd frontend
uvicorn app:app --host 0.0.0.0 --port 8000

# 3. Chạy AI Core (Detection Engine) - Terminal mới
export PYTHONPATH=$PYTHONPATH:$(pwd)
python3 backend/main.py
```

## 🔗 Truy cập

| Dịch vụ | URL |
|---|---|
| Web Management | `http://localhost:8080` |
| Adminer (DB Admin) | `http://localhost:8888` |

## 🗄️ Database Schema

| Bảng | Mô tả |
|---|---|
| `roles` | RBAC roles + allowed_menus |
| `users` | Users + remember_token + site_ids |
| `sites` | Địa điểm bãi xe + total_slots |
| `cameras` | Camera config + site_ids (multi-site) |
| `plate_events` | Sự kiện nhận diện biển số |
| `vehicle_sessions` | Phiên đỗ xe |
| `plate_whitelist` | Whitelist biển số |
| `pending_plates` | Biển số lạ chờ duyệt |
| `app_settings` | Key-value settings |
| `telegram_bots` | Multi-bot Telegram config |
| `camera_providers` | Nhà cung cấp camera |
| `door_providers` | Nhà cung cấp cửa cuốn (7 loại) |
| `stranger_logs` | Log phát hiện người lạ |
| `ocr_training_samples` | OCR active learning samples |
| `cameras_asset` | CMDB camera assets |

## 📋 Tính năng chi tiết

### 🔍 AI Detection
- **YOLOv2.6 INT8** — Tối ưu cho ARM (Orange Pi 4 Pro)
- **PaddleOCR** — Nhận diện chữ trên biển số Việt Nam
- **InsightFace buffalo_sc** — Nhận diện khuôn mặt, phát hiện người lạ (stranger detection)
- **OCR Active Learning** — Confidence 0.3–0.7 lưu sample, admin review qua web

### 🚗 Quản lý xe
- **Whitelist**: Biển số đã đăng ký → hiển thị màu xanh
- **Unknown Plate → Pending**: Biển số lạ tự động vào hàng chờ duyệt (cooldown 60s)
- **Vehicle Sessions**: Theo dõi phiên đỗ xe

### 🚪 Điều khiển cửa cuốn
- 7 nhà cung cấp: **Tuya**, **Tasmota**, **Shelly**, **Sonoff**, **BroadLink**, **GPIO**, **MQTT**
- Tuya local SDK (tinytuya), toggle relay DPS "1"

### 📱 Telegram Alerts
- Multi-bot configuration
- 2 kênh cảnh báo: **Quan trọng** & **Thường**
- Bot username verification

### 🌐 Web Management
- **Mobile responsive** — CSS breakpoints 768px + 480px
- **RBAC** — Phân quyền menu theo role từ DB
- **Remember login** — Token-based, localStorage + DB
- **17 route handlers** — Quản lý toàn diện

## ⚙️ Thông số kỹ thuật

| Thông số | Giá trị |
|---|---|
| Python | 3.10.x (production), 3.11.x (dev) |
| Model | `models/best_int8.onnx` (YOLOv2.6 INT8) |
| Face threshold | 0.25 |
| Stranger cooldown | 60s per camera |
| Pending plate cooldown | 60s per plate |
| OCR sample range | Confidence 0.3–0.7 |
| Remember token | `secrets.token_urlsafe(32)` |
| Data retention | 30 ngày (legal hold không xóa) |

## 📄 License

[MIT License](LICENSE) © 2025 Hoang Quoc Phuong
