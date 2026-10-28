# 📦 Hướng Dẫn Cài Đặt & Triển Khai (INSTALL.md)

Hệ thống **Camera AI — Giám sát bãi giữ xe thông minh** hỗ trợ nhiều hình thức cài đặt linh hoạt:
- [1. Yêu cầu hệ thống](#1-yêu-cầu-hệ-thống)
- [2. Cài đặt thủ công (PC / Mac / Linux Server)](#2-cài-đặt-thủ-công-pc--mac--linux-server)
- [3. Cài đặt bằng Docker Compose](#3-cài-đặt-bằng-docker-compose)
- [4. Triển khai chuyên biệt trên NVIDIA Jetson (Jetson Nano / Xavier / Orin)](#4-triển-khai-chuyên-biệt-trên-nvidia-jetson)
- [5. Tự động khởi động cùng hệ điều hành (Systemd)](#5-tự-động-khởi-động-cùng-hệ-điều-hành-systemd)
- [6. Xử lý các lỗi thường gặp (Troubleshooting)](#6-xử-lý-các-lỗi-thường-gặp-troubleshooting)

---

## 1. Yêu cầu hệ thống

* **Hệ điều hành:** Linux (Ubuntu 18.04+, Debian, JetPack), macOS, hoặc Windows.
* **Python:** `3.10+` (Khuyến nghị 3.10 hoặc 3.11).
* **Cơ sở dữ liệu:** PostgreSQL 14+ (Khuyến nghị PostgreSQL 16).
* **RAM:** Tối thiểu 4GB RAM vật lý (khuyến nghị có 4GB - 8GB Swap trên các thiết bị Edge như Jetson Nano/Orange Pi).
* **Phần cứng suy luận:**
  * CPU thông thường: Tự động chạy ONNX Runtime INT8 (`yolo26_plate_people_door_int8.onnx`).
  * NVIDIA GPU / Jetson: Hỗ trợ CUDA / TensorRT FP16 (`yolo26_plate_people_door.onnx`).
  * Orange Pi / RK3588: Hỗ trợ NPU thông qua RKNN.

---

## 2. Cài đặt thủ công (PC / Mac / Linux Server)

### Bước 2.1: Clone mã nguồn và chuẩn bị môi trường
```bash
git clone https://github.com/satosis/yolov26-nhandien-biensoxe.git
cd yolov26-nhandien-biensoxe

# Tạo và kích hoạt môi trường ảo Python 3.10+
python3 -m venv venv
source venv/bin/activate    # Trên Windows: venv\Scripts\activate

# Nâng cấp pip và cài đặt thư viện
pip install --upgrade pip setuptools wheel
pip install -r backend/requirements.txt
```

### Bước 2.2: Cài đặt và cấu hình PostgreSQL
```bash
# Trên Ubuntu/Debian:
sudo apt update
sudo apt install -y postgresql postgresql-contrib libpq-dev

# Đặt mật khẩu cho user postgres và tạo database camera_ai
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres123';"
sudo -u postgres createdb camera_ai

# Nạp dữ liệu khởi tạo ban đầu
sudo -u postgres psql -d camera_ai -f data.sql
```

### Bước 2.3: Thiết lập file cấu hình môi trường `.env`
Sao chép file mẫu:
```bash
cp .env.example .env
```
Mở file `.env` và cập nhật thông tin database thực tế:
```ini
DB_USERNAME=postgres
DB_PASSWORD=postgres123
DATABASE_URL=postgresql://postgres:postgres123@127.0.0.1:5432/camera_ai
SESSION_SECRET=thay-the-bang-mot-chuoi-khoa-ngau-nhien-32-ky-tu
```

### Bước 2.4: Khởi chạy hệ thống

**Cách A: Sử dụng script tự động có sẵn (`install.sh`)**
```bash
chmod +x install.sh
./install.sh
```

**Cách B: Khởi chạy thủ công bằng 2 terminal riêng biệt**
* **Terminal 1: Khởi động Web Management (FastAPI/Uvicorn)**
  ```bash
  source venv/bin/activate
  cd frontend
  uvicorn app:app --host 0.0.0.0 --port 8000 --reload
  ```
* **Terminal 2: Khởi động AI Core (Nhận diện biển số, theo dõi luồng RTSP)**
  ```bash
  source venv/bin/activate
  export PYTHONPATH=$PYTHONPATH:$(pwd)
  python3 backend/main.py
  ```

👉 **Truy cập:** Web quản lý tại `http://localhost:8000`

---

## 3. Cài đặt bằng Docker Compose

Dành cho môi trường muốn triển khai nhanh, cô lập tài nguyên:

```bash
# 1. Cấu hình .env
cp .env.example .env

# 2. Khởi chạy toàn bộ hệ thống
docker compose up -d

# 3. Theo dõi log hoạt động của AI Core
docker compose logs -f ai_core
```

---

## 4. Triển khai chuyên biệt trên NVIDIA Jetson (Đặc biệt Jetson Nano)

Áp dụng cho: **Jetson Nano**, **Xavier NX**, **AGX Xavier**, **Orin Nano**, **Orin NX**, **AGX Orin**.

### ⚡ Triển khai tự động bằng script (Khuyến nghị)

Chạy 1 lệnh duy nhất từ thư mục dự án trên Jetson Nano:

```bash
chmod +x scripts/deploy_jetson_nano.sh && ./scripts/deploy_jetson_nano.sh
```

*(Script [scripts/deploy_jetson_nano.sh](scripts/deploy_jetson_nano.sh) sẽ tự động tối ưu phần cứng, tạo swap 8GB, cấu hình PostgreSQL, cài Python 3.10, tạo venv và cấu hình 2 systemd service `camera-web` & `camera-ai`).*

### ⚠️ Lưu ý phần cứng cực kỳ quan trọng cho Jetson Nano:
1. **Nguồn điện:** Bắt buộc dùng nguồn cắm tròn **DC Barrel Jack 5V - 4A** và cắm jumper vào chân **J48**. Tuyệt đối **không dùng cổng Micro-USB** vì GPU nhận diện AI sẽ gây sụt áp làm tắt máy đột ngột.
2. **Bộ nhớ ảo (Swap):** Jetson Nano dùng chung 4GB RAM cho cả CPU/GPU, bắt buộc tạo **8GB Swap**.

---

### Chi tiết từng bước thủ công (nếu muốn debug riêng rẽ):

#### Bước 4.1: Tối ưu công suất & Tạo Swap
```bash
# Bật chế độ tối đa công suất 10W (MAXN) và bật quạt
sudo nvpmodel -m 0
sudo jetson_clocks

# Tạo 8GB Swap
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo "/swapfile swap swap defaults 0 0" | sudo tee -a /etc/fstab
```

#### Bước 4.2: Cài Python 3.10 trên Jetson Nano (JetPack 4.6 / Ubuntu 18.04)
Ubuntu 18.04 mặc định chỉ có Python 3.6. Cài đặt nhanh Python 3.10 từ source:
```bash
sudo apt update
sudo apt install -y build-essential zlib1g-dev libssl-dev libffi-dev libsqlite3-dev libbz2-dev wget
cd /tmp
wget https://www.python.org/ftp/python/3.10.12/Python-3.10.12.tgz
tar -xf Python-3.10.12.tgz
cd Python-3.10.12
./configure --enable-optimizations
make -j4
sudo make altinstall
```

#### Bước 4.3: Cài đặt thư viện & dependencies
```bash
cd ~/yolov26-nhandien-biensoxe
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r backend/requirements.txt
```

#### Bước 4.4: Tăng tốc tối đa với TensorRT FP16
Chuyển đổi mô hình YOLO sang TensorRT Engine để chạy trực tiếp trên GPU Maxwell:
```bash
/usr/src/tensorrt/bin/trtexec \
  --onnx=backend/models/yolo26_plate_people_door.onnx \
  --saveEngine=backend/models/yolo26_plate_people_door.engine \
  --fp16
```

---

## 5. Tự động khởi động cùng hệ điều hành (Systemd)

Để hệ thống tự khởi động lại khi cắm nguồn hoặc khi bị crash, sử dụng 2 file service cấu hình sẵn tại `scripts/systemd/`:

1. **Chỉnh sửa đúng username và đường dẫn thư mục** trong 2 file:
   - `scripts/systemd/camera-web.service`
   - `scripts/systemd/camera-ai.service`

2. **Kích hoạt service:**
```bash
# Copy vào thư mục systemd
sudo cp scripts/systemd/camera-web.service /etc/systemd/system/
sudo cp scripts/systemd/camera-ai.service /etc/systemd/system/

# Reload & kích hoạt
sudo systemctl daemon-reload
sudo systemctl enable camera-web camera-ai
sudo systemctl start camera-web camera-ai
```

3. **Quản lý & xem log thời gian thực:**
```bash
# Xem trạng thái:
sudo systemctl status camera-web
sudo systemctl status camera-ai

# Xem logs trực tiếp:
journalctl -u camera-ai -f
```

---

## 6. Xử lý các lỗi thường gặp (Troubleshooting)

| Lỗi gặp phải | Nguyên nhân | Cách xử lý |
|---|---|---|
| `ModuleNotFoundError: No module named 'backend'` | Chưa export biến môi trường `PYTHONPATH` | Chạy `export PYTHONPATH=$PYTHONPATH:$(pwd)` trước khi chạy `main.py` |
| `psycopg2.OperationalError: connection to server at "127.0.0.1" failed` | Dịch vụ PostgreSQL chưa chạy hoặc sai mật khẩu | Kiểm tra `sudo systemctl status postgresql` và kiểm tra lại `DATABASE_URL` trong `.env` |
| Jetson Nano tự sập nguồn khi camera vừa bật | Sụt áp do dùng cổng cấp nguồn Micro-USB 5V-2A | Chuyển sang nguồn cắm tròn DC Jack 5V-4A và nối jumper chân J48 |
| Tiến trình Python bị `Killed` đột ngột | Tràn bộ nhớ RAM (OOM Killer) | Kiểm tra `free -h` xem đã bật Swap chưa, làm theo Bước 4.1 để bật Swap 8GB |
| Cổng `8000` hoặc `5432` báo `Address already in use` | Tiến trình cũ vẫn đang chạy ngầm | Tìm và kill tiến trình cũ: `sudo fuser -k 8000/tcp` |
