"""Review inbox for messages from the Đại Lộ xanh Zalo group."""

import asyncio
import hmac
import json
import logging
import os
import secrets
import subprocess
import urllib.parse
from pathlib import Path

import psutil

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from frontend.deps import get_menu_context, require_menu, templates
from frontend.pagination import paginate_counts
from frontend.modules.zalo.inbox import (
    INBOX_DIR,
    MAX_FILE_BYTES,
    bridge_secret,
    claim_salary_automation,
    count_messages,
    get_message,
    list_messages,
    mark_reviewed,
    save_extracted_lines,
    save_message,
    set_salary_advance_status,
)
from frontend.modules.zalo.payroll_automation import create_advance_from_message, looks_like_salary_advance
from frontend.modules.zalo.trip_requests import build_trip_draft
from frontend.modules.transport.catalog import list_items as list_catalog_items
from frontend.modules.administrative_divisions import list_provinces
from backend.ai.handwriting_ocr import extract_trip_candidates_from_document


router = APIRouter(prefix="/zalo_inbox")
logger = logging.getLogger(__name__)
bridge_secret()
BRIDGE_DIR = Path(__file__).resolve().parents[3] / "scripts" / "zalo_bridge"
PID_FILE = INBOX_DIR / "bridge.pid"


def _bridge_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        process = psutil.Process(int(PID_FILE.read_text(encoding="ascii").strip()))
        if not process.is_running():
            return False
        cmdline = " ".join(process.cmdline())
        return "bridge.js" in cmdline
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False


async def _process_salary_message(item_id: str, *, retry: bool = False, company_id: int | None = None) -> None:
    with claim_salary_automation(item_id) as claimed:
        if not claimed:
            return
        item = get_message(item_id)
        if not item or item.get("attachment_name") or not looks_like_salary_advance(item.get("text", "")):
            return
        previous = item.get("salary_advance") or {}
        if previous.get("advance_id") and previous.get("staff_id"):
            if previous.get("state") != "saved":
                set_salary_advance_status(item_id, {**previous, "state": "saved", "error": ""})
            return
        if previous and not retry:
            return
        set_salary_advance_status(item_id, {"state": "processing", "error": ""})
        target_company_id = company_id or item.get("company_id")
        if not target_company_id and item.get("group_name"):
            try:
                from frontend.modules.invoices.companies import find_company_by_name
                matched = find_company_by_name(item["group_name"])
                if matched:
                    target_company_id = matched["id"]
            except Exception:
                pass
        try:
            member, payment = await asyncio.to_thread(create_advance_from_message, item, target_company_id)
        except ValueError as exc:
            set_salary_advance_status(item_id, {"state": "needs_review", "error": str(exc)})
            return
        except Exception:
            logger.exception("Không xử lý được tạm ứng từ tin nhắn Zalo %s", item_id)
            set_salary_advance_status(item_id, {"state": "needs_review", "error": "Lỗi xử lý tạm ứng; kiểm tra dữ liệu rồi thử lại."})
            return
        details = {
            "staff_id": member["id"], "staff_name": payment["recipient_name"],
            "amount": str(int(payment["amount"])), "month": payment["month"].strftime("%Y-%m"),
            "advance_id": payment["id"],
        }
        set_salary_advance_status(item_id, {**details, "state": "saved", "error": ""})


@router.get("")
async def inbox_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu("zalo_inbox")),
):
    ctx = get_menu_context(request, user)
    total_messages = count_messages()
    page_data = paginate_counts(total_messages, page=page, per_page=20, base_url="/admin/zalo_inbox")
    items = list_messages(
        limit=page_data["per_page"],
        offset=(page_data["page"] - 1) * page_data["per_page"],
    )
    for item in items:
        item["salary_candidate"] = not item.get("attachment_name") and looks_like_salary_advance(item.get("text", ""))
    ctx["items"] = items
    ctx["pagination"] = page_data
    ctx["total_messages"] = total_messages
    ctx["current_menu"] = "zalo_inbox"
    status_file = INBOX_DIR / "bridge-status.json"
    ctx["bridge_status"] = json.loads(status_file.read_text(encoding="utf-8")) if status_file.exists() else None
    ctx["qr_ready"] = (INBOX_DIR / "qr.png").exists()
    ctx["bridge_running"] = _bridge_running()
    if "zalo_send_nonce" not in request.session:
        request.session["zalo_send_nonce"] = secrets.token_urlsafe(24)
    ctx["send_nonce"] = request.session["zalo_send_nonce"]
    ctx["draft_error"] = request.query_params.get("draft_error", "")

    # Active invoice gathering sessions
    from frontend.modules.zalo.invoice_automation import SESSIONS_DIR
    active_sessions = []
    if SESSIONS_DIR.exists():
        import time as _t
        now_ts = _t.time()
        for sp in SESSIONS_DIR.glob("*.json"):
            try:
                s_data = json.loads(sp.read_text(encoding="utf-8"))
                if s_data.get("status") == "collecting":
                    rem = max(0, int(300 - (now_ts - s_data.get("last_message_at", 0))))
                    s_data["remaining_seconds"] = rem
                    active_sessions.append(s_data)
            except Exception:
                pass
    ctx["active_invoice_sessions"] = active_sessions
    return templates.TemplateResponse("pages/zalo_inbox.html", {"request": request, **ctx})


