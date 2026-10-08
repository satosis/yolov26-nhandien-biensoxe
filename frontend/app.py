"""FastAPI application — Parking Camera webapp."""
import decimal
import json
import logging
import os
import ssl
import sys
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

import certifi
import jinja2
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import RedirectResponse, Response

# Fix SSL certificate verification on macOS
ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load environment variables
load_dotenv()

from backend.services import config as cfg
from frontend.deps import get_menu_context, get_optional_user
from frontend.notifications import manager
from frontend.routers.auth_router import router as auth_router
from frontend.routers.cameras_router import router as cameras_router
from frontend.routers.cargo_trips_router import router as cargo_trips_router
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

# Configure logging (console + data/logs/frontend.log)
cfg.setup_logging("frontend.log")
logger = logging.getLogger("frontend.app")

STATIC_CACHE_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".webp", ".svg", ".ico",
    ".woff2", ".woff", ".ttf",
)

_KILL_SW_JS = """
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    try { const keys = await caches.keys(); await Promise.all(keys.map(k => caches.delete(k))); } catch (e) {}
    try { await self.registration.unregister(); } catch (e) {}
    const cs = await self.clients.matchAll({ type: 'window' });
    cs.forEach((c) => { try { c.navigate(c.url); } catch (e) {} });
  })());
});
""".strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    port = "8000"
    if "--port" in sys.argv:
        try:
            port = sys.argv[sys.argv.index("--port") + 1]
        except IndexError:
            pass

    print("====================================================")
    print("   [P]  PARKING AI - FRONTEND WEB UI                ")
    print(f"   Starting server on: http://localhost:{port}".ljust(52))
    print("====================================================")
    yield


app = FastAPI(title="Parking Camera", lifespan=lifespan)

# Session middleware
_SESSION_SECRET = os.environ.get("SESSION_SECRET")
app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    session_cookie="parking_session",
    max_age=60 * 60 * 24 * 7,
    https_only=False,
)


class StaticCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/"):
            if path.endswith(STATIC_CACHE_EXTENSIONS):
                response.headers["Cache-Control"] = "public, max-age=604800, immutable"
            elif path.startswith("/static/vendor/"):
                response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        return response


app.add_middleware(StaticCacheMiddleware)

# Static & media mount points
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

for d in (cfg.KNOWN_FACES_DIR, cfg.DATA_DIR, cfg.SNAPSHOT_DIR, cfg.RECORDING_DIR):
    os.makedirs(d, exist_ok=True)

app.mount("/known_faces", StaticFiles(directory=cfg.KNOWN_FACES_DIR), name="known_faces")
app.mount("/snapshots", StaticFiles(directory=cfg.SNAPSHOT_DIR), name="snapshots")
app.mount("/data/recordings", StaticFiles(directory=cfg.RECORDING_DIR), name="recordings")

# Template engine configuration
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.add_extension("jinja2.ext.do")


def custom_tojson(obj):
    def default(o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")

    return json.dumps(obj, default=default)


templates.env.filters["tojson"] = custom_tojson

# Admin routers under /admin prefix
ADMIN_ROUTERS = [
    detection_router,
    cameras_router,
    vehicles_router,
    cargo_trips_router,
    strangers_router,
    pending_plates_router,
    parking_router,
    recordings_router,
    sites_router,
    providers_router,
    door_providers_router,
    rolling_doors_router,
    telegram_router,
    rbac_router,
    users_router,
    system_router,
    inquiries_router,
    cms_router,
]

for router in ADMIN_ROUTERS:
    app.include_router(router, prefix="/admin")

# Root routers
app.include_router(auth_router)
app.include_router(landing_router)
app.include_router(kyc_router)


# Utility & system endpoints
@app.get("/admin")
async def admin_root():
    """Bare /admin → default admin landing page."""
    return RedirectResponse("/admin/detection", status_code=303)


@app.get("/sw.js")
async def kill_service_worker():
    """Kill-switch service worker to unregister stale SWs from browsers."""
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
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/api/internal/notify-inquiry")
async def notify_inquiry(data: dict):
    """Internal endpoint to trigger WebSocket broadcast."""
    await manager.broadcast({"type": "NEW_INQUIRY", "data": data})
    return {"status": "ok"}


def _build_error_context(request: Request) -> dict:
    user = get_optional_user(request)
    if user:
        try:
            return get_menu_context(request, user)
        except Exception:
            pass
    return {
        "user": user,
        "menu_groups": {},
        "sites": [],
        "active_site_id": 0,
        "current_menu": "",
    }


# Exception handlers
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code in (301, 302, 303, 307, 308):
        location = exc.headers.get("Location", "/login") if exc.headers else "/login"
        return RedirectResponse(url=location, status_code=exc.status_code)

    ctx = _build_error_context(request)
    if exc.status_code == 404:
        return templates.TemplateResponse("pages/404.html", {"request": request, **ctx}, status_code=404)
    return templates.TemplateResponse("pages/500.html", {"request": request, "exc": exc, **ctx}, status_code=500)


@app.exception_handler(jinja2.exceptions.TemplateNotFound)
async def template_not_found_handler(request: Request, exc: jinja2.exceptions.TemplateNotFound):
    ctx = _build_error_context(request)
    return templates.TemplateResponse("pages/404.html", {"request": request, **ctx}, status_code=404)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception on %s: %s\n%s", request.url.path, exc, traceback.format_exc())
    ctx = _build_error_context(request)
    return templates.TemplateResponse("pages/500.html", {"request": request, "exc": exc, **ctx}, status_code=500)
