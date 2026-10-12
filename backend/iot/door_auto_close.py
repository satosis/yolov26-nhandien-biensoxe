import time
import logging
import json
import math
from typing import Dict, List, Tuple, Optional

from backend.ai.tripwire import TripwireTracker
from backend.iot.door_config import normalize_relay_config, with_tuya_defaults
from backend.services import config as cfg
from backend.services.database import db
from backend.services.settings_service import get_app_setting

logger = logging.getLogger("door_auto_close")


class CentroidTracker:

    def __init__(self, max_disappeared: int = 15):
        self._next_id = 0
        self._objects: Dict[int, Tuple[float, float, float, float]] = {}
        self._disappeared: Dict[int, int] = {}
        self._max_disappeared = max_disappeared

    def update(self, detections: List[Tuple[float, float, float, float]]) -> Dict[int, Tuple[float, float, float, float]]:
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

        obj_ids = list(self._objects.keys())
        obj_boxes = list(self._objects.values())
        used_dets = set()
        used_objs = set()

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

        matches.sort(key=lambda x: -x[2])
        for i, j, _ in matches:
            if i in used_objs or j in used_dets:
                continue
            oid = obj_ids[i]
            self._objects[oid] = detections[j]
            self._disappeared[oid] = 0
            used_objs.add(i)
            used_dets.add(j)

        for i in range(len(obj_ids)):
            if i not in used_objs:
                oid = obj_ids[i]
                self._disappeared[oid] += 1
                if self._disappeared[oid] > self._max_disappeared:
                    self._objects.pop(oid, None)
                    self._disappeared.pop(oid, None)

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

    def __init__(self):
        self._trackers: Dict[int, CentroidTracker] = {}
        self._tripwires: Dict[int, TripwireTracker] = {}
        self._person_counter: Dict[int, int] = {}
        self._last_person_time: Dict[int, float] = {}
        self._empty_since: Dict[int, Optional[float]] = {}
        self._door_closed: Dict[int, bool] = {}
        self._close_failed: set[int] = set()
        self._doors_config: Dict[int, dict] = {}
        self._camera_to_doors: Dict[int, List[int]] = {}
        self._last_reload = 0.0
        self._reload_interval = 60.0

    def reload_config(self):
        doors = db.execute_query("""
            SELECT rd.id, rd.name, rd.camera_id, rd.auto_close_delay_sec,
                   rd.relay_type, rd.relay_config, rd.site_id
            FROM rolling_doors rd
            WHERE rd.auto_close_enabled = TRUE AND rd.camera_id IS NOT NULL AND rd.is_active = TRUE
        """, fetchall=True, commit=False)
        line_ratio = float(get_app_setting('DOOR_TRIPWIRE_LINE_RATIO', '0.6'))
        if not math.isfinite(line_ratio) or not 0 < line_ratio < 1:
            raise ValueError("DOOR_TRIPWIRE_LINE_RATIO must be between 0 and 1")
        now = time.monotonic()
        doors_config = {}
        camera_to_doors = {}

        for door in doors:
            door_id = int(door['id'])
            cam_id = int(door['camera_id'])
            delay_sec = int(door.get('auto_close_delay_sec') or 300)
            if delay_sec <= 0:
                raise ValueError(f"Door {door_id} auto-close delay must be positive")
            relay_config = door.get('relay_config') or {}
            if isinstance(relay_config, str):
                relay_config = json.loads(relay_config)
            relay_type = door.get('relay_type') or 'http'
            if relay_type == 'tuya_local':
                relay_config = with_tuya_defaults(relay_type, relay_config, {
                    'device_id': get_app_setting('TUYA_DEVICE_ID', ''),
                    'local_key': get_app_setting('TUYA_LOCAL_KEY', ''),
                    'ip': get_app_setting('TUYA_IP', ''),
                })
            relay_config = normalize_relay_config(relay_type, relay_config)
            doors_config[door_id] = {
                'name': door['name'],
                'camera_id': cam_id,
                'delay_sec': delay_sec,
                'relay_type': relay_type,
                'relay_config': relay_config,
            }
            camera_to_doors.setdefault(cam_id, []).append(door_id)

        trackers = {
            cam_id: self._trackers.get(cam_id) or CentroidTracker(max_disappeared=15)
            for cam_id in camera_to_doors
        }
        tripwires = {
            cam_id: self._tripwires.get(cam_id) or TripwireTracker(
                line_y_fn=lambda r=line_ratio: int(cfg.GRAB_HEIGHT * r),
                buffer_frames=3,
                cooldown_secs=2.0,
                stale_secs=30.0,
            )
            for cam_id in camera_to_doors
        }
        self._close_failed = {
            door_id for door_id in self._close_failed
            if doors_config.get(door_id) == self._doors_config.get(door_id)
        }
        self._doors_config = doors_config
        self._camera_to_doors = camera_to_doors
        self._trackers = trackers
        self._tripwires = tripwires
        self._person_counter = {
            door_id: self._person_counter.get(door_id, 0) for door_id in doors_config
        }
        self._last_person_time = {
            door_id: self._last_person_time.get(door_id, now) for door_id in doors_config
        }
        self._empty_since = {
            door_id: self._empty_since.get(door_id) for door_id in doors_config
        }
        self._door_closed = {
            door_id: self._door_closed.get(door_id, False) for door_id in doors_config
        }
        self._last_reload = now
        if doors_config:
            logger.info(
                "Auto-close config loaded: %d door(s) on %d camera(s)",
                len(doors_config), len(camera_to_doors),
            )

    def on_frame(self, camera_id, detections: list, frame_height: int):
        now = time.monotonic()
        if now - self._last_reload > self._reload_interval:
            self.reload_config()

        cam_id = int(camera_id) if isinstance(camera_id, str) and camera_id.isdigit() else camera_id
        door_ids = self._camera_to_doors.get(cam_id, [])
        if not door_ids:
            return

        person_dets = [(x1, y1, x2, y2) for x1, y1, x2, y2, conf, cls_id in detections if cls_id == 1]
        has_person = len(person_dets) > 0

        tracker = self._trackers.get(cam_id)
        tripwire = self._tripwires.get(cam_id)
        if not tracker or not tripwire:
            return

        tracked = tracker.update(person_dets)

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

        for door_id in door_ids:
            config = self._doors_config.get(door_id)
            if not config or self._door_closed.get(door_id, False) or door_id in self._close_failed:
                continue

            counter = self._person_counter.get(door_id, 0)

            if has_person:
                self._last_person_time[door_id] = now
                self._empty_since[door_id] = None
            else:
                if counter > 0 and (now - self._last_person_time.get(door_id, now)) >= config['delay_sec']:
                    self._person_counter[door_id] = 0
                    counter = 0

                if counter > 0:
                    self._empty_since[door_id] = None
                else:
                    if self._empty_since[door_id] is None:
                        self._empty_since[door_id] = now
                        logger.info(f"[Door {config['name']}] No person — starting {config['delay_sec']}s countdown")

                    elapsed = now - self._empty_since[door_id]
                    if elapsed >= config['delay_sec']:
                        self._close_door(door_id, config)

    def _close_door(self, door_id: int, config: dict):
        from backend.iot.door_controller import DoorController
        controller = DoorController()
        ok = controller.control_door("CLOSE", config['relay_type'], config['relay_config'])

        if not ok:
            # False means the relay command was not sent (usually invalid configuration).
            # A new configuration or an explicit reset may make one fresh attempt.
            logger.error("Auto-close relay command was not sent for door %s", door_id)
            self._close_failed.add(door_id)
            event_written = db.execute_query("""
                INSERT INTO camera_status_log (camera_id, status, event_type, notes, logged_at)
                VALUES (%s, %s, %s, %s, NOW())
            """, (str(door_id), 'auto_close_failed', 'auto_close_failed',
                  'Relay command was not sent; check door configuration'), commit=True)

            from backend.services.telegram_service import notify_telegram
            notify_telegram(
                f"🚨 Cửa '{config['name']}' chưa thể tự đóng. Vui lòng kiểm tra cấu hình relay.",
                important=True,
            )
            if not event_written:
                logger.error("Auto-close failure event was not stored for door %s", door_id)
                raise RuntimeError(f"Auto-close failure event for door {door_id} was not stored")
            return

        logger.info(f"[Auto-close] Cửa '{config['name']}' đã đóng sau {config['delay_sec']}s không có người")
        self._door_closed[door_id] = True

        event_written = db.execute_query("""
            INSERT INTO camera_status_log (camera_id, status, event_type, notes, logged_at)
            VALUES (%s, %s, %s, %s, NOW())
        """, (str(door_id), 'auto_closed', 'auto_closed', f"No person for {config['delay_sec']}s"), commit=True)

        from backend.services.telegram_service import notify_telegram
        notify_telegram(
            f"🚪 Cửa '{config['name']}' đã tự đóng sau {config['delay_sec'] // 60} phút không có người.",
            important=False,
        )
        if not event_written:
            logger.error("Auto-close event was not stored for door %s", door_id)
            raise RuntimeError(f"Auto-close event for door {door_id} was not stored")

    def reset_door(self, door_id: int):
        self._person_counter[door_id] = 0
        self._empty_since[door_id] = None
        self._door_closed[door_id] = False
        self._close_failed.discard(door_id)
        self._last_person_time[door_id] = time.monotonic()
        logger.info(f"[Door {door_id}] State reset — ready for auto-close")
