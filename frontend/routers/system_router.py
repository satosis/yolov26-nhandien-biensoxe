from fastapi import APIRouter, Depends
from frontend.deps import get_current_user
import socket
import logging

router = APIRouter(prefix="/system", tags=["System"])
logger = logging.getLogger("system_router")

@router.get("/network-info")
async def get_network_info(user: dict = Depends(get_current_user)):
    """Returns the server's current IP and guessed subnet prefix."""
    try:
        # Get primary IP by connecting to a public DNS (doesn't actually send data)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        
        # Guess subnet (e.g. 192.168.1.15 -> 192.168.1)
        parts = ip.split('.')
        subnet = ".".join(parts[:3]) if len(parts) == 4 else "192.168.1"
        
        return {
            "ip": ip,
            "subnet": subnet,
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Error getting network info: {e}")
        return {"status": "error", "message": str(e), "subnet": "192.168.1"}
