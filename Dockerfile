FROM python:3.10-slim

WORKDIR /app

# Cài đặt thư viện hệ thống cần thiết cho OpenCV, dlib và glib
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgtk-3-dev \
    && rm -rf /var/lib/apt/lists/*

# Sao chép requirements.txt và cài đặt phụ thuộc
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Sao chép mã nguồn (Giả sử bạn muốn sao chép toàn bộ source vào /app)
COPY . .

# Lệnh mặc định, chạy main.py
CMD ["python", "-u", "main.py"]
