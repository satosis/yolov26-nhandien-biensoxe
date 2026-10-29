import os
import ssl
import sys
from pathlib import Path

import certifi
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, BackgroundTasks
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
from frontend.context.company_context import company_context
from frontend.modules.auth import router as auth_router
from frontend.modules.invoices import router as input_invoices_router, companies_router
from frontend.modules.enterprise import enterprise_router, supplier_router
from frontend.modules.enterprise.tax_checklist_router import router as tax_checklist_router
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
from frontend.modules.transport.tax_report_router import router as tax_report_router
from frontend.modules.transport.depreciation_router import router as depreciation_router
from frontend.modules.transport.trial_balance_router import router as trial_balance_router
from frontend.modules.transport.cash_bank_router import router as cash_bank_router
from frontend.modules.transport.prepaid_expenses_router import router as prepaid_expenses_router
from frontend.modules.zalo import router as zalo_inbox_router
from frontend.modules.advertising import advertising_router

from frontend.routers.cameras_router import router as cameras_router
from frontend.routers.administrative_divisions_router import router as administrative_divisions_router
from frontend.routers.api_keys_router import router as api_keys_router, alias_router as api_keys_alias_router
from frontend.routers.cms_router import router as cms_router
from frontend.routers.detection import router as detection_router
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
from frontend.modules.reminders.router import router as reminders_router
from frontend.services.crane.router import router as crane_service_router

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

class StaticCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/vendor/"):
            response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        elif path.startswith("/static/") and path.endswith(STATIC_CACHE_EXTENSIONS):
            response.headers["Cache-Control"] = "public, max-age=604800, immutable"
        return response


_SKIP_COMPANY_PREFIXES = (
    "/static",
    "/snapshots",
    "/upload",
    "/known_faces",
    "/data/recordings",
    "/sw.js",
    "/favicon.ico",
)


def _extract_company_id(request: Request) -> int | None:
    raw = request.query_params.get("company_id")
    if not raw and "session" in request.scope:
        sess = request.session
        u = sess.get("user")
        raw = sess.get("active_company_id") or (u.get("company_id") if isinstance(u, dict) else None)
    try:
        val = int(raw)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


class CompanyContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(_SKIP_COMPANY_PREFIXES):
            return await call_next(request)

        with company_context(_extract_company_id(request)):
            return await call_next(request)


app.add_middleware(CompanyContextMiddleware)
app.add_middleware(StaticCacheMiddleware)

_SESSION_SECRET = os.environ.get("SESSION_SECRET", "son-lam-industrial-transport-secret-key-2026")
app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    session_cookie="parking_session",
    max_age=60 * 60 * 24 * 7,
    https_only=False,
)

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
    tax_report_router,
    depreciation_router,
    trial_balance_router,
    cash_bank_router,
    prepaid_expenses_router,
    service_accounts_router,
    service_accounts_alias_router,
    api_keys_router,
    api_keys_alias_router,
    cargo_trips_router,
    advertising_router,
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
    tax_checklist_router,
    supplier_router,
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
app.include_router(crane_service_router)
app.include_router(kyc_router)
app.include_router(reminders_router)
app.include_router(reminders_router, prefix="/admin")


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
async def notify_inquiry(data: InquiryNotification, background_tasks: BackgroundTasks, request: Request):
    await manager.broadcast({"type": "NEW_INQUIRY", "data": data.model_dump()})
    try:
        from frontend.routers.landing_router import send_inquiry_telegram_alert, InquiryInput
        inq_item = InquiryInput(
            full_name=data.full_name,
            phone=data.phone,
            company_name="",
            demand=data.demand,
            source_page=data.source_page,
        )
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
        base_url = f"{proto}://{host}"
        background_tasks.add_task(send_inquiry_telegram_alert, inq_item, data.id, base_url)
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not dispatch Telegram alert: %s", exc)
    return {"status": "ok"}