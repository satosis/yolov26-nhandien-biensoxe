"""Cầu nối HTTP của frontend.routers.telegram_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Depends, Form
from frontend.deps import require_menu
import frontend.routers.telegram_controller as controller


async def telegram_page(request: Request, user: dict = Depends(require_menu('telegram'))):
    return await controller.telegram_page(request=request, user=user)


async def add_bot(
    request: Request,
    bot_name: str = Form(...),
    token: str = Form(None),
    bot_token: str = Form(None),
    chat_id: str = Form(...),
    user: dict = Depends(require_menu('telegram')),
):
    return await controller.add_bot(request=request, bot_name=bot_name, token=token, bot_token=bot_token, chat_id=chat_id, user=user)


async def test_bot(bot_name: str, _: dict = Depends(require_menu('telegram'))):
    return await controller.test_bot(bot_name=bot_name, _=_)


async def delete_bot(bot_name: str, _: dict = Depends(require_menu('telegram'))):
    return await controller.delete_bot(bot_name=bot_name, _=_)
