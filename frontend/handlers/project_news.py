"""Cầu nối HTTP của frontend.modules.project_news.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.modules.project_news.controller as controller


def list_page(
    request: Request,
    status: str = Query(''),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('project_news')),
):
    'Trang danh sách tin dự án (chỉ đọc DB).'
    return controller.list_page(request=request, status=status, page=page, user=user)


def save_news(
    news_id: int,
    status: str = Query(''),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('project_news')),
):
    'Lưu tin dự án thành khách tiềm năng.'
    return controller.save_news(news_id=news_id, status=status, page=page, user=user)


def skip_news(
    news_id: int,
    status: str = Query(''),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('project_news')),
):
    'Bỏ qua tin dự án.'
    return controller.skip_news(news_id=news_id, status=status, page=page, user=user)


def update_queries(
    queries: str = Form(''),
    status: str = Query(''),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('project_news')),
):
    'Lưu danh sách truy vấn tìm kiếm.'
    return controller.update_queries(queries=queries, status=status, page=page, user=user)


def refresh_news(
    status: str = Query(''),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('project_news')),
):
    'Cập nhật tin dự án ngay lập tức.'
    return controller.refresh_news(status=status, page=page, user=user)
