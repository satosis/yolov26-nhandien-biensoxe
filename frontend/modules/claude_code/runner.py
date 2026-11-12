"""Chạy một lượt Claude Code CLI cho nút chat Claude trong trang quản trị, tách khỏi web server.

scripts/dev_stack.py dừng cả cây tiến trình của Uvicorn mỗi khi file trong frontend/ hoặc backend/ đổi, tức là
ngay lần đầu Claude sửa file. Vì vậy web gọi `spawn`: tiến trình này khởi động `run` rồi thoát ngay; `run` mất
tiến trình cha nên không còn nằm trong cây của Uvicorn và chạy tiếp qua các lần web khởi động lại.

    python runner.py spawn <run_dir>
    python runner.py run <run_dir>

`run` đọc <run_dir>/request.json (argv, cwd, prompt, timeout_s), chép từng dòng stdout của CLI vào events.jsonl,
stderr vào stderr.log và ghi trạng thái vào status.json. Có file <run_dir>/stop thì dừng lượt chạy.
Chỉ dùng thư viện chuẩn và psutil, không import code của web.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import psutil

# Biến của một phiên Claude Code cha (khi web được khởi động từ trong Claude Code) không được lọt sang lượt chạy.
PARENT_SESSION_VARS = {
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_EFFORT",
    "CLAUDE_PID",
    "CLAUDE_AGENT_SDK_VERSION",
}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
POLL_SECONDS = 0.5

# Khi Claude báo các lỗi này (hết quota/credit, quá tải, hết hạn đăng nhập) thì chuyển sang Antigravity.
AGY_DIRECT = Path(__file__).resolve().parents[3] / "scripts" / "agents" / "agy_direct.py"
QUOTA_MARKERS = (
    "credit balance", "insufficient credit", "quota", "usage limit",
    "rate limit", "rate_limit", "too many requests", "overloaded",
    "limit reached", "429", "529",
    "unauthorized", "authentication_error", "invalid api key",
    "invalid x-api-key", "has expired", "please run /login",
)


def is_quota_error(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in QUOTA_MARKERS)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json(path: Path, data: dict) -> None:
    """Ghi nguyên tử; trên Windows os.replace lỗi khi web đang mở file để đọc nên thử lại vài lần."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def spawn(run_dir: Path) -> int:
    detach = {"creationflags": NO_WINDOW | NEW_GROUP} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "run", str(run_dir)],
        cwd=str(run_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **detach,
    )
    return 0


def kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
        family = parent.children(recursive=True) + [parent]
    except psutil.Error:
        return
    for member in family:
        try:
            member.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(family, timeout=5)
    for member in alive:
        try:
            member.kill()
        except psutil.Error:
            pass


def stderr_tail(run_dir: Path) -> str:
    try:
        return (run_dir / "stderr.log").read_bytes()[-800:].decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _agy_system_prompt(argv: list) -> str:
    """Lấy lại lời dặn đã truyền cho Claude (--append-system-prompt) để Antigravity theo cùng luật."""
    try:
        return str(argv[argv.index("--append-system-prompt") + 1])
    except (ValueError, IndexError):
        return ""


