"""Nút chat Claude trong trang quản trị: chủ dự án nhờ Claude Code đọc và sửa mã nguồn của chính dự án này.

Mỗi tin nhắn là một lượt `claude -p` chạy trong thư mục dự án; các lượt của một cuộc trò chuyện dùng chung session
id nên Claude nhớ các tin trước. Lượt chạy do runner.py chạy ngoài cây tiến trình của web (web tự khởi động lại mỗi
khi Claude sửa file); trạng thái nằm ở frontend/.runtime/claude_code/ nên trang đọc lại được sau khi web khởi động lại.

Quyền của Claude: chỉ đọc/sửa file trong thư mục dự án (--restricted) và chạy vài lệnh kiểm tra; lệnh khác bị từ
chối vì không có ai duyệt. Chỉ tài khoản Admin có id trong CLAUDE_CHAT_USER_IDS (mặc định 1 = admin) dùng được.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import psutil

PROJECT_DIR = Path(__file__).resolve().parents[3]
RUNTIME_DIR = PROJECT_DIR / "frontend" / ".runtime" / "claude_code"
RUNS_DIR = RUNTIME_DIR / "runs"
CONVERSATIONS_DIR = RUNTIME_DIR / "conversations"
ACTIVE_FILE = RUNTIME_DIR / "active.json"
RUNNER = Path(__file__).with_name("runner.py")

DEFAULT_USER_IDS = "1"
RUN_TIMEOUT_S = 30 * 60
STARTING_GRACE_S = 60
RUNS_SHOWN = 30
ORPHAN_RUNS_KEPT_S = 30 * 86400
DETAIL_CHARS = 4000
FINAL_STATES = {"done", "error", "stopped", "timeout", "interrupted"}
RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}$")
FILENAME_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

TOOLS = "Read,Glob,Grep,Edit,Write,TodoWrite,Bash"
# Lệnh tự chạy được, ngoài các lệnh chỉ đọc trong thư mục dự án mà Claude Code tự cho phép; lệnh khác bị từ chối.
ALLOWED_TOOLS = [
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(python -m py_compile:*)",
    "Bash(python -m unittest:*)",
    "Bash(python scripts/check_all.py:*)",
    "Bash(node --check:*)",
]
DISALLOWED_TOOLS = ["WebFetch", "WebSearch", "Bash(rm:*)", "Bash(rmdir:*)"]
INSTRUCTIONS = "\n".join([
    "Bạn được gọi từ nút chat Claude trong trang quản trị web của chính dự án này. Người dùng là chủ dự án; "
    "họ chỉ thấy câu trả lời và danh sách thao tác của bạn trên trang web, không có terminal, "
    "và không ai duyệt quyền giữa chừng.",
    "- Trả lời bằng tiếng Việt, ngắn gọn: đã sửa gì, ở đâu, người dùng cần làm gì tiếp.",
    "- Tin nhắn bắt đầu bằng dòng [Trang đang mở: ...] là trang người dùng đang xem; 'trang này' là trang đó.",
    "- File người dùng đính kèm được liệt kê cuối tin nhắn kèm đường dẫn đầy đủ; ảnh, PDF và file văn bản mở bằng "
    "công cụ Read, file khác thì chỉ biết tên và dung lượng.",
    "- Web tự khởi động lại khi file trong frontend/ hoặc backend/ thay đổi; sửa xong thì nhắc người dùng tải lại trang.",
    "- Thư mục đang có thay đổi chưa commit của người khác: không xóa, hoàn tác hay sắp xếp lại code ngoài phạm vi "
    "yêu cầu; không commit, push, git checkout/restore/stash/reset.",
    "- Lệnh shell chạy được: git status/diff/log/show, python -m py_compile <file>, python -m unittest ..., "
    "python scripts/check_all.py, node --check <file>; lệnh khác bị chặn.",
    "- Yêu cầu mơ hồ hoặc rủi ro (xóa dữ liệu, đổi cấu trúc DB, sửa .env): hỏi lại trong câu trả lời thay vì đoán.",
    "- Người dùng nhờ lưu lại việc để xem/làm sau (việc chưa xử lý, 'lưu lại', 'ghi nhớ'): chưa làm việc đó, chỉ ghi "
    "thêm vào cuối file .ai/viec-chua-xu-ly.md một dòng '- [ ] dd/mm/yyyy: <nội dung>' đủ rõ để sau đọc lại hiểu được "
    "(có ảnh đính kèm thì tả bằng chữ những gì trong ảnh cần cho việc đó, kèm đường dẫn ảnh). Hỏi các việc đã lưu "
    "thì đọc file đó và liệt kê đánh số các dòng '[ ]'; làm xong việc nào thì đổi '[ ]' của dòng đó thành '[x]'.",
])
TOOL_LABELS = {
    "Read": "Đọc",
    "Edit": "Sửa",
    "Write": "Ghi",
    "Bash": "Chạy",
    "Grep": "Tìm",
    "Glob": "Tìm file",
    "TodoWrite": "Kế hoạch",
}

_lock = threading.Lock()


class ChatError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def allowed_user_ids() -> set[int]:
    raw = os.environ.get("CLAUDE_CHAT_USER_IDS", DEFAULT_USER_IDS)
    return {int(part) for part in re.split(r"[\s,;]+", raw) if part.isdigit()}


def is_allowed(user) -> bool:
    """Tài khoản Admin có id trong CLAUDE_CHAT_USER_IDS; cần SESSION_SECRET riêng để cookie phiên không giả được."""
    if not isinstance(user, dict) or user.get("role") != "Admin" or not os.environ.get("SESSION_SECRET"):
        return False
    user_id = str(user.get("id", ""))
    return user_id.isdigit() and int(user_id) in allowed_user_ids()


def is_allowed_request(request) -> bool:
    """Cho template: có hiện nút chat Claude cho người đang xem trang không."""
    session = request.scope.get("session") if request is not None else None
    return is_allowed(session.get("user") if isinstance(session, dict) else None)


def claude_cli() -> str | None:
    configured = os.environ.get("CLAUDE_CLI", "").strip()
    if configured:
        return configured
    if os.name == "nt":
        candidates = [Path.home() / ".local" / "bin" / "claude.exe"]
        if os.environ.get("APPDATA"):
            candidates.insert(0, Path(os.environ["APPDATA"]) / "npm" / "node_modules" / "@anthropic-ai"
                              / "claude-code" / "bin" / "claude.exe")
        found = next((str(path) for path in candidates if path.is_file()), None)
        # Không dùng claude.cmd: cmd.exe làm hỏng tham số có ký tự đặc biệt.
        return found or shutil.which("claude.exe")
    return shutil.which("claude")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def _conversation_key(user: dict) -> str:
    """Một cuộc trò chuyện cho mỗi tài khoản; đăng nhập máy nào cũng dùng chung."""
    return str(int(user["id"]))


def _conversation_path(user) -> Path:
    return CONVERSATIONS_DIR / f"{_conversation_key(user)}.json"


def _fresh_conversation(user) -> dict:
    conversation = {"session_id": str(uuid.uuid4()), "runs": [], "created_at": _now()}
    _write_json(_conversation_path(user), conversation)
    return conversation


def _valid(conversation) -> bool:
    return isinstance(conversation, dict) and bool(conversation.get("session_id")) and isinstance(conversation.get("runs"), list)


def _load_conversation(user) -> dict:
    path = _conversation_path(user)
    conversation = _read_json(path)
    if _valid(conversation):
        return conversation
    return _fresh_conversation(user)


def _alive(status: dict) -> bool:
    try:
        process = psutil.Process(int(status["runner_pid"]))
        return abs(process.create_time() - float(status["runner_started"])) < 1.0
    except (KeyError, TypeError, ValueError, psutil.Error):
        return False


def _read_status(path: Path) -> dict | None:
    """status.json, hoặc None khi chưa đọc được (Windows từ chối mở đúng lúc runner đang thay file)."""
    for _ in range(5):
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
            return status if isinstance(status, dict) else None
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            time.sleep(0.05)
    return None


def _status(run_id: str) -> dict:
    """Trạng thái lượt chạy; lượt có runner đã chết (máy khởi động lại, tiến trình bị tắt) ghi lại là interrupted."""
    path = RUNS_DIR / run_id / "status.json"
    status = _read_status(path)
    if status is None:
        # Không đọc được lúc này thì coi như còn chạy; lần hỏi sau đọc lại, không ghi đè trạng thái của runner.
        return {"state": "running"} if path.exists() else {"state": "interrupted", "error": "Mất trạng thái lượt chạy."}
    state = status.get("state")
    if state in FINAL_STATES:
        return status
    if state == "running" and _alive(status):
        return status
    if state == "starting" and time.time() - float(status.get("created") or 0) < STARTING_GRACE_S:
        return status
    # Runner có thể vừa ghi trạng thái mới giữa hai lần đọc.
    latest = _read_status(path) or status
    if latest.get("state") in FINAL_STATES or (latest.get("state") == "running" and _alive(latest)):
        return latest
    status = {**latest, "state": "interrupted", "finished_at": _now(),
              "error": "Lượt chạy bị ngắt (máy chủ khởi động lại hoặc tiến trình bị tắt)."}
    _write_json(path, status)
    return status


def _active_run_id() -> str | None:
    run_id = (_read_json(ACTIVE_FILE, {}) or {}).get("run_id")
    if not isinstance(run_id, str) or not RUN_ID.match(run_id):
        return None
    return None if _status(run_id).get("state") in FINAL_STATES else run_id


def _has_session(run_id: str) -> bool:
    """Lượt chạy đã tạo phiên Claude (sự kiện init) thì các lượt sau phải --resume thay vì --session-id."""
    try:
        with (RUNS_DIR / run_id / "events.jsonl").open("rb") as handle:
            return b'"subtype":"init"' in handle.read(65536)
    except OSError:
        return False


def _command(cli: str, session_id: str, resume: bool) -> list[str]:
    command = [
        cli, "-p",
        "--output-format", "stream-json", "--verbose",
        "--restricted", "--tools", TOOLS,
        "--permission-mode", "acceptEdits", "--permission-prompts", "none",
        "--allowedTools", *ALLOWED_TOOLS,
        "--disallowedTools", *DISALLOWED_TOOLS,
        "--append-system-prompt", INSTRUCTIONS,
        "--resume" if resume else "--session-id", session_id,
    ]
    model = os.environ.get("CLAUDE_CHAT_MODEL", "").strip()
    if model:
        command += ["--model", model]
    return command


def _launch(run_dir: Path) -> None:
    """runner.py spawn khởi động lượt chạy rồi thoát ngay, để lượt chạy không thuộc cây tiến trình của web."""
    spawner = subprocess.Popen(
        [sys.executable, str(RUNNER), "spawn", str(run_dir)],
        cwd=str(PROJECT_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if spawner.wait(timeout=30) != 0:
        raise OSError(f"runner spawn thoát với mã {spawner.returncode}")


def _prune_orphan_runs(active: str | None) -> None:
    """Xóa lượt chạy cũ không còn thuộc cuộc trò chuyện nào (sau khi bấm cuộc trò chuyện mới)."""
    if not RUNS_DIR.is_dir():
        return
    kept = set()
    for path in CONVERSATIONS_DIR.glob("*.json"):
        conversation = _read_json(path, {}) or {}
        kept.update(run for run in conversation.get("runs", []) if isinstance(run, str))
    cutoff = time.time() - ORPHAN_RUNS_KEPT_S
    for run_dir in RUNS_DIR.iterdir():
        if run_dir.name in kept or run_dir.name == active or not RUN_ID.match(run_dir.name):
            continue
        try:
            if run_dir.stat().st_mtime < cutoff:
                shutil.rmtree(run_dir, ignore_errors=True)
        except OSError:
            pass


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _relative(path) -> str:
    text = str(path or "")
    candidate = Path(text)
    if not candidate.is_absolute():
        return text.replace("\\", "/")
    try:
        return candidate.relative_to(PROJECT_DIR).as_posix()
    except ValueError:
        return text


def _content(event: dict) -> list[dict]:
    content = (event.get("message") or {}).get("content")
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return ""


def _tool_item(block: dict) -> dict:
    name = str(block.get("name") or "")
    data = block.get("input") if isinstance(block.get("input"), dict) else {}
    item = {"kind": "tool", "id": block.get("id"), "name": name, "label": TOOL_LABELS.get(name, name), "target": ""}
    if name in ("Read", "Edit", "Write"):
        item["target"] = _relative(data.get("file_path"))
    if name == "Edit":
        item["old"] = _clip(str(data.get("old_string") or ""), DETAIL_CHARS)
        item["new"] = _clip(str(data.get("new_string") or ""), DETAIL_CHARS)
    elif name == "Write":
        item["new"] = _clip(str(data.get("content") or ""), DETAIL_CHARS)
    elif name == "Bash":
        item["target"] = _clip(str(data.get("command") or ""), 400)
    elif name == "Grep":
        where = _relative(data.get("path")) if data.get("path") else ""
        item["target"] = _clip(str(data.get("pattern") or ""), 200) + (f" — {where}" if where else "")
    elif name == "Glob":
        item["target"] = _clip(str(data.get("pattern") or ""), 200)
    elif name == "TodoWrite":
        todos = data.get("todos") if isinstance(data.get("todos"), list) else []
        item["todos"] = [{"text": _clip(str(todo.get("content") or ""), 200), "status": todo.get("status")}
                         for todo in todos if isinstance(todo, dict)][:20]
    return item


def items_from_events(lines: list[str]) -> list[dict]:
    """Đổi luồng stream-json của Claude CLI thành các dòng hiển thị: lời Claude, thao tác, kết quả thao tác, tổng kết.

    Kết quả của thao tác là mục `tool_status` riêng trỏ về id của thao tác; trang tự ghép vào dòng thao tác.
    """
    items = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "assistant":
            for block in _content(event):
                if block.get("type") == "text" and str(block.get("text") or "").strip():
                    items.append({"kind": "text", "text": str(block["text"])})
                elif block.get("type") == "tool_use":
                    items.append(_tool_item(block))
        elif kind == "user":
            for block in _content(event):
                if block.get("type") != "tool_result":
                    continue
                if block.get("is_error"):
                    items.append({"kind": "tool_status", "id": block.get("tool_use_id"), "status": "error",
                                  "text": _clip(_text_of(block.get("content")), 400)})
                else:
                    items.append({"kind": "tool_status", "id": block.get("tool_use_id"), "status": "ok"})
        elif kind == "system" and event.get("subtype") == "permission_denied":
            items.append({"kind": "tool_status", "id": event.get("tool_use_id"), "status": "denied",
                          "text": _clip(str(event.get("message") or ""), 400)})
        elif kind == "result":
            items.append({
                "kind": "result",
                "is_error": bool(event.get("is_error")),
                "subtype": event.get("subtype"),
                "text": _clip(str(event.get("result") or ""), 2000) if event.get("is_error") else "",
                "cost_usd": event.get("total_cost_usd"),
                "duration_ms": event.get("duration_ms"),
                "num_turns": event.get("num_turns"),
            })
    return items


def _read_lines(path: Path, after: int) -> tuple[list[str], int]:
    """Các dòng đã ghi trọn vẹn kể từ byte `after`, và vị trí byte tiếp theo cần đọc."""
    try:
        with path.open("rb") as handle:
            handle.seek(after)
            data = handle.read()
    except OSError:
        return [], after
    end = data.rfind(b"\n") + 1
    return data[:end].decode("utf-8", "replace").splitlines(), after + end


def _items(run_dir: Path, after: int, final: bool) -> tuple[list[dict], int]:
    cache = run_dir / "items.json"
    if final and after == 0:
        cached = _read_json(cache)
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            return cached["items"], int(cached.get("next") or 0)
    lines, next_offset = _read_lines(run_dir / "events.jsonl", after)
    items = items_from_events(lines)
    if final and after == 0:
        _write_json(cache, {"items": items, "next": next_offset})
    return items, next_offset


def _elapsed_s(status: dict) -> int | None:
    """Số giây đã chạy, tính trên đồng hồ máy chủ để trang không lệch theo múi giờ của trình duyệt."""
    try:
        started = datetime.fromisoformat(status["started_at"])
        finished = datetime.fromisoformat(status["finished_at"]) if status.get("finished_at") else datetime.now()
    except (KeyError, TypeError, ValueError):
        return None
    return max(0, int((finished - started).total_seconds()))


def _run_view(run_id: str, after: int) -> dict:
    run_dir = RUNS_DIR / run_id
    request = _read_json(run_dir / "request.json", {}) or {}
    status = _status(run_id)
    items, next_offset = _items(run_dir, after, status.get("state") in FINAL_STATES)
    return {
        "elapsed_s": _elapsed_s(status),
        "run_id": run_id,
        "message": request.get("message", ""),
        "page_title": request.get("page_title", ""),
        "page_path": request.get("page_path", ""),
        "attachments": request.get("attachments", []),
        "created_at": request.get("created_at"),
        "state": status.get("state"),
        "error": status.get("error"),
        "started_at": status.get("started_at"),
        "finished_at": status.get("finished_at"),
        "items": items,
        "next": next_offset,
    }


def _owned_run(user: dict, run_id: str) -> Path:
    request = _read_json(RUNS_DIR / run_id / "request.json") if RUN_ID.match(run_id or "") else None
    if not isinstance(request, dict) or request.get("user_id") != int(user["id"]):
        raise ChatError("Không tìm thấy lượt chạy.", 404)
    return RUNS_DIR / run_id


def _run_ids(conversation: dict) -> list[str]:
    return [run for run in conversation["runs"] if isinstance(run, str) and RUN_ID.match(run)]


def conversation_view(user: dict) -> dict:
    conversation = _load_conversation(user)
    runs = [run for run in _run_ids(conversation) if (RUNS_DIR / run).is_dir()][-RUNS_SHOWN:]
    active = _active_run_id()
    return {
        "runs": [_run_view(run, 0) for run in runs],
        "busy_elsewhere": bool(active and active not in conversation["runs"]),
    }


def run_view(user: dict, run_id: str, after: int) -> dict:
    _owned_run(user, run_id)
    return _run_view(run_id, after)


def _save_uploads(run_dir: Path, files) -> list[dict]:
    """Chép file đính kèm (không giới hạn dung lượng) vào <run_dir>/uploads/ để Claude mở bằng Read."""
    saved = []
    for index, upload in enumerate(files, start=1):
        name = Path(str(upload.filename or "").replace("\\", "/")).name or "file"
        target = run_dir / "uploads" / f"{index}-{FILENAME_UNSAFE.sub('_', name)}"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as out:
            shutil.copyfileobj(upload.file, out, 1024 * 1024)
        saved.append({"name": name, "size": target.stat().st_size,
                      "image": (upload.content_type or "").startswith("image/"), "path": str(target)})
    return saved


def send(user: dict, message: str, page_path: str, page_title: str, files=()) -> dict:
    text = (message or "").strip()
    if not text and not files:
        raise ChatError("Nhập việc cần Claude làm.")
    cli = claude_cli()
    if not cli:
        raise ChatError("Máy chủ chưa cài Claude Code CLI.", 503)
    user_id = int(user["id"])
    page_path = (page_path or "").strip()[:300]
    page_title = (page_title or "").strip()[:200]
    with _lock:
        if _active_run_id():
            raise ChatError("Claude đang làm yêu cầu trước; chờ xong hoặc bấm Dừng.", 409)
        conversation = _load_conversation(user)
        resume = any(_has_session(run) for run in _run_ids(conversation))
        run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        run_dir = RUNS_DIR / run_id
        saved = _save_uploads(run_dir, files)
        prompt = f"[Trang đang mở: {page_title or 'không rõ'} — {page_path or '/admin'}]\n\n{text or '(Không có lời nhắn, xem file đính kèm.)'}"
        if saved:
            prompt += "\n\n[File đính kèm]\n" + "\n".join(f"- {item['path']} ({item['name']})" for item in saved)
        _write_json(run_dir / "request.json", {
            "run_id": run_id,
            "user_id": user_id,
            "session_id": conversation["session_id"],
            "message": text,
            "attachments": [{key: item[key] for key in ("name", "size", "image")} for item in saved],
            "page_path": page_path,
            "page_title": page_title,
            "created_at": _now(),
            "cwd": str(PROJECT_DIR),
            "argv": _command(cli, conversation["session_id"], resume),
            "prompt": prompt,
            "timeout_s": RUN_TIMEOUT_S,
        })
        _write_json(run_dir / "status.json", {"state": "starting", "created": time.time()})
        conversation["runs"].append(run_id)
        _write_json(_conversation_path(user), conversation)
        _write_json(ACTIVE_FILE, {"run_id": run_id})
        try:
            _launch(run_dir)
        except (OSError, subprocess.SubprocessError) as error:
            _write_json(run_dir / "status.json", {"state": "error", "finished_at": _now(),
                                                  "error": f"Không khởi động được Claude: {error}"})
    _prune_orphan_runs(run_id)
    return _run_view(run_id, 0)


async def fill_companies_from_run(run_id: str) -> None:
    """Tin nhắn hoặc ảnh/PDF đính kèm có thông tin công ty (MST + tài khoản hoặc email): bổ sung các ô còn trống của
    công ty đó, như tin gửi trong nhóm Zalo."""
    from frontend.modules.zalo.company_card import fill_company_from_card
    from frontend.modules.zalo.invoice_automation import read_image_text, read_pdf_text

    run_dir = RUNS_DIR / run_id
    texts = [(_read_json(run_dir / "request.json", {}) or {}).get("message") or ""]
    uploads = run_dir / "uploads"
    for path in sorted(uploads.iterdir()) if uploads.is_dir() else []:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            texts.append(await asyncio.to_thread(read_pdf_text, path))
        elif suffix in IMAGE_SUFFIXES:
            texts.append(await asyncio.to_thread(read_image_text, path))
    for text in texts:
        if text.strip():
            await fill_company_from_card(text)


def stop(user: dict, run_id: str) -> None:
    run_dir = _owned_run(user, run_id)
    if _status(run_id).get("state") not in FINAL_STATES:
        (run_dir / "stop").touch()


def new_conversation(user: dict) -> dict:
    with _lock:
        active = _active_run_id()
        if active and active in _load_conversation(user)["runs"]:
            raise ChatError("Claude đang chạy; bấm Dừng trước khi mở cuộc trò chuyện mới.", 409)
        _fresh_conversation(user)
    return conversation_view(user)
