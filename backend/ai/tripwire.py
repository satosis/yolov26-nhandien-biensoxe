
from __future__ import annotations

import time
from collections import deque
from typing import Callable


class TripwireTracker:

    def __init__(
        self,
        line_y_fn: Callable[[], int],
        buffer_frames: int = 3,
        cooldown_secs: float = 3.0,
        stale_secs: float = 30.0,
    ) -> None:
        self._line_y_fn = line_y_fn
        self._buffer = max(1, buffer_frames)
        self._cooldown = cooldown_secs
        self._stale = stale_secs

        self._positions: dict[int, deque[bool]] = {}
        self._last_fire: dict[int, float] = {}
        self._confirmed_side: dict[int, bool | None] = {}
        self._last_seen: dict[int, float] = {}

        self._last_cleanup = 0.0
        self._current_frame_line_y = None
        self._current_frame_time = 0.0

    def update(self, obj_id: int, center_y: int) -> str | None:
        now = time.monotonic()
        if now - self._current_frame_time > 0.01:
            self._current_frame_time = now
            self._current_frame_line_y = self._line_y_fn()

        line_y = self._current_frame_line_y
        below = center_y >= line_y

        buf = self._positions.setdefault(obj_id, deque(maxlen=self._buffer))
        if obj_id not in self._confirmed_side:
            self._confirmed_side[obj_id] = None
        buf.append(below)
        self._last_seen[obj_id] = now

        if len(buf) < self._buffer:
            return None

        all_below = all(buf)
        all_above = not any(buf)
        consistent = all_below or all_above
        if not consistent:
            return None

        new_side = all_below

        prev_side = self._confirmed_side[obj_id]
        if prev_side is None:
            self._confirmed_side[obj_id] = new_side
            return None

        if new_side == prev_side:
            return None

        self._confirmed_side[obj_id] = new_side

        last = self._last_fire.get(obj_id, 0.0)
        if now - last < self._cooldown:
            return None

        self._last_fire[obj_id] = now
        return "IN" if new_side else "OUT"

    def cleanup_stale(self, active_ids: set[int] | None = None) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < 1.0:
            return
        self._last_cleanup = now

        stale_ids = [
            oid
            for oid, ts in self._last_seen.items()
            if now - ts > self._stale or (active_ids is not None and oid not in active_ids)
        ]
        for oid in stale_ids:
            self._positions.pop(oid, None)
            self._confirmed_side.pop(oid, None)
            self._last_fire.pop(oid, None)
            self._last_seen.pop(oid, None)

    def active_count(self) -> int:
        return len(self._positions)
