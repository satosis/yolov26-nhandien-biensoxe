"""FastAPI application — Parking Camera webapp."""
import os
import sys
import ssl
import certifi
import asyncio
import logging

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
from starlette.responses import RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
import jinja2

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from frontend.auth import try_remember_token
from frontend.deps import get_current_user, get_optional_user, get_menu_context

_discovery_logger = logging.getLogger("discovery")


def _run_background_discovery():
    """Synchronous wrapper for background discovery — only updates IPs, never auto-inserts."""
    try:
        from backend.util.network_utils import discover_and_update_all_cameras
        result = discover_and_update_all_cameras()
        updated = result.get('updated', 0)
        if updated > 0:
            _discovery_logger.info(f"Background discovery: updated {updated} camera IP(s)")
    except Exception as e:
        _discovery_logger.error(f"Background discovery error: {e}")


async def _periodic_discovery():
    """Run lightweight camera discovery periodically in background."""
    await asyncio.sleep(30)  # Wait for app to fully start
    while True:
        try:
            from backend.core.database import DatabaseManager
            db = DatabaseManager()
            interval = int(db.get_app_setting("DISCOVERY_INTERVAL_SEC", "300") or "300")
        except Exception:
            interval = 300

        try:
            await asyncio.to_thread(_run_background_discovery)
        except Exception as e:
            _discovery_logger.error(f"Periodic discovery error: {e}")

        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Professional Startup Logo
    print("====================================================")
    print("   🅿  PARKING AI - FRONTEND WEB UI                 ")
    print("   Starting server on: http://localhost:8000       ")
    print("====================================================")
    # Start background periodic camera discovery
    discovery_task = asyncio.create_task(_periodic_discovery())
    yield
    discovery_task.cancel()
    try:
        await discovery_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Parking Camera", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-secret-change-me"),
    session_cookie="parking_session",
    max_age=60 * 60 * 24 * 7,
    https_only=False,
)

# Static files
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

from backend.core import config as cfg
os.makedirs(cfg.KNOWN_FACES_DIR, exist_ok=True)
os.makedirs(cfg.SNAPSHOT_DIR, exist_ok=True)
app.mount("/known_faces", StaticFiles(directory=cfg.KNOWN_FACES_DIR), name="known_faces")
app.mount("/snapshots", StaticFiles(directory=cfg.SNAPSHOT_DIR), name="snapshots")

# Templates
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.add_extension('jinja2.ext.do')

# --- Import and register routers ---
from frontend.routers.auth_router import router as auth_router
from frontend.routers.detection_router import router as detection_router
from frontend.routers.cameras_router import router as cameras_router
from frontend.routers.sites_router import router as sites_router
from frontend.routers.providers_router import router as providers_router
from frontend.routers.door_providers_router import router as door_providers_router
from frontend.routers.rolling_doors_router import router as rolling_doors_router
from frontend.routers.vehicles_router import router as vehicles_router
from frontend.routers.strangers_router import router as strangers_router
from frontend.routers.pending_plates_router import router as pending_plates_router
from frontend.routers.parking_router import router as parking_router
from frontend.routers.ocr_review_router import router as ocr_review_router
from frontend.routers.telegram_router import router as telegram_router
from frontend.routers.rbac_router import router as rbac_router
from frontend.routers.users_router import router as users_router
from frontend.routers.config_router import router as config_router
from frontend.routers.hpc_router import router as hpc_router
from frontend.routers.dori_router import router as dori_router
from frontend.routers.system_router import router as system_router

for r in [auth_router, detection_router, cameras_router, sites_router,
          providers_router, door_providers_router, rolling_doors_router,
          vehicles_router, strangers_router, pending_plates_router,
          parking_router, ocr_review_router, telegram_router, rbac_router,
          users_router, config_router, hpc_router, dori_router, system_router]:
    app.include_router(r)

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    user = get_optional_user(request)
    # Default context if no user is logged in
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

@app.exception_handler(jinja2.exceptions.TemplateNotFound)
async def template_not_found_handler(request: Request, exc: jinja2.exceptions.TemplateNotFound):
    user = get_optional_user(request)
    ctx = get_menu_context(request, user) if user else {
        "user": None, "menu_groups": {}, "sites": [], "active_site_id": 0, "current_menu": ""
    }
    return templates.TemplateResponse("pages/404.html", {"request": request, **ctx}, status_code=404)


@app.get("/.well-known/appspecific/com.chrome.devtools.json")
async def devtools_json():
    return {}


@app.get("/")
async def root(request: Request):
    """Redirect to first allowed menu page."""
    user = request.session.get("user")
    if not user:
        # Try remember token
        user = try_remember_token(request)
    if not user:
        return RedirectResponse("/login")
    ctx = get_menu_context(request, user)
    first_key = ""
    for items in ctx["menu_groups"].values():
        first_key = next(iter(items))
        break
    return RedirectResponse(f"/{first_key}" if first_key else "/login")
