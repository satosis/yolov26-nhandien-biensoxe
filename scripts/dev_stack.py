"""Chạy web (Uvicorn), backend AI + cầu nối Zalo và Cloudflare Tunnel trong một terminal.

Web và backend tự khởi động lại khi code đổi. Không dùng `uvicorn --reload` vì reloader của Uvicorn trên Windows
gửi CTRL_C_EVENT làm ngắt mọi tiến trình dùng chung terminal VS Code.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"
ZALO_BRIDGE = ROOT / "scripts" / "zalo_bridge"
AI_BUSY_PID = FRONTEND / ".runtime" / "zalo_inbox" / "ai-running.pid"
STOP = threading.Event()


def snapshot(files) -> dict[Path, tuple[int, int]]:
    """Modification time and size of each file; a file that vanished mid-scan is skipped."""
    state = {}
    for path in files:
        try:
            info = path.stat()
            state[path] = (info.st_mtime_ns, info.st_size)
        except OSError:
            pass
    return state


def stop_tree(process: subprocess.Popen) -> None:
    """Terminate a process and its children, kill what survives 5 seconds, and reap the process."""
    try:
        parent = psutil.Process(process.pid)
        family = parent.children(recursive=True) + [parent]
    except psutil.Error:
        family = []
    for member in family:
        try:
            member.terminate()
        except psutil.Error:
            pass
    if family:
        _, alive = psutil.wait_procs(family, timeout=5)
        for member in alive:
            try:
                member.kill()
            except psutil.Error:
                pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def web_files():
    skip = {".runtime", "node_modules", "__pycache__"}
    for path in FRONTEND.rglob("*"):
        if path.suffix in {".py", ".html", ".css", ".js"} and not skip.intersection(path.parts):
            yield path
    yield from BACKEND.rglob("*.py")


def backend_files():
    yield from BACKEND.rglob("*.py")
    for pattern in ("*.js", "*.mjs", "*.cjs"):
        yield from ZALO_BRIDGE.glob(pattern)
    yield ZALO_BRIDGE / "package.json"


def ai_command_running() -> bool:
    """A Zalo AI command is running (a marker left by a bridge that was killed externally does not count)."""
    try:
        bridge = psutil.Process(int(AI_BUSY_PID.read_text(encoding="ascii").strip()))
        return bridge.is_running() and "bridge.js" in " ".join(bridge.cmdline())
    except (OSError, ValueError, psutil.Error):
        return False


def stop_backend(process: subprocess.Popen) -> None:
    """Stop the backend, then the Zalo bridge, which can be detached from it on Windows."""
    stop_tree(process)
    sys.path.insert(0, str(ROOT))
    try:
        from backend.services import zalo_bridge

        zalo_bridge.stop()
    except Exception as exc:
        # A source edit can break an import for a moment; keep watching.
        print(f"Could not stop the detached Zalo bridge: {exc}", file=sys.stderr, flush=True)


def watch(name: str, command: list[str], cwd: Path, files, stop=stop_tree, busy=lambda: False) -> None:
    """Run `command` and restart it when `files()` change; wait while `busy()` (e.g. an AI command is running)."""
    print(f"[{name}] watching source for changes", flush=True)
    state = snapshot(files())
    process = None
    pending = waiting = False
    while not STOP.is_set():
        if process is None or process.poll() is not None:
            if process is not None:
                print(f"[{name}] exited with code {process.returncode}; restarting...", flush=True)
                STOP.wait(2)
            process = subprocess.Popen(command, cwd=cwd)
        STOP.wait(1)
        if snapshot(files()) != state:
            STOP.wait(0.5)  # let editors finish atomic saves
            state = snapshot(files())
            pending = True
        if pending and busy():
            if not waiting:
                print(f"[{name}] source changed; waiting for the Zalo AI command to finish...", flush=True)
            waiting = True
            continue
        if pending:
            print(f"[{name}] source changed; restarting...", flush=True)
            stop(process)
            process = None
            pending = waiting = False
    if process is not None:
        stop(process)


def env_setting(name: str) -> str:
    """A setting from the environment, else from .env (this script runs outside the web app)."""
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    return ""


def show_tunnel_problems(process: subprocess.Popen) -> None:
    for line in process.stdout:
        if "ERR" in line or "WARN" in line:
            print(f"[Tunnel] {line.strip()}", flush=True)


def tunnel() -> None:
    """Keep the named Cloudflare Tunnel running (CLOUDFLARE_TUNNEL_TOKEN in .env). A quick tunnel is never started:
    its random trycloudflare.com address dies with the process."""
    token = env_setting("CLOUDFLARE_TUNNEL_TOKEN")
    binary = shutil.which("cloudflared") or next(
        (str(p) for p in (Path(os.environ.get("ProgramFiles", "")) / "cloudflared" / "cloudflared.exe",
                          Path(os.environ.get("ProgramFiles(x86)", "")) / "cloudflared" / "cloudflared.exe") if p.is_file()), "")
    if not token or not binary:
        print("[Tunnel] skipped: set CLOUDFLARE_TUNNEL_TOKEN in .env and install cloudflared", flush=True)
        return
    while not STOP.is_set():
        process = subprocess.Popen([binary, "tunnel", "run", "--token", token], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        threading.Thread(target=show_tunnel_problems, args=(process,), daemon=True).start()
        while process.poll() is None and not STOP.wait(1):
            pass
        stop_tree(process)
        STOP.wait(5)


def main() -> int:
    with socket.socket() as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(("127.0.0.1", 8001)) == 0:
            print("Port 8001 is already in use. Stop the previous web server first.", file=sys.stderr)
            return 1
    jobs = [
        threading.Thread(target=watch, args=("Web", [sys.executable, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"],
                                             FRONTEND, web_files)),
        threading.Thread(target=watch, args=("AI backend + Zalo", [sys.executable, "backend/main.py"], ROOT, backend_files),
                         kwargs={"stop": stop_backend, "busy": ai_command_running}),
        threading.Thread(target=tunnel),
    ]
    for job in jobs:
        job.start()
    print("[*] Web, AI backend, Zalo bridge and tunnel share this terminal. Press Ctrl+C to stop.", flush=True)
    try:
        while not STOP.wait(0.5):
            pass
    except KeyboardInterrupt:
        print("\n[*] Stopping services...", flush=True)
    finally:
        STOP.set()
        for job in jobs:
            job.join(timeout=30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
