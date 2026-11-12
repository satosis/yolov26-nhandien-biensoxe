"""Admin pages for the company Facebook Page: drafts awaiting approval, compose, Page posts, auto-posting."""

import io
import logging
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import Depends, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from PIL import Image

from backend.services import facebook_autopost as autopost
from backend.services import facebook_inbox, facebook_page, facebook_radar
from frontend.deps import get_menu_context, require_menu, templates
from frontend.pagination import paginate
from frontend.redirects import redirect_with_query

logger = logging.getLogger(__name__)
BASE_URL = "/admin/advertising/facebook"
PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
PICTURE_FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}
# Bộ lọc danh sách bài: (khóa, nhãn, các trạng thái; None = tất cả).
DRAFT_FILTERS = (
    ("todo", "Cần xử lý", ("pending", "sending", "failed")),
    ("scheduled", "Đã hẹn giờ", ("scheduled",)),
    ("published", "Đã đăng", ("published",)),
    ("rejected", "Đã bỏ", ("rejected",)),
    ("all", "Tất cả", None),
)
DRAFT_LIST_LIMIT = 500
DRAFT_PER_PAGE = 20


def _back(tab: str, msg: str = "", error: str = "") -> RedirectResponse:
    return redirect_with_query(BASE_URL, tab=tab, msg=msg, error=error)


def _parse_time(value: str) -> datetime | None:
    """`<input type="datetime-local">` value in server local time."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M").astimezone() if value.strip() else None


def _is_picture(data: bytes) -> bool:
    """True only for a complete jpg/png/gif/webp picture, whatever the file name says."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            return img.format in PICTURE_FORMATS
    except Exception:
        return False


async def _save_photos(files: list[UploadFile]) -> tuple[list[str], list[str]]:
    """Save the uploaded pictures. Returns (saved names, upload names that are not pictures);
    when any upload is not a picture, nothing is saved."""
    picked = []
    for upload in files:
        suffix = Path(upload.filename or "").suffix.lower()
        if upload.filename and suffix in PHOTO_TYPES:
            picked.append((upload.filename, suffix, await upload.read()))
    not_pictures = [filename for filename, _, data in picked if not _is_picture(data)]
    if not_pictures:
        return [], not_pictures
    autopost.PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    names = []
    for _, suffix, data in picked:
        name = f"{uuid.uuid4().hex}{suffix}"
        (autopost.PHOTO_DIR / name).write_bytes(data)
        names.append(name)
    return names, []


def _not_pictures_error(filenames: list[str]) -> str:
    return f"Không lưu: tệp không đúng định dạng ảnh (jpg, png, gif, webp): {', '.join(filenames)}."


async def _pool_photo_ids(request: Request) -> list[int]:
    """Ids of the pool photos a form picked (its repeated pool_photo_ids inputs)."""
    ids = []
    for value in (await request.form()).getlist("pool_photo_ids"):
        try:
            ids.append(int(value))
        except (ValueError, TypeError):
            pass
    return ids


async def _collect_photos(request: Request, uploads: list[UploadFile]) -> tuple[list[int], list[str], list[str]]:
    """The photos a form picked: (pool photo ids, filenames to attach = pool photos + saved uploads,
    upload names that are not pictures)."""
    pool_ids = await _pool_photo_ids(request)
    pool_names = await run_in_threadpool(autopost.resolve_pool_photos, pool_ids)
    names, not_pictures = await _save_photos(uploads)
    return pool_ids, pool_names + names, not_pictures


def _post_time(post: dict) -> str:
    value = post.get("created_time") or post.get("scheduled_publish_time")
    if isinstance(value, int):
        return datetime.fromtimestamp(value).strftime("%d/%m/%Y %H:%M")
    return datetime.fromisoformat(value.replace("+0000", "+00:00")).astimezone().strftime("%d/%m/%Y %H:%M")


