"""Zalo bridge process (scripts/zalo_bridge/bridge.js) tied to the backend lifecycle.

`python backend/main.py` starts the bridge, restarts it if it dies, and stops it on exit.
It is not started before a QR login exists (session.json) or after "Ngắt kết nối" (bridge-disabled).
"""

import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parents[2]
BRIDGE_DIR = PROJECT_DIR / "scripts" / "zalo_bridge"
INBOX_DIR = PROJECT_DIR / "frontend" / ".runtime" / "zalo_inbox"
PID_FILE = INBOX_DIR / "bridge.pid"
PORT_FILE = INBOX_DIR / "bridge-port"
SESSION_FILE = INBOX_DIR / "session.json"
DISABLED_FILE = INBOX_DIR / "bridge-disabled"


def live_port() -> int | None:
    """Port of a bridge that actually accepts connections; status/port files alone can be stale."""
    try:
        port = int(PORT_FILE.read_text(encoding="ascii").strip())
        if not 1 <= port <= 65535:
            return None
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return port
    except (OSError, ValueError):
        return None


def _process() -> psutil.Process | None:
    try:
        process = psutil.Process(int(PID_FILE.read_text(encoding="ascii").strip()))
        if process.is_running() and "bridge.js" in " ".join(process.cmdline()):
            return process
    except (OSError, ValueError, psutil.Error):
        pass
    return None


def running() -> bool:
    return _process() is not None


def spawn() -> subprocess.Popen:
    """Start bridge.js detached from this process and record its pid; OSError when node cannot start."""
    with (INBOX_DIR / "bridge.log").open("ab") as logfile:
        process = subprocess.Popen(
            ["node", "bridge.js"], cwd=BRIDGE_DIR,
            stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT,
            creationflags=(subprocess.CREATE_NO_WINDOW | 0x00000008) if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    PID_FILE.write_text(str(process.pid), encoding="ascii")
    return process


def ensure(timeout_secs: float = 6.0) -> int | None:
    """Return the bridge port, starting the bridge first if it is not running."""
    port = live_port()
    if port:
        return port
    # Running but no port yet (logging in / waiting for QR): wait instead of starting a second copy.
    if _process() is None:
        if not SESSION_FILE.exists() or DISABLED_FILE.exists():
            return None
        PORT_FILE.unlink(missing_ok=True)
        try:
            process = spawn()
        except OSError as exc:
            logger.warning("Failed to auto-start Zalo bridge: %s", exc)
            return None
        logger.info("Zalo bridge started (pid %s)", process.pid)

    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        port = live_port()
        if port:
            return port
        time.sleep(0.3)
    return None


def stop() -> None:
    """Stop the bridge and anything it spawned (e.g. a running Claude command)."""
    process = _process()
    if process is None:
        return
    family = process.children(recursive=True) + [process]
    for member in family:
        try:
            member.terminate()
        except psutil.Error:
            pass
    psutil.wait_procs(family, timeout=5)
    PID_FILE.unlink(missing_ok=True)
    PORT_FILE.unlink(missing_ok=True)
    logger.info("Zalo bridge stopped")


def start_supervisor(interval_secs: float = 30.0) -> threading.Event:
    """Keep the bridge up in a daemon thread; set the returned event to stop supervising.

    A bridge left over from an earlier run (backend killed without cleanup, or started from the UI)
    is replaced, so restarting the backend always runs the current bridge code.
    """
    stopped = threading.Event()

    def loop():
        try:
            stop()
        except Exception as exc:
            logger.warning("Could not stop old Zalo bridge: %s", exc)
        while not stopped.is_set():
            try:
                ensure(20.0)
            except Exception as exc:
                logger.warning("Zalo bridge supervisor: %s", exc)
            stopped.wait(interval_secs)

    threading.Thread(target=loop, name="zalo-bridge", daemon=True).start()
    return stopped
