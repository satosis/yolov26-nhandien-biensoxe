"""CMS Router — Management of projects, news and landing assets."""
import logging
from fastapi import APIRouter, Request, Form, Depends, File, UploadFile
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pathlib import Path

from frontend.deps import get_db, get_current_user, get_menu_context

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cms", tags=["cms"])
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def get_projects(db, limit: int = 10) -> list:
    """Fetch projects for landing page, ordered by newest first."""
    query = """
        SELECT id, name, location, area, height, bays, load_capacity, image, description
        FROM projects
        ORDER BY created_at DESC
        LIMIT %s
    """
    return db._execute_query(query, (limit,), fetchall=True, commit=False) or []


def get_news(db, limit: int = 5) -> list:
    """Fetch latest news items, with publish_date serialized to ISO string."""
    query = """
        SELECT id, slug, title, category, summary, image, publish_date
        FROM news
        ORDER BY publish_date DESC
        LIMIT %s
    """
    rows = db._execute_query(query, (limit,), fetchall=True, commit=False) or []
    for row in rows:
        if row.get("publish_date") and not isinstance(row["publish_date"], str):
            row["publish_date"] = row["publish_date"].isoformat()
    return rows


def get_news_by_slug(db, slug: str) -> dict | None:
    """Fetch a single published news item by slug."""
    rows = db._execute_query(
        "SELECT id, slug, title, category, summary, content, image, publish_date FROM news WHERE slug = %s AND is_published IS DISTINCT FROM false",
        (slug,), fetchall=True, commit=False
    ) or []
    if not rows:
        return None
    item = dict(rows[0])
    if item.get("publish_date") and hasattr(item["publish_date"], "strftime"):
        item["publish_date_str"] = item["publish_date"].strftime("%d/%m/%Y")
    else:
        item["publish_date_str"] = str(item.get("publish_date", ""))
    return item


def get_news_by_id(db, news_id: int) -> dict | None:
    """Fetch a single published news item by ID (legacy, redirects to slug)."""
    rows = db._execute_query(
        "SELECT id, slug, title, category, summary, content, image, publish_date FROM news WHERE id = %s AND is_published IS DISTINCT FROM false",
        (news_id,), fetchall=True, commit=False
    ) or []
    if not rows:
        return None
    item = dict(rows[0])
    if item.get("publish_date") and hasattr(item["publish_date"], "strftime"):
        item["publish_date_str"] = item["publish_date"].strftime("%d/%m/%Y")
    else:
        item["publish_date_str"] = str(item.get("publish_date", ""))
    return item


def get_landing_settings(db) -> dict:
    """Batch-get all LANDING_* keys from app_settings."""
    try:
        rows = db._execute_query(
            "SELECT key, value FROM app_settings WHERE key LIKE 'LANDING_%'",
            fetchall=True,
            commit=False,
        )
        if not rows:
            return {}
        return {row["key"]: row["value"] for row in rows}
    except Exception as e:
        logger.warning("get_landing_settings: query failed: %s", e)
        return {}


@router.get("/", response_class=HTMLResponse)
async def cms_dashboard(request: Request, user=Depends(get_current_user)):
    """CMS Dashboard — list projects and news."""
    db = get_db()
    projects = get_projects(db)
    news = get_news(db, limit=100)

    ctx = get_menu_context(request, user)
    return templates.TemplateResponse(
        "pages/cms.html",
        {
            "request": request,
            "user": user,
            "projects": projects,
            "news": news,
            "current_menu": "cms",
            **ctx
        }
    )

@router.post("/projects", response_class=HTMLResponse)
async def add_project(
    request: Request,
    name: str = Form(...),
    location: str = Form(...),
    area: str = Form(...),
    height: str = Form(...),
    bays: str = Form(...),
    load_capacity: str = Form(...),
    image: str = Form("/static/brain/wh_real1.jpg"),
    user=Depends(get_current_user)
):
    """Add a new project."""
    db = get_db()
    try:
        db._execute_query(
            "INSERT INTO projects (name, location, area, height, bays, load_capacity, image) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (name, location, area, height, bays, load_capacity, image),
            commit=True
        )
    except Exception as e:
        logger.error(f"Error adding project: {e}")

    return RedirectResponse(url="/admin/cms", status_code=303)

@router.post("/news", response_class=HTMLResponse)
async def add_news(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    summary: str = Form(...),
    image: str = Form("/static/brain/news1.jpg"),
    user=Depends(get_current_user)
):
    """Add a new news item."""
    db = get_db()
    try:
        db._execute_query(
            "INSERT INTO news (title, category, summary, image) VALUES (%s, %s, %s, %s)",
            (title, category, summary, image),
            commit=True
        )
    except Exception as e:
        logger.error(f"Error adding news: {e}")

    return RedirectResponse(url="/admin/cms", status_code=303)

