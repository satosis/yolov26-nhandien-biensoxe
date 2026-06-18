"""FastAPI application — Parking Camera webapp."""
import os
import sys
import ssl
import certifi
import asyncio
import logging
import decimal
import json

# Fix for SSL certificate verification on macOS
ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
import jinja2

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from frontend.deps import get_optional_user, get_menu_context

_discovery_logger = logging.getLogger("discovery")


def _run_background_discovery():
    """Synchronous wrapper — full ONVIF + Dahua + Cloud discovery (slow path)."""
    try:
        from backend.util.network_utils import discover_and_update_all_cameras
        result = discover_and_update_all_cameras()
        updated = result.get('updated', 0)
        registered = result.get('auto_registered', 0)
        if updated > 0 or registered > 0:
            _discovery_logger.info(
                f"Full discovery: updated={updated} IP(s), auto_registered={registered}"
            )
    except Exception as e:
        _discovery_logger.error(f"Full discovery error: {e}")


def _run_arp_discovery():
    """Synchronous wrapper — ARP table scan only (fast path, zero network cost)."""
    try:
        from backend.util.network_utils import discover_from_arp, auto_register_discovered_cameras
        from backend.core.database import DatabaseManager
        db = DatabaseManager()
        candidates = discover_from_arp()
        if candidates:
            result = auto_register_discovered_cameras(candidates, db)
            if result['updated'] > 0 or result['registered'] > 0:
                _discovery_logger.info(
                    f"ARP scan: updated={result['updated']}, registered={result['registered']}"
                )
    except Exception as e:
        _discovery_logger.error(f"ARP discovery error: {e}")