@router.post("/trip-draft")
async def create_trip_draft(
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
    _: dict = Depends(require_menu("zalo_inbox")),
):
    form = await request.form()
    nonce = str(form.get("nonce") or "")
    expected = request.session.get("zalo_send_nonce", "")
    if not expected or not hmac.compare_digest(nonce, expected):
        raise HTTPException(status_code=403, detail="Yêu cầu không hợp lệ")
    ids = form.getlist("message_ids")
    if len(ids) != len(set(ids)) or not 1 <= len(ids) <= 20:
        return RedirectResponse("/admin/zalo_inbox?draft_error=Chọn từ 1 đến 20 tin nhắn.", status_code=303)
    messages = [get_message(item_id) for item_id in ids]
    if any(item is None for item in messages):
        raise HTTPException(status_code=404, detail="Không tìm thấy một tin nhắn đã chọn.")
    try:
        draft = build_trip_draft(
            messages,
            provinces=[row["name"] for row in list_provinces()],
            services=[row["name"] for row in list_catalog_items("service")],
            cargo_types=[row["name"] for row in list_catalog_items("cargo_type")],
        )
    except ValueError as exc:
        return RedirectResponse("/admin/zalo_inbox?" + urllib.parse.urlencode(
            {"draft_error": str(exc)}), status_code=303)
    recent = [item for item in list_messages(300)
              if item.get("group_id") == draft["group_id"]
              and str(item.get("sent_at") or "")[:10] == str(messages[0].get("sent_at") or "")[:10]]
    if any("tạo hóa đơn" in (item.get("text") or "").casefold()
           for item in recent):
        draft["possible_existing_invoice"] = True
    if draft.get("company_id"):
        request.session["active_company_id"] = draft["company_id"]
    draft["token"] = secrets.token_urlsafe(20)
    draft["user_id"] = user["id"]
    request.session["zalo_trip_draft"] = draft
    return RedirectResponse("/admin/vehicles/cargo/add?zalo_draft=1", status_code=303)


@router.post("/connect")
async def connect_bridge(_: dict = Depends(require_menu("zalo_inbox"))):
    if not _bridge_running():
        (INBOX_DIR / "qr.png").unlink(missing_ok=True)
        (INBOX_DIR / "bridge-status.json").write_text(
            json.dumps({"state": "starting", "detail": "Đang tạo mã QR kết nối"}), encoding="utf-8")
        logfile = (INBOX_DIR / "bridge.log").open("ab")
        try:
            process = subprocess.Popen(
                ["node", "bridge.js"], cwd=BRIDGE_DIR,
                stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT,
                creationflags=(subprocess.CREATE_NO_WINDOW | 0x00000008) if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        finally:
            logfile.close()
        PID_FILE.write_text(str(process.pid), encoding="ascii")
    return RedirectResponse("/admin/zalo_inbox", status_code=303)


@router.post("/disconnect")
async def disconnect_bridge(_: dict = Depends(require_menu("zalo_inbox"))):
    if _bridge_running():
        psutil.Process(int(PID_FILE.read_text(encoding="ascii"))).terminate()
    PID_FILE.unlink(missing_ok=True)
    (INBOX_DIR / "bridge-status.json").write_text(
        json.dumps({"state": "disconnected", "detail": "Đã ngắt kết nối"}), encoding="utf-8")
    return RedirectResponse("/admin/zalo_inbox", status_code=303)


@router.get("/companies")
async def list_companies_for_zalo_bridge(x_zalo_bridge_secret: str = Header("")):
    if not hmac.compare_digest(x_zalo_bridge_secret, bridge_secret()):
        raise HTTPException(status_code=401)
    from frontend.modules.invoices.companies import list_companies_for_bridge
    return {"ok": True, "companies": list_companies_for_bridge()}


@router.post("/ingest")
async def ingest_message(
    event: str = Form(...),
    file: UploadFile | None = File(None),
    x_zalo_bridge_secret: str = Header(""),
):
    if not hmac.compare_digest(x_zalo_bridge_secret, bridge_secret()):
        raise HTTPException(status_code=401)
    payload = json.loads(event)
    if not payload.get("group_id") or not payload.get("message_id"):
        raise HTTPException(status_code=422, detail="Missing group or message ID")
    content = await file.read(MAX_FILE_BYTES + 1) if file else None
    if content is not None and len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 20 MB")
    item, created = save_message(payload, content, with_created=True)
    if not item:
        return {"ok": False, "ignored": True, "detail": "Nhóm Zalo không khớp 100% với tên công ty hoặc không có MST hợp lệ"}
    if created:
        await _process_salary_message(item["id"])
        try:
            from frontend.modules.zalo.invoice_automation import process_invoice_message_ingest
            await process_invoice_message_ingest(item)
        except Exception as exc:
            logger.exception("Lỗi khi xử lý phiên tạo hóa đơn Zalo: %s", exc)
    return {"ok": True, "id": item["id"]}


@router.get("/drafts/{draft_id}/apply")
async def apply_saved_draft(
    draft_id: str,
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
    _: dict = Depends(require_menu("zalo_inbox")),
):
    from frontend.modules.zalo.invoice_automation import DRAFTS_DIR
    path = DRAFTS_DIR / f"{draft_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy bản nháp hóa đơn.")
    draft = json.loads(path.read_text(encoding="utf-8"))
    if draft.get("company_id"):
        request.session["active_company_id"] = draft["company_id"]
    draft["token"] = secrets.token_urlsafe(20)
    draft["user_id"] = user["id"]
    request.session["zalo_trip_draft"] = draft
    return RedirectResponse("/admin/vehicles/cargo/add?zalo_draft=1", status_code=303)


