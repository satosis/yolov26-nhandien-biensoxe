"""Cầu nối HTTP của frontend.modules.listings.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.modules.listings.controller as controller


def list_page(
    request: Request,
    service: str = Query('thue-xe-cau'),
    area: str = Query('phu-tho'),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('listings')),
):
    return controller.list_page(request=request, service=service, area=area, page=page, user=user)


def create_directory(
    name: str = Form(''),
    url: str = Form(''),
    note: str = Form(''),
    service: str = Form(''),
    area: str = Form(''),
    user: dict = Depends(require_menu('listings')),
):
    return controller.create_directory(name=name, url=url, note=note, service=service, area=area, user=user)


def update_directory(
    directory_id: int,
    name: str = Form(''),
    url: str = Form(''),
    posted_url: str = Form(''),
    posted_on: str = Form(''),
    note: str = Form(''),
    service: str = Form(''),
    area: str = Form(''),
    user: dict = Depends(require_menu('listings')),
):
    return controller.update_directory(directory_id=directory_id, name=name, url=url, posted_url=posted_url, posted_on=posted_on, note=note, service=service, area=area, user=user)


def delete_directory(
    directory_id: int,
    service: str = Form(''),
    area: str = Form(''),
    user: dict = Depends(require_menu('listings')),
):
    return controller.delete_directory(directory_id=directory_id, service=service, area=area, user=user)


def mark_directory(
    directory_id: int,
    status: str = Form(''),
    posted_url: str = Form(''),
    posted_on: str = Form(''),
    service: str = Form(''),
    area: str = Form(''),
    user: dict = Depends(require_menu('listings')),
):
    return controller.mark_directory(directory_id=directory_id, status=status, posted_url=posted_url, posted_on=posted_on, service=service, area=area, user=user)
