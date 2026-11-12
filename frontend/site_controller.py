"""Handler của các route cấp ứng dụng (trang gốc quản trị, service worker, websocket thông báo)."""
import logging

from fastapi import BackgroundTasks, Request, WebSocket
from starlette.responses import RedirectResponse, Response

from frontend.notifications import InquiryNotification, manager

_KILL_SW_JS = """
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map(k => caches.delete(k)));
    await self.registration.unregister();
    const cs = await self.clients.matchAll({ type: 'window' });
    await Promise.all(cs.map(c => c.navigate(c.url)));
  })());
});
""".strip()


async def admin_root(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("role") != "Admin":
        return RedirectResponse("/tai-khoan", status_code=303)
    return RedirectResponse("/admin/detection", status_code=303)


async def kill_service_worker():
    return Response(
        content=_KILL_SW_JS,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
    finally:
        manager.disconnect(websocket)


async def notify_inquiry(data: InquiryNotification, background_tasks: BackgroundTasks, request: Request):
    await manager.broadcast({"type": "NEW_INQUIRY", "data": data.model_dump()})
    try:
        from frontend.routers.landing_controller import send_inquiry_telegram_alert, InquiryInput
        inq_item = InquiryInput(
            full_name=data.full_name,
            phone=data.phone,
            company_name="",
            demand=data.demand,
            source_page=data.source_page,
        )
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8001"
        base_url = f"{proto}://{host}"
        background_tasks.add_task(send_inquiry_telegram_alert, inq_item, data.id, base_url)
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not dispatch Telegram alert: %s", exc)
    return {"status": "ok"}
