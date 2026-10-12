import time
import logging
from typing import Dict, List, Tuple, Optional

from backend.ai.tripwire import TripwireTracker
from backend.services import config as cfg
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
        self._doors_config: Dict[int, dict] = {}
        self._camera_to_doors: Dict[int, List[int]] = {}
        self._last_reload = 0.0
        self._reload_interval = 60.0

    def reload_config(self, db):
        try:
            doors = db.execute_query("""
                SELECT rd.id, rd.name, rd.camera_id, rd.auto_close_delay_sec,
                       rd.relay_type, rd.relay_config, rd.site_id
                FROM rolling_doors rd
                WHERE rd.auto_close_enabled = TRUE AND rd.camera_id IS NOT NULL AND rd.is_active = TRUE
            """, fetchall=True, commit=False)
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

                if door_id not in self._person_counter:
                    self._person_counter[door_id] = 0
                    self._last_person_time[door_id] = time.monotonic()
                    self._empty_since[door_id] = None
                    self._door_closed[door_id] = False

                if cam_id not in self._trackers:
                    self._trackers[cam_id] = CentroidTracker(max_disappeared=15)

            active = set(self._doors_config.keys())
            for d in (self._person_counter, self._last_person_time,
                      self._empty_since, self._door_closed):
                for stale_id in [k for k in d if k not in active]:
                    d.pop(stale_id, None)

            line_ratio = float(get_app_setting(db, 'DOOR_TRIPWIRE_LINE_RATIO', '0.6'))

            for cam_id in self._camera_to_doors:
                if cam_id not in self._tripwires:
                    self._tripwires[cam_id] = TripwireTracker(
                        line_y_fn=lambda r=line_ratio: int(cfg.GRAB_HEIGHT * r),
                        buffer_frames=3,
                        cooldown_secs=2.0,
                        stale_secs=30.0,
                    )

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
        now = time.monotonic()
        if now - self._last_reload > self._reload_interval:
            if db is not None:
                self.reload_config(db)

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
            if not config or self._door_closed.get(door_id, False):
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
        try:
            from backend.iot.door_controller import DoorController
            controller = DoorController()
            ok = controller.control_door("CLOSE", config['relay_type'], config['relay_config'])

            if ok:
                logger.info(f"[Auto-close] Cửa '{config['name']}' đã đóng sau {config['delay_sec']}s không có người")
                self._door_closed[door_id] = True

                try:
                    from backend.services.telegram_service import notify_telegram
                    notify_telegram(
                        f"🚪 Cửa '{config['name']}' đã tự đóng sau {config['delay_sec'] // 60} phút không có người.",
                        important=False,
                    )
                except Exception as e:
                    logger.debug(f"Telegram notify failed: {e}")

                try:
                    from backend.services.database import DatabaseManager
                    db = DatabaseManager()
                    db.execute_query("""
                        INSERT INTO camera_status_log (camera_id, status, event_type, notes, logged_at)
                        VALUES (%s, %s, %s, %s, NOW())
                    """, (str(door_id), 'auto_closed', 'auto_closed', f"No person for {config['delay_sec']}s"), commit=True)
                except Exception as e:
                    logger.debug(f"Door event log failed: {e}")
        except Exception as e:
            logger.error(f"[Auto-close] Error closing door {door_id}: {e}")

    def reset_door(self, door_id: int):
        self._person_counter[door_id] = 0
        self._empty_since[door_id] = None
        self._door_closed[door_id] = False
        self._last_person_time[door_id] = time.monotonic()
        logger.info(f"[Door {door_id}] State reset — ready for auto-close")
