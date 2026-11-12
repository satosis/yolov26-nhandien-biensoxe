"""Router quản lý tin dự án: danh sách tin, lưu thành khách tiềm năng, bỏ qua và cập nhật truy vấn."""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import RedirectResponse

from backend.services import project_news as project_news_service
from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate_counts
from frontend.redirects import redirect_with_query

logger = logging.getLogger(__name__)

BASE_URL = "/admin/advertising/project-news"
PER_PAGE = 20


def _back(status: str = "", page: int = 1, msg: str = "", error: str = "") -> RedirectResponse:
    return redirect_with_query(BASE_URL, status=status, page=page if page > 1 else 0, msg=msg, error=error)


def list_page(request: Request, status: str, page: int, user: dict):
    """Trang danh sách tin dự án (chỉ đọc DB)."""
    clean_status = status.strip()
    offset = (page - 1) * PER_PAGE

    total = project_news_service.count_news(status=clean_status)
    rows = project_news_service.list_news(status=clean_status, limit=PER_PAGE, offset=offset)
    st_counts = project_news_service.status_counts()
    queries = project_news_service.get_queries()

    pagination = paginate_counts(
        total,
        page=page,
        per_page=PER_PAGE,
        base_url=BASE_URL,
        extra_params={"status": clean_status} if clean_status else {},
    )

    ctx = get_menu_context(request, user)
    ctx.update({
        "current_menu": "project_news",
        "page_title": "Tin dự án",
        "news": rows,
        "total": total,
        "status_counts": st_counts,
        "pagination": pagination,
        "status": clean_status,
        "queries_text": "\n".join(queries),
        "msg": request.query_params.get("msg", ""),
        "error": request.query_params.get("error", ""),
    })
    return templates.TemplateResponse("pages/advertising/project_news.html", {"request": request, **ctx})


def save_news(news_id: int, status: str, page: int, user: dict):
    """Lưu tin dự án thành khách tiềm năng."""
    prospect_id = project_news_service.save_as_prospect(news_id)
    if not prospect_id:
        return _back(status=status, page=page, error="Không lưu được khách.")
    return _back(status=status, page=page, msg="Đã lưu.")


def skip_news(news_id: int, status: str, page: int, user: dict):
    """Bỏ qua tin dự án."""
    ok = project_news_service.set_status(news_id, "skipped")
    if not ok:
        return _back(status=status, page=page, error="Không cập nhật được tin.")
    return _back(status=status, page=page, msg="Đã lưu.")


def update_queries(queries: str, status: str, page: int, user: dict):
    """Lưu danh sách truy vấn tìm kiếm."""
    project_news_service.save_queries(queries)
    return _back(status=status, page=page, msg="Đã lưu.")


def refresh_news(status: str, page: int, user: dict):
    """Cập nhật tin dự án ngay lập tức."""
    res = project_news_service.refresh()
    new_count = len(res.get("new", []))
    if new_count > 0:
        msg = f"Có {new_count} tin mới."
    else:
        msg = "Không có tin mới."
    return _back(status=status, page=page, msg=msg)