async def facebook_page_view(
    request: Request,
    tab: str,
    status: str,
    q: str,
    grp: str,
    page: int,
    user: dict,
):
    ctx = get_menu_context(request, user)
    ctx.update(current_menu="facebook_page", page_title="Facebook Page", tab=tab,
               msg=request.query_params.get("msg"), error=request.query_params.get("error"))
    ctx["drafts"] = await run_in_threadpool(autopost.list_drafts, DRAFT_LIST_LIMIT)
    counts = {key: sum(1 for d in ctx["drafts"] if states is None or d["status"] in states) for key, _, states in DRAFT_FILTERS}
    view = status if status in counts else ("todo" if counts["todo"] else "all")
    wanted = next(states for key, _, states in DRAFT_FILTERS if key == view)
    ctx["draft_filters"] = [(key, label, counts[key]) for key, label, _ in DRAFT_FILTERS]
    ctx["draft_view"] = view
    needle = q.strip().lower()
    matched = [d for d in ctx["drafts"] if (wanted is None or d["status"] in wanted)
               and (not needle or needle in f'{d["message"] or ""} {d["topic"] or ""}'.lower())]
    ctx["q"] = q.strip()
    ctx["pagination"] = paginate(matched, page=page, per_page=DRAFT_PER_PAGE, base_url=BASE_URL,
                                 extra_params={"tab": "drafts", "status": view, "q": q.strip()})
    ctx["visible_drafts"] = ctx["pagination"].items
    now = datetime.now().astimezone()
    for d in ctx["visible_drafts"]:
        d["can_edit"] = d["status"] in ("pending", "failed")
        d["can_delete"] = autopost.is_deletable(d, now)
    # Dữ liệu cho cửa sổ Sửa/Xem (một cửa sổ dùng chung, điền bằng JavaScript).
    ctx["draft_json"] = [{
        "id": d["id"], "message": d["message"] or "", "link": d["link"] or "",
        "publish_at": d["publish_at"].astimezone().strftime("%Y-%m-%dT%H:%M") if d["publish_at"] else "",
        "photos": list(d["photos"] or []), "status": autopost.STATUSES.get(d["status"], d["status"]),
        "error": d["error"] or "", "can_edit": d["can_edit"],
    } for d in ctx["visible_drafts"]]
    ctx["settings"] = await run_in_threadpool(autopost.get_settings)
    ctx["photos"] = await run_in_threadpool(autopost.list_photos)
    ctx["photo_groups"] = autopost.PHOTO_GROUPS
    ctx["next_slot"] = autopost.next_slot(datetime.now().astimezone(), ctx["settings"])
    ctx["peak_times"] = autopost.PEAK_TIMES
    ctx["statuses"] = autopost.STATUSES
    ctx["weekdays"] = autopost.WEEKDAYS
    ctx["now"] = datetime.now().astimezone()
    ctx["health"] = facebook_page.read_cached_health()
    ctx["reply_enabled"] = await run_in_threadpool(facebook_inbox.is_enabled)
    if tab == "radar":
        ctx["radar_groups"] = await run_in_threadpool(facebook_radar.search_groups)
        ctx["radar_keywords"] = "\n".join(await run_in_threadpool(facebook_radar.get_keywords))
        ctx["radar_provinces"] = "\n".join(await run_in_threadpool(facebook_radar.get_provinces))
    ctx["page"], ctx["posts"], ctx["scheduled"], ctx["graph_error"] = None, [], [], ""
    try:
        ctx["page"] = await run_in_threadpool(facebook_page.page_info)
        if tab == "posts":
            ctx["posts"] = await run_in_threadpool(facebook_page.list_posts, 25)
            ctx["scheduled"] = await run_in_threadpool(facebook_page.list_scheduled)
            for post in ctx["posts"] + ctx["scheduled"]:
                post["time_label"] = _post_time(post)
    except Exception as error:
        ctx["graph_error"] = getattr(error, "user_message", None) or str(error)
    if tab == "posts":
        items = [
            {"id": p["id"], "kind": kind, "message": p.get("message") or "", "time_label": p["time_label"],
             "picture": p.get("full_picture") or "", "url": p.get("permalink_url") or ""}
            for kind, source in (("scheduled", ctx["scheduled"]), ("published", ctx["posts"])) for p in source
        ]
        counts = {"all": len(items), "published": sum(i["kind"] == "published" for i in items),
                  "scheduled": sum(i["kind"] == "scheduled" for i in items)}
        view = status if status in counts else "all"
        needle = q.strip().lower()
        matched = [i for i in items if view in ("all", i["kind"]) and (not needle or needle in i["message"].lower())]
        ctx["q"] = q.strip()
        ctx["pagination"] = paginate(matched, page=page, per_page=DRAFT_PER_PAGE, base_url=BASE_URL,
                                     extra_params={"tab": "posts", "status": view, "q": q.strip()})
        ctx["post_items"] = ctx["pagination"].items
        ctx["post_view"] = view
        ctx["post_filters"] = [("all", "Tất cả", counts["all"]), ("published", "Đã đăng", counts["published"]),
                               ("scheduled", "Đã hẹn giờ", counts["scheduled"])]
        # Nội dung các bài đã đăng của trang hiện tại, cho cửa sổ Sửa (điền bằng JavaScript).
        ctx["post_json"] = {i["id"]: i["message"] for i in ctx["post_items"] if i["kind"] == "published"}
    if tab == "photos":
        group = grp if grp in autopost.PHOTO_GROUPS or grp == "none" else ""
        needle = q.strip().lower()
        matched = [p for p in ctx["photos"]
                   if (not group or (not p.get("grp") if group == "none" else p.get("grp") == group))
                   and (not needle or needle in (p.get("caption") or "").lower())]
        ctx["q"], ctx["photo_group_view"] = q.strip(), group
        ctx["pagination"] = paginate(matched, page=page, per_page=DRAFT_PER_PAGE, base_url=BASE_URL,
                                     extra_params={"tab": "photos", "grp": group, "q": q.strip()})
        ctx["photo_items"] = ctx["pagination"].items
    return templates.TemplateResponse("pages/advertising/facebook.html", {"request": request, **ctx})


