"""Cầu nối HTTP của frontend.routers.cms_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Depends, Form, UploadFile, File
from frontend.deps import get_current_user
import frontend.routers.cms_controller as controller


async def cms_dashboard(
    request: Request,
    section: str = 'projects',
    page: int = 1,
    user: dict = Depends(get_current_user),
):
    return await controller.cms_dashboard(request=request, section=section, page=page, user=user)


async def add_project(
    name: str = Form(...),
    location: str = Form(...),
    area: str = Form(...),
    height: str = Form(...),
    bays: str = Form(...),
    load_capacity: str = Form(...),
    image: str = Form('/upload/brain/wh_real1.jpg'),
):
    return await controller.add_project(name=name, location=location, area=area, height=height, bays=bays, load_capacity=load_capacity, image=image)


async def news_new_page(request: Request, user: dict = Depends(get_current_user)):
    return await controller.news_new_page(request=request, user=user)


async def news_edit_page(news_id: int, request: Request, user: dict = Depends(get_current_user)):
    return await controller.news_edit_page(news_id=news_id, request=request, user=user)


async def news_save(
    news_id: str = Form(''),
    title: str = Form(...),
    category: str = Form(...),
    summary: str = Form(''),
    content: str = Form(''),
    image: str = Form('/upload/brain/news1.jpg'),
    is_published: str = Form('false'),
):
    return await controller.news_save(news_id=news_id, title=title, category=category, summary=summary, content=content, image=image, is_published=is_published)


async def delete_project(project_id: int):
    return await controller.delete_project(project_id=project_id)


async def delete_news(news_id: int):
    return await controller.delete_news(news_id=news_id)


async def update_project(
    project_id: int,
    name: str = Form(...),
    location: str = Form(...),
    area: str = Form(...),
    height: str = Form(''),
    bays: str = Form(''),
    load_capacity: str = Form(''),
    image: str = Form('/upload/brain/wh_real1.jpg'),
):
    return await controller.update_project(project_id=project_id, name=name, location=location, area=area, height=height, bays=bays, load_capacity=load_capacity, image=image)


async def upload_image(file: UploadFile = File(...)):
    return await controller.upload_image(file=file)