async def _periodic_discovery():
    """Two-speed camera discovery loop.
    Fast (every 60s): ARP table scan — zero network cost, instant.
    Slow (every 300s): Full ONVIF + Dahua UDP + Cloud API — multicast/TCP.
    """
    await asyncio.sleep(30)  # Wait for app to fully start
    fast_cycle = 0
    fast_interval, slow_interval = 60, 300

    # Read intervals from DB once at startup
    try:
        from frontend.deps import get_db
        db = get_db()
        fast_interval = int(db.get_app_setting("ARP_SCAN_INTERVAL_SEC", "60") or "60")
        slow_interval = int(db.get_app_setting("DISCOVERY_INTERVAL_SEC", "300") or "300")
    except Exception:
        pass

    while True:
        slow_every_n = max(1, slow_interval // fast_interval)

        try:
            if fast_cycle % slow_every_n == 0:
                _discovery_logger.info("Running full discovery (slow path)...")
                await asyncio.to_thread(_run_background_discovery)
                # Refresh intervals on slow cycle (settings may have changed)
                try:
                    from frontend.deps import get_db
                    db = get_db()
                    fast_interval = int(db.get_app_setting("ARP_SCAN_INTERVAL_SEC", "60") or "60")
                    slow_interval = int(db.get_app_setting("DISCOVERY_INTERVAL_SEC", "300") or "300")
                except Exception:
                    pass
            else:
                await asyncio.to_thread(_run_arp_discovery)
        except Exception as e:
            _discovery_logger.error(f"Discovery cycle error: {e}")

        fast_cycle += 1
        await asyncio.sleep(fast_interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import sys
    port = "8000"
    if "--port" in sys.argv:
        port = sys.argv[sys.argv.index("--port") + 1]
    
    print("====================================================")
    print("   🅿  PARKING AI - FRONTEND WEB UI                 ")
    print(f"   Starting server on: http://localhost:{port}".ljust(52))
    print("====================================================")
    yield


app = FastAPI(title="Parking Camera", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key="parking-camera-session",
    session_cookie="parking_session",
    max_age=60 * 60 * 24 * 7,
    https_only=False,
)

# Static files — with long-lived cache headers for assets
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

class StaticCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/"):
            # Images/fonts/vendor JS: cache 7 days
            if any(path.endswith(ext) for ext in (".jpg",".jpeg",".png",".webp",".svg",".ico",".woff2",".woff",".ttf")):
                response.headers["Cache-Control"] = "public, max-age=604800, immutable"
            # Vendor JS/CSS: cache 30 days
            elif path.startswith("/static/vendor/"):
                response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        return response

app.add_middleware(StaticCacheMiddleware)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

from backend.core import config as cfg
os.makedirs(cfg.KNOWN_FACES_DIR, exist_ok=True)
os.makedirs(cfg.SNAPSHOT_DIR, exist_ok=True)
app.mount("/known_faces", StaticFiles(directory=cfg.KNOWN_FACES_DIR), name="known_faces")
app.mount("/snapshots", StaticFiles(directory=cfg.SNAPSHOT_DIR), name="snapshots")
app.mount("/data/recordings", StaticFiles(directory=cfg.RECORDING_DIR), name="recordings")

# Templates
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.add_extension('jinja2.ext.do')

# Custom tojson filter to handle Decimal objects
def custom_tojson(obj):
    def default(o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")
    return json.dumps(obj, default=default)

templates.env.filters['tojson'] = custom_tojson

# --- Import and register routers ---
# --- Import and register routers ---
from frontend.routers.auth_router import router as auth_router
from frontend.routers.detection_router import router as detection_router
from frontend.routers.cameras_router import router as cameras_router
from frontend.routers.sites_router import router as sites_router
from frontend.routers.providers_router import router as providers_router
from frontend.routers.door_providers_router import router as door_providers_router
from frontend.routers.rolling_doors_router import router as rolling_doors_router
from frontend.routers.vehicles_router import router as vehicles_router
from frontend.routers.cargo_trips_router import router as cargo_trips_router
from frontend.routers.strangers_router import router as strangers_router
from frontend.routers.pending_plates_router import router as pending_plates_router
from frontend.routers.parking_router import router as parking_router
from frontend.routers.telegram_router import router as telegram_router
from frontend.routers.rbac_router import router as rbac_router
from frontend.routers.users_router import router as users_router
from frontend.routers.system_router import router as system_router
from frontend.routers.recordings_router import router as recordings_router
from frontend.routers.landing_router import router as landing_router
from frontend.routers.inquiries_router import router as inquiries_router
from frontend.routers.cms_router import router as cms_router
from frontend.notifications import manager
from fastapi import WebSocket, WebSocketDisconnect

# Routers to be prefixed with /camera
CAMERA_PAGE_KEYS = {
    "detection", "cameras", "vehicles", "strangers", 
    "pending_plates", "parking_dashboard", "parking_logs", 
    "parking_analytics", "recordings"
}

# Grouped registration
for r in [detection_router, cameras_router, vehicles_router, cargo_trips_router, strangers_router,
          pending_plates_router, parking_router, recordings_router]:
    app.include_router(r, prefix="/camera")

for r in [auth_router, sites_router, providers_router, door_providers_router,
          rolling_doors_router, telegram_router, rbac_router,
          users_router, system_router, inquiries_router, cms_router]:
    app.include_router(r)

app.include_router(landing_router)

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

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code in (301, 302, 303, 307, 308):
        location = exc.headers.get("Location", "/login") if exc.headers else "/login"
        return RedirectResponse(url=location, status_code=exc.status_code)

    user = get_optional_user(request)
    ctx = {
        "user": user,
        "menu_groups": {},
        "sites": [],
        "active_site_id": 0,
        "current_menu": ""
    }
    if user:
        ctx = get_menu_context(request, user)

    if exc.status_code == 404:
        return templates.TemplateResponse("pages/404.html", {"request": request, **ctx}, status_code=404)
    return templates.TemplateResponse("pages/500.html", {"request": request, "exc": exc, **ctx}, status_code=500)

import traceback as _traceback
import logging as _logging
_app_logger = _logging.getLogger("frontend.app")

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    _app_logger.error("Unhandled exception on %s: %s\n%s", request.url.path, exc, _traceback.format_exc())
    user = get_optional_user(request)
    ctx = {
        "user": user,
        "menu_groups": {},
        "sites": [],
        "active_site_id": 0,
        "current_menu": ""
    }
    if user:
        try:
            ctx = get_menu_context(request, user)
        except Exception:
            pass
    return templates.TemplateResponse("pages/500.html", {"request": request, "exc": exc, **ctx}, status_code=500)

@app.exception_handler(jinja2.exceptions.TemplateNotFound)
async def template_not_found_handler(request: Request, exc: jinja2.exceptions.TemplateNotFound):
    user = get_optional_user(request)
    ctx = get_menu_context(request, user) if user else {
        "user": None, "menu_groups": {}, "sites": [], "active_site_id": 0, "current_menu": ""
    }
    return templates.TemplateResponse("pages/404.html", {"request": request, **ctx}, status_code=404)
