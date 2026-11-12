"""Nghiệp vụ Live Chatbot nội bộ hệ thống giữa Khách hàng và Quản trị viên.

Toàn bộ logic truy vấn CSDL, điều phối phiên chat, nhận diện dịch vụ,
phát thông báo WebSocket và kiểm tra quyền hạn đều tập trung tại đây.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from backend.services import config as cfg
from backend.services.database import db
from frontend.deps import templates
from frontend.context.menu_context import get_menu_context
from frontend.notifications import manager

logger = logging.getLogger(__name__)


_VN_TZ = timezone(timedelta(hours=7))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_vn(dt, fmt: str) -> str:
    if not dt:
        return ""
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_VN_TZ).strftime(fmt)


def serialize_row(row: dict | None) -> dict | None:
    """Chuyển đổi datetime/date trong dict DB sang ISO string để JSONResponse không lỗi."""
    if not row:
        return row
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            out[k] = v.astimezone(_VN_TZ).isoformat()
        else:
            out[k] = v
    return out


def serialize_rows(rows: list[dict] | None) -> list[dict]:
    return [serialize_row(r) for r in (rows or []) if r is not None]


# =========================================================================
# TỆP ĐÍNH KÈM: lưu trong data/chat_uploads (ngoài Git), mở qua /chat-files/<tên>
# =========================================================================

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _attachment_fields(row: dict) -> dict:
    """The three fields a page needs to draw a message's file: url, original name, 'image' or 'file'."""
    path = row.get("attachment_path")
    return {
        "attachment_url": f"/chat-files/{path}" if path else "",
        "attachment_name": row.get("attachment_name") or "",
        "attachment_type": row.get("attachment_type") or "",
    }


def _save_upload(upload: UploadFile) -> dict:
    """Store one uploaded file under a random name and return what the message row records."""
    original = Path((upload.filename or "tệp").replace("\\", "/")).name or "tệp"
    suffix = Path(original).suffix.lower()
    stored = uuid.uuid4().hex + re.sub(r"[^a-z0-9.]", "", suffix)[:12]
    with open(Path(cfg.CHAT_UPLOAD_DIR) / stored, "wb") as out:
        shutil.copyfileobj(upload.file, out)
    return {"path": stored, "name": original, "type": "image" if suffix in _IMAGE_EXTS else "file"}


def chat_file_response(name: str) -> FileResponse:
    """A file a customer or an admin sent. Only images open in the browser; everything else (html, svg, ...) is
    downloaded, so an uploaded page can never run on this site."""
    path = Path(cfg.CHAT_UPLOAD_DIR) / Path(name).name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp.")
    row = db.execute_query("SELECT attachment_name FROM chat_messages WHERE attachment_path = %s", (path.name,), fetch=True)
    return FileResponse(
        path, filename=(row or {}).get("attachment_name") or path.name,
        content_disposition_type="inline" if path.suffix.lower() in _IMAGE_EXTS else "attachment",
        headers={"X-Content-Type-Options": "nosniff"})


def _preview(text: str, attachment: dict | None) -> str:
    """What the conversation list shows for a message."""
    if text:
        return text
    return "[Hình ảnh]" if attachment and attachment["type"] == "image" else f"[Tệp] {attachment['name']}"


def _record_message(session: dict, sender: str, sender_name: str, text: str, attachment: dict | None) -> dict:
    """Insert one message and update the conversation row (preview, unread counters, status)."""
    msg_row = db.execute_query(
        """
        INSERT INTO chat_messages (session_id, sender, sender_name, message, is_read, created_at,
                                   attachment_path, attachment_name, attachment_type)
        VALUES (%s, %s, %s, %s, FALSE, NOW(), %s, %s, %s)
        RETURNING id, created_at
        """,
        (session["id"], sender, sender_name, text,
         attachment and attachment["path"], attachment and attachment["name"], attachment and attachment["type"]),
        fetch=True,
    )
    if sender == "admin":
        counters = "unread_admin_count = 0, unread_customer_count = unread_customer_count + 1"
    else:  # the customer, or the system's notice (both sides see it and it is the admin's turn): back to waiting
        counters = "unread_admin_count = unread_admin_count + 1, status = 'open'"
        if sender == "system":
            counters += ", unread_customer_count = unread_customer_count + 1"
    db.execute_query(
        f"""
        UPDATE chat_sessions
        SET last_message = %s, last_message_sender = %s, last_message_at = NOW(), {counters}, updated_at = NOW()
        WHERE id = %s
        """,
        (_preview(text, attachment), "admin" if sender == "admin" else "customer", session["id"]),
    )
    return {
        "id": msg_row["id"] if msg_row else None,
        "sender": sender,
        "sender_name": sender_name,
        "message": text,
        "preview": _preview(text, attachment),
        "created_at": msg_row["created_at"].isoformat() if msg_row and msg_row.get("created_at") else _now_iso(),
        "time": _fmt_vn(msg_row["created_at"] if msg_row else datetime.now(timezone.utc), "%H:%M"),
        **_attachment_fields({"attachment_path": attachment and attachment["path"],
                              "attachment_name": attachment and attachment["name"],
                              "attachment_type": attachment and attachment["type"]}),
    }


