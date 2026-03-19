# Hệ Thống AI Camera & Nhận Diện Biển Số YOLOv26 (Orange Pi 4 Pro)

Hệ thống giám sát thông minh tích hợp nhận diện biển số (LPR), nhận diện khuôn mặt và quản lý bãi xe tập trung. Tối ưu hóa cho thiết bị biên ARM (Orange Pi) và chạy toàn bộ trên nền tảng Docker.

## 🚀 Tính Năng Chính

*   **Nhận diện biển số (ALPR):** Sử dụng YOLOv26 và PaddleOCR để nhận diện biển số xe máy/ô tô với độ chính xác cao.
*   **Nhận diện khuôn mặt:** Tích hợp InsightFace (buffalo_l) cho phép nhận diện danh tính người ra vào.
*   **Giao diện Dashboard:** Xây dựng trên nền tảng Streamlit, hiển thị trực quan live stream, lịch sử ra vào và quản lý Whitelist.
*   **Cơ sở dữ liệu tập trung:** PostgreSQL 16 để quản lý dữ liệu bền vững.
*   **Quản lý hoàn toàn qua Streamlit:** Toàn bộ thao tác quản trị, giám sát và cấu hình đều thực hiện trên giao diện Streamlit.

## 📂 Cấu Trúc Thư Mục

*   `docker/`: Chứa Dockerfile và cấu hình dịch vụ (`init.sql` cho Postgres).
*   `core/`: Nhân lõi xử lý Logic, Database, và Cấu hình hệ thống.
*   `streamlit_app/`: Mã nguồn giao diện Web chính.
*   `models/`: Nơi lưu trữ các model AI (.pt, .onnx).

## 🛠 Hướng Dẫn Cài Đặt

### 1. Chuẩn bị môi trường
Yêu cầu Docker và Docker Compose phải được cài đặt trên máy.

```bash
# Clone dự án
git clone https://github.com/satosis/yolov26-nhandien-biensoxe.git
cd yolov26-nhandien-biensoxe

# Tạo môi trường ảo Python (cho Web UI chạy host)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Cấu hình .env
Sao chép `.env.example` thành `.env` và điền các thông tin quan trọng:
*   `POSTGRES_PASSWORD`: Mật khẩu cơ sở dữ liệu.
*   `TELEGRAM_TOKEN`: Token bot để nhận thông báo.

### 3. Khởi động các dịch vụ

```bash
docker compose up -d --build
```
Hệ thống sẽ khởi chạy: **Postgres và AI Core.**

### 4. Chạy Giao diện Web

```bash
streamlit run streamlit_app/main.py
```

## ⚙️ Thông Số Kỹ Thuật

*   **OS:** Armbian/Ubuntu (Optimized for Orange Pi 4 Pro).
*   **Model:** YOLOv26 Triple-Task Detector (yolo26_plate_people_door.onnx), InsightFace (face).
*   **Database:** PostgreSQL 16.
*   **UI/Management:** Streamlit.

## 🔒 Bảo Mật & Whitelist
*   Quản lý biển số xe quen trực tiếp trên giao diện Web.
*   Hỗ trợ ghi log lịch sử ảnh chụp xe ngay khi đi qua vạch ranh giới (Tripwire).
*   Hệ thống tự động bỏ qua nhận diện khi camera quay sang vị trí khác (PTZ Tracking).
