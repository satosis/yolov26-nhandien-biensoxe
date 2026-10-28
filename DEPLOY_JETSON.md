# 🚀 Hướng dẫn Triển khai Hệ thống Camera AI lên NVIDIA Jetson

Tài liệu này hướng dẫn chi tiết cách deploy hệ thống nhận diện biển số & quản lý bãi xe thông minh lên các dòng **NVIDIA Jetson** (Jetson Nano, Xavier NX, AGX Xavier, Orin Nano, Orin NX, AGX Orin).

---

## 📋 1. Chuẩn bị & Tối ưu hóa Jetson trước khi chạy

### 1.1. Bật chế độ hiệu năng tối đa (Max Performance)
Mặc định Jetson thường chạy ở chế độ tiết kiệm điện. Hãy bật công suất tối đa và quạt tản nhiệt:
```bash
# Chọn Power Mode cao nhất (chế độ 0 - MAXN)
sudo nvpmodel -m 0

# Ép xung CPU/GPU/EMC lên xung nhịp tối đa
sudo jetson_clocks
```

### 1.2. Kích hoạt bộ nhớ ảo (Swap File) — Cực kỳ quan trọng!
Trên các bản Jetson 4GB hoặc 8GB (như Jetson Nano, Orin Nano), mô hình AI (YOLO + InsightFace + RapidOCR) cùng các tiến trình có thể gây tràn RAM và kích hoạt Linux OOM Killer. Cần tạo ít nhất 4GB - 8GB Swap:
```bash
# Tạo file swap 8GB
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Tự động mount khi khởi động lại
echo "/swapfile swap swap defaults 0 0" | sudo tee -a /etc/fstab
```

### 1.3. Cài đặt công cụ giám sát `jtop`
```bash
sudo apt update && sudo apt install -y python3-pip
sudo -H pip3 install -U jetson-stats
sudo systemctl restart jtop.service

# Mở giao diện giám sát GPU, RAM, nhiệt độ:
jtop
```

---

## 🐳 2. Phương án 1: Triển khai bằng Docker Compose (Khuyến nghị)

Phương án này đóng gói đầy đủ: **PostgreSQL 16**, **go2rtc** (WebRTC stream), **Frontend Web UI** (Port 8000), và **AI Core** (GPU YOLO + OCR).

### Bước 2.1: Cấu hình NVIDIA Container Runtime cho Docker
Đảm bảo Docker trên Jetson có thể truy cập GPU:
```bash
# Kiểm tra nvidia container toolkit
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```
Kiểm tra cấu hình runtime trong `/etc/docker/daemon.json` xem đã có `"default-runtime": "nvidia"` chưa:
```json
{
    "runtimes": {
        "nvidia": {
            "path": "nvidia-container-runtime",
            "runtimeArgs": []
        }
    },
    "default-runtime": "nvidia"
}
```

### Bước 2.2: Thiết lập file môi trường `.env`
Sao chép file mẫu:
```bash
cp .env.example .env
```
Mở `.env` và cập nhật mật khẩu Database:
```ini
DB_USERNAME=postgres
DB_PASSWORD=postgres123
DATABASE_URL=postgresql://postgres:postgres123@127.0.0.1:5432/camera_ai
SESSION_SECRET=jetson-secure-key-random-123456
```

### Bước 2.3: Khởi chạy hệ thống bằng Docker Compose Jetson
```bash
# Build và chạy ngầm toàn bộ dịch vụ
docker compose -f docker-compose.jetson.yml up -d --build

# Xem tiến trình khởi động AI Core
docker logs -f camera_ai_core

# Xem logs Web UI
docker logs -f camera_ai_web
```

Hệ thống sẽ tự động:
1. Tạo database PostgreSQL và nạp schema + dữ liệu ban đầu từ `data.sql`.
2. Khởi động WebRTC proxy `go2rtc` (Port 1984).
3. Khởi động Web Admin FastAPI (Port 8000).
4. Khởi động `ai_core` với mô hình YOLO float `yolo26_plate_people_door.onnx` trên GPU thông qua `CUDAExecutionProvider` / `TensorrtExecutionProvider`.

---

## 💻 3. Phương án 2: Triển khai trực tiếp (Bare-Metal trên Jetson)

Nếu bạn muốn chạy trực tiếp không qua Docker để tối ưu tài nguyên tối đa:

### Bước 3.1: Cài đặt PostgreSQL & Restore Database
```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib

# Đổi mật khẩu postgres
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres123';"
sudo -u postgres createdb camera_ai

# Nạp dữ liệu ban đầu
sudo -u postgres psql -d camera_ai -f data.sql
```

### Bước 3.2: Cài đặt thư viện hệ thống
```bash
sudo apt install -y python3-dev python3-pip libopenblas-dev liblapack-dev \
    libglib2.0-0 libgomp1 libpq-dev libgl1 ffmpeg v4l-utils
```

### Bước 3.3: Cài đặt Python Dependencies cho Jetson
```bash
# Nâng cấp pip
python3 -m pip install --upgrade pip

# Cài đặt ONNX Runtime GPU tương thích JetPack của bạn
# (Tải wheel onnxruntime-gpu aarch64 phù hợp JetPack từ NVIDIA hoặc dusty-nv)
python3 -m pip install -r backend/requirements.txt
```

> **Lưu ý về PyTorch / ONNX Runtime trên Jetson:**
> - Trên Jetson, không dùng `pip install torch` bình thường từ PyPI.
> - Cài PyTorch cho Jetson từ link chính thức của NVIDIA:
>   - JetPack 5: `pip install --no-cache https://developer.download.nvidia.com/compute/redist/jp/v51/pytorch/torch-2.0.0+nv23.05-cp38-cp38-linux_aarch64.whl`
>   - JetPack 6: `pip install --no-cache https://developer.download.nvidia.com/compute/redist/jp/v60/pytorch/torch-2.3.0a0+ebed944e.nv24.6-cp310-cp310-linux_aarch64.whl`

### Bước 3.4: Khởi chạy
Sử dụng script có sẵn:
```bash
chmod +x install.sh
./install.sh
```
Hoặc chạy bằng 2 terminal:
```bash
# Terminal 1: Web UI
cd frontend
uvicorn app:app --host 0.0.0.0 --port 8000

# Terminal 2: AI Core
export PYTHONPATH=$PYTHONPATH:$(pwd)
export PLATE_MODEL_PATH=$(pwd)/backend/models/yolo26_plate_people_door.onnx
python3 backend/main.py
```

---

## ⚡ 4. Tối ưu tốc độ Inference với TensorRT

Trên Jetson, suy luận bằng **TensorRT (.engine)** đạt tốc độ cao nhất (gấp 3-5 lần so với ONNX CPU):

### Convert ONNX sang TensorRT Engine bằng `trtexec`:
```bash
/usr/src/tensorrt/bin/trtexec \
  --onnx=backend/models/yolo26_plate_people_door.onnx \
  --saveEngine=backend/models/yolo26_plate_people_door.engine \
  --fp16
```
Sau đó hệ thống sẽ tự động ưu tiên CUDA/TensorRT execution provider khi load model!

---

## 🌐 5. Truy cập & Kiểm tra

- **Web Quản lý Bãi xe:** `http://<IP-JETSON>:8000`
- **Tài khoản mặc định:** Xem trong bảng `users` từ `data.sql`
- **WebRTC Stream:** `http://<IP-JETSON>:1984`
- **Giám sát GPU:** Gõ lệnh `jtop` trên Jetson để quan sát phần trăm tải GPU, nhiệt độ và mức RAM tiêu thụ.