def _run_agy_fallback(request: dict, env: dict) -> str | None:
    """Claude hết hạn: chạy Antigravity trực tiếp cho cùng yêu cầu. Trả câu trả lời, hoặc None nếu agy cũng hỏng."""
    system = _agy_system_prompt(request.get("argv") or [])
    prompt = f"{system}\n\n{request.get('prompt', '')}".strip()
    timeout = int(request.get("timeout_s") or 1800)
    try:
        done = subprocess.run(
            [sys.executable, str(AGY_DIRECT), "--full", "--timeout", str(timeout)],
            input=prompt.encode("utf-8"),
            cwd=request["cwd"], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout + 60, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    answer = done.stdout.decode("utf-8", "replace").strip()
    return answer if done.returncode == 0 and answer else None


def _write_agy_events(run_dir: Path, answer: str) -> None:
    """Ghi đè events.jsonl bằng câu trả lời của Antigravity theo đúng dạng stream-json để trang hiển thị như thường."""
    lines = (
        {"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}},
        {"type": "result", "is_error": False, "subtype": "success", "result": "",
         "total_cost_usd": 0, "num_turns": 1},
    )
    with open(run_dir / "events.jsonl", "wb") as events:
        for line in lines:
            events.write((json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8"))


def run(run_dir: Path) -> int:
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    status_path = run_dir / "status.json"
    stop_file = run_dir / "stop"
    me = psutil.Process()
    base = {"runner_pid": me.pid, "runner_started": me.create_time(), "started_at": now()}
    if stop_file.exists():
        write_json(status_path, {**base, "state": "stopped", "finished_at": now()})
        return 0
    write_json(status_path, {**base, "state": "running"})

    env = {key: value for key, value in os.environ.items() if key.upper() not in PARENT_SESSION_VARS}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    result: dict = {}
    reason = None
    with open(run_dir / "events.jsonl", "ab") as events, open(run_dir / "stderr.log", "ab") as stderr:
        try:
            process = subprocess.Popen(
                request["argv"],
                cwd=request["cwd"],
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                creationflags=NO_WINDOW,
            )
        except OSError as error:
            write_json(status_path, {**base, "state": "error", "finished_at": now(),
                                     "error": f"Không chạy được Claude CLI: {error}"})
            return 1
        write_json(status_path, {**base, "state": "running", "claude_pid": process.pid})

        def feed() -> None:
            try:
                process.stdin.write(request["prompt"].encode("utf-8"))
                process.stdin.close()
            except OSError:
                pass

        def copy() -> None:
            for line in process.stdout:
                events.write(line)
                events.flush()
                # Khóa "type" không nhất thiết đứng đầu dòng; chuỗi này nằm trong nội dung file thì đã bị escape.
                if b'"type":"result"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") == "result":
                    result.clear()
                    result.update(event)

        reader = threading.Thread(target=copy, daemon=True)
        reader.start()
        threading.Thread(target=feed, daemon=True).start()
        deadline = time.monotonic() + float(request.get("timeout_s") or 1800)
        while process.poll() is None:
            if stop_file.exists():
                reason = "stopped"
            elif time.monotonic() > deadline:
                reason = "timeout"
            if reason:
                kill_tree(process.pid)
                break
            time.sleep(POLL_SECONDS)
        process.wait()
        reader.join(timeout=10)

    error = None
    if reason == "stopped":
        state = "stopped"
    elif reason == "timeout":
        state = "timeout"
        error = f"Quá {int(request.get('timeout_s') or 1800) // 60} phút, đã dừng."
    elif result and not result.get("is_error"):
        state = "done"
    else:
        state = "error"
        error = (str(result.get("result") or result.get("subtype") or "") if result else "") \
            or stderr_tail(run_dir) or f"Claude CLI thoát với mã {process.returncode}."
        if is_quota_error(error):
            answer = _run_agy_fallback(request, env)
            if answer:
                _write_agy_events(run_dir, answer)
                state, error = "done", None
    write_json(status_path, {
        **base,
        "state": state,
        "error": error,
        "exit_code": process.returncode,
        "finished_at": now(),
        "cost_usd": result.get("total_cost_usd"),
        "duration_ms": result.get("duration_ms"),
        "num_turns": result.get("num_turns"),
    })
    return 0


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("spawn", "run"):
        print(__doc__, file=sys.stderr)
        return 2
    run_dir = Path(sys.argv[2]).resolve()
    if sys.argv[1] == "spawn":
        return spawn(run_dir)
    try:
        return run(run_dir)
    except Exception as error:
        # Ghi lỗi để trang thấy ngay thay vì chờ phát hiện runner đã chết.
        write_json(run_dir / "status.json", {"state": "error", "finished_at": now(), "error": f"Lỗi runner: {error!r}"})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
