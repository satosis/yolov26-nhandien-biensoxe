import threading
import collections
from backend.services import config as cfg


class PlateVoter:
    def __init__(self, buffer_size: int = 5):
        self._buffer: collections.deque = collections.deque(maxlen=buffer_size)
        self._buffer_size = buffer_size
        self._min_conf = cfg.VOTE_MIN_CONF
        self._lock = threading.RLock()

    def add(self, text: str, conf: float):
        if text and conf >= self._min_conf:
            with self._lock:
                self._buffer.append((text, conf))

    def is_ready(self) -> bool:
        with self._lock:
            if len(self._buffer) == 0: return False
            if len(self._buffer) >= self._buffer_size: return True
            if len(self._buffer) >= 3:
                best_text, best_conf = self.best()
                if best_conf > 0.88: return True
            return False

    def best(self) -> tuple[str, float]:
        with self._lock:
            if not self._buffer: return "", 0.0
            scores: dict[str, float] = {}
            counts: dict[str, int] = {}
            for text, conf in self._buffer:
                scores[text] = scores.get(text, 0.0) + conf
                counts[text] = counts.get(text, 0) + 1
            best_text = max(scores, key=lambda t: scores[t])
            avg_conf = scores[best_text] / counts[best_text]
            return best_text, avg_conf

    def reset(self):
        with self._lock:
            self._buffer.clear()