async def _save_uploads(files: list[UploadFile]) -> list[dict]:
    return [await asyncio.to_thread(_save_upload, upload) for upload in files if upload.filename]


# =========================================================================
# QUẢN LÝ PHIÊN CHAT (SESSIONS) & TIN NHẮN (MESSAGES)
# =========================================================================

def get_or_create_session(
    session_token: str | None = None,
    customer_name: str = "",
    customer_contact: str = "",
    service_name: str = "",
    service_group: str = "",
    service_category: str = "",
    current_url: str = "",
    user: dict | None = None,
) -> dict:
    """Lấy hoặc tạo mới phiên chat của khách hàng."""
    user_id = user.get("id") if user else None
    if user and not customer_name:
        customer_name = user.get("full_name") or user.get("username") or ""
    if user and not customer_contact:
        customer_contact = user.get("email") or ""

    token = (session_token or "").strip()
    session: dict | None = None

    if token:
        session = db.execute_query(
            "SELECT * FROM chat_sessions WHERE session_token = %s",
            (token,),
            fetch=True,
        )

    if session:
        # Cập nhật thông tin dịch vụ / trang URL khách đang xem nếu có thay đổi
        updates = []
        params = []
        if customer_name and customer_name != session.get("customer_name"):
            updates.append("customer_name = %s")
            params.append(customer_name)
        if customer_contact and customer_contact != session.get("customer_contact"):
            updates.append("customer_contact = %s")
            params.append(customer_contact)
        if service_name and service_name != session.get("service_name"):
            updates.append("service_name = %s")
            params.append(service_name)
        if service_group and service_group != session.get("service_group"):
            updates.append("service_group = %s")
            params.append(service_group)
        if service_category and service_category != session.get("service_category"):
            updates.append("service_category = %s")
            params.append(service_category)
        if current_url and current_url != session.get("current_url"):
            updates.append("current_url = %s")
            params.append(current_url)
        if user_id and not session.get("user_id"):
            updates.append("user_id = %s")
            params.append(user_id)

        if updates:
            updates.append("updated_at = NOW()")
            params.append(session["id"])
            query = f"UPDATE chat_sessions SET {', '.join(updates)} WHERE id = %s RETURNING *"
            updated = db.execute_query(query, tuple(params), fetch=True)
            if updated:
                session = updated
        return session

    # Tạo mới phiên chat
    new_token = token or uuid.uuid4().hex
    display_name = customer_name.strip() or "Khách hàng"
    svc_name = service_name.strip() or "Tài khoản số & AI"
    url = current_url.strip() or "/tai-khoan"

    insert_sql = """
        INSERT INTO chat_sessions (
            session_token, user_id, customer_name, customer_contact,
            service_name, service_group, service_category, current_url,
            status, unread_admin_count, unread_customer_count,
            last_message, last_message_sender, last_message_at,
            created_at, updated_at
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            'open', 0, 0,
            '', 'customer', NOW(),
            NOW(), NOW()
        )
        RETURNING *
    """
    row = db.execute_query(
        insert_sql,
        (
            new_token, user_id, display_name, customer_contact.strip(),
            svc_name, service_group.strip(), service_category.strip(), url,
        ),
        fetch=True,
    )
    return row or {"session_token": new_token, "customer_name": display_name, "service_name": svc_name}


