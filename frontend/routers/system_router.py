import socket
from fastapi import APIRouter, Depends
from frontend.deps import get_current_user

router = APIRouter(prefix="/system", tags=["System"], dependencies=[Depends(get_current_user)])

@router.get("/network-info")
async def get_network_info():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    return {"ip": ip, "subnet": ".".join(ip.split(".")[:3]), "status": "success"}