async def facebook_run_health_check(user: dict):
    result = await run_in_threadpool(facebook_page.health_check)
    level = result.get("level", "ok")
    msg = result.get("message", "")
    if level == "bad":
        return _back("auto", error=f"Kiểm tra kết nối Facebook: {msg}")
    return _back("auto", msg=f"Kiểm tra kết nối Facebook: {msg or 'Kết nối ổn định'}")


async def create_draft(
    request: Request,
    message: str = Form(""),
    link: str = Form(""),
    publish_at: str = Form(""),
    action: str = Form("save"),
    back: str = Form("compose"),
    photos: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_menu("facebook_page")),
):
    back = back if back in ("compose", "drafts") else "compose"  # nơi hiện lỗi: tab Soạn bài hoặc danh sách
    try:
        when = _parse_time(publish_at)
    except ValueError:
        return _back(back, error="Thời gian đăng không hợp lệ.")
    pool_ids, all_photos, not_pictures = await _collect_photos(request, photos)
    if not_pictures:
        return _back(back, error=_not_pictures_error(not_pictures))
    if not message.strip() and not all_photos:
        return _back(back, error="Bài cần có nội dung hoặc ảnh.")
    draft_id = await run_in_threadpool(autopost.create_draft, message, link, all_photos, when)
    # Mark selected pool photos as used by this draft
    if pool_ids:
        await run_in_threadpool(autopost.mark_pool_photos_used, pool_ids, draft_id)
    if action != "publish":
        return _back("drafts", msg="Đã lưu bài vào danh sách chờ duyệt.")
    return await _approve(draft_id, user)


async def suggest_post(request: Request, topic: str = Form(""), photos: list[UploadFile] = File(default=[]),
                       user: dict = Depends(require_menu("facebook_page"))):
    """Fill the compose form with AI text only; the schedule time stays whatever the user picked (empty = post now).
    The AI looks at the first photo picked, from the pool or else the first upload; nothing is saved."""
    pool_names = await run_in_threadpool(autopost.resolve_pool_photos, await _pool_photo_ids(request))
    with tempfile.TemporaryDirectory() as tmpdir:
        photo = autopost.PHOTO_DIR / pool_names[0] if pool_names else None
        if photo is None:
            for upload in photos:
                suffix = Path(upload.filename or "").suffix.lower()
                if upload.filename and suffix in PHOTO_TYPES:
                    data = await upload.read()
                    if not _is_picture(data):
                        return JSONResponse({"ok": False, "message": _not_pictures_error([upload.filename])}, status_code=400)
                    photo = Path(tmpdir) / f"anh{suffix}"
                    photo.write_bytes(data)
                    break
        result = await run_in_threadpool(autopost.suggest_post, topic.strip(), photo)
    return {"ok": True, **result}


async def generate_draft(request: Request, topic: str = Form(""), publish_at: str = Form(""),
                         photos: list[UploadFile] = File(default=[]),
                         user: dict = Depends(require_menu("facebook_page"))):
    try:
        when = _parse_time(publish_at)
    except ValueError:
        return _back("drafts", error="Thời gian đăng không hợp lệ.")
    pool_ids, all_photos, not_pictures = await _collect_photos(request, photos)
    if not_pictures:
        return _back("drafts", error=_not_pictures_error(not_pictures))
    draft_id = await run_in_threadpool(autopost.generate_draft, when, topic.strip(), all_photos)
    if pool_ids and draft_id:
        await run_in_threadpool(autopost.mark_pool_photos_used, pool_ids, draft_id)
    return _back("drafts", msg="AI đã viết xong một bài.")


