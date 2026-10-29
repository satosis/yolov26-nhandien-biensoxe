import socket
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from frontend.deps import get_current_user, get_menu_context, require_menu, templates
from scripts.system_self_test import run_full_self_test

router = APIRouter(prefix="/system", tags=["System"], dependencies=[Depends(get_current_user)])


@router.get("/network-info")
async def get_network_info():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    return {"ip": ip, "subnet": ".".join(ip.split(".")[:3]), "status": "success"}