@router.post("/sessions/{group_id}/finalize-now")
async def finalize_session_now(
    group_id: str,
    request: Request,
    nonce: str = Form(...),
    user: dict = Depends(require_menu("vehicles")),
    _: dict = Depends(require_menu("zalo_inbox")),
):
    expected = request.session.get("zalo_send_nonce", "")
    if not expected or not hmac.compare_digest(nonce, expected):
        raise HTTPException(status_code=403, detail="Yêu cầu không hợp lệ")
    from frontend.modules.zalo.invoice_automation import finalize_invoice_session_sync
    draft = finalize_invoice_session_sync(group_id)
    if draft:
        if draft.get("company_id"):
            request.session["active_company_id"] = draft["company_id"]
        draft["token"] = secrets.token_urlsafe(20)
        draft["user_id"] = user["id"]
        request.session["zalo_trip_draft"] = draft
        return RedirectResponse("/admin/vehicles/cargo/add?zalo_draft=1", status_code=303)
    return RedirectResponse("/admin/zalo_inbox?draft_error=Không thể gom phiên tạo hóa đơn này.", status_code=303)


@router.post("/{item_id}/retry-salary")
async def retry_salary_message(
    item_id: str, request: Request, nonce: str = Form(...),
    _: dict = Depends(require_menu("zalo_inbox")),
):
    expected = request.session.get("zalo_send_nonce", "")
    if not expected or not hmac.compare_digest(nonce, expected):
        raise HTTPException(status_code=403, detail="Yêu cầu không hợp lệ")
    if not get_message(item_id):
        raise HTTPException(status_code=404)
    active_cid = request.session.get("active_company_id")
    await _process_salary_message(item_id, retry=True, company_id=active_cid)
    return RedirectResponse("/admin/zalo_inbox", status_code=303)


@router.get("/{item_id}/file")
async def download_file(item_id: str, _: dict = Depends(require_menu("zalo_inbox"))):
    item = get_message(item_id)
    if not item or not item["saved_file"]:
        raise HTTPException(status_code=404)
    return FileResponse(INBOX_DIR / item["saved_file"], filename=item["attachment_name"] or "zalo-file")


@router.get("/qr")
async def login_qr(_: dict = Depends(require_menu("zalo_inbox"))):
    path = INBOX_DIR / "qr.png"
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/{item_id}/reviewed")
async def review_message(item_id: str, _: dict = Depends(require_menu("zalo_inbox"))):
    if not mark_reviewed(item_id):
        raise HTTPException(status_code=404)
    return RedirectResponse("/admin/zalo_inbox", status_code=303)


@router.post("/{item_id}/analyze")
async def analyze_file(item_id: str, _: dict = Depends(require_menu("zalo_inbox"))):
    item = get_message(item_id)
    if not item or not item["saved_file"]:
        raise HTTPException(status_code=404)
    suffix = Path(item["attachment_name"]).suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=415, detail="Chỉ đọc được ảnh hoặc PDF")
    content = (INBOX_DIR / item["saved_file"]).read_bytes()
    result = await asyncio.to_thread(
        extract_trip_candidates_from_document, content, item["attachment_name"],
        "application/pdf" if suffix == ".pdf" else "image/" + suffix.lstrip("."),
    )
    save_extracted_lines(item_id, result["lines"])
    return RedirectResponse("/admin/zalo_inbox", status_code=303)
