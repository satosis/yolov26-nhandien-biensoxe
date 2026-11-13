"""AI-written Facebook Page posts on a schedule: approval mode and auto-publish mode, with a photo pool.

The backend scheduler writes drafts for posting slots. In 'approve' mode, drafts wait in admin for human approval.
In 'auto' mode, when within 2 hours of slot, drafts are created and published automatically without approval.
The photo pool is shared: a photo can be used by any number of posts. When it has photos, the AI inspects the one
used least recently (never-used first) and writes a post about it.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from backend.services import config as cfg
from backend.services import facebook_page
from backend.services.database import db
from backend.services.log_once import log_once, log_resolved
from backend.services.site_url import configured_site_url
from backend.services.periodic import start_periodic
from backend.services.settings_service import get_app_setting, set_app_setting

logger = logging.getLogger(__name__)

PHOTO_DIR = Path(cfg.FB_POST_DIR)
PHOTO_GROUPS = {"van_chuyen": "Vận chuyển", "qua_can": "Quả cân"}
AUTO_LEAD = timedelta(hours=2)
# Giờ cao điểm của người dùng Facebook ở Việt Nam: buổi sáng đi làm, nghỉ trưa, buổi tối.
PEAK_TIMES = [("07:30", "Sáng"), ("11:30", "Trưa"), ("20:00", "Tối")]
DEFAULT_TIMES = ["11:30", "20:00"]
DEFAULT_DAYS = [0, 2, 4]  # Thứ 2, 4, 6
WEEKDAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
STATUSES = {
    "pending": "Chờ duyệt",
    "sending": "Đang gửi lên Facebook",
    "scheduled": "Đã hẹn giờ trên Facebook",
    "published": "Đã đăng",
    "rejected": "Đã bỏ",
    "failed": "Lỗi",
}
CLAUDE_TIMEOUT_SECS = 300


# ---- settings -------------------------------------------------------------------------------

def parse_times(raw: str) -> list[str]:
    """Posting times from text like '11:30, 20:00': normalised to HH:MM, sorted, no duplicates. ValueError on a bad time."""
    found = set()
    for part in re.split(r"[,;\s]+", raw or ""):
        if part:
            parsed = datetime.strptime(part, "%H:%M")
            found.add(f"{parsed.hour:02d}:{parsed.minute:02d}")
    return sorted(found)


def get_settings() -> dict:
    try:
        days = [int(d) for d in get_app_setting("FB_AUTO_DAYS").split(",") if d.strip()]
    except ValueError:
        days = []
    try:
        times = parse_times(get_app_setting("FB_AUTO_TIME"))
    except ValueError:
        times = []
    return {
        "enabled": get_app_setting("FB_AUTO_ENABLED") == "1",
        "topics": [t.strip() for t in get_app_setting("FB_AUTO_TOPICS").splitlines() if t.strip()],
        "times": times or DEFAULT_TIMES,
        "days": days if get_app_setting("FB_AUTO_DAYS") else DEFAULT_DAYS,
        "notes": get_app_setting("FB_AUTO_NOTES"),
        "mode": "auto" if get_app_setting("FB_AUTO_MODE") == "auto" else "approve",
    }


def save_settings(enabled: bool, topics: str, times: str, days: list[int], notes: str, mode: str = "approve") -> None:
    post_times = parse_times(times)  # raises ValueError on a bad time
    if not post_times:
        raise ValueError("Chưa có giờ đăng.")
    set_app_setting("FB_AUTO_ENABLED", "1" if enabled else "0")
    set_app_setting("FB_AUTO_TOPICS", "\n".join(t.strip() for t in topics.splitlines() if t.strip()))
    set_app_setting("FB_AUTO_TIME", ",".join(post_times))
    set_app_setting("FB_AUTO_DAYS", ",".join(str(d) for d in sorted(set(days)) if 0 <= d <= 6))
    set_app_setting("FB_AUTO_NOTES", notes.strip())
    set_app_setting("FB_AUTO_MODE", "auto" if mode == "auto" else "approve")


def next_slot(now: datetime, settings: dict) -> datetime | None:
    """First chosen weekday and time strictly after `now` (local time), within a week."""
    if not settings["days"]:
        return None
    for offset in range(8):
        day = now + timedelta(days=offset)
        if day.weekday() not in settings["days"]:
            continue
        for text in settings["times"]:  # sorted, so the first one after `now` is the earliest
            hour, minute = (int(x) for x in text.split(":"))
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > now:
                return candidate
    return None


# ---- drafts ---------------------------------------------------------------------------------

def _site_url() -> str:
    """Public site URL from the environment (.env: APP_URL); empty when unset."""
    return configured_site_url()


def with_utm(link: str, campaign: str, site_host: str | None = None) -> str:
    """Append Facebook UTM params to link if it points to this site."""
    if not link or not isinstance(link, str) or not link.strip():
        return link
    link = link.strip()
    try:
        parsed = urllib.parse.urlsplit(link)
    except Exception:
        return link

    if not parsed.scheme or not parsed.netloc:
        return link

    if site_host is None:
        base = _site_url()
        if base:
            try:
                site_host = urllib.parse.urlsplit(base).netloc.split(":")[0].lower()
            except Exception:
                site_host = ""
        else:
            site_host = ""

    if not site_host:
        return link

    link_host = parsed.netloc.split(":")[0].lower()
    site_host = site_host.lower()
    if not (link_host == site_host or link_host.endswith("." + site_host)):
        return link

    utm_defaults = [
        ("utm_source", "facebook"),
        ("utm_medium", "social"),
        ("utm_campaign", campaign),
    ]
    new_params = list(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    existing_keys = {k for k, _ in new_params}
    for k, v in utm_defaults:
        if k not in existing_keys:
            new_params.append((k, v))
            existing_keys.add(k)

    new_query = urllib.parse.urlencode(new_params)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def list_drafts(limit: int = 50) -> list[dict]:
    return db.query("""
        SELECT * FROM fb_post_drafts
        ORDER BY created_at DESC, id DESC
        LIMIT %s
    """, (limit,), fetchall=True)


def get_draft(draft_id: int) -> dict | None:
    return db.query("SELECT * FROM fb_post_drafts WHERE id = %s", (draft_id,), fetch=True)


def create_draft(message: str, link: str = "", photos: list[str] = (), publish_at: datetime | None = None,
                 source: str = "manual", topic: str = "", slot_at: datetime | None = None) -> int | None:
    if slot_at is not None:
        row = db.query("""
            INSERT INTO fb_post_drafts (message, link, photos, publish_at, source, topic, slot_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (slot_at) WHERE slot_at IS NOT NULL DO NOTHING
            RETURNING id
        """, (message.strip(), link.strip(), json.dumps(list(photos)), publish_at, source, topic, slot_at), fetch=True)
        return row["id"] if row else None

    return db.query("""
        INSERT INTO fb_post_drafts (message, link, photos, publish_at, source, topic)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
    """, (message.strip(), link.strip(), json.dumps(list(photos)), publish_at, source, topic), return_id=True)


def update_draft(draft_id: int, message: str, link: str, publish_at: datetime | None, photos: list[str]) -> None:
    db.query("""
        UPDATE fb_post_drafts SET message = %s, link = %s, publish_at = %s, photos = %s, updated_at = NOW()
        WHERE id = %s AND status IN ('pending', 'failed')
    """, (message.strip(), link.strip(), publish_at, json.dumps(photos), draft_id))


def reject(draft_id: int) -> None:
    db.query("UPDATE fb_post_drafts SET status = 'rejected', updated_at = NOW() "
             "WHERE id = %s AND status IN ('pending', 'failed')", (draft_id,))


def is_deletable(draft: dict, now: datetime | None = None) -> bool:
    """Bài chưa lên Facebook và không giữ chỗ một lượt đăng tự động chưa tới giờ (xóa thì lịch sẽ viết lại bài mới)."""
    now = now or datetime.now().astimezone()
    slot = draft.get("slot_at")
    return draft.get("status") in ("pending", "failed", "rejected") and (slot is None or slot <= now)


def delete_draft(draft_id: int) -> bool:
    """Xóa hẳn một bài nháp (điều kiện như is_deletable, kiểm tra lại ngay trong câu lệnh); trả ảnh về kho ảnh."""
    row = db.query("""
        DELETE FROM fb_post_drafts
        WHERE id = %s AND status IN ('pending', 'failed', 'rejected') AND (slot_at IS NULL OR slot_at <= NOW())
        RETURNING id
    """, (draft_id,), fetch=True)
    if not row:
        return False
    db.query("UPDATE public.fb_photo_pool SET used_draft_id = NULL, used_at = NULL WHERE used_draft_id = %s", (draft_id,))
    return True


def recover_stuck_sending(now: datetime | None = None, max_minutes: int = 15) -> int:
    """Reset drafts stuck in 'sending' state for longer than max_minutes to 'failed'."""
    now = now or datetime.now().astimezone()
    cutoff = now - timedelta(minutes=max_minutes)
    msg = "Tiến trình bị gián đoạn khi gửi — kiểm tra Page rồi duyệt lại nếu bài chưa lên."
    res = db.query("""
        UPDATE public.fb_post_drafts
        SET status = 'failed', error = %s, sending_since = NULL, updated_at = NOW()
        WHERE status = 'sending' AND sending_since IS NOT NULL AND sending_since <= %s
        RETURNING id
    """, (msg, cutoff), fetchall=True)
    count = len(res) if res else 0
    if count:
        logger.warning("Đã thu hồi %s bản nháp bị kẹt ở trạng thái 'sending'", count)
    return count


def approve(draft_id: int, approved_by: str = "", now: datetime | None = None) -> dict:
    """Send an approved draft to Facebook: scheduled if its time is still ahead, otherwise now."""
    draft = get_draft(draft_id)
    if not draft or draft["status"] not in ("pending", "failed"):
        raise ValueError("Bài không còn ở trạng thái chờ duyệt.")
    if not draft["message"].strip() and not draft["photos"]:
        raise ValueError("Bài chưa có nội dung.")

    now = now or datetime.now().astimezone()
    publish_at = draft.get("publish_at")
    if publish_at and (publish_at - now).total_seconds() > facebook_page.MAX_SCHEDULE_SECONDS:
        raise ValueError("Facebook chỉ hẹn giờ tối đa 30 ngày; chọn thời gian gần hơn.")

    claimed = db.query("""
        UPDATE public.fb_post_drafts
        SET status = 'sending', sending_since = NOW(), updated_at = NOW()
        WHERE id = %s AND status IN ('pending', 'failed')
        RETURNING *
    """, (draft_id,), fetch=True)
    if not claimed:
        raise ValueError("Bài không còn ở trạng thái chờ duyệt (có thể đang được gửi).")
    draft = {**draft, **claimed}  # send exactly what was in the row when it was claimed

    tagged_link = with_utm(draft.get("link", ""), f"fb_post_{draft_id}")
    schedule = publish_at if publish_at and (publish_at - now).total_seconds() > facebook_page.MIN_SCHEDULE_SECONDS else None

    try:
        post_id = facebook_page.create_post(
            draft["message"],
            [PHOTO_DIR / p for p in draft["photos"]],
            tagged_link,
            schedule,
        )
    except Exception as error:
        user_msg = getattr(error, "user_message", None) or str(error)
        raw_msg = str(error)
        if user_msg and raw_msg and user_msg != raw_msg:
            err_text = f"{user_msg} — {raw_msg}"
        else:
            err_text = user_msg or raw_msg
        token = get_app_setting('FB_PAGE_TOKEN').strip()
        app_secret = get_app_setting('FB_APP_SECRET').strip()
        err_text = facebook_page._redact(err_text, token, app_secret)[:1000]
        db.query("""
            UPDATE public.fb_post_drafts
            SET status = 'failed', error = %s, sending_since = NULL, updated_at = NOW()
            WHERE id = %s
        """, (err_text, draft_id))
        raise

    status = "scheduled" if schedule else "published"
    sql_success = """
        UPDATE public.fb_post_drafts
        SET status = %s, fb_post_id = %s, error = NULL, approved_by = %s,
            publish_at = COALESCE(%s, NOW()), sending_since = NULL, updated_at = NOW()
        WHERE id = %s AND status = 'sending'
    """
    try:
        db.query(sql_success, (status, post_id, approved_by, schedule, draft_id))
    except Exception:
        try:
            db.query(sql_success, (status, post_id, approved_by, schedule, draft_id))
        except Exception:
            logger.critical("Không thể cập nhật DB sau khi đăng Facebook thành công, post_id=%s, draft_id=%s",
                            post_id, draft_id, exc_info=True)
            raise

    return {"status": status, "post_id": post_id}


# ---- photo pool -----------------------------------------------------------------------------

def add_photo(filename: str, grp: str, caption: str) -> int:
    valid_grp = grp if grp in PHOTO_GROUPS else ""
    valid_caption = (caption or "").strip()[:500]
    return db.query(
        "INSERT INTO public.fb_photo_pool (filename, grp, caption) VALUES (%s, %s, %s) RETURNING id",
        (filename.strip(), valid_grp, valid_caption),
        return_id=True,
    )


def list_photos(limit: int = 60) -> list[dict]:
    rows = db.query("""
        SELECT * FROM public.fb_photo_pool
        ORDER BY created_at DESC, id DESC
        LIMIT %s
    """, (limit,), fetchall=True)
    return rows or []


def delete_photo(photo_id: int) -> bool:
    """Remove a photo from the shared pool. Its file stays while any post draft still shows it."""
    row = db.query("DELETE FROM public.fb_photo_pool WHERE id = %s RETURNING filename", (photo_id,), fetch=True)
    if not row:
        return False
    in_use = db.query("SELECT 1 FROM public.fb_post_drafts WHERE photos @> %s::jsonb LIMIT 1",
                      (json.dumps([row["filename"]]),), fetch=True)
    if not in_use:
        (PHOTO_DIR / row["filename"]).unlink(missing_ok=True)
    return True


def _next_photo() -> dict | None:
    """The pool photo used least recently; photos never used come first, oldest upload first."""
    rows = db.query("""
        SELECT * FROM public.fb_photo_pool
        ORDER BY used_at ASC NULLS FIRST, created_at ASC, id ASC
    """, fetchall=True)
    for r in (rows or []):
        if (PHOTO_DIR / r["filename"]).is_file():
            return r
    return None


def _mark_photo_used(photo_id: int, draft_id: int) -> None:
    db.query(
        "UPDATE public.fb_photo_pool SET used_draft_id = %s, used_at = NOW() WHERE id = %s",
        (draft_id, photo_id),
    )


def resolve_pool_photos(photo_ids: list[int]) -> list[str]:
    """Filenames of the chosen pool photos that still exist on disk; any photo can be picked again."""
    if not photo_ids:
        return []
    names = []
    for pid in photo_ids:
        row = db.query("SELECT filename FROM public.fb_photo_pool WHERE id = %s", (pid,), fetch=True)
        if row and (PHOTO_DIR / row["filename"]).is_file():
            names.append(row["filename"])  # the draft references the pool file itself, no copy
    return names


def mark_pool_photos_used(photo_ids: list[int], draft_id: int) -> None:
    """Stamp pool photos as last used by the given draft (the auto-poster rotates by it); they stay available."""
    for pid in photo_ids:
        _mark_photo_used(pid, draft_id)


# ---- AI writing -----------------------------------------------------------------------------

def _build_prompt(topic: str, notes: str, recent: list[str], photo_name: str = "", caption: str = "") -> str:
    lines = [
        "Viết MỘT bài đăng Facebook Page bằng tiếng Việt cho doanh nghiệp.",
        f"Chủ đề: {topic}" if topic else
        "Chủ đề: tự chọn một chủ đề cụ thể, hữu ích cho khách hàng, dựa trên thông tin doanh nghiệp bên dưới và khác các bài gần đây.",
    ]
    if photo_name:
        lines.append(f"Ảnh thực tế đính kèm: ./{photo_name} — dùng công cụ Read để xem ảnh trước khi viết; chỉ mô tả những gì thực sự thấy trong ảnh.")
    if caption and caption.strip():
        lines.append(f"Ghi chú của chủ ảnh: {caption.strip()}")
    if notes:
        lines += ["", "Thông tin doanh nghiệp và văn phong cần theo:", notes]
    if recent:
        lines += ["", "Các bài gần đây (tránh lặp lại ý và câu mở đầu):", *[f"- {r[:300]}" for r in recent]]
    lines += [
        "",
        "Yêu cầu: bài ngắn 50–90 từ, giọng người làm nghề kể chuyện, không viết như quảng cáo. Mở đầu bằng một tình huống thật khách hay gặp, "
        "kết bài bằng MỘT câu hỏi ngắn dễ trả lời để người đọc bình luận, thêm một dòng số Zalo/hotline nếu có trong thông tin trên, "
        "vài emoji phù hợp, 2–3 hashtag cuối bài.",
        "Không dùng Markdown (không **, không #tiêu đề). Không bịa giá, số liệu hay cam kết không có trong thông tin trên.",
        "Chỉ trả về nội dung bài đăng, không giải thích gì thêm." if topic else
        "Dòng đầu tiên ghi đúng dạng 'CHỦ ĐỀ: <chủ đề đã chọn>', rồi một dòng trống, rồi nội dung bài đăng; không giải thích gì thêm.",
    ]
    return "\n".join(lines)


def write_with_ai(topic: str, notes: str, recent: list[str], photo: Path | None = None, caption: str = "") -> str:
    claude = shutil.which(os.environ.get("CLAUDE_BIN", "claude"))
    if not claude:
        raise RuntimeError("Không tìm thấy Claude Code CLI (lệnh `claude`) trên máy chủ.")
    if photo is not None:
        photo_path = Path(photo)
        suffix = photo_path.suffix.lower()
        photo_name = f"anh{suffix}"
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_photo = Path(tmpdir) / photo_name
            shutil.copy2(photo_path, tmp_photo)
            prompt = _build_prompt(topic, notes, recent, photo_name=photo_name, caption=caption)
            result = subprocess.run(
                [claude, "-p", "--output-format", "json", "--allowedTools", "Read"],
                input=prompt,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=CLAUDE_TIMEOUT_SECS, cwd=tmpdir,
            )
    else:
        prompt = _build_prompt(topic, notes, recent, photo_name="", caption=caption)
        result = subprocess.run(
            [claude, "-p", "--output-format", "json"],
            input=prompt,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=CLAUDE_TIMEOUT_SECS, cwd=tempfile.gettempdir(),
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude không trả kết quả: {(result.stderr or result.stdout).strip()[-300:]}")
    text = str(data.get("result") or "").strip()
    if result.returncode != 0 or data.get("is_error") or not text:
        raise RuntimeError(f"Claude báo lỗi: {text[-300:] or result.stderr.strip()[-300:]}")
    return text


def _recent_messages() -> list[str]:
    rows = db.query("SELECT message FROM fb_post_drafts WHERE status <> 'rejected' AND message <> '' "
                    "ORDER BY created_at DESC LIMIT 5", fetchall=True)
    return [r["message"] for r in rows] if rows else []


def _business_notes(settings: dict) -> str:
    """The configured notes, or while they are empty, the company facts from its public service pages."""
    if settings["notes"]:
        return settings["notes"]
    from frontend.container.config import get_container_settings
    from frontend.solar.config import get_solar_settings
    from frontend.crane.config import get_crane_settings
    from frontend.weights.config import get_test_weights_settings
    crane, weights, container = get_crane_settings(), get_test_weights_settings(), get_container_settings()
    solar = get_solar_settings()
    return "\n".join([
        crane["seo_config"]["description"],
        weights["seo_config"]["description"],
        "Đội xe: " + "; ".join(f"{f['name']} ({f['model']})" for f in crane["fleet_items"]),
        "Quả cân và kiểm định: " + "; ".join(w["name"] for w in weights["weight_items"]),
        container["seo_config"]["description"],
        "Container cho thuê: " + "; ".join(c["name"] for c in container["catalog_items"]),
        solar["seo_config"]["description"],
        "Thiết bị điện mặt trời: " + "; ".join(s["name"] for s in solar["catalog_items"]),
        f"Bãi xe: {crane['depot_address']}. Hotline/Zalo: {crane['phone']}.",
    ])


def _write_own_topic(notes: str) -> tuple[str, str]:
    """No topic given or configured: the AI picks one and states it on the first line. Returns (topic, message)."""
    text = write_with_ai("", notes, _recent_messages())
    first, _, rest = text.partition("\n")
    label, sep, topic = first.partition(":")
    if sep and label.strip(" *#").upper() == "CHỦ ĐỀ" and rest.strip():
        return topic.strip(" *") or "AI tự chọn", rest.strip()
    return "AI tự chọn", text


def _write_about_photo(topic: str, notes: str, photo: Path) -> tuple[str, str]:
    """(topic, message) of a post the AI writes about one photo. A pool photo brings its group and caption;
    without `topic` the post is named after the group."""
    pooled = db.query("SELECT grp, caption FROM public.fb_photo_pool WHERE filename = %s", (photo.name,), fetch=True) or {}
    topic = topic or f"{PHOTO_GROUPS.get(pooled.get('grp') or '', 'Hoạt động của công ty')} — ảnh thực tế"
    return topic, write_with_ai(topic, notes, _recent_messages(), photo=photo, caption=pooled.get("caption") or "")


def suggest_post(topic: str = "", photo: Path | None = None) -> dict:
    """AI text for the compose form, not saved anywhere. With a `photo` the AI writes about it. Without `topic`,
    take the next configured topic, or let the AI choose one when none is configured."""
    settings = get_settings()
    notes = _business_notes(settings)
    if photo is not None:
        topic, message = _write_about_photo(topic, notes, photo)
        return {"topic": topic, "message": message}
    if not topic:
        if not settings["topics"]:
            topic, message = _write_own_topic(notes)
            return {"topic": topic, "message": message}
        used_row = db.query("SELECT COUNT(*) AS n FROM fb_post_drafts WHERE source = 'ai'", fetch=True)
        topic = settings["topics"][(used_row["n"] if used_row else 0) % len(settings["topics"])]
    return {"topic": topic, "message": write_with_ai(topic, notes, _recent_messages())}


def generate_draft(publish_at: datetime | None = None, topic: str = "", photos: list[str] = (),
                   _quiet: bool = False) -> int | None:
    """Have the AI write a pending draft. With chosen `photos` it writes about the first one, like a normal post.
    Otherwise, without `topic`, it inspects the photo pool first, else rotates through configured topics."""
    settings = get_settings()
    notes = _business_notes(settings)
    photo = None
    photos = list(photos)
    if photos:
        topic, message = _write_about_photo(topic, notes, PHOTO_DIR / photos[0])
    elif not topic:
        photo = _next_photo()
        if photo:
            topic, message = _write_about_photo("", notes, PHOTO_DIR / photo["filename"])
            photos = [photo["filename"]]
        elif not settings["topics"]:
            topic, message = _write_own_topic(notes)
        else:
            used_row = db.query("SELECT COUNT(*) AS n FROM fb_post_drafts WHERE source = 'ai'", fetch=True)
            used = used_row["n"] if used_row else 0
            topic = settings["topics"][used % len(settings["topics"])]
            message = write_with_ai(topic, notes, _recent_messages())
    else:
        message = write_with_ai(topic, notes, _recent_messages())

    draft_id = create_draft(message, photos=photos, publish_at=publish_at, source="ai", topic=topic, slot_at=publish_at)
    if draft_id is not None and photo:
        _mark_photo_used(photo["id"], draft_id)
    if draft_id is None and not _quiet:
        raise ValueError("Đã có bài cho khung giờ này.")
    return draft_id


# ---- Telegram alerts & scheduler ------------------------------------------------------------

def _alert(key: str, text: str, reply_markup: dict | None = None) -> bool:
    """Send a Telegram alert once per problem: the same text for the same key is not sent again until _alert_resolved(key)."""
    sent = get_app_setting("FB_ALERT_" + key).strip()
    # Values written before this rule are timestamps: that problem was already reported.
    if sent == text or re.match(r"\d{4}-\d\d-\d\dT", sent):
        return False
    from backend.services.telegram_service import notify_telegram
    ok = notify_telegram(text, important=True, prefix="", reply_markup=reply_markup)
    if ok:
        set_app_setting("FB_ALERT_" + key, text)
    return ok


def _alert_resolved(key: str) -> None:
    if get_app_setting("FB_ALERT_" + key).strip():
        set_app_setting("FB_ALERT_" + key, "")


def _notify_new_draft(draft_id: int, slot: datetime | None) -> None:
    draft = get_draft(draft_id)
    msg_preview = draft["message"][:120] if draft and draft.get("message") else ""
    time_str = slot.strftime("%d/%m %H:%M") if slot else ""
    header = f"📝 Bài Facebook mới chờ duyệt cho {time_str}" if time_str else "📝 Bài Facebook mới chờ duyệt"
    text = f"{header}\n\n{msg_preview}".strip()

    base_url = _site_url() or "http://127.0.0.1:8001"
    safe_base = base_url.replace("://localhost", "://127.0.0.1")
    admin_link = f"{safe_base}/admin/advertising/facebook?tab=drafts"

    reply_markup = {
        "inline_keyboard": [
            [{"text": "Duyệt bài", "url": admin_link}]
        ]
    }
    from backend.services.telegram_service import notify_telegram
    notify_telegram(text, important=True, prefix="", reply_markup=reply_markup)


def _notify_auto_posted(draft_id: int, slot: datetime | None, result: dict) -> None:
    draft = get_draft(draft_id)
    msg_preview = draft["message"][:120] if draft and draft.get("message") else ""
    if result.get("status") == "scheduled":
        time_str = slot.strftime("%d/%m %H:%M") if slot else ""
        header = f"✅ Đã hẹn đăng Facebook lúc {time_str}".strip()
    else:
        header = "✅ Đã đăng Facebook"
    text = f"{header}\n\n{msg_preview}".strip() if msg_preview else header
    from backend.services.telegram_service import notify_telegram
    notify_telegram(text, important=True, prefix="")


def tick(now: datetime | None = None) -> int | None:
    """Write the draft for the next slot if it does not exist yet. Returns the new draft id."""
    now = now or datetime.now().astimezone()
    stuck = recover_stuck_sending(now=now)
    if stuck > 0:
        _alert("STUCK", f"Có {stuck} bài Facebook bị kẹt khi gửi, đã chuyển về trạng thái Lỗi.")
    else:
        _alert_resolved("STUCK")

    settings = get_settings()
    if not settings["enabled"] or (not settings["topics"] and _next_photo() is None):
        return None
    slot = next_slot(now, settings)
    if slot is None:
        return None
    if db.query("SELECT 1 FROM fb_post_drafts WHERE slot_at = %s OR (source = 'ai' AND publish_at = %s)",
                (slot, slot), fetch=True):
        return None

    mode = settings.get("mode", "approve")
    if mode == "auto":
        if slot - now > AUTO_LEAD:
            return None
        draft_id = generate_draft(publish_at=slot, _quiet=True)
        if draft_id is None:
            return None
        try:
            res = approve(draft_id, approved_by="tự động", now=now)
            _notify_auto_posted(draft_id, slot, res)
            _alert_resolved("AUTO_POST_FAILED")
            log_resolved("autopost:post")
        except Exception as exc:
            log_once(logger, logging.ERROR, "autopost:post", "Không tự đăng được bài #%s lên Facebook", draft_id, exc_info=True)
            _alert("AUTO_POST_FAILED", f"Không tự đăng được bài #{draft_id}: {exc}")
        return draft_id
    else:
        draft_id = generate_draft(publish_at=slot, _quiet=True)
        if draft_id is None:
            return None
        logger.info("Đã viết bài Facebook chờ duyệt #%s cho %s", draft_id, slot)
        _notify_new_draft(draft_id, slot)
        return draft_id


def run_once(now: datetime | None = None, last_health_at: datetime | None = None) -> dict:
    """Run one scheduler cycle: tick draft generation and periodic health check."""
    now = now or datetime.now().astimezone()
    res: dict = {"draft_id": None, "health": None, "error": None}

    # 1. Tick
    try:
        res["draft_id"] = tick(now=now)
        _alert_resolved("AI_FAILED")
        log_resolved("autopost:run_once")
    except Exception as exc:
        res["error"] = str(exc)
        log_once(logger, logging.ERROR, "autopost:run_once", "Tự động viết bài Facebook lỗi trong run_once", exc_info=True)
        slot_str = ""
        try:
            settings = get_settings()
            slot = next_slot(now, settings)
            if slot:
                slot_str = slot.strftime("%d/%m %H:%M")
        except Exception:
            pass
        msg = f"AI chưa viết được bài cho khung giờ {slot_str}" if slot_str else "AI chưa viết được bài Facebook"
        _alert("AI_FAILED", msg)

    # 2. Periodic health check
    page_id = get_app_setting("FB_PAGE_ID").strip()
    page_token = get_app_setting("FB_PAGE_TOKEN").strip()
    if page_id and page_token:
        should_check = False
        if last_health_at is None:
            cached = facebook_page.read_cached_health()
            if cached and cached.get("checked_at"):
                try:
                    last_health_at = datetime.fromisoformat(cached["checked_at"])
                except Exception:
                    last_health_at = None
        if last_health_at is None or (now - last_health_at).total_seconds() >= 6 * 3600:
            should_check = True

        if should_check:
            health = facebook_page.health_check(now=now)
            res["health"] = health
            if health.get("level") != "ok":
                _alert("HEALTH", health.get("message") or "Kết nối Facebook không ổn định.")
            else:
                _alert_resolved("HEALTH")

    return res


def start_scheduler(interval_secs: int = 300) -> threading.Event:
    return start_periodic(lambda: run_once(), name="facebook-autopost", interval_secs=interval_secs, error_wait_secs=1800, error_message="Tự động viết bài Facebook lỗi", logger=logger)  # không gọi AI dồn dập khi đang lỗi
