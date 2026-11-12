"""Cầu nối HTTP của frontend.modules.facebook.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from backend.services import facebook_autopost as autopost
from fastapi import Request, Query, Depends, Form, UploadFile, File
from frontend.deps import require_menu
import frontend.modules.facebook.controller as controller


async def facebook_page_view(
    request: Request,
    tab: str = 'drafts',
    status: str = '',
    q: str = '',
    grp: str = '',
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('facebook_page')),
):
    return await controller.facebook_page_view(request=request, tab=tab, status=status, q=q, grp=grp, page=page, user=user)


async def facebook_run_health_check(user: dict = Depends(require_menu('facebook_page'))):
    return await controller.facebook_run_health_check(user=user)


async def create_draft(
    request: Request,
    message: str = Form(''),
    link: str = Form(''),
    publish_at: str = Form(''),
    action: str = Form('save'),
    back: str = Form('compose'),
    photos: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_menu('facebook_page')),
):
    return await controller.create_draft(request=request, message=message, link=link, publish_at=publish_at, action=action, back=back, photos=photos, user=user)


async def suggest_post(
    request: Request,
    topic: str = Form(''),
    photos: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_menu('facebook_page')),
):
    'Fill the compose form with AI text only; the schedule time stays whatever the user picked (empty = post now).\n    The AI looks at the first photo picked, from the pool or else the first upload; nothing is saved.'
    return await controller.suggest_post(request=request, topic=topic, photos=photos, user=user)


async def generate_draft(
    request: Request,
    topic: str = Form(''),
    publish_at: str = Form(''),
    photos: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_menu('facebook_page')),
):
    return await controller.generate_draft(request=request, topic=topic, publish_at=publish_at, photos=photos, user=user)


async def update_draft(
    draft_id: int,
    message: str = Form(''),
    link: str = Form(''),
    publish_at: str = Form(''),
    remove_photos: list[str] = Form(default=[]),
    photos: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_menu('facebook_page')),
):
    return await controller.update_draft(draft_id=draft_id, message=message, link=link, publish_at=publish_at, remove_photos=remove_photos, photos=photos, user=user)


async def approve_draft(draft_id: int, user: dict = Depends(require_menu('facebook_page'))):
    return await controller.approve_draft(draft_id=draft_id, user=user)


async def reject_draft(draft_id: int, user: dict = Depends(require_menu('facebook_page'))):
    return await controller.reject_draft(draft_id=draft_id, user=user)


async def delete_draft(draft_id: int, user: dict = Depends(require_menu('facebook_page'))):
    return await controller.delete_draft(draft_id=draft_id, user=user)


async def edit_post(
    post_id: str,
    message: str = Form(''),
    user: dict = Depends(require_menu('facebook_page')),
):
    return await controller.edit_post(post_id=post_id, message=message, user=user)


async def delete_post(post_id: str, user: dict = Depends(require_menu('facebook_page'))):
    return await controller.delete_post(post_id=post_id, user=user)


async def upload_photos(
    photos: list[UploadFile] = File(default=[]),
    grp: str = Form(''),
    caption: str = Form(''),
    user: dict = Depends(require_menu('facebook_page')),
):
    return await controller.upload_photos(photos=photos, grp=grp, caption=caption, user=user)


async def delete_photo(photo_id: int, user: dict = Depends(require_menu('facebook_page'))):
    return await controller.delete_photo(photo_id=photo_id, user=user)


async def radar_settings(
    keywords: str = Form(''),
    provinces: str = Form(''),
    user: dict = Depends(require_menu('facebook_page')),
):
    return await controller.radar_settings(keywords=keywords, provinces=provinces, user=user)


async def radar_draft(post: str = Form(''), user: dict = Depends(require_menu('facebook_page'))):
    "Comment and message drafted for a customer's post that a person pasted. Nothing is posted."
    return await controller.radar_draft(post=post, user=user)


async def radar_lead(
    name: str = Form(''),
    phone: str = Form(''),
    post: str = Form(''),
    user: dict = Depends(require_menu('facebook_page')),
):
    return await controller.radar_lead(name=name, phone=phone, post=post, user=user)


async def save_settings(
    enabled: str = Form(''),
    topics: str = Form(''),
    times: str = Form(', '.join(autopost.DEFAULT_TIMES)),
    days: list[int] = Form(default=[]),
    notes: str = Form(''),
    mode: str = Form('approve'),
    reply_enabled: str = Form(''),
    user: dict = Depends(require_menu('facebook_page')),
):
    return await controller.save_settings(enabled=enabled, topics=topics, times=times, days=days, notes=notes, mode=mode, reply_enabled=reply_enabled, user=user)
