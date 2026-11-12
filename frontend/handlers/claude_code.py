"""Cầu nối HTTP của frontend.modules.claude_code.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse

import frontend.modules.claude_code.controller as controller


def conversation(user: dict = Depends(controller.require_owner)) -> JSONResponse:
    'Cuộc trò chuyện hiện tại với Claude.'
    return controller.conversation(user=user)


def new_conversation(user: dict = Depends(controller.require_owner_write)) -> JSONResponse:
    'Bắt đầu cuộc trò chuyện mới; Claude không nhớ các tin trước.'
    return controller.new_conversation(user=user)


def send(
    message: str = Form(''),
    page_path: str = Form(''),
    page_title: str = Form(''),
    files: list[UploadFile] = File([]),
    user: dict = Depends(controller.require_owner_write),
) -> JSONResponse:
    'Gửi tin nhắn kèm ảnh/file: bắt đầu một lượt chạy Claude.'
    return controller.send(message=message, page_path=page_path, page_title=page_title, files=files, user=user)


def run_events(
    run_id: str,
    after: int = Query(0, ge=0),
    user: dict = Depends(controller.require_owner),
) -> JSONResponse:
    'Diễn biến mới của một lượt chạy kể từ byte after.'
    return controller.run_events(run_id=run_id, after=after, user=user)


def stop(run_id: str, user: dict = Depends(controller.require_owner_write)) -> JSONResponse:
    'Dừng lượt chạy.'
    return controller.stop(run_id=run_id, user=user)
