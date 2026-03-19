"""
parking_hpc/main.py
Orchestrator — entry point for the high-performance parking monitor.

Startup sequence:
  1. Force CPU governor to 'performance' on all cores (RK3399: 6 cores)
  2. Optionally set up ZRAM (if not already active)
  3. Allocate SharedMemory segments for frame IPC
  4. Spawn Process 1 (grabber) × N cameras
  5. Spawn Process 2 (inference)
  6. Spawn Process 3 (UI server)
  7. Monitor child processes; restart on unexpected exit
  8. Graceful shutdown on SIGINT/SIGTERM

Run:
    python -m parking_hpc.main
    # or
    python parking_hpc/main.py
"""
import os
import sys
import time
import signal
import logging
import multiprocessing as mp
from multiprocessing import Queue, Event

from streamlit_app.engine import config as cfg
from streamlit_app.engine.grabber import grabber_process
from streamlit_app.engine.inference import inference_process
from streamlit_app.engine.ui_server import ui_process

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


# ── Hardware setup ────────────────────────────────────────────────────────────

def set_cpu_performance():
    """Force all CPU cores to performance governor (requires root or sudo)."""
    try:
        allowed_governors = {"performance", "powersave", "ondemand", "conservative", "schedutil"}
        governor = cfg.CPU_GOVERNOR if cfg.CPU_GOVERNOR in allowed_governors else "performance"
        cpu_count = os.cpu_count() or 6
        for i in range(cpu_count):
            path = f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_governor"
            if os.path.exists(path):
                import subprocess
                subprocess.run(["sudo", "tee", path], input=governor.encode(),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        logger.info("CPU governor set to '%s' on %d cores", governor, cpu_count)
    except Exception as e:
        logger.warning("Could not set CPU governor: %s", e)


def setup_zram():
    """Enable ZRAM swap if not already active (prevents OOM on full load)."""
    # ZRAM with LZ4 compression overhead may impact performance dynamically on RK3399
    # so we disable it for now according to the optimization plan.
    logger.info("ZRAM disabled for better dynamic performance on RK3399")
    return


# ── Process management ────────────────────────────────────────────────────────

def _spawn_grabber(cam_id, rtsp_url, shm_name, infer_queue, stop_event) -> mp.Process:
    p = mp.Process(
        target=grabber_process,
        args=(cam_id, rtsp_url, shm_name, infer_queue, stop_event),
        name=f"grabber-{cam_id}",
        daemon=True,
    )
    p.start()
    return p


def _spawn_inference(infer_queue, result_queue, stop_event) -> mp.Process:
    p = mp.Process(
        target=inference_process,
        args=(infer_queue, result_queue, stop_event),
        name="inference",
        daemon=True,
    )
    p.start()
    return p


def _spawn_ui(result_queue, stop_event) -> mp.Process:
    p = mp.Process(
        target=ui_process,
        args=(result_queue, stop_event),
        name="ui_server",
        daemon=True,
    )
    p.start()
    return p


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Use 'spawn' start method — safer for CUDA/OpenCL contexts
    mp.set_start_method("spawn", force=True)

    logger.info("=== Parking HPC Monitor starting ===")
    set_cpu_performance()
    setup_zram()

    stop_event = Event()

    # Queues
    infer_queue: Queue = Queue(maxsize=cfg.INFER_QUEUE_MAXSIZE)
    result_queue: Queue = Queue(maxsize=cfg.RESULT_QUEUE_MAXSIZE)

    # Build camera list dynamically from Database
    from streamlit_app.controllers.camera_controller import camera_controller

    db_cameras = camera_controller.get_all_cameras()

    cameras = []
    cam_index = 1
    for cam in db_cameras:
        if cam.get("is_active") and cam.get("rtsp_url"):
            # generate unique SHM name based on cam index
            shm_name = f"hpc_cam{cam_index}_frame"
            cameras.append((f"cam{cam_index}", cam["rtsp_url"], shm_name))
            cam_index += 1
            
    if not cameras:
        logger.warning("No active cameras found in database. Please configure cameras in Camera Management view.")

    # Spawn processes
    grabbers = [
        _spawn_grabber(cam_id, url, shm, infer_queue, stop_event)
        for cam_id, url, shm in cameras
    ]
    infer_proc = _spawn_inference(infer_queue, result_queue, stop_event)
    ui_proc = _spawn_ui(result_queue, stop_event)

    all_procs = grabbers + [infer_proc, ui_proc]
    logger.info(
        "Spawned %d grabber(s) + inference + UI. Dashboard → http://0.0.0.0:%d",
        len(grabbers), cfg.UI_PORT,
    )

    # Graceful shutdown handler
    def _shutdown(signum, frame):
        logger.info("Shutdown signal received — stopping all processes…")
        stop_event.set()

        # Cleanup shared memory
        from multiprocessing import shared_memory
        for cam in cameras:
            try:
                shm = shared_memory.SharedMemory(name=cam[2])
                shm.close()
                shm.unlink()
                logger.info("Unlinked SHM: %s", cam[2])
            except FileNotFoundError:
                pass

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Watchdog loop — restart crashed processes
    restart_counts: dict[str, int] = {}
    MAX_RESTARTS = 5

    while not stop_event.is_set():
        time.sleep(2)
        for i, p in enumerate(list(grabbers)):
            if not p.is_alive() and not stop_event.is_set():
                cam_id, url, shm = cameras[i]
                key = f"grabber-{cam_id}"
                restart_counts[key] = restart_counts.get(key, 0) + 1
                if restart_counts[key] > MAX_RESTARTS:
                    logger.error("%s crashed too many times — giving up", key)
                    continue
                logger.warning("%s died (exit %s) — restarting (#%d)",
                               key, p.exitcode, restart_counts[key])

                # Cleanup potential orphaned shared memory
                try:
                    from multiprocessing import shared_memory
                    old_shm = shared_memory.SharedMemory(name=shm)
                    old_shm.close()
                    old_shm.unlink()
                    logger.info("Cleaned orphaned SHM: %s before restart", shm)
                except FileNotFoundError:
                    pass

                new_p = _spawn_grabber(cam_id, url, shm, infer_queue, stop_event)
                grabbers[i] = new_p
                all_procs[i] = new_p

        if not infer_proc.is_alive() and not stop_event.is_set():
            key = "inference"
            restart_counts[key] = restart_counts.get(key, 0) + 1
            if restart_counts[key] <= MAX_RESTARTS:
                logger.warning("inference died — restarting (#%d)", restart_counts[key])
                infer_proc = _spawn_inference(infer_queue, result_queue, stop_event)

    # Teardown
    logger.info("Waiting for processes to exit…")
    for p in all_procs:
        p.join(timeout=5)
        if p.is_alive():
            p.terminate()
    logger.info("All processes stopped. Bye.")


if __name__ == "__main__":
    main()