async def update_draft(
    draft_id: int,
    message: str = Form(""),
    link: str = Form(""),
    publish_at: str = Form(""),
    remove_photos: list[str] = Form(default=[]),
    photos: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_menu("facebook_page")),
):
    draft = await run_in_threadpool(autopost.get_draft, draft_id)
    if not draft:
        return _back("drafts", error="Không tìm thấy bài.")
    try:
        when = _parse_time(publish_at)
    except ValueError:
        return _back("drafts", error="Thời gian đăng không hợp lệ.")
    kept = [p for p in draft["photos"] if p not in remove_photos]
    names, not_pictures = await _save_photos(photos)
    if not_pictures:
        return _back("drafts", error=_not_pictures_error(not_pictures))
    await run_in_threadpool(autopost.update_draft, draft_id, message, link, when, kept + names)
    return _back("drafts", msg="Đã lưu bài.")


async def _approve(draft_id: int, user: dict) -> RedirectResponse:
    result = await run_in_threadpool(autopost.approve, draft_id, user.get("username", ""))
    done = "đã hẹn giờ trên Facebook" if result["status"] == "scheduled" else "đã đăng lên Page"
    return _back("drafts", msg=f"Bài {done}.")


async def approve_draft(draft_id: int, user: dict):
    return await _approve(draft_id, user)


async def reject_draft(draft_id: int, user: dict):
    await run_in_threadpool(autopost.reject, draft_id)
    return _back("drafts", msg="Đã bỏ bài.")


async def delete_draft(draft_id: int, user: dict = Depends(require_menu("facebook_page"))):
    deleted = await run_in_threadpool(autopost.delete_draft, draft_id)
    if not deleted:
        return _back("drafts", error="Không xóa được bài: bài đã lên Facebook hoặc đang giữ lịch đăng.")
    return _back("drafts", msg="Đã xóa bài.")


async def edit_post(post_id: str, message: str = Form(""), user: dict = Depends(require_menu("facebook_page"))):
    await run_in_threadpool(facebook_page.edit_post, post_id, message)
    return _back("posts", msg="Đã sửa nội dung bài.")


async def delete_post(post_id: str, user: dict = Depends(require_menu("facebook_page"))):
    await run_in_threadpool(facebook_page.delete_post, post_id)
    logger.info("Facebook post %s deleted by %s", post_id, user.get("username"))
    return _back("posts", msg="Đã xóa bài khỏi Page.")


async def upload_photos(photos: list[UploadFile], grp: str, caption: str, user: dict):
    names, not_pictures = await _save_photos(photos)
    if not_pictures:
        return _back("photos", error=_not_pictures_error(not_pictures))
    if not names:
        return _back("photos", error="Chưa chọn ảnh (jpg, png, gif, webp).")
    for name in names:
        await run_in_threadpool(autopost.add_photo, name, grp, caption)
    return _back("photos", msg=f"Đã thêm {len(names)} ảnh vào kho.")


async def delete_photo(photo_id: int, user: dict = Depends(require_menu("facebook_page"))):
    ok = await run_in_threadpool(autopost.delete_photo, photo_id)
    if not ok:
        return _back("photos", error="Không tìm thấy ảnh.")
    return _back("photos", msg="Đã xóa ảnh khỏi kho.")


async def radar_settings(keywords: str, provinces: str, user: dict):
    await run_in_threadpool(facebook_radar.save_settings, keywords, provinces)
    return _back("radar", msg="Đã lưu.")


async def radar_draft(post: str, user: dict):
    """Comment and message drafted for a customer's post that a person pasted. Nothing is posted."""
    if not post.strip():
        return JSONResponse({"ok": False, "message": "Chưa có nội dung bài."}, status_code=422)
    return {"ok": True, **await run_in_threadpool(facebook_radar.draft_reply, post)}


async def radar_lead(name: str, phone: str, post: str, user: dict):
    if not post.strip():
        return JSONResponse({"ok": False, "message": "Chưa có nội dung bài."}, status_code=422)
    lead_id = await run_in_threadpool(facebook_radar.save_lead, name, phone, post)
    if lead_id is None:
        return JSONResponse({"ok": False, "message": "Không lưu được khách."}, status_code=502)
    return {"ok": True, "message": "Đã lưu vào Yêu cầu tư vấn."}


async def save_settings(
    enabled: str = Form(""),
    topics: str = Form(""),
    times: str = Form(", ".join(autopost.DEFAULT_TIMES)),
    days: list[int] = Form(default=[]),
    notes: str = Form(""),
    mode: str = Form("approve"),
    reply_enabled: str = Form(""),
    user: dict = Depends(require_menu("facebook_page")),
):
    try:
        await run_in_threadpool(autopost.save_settings, enabled == "1", topics, times, days, notes, mode)
        await run_in_threadpool(facebook_inbox.set_enabled, reply_enabled == "1")
    except ValueError:
        return _back("auto", error="Giờ đăng không hợp lệ.")
    return _back("auto", msg="Đã lưu cài đặt tự động đăng bài.")
