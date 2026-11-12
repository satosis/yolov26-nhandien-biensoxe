"""Log every unhandled web error and answer AJAX callers with JSON instead of an HTML error page."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger("frontend.errors")
MESSAGE = "Lỗi hệ thống, đã ghi log. Vui lòng thử lại hoặc báo quản trị."


def _wants_json(request: Request) -> bool:
    return (request.headers.get("x-requested-with") == "XMLHttpRequest"
            or "application/json" in request.headers.get("accept", ""))


async def unhandled_error(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    if _wants_json(request):
        return JSONResponse({"ok": False, "error": MESSAGE}, status_code=500)
    return PlainTextResponse(MESSAGE, status_code=500)


def register_error_handlers(app: FastAPI) -> None:
    # Chỉ Exception chung: HTTPException và lỗi validate giữ xử lý mặc định của FastAPI.
    app.add_exception_handler(Exception, unhandled_error)
