#!/bin/bash
set -e

PROJECT_DIR=$(pwd)
CURRENT_USER=$(whoami)

# 1. Tối ưu phần cứng & Swap 8GB
sudo nvpmodel -m 0
sudo jetson_clocks
if [ ! -f /swapfile ]; then
    sudo fallocate -l 8G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=8192
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    grep -q "/swapfile" /etc/fstab || echo "/swapfile swap swap defaults 0 0" | sudo tee -a /etc/fstab
fi

# 2. Cài đặt gói hệ thống & PostgreSQL
sudo apt update
sudo apt install -y postgresql postgresql-contrib libpq-dev \
    build-essential zlib1g-dev libssl-dev libffi-dev libsqlite3-dev libbz2-dev \
    libglib2.0-0 libgomp1 libgl1 ffmpeg v4l-utils cmake pkg-config wget

sudo systemctl start postgresql
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres123';"
sudo -u postgres createdb camera_ai 2>/dev/null || true
sudo -u postgres psql -d camera_ai -f "$PROJECT_DIR/data.sql"

# 3. Cài đặt Python 3.10 nếu chưa có
if ! command -v python3.10 &> /dev/null; then
    cd /tmp
    wget -qO- https://www.python.org/ftp/python/3.10.12/Python-3.10.12.tgz | tar -xz
    cd Python-3.10.12
    ./configure --enable-optimizations
    make -j"$(nproc)"
    sudo make altinstall
    cd "$PROJECT_DIR"
fi

# 4. Môi trường ảo & Dependencies
[ ! -d "venv" ] && python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r backend/requirements.txt

# 5. Cấu hình .env (chỉ tạo nếu chưa tồn tại)
if [ ! -f .env ]; then
    cat << 'EOF' > .env
DB_USERNAME=postgres
DB_PASSWORD=postgres123
DATABASE_URL=postgresql://postgres:postgres123@127.0.0.1:5432/camera_ai
SESSION_SECRET=jetson-nano-parking-secret-key-987654
EOF
fi

# 6. Build TensorRT FP16 (bỏ qua nếu đã build)
ENGINE="$PROJECT_DIR/backend/models/yolo26_plate_people_door.engine"
if [ -f "/usr/src/tensorrt/bin/trtexec" ] && [ ! -f "$ENGINE" ]; then
    /usr/src/tensorrt/bin/trtexec \
        --onnx="$PROJECT_DIR/backend/models/yolo26_plate_people_door.onnx" \
        --saveEngine="$ENGINE" \
        --fp16 2>/dev/null || true
fi

# 7. Cấu hình & Chạy Systemd Services
create_service() {
    local name="$1" desc="$2" workdir="$3" exec_cmd="$4" extra_env="$5"
    sudo tee "/etc/systemd/system/${name}.service" > /dev/null << EOF
[Unit]
Description=$desc
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$workdir
Environment="PATH=$PROJECT_DIR/venv/bin:/usr/local/cuda/bin:/usr/bin"
Environment="PYTHONPATH=$PROJECT_DIR"
$extra_env
ExecStart=$exec_cmd
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

create_service "camera-web" "Camera AI Web Management" "$PROJECT_DIR/frontend" "$PROJECT_DIR/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000"
create_service "camera-ai" "Camera AI Core Inference" "$PROJECT_DIR" "$PROJECT_DIR/venv/bin/python3 backend/main.py" "Environment=\"PLATE_MODEL_PATH=$PROJECT_DIR/backend/models/yolo26_plate_people_door.onnx\""

sudo systemctl daemon-reload
sudo systemctl enable --now camera-web camera-ai

echo "--------------------------------------------------------"
echo "✅ Hoàn tất! Web UI: http://$(hostname -I | awk '{print $1}'):8000"
echo "--------------------------------------------------------"
