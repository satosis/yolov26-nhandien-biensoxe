"""Block every outbound connection except an allow-list (used for tests and the dry-run test server)."""

from __future__ import annotations

import json
import os
import socket
import threading
import traceback
import urllib.parse
from datetime import datetime
from pathlib import Path

_orig_connect = socket.socket.connect
_orig_create_connection = socket.create_connection
_orig_socketpair = socket.socketpair

_allowed: set[tuple[str, int]] = set()
_log_path: Path | None = None
_installed: bool = False
_local = threading.local()


def _normalize_host(host: str) -> str:
    h = str(host).strip().lower()
    return "127.0.0.1" if h == "localhost" else h


def _is_allowed(host: str, port: int) -> bool:
    norm_host = _normalize_host(host)
    try:
        norm_port = int(port)
    except (ValueError, TypeError):
        return False
    return (norm_host, norm_port) in _allowed or (str(host).strip().lower(), norm_port) in _allowed


def _log_blocked(host: str, port: int) -> None:
    if _log_path is None:
        return
    stack_top = ""
    for frame in reversed(traceback.extract_stack()):
        fpath = frame.filename.replace("\\", "/")
        if fpath.endswith("net_guard.py") or fpath.endswith("/socket.py"):
            continue
        stack_top = f"{frame.filename}:{frame.lineno} in {frame.name}"
        break

    record = {
        "time": datetime.now().isoformat(),
        "host": str(host),
        "port": int(port),
        "stack_top": stack_top,
    }
    try:
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _guarded_connect(self, address):
    if getattr(_local, "in_allowed_connection", False):
        return _orig_connect(self, address)
    if not isinstance(address, (tuple, list)) or len(address) < 2:
        return _orig_connect(self, address)
    host, port = address[0], address[1]
    if not _is_allowed(host, port):
        _log_blocked(host, port)
        raise ConnectionRefusedError(f"EXTERNAL_DRY_RUN: chặn kết nối tới {host}:{port}")
    return _orig_connect(self, address)


def _guarded_create_connection(address, *args, **kwargs):
    if not isinstance(address, (tuple, list)) or len(address) < 2:
        return _orig_create_connection(address, *args, **kwargs)
    host, port = address[0], address[1]
    if not _is_allowed(host, port):
        _log_blocked(host, port)
        raise ConnectionRefusedError(f"EXTERNAL_DRY_RUN: chặn kết nối tới {host}:{port}")

    _local.in_allowed_connection = True
    try:
        return _orig_create_connection(address, *args, **kwargs)
    finally:
        _local.in_allowed_connection = False


def _guarded_socketpair(*args, **kwargs):
    # Trên Windows, socket.socketpair() (asyncio dùng để đánh thức event loop) tự kết nối tới 127.0.0.1:<cổng ngẫu nhiên>.
    # Chặn kết nối nội bộ đó làm server treo mỗi khi chuyển việc sang thread pool — luôn cho qua.
    _local.in_allowed_connection = True
    try:
        return _orig_socketpair(*args, **kwargs)
    finally:
        _local.in_allowed_connection = False


def install(allowed: set[tuple[str, int]], log_path: Path) -> None:
    global _allowed, _log_path, _installed
    _log_path = Path(log_path)
    normalized: set[tuple[str, int]] = set()
    for h, p in allowed:
        host_str = str(h).strip().lower()
        port_int = int(p)
        if host_str == "localhost":
            normalized.add(("127.0.0.1", port_int))
            normalized.add(("localhost", port_int))
        elif host_str == "127.0.0.1":
            normalized.add(("127.0.0.1", port_int))
            normalized.add(("localhost", port_int))
        else:
            normalized.add((host_str, port_int))
    _allowed = normalized

    if not _installed:
        socket.socket.connect = _guarded_connect
        socket.create_connection = _guarded_create_connection
        socket.socketpair = _guarded_socketpair
        _installed = True


def install_from_env() -> bool:
    if os.environ.get("EXTERNAL_DRY_RUN") != "1":
        return False

    db_url = os.environ.get("DATABASE_URL", "")
    allowed: set[tuple[str, int]] = set()

    if db_url:
        parsed = urllib.parse.urlparse(db_url)
        db_host = parsed.hostname or "127.0.0.1"
        db_port = parsed.port or 5432
        allowed.add((db_host, db_port))
    else:
        allowed.add(("127.0.0.1", 5432))

    test_port = int(os.environ.get("TEST_SERVER_PORT", "8001"))
    allowed.add(("127.0.0.1", test_port))

    repo_root = Path(__file__).resolve().parents[2]
    log_path = repo_root / "data" / "logs" / "dry_run.jsonl"

    install(allowed, log_path)
    return True
