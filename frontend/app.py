import os
import ssl
import sys
from pathlib import Path

import certifi
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse, Response

ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from backend.services import config as cfg
cfg.setup_logging("frontend.log")

from frontend.notifications import InquiryNotification, manager
from frontend.modules.auth import router as auth_router
from frontend.modules.invoices import router as input_invoices_router, companies_router
from frontend.modules.enterprise import enterprise_router
from frontend.modules.payroll import router as staff_payroll_router
from frontend.modules.staff import staff_router
from frontend.modules.service_accounts import router as service_accounts_router, alias_router as service_accounts_alias_router
from frontend.modules.transport import (
    trips_router as cargo_trips_router,
    catalog_router,
    transport_catalog_router,
    contracts_router,
    report_router as vehicle_monthly_report_router,
)
from frontend.modules.zalo import router as zalo_inbox_router

from frontend.routers.cameras_router import router as cameras_router
from frontend.routers.administrative_divisions_router import router as administrative_divisions_router
from frontend.routers.api_keys_router import router as api_keys_router, alias_router as api_keys_alias_router
from frontend.routers.cms_router import router as cms_router
from frontend.routers.detection_router import router as detection_router
from frontend.routers.door_providers_router import router as door_providers_router
from frontend.routers.inquiries_router import router as inquiries_router
from frontend.routers.kyc_router import router as kyc_router
from frontend.routers.landing_router import router as landing_router
from frontend.routers.parking_router import router as parking_router
from frontend.routers.pending_plates_router import router as pending_plates_router
from frontend.routers.providers_router import router as providers_router
from frontend.routers.rbac_router import router as rbac_router
from frontend.routers.recordings_router import router as recordings_router
from frontend.routers.rolling_doors_router import router as rolling_doors_router
from frontend.routers.sites_router import router as sites_router
from frontend.routers.strangers_router import router as strangers_router
from frontend.routers.system_router import router as system_router
from frontend.routers.telegram_router import router as telegram_router
from frontend.routers.users_router import router as users_router
from frontend.routers.vehicles_router import router as vehicles_router

STATIC_CACHE_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".webp", ".svg", ".ico",
    ".woff2", ".woff", ".ttf",
)

_KILL_SW_JS = """
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map(k => caches.delete(k)));
    await self.registration.unregister();
    const cs = await self.clients.matchAll({ type: 'window' });
    await Promise.all(cs.map(c => c.navigate(c.url)));
  })());
});
""".strip()


app = FastAPI(title="Parking Camera")

_SESSION_SECRET = os.environ.get("SESSION_SECRET")
app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    session_cookie="parking_session",
    max_age=60 * 60 * 24 * 7,
    https_only=False,
)


class StaticCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/"):
            if path.endswith(STATIC_CACHE_EXTENSIONS):
                response.headers["Cache-Control"] = "public, max-age=604800, immutable"
            elif path.startswith("/static/vendor/"):
                response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        return response


app.add_middleware(StaticCacheMiddleware)

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

for d in (cfg.KNOWN_FACES_DIR, cfg.DATA_DIR, cfg.SNAPSHOT_DIR, cfg.RECORDING_DIR):
    os.makedirs(d, exist_ok=True)

app.mount("/known_faces", StaticFiles(directory=cfg.KNOWN_FACES_DIR), name="known_faces")
app.mount("/snapshots", StaticFiles(directory=cfg.SNAPSHOT_DIR), name="snapshots")
app.mount("/data/recordings", StaticFiles(directory=cfg.RECORDING_DIR), name="recordings")

UPLOAD_DIR = Path(cfg.BASE_DIR) / "upload"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/upload", StaticFiles(directory=UPLOAD_DIR), name="upload")

ADMIN_ROUTERS = [
    administrative_divisions_router,
    detection_router,
    cameras_router,
    vehicles_router,
    vehicle_monthly_report_router,
    staff_payroll_router,
    input_invoices_router,
    service_accounts_router,
    service_accounts_alias_router,
    api_keys_router,
    api_keys_alias_router,
    cargo_trips_router,
    contracts_router,
    catalog_router,
    transport_catalog_router,
    strangers_router,
    pending_plates_router,
    parking_router,
    recordings_router,
    sites_router,
    companies_router,
    enterprise_router,
    staff_router,
    providers_router,
    door_providers_router,
    rolling_doors_router,
    telegram_router,
    zalo_inbox_router,
    rbac_router,
    users_router,
    system_router,
    inquiries_router,
    cms_router,
]

for router in ADMIN_ROUTERS:
    app.include_router(router, prefix="/admin")

app.include_router(auth_router)
app.include_router(landing_router)
app.include_router(kyc_router)


@app.get("/admin")
async def admin_root():
    return RedirectResponse("/admin/detection", status_code=303)


@app.get("/sw.js")
async def kill_service_worker():
    return Response(
        content=_KILL_SW_JS,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.websocket("/ws/notifications")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
    finally:
        manager.disconnect(websocket)


@app.post("/api/internal/notify-inquiry")
async def notify_inquiry(data: InquiryNotification):
    await manager.broadcast({"type": "NEW_INQUIRY", "data": data.model_dump()})
    return {"status": "ok"}
