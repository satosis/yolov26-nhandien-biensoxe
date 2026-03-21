"""FastAPI application — Parking Camera webapp."""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from frontend.auth import try_remember_token
from frontend.deps import get_current_user, get_optional_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm AI models at startup (non-blocking for dev)
    # from frontend.models_loader import preload_models
    # preload_models()
    yield


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

# Templates
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# --- Import and register routers ---
from frontend.routers.auth_router import router as auth_router
from frontend.routers.detection_router import router as detection_router
from frontend.routers.cameras_router import router as cameras_router
from frontend.routers.sites_router import router as sites_router
from frontend.routers.providers_router import router as providers_router
from frontend.routers.door_providers_router import router as door_providers_router
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

for r in [auth_router, detection_router, cameras_router, sites_router,
          providers_router, door_providers_router, vehicles_router,
          strangers_router, pending_plates_router, parking_router,
          ocr_review_router, telegram_router, rbac_router, users_router,
          config_router, hpc_router, dori_router]:
    app.include_router(r)


@app.get("/")
async def root(request: Request):
    """Redirect to first allowed menu page."""
    user = request.session.get("user")
    if not user:
        # Try remember token
        user = try_remember_token(request)
    if not user:
        return RedirectResponse("/login")
    from frontend.deps import get_menu_context
    ctx = get_menu_context(request, user)
    first_key = ""
    for items in ctx["menu_groups"].values():
        first_key = next(iter(items))
        break
    return RedirectResponse(f"/{first_key}" if first_key else "/login")
