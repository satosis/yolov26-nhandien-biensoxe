from fastapi import APIRouter, Request, Form, Depends, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pathlib import Path

from frontend.deps import get_db, get_current_user, get_menu_context, templates

router = APIRouter(prefix="/cms", tags=["cms"])


def get_projects(db, limit: int = 10) -> list:
    query = """
        SELECT id, name, location, area, height, bays, load_capacity, image, description
        FROM projects
        ORDER BY created_at DESC
        LIMIT %s
    """
    return db._execute_query(query, (limit,), fetchall=True, commit=False)


def get_news(db, limit: int = 5) -> list:
    query = """
        SELECT id, slug, title, category, summary, image, publish_date
        FROM news
        ORDER BY publish_date DESC
        LIMIT %s
    """
    rows = db._execute_query(query, (limit,), fetchall=True, commit=False)
    for row in rows:
        if row.get("publish_date") and not isinstance(row["publish_date"], str):
            row["publish_date"] = row["publish_date"].isoformat()
    return rows


def get_news_by_slug(db, slug: str) -> dict | None:
    item = db._execute_query(
        "SELECT id, slug, title, category, summary, content, image, publish_date FROM news WHERE slug = %s AND is_published IS DISTINCT FROM false",
        (slug,), fetch=True, commit=False
    )
    if item:
        published = item.get("publish_date")
        item["publish_date_str"] = published.strftime("%d/%m/%Y") if hasattr(published, "strftime") else str(published or "")
    return item


def get_news_by_id(db, news_id: int) -> dict | None:
    item = db._execute_query(
        "SELECT id, slug, title, category, summary, content, image, publish_date FROM news WHERE id = %s AND is_published IS DISTINCT FROM false",
        (news_id,), fetch=True, commit=False
    )
    if item:
        published = item.get("publish_date")
        item["publish_date_str"] = published.strftime("%d/%m/%Y") if hasattr(published, "strftime") else str(published or "")
    return item


def get_landing_settings(db) -> dict:
    rows = db._execute_query(
        "SELECT key, value FROM app_settings WHERE key LIKE 'LANDING_%'",
        fetchall=True,
        commit=False,
    )
    return {row["key"]: row["value"] for row in rows}


@router.get("/", response_class=HTMLResponse)
async def cms_dashboard(request: Request, user=Depends(get_current_user)):
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
    db = get_db()
    db._execute_query(
        "INSERT INTO projects (name, location, area, height, bays, load_capacity, image) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (name, location, area, height, bays, load_capacity, image), commit=True
    )

    return RedirectResponse(url="/admin/cms", status_code=303)


@router.get("/news/new", response_class=HTMLResponse)
async def news_new_page(request: Request, user=Depends(get_current_user)):
    ctx = get_menu_context(request, user)
    return templates.TemplateResponse("pages/cms_news_edit.html", {
        "request": request, "user": user, "item": None, "current_menu": "cms", **ctx
    })

@router.get("/news/{news_id}/edit", response_class=HTMLResponse)
async def news_edit_page(news_id: int, request: Request, user=Depends(get_current_user)):
    db = get_db()
    item = db._execute_query("SELECT * FROM news WHERE id=%s", (news_id,), fetch=True)
    if not item:
        return RedirectResponse("/admin/cms")
    ctx = get_menu_context(request, user)
    return templates.TemplateResponse("pages/cms_news_edit.html", {
        "request": request, "user": user, "item": item, "current_menu": "cms", **ctx
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
    db = get_db()
    published = is_published == "true"
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
    return RedirectResponse(url="/admin/cms", status_code=303)

@router.delete("/projects/{project_id}", response_class=HTMLResponse)
async def delete_project(project_id: int, user=Depends(get_current_user)):
    db = get_db()
    db.delete("projects", {"id": project_id})
    return HTMLResponse(content="")

@router.delete("/news/{news_id}", response_class=HTMLResponse)
async def delete_news(news_id: int, user=Depends(get_current_user)):
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
    db = get_db()
    db._execute_query(
        "UPDATE projects SET name=%s, location=%s, area=%s, height=%s, bays=%s, load_capacity=%s, image=%s WHERE id=%s",
        (name, location, area, height, bays, load_capacity, image, project_id), commit=True
    )

    return RedirectResponse(url="/admin/cms", status_code=303)



@router.post("/upload-image")
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(get_current_user)
):
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
    if len(contents) > 10 * 1024 * 1024:
        return JSONResponse({"error": "File quá lớn (tối đa 10MB)"}, status_code=400)

    dest.write_bytes(contents)
    url = f"/static/uploads/{filename}"
    return JSONResponse({"url": url})
