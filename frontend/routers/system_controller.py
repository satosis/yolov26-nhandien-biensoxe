import socket
from fastapi.responses import JSONResponse


async def get_network_info():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))  # UDP: không gửi gói nào, chỉ để hệ điều hành chọn card mạng ra Internet
            ip = sock.getsockname()[0]
    except OSError:  # không có mạng ra ngoài: dùng IP theo tên máy
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            return JSONResponse({"status": "error", "message": "Không xác định được IP mạng nội bộ."}, status_code=503)
    return {"ip": ip, "subnet": ".".join(ip.split(".")[:3]), "status": "success"}

