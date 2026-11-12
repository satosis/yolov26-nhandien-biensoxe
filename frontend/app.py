import sys
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from backend.services import net_guard
net_guard.install_from_env()

import os
import ssl

import certifi
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse, Response

ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from backend.services import config as cfg
cfg.setup_logging("frontend.log")

from frontend.error_handlers import register_error_handlers
from frontend.context.company_context import company_context

from frontend.routes import register_routes
from frontend.modules.zalo.controller import resume_invoice_sessions
from frontend.routers.detection.video_api import close_video_jobs
from frontend.catalog import migrate_product_catalog

STATIC_CACHE_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".webp", ".svg", ".ico",
    ".woff2", ".woff", ".ttf",
)

app = FastAPI(title="Parking Camera")
register_error_handlers(app)

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


class AdminAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/admin"):
            sess = getattr(request, "session", None)
            user = sess.get("user") if sess else None
            if not user:
                if "/api/" in path or request.headers.get("accept", "").startswith("application/json") or request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return Response(content='{"detail":"Chưa đăng nhập"}', status_code=401, media_type="application/json")
                return RedirectResponse("/login", status_code=303)
            if user.get("role") != "Admin":
                if "/api/" in path or request.headers.get("accept", "").startswith("application/json") or request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return Response(content='{"detail":"Tài khoản không có quyền quản trị"}', status_code=403, media_type="application/json")
                return RedirectResponse("/tai-khoan", status_code=303)
        return await call_next(request)


app.add_middleware(AdminAccessMiddleware)
app.add_middleware(CompanyContextMiddleware)
app.add_middleware(StaticCacheMiddleware)

_SESSION_SECRET = os.environ["SESSION_SECRET"]
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
# Invoice, Facebook post and goods spec images live under data/, the links stay /upload/bkav_invoices/..., /upload/fb_posts/... and /upload/goods_specs/...; these mounts must come before /upload
app.mount("/upload/bkav_invoices", StaticFiles(directory=cfg.BKAV_INVOICE_DIR), name="upload_bkav_invoices")
app.mount("/upload/fb_posts", StaticFiles(directory=cfg.FB_POST_DIR), name="upload_fb_posts")
app.mount("/upload/goods_specs", StaticFiles(directory=cfg.GOODS_SPEC_DIR), name="upload_goods_specs")
app.mount("/upload", StaticFiles(directory=UPLOAD_DIR), name="upload")

register_routes(app)
app.add_event_handler("startup", resume_invoice_sessions)
app.add_event_handler("startup", migrate_product_catalog)
app.add_event_handler("shutdown", close_video_jobs)


