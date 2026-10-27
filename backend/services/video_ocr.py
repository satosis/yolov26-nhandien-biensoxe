"""Bounded, local video jobs reusable by OCR adapters; no DB/camera side effects.

The analyzer factory provides observe(frame, pts), recognize_crop(crop), is_valid
and optional diagnostics. Image preparation and temporal aggregation are shared
with still-image and document OCR through backend.util.ocr_pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import math
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

import cv2

from backend.util.ocr_pipeline import OCRTrackAggregator
from backend.util.ocr_utils import prepare_plate_preview
from backend.services.video_ocr_ipc import read_json, write_json_atomic

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "cancelled", "failed"}


class _VideoFrameReader:
    """Overlap local-file decoding with inference using a bounded FIFO.

    Only the decoder thread touches VideoCapture after setup. Each queued frame
    carries its own source timestamp; detailed analysis never drops a frame.
    ``prefetch=0`` retains the sequential path for constrained deployments and
    comparisons. Cancellation interrupts queue waits, not a native decode call.
    """

    def __init__(self, capture, cancel, max_pixels, prefetch=2):
        self.capture, self.cancel, self.max_pixels = capture, cancel, max_pixels
        self.decode_seconds = 0.0
        self._stop = threading.Event()
        self._queue = Queue(maxsize=prefetch) if prefetch else None
        self._thread = None
        if self._queue is not None:
            self._thread = threading.Thread(target=self._produce, name="video-decode", daemon=True)
            self._thread.start()

    def _decode(self):
        started = time.perf_counter()
        try:
            ok, frame = self.capture.read()
            if not ok:
                return False, None, 0.0
            height, width = frame.shape[:2]
            if height * width > self.max_pixels:
                raise ValueError("Khung hình quá lớn. Hãy xuất video dưới 16 megapixel mỗi frame.")
            timestamp = float(self.capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000
            return True, frame, timestamp
        finally:
            self.decode_seconds += time.perf_counter() - started

    def _put(self, item):
        while not self._stop.is_set() and not self.cancel.is_set():
            try:
                self._queue.put(item, timeout=0.05)
                return True
            except Full:
                continue
        return False

    def _produce(self):
        try:
            while not self._stop.is_set() and not self.cancel.is_set():
                item = self._decode()
                if not self._put(item) or not item[0]:
                    return
        except Exception as error:
            self._put(error)

    def read(self):
        if self._queue is None:
            return self._decode()
        while not self._stop.is_set() and not self.cancel.is_set():
            try:
                item = self._queue.get(timeout=0.05)
            except Empty:
                if not self._thread.is_alive():
                    return False, None, 0.0
                continue
            if isinstance(item, Exception):
                raise item
            return item
        return False, None, 0.0

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()


@dataclass
class VideoOCRJob:
    job_id: str
    owner: str
    site: str
    directory: Path
    source: Path
    mode: str = "detailed"
    roi: list | None = None
    status: str = "uploading"
    metadata: dict = field(default_factory=dict)
    progress: dict = field(default_factory=lambda: {
        "processed_frames": 0, "analyzed_frames": 0, "total_frames": 0,
        "current_time": 0., "duration": 0.,
    })
    events: list = field(default_factory=list)
    message: str = "Đang tải video lên."
    error: str | None = None
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


class VideoOCRJobs:
    def __init__(self, directory, analyzer_factory=None, *, normalize=None, max_active=3,
                 retention_seconds=3600, max_duration=3600, max_pixels=16_777_216,
                 max_events=500, max_history=24, capture_factory=None, decode_prefetch=2,
                 process_isolation=False, cpu_workers=2):
        if isinstance(decode_prefetch, bool) or not isinstance(decode_prefetch, int) or not 0 <= decode_prefetch <= 4:
            raise ValueError("decode_prefetch must be an integer from 0 to 4")
        if isinstance(cpu_workers, bool) or not isinstance(cpu_workers, int) or not 1 <= cpu_workers <= 256:
            raise ValueError("cpu_workers must be an integer from 1 to 256")
        if process_isolation and any(value is not None for value in (analyzer_factory, normalize, capture_factory)):
            raise ValueError("Isolated video uses the child process's own plate analyzer")
        if not process_isolation and not callable(analyzer_factory):
            raise ValueError("An analyzer_factory is required for in-process video adapters")
        self.directory = Path(directory).resolve()
        self.analyzer_factory = analyzer_factory
        self.normalize = normalize
        self.max_active, self.retention = max_active, retention_seconds
        self.max_duration, self.max_pixels, self.max_events = max_duration, max_pixels, max_events
        self.max_history = max(max_active, max_history)
        self.capture_factory = capture_factory or cv2.VideoCapture
        self.decode_prefetch = decode_prefetch
        self.process_isolation, self.cpu_workers = bool(process_isolation), cpu_workers
        self._processes = {}
        self._cancel_grace_seconds = 1.5
        self._heartbeat_timeout = 120.
        self.jobs = {}
        self.lock = threading.RLock()
        # A removable bounded queue avoids cancelled executor work accumulating
        # behind a slow inference call. Start the inference thread only on demand.
        self._condition = threading.Condition(self.lock)
        self._queue = []
        self._worker = None
        self._closed = False
        self._maintenance_stop = threading.Event()
        self._cleanup_orphans()
        self._maintenance = threading.Thread(target=self._maintain, name="ocr-retention", daemon=True)
        self._maintenance.start()

    def _cleanup_orphans(self):
        if not self.directory.is_dir():
            return
        for path in self.directory.iterdir():
            if (path.is_dir() and not path.is_symlink() and re.fullmatch(r"[0-9a-f]{32}", path.name)
                    and path.name not in self.jobs
                    and path.resolve().parent == self.directory
                    and time.time() - path.stat().st_mtime >= self.retention):
                shutil.rmtree(path, ignore_errors=True)

    def _maintain(self):
        while not self._maintenance_stop.wait(30):
            with self.lock:
                self.cleanup()
                self._cleanup_orphans()

    def has_active_analysis(self):
        with self.lock:
            return any(job.status == "running" for job in self.jobs.values())

    def _work(self):
        while True:
            with self._condition:
                while not self._queue and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                job = self._queue.pop(0)
            try:
                if self.process_isolation:
                    self._run_process(job)
                else:
                    self._run(job)
            except Exception:
                # File cleanup errors must not permanently kill the only worker.
                logger.exception("Unexpected OCR worker failure for %s", job.job_id)
                with job.lock:
                    job.status, job.error = "failed", "Không thể hoàn tất tác vụ video."
                    job.message, job.updated = job.error, time.time()

    def reserve(self, owner, site, filename, *, mode="detailed", roi=None):
        if mode not in {"detailed", "fast", "realtime"}:
            raise ValueError("Chế độ phân tích không hợp lệ.")
        suffix = Path(filename or "").suffix.lower()
        if suffix not in {".mov", ".mp4", ".webm", ".mkv", ".avi", ".m4v", ".ogg"}:
            raise ValueError("Hãy chọn video MOV, MP4, WebM, MKV hoặc AVI.")
        with self.lock:
            if self._closed:
                raise RuntimeError("Bộ xử lý video đang dừng. Hãy thử lại sau.")
            self.cleanup()
            if sum(j.status not in TERMINAL for j in self.jobs.values()) >= self.max_active:
                raise RuntimeError("Hàng đợi đang đầy. Hãy chờ hoặc hủy tác vụ trước.")
            while len(self.jobs) >= self.max_history:
                old = min((j for j in self.jobs.values() if j.status in TERMINAL),
                          key=lambda j: j.updated, default=None)
                if old is None:
                    raise RuntimeError("Hàng đợi đang đầy. Hãy thử lại sau.")
                self.jobs.pop(old.job_id)
                self._remove_directory(old)
            job_id = uuid.uuid4().hex
            directory = self.directory / job_id
            directory.mkdir(parents=True, exist_ok=False)
            job = VideoOCRJob(job_id, str(owner), str(site), directory,
                              directory / ("source" + suffix), mode=mode, roi=roi)
            self.jobs[job_id] = job
            return job

    def enqueue(self, job):
        with self._condition, job.lock:
            if self._closed:
                raise RuntimeError("Bộ xử lý video đang dừng.")
            if job.status != "uploading":
                raise ValueError("Tác vụ đã được gửi xử lý.")
            job.status, job.message = "queued", "Đã tải video. Đang chờ phân tích."
            self._queue.append(job)
            if self._worker is None:
                self._worker = threading.Thread(target=self._work, name="video-ocr", daemon=True)
                self._worker.start()
            self._condition.notify()

    def get(self, job_id, owner, site):
        with self.lock:
            self.cleanup()
            job = self.jobs.get(job_id)
            if not job or job.owner != str(owner) or job.site != str(site):
                return None
            return job

    def latest(self, owner, site):
        """Return an active job first, otherwise the newest owner/site results."""
        with self.lock:
            self.cleanup()
            return max((job for job in self.jobs.values()
                        if job.owner == str(owner) and job.site == str(site)),
                       key=lambda job: (job.status not in TERMINAL, job.created), default=None)

    def snapshot(self, job):
        with job.lock:
            return {"job_id": job.job_id, "status": job.status, "mode": job.mode,
                    "metadata": dict(job.metadata), "progress": dict(job.progress),
                    "events": list(job.events), "message": job.message,
                    "error": job.error}

    def cancel_job(self, job):
        with self._condition, job.lock:
            if job.status in TERMINAL:
                return
            job.cancel.set()
            if self.process_isolation and job.status == "running":
                try:
                    (job.directory / "cancel.request").touch(exist_ok=True)
                except OSError:
                    logger.warning("Could not write video cancellation marker for %s; supervisor will stop it", job.job_id)
            job.message = "Đang dừng sau bước xử lý hiện tại."
            if job.status in {"uploading", "queued"}:
                self._queue = [item for item in self._queue if item is not job]
                job.source.unlink(missing_ok=True)
                job.status, job.message = "cancelled", "Đã hủy phân tích video."
                job.updated = time.time()

    def discard(self, job):
        with self.lock:
            self.jobs.pop(job.job_id, None)
            self._remove_directory(job)

    def _remove_directory(self, job):
        target = job.directory.resolve()
        if target.parent != self.directory or not re.fullmatch(r"[0-9a-f]{32}", target.name):
            raise ValueError("Invalid OCR job directory")
        shutil.rmtree(target, ignore_errors=True)

    def cleanup(self):
        with self.lock:
            now = time.time()
            for key, job in list(self.jobs.items()):
                if job.status in TERMINAL and now - job.updated >= self.retention:
                    self.jobs.pop(key, None)
                    self._remove_directory(job)

    def close(self):
        self._maintenance_stop.set()
        with self._condition:
            for job in list(self.jobs.values()):
                if job.status not in TERMINAL:
                    self.cancel_job(job)
            self._closed = True
            self._queue.clear()
            self._condition.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=6. if self.process_isolation else None)
            if self.process_isolation and self._worker.is_alive():
                with self.lock:
                    processes = list(self._processes.values())
                for process in processes:
                    self._stop_process(process)
                self._worker.join(timeout=2.)
        self._maintenance.join()

    @staticmethod
    def _stop_process(process):
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            return
        try:
            process.wait(timeout=1.)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                return
            try:
                process.wait(timeout=1.)
            except subprocess.TimeoutExpired:
                logger.error("Video worker %s did not exit after kill", process.pid)

    def _process_command(self, manifest):
        return [sys.executable, "-m", "backend.services.video_ocr_worker", str(manifest)]

    def _apply_process_snapshot(self, job, payload):
        if not isinstance(payload, dict) or payload.get("job_id") != job.job_id:
            return False
        state = payload.get("status")
        if state not in TERMINAL | {"running"}:
            return False
        events, metadata, progress = payload.get("events"), payload.get("metadata"), payload.get("progress")
        if not isinstance(events, list) or len(events) > self.max_events or not isinstance(metadata, dict) or not isinstance(progress, dict):
            return False
        with job.lock:
            job.events = events
            job.metadata.update(metadata)
            job.progress.update(progress)
            # A terminal state is public only after the child has finished its
            # decoder/model cleanup and committed the final snapshot.
            job.status = state if payload.get("worker_done") else "running"
            job.message = str(payload.get("message") or job.message)
            job.error = payload.get("error")
            job.updated = time.time()
        return True

    def _run_process(self, job):
        """Supervise an independent model process without holding web locks."""
        process = None
        manifest = job.directory / "worker_manifest.json"
        status_file = job.directory / "worker_status.json"
        cancel_file = job.directory / "cancel.request"
        final_received = False
        last_signature = None
        last_heartbeat = time.monotonic()
        cancel_started = None
        try:
            with job.lock:
                if job.cancel.is_set():
                    return
                job.status = "running"
                job.message = "Đang khởi động bộ phân tích video riêng."
            write_json_atomic(manifest, {
                "job_id": job.job_id, "directory": str(job.directory), "source": str(job.source),
                "mode": job.mode, "roi": job.roi, "parent_pid": os.getpid(),
                "limits": {"max_duration": self.max_duration, "max_pixels": self.max_pixels,
                           "max_events": self.max_events, "decode_prefetch": self.decode_prefetch,
                           "cpu_workers": self.cpu_workers},
            })
            options = {"cwd": str(Path(__file__).resolve().parents[2]), "close_fds": True}
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            with (job.directory / "worker.log").open("ab", buffering=0) as output:
                process = subprocess.Popen(self._process_command(manifest), stdout=output, stderr=subprocess.STDOUT, **options)
                with self.lock:
                    self._processes[job.job_id] = process
                with job.lock:
                    job.metadata.update(execution="process", worker_pid=process.pid, cpu_workers=self.cpu_workers)
                while True:
                    try:
                        stat = status_file.stat()
                        signature = (stat.st_mtime_ns, stat.st_size)
                        if signature != last_signature:
                            payload = read_json(status_file)
                            if self._apply_process_snapshot(job, payload):
                                last_signature = signature
                                last_heartbeat = time.monotonic()
                                final_received = bool(payload.get("worker_done"))
                    except (OSError, ValueError, TypeError):
                        # Atomic writes prevent partial JSON normally; tolerate
                        # transient antivirus/file locks without blocking HTTP.
                        pass
                    if process.poll() is not None:
                        # Read once more after exit so a fast final atomic write
                        # cannot be missed between the last stat and poll.
                        try:
                            payload = read_json(status_file)
                            if self._apply_process_snapshot(job, payload):
                                final_received = bool(payload.get("worker_done"))
                        except (OSError, ValueError, TypeError):
                            pass
                        break
                    if job.cancel.is_set():
                        cancel_file.touch(exist_ok=True)
                        cancel_started = cancel_started or time.monotonic()
                        if time.monotonic() - cancel_started >= self._cancel_grace_seconds:
                            self._stop_process(process)
                            break
                    elif time.monotonic() - last_heartbeat > self._heartbeat_timeout:
                        self._stop_process(process)
                        raise RuntimeError("Video worker stopped reporting progress")
                    # Event.wait would spin after cancellation is set, stealing
                    # frontend CPU during the graceful shutdown interval.
                    time.sleep(.1)
                with job.lock:
                    if job.cancel.is_set():
                        job.status, job.message = "cancelled", "Đã hủy; giữ lại các kết quả đã xử lý."
                        job.error = None
                    elif not final_received or process.returncode != 0 or job.status not in TERMINAL:
                        if job.status != "failed":
                            job.error = "Bộ phân tích video đã dừng bất ngờ. Hãy thử lại tác vụ."
                        job.status, job.message = "failed", job.error
        except Exception:
            logger.exception("Isolated video job %s failed", job.job_id)
            with job.lock:
                job.status = "cancelled" if job.cancel.is_set() else "failed"
                job.error = None if job.cancel.is_set() else "Không khởi chạy được bộ phân tích video riêng."
                job.message = "Đã hủy phân tích video." if job.cancel.is_set() else job.error
        finally:
            if process is not None:
                self._stop_process(process)
            with self.lock:
                self._processes.pop(job.job_id, None)
            job.source.unlink(missing_ok=True)
            with job.lock:
                job.updated = time.time()
                if job.cancel.is_set() and job.status not in TERMINAL:
                    job.status, job.message = "cancelled", "Đã hủy phân tích video."

    def _publish(self, job, events):
        for event in events:
            if len(job.events) >= self.max_events:
                raise ValueError("Video có quá nhiều sự kiện. Hãy chia thành các đoạn ngắn hơn.")
            crop = event.pop("best_crop", None)
            event_id = uuid.uuid4().hex
            crop_url = None
            if crop is not None and crop.size:
                h, w = crop.shape[:2]
                scale = min(1., 1024 / max(h, w))
                if scale < 1:
                    crop = cv2.resize(crop, (max(1, round(w * scale)), max(1, round(h * scale))))
                display_crop = (prepare_plate_preview(crop)
                                if event.get("type", "plate") == "plate" else crop)
                filename = event_id + ".jpg"
                if not cv2.imwrite(str(job.directory / filename), display_crop,
                                   [cv2.IMWRITE_JPEG_QUALITY, 95]):
                    raise OSError("Không lưu được ảnh biển số.")
                crop_url = f"/admin/detection/api/videos/{job.job_id}/assets/{filename}"
            public = {
                "id": event_id, "type": event.get("type", "plate"), "track_id": event.get("track_id"),
                "text": event.get("text", ""), "status": event.get("status", "unreadable"),
                "confidence": round(float(event.get("confidence", 0)) * 100, 1),
                "ocr_confidence": round(float(event.get("confidence", 0)) * 100, 1),
                "bbox": event.get("bbox", []), "start_time": event.get("start_time", 0),
                "end_time": event.get("end_time", 0), "crop_url": crop_url,
                "observations": event.get("observations", 0),
                "candidates": event.get("candidates", []),
            }
            with job.lock:
                job.events.append(public)

    def _run(self, job):
        cap = None
        frame_reader = None
        tracker = None
        people_tracker = None
        started = time.perf_counter()
        last_report = started
        decoded = analyzed = 0
        last_pts = -1.
        first_pts = None
        fps = 30.
        timings = {"model_load_seconds": 0., "detection_seconds": 0., "ocr_seconds": 0., "tracking_seconds": 0.}

        def report_performance(force=False):
            nonlocal last_report
            now = time.perf_counter()
            if not force and now - last_report < .25:
                return
            last_report = now
            elapsed = max(1e-6, now - started)
            source_seconds = max(0., last_pts - first_pts) + 1 / fps if first_pts is not None else 0.
            with job.lock:
                job.metadata.update(
                    elapsed_seconds=round(elapsed, 3),
                    processing_fps=round(decoded / elapsed, 3),
                    analysis_fps=round(analyzed / elapsed, 3),
                    realtime_factor=round(source_seconds / elapsed, 3),
                    decode_seconds=round(frame_reader.decode_seconds if frame_reader else 0., 3),
                    **{key: round(value, 3) for key, value in timings.items()},
                )

        def recognize(crop):
            if job.cancel.is_set():
                return {"text": "", "confidence": 0.}
            before = time.perf_counter()
            try:
                return analyzer.recognize_crop(crop)
            finally:
                timings["ocr_seconds"] += time.perf_counter() - before

        def recognize_track(crops):
            method = getattr(analyzer, "recognize_track", None)
            if job.cancel.is_set() or not callable(method):
                return None
            before = time.perf_counter()
            try:
                return method(crops)
            finally:
                timings["ocr_seconds"] += time.perf_counter() - before

        def aggregate(operation, *args, people=False):
            before = time.perf_counter()
            previous_ocr = timings["ocr_seconds"]
            events = operation(*args)
            if people:
                for event in events:
                    event.update(type="person", text="Người", status="detected")
            timings["tracking_seconds"] += max(0., time.perf_counter() - before - (timings["ocr_seconds"] - previous_ocr))
            self._publish(job, events)

        try:
            with job.lock:
                if job.cancel.is_set():
                    return
                job.status, job.message = "running", "Đang đọc video gốc và tải bộ nhận diện."
            cap = self.capture_factory(str(job.source))
            if not cap.isOpened():
                raise ValueError("Không giải mã được video. Hãy xuất lại thành MP4/H.264 và thử lại.")
            if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
                cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            fps = fps if math.isfinite(fps) and 0 < fps <= 1000 else 30.
            count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            total = int(count) if math.isfinite(count) and count > 0 else 0
            estimated_duration = total / fps
            if estimated_duration > self.max_duration:
                raise ValueError("Video quá dài. Hãy chia thành các đoạn tối đa 60 phút.")
            fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
            with job.lock:
                job.metadata = {"fps": fps, "duration": estimated_duration,
                                "codec": "".join(chr((fourcc >> (8 * i)) & 255) for i in range(4)),
                                "prefetch_frames": self.decode_prefetch,
                                "target_fps": min(fps, 8.) if job.mode == "realtime" else min(fps, 5.) if job.mode == "fast" else fps,
                                "classes": ["person", "plate"]}
                job.progress.update(total_frames=total, duration=estimated_duration)
            frame_reader = _VideoFrameReader(cap, job.cancel, self.max_pixels, self.decode_prefetch)
            report_performance(force=True)
            before = time.perf_counter()
            analyzer = self.analyzer_factory(job)
            timings["model_load_seconds"] = time.perf_counter() - before
            tracker = OCRTrackAggregator(
                normalize=self.normalize, validator=analyzer.is_valid,
                recognize=recognize, recognize_track=recognize_track,
                max_gap_seconds=1., max_crops=3 if job.mode == "realtime" else 5,
                max_tracks=64,
            )
            people_tracker = OCRTrackAggregator(max_gap_seconds=1., max_crops=1, max_tracks=32)
            next_sample = 0.
            analysis_started = time.perf_counter()
            while not job.cancel.is_set():
                ok, frame, pts = frame_reader.read()
                if not ok:
                    break
                h, w = frame.shape[:2]
                if h * w > self.max_pixels:
                    raise ValueError("Khung hình quá lớn. Hãy xuất video dưới 16 megapixel mỗi frame.")
                if not math.isfinite(pts) or pts < 0 or (decoded > 0 and pts <= last_pts):
                    pts = max(decoded / fps, last_pts + 1 / fps)
                    with job.lock:
                        job.metadata["timestamp_fallback"] = True
                if pts > self.max_duration:
                    raise ValueError("Video quá dài. Hãy chia thành các đoạn tối đa 60 phút.")
                last_pts = pts
                if first_pts is None:
                    first_pts = pts
                decoded += 1
                with job.lock:
                    job.metadata.update(width=w, height=h)
                    job.progress.update(processed_frames=decoded, current_time=pts)
                if job.mode in {"fast", "realtime"} and pts + 1e-6 < next_sample:
                    report_performance()
                    continue
                next_sample = pts + .2
                before = time.perf_counter()
                observations = analyzer.observe(frame, pts)
                timings["detection_seconds"] += time.perf_counter() - before
                plates = [item for item in observations if item.get("type", "plate") == "plate"]
                people = [item for item in observations if item.get("type") == "person"]
                # Publish current people/plate boxes before deferred OCR. OCR on
                # a retired track must not hide this frame's person detections.
                with job.lock:
                    job.progress.update(detection_time=pts, detections=[
                        {"type": item.get("type", "plate"), "bbox": item["bbox"],
                         "text": "Người" if item.get("type") == "person" else "Biển số",
                         "confidence": round(item.get("detector_confidence", 0.) * 100, 1)}
                        for item in observations if item.get("type", "plate") in {"person", "plate"}
                    ][:96])
                aggregate(tracker.update, pts, plates)
                aggregate(people_tracker.update, pts, people, people=True)
                analyzed += 1
                if job.mode == "realtime":
                    # Follow the source timeline at approximately wall-clock
                    # speed. Expensive detection/OCR never builds an inference
                    # backlog: decode every frame, analyze only those due.
                    # No accumulated startup/model-load time is skipped.
                    next_sample = max(pts + 1 / min(fps, 8.),
                                      first_pts + (time.perf_counter() - analysis_started) * 1.10)
                with job.lock:
                    job.progress["analyzed_frames"] = analyzed
                    job.metadata["sampled_fps"] = round(analyzed / max(1 / fps, pts - first_pts + 1 / fps), 3)
                    job.message = "Đang tìm người và biển số; kết quả được giữ theo thời gian xuất hiện."
                    job.updated = time.time()
                report_performance()
            if not decoded and not job.cancel.is_set():
                raise ValueError("Video không có frame giải mã được.")
            if not job.cancel.is_set() and total and decoded < total * .98:
                raise ValueError("Video bị ngắt hoặc lỗi giải mã trước khi đọc hết. Hãy kiểm tra file gốc.")
            if tracker is not None:
                with job.lock:
                    job.message = "Đang OCR và tổng hợp các ảnh rõ nhất."
                aggregate(tracker.finish)
                aggregate(people_tracker.finish, people=True)
            report_performance(force=True)
            with job.lock:
                job.status = "cancelled" if job.cancel.is_set() else "completed"
                job.metadata.update(detected_frames=analyzer.detected_frames, ocr_calls=analyzer.ocr_calls)
                job.metadata["dropped_observations"] = tracker.dropped_observations
                if not total:
                    job.progress["total_frames"] = decoded
                if not estimated_duration:
                    job.progress["duration"] = max(0, last_pts) + 1 / fps
                    job.metadata["duration"] = job.progress["duration"]
                job.message = ("Đã hủy; giữ lại các kết quả đã xử lý." if job.cancel.is_set() else
                               "Đã phân tích xong." if job.events else
                               "Đã đọc hết video, chưa tìm thấy người hoặc biển số. Hãy thử khoanh vùng cần quét.")
        except Exception as exc:
            logger.exception("Video OCR job %s failed", job.job_id)
            with job.lock:
                job.status, job.error = "failed", str(exc) if isinstance(exc, ValueError) else "Không thể xử lý video. Hãy thử lại hoặc kiểm tra cấu hình OCR."
                job.message = job.error
        finally:
            if frame_reader is not None:
                frame_reader.close()
            if cap is not None:
                cap.release()
            report_performance(force=True)
            try:
                job.source.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove OCR source for %s; retention will retry", job.job_id)
            with job.lock:
                job.updated = time.time()
                if job.cancel.is_set() and job.status not in TERMINAL:
                    job.status, job.message = "cancelled", "Đã hủy phân tích video."