async def customer_send_message(
    session_token: str,
    message: str,
    customer_name: str = "",
    customer_contact: str = "",
    service_name: str = "",
    service_group: str = "",
    service_category: str = "",
    current_url: str = "",
    user: dict | None = None,
    files: list[UploadFile] | None = None,
) -> JSONResponse:
    """Khách hàng gửi tin nhắn (chữ, ảnh, tệp) vào hệ thống live chat; mỗi tệp là một tin nhắn."""
    text = (message or "").strip()
    attachments = await _save_uploads(files or [])
    if not text and not attachments:
        return JSONResponse({"ok": False, "error": "Vui lòng nhập nội dung tin nhắn."}, status_code=400)

    session = get_or_create_session(
        session_token=session_token,
        customer_name=customer_name,
        customer_contact=customer_contact,
        service_name=service_name,
        service_group=service_group,
        service_category=service_category,
        current_url=current_url,
        user=user,
    )
    c_name = session.get("customer_name") or "Khách hàng"
    parts = ([(text, None)] if text else []) + [("", attachment) for attachment in attachments]
    sent = [_record_message(session, "customer", c_name, body, attachment) for body, attachment in parts]

    # Phát thông báo WebSocket tới tất cả Quản trị viên
    for item in sent:
        try:
            await manager.broadcast({
                "type": "CHAT_MESSAGE",
                "data": {
                    "session_id": session["id"],
                    "session_token": session["session_token"],
                    "sender": "customer",
                    "customer_name": c_name,
                    "customer_contact": session.get("customer_contact") or "",
                    "sender_name": c_name,
                    "service_name": session.get("service_name") or "Dịch vụ tài khoản số",
                    "current_url": session.get("current_url") or "",
                    "message": item["message"],
                    "preview": item["preview"],
                    "attachment": {"url": item["attachment_url"], "name": item["attachment_name"], "type": item["attachment_type"]}
                    if item["attachment_url"] else None,
                    "message_id": item["id"],
                    "created_at": item["created_at"],
                    "time_short": item["time"],
                },
            })
        except Exception as exc:
            logger.warning("Không thể phát WebSocket chat: %s", exc)

    return JSONResponse({
        "ok": True,
        "session_token": session["session_token"],
        "session_id": session["id"],
        "message_id": sent[-1]["id"],
        "messages": sent,
    })


async def post_system_notice(session_token: str, customer_name: str, customer_contact: str, service_name: str,
                             current_url: str, text: str) -> None:
    """Một thông báo của hệ thống (ví dụ đơn đã thanh toán) vào cuộc trò chuyện của khách; chưa có thì tạo mới."""
    session = get_or_create_session(
        session_token=session_token, customer_name=customer_name, customer_contact=customer_contact,
        service_name=service_name, current_url=current_url)
    item = _record_message(session, "system", "Hệ thống", text, None)
    try:
        await manager.broadcast({
            "type": "CHAT_MESSAGE",
            "data": {
                "session_id": session["id"],
                "session_token": session["session_token"],
                "sender": "system",
                "customer_name": session.get("customer_name") or "Khách hàng",
                "customer_contact": session.get("customer_contact") or "",
                "sender_name": "Hệ thống",
                "service_name": session.get("service_name") or "",
                "current_url": session.get("current_url") or "",
                "message": item["message"],
                "preview": item["preview"],
                "attachment": None,
                "message_id": item["id"],
                "created_at": item["created_at"],
                "time_short": item["time"],
            },
        })
    except Exception as exc:
        logger.warning("Không thể phát WebSocket chat: %s", exc)