@router.get("/news/new", response_class=HTMLResponse)
async def news_new_page(request: Request, user=Depends(get_current_user)):
    """Trang tạo tin tức mới."""
    ctx = get_menu_context(request, user)
    return templates.TemplateResponse("pages/cms_news_edit.html", {
        "request": request, "user": user, "item": None, "current_menu": "cms", **ctx
    })

@router.get("/news/{news_id}/edit", response_class=HTMLResponse)
async def news_edit_page(news_id: int, request: Request, user=Depends(get_current_user)):
    """Trang sửa tin tức theo ID."""
    db = get_db()
    rows = db._execute_query("SELECT * FROM news WHERE id=%s", (news_id,), fetchall=True) or []
    if not rows:
        return RedirectResponse("/admin/cms")
    ctx = get_menu_context(request, user)
    return templates.TemplateResponse("pages/cms_news_edit.html", {
        "request": request, "user": user, "item": dict(rows[0]), "current_menu": "cms", **ctx
    })

@router.post("/news/save", response_class=HTMLResponse)
async def news_save(
    request: Request,
    news_id: str = Form(""),
    title: str = Form(...),
    category: str = Form(...),
    summary: str = Form(""),
    content: str = Form(""),
    image: str = Form("/static/brain/news1.jpg"),
    is_published: str = Form("false"),
    user=Depends(get_current_user)
):
    """Save tin tức — tạo mới hoặc cập nhật."""
    db = get_db()
    published = is_published == "true"
    try:
        if news_id:
            db._execute_query(
                "UPDATE news SET title=%s, category=%s, summary=%s, content=%s, image=%s, is_published=%s WHERE id=%s",
                (title, category, summary, content, image, published, int(news_id)), commit=True
            )
        else:
            db._execute_query(
                "INSERT INTO news (title, category, summary, content, image, is_published) VALUES (%s, %s, %s, %s, %s, %s)",
                (title, category, summary, content, image, published), commit=True
            )
    except Exception as e:
        logger.error(f"Error saving news: {e}")
    return RedirectResponse(url="/admin/cms", status_code=303)

@router.delete("/projects/{project_id}", response_class=HTMLResponse)
async def delete_project(project_id: int, user=Depends(get_current_user)):
    """Delete a project via HTMX."""
    db = get_db()
    db.delete("projects", {"id": project_id})
    return HTMLResponse(content="")

@router.delete("/news/{news_id}", response_class=HTMLResponse)
async def delete_news(news_id: int, user=Depends(get_current_user)):
    """Delete news via HTMX."""
    db = get_db()
    db.delete("news", {"id": news_id})
    return HTMLResponse(content="")

@router.post("/projects/update/{project_id}", response_class=HTMLResponse)
async def update_project(
    project_id: int,
    request: Request,
    name: str = Form(...),
    location: str = Form(...),
    area: str = Form(...),
    height: str = Form(""),
    bays: str = Form(""),
    load_capacity: str = Form(""),
    image: str = Form("/static/brain/wh_real1.jpg"),
    user=Depends(get_current_user)
):
    """Update a project."""
    db = get_db()
    try:
        db._execute_query(
            "UPDATE projects SET name=%s, location=%s, area=%s, height=%s, bays=%s, load_capacity=%s, image=%s WHERE id=%s",
            (name, location, area, height, bays, load_capacity, image, project_id),
            commit=True
        )
    except Exception as e:
        logger.error(f"Error updating project: {e}")

    return RedirectResponse(url="/admin/cms", status_code=303)

@router.post("/news/update/{news_id}", response_class=HTMLResponse)
async def update_news(
    news_id: int,
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    summary: str = Form(""),
    image: str = Form("/static/brain/news1.jpg"),
    user=Depends(get_current_user)
):
    """Update a news item."""
    db = get_db()
    try:
        db._execute_query(
            "UPDATE news SET title=%s, category=%s, summary=%s, image=%s WHERE id=%s",
            (title, category, summary, image, news_id),
            commit=True
        )
    except Exception as e:
        logger.error(f"Error updating news: {e}")

    return RedirectResponse(url="/admin/cms", status_code=303)


@router.post("/upload-image")
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(get_current_user)
):
    """Upload ảnh, trả về JSON {url: ...} cho CKEditor hoặc HTMX."""
    import uuid

    allowed = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if file.content_type not in allowed:
        return JSONResponse({"error": "Chỉ chấp nhận JPG, PNG, WEBP, GIF"}, status_code=400)

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "jpg"
    filename = f"{uuid.uuid4().hex[:12]}.{ext}"
    upload_dir = Path(__file__).parent.parent / "static" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / filename

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:  # 10MB limit
        return JSONResponse({"error": "File quá lớn (tối đa 10MB)"}, status_code=400)

    dest.write_bytes(contents)
    url = f"/static/uploads/{filename}"
    return JSONResponse({"url": url})
