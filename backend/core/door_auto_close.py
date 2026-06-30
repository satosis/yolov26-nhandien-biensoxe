"""
core/door_auto_close.py — Auto-close cửa cuốn khi không còn người

Logic:
  1. Camera detect person (cls_id=1) qua YOLO
  2. Centroid tracker gán ID cho mỗi person, feed vào TripwireTracker
  3. TripwireTracker đếm IN/OUT → person_counter
  4. Khi counter=0 VÀ không detect person → bắt đầu countdown
  5. Countdown hết (default 5 phút) → gọi DoorController.close()
  6. Nếu detect person giữa chừng → reset countdown
"""
import time
import logging
from typing import Dict, List, Tuple, Optional

from backend.core.tripwire import TripwireTracker
from backend.core import config as cfg

logger = logging.getLogger("door_auto_close")


class CentroidTracker:
    """Lightweight IoU-based tracker — assign persistent IDs to detections across frames.
    Designed for ARM: no external dependencies, O(N*M) matching."""

    def __init__(self, max_disappeared: int = 15):
        self._next_id = 0
        self._objects: Dict[int, Tuple[float, float, float, float]] = {}  # id → (x1,y1,x2,y2)
        self._disappeared: Dict[int, int] = {}  # id → frames since last seen
        self._max_disappeared = max_disappeared

    def update(self, detections: List[Tuple[float, float, float, float]]) -> Dict[int, Tuple[float, float, float, float]]:
        """Update tracker with new detections. Returns {track_id: (x1,y1,x2,y2)}."""
        if not detections:
            for oid in list(self._disappeared):
                self._disappeared[oid] += 1
                if self._disappeared[oid] > self._max_disappeared:
                    self._objects.pop(oid, None)
                    self._disappeared.pop(oid, None)
            return dict(self._objects)

        if not self._objects:
            for det in detections:
                self._objects[self._next_id] = det
                self._disappeared[self._next_id] = 0
                self._next_id += 1
            return dict(self._objects)

        # IoU matching
        obj_ids = list(self._objects.keys())
        obj_boxes = list(self._objects.values())
        used_dets = set()
        used_objs = set()

        # Compute IoU matrix and greedy match
        matches = []
        for i, obox in enumerate(obj_boxes):
            best_iou = 0.0
            best_j = -1
            for j, dbox in enumerate(detections):
                if j in used_dets:
                    continue
                iou = self._iou(obox, dbox)
                if iou > best_iou:
                    best_iou = iou
                    best_j = j
            if best_iou > 0.2 and best_j >= 0:
                matches.append((i, best_j, best_iou))

        # Sort by IoU descending, greedy assign
        matches.sort(key=lambda x: -x[2])
        for i, j, _ in matches:
            if i in used_objs or j in used_dets:
                continue
            oid = obj_ids[i]
            self._objects[oid] = detections[j]
            self._disappeared[oid] = 0
            used_objs.add(i)
            used_dets.add(j)

        # Mark unmatched objects as disappeared
        for i in range(len(obj_ids)):
            if i not in used_objs:
                oid = obj_ids[i]
                self._disappeared[oid] += 1
                if self._disappeared[oid] > self._max_disappeared:
                    self._objects.pop(oid, None)
                    self._disappeared.pop(oid, None)

        # Register new detections
        for j in range(len(detections)):
            if j not in used_dets:
                self._objects[self._next_id] = detections[j]
                self._disappeared[self._next_id] = 0
                self._next_id += 1

        return dict(self._objects)

    @staticmethod
    def _iou(a, b):
        x1 = max(a[0], b[0])
        y1 = max(a[1], b[1])
        x2 = min(a[2], b[2])
        y2 = min(a[3], b[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        area_a = (a[2] - a[0]) * (a[3] - a[1])
        area_b = (b[2] - b[0]) * (b[3] - b[1])
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0


class DoorAutoCloseManager:
    """Quản lý tự đóng cửa cuốn khi không còn người.

    Sử dụng:
        manager = DoorAutoCloseManager()
        manager.reload_config(db)

        # Gọi mỗi frame từ InferenceWorker:
        manager.on_frame(camera_id, detections, frame_height)
    """

    def __init__(self):
        self._trackers: Dict[int, CentroidTracker] = {}      # camera_id → tracker
        self._tripwires: Dict[int, TripwireTracker] = {}      # camera_id → tripwire
        self._person_counter: Dict[int, int] = {}             # door_id → net person count
        self._last_person_time: Dict[int, float] = {}         # door_id → last time person detected
        self._empty_since: Dict[int, Optional[float]] = {}    # door_id → timestamp when became empty
        self._door_closed: Dict[int, bool] = {}               # door_id → True if already closed
        self._doors_config: Dict[int, dict] = {}              # door_id → config dict
        self._camera_to_doors: Dict[int, List[int]] = {}      # camera_id → [door_ids]
        self._last_reload = 0.0
        self._reload_interval = 60.0  # Reload config every 60s

    def reload_config(self, db):
        """Load cấu hình cửa có auto_close_enabled từ DB."""
        try:
            doors = db.get_doors_for_auto_close()
            self._doors_config.clear()
            self._camera_to_doors.clear()

            for door in doors:
                door_id = door['id']
                cam_id = door.get('camera_id')
                if not cam_id:
                    continue

                self._doors_config[door_id] = {
                    'name': door['name'],
                    'camera_id': cam_id,
                    'delay_sec': door.get('auto_close_delay_sec', 300),
                    'relay_type': door.get('relay_type', 'http'),
                    'relay_config': door.get('relay_config', {}),
                }

                if cam_id not in self._camera_to_doors:
                    self._camera_to_doors[cam_id] = []
                self._camera_to_doors[cam_id].append(door_id)

                # Initialize state for new doors
                if door_id not in self._person_counter:
                    self._person_counter[door_id] = 0
                    self._last_person_time[door_id] = time.monotonic()
                    self._empty_since[door_id] = None
                    self._door_closed[door_id] = False

                # Initialize tracker for camera
                if cam_id not in self._trackers:
                    self._trackers[cam_id] = CentroidTracker(max_disappeared=15)

            # Prune per-door state for doors removed from config (avoid stale
            # _door_closed pinning a recreated door, and unbounded growth).
            active = set(self._doors_config.keys())
            for d in (self._person_counter, self._last_person_time,
                      self._empty_since, self._door_closed):
                for stale_id in [k for k in d if k not in active]:
                    d.pop(stale_id, None)

            # Get tripwire line ratio from settings
            line_ratio = 0.6
            try:
                ratio_str = db.get_app_setting('DOOR_TRIPWIRE_LINE_RATIO', '0.6')
                line_ratio = float(ratio_str)
            except (ValueError, TypeError):
                pass

            # Create/update tripwires for each camera
            for cam_id in self._camera_to_doors:
                if cam_id not in self._tripwires:
                    self._tripwires[cam_id] = TripwireTracker(
                        line_y_fn=lambda r=line_ratio: int(cfg.GRAB_HEIGHT * r),
                        buffer_frames=3,
                        cooldown_secs=2.0,
                        stale_secs=30.0,
                    )

            # Prune stale trackers for cameras no longer in config
            active_cams = set(self._camera_to_doors.keys())
            for old_cam in list(self._trackers):
                if old_cam not in active_cams:
                    del self._trackers[old_cam]
                    self._tripwires.pop(old_cam, None)

            self._last_reload = time.monotonic()
            if self._doors_config:
                logger.info(f"Auto-close config loaded: {len(self._doors_config)} door(s) on {len(self._camera_to_doors)} camera(s)")
        except Exception as e:
            logger.error(f"Failed to reload auto-close config: {e}")

    def on_frame(self, camera_id, detections: list, frame_height: int, db=None):
        """Gọi mỗi frame từ InferenceWorker.
        detections = [(x1, y1, x2, y2, conf, cls_id), ...]
        """
        # Periodic config reload
        now = time.monotonic()
        if now - self._last_reload > self._reload_interval:
            if db is not None:
                self.reload_config(db)

        # Check if this camera controls any doors
        cam_id = int(camera_id) if isinstance(camera_id, str) and camera_id.isdigit() else camera_id
        door_ids = self._camera_to_doors.get(cam_id, [])
        if not door_ids:
            return

        # Filter person detections only (cls_id=1)
        person_dets = [(x1, y1, x2, y2) for x1, y1, x2, y2, conf, cls_id in detections if cls_id == 1]
        has_person = len(person_dets) > 0

        # Update centroid tracker
        tracker = self._trackers.get(cam_id)
        tripwire = self._tripwires.get(cam_id)
        if not tracker or not tripwire:
            return

        tracked = tracker.update(person_dets)

        # Feed tracked persons into tripwire
        for track_id, (x1, y1, x2, y2) in tracked.items():
            center_y = int((y1 + y2) / 2)
            direction = tripwire.update(track_id, center_y)

            if direction:
                for door_id in door_ids:
                    if direction == "IN":
                        self._person_counter[door_id] = self._person_counter.get(door_id, 0) + 1
                        logger.info(f"[Door {door_id}] Person IN — counter={self._person_counter[door_id]}")
                    elif direction == "OUT":
                        self._person_counter[door_id] = max(0, self._person_counter.get(door_id, 0) - 1)
                        logger.info(f"[Door {door_id}] Person OUT — counter={self._person_counter[door_id]}")

        tripwire.cleanup_stale(set(tracked.keys()))

        # Check auto-close for each door
        for door_id in door_ids:
            config = self._doors_config.get(door_id)
            if not config or self._door_closed.get(door_id, False):
                continue

            counter = self._person_counter.get(door_id, 0)

            if has_person:
                # Person visibly present — reset timer + remember last-seen.
                self._last_person_time[door_id] = now
                self._empty_since[door_id] = None
            else:
                # No person currently detected. Self-heal a stale net counter:
                # a tracker id-switch can leave a phantom "IN" that would otherwise
                # pin counter>0 forever and never let the door auto-close. If no
                # person has been seen for the full delay, force the counter to 0.
                if counter > 0 and (now - self._last_person_time.get(door_id, now)) >= config['delay_sec']:
                    self._person_counter[door_id] = 0
                    counter = 0

                if counter > 0:
                    # Recently counted in and not yet timed out — hold the door.
                    self._empty_since[door_id] = None
                else:
                    if self._empty_since[door_id] is None:
                        self._empty_since[door_id] = now
                        logger.info(f"[Door {config['name']}] No person — starting {config['delay_sec']}s countdown")

                    elapsed = now - self._empty_since[door_id]
                    if elapsed >= config['delay_sec']:
                        self._close_door(door_id, config)

    def _close_door(self, door_id: int, config: dict):
        """Trigger cửa đóng + thông báo Telegram."""
        try:
            from backend.core.door_controller import DoorController
            controller = DoorController()
            ok = controller.control_door("CLOSE", config['relay_type'], config['relay_config'])

            if ok:
                logger.info(f"[Auto-close] Cửa '{config['name']}' đã đóng sau {config['delay_sec']}s không có người")
                self._door_closed[door_id] = True

                # Telegram notification
                try:
                    from backend.core.telegram_service import notify_telegram
                    notify_telegram(
                        f"🚪 Cửa '{config['name']}' đã tự đóng sau {config['delay_sec'] // 60} phút không có người.",
                        important=False,
                    )
                except Exception as e:
                    logger.debug(f"Telegram notify failed: {e}")

                # Log event
                try:
                    from backend.core.database import DatabaseManager
                    db = DatabaseManager()
                    db.log_door_event(door_id, 'auto_closed', f"No person for {config['delay_sec']}s")
                except Exception as e:
                    logger.debug(f"Door event log failed: {e}")
            else:
                logger.warning(f"[Auto-close] Failed to close door '{config['name']}'")
        except Exception as e:
            logger.error(f"[Auto-close] Error closing door {door_id}: {e}")

    def reset_door(self, door_id: int):
        """Reset state khi cửa được mở lại (gọi từ manual control)."""
        self._person_counter[door_id] = 0
        self._empty_since[door_id] = None
        self._door_closed[door_id] = False
        self._last_person_time[door_id] = time.monotonic()
        logger.info(f"[Door {door_id}] State reset — ready for auto-close")
