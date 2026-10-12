import logging
import threading
import time
import numpy as np
from backend.services import config as cfg
from backend.services.database import db
from backend.util.image_utils import save_face_snapshot

logger = logging.getLogger(__name__)


class StrangerManager:
    _instance = None
    _singleton_lock = threading.Lock()

    def __init__(self, *args, **kwargs):
        self._lock = threading.Lock()
        self._records = []
        self._matrix = None
        self._initialized = False

    @classmethod
    def get_instance(cls, *args, **kwargs) -> "StrangerManager":
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls()
                    cls._instance._ensure_loaded()
        return cls._instance

    def _ensure_loaded(self):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return

            rows = db._execute_query(
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
                if not isinstance(raw_emb, (bytes, bytearray, memoryview)) or len(raw_emb) != 512 * 4:
                    logger.error("Stored stranger embedding has invalid length for record %s", r["id"])
                    raise ValueError(f"Stranger {r['id']} has an invalid stored embedding size")
                emb = np.frombuffer(raw_emb, dtype=np.float32)
                if not np.isfinite(emb).all():
                    logger.error("Stored stranger embedding has non-finite values for record %s", r["id"])
                    raise ValueError(f"Stranger {r['id']} has a non-finite stored embedding")
                norm = float(np.linalg.norm(emb))
                if not np.isfinite(norm) or norm <= 1e-6:
                    logger.error("Stored stranger embedding has zero norm for record %s", r["id"])
                    raise ValueError(f"Stranger {r['id']} has a zero stored embedding")
                emb = (emb / norm).astype(np.float32)

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

    def _update_db(self, cam_id, stranger_id):
        updated = db._execute_query(
            """
            UPDATE stranger_logs
            SET last_seen = NOW(),
                seen_count = COALESCE(seen_count, 1) + 1,
                camera_id = COALESCE(%s, camera_id)
            WHERE id = %s
            """,
            (cam_id, stranger_id), commit=True
        )
        if not updated:
            logger.error("Stranger record %s was missing during database sync", stranger_id)
            raise RuntimeError(f"Stranger record {stranger_id} is missing during sync")

    def process_stranger(
        self,
        emb: np.ndarray,
        frame,
        cam_id: str,
        bbox: tuple | list,
        direction: str = "frontal",
        site_id: int | None = None,
        *args,
        **kwargs,
    ) -> tuple[int, float, bool]:
        if not isinstance(emb, np.ndarray) or emb.shape != (512,) or not np.isfinite(emb).all():
            raise ValueError("Face embedding must contain 512 finite values")

        self._ensure_loaded()

        norm = float(np.linalg.norm(emb))
        if not np.isfinite(norm) or norm <= 1e-6:
            raise ValueError("Face embedding must have a non-zero norm")
        normed_emb = (emb / norm).astype(np.float32)

        threshold = getattr(cfg, "STRANGER_MATCH_THRESHOLD", 0.48)
        now = time.time()

        match_found = False
        stranger_id = -1
        best_sim = 0.0
        need_db_sync = False

        # Phase 1: Fast in-memory matching with lock (No I/O inside lock)
        with self._lock:
            if self._matrix is not None and len(self._matrix) > 0:
                sims = np.dot(self._matrix, normed_emb)
                best_idx = int(np.argmax(sims))
                best_sim = float(sims[best_idx])

                if best_sim >= threshold:
                    match_found = True
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
                        need_db_sync = True

        if match_found:
            if need_db_sync:
                self._update_db(cam_id, stranger_id)
            return stranger_id, best_sim, False

        # Phase 2: New stranger detected. Perform disk snapshot and DB insert OUTSIDE lock!
        snap_path = save_face_snapshot(frame, cam_id, bbox=bbox)
        new_id = db._execute_query(
            """
            INSERT INTO stranger_logs
                (camera_id, similarity, image_path, site_id, face_embedding, status, direction, event_time, seen_count, last_seen)
            VALUES (%s, %s, %s, %s, %s, 'new', %s, NOW(), 1, NOW())
            RETURNING id
            """,
            (cam_id, 0.0, snap_path, site_id, normed_emb.tobytes(), direction),
            commit=True, return_id=True
        )
        if isinstance(new_id, bool) or not isinstance(new_id, int) or new_id <= 0:
            logger.error("Stranger record insert returned no valid ID")
            raise RuntimeError("Stranger record insert did not return a valid ID")

        # Phase 3: Fast memory update for the new stranger with lock
        new_rec = {
            "id": new_id,
            "emb": normed_emb,
            "seen_count": 1,
            "last_seen": now,
            "last_db_sync": now,
            "image_path": snap_path,
            "direction": direction,
        }
        with self._lock:
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

    def reload(self):
        with self._lock:
            self._initialized = False
        self._ensure_loaded()


def get_stranger_manager(*args, **kwargs) -> StrangerManager:
    return StrangerManager.get_instance(*args, **kwargs)


