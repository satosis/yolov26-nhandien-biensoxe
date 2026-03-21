#!/bin/bash

# Cấu hình các thông số
IMAGE="postgres:16-alpine"
CONTAINER_NAME="global-postgres"
NETWORK_NAME="shared-services"
PORT_HOST=5432
PORT_CONTAINER=5432

echo "--- Kiểm tra trạng thái Hệ thống Docker ---"

# 1. Tạo network nếu chưa có
if ! [ "$(docker network ls -q -f name=^${NETWORK_NAME}$)" ]; then
    echo "[NEW] Tạo network dùng chung: $NETWORK_NAME"
    docker network create $NETWORK_NAME
fi

# 2. Kiểm tra xem container có đang chạy không
if [ "$(docker ps -q -f name=^/${CONTAINER_NAME}$)" ]; then
    echo "[OK] Container '$CONTAINER_NAME' đang chạy. Tận dụng luôn."
    exit 0
fi

# 3. Nếu không chạy, kiểm tra xem nó có tồn tại (nhưng bị stop) không
if [ "$(docker ps -aq -f name=^/${CONTAINER_NAME}$)" ]; then
    echo "[INFO] Container '$CONTAINER_NAME' tồn tại nhưng đang dừng. Đang khởi động lại..."
    docker start $CONTAINER_NAME
    exit 0
fi

# 4. Nếu chưa tồn tại, tạo mới
echo "[NEW] Container '$CONTAINER_NAME' chưa có. Đang tạo mới và chạy..."

# Kiểm tra cổng host
if lsof -Pi :$PORT_HOST -sTCP:LISTEN -t >/dev/null ; then
    echo "[ERROR] Cổng $PORT_HOST đã bị chiếm bởi một ứng dụng khác ngoài Docker!"
    exit 1
fi

docker run -d \
  --name $CONTAINER_NAME \
  --network $NETWORK_NAME \
  -p ${PORT_HOST}:${PORT_CONTAINER} \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_USER=camera_user \
  -e POSTGRES_DB=camera_ai \
  --restart unless-stopped \
  $IMAGE

echo "[OK] Đã tạo và chạy container thành công trên network '$NETWORK_NAME'."
