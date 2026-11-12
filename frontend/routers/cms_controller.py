from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pathlib import Path
import uuid

from frontend.deps import get_menu_context, templates
from frontend.tracking import clean_gtm_id, clean_pixel_id


from frontend.pagination import paginate_counts
import frontend.routers.cms_queries as queries


def count_projects() -> int:
    row = queries.count_projects_select_projects()
    return row.get("total", 0) if row else 0


def count_news() -> int:
    row = queries.count_news_select_news()
    return row.get("total", 0) if row else 0


def get_news_by_slug(slug: str) -> dict | None:
    item = queries.get_news_by_slug_select_news(slug)
    if item:
        published = item.get("publish_date")
        item["publish_date_str"] = published.strftime("%d/%m/%Y") if hasattr(published, "strftime") else str(published or "")
    return item


def get_news_by_id(news_id: int) -> dict | None:
    item = queries.get_news_by_id_select_news(news_id)
    if item:
        published = item.get("publish_date")
        item["publish_date_str"] = published.strftime("%d/%m/%Y") if hasattr(published, "strftime") else str(published or "")
    return item


def get_landing_settings() -> dict:
    rows = queries.get_landing_settings_select_app_settings()
    result = {row["key"]: row["value"] for row in (rows or [])}
    if "GTM_ID" in result:
        result["GTM_ID"] = clean_gtm_id(result["GTM_ID"])
    if "FB_PIXEL_ID" in result:
        result["FB_PIXEL_ID"] = clean_pixel_id(result["FB_PIXEL_ID"])
    return result


async def cms_dashboard(request: Request, section: str, page: int, user: dict):
    page = max(1, page)
    per_page = 20
    offset = (page - 1) * per_page

    if section == "news":
        total_items = count_news()
        news = queries.get_news(limit=per_page, offset=offset)
        projects = []
    else:
        section = "projects"
        total_items = count_projects()
        projects = queries.get_projects(limit=per_page, offset=offset)
        news = []

    pagination = paginate_counts(
        total=total_items,
        page=page,
        per_page=per_page,
        base_url="/admin/cms",
        extra_params={"section": section},
    )

    ctx = get_menu_context(request, user)
    return templates.TemplateResponse(
        "pages/cms.html",
        {
            "request": request,
            "user": user,
            "projects": projects,
            "news": news,
            "section": section,
            "pagination": pagination,
            "current_menu": "cms",
            **ctx
        }
    )

async def add_project(
    name: str,
    location: str,
    area: str,
    height: str,
    bays: str,
    load_capacity: str,
    image: str,
):
    saved = queries.add_project_insert_projects(name, location, area, height, bays, load_capacity, image)
    if not saved:
        return HTMLResponse("Không thể lưu dự án.", status_code=500)
    return RedirectResponse(url="/admin/cms", status_code=303)


async def news_new_page(request: Request, user: dict):
    ctx = get_menu_context(request, user)
    return templates.TemplateResponse("pages/cms_news_edit.html", {
        "request": request, "user": user, "item": None, "current_menu": "cms", **ctx
    })

async def news_edit_page(news_id: int, request: Request, user: dict):
    item = queries.news_edit_page_select_news(news_id)
    if not item:
        return HTMLResponse("Không tìm thấy tin tức.", status_code=404)
    ctx = get_menu_context(request, user)
    return templates.TemplateResponse("pages/cms_news_edit.html", {
        "request": request, "user": user, "item": item, "current_menu": "cms", **ctx
    })

async def news_save(
    news_id: str,
    title: str,
    category: str,
    summary: str,
    content: str,
    image: str,
    is_published: str,
):
    published = is_published == "true"
    if news_id:
        try:
            news_id_value = int(news_id)
        except ValueError:
            return HTMLResponse("ID tin tức không hợp lệ.", status_code=422)
        saved = queries.news_save_update_news(title, category, summary, content, image, published, news_id_value)
    else:
        saved = queries.news_save_insert_news(title, category, summary, content, image, published)
    if not saved:
        return HTMLResponse("Không tìm thấy hoặc không thể lưu tin tức.", status_code=404 if news_id else 500)
    return RedirectResponse(url="/admin/cms", status_code=303)

async def delete_project(project_id: int):
    if not queries.delete_project_delete_projects(project_id):
        return HTMLResponse("Không tìm thấy dự án.", status_code=404)
    return HTMLResponse(content="")

async def delete_news(news_id: int):
    if not queries.delete_news_delete_news(news_id):
        return HTMLResponse("Không tìm thấy tin tức.", status_code=404)
    return HTMLResponse(content="")

async def update_project(
    project_id: int,
    name: str,
    location: str,
    area: str,
    height: str,
    bays: str,
    load_capacity: str,
    image: str,
):
    if not queries.update_project_update_projects(name, location, area, height, bays, load_capacity, image, project_id):
        return HTMLResponse("Không tìm thấy dự án.", status_code=404)

    return RedirectResponse(url="/admin/cms", status_code=303)


async def upload_image(file: UploadFile):
    extensions = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
    ext = extensions.get(file.content_type)
    if ext is None:
        return JSONResponse({"ok": False, "message": "Chỉ chấp nhận JPG, PNG, WEBP, GIF"}, status_code=400)

    filename = f"{uuid.uuid4().hex[:12]}.{ext}"
    upload_dir = Path(__file__).parent.parent / "static" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / filename

    dest.write_bytes(await file.read())
    url = f"/static/uploads/{filename}"
    return JSONResponse({"ok": True, "url": url})
