"""Local inbox for messages received by the personal Zalo bridge."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


INBOX_DIR = Path(__file__).resolve().parents[2] / ".runtime" / "zalo_inbox"
MAX_FILE_BYTES = 20 * 1024 * 1024


def bridge_secret() -> str:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    path = INBOX_DIR / "bridge-secret"
    try:
        with path.open("x", encoding="ascii") as output:
            output.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    return path.read_text(encoding="ascii")


def _item_id(group_id: str, message_id: str) -> str:
    return hashlib.sha256(f"{group_id}:{message_id}".encode()).hexdigest()


def _category(text: str, has_file: bool) -> str:
    if has_file:
        return "Có tệp cần xem"
    if re.search(r"\bapi\b", text, re.IGNORECASE):
        return "Yêu cầu API"
    if re.search(r"\b\d{10}(?:-\d{3})?\b", text):
        return "Tạo hóa đơn"
    if any(word in text.casefold() for word in ("hóa đơn", "chuyến", "đơn vị", "xe", "biển số")):
        return "Nghiệp vụ vận tải"
    return "Tin nhắn cần xem"


def _read_item(path: Path) -> dict:
    # Another ingest may have created the file but not finished writing yet.
    item = None
    for attempt in range(5):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            break
        except json.JSONDecodeError:
            if attempt == 4:
                raise
            time.sleep(0.02)
    if item is not None and "company_id" not in item:
        gname = item.get("group_name")
        cid = None
        cname = ""
        if gname:
            try:
                from frontend.modules.invoices.companies import find_company_by_name
                matched = find_company_by_name(gname)
                if matched:
                    cid = matched["id"]
                    cname = matched["name"]
            except Exception:
                pass
        item["company_id"] = cid
        item["company_name"] = cname
    return item


def save_message(event: dict, file_bytes: bytes | None = None, *, with_created: bool = False):
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    item_id = _item_id(str(event["group_id"]), str(event["message_id"]))
    path = INBOX_DIR / f"{item_id}.json"
    if path.exists():
        existing = _read_item(path)
        return (existing, False) if with_created else existing

    attachment = event.get("attachment") or {}
    filename = str(attachment.get("title") or ("Tệp đính kèm Zalo" if attachment else ""))
    extension = Path(filename).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
        extension = ".bin"
    saved_file = ""
    if file_bytes is not None:
        saved_file = f"{item_id}{extension}"
        (INBOX_DIR / saved_file).write_bytes(file_bytes)

    company_id = event.get("company_id")
    company_name = event.get("company_name")
    group_name = str(event.get("group_name") or "")
    if not company_id and group_name:
        try:
            from frontend.modules.invoices.companies import find_company_by_name
            matched = find_company_by_name(group_name)
            if matched:
                company_id = matched["id"]
                company_name = matched["name"]
        except Exception:
            pass

    text = str(event.get("text") or "").strip()
    if not company_id and text:
        try:
            from frontend.modules.zalo.invoice_automation import extract_matching_company
            matched_tax = extract_matching_company(text)
            if matched_tax:
                company_id = matched_tax["id"]
                company_name = matched_tax["name"]
        except Exception:
            pass

    if not company_id:
        try:
            from frontend.modules.zalo.invoice_automation import get_active_session
            active_s = get_active_session(str(event.get("group_id") or ""))
            if active_s and active_s.get("company_id"):
                company_id = active_s["company_id"]
                company_name = active_s.get("company_name", "")
        except Exception:
            pass

    # Phải match tên nhóm 100% hoặc có MST công ty khớp trong DB
    if not company_id:
        return (None, False) if with_created else None

    text = str(event.get("text") or "").strip()
    item = {
        "id": item_id,
        "group_id": str(event["group_id"]),
        "group_name": group_name,
        "company_id": company_id,
        "company_name": str(company_name or ""),
        "message_id": str(event["message_id"]),
        "sender_id": str(event.get("sender_id") or ""),
        "sender_name": str(event.get("sender_name") or ""),
        "sent_at": str(event.get("sent_at") or ""),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "text": text,
        "attachment_name": filename,
        "attachment_type": str(attachment.get("type") or ""),
        "attachment_url": str(attachment.get("href") or ""),
        "saved_file": saved_file,
        "category": _category(text, bool(attachment)),
        "extracted_lines": [],
        "reviewed": False,
    }
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump(item, output, ensure_ascii=False)
    except FileExistsError:
        existing = _read_item(path)
        return (existing, False) if with_created else existing
    return (item, True) if with_created else item


@contextmanager
def claim_salary_automation(item_id: str):
    """Let only one ingest/retry path process a given Zalo message at a time."""
    if not re.fullmatch(r"[a-f0-9]{64}", item_id):
        yield False
        return
    lock = INBOX_DIR / f"{item_id}.salary.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        try:
            if time.time() - lock.stat().st_mtime <= 120:
                yield False
                return
            lock.rmdir()  # Recover a lock left by a terminated process.
            lock.mkdir()
        except (FileNotFoundError, FileExistsError, OSError):
            yield False
            return
    try:
        yield True
    finally:
        lock.rmdir()


def set_salary_advance_status(item_id: str, status: dict) -> dict:
    item = get_message(item_id)
    if item is None:
        raise ValueError("Không tìm thấy tin nhắn Zalo.")
    item["salary_advance"] = status
    if status.get("state") != "ignored":
        item["category"] = "Tạm ứng lương"
    path = INBOX_DIR / f"{item_id}.json"
    temporary = INBOX_DIR / f"{item_id}.{secrets.token_hex(8)}.tmp"
    temporary.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)
    return item


def count_messages() -> int:
    if not INBOX_DIR.exists():
        return 0
    return sum(1 for path in INBOX_DIR.glob("*.json")
               if re.fullmatch(r"[a-f0-9]{64}", path.stem))


def list_messages(limit: int = 100, offset: int = 0) -> list[dict]:
    if not INBOX_DIR.exists():
        return []
    paths = sorted((path for path in INBOX_DIR.glob("*.json")
                    if re.fullmatch(r"[a-f0-9]{64}", path.stem)),
                   key=lambda path: path.stat().st_mtime, reverse=True)
    start = max(0, int(offset))
    return [_read_item(path) for path in paths[start:start + max(0, int(limit))]]


def get_message(item_id: str) -> dict | None:
    if not re.fullmatch(r"[a-f0-9]{64}", item_id):
        return None
    path = INBOX_DIR / f"{item_id}.json"
    return _read_item(path) if path.exists() else None


def mark_reviewed(item_id: str) -> bool:
    item = get_message(item_id)
    if item is None:
        return False
    item["reviewed"] = True
    (INBOX_DIR / f"{item_id}.json").write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
    return True


def save_extracted_lines(item_id: str, lines: list[dict]) -> None:
    item = get_message(item_id)
    item["extracted_lines"] = [
        {"text": str(line["text"]), "confidence": round(float(line["confidence"]), 2)}
        for line in lines[:100]
    ]
    (INBOX_DIR / f"{item_id}.json").write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
