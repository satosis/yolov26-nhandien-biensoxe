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
uvicorn app:app --host 0.0.0.0 --port 8000   # Frontend
export PYTHONPATH=$PYTHONPATH:$(pwd)
python main.py       # AI Core
```

**Truy cập:** Web Management `http://localhost:8080` · Adminer `http://localhost:8888`

## 🗄️ Database Schema (chính)

`roles`, `users`, `sites`, `cameras`, `plate_events`, `vehicle_sessions`, `plate_whitelist`, `pending_plates`, `app_settings`, `telegram_bots`, `camera_providers`, `door_providers`, `stranger_logs`, `ocr_training_samples`, `cameras_asset`

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
