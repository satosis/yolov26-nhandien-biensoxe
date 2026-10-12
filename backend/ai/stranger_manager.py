import logging
import threading
import time
import numpy as np
from backend.services import config as cfg
from backend.util.image_utils import save_face_snapshot

logger = logging.getLogger(__name__)


class StrangerManager:
    _instance = None
    _singleton_lock = threading.Lock()

    def __init__(self, db=None):
        self._lock = threading.Lock()
        self._db = db
        self._records = []
        self._matrix = None
        self._initialized = False

    @classmethod
    def get_instance(cls, db=None) -> "StrangerManager":
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls(db=db)
                    cls._instance._ensure_loaded(db)
        elif db is not None:
            if cls._instance._db is None:
                cls._instance._db = db
            cls._instance._ensure_loaded(db)
        return cls._instance

    def _ensure_loaded(self, db):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            target_db = db or self._db
            if target_db is None:
                from backend.services.database import DatabaseManager
                target_db = DatabaseManager()
                self._db = target_db

            try:
                rows = target_db._execute_query(
                    """
                    SELECT id, camera_id, image_path, direction, face_embedding, seen_count, last_seen, event_time
                    FROM stranger_logs
                    WHERE status = 'new' AND face_embedding IS NOT NULL
                    ORDER BY COALESCE(last_seen, event_time) DESC
                    LIMIT 300
                    """,
                    fetchall=True, commit=False
                )
                records = []
                embs = []
                now = time.time()
                for r in rows:
                    raw_emb = r.get("face_embedding")
                    if not raw_emb:
                        continue
                    try:
                        emb = np.frombuffer(raw_emb, dtype=np.float32)
                        norm = float(np.linalg.norm(emb))
                        if norm > 1e-6:
                            emb = (emb / norm).astype(np.float32)
                        else:
                            continue
                    except Exception:
                        continue

                    records.append({
                        "id": r["id"],
                        "emb": emb,
                        "seen_count": r.get("seen_count") or 1,
                        "last_seen": now,
                        "last_db_sync": now,
                        "image_path": r.get("image_path") or "",
                        "direction": r.get("direction") or "frontal",
                    })
                    embs.append(emb)

                self._records = records
                self._matrix = np.array(embs, dtype=np.float32) if embs else None
                self._initialized = True
                logger.info("StrangerManager initialized with %d active stranger identities.", len(records))
            except Exception as e:
                logger.error("Failed to load stranger profiles into StrangerManager: %s", e)

    def process_stranger(
        self,
        emb: np.ndarray,
        frame,
        cam_id: str,
        bbox: tuple | list,
        direction: str = "frontal",
        site_id: int | None = None,
        db=None,
    ) -> tuple[int, float, bool]:
        if emb is None:
            return -1, 0.0, False

        self._ensure_loaded(db)
        target_db = db or self._db

        norm = float(np.linalg.norm(emb))
        if norm <= 1e-6:
            return -1, 0.0, False
        normed_emb = (emb / norm).astype(np.float32)

        threshold = getattr(cfg, "STRANGER_MATCH_THRESHOLD", 0.48)
        now = time.time()

        with self._lock:
            if self._matrix is not None and len(self._matrix) > 0:
                sims = np.dot(self._matrix, normed_emb)
                best_idx = int(np.argmax(sims))
                best_sim = float(sims[best_idx])

                if best_sim >= threshold:
                    rec = self._records[best_idx]
                    stranger_id = rec["id"]
                    rec["seen_count"] += 1
                    rec["last_seen"] = now

                    if best_sim >= 0.55:
                        updated_emb = 0.85 * rec["emb"] + 0.15 * normed_emb
                        u_norm = float(np.linalg.norm(updated_emb))
                        if u_norm > 1e-6:
                            rec["emb"] = (updated_emb / u_norm).astype(np.float32)
                            self._matrix[best_idx] = rec["emb"]

                    if now - rec["last_db_sync"] >= 5.0:
                        rec["last_db_sync"] = now
                        if target_db:
                            try:
                                target_db._execute_query(
                                    """
                                    UPDATE stranger_logs
                                    SET last_seen = NOW(),
                                        seen_count = COALESCE(seen_count, 1) + 1,
                                        camera_id = COALESCE(%s, camera_id)
                                    WHERE id = %s
                                    """,
                                    (cam_id, stranger_id), commit=True
                                )
                            except Exception as ex:
                                logger.warning("Error updating stranger %s in DB: %s", stranger_id, ex)

                    return stranger_id, best_sim, False

            snap_path = save_face_snapshot(frame, cam_id, bbox=bbox)
            new_id = None
            if target_db:
                try:
                    new_id = target_db._execute_query(
                        """
                        INSERT INTO stranger_logs
                            (camera_id, similarity, image_path, site_id, face_embedding, status, direction, event_time, seen_count, last_seen)
                        VALUES (%s, %s, %s, %s, %s, 'new', %s, NOW(), 1, NOW())
                        RETURNING id
                        """,
                        (cam_id, 0.0, snap_path, site_id, normed_emb.tobytes(), direction),
                        commit=True, return_id=True
                    )
                except Exception as ex:
                    logger.error("Failed to log new stranger into DB: %s", ex)

            if new_id is None:
                new_id = int(now) % 100000

            new_rec = {
                "id": new_id,
                "emb": normed_emb,
                "seen_count": 1,
                "last_seen": now,
                "last_db_sync": now,
                "image_path": snap_path,
                "direction": direction,
            }
            self._records.append(new_rec)
            if self._matrix is None or len(self._matrix) == 0:
                self._matrix = np.array([normed_emb], dtype=np.float32)
            else:
                self._matrix = np.vstack([self._matrix, normed_emb])

            logger.info("New stranger registered: ID #%d from %s (snapshot: %s)", new_id, cam_id, snap_path)
            return new_id, 0.0, True

    def remove(self, stranger_id: int):
        with self._lock:
            if not self._records:
                return
            idx = next((i for i, r in enumerate(self._records) if r["id"] == stranger_id), None)
            if idx is not None:
                self._records.pop(idx)
                embs = [r["emb"] for r in self._records]
                self._matrix = np.array(embs, dtype=np.float32) if embs else None
                logger.info("Stranger #%d removed from StrangerManager memory.", stranger_id)

    def reload(self, db=None):
        with self._lock:
            self._initialized = False
        self._ensure_loaded(db)


def get_stranger_manager(db=None) -> StrangerManager:
    return StrangerManager.get_instance(db=db)