def get_session_messages(session_token: str, mark_read: bool = True) -> JSONResponse:
    """Lấy danh sách tin nhắn của 1 phiên chat (dành cho Khách hàng)."""
    token = (session_token or "").strip()
    if not token:
        return JSONResponse({"ok": True, "messages": [], "session": None})

    session = db.execute_query(
        "SELECT * FROM chat_sessions WHERE session_token = %s",
        (token,),
        fetch=True,
    )
    if not session:
        return JSONResponse({"ok": True, "messages": [], "session": None})

    session_id = session["id"]

    # Đánh dấu khách đã đọc tin nhắn của admin
    if mark_read:
        db.execute_query(
            "UPDATE chat_messages SET is_read = TRUE WHERE session_id = %s AND sender IN ('admin', 'system') AND is_read = FALSE",
            (session_id,),
        )
        db.execute_query(
            "UPDATE chat_sessions SET unread_customer_count = 0 WHERE id = %s",
            (session_id,),
        )

    messages = db.execute_query(
        """
        SELECT id, session_id, sender, sender_name, message, is_read, created_at,
               attachment_path, attachment_name, attachment_type
        FROM chat_messages
        WHERE session_id = %s
        ORDER BY id ASC
        """,
        (session_id,),
        fetchall=True,
    ) or []

    # Định dạng timestamp an toàn JSON theo giờ Việt Nam (GMT+7)
    formatted = []
    for m in messages:
        ts = _fmt_vn(m.get("created_at"), "%H:%M %d/%m")
        formatted.append({
            "id": m["id"],
            "sender": m["sender"],
            "sender_name": m["sender_name"],
            "message": m["message"],
            "is_read": bool(m.get("is_read")),
            "time": ts,
            **_attachment_fields(m),
        })

    return JSONResponse({
        "ok": True,
        "session": {
            "id": session["id"],
            "session_token": session["session_token"],
            "customer_name": session["customer_name"],
            "service_name": session["service_name"],
            "status": session["status"],
        },
        "messages": formatted,
    })


# =========================================================================
# QUẢN TRỊ VIÊN: LIVE CHAT DASHBOARD & ACTIONS
# =========================================================================

def _search_filter(q: str) -> tuple[str, list]:
    if not (q or "").strip():
        return "1=1", []
    pat = f"%{q.strip()}%"
    return "(s.customer_name ILIKE %s OR s.customer_contact ILIKE %s OR s.service_name ILIKE %s OR s.last_message ILIKE %s)", [pat] * 4


def query_admin_sessions(q: str = "", limit: int = 100, offset: int = 0) -> dict:
    """Danh sách hội thoại của mọi dịch vụ, tin mới nhất trước, phân trang cho Admin khi lưu lượng khách lớn."""
    safe_limit = max(1, min(int(limit or 100), 200))
    safe_offset = max(0, int(offset or 0))
    where_sql, params = _search_filter(q)

    query = f"""
        SELECT s.*,
               u.avatar as customer_avatar,
               TO_CHAR(s.last_message_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'HH24:MI DD/MM') as last_msg_time,
               CASE
                   WHEN (s.last_message_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date = (NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date
                   THEN TO_CHAR(s.last_message_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'HH24:MI')
                   ELSE TO_CHAR(s.last_message_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'DD/MM')
               END as last_msg_time_short,
               TO_CHAR(s.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'HH24:MI DD/MM/YYYY') as created_time
        FROM chat_sessions s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE {where_sql}
        ORDER BY s.last_message_at DESC NULLS LAST, s.id DESC
        LIMIT %s OFFSET %s
    """
    rows = db.execute_query(query, tuple(params) + (safe_limit + 1, safe_offset), fetchall=True) or []
    has_more = len(rows) > safe_limit
    sliced = serialize_rows(rows[:safe_limit])
    return {
        "sessions": sliced,
        "has_more": has_more,
        "limit": safe_limit,
        "offset": safe_offset,
    }


def get_admin_session_detail(session_id: int, mark_read: bool = True) -> dict | None:
    """Lấy chi tiết phiên chat và toàn bộ tin nhắn cho Quản trị viên."""
    session = db.execute_query(
        """
        SELECT s.*,
               u.avatar as customer_avatar,
               TO_CHAR(s.last_message_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'HH24:MI DD/MM') as last_msg_time,
               CASE
                   WHEN (s.last_message_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date = (NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date
                   THEN TO_CHAR(s.last_message_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'HH24:MI')
                   ELSE TO_CHAR(s.last_message_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'DD/MM')
               END as last_msg_time_short,
               TO_CHAR(s.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'HH24:MI DD/MM/YYYY') as created_time
        FROM chat_sessions s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE s.id = %s
        """,
        (session_id,),
        fetch=True,
    )
    if not session:
        return None

    if mark_read:
        db.execute_query(
            "UPDATE chat_messages SET is_read = TRUE WHERE session_id = %s AND sender IN ('customer', 'system') AND is_read = FALSE",
            (session_id,),
        )
        db.execute_query(
            "UPDATE chat_sessions SET unread_admin_count = 0 WHERE id = %s",
            (session_id,),
        )
        session["unread_admin_count"] = 0

    messages = db.execute_query(
        """
        SELECT id, session_id, sender, sender_name, message, is_read, created_at,
               attachment_path, attachment_name, attachment_type,
               TO_CHAR(created_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'HH24:MI') as time_str,
               TO_CHAR(created_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'DD/MM') as date_str
        FROM chat_messages
        WHERE session_id = %s
        ORDER BY id ASC
        """,
        (session_id,),
        fetchall=True,
    ) or []

    out = serialize_row(session) or {}
    out["messages"] = [{**{k: v for k, v in m.items() if k != "attachment_path"}, **_attachment_fields(m)}
                       for m in serialize_rows(messages)]
    return out


