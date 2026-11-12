"""Dedicated video OCR process; imports models only after resource limits.

Run with ``python -m backend.services.video_ocr_worker manifest.json``. The web
process only exchanges bounded JSON snapshots and a cancellation marker with
this process. ``--probe`` reports the child resource profile without loading AI.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import threading
import time
import traceback

from backend.services.video_ocr_ipc import read_json, write_json_atomic


def resource_profile(cpu_workers=2):
    """Limit this child, never the web process, with portable fallbacks."""
    requested = int(os.environ.get("VIDEO_OCR_CPU_WORKERS", cpu_workers))
    if not 1 <= requested <= 256:
        raise ValueError("VIDEO_OCR_CPU_WORKERS must be between 1 and 256")
    affinity = None
    priority_applied = affinity_applied = False
    try:
        import psutil
        process = psutil.Process()
        try:
            available = process.cpu_affinity()
            affinity = available[-min(requested, len(available)):]
            process.cpu_affinity(affinity)
            affinity_applied = True
        except (AttributeError, OSError, psutil.Error):
            affinity = None
        try:
            process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
            priority_applied = True
        except (AttributeError, OSError, psutil.Error):
            pass
    except ImportError:
        pass
    if affinity is None and hasattr(os, "sched_getaffinity"):
        try:
            available = sorted(os.sched_getaffinity(0))
            affinity = available[-min(requested, len(available)):]
            os.sched_setaffinity(0, affinity)
            affinity_applied = True
        except OSError:
            affinity = None
    if os.name == "nt" and (not affinity_applied or not priority_applied):
        # psutil is a declared dependency, but retain a stdlib Windows fallback.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        handle = kernel.GetCurrentProcess()
        kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        if not priority_applied:
            priority_applied = bool(kernel.SetPriorityClass(handle, 0x00004000))
        if not affinity_applied:
            process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
            kernel.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
            if kernel.GetProcessAffinityMask(handle, ctypes.byref(process_mask), ctypes.byref(system_mask)):
                available = [index for index in range(ctypes.sizeof(process_mask) * 8) if process_mask.value & (1 << index)]
                selected = available[-min(requested, len(available)):]
                kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
                affinity_applied = bool(kernel.SetProcessAffinityMask(handle, sum(1 << index for index in selected)))
                if affinity_applied:
                    affinity = selected
    if not priority_applied and hasattr(os, "nice"):
        try:
            os.nice(10)
            priority_applied = True
        except OSError:
            pass
    workers = min(requested, len(affinity) if affinity else (os.cpu_count() or 1))
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS", "OPENCV_FOR_THREADS_NUM", "VIDEO_OCR_NATIVE_THREADS"):
        os.environ[name] = str(workers)
    return {
        "execution": "process", "worker_pid": os.getpid(), "cpu_workers": workers,
        "cpu_affinity": affinity, "cpu_affinity_applied": affinity_applied,
        "priority": "below_normal" if os.name == "nt" else "nice_10",
        "priority_applied": priority_applied, "native_threads": workers,
    }


class FileCancellation:
    def __init__(self, path):
        self.path = Path(path)

    def is_set(self):
        return self.path.exists()

    def set(self):
        self.path.touch(exist_ok=True)


class LazyVideoOCRReader:
    """Load OCR only when a retained plate actually needs reading."""

    def __init__(self, factory):
        self.factory, self.reader = factory, None

    def _get(self):
        if self.reader is None:
            self.reader = self.factory()
        return self.reader

    @property
    def _ocr(self):
        return self._get()._ocr

    def read(self, crop):
        return self._get().read(crop)

    def read_quick(self, crop):
        return self._get().read_quick(crop)


def parent_is_alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not death.
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False


def _watch_parent(pid, stop):
    while not stop.wait(.5):
        if not parent_is_alive(pid):
            # The web process can no longer own/cancel this job. Terminate even
            # if a native model call is stuck; retention cleans residual files.
            os._exit(2)


def run_manifest(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json(manifest_path, max_bytes=64 * 1024)
    directory, source = Path(manifest["directory"]).resolve(), Path(manifest["source"]).resolve()
    job_id = manifest["job_id"]
    if (directory != manifest_path.parent or directory.name != job_id
            or not re.fullmatch(r"[0-9a-f]{32}", job_id) or source.parent != directory
            or not source.name.startswith("source.")):
        raise ValueError("Invalid isolated video job paths")
    limits = manifest.get("limits", {})
    profile = resource_profile(limits.get("cpu_workers", 2))
    parent_stop = threading.Event()
    parent_pid = manifest.get("parent_pid")
    if parent_pid is not None:
        threading.Thread(target=_watch_parent, args=(parent_pid, parent_stop),
                         name="video-parent-watch", daemon=True).start()

    # Heavy dependencies are deliberately imported only after affinity, priority,
    # and native thread limits have been set in this independent process.
    import cv2
    from backend.ai.inference import PlateDetector, OCRReader
    from backend.ai.plate_analysis import PlateFrameAnalyzer, plate_key
    from backend.services.video_ocr import VideoOCRJob, VideoOCRJobs

    cv2.setNumThreads(profile["cpu_workers"])
    job = VideoOCRJob(job_id, "", "", directory, source,
                      mode=manifest.get("mode", "detailed"), roi=manifest.get("roi"))
    job.cancel = FileCancellation(directory / "cancel.request")
    job.status, job.message = "running", "Đang tải mô hình trong bộ phân tích video riêng."
    # Reuse exactly the tested decode/tracking/OCR pipeline without starting
    # another manager, cleanup daemon, queue, or web service in the child.
    runner = VideoOCRJobs.__new__(VideoOCRJobs)
    runner.capture_factory = cv2.VideoCapture
    runner.decode_prefetch = int(limits.get("decode_prefetch", 2))
    runner.normalize = plate_key
    runner.max_duration = limits.get("max_duration", 3600)
    runner.max_pixels = limits.get("max_pixels", 16_777_216)
    runner.max_events = min(int(limits.get("max_events", 500)), 500)

    def analyzer_factory(_):
        return PlateFrameAnalyzer(PlateDetector(), LazyVideoOCRReader(OCRReader), roi=job.roi,
                                  search_small=job.mode != "realtime", include_people=True,
                                  quick_ocr=job.mode == "realtime")

    runner.analyzer_factory = analyzer_factory
    stop = threading.Event()
    writer_errors = []

    def publish(done=False):
        snapshot = runner.snapshot(job)
        snapshot["metadata"].update(profile)
        snapshot["metadata"]["worker_updated"] = time.time()
        snapshot.update(worker_done=done, worker_updated=time.time())
        write_json_atomic(directory / "worker_status.json", snapshot)

    def report():
        while not stop.wait(.2):
            try:
                publish()
            except Exception as error:
                writer_errors.append(error)
                job.cancel.set()
                return

    publish()
    writer = threading.Thread(target=report, name="video-status", daemon=True)
    writer.start()
    try:
        runner._run(job)
    finally:
        stop.set()
        writer.join(timeout=2.)
    if writer_errors:
        raise RuntimeError("Could not publish isolated video progress") from writer_errors[0]
    publish(done=True)
    parent_stop.set()
    return 1 if job.status == "failed" else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--cpu-workers", type=int, default=2)
    args = parser.parse_args()
    if args.probe:
        print(json.dumps(resource_profile(args.cpu_workers)))
        return 0
    if not args.manifest:
        parser.error("manifest is required unless --probe is specified")
    try:
        return run_manifest(args.manifest)
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