async def admin_send_reply(session_id: int, admin_user: dict, message: str, files: list[UploadFile] | None = None) -> JSONResponse:
    """Quản trị viên trả lời khách (chữ, ảnh, tệp); mỗi tệp là một tin nhắn."""
    text = (message or "").strip()
    attachments = await _save_uploads(files or [])
    if not text and not attachments:
        return JSONResponse({"ok": False, "error": "Vui lòng nhập nội dung trả lời."}, status_code=400)

    session = db.execute_query("SELECT * FROM chat_sessions WHERE id = %s", (session_id,), fetch=True)
    if not session:
        return JSONResponse({"ok": False, "error": "Phiên trò chuyện không tồn tại."}, status_code=404)

    admin_name = admin_user.get("full_name") or admin_user.get("username") or "Quản trị viên"
    parts = ([(text, None)] if text else []) + [("", attachment) for attachment in attachments]
    sent = [_record_message(session, "admin", admin_name, body, attachment) for body, attachment in parts]

    for item in sent:
        try:
            await manager.broadcast({
                "type": "CHAT_REPLY",
                "data": {
                    "session_id": session_id,
                    "session_token": session["session_token"],
                    "sender": "admin",
                    "sender_name": admin_name,
                    "message": item["message"],
                    "preview": item["preview"],
                    "message_id": item["id"],
                    "created_at": item["created_at"],
                },
            })
        except Exception as exc:
            logger.warning("Không thể phát WebSocket chat reply: %s", exc)

    return JSONResponse({
        "ok": True,
        "message_id": sent[-1]["id"],
        "sender_name": admin_name,
        "time": sent[-1]["time"],
        "message": sent[-1]["preview"],
        "messages": sent,
    })


def admin_update_session_status(session_id: int, status: str) -> JSONResponse:
    """Cập nhật trạng thái phiên trò chuyện (open / resolved / closed)."""
    valid = {"open", "resolved", "closed"}
    st = status.strip().lower()
    if st not in valid:
        return JSONResponse({"ok": False, "error": "Trạng thái không hợp lệ."}, status_code=400)

    if st in {"resolved", "closed"}:
        db.execute_query(
            "UPDATE chat_sessions SET status = %s, unread_admin_count = 0, updated_at = NOW() WHERE id = %s",
            (st, session_id),
        )
    else:
        db.execute_query(
            "UPDATE chat_sessions SET status = %s, updated_at = NOW() WHERE id = %s",
            (st, session_id),
        )
    return JSONResponse({"ok": True, "status": st})


def render_admin_chat_page(
    request: Request,
    user: dict,
    session_id: int | None = None,
    q: str = "",
) -> HTMLResponse:
    """Render giao diện Live Chat dành cho Quản trị viên."""
    res = query_admin_sessions(q=q, limit=100, offset=0)
    sessions = res["sessions"]
    active_session = None

    if session_id:
        active_session = get_admin_session_detail(session_id, mark_read=True)

    # Vừa mark_read nên hội thoại đang mở không còn chấm chưa đọc
    if active_session:
        for item in sessions:
            if item["id"] == active_session["id"]:
                item["unread_admin_count"] = 0
                break

    ctx = {
        **get_menu_context(request, user),
        "current_menu": "account_live_chat",
        "request": request,
        "user": user,
        "sessions": sessions,
        "has_more_sessions": res["has_more"],
        "active_session": active_session,
        "active_session_id": active_session["id"] if active_session else None,
        "search_query": q,
    }
    return templates.TemplateResponse("pages/chat/admin_chat.html", ctx)

