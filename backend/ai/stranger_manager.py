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

    def __init__(self):
        self._lock = threading.Lock()
        self._records = []
        self._matrix = None
        self._initialized = False

    @classmethod
    def get_instance(cls) -> "StrangerManager":
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

                db_ls = r.get("last_seen") or r.get("event_time")
                ls_ts = db_ls.timestamp() if hasattr(db_ls, "timestamp") else now

                records.append({
                    "id": r["id"],
                    "emb": emb,
                    "camera_id": str(r.get("camera_id") or ""),
                    "seen_count": r.get("seen_count") or 1,
                    "last_seen": ls_ts,
                    "last_db_sync": ls_ts,
                    "image_path": r.get("image_path") or "",
                    "direction": r.get("direction") or "frontal",
                })
                embs.append(emb)

            self._records = records
            self._matrix = np.array(embs, dtype=np.float32) if embs else None
            self._initialized = True
            logger.info("StrangerManager initialized with %d active stranger identities.", len(records))

    def reload(self):
        with self._lock:
            self._initialized = False
            self._records = []
            self._matrix = None
            self._ensure_loaded()

    def _update_db(self, cam_id, stranger_id, seen_count, emb=None):
        if emb is not None:
            updated = db._execute_query(
                """
                UPDATE stranger_logs
                SET last_seen = NOW(),
                    seen_count = %s,
                    camera_id = COALESCE(%s, camera_id),
                    face_embedding = %s
                WHERE id = %s
                """,
                (seen_count, cam_id, emb.tobytes(), stranger_id), commit=True
            )
        else:
            updated = db._execute_query(
                """
                UPDATE stranger_logs
                SET last_seen = NOW(),
                    seen_count = %s,
                    camera_id = COALESCE(%s, camera_id)
                WHERE id = %s
                """,
                (seen_count, cam_id, stranger_id), commit=True
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
    ) -> tuple[int, float, bool]:
        self._ensure_loaded()

        normed_emb = emb

        base_threshold = getattr(cfg, "STRANGER_MATCH_THRESHOLD", 0.35)
        now = time.time()

        match_found = False
        stranger_id = -1
        best_sim = 0.0
        need_db_sync = False
        curr_seen_count = 1

        curr_emb = None

        # Phase 1: Fast in-memory matching with lock (No I/O inside lock)
        with self._lock:
            if self._matrix is not None and len(self._matrix) > 0:
                sims = np.dot(self._matrix, normed_emb)
                best_match_idx = None
                best_match_score = -999.0

                for i, rec in enumerate(self._records):
                    s = float(sims[i])
                    time_diff = now - rec.get("last_seen", 0)
                    same_cam = (str(rec.get("camera_id") or "") == str(cam_id or ""))

                    if same_cam and time_diff < 30.0:
                        thresh = 0.20
                    elif same_cam and time_diff < 120.0:
                        thresh = 0.25
                    elif same_cam and time_diff < 300.0:
                        thresh = 0.28
                    elif time_diff < 180.0:
                        thresh = 0.30
                    else:
                        thresh = base_threshold

                    if s >= thresh:
                        # Prioritize higher similarity with a bonus for recent activity on same camera
                        score = s - thresh + (0.15 if (same_cam and time_diff < 60.0) else 0.0)
                        if score > best_match_score:
                            best_match_score = score
                            best_match_idx = i
                            best_sim = s

                if best_match_idx is not None:
                    match_found = True
                    rec = self._records[best_match_idx]
                    stranger_id = rec["id"]
                    rec["seen_count"] += 1
                    curr_seen_count = rec["seen_count"]
                    rec["last_seen"] = now
                    rec["camera_id"] = str(cam_id or "")

                    # Adaptively update running face embedding
                    updated_emb = 0.80 * rec["emb"] + 0.20 * normed_emb
                    u_norm = float(np.linalg.norm(updated_emb))
                    if u_norm > 1e-6:
                        rec["emb"] = (updated_emb / u_norm).astype(np.float32)
                        self._matrix[best_match_idx] = rec["emb"]
                    curr_emb = rec["emb"]

                    if now - rec["last_db_sync"] >= 2.0:
                        rec["last_db_sync"] = now
                        need_db_sync = True

        if match_found:
            if need_db_sync:
                self._update_db(cam_id, stranger_id, curr_seen_count, emb=curr_emb)
            return stranger_id, best_sim, False

        # Phase 1.5: Cross-process safety check against DB for recently added strangers on the same camera
        try:
            recent_row = db._execute_query(
                """
                SELECT id, face_embedding, seen_count, camera_id
                FROM stranger_logs
                WHERE status = 'new' AND camera_id = %s AND last_seen > NOW() - INTERVAL '20 seconds'
                ORDER BY id DESC LIMIT 1
                """,
                (str(cam_id or ""),), fetch=True, commit=False
            )
            if recent_row and recent_row.get("face_embedding"):
                raw_b = recent_row["face_embedding"]
                if len(raw_b) == 512 * 4:
                    e_prev = np.frombuffer(raw_b, dtype=np.float32).copy()
                    p_norm = float(np.linalg.norm(e_prev))
                    if p_norm > 1e-6:
                        e_prev /= p_norm
                        s_prev = float(np.dot(e_prev, normed_emb))
                        if s_prev >= 0.20:
                            s_id = recent_row["id"]
                            c_cnt = (recent_row.get("seen_count") or 1) + 1
                            new_emb = 0.80 * e_prev + 0.20 * normed_emb
                            new_emb /= float(np.linalg.norm(new_emb))
                            self._update_db(cam_id, s_id, c_cnt, emb=new_emb)
                            # Register into local memory cache
                            with self._lock:
                                self._records.append({
                                    "id": s_id, "emb": new_emb, "camera_id": str(cam_id or ""),
                                    "seen_count": c_cnt, "last_seen": now, "last_db_sync": now,
                                    "image_path": "", "direction": direction
                                })
                                if self._matrix is None or len(self._matrix) == 0:
                                    self._matrix = np.array([new_emb], dtype=np.float32)
                                else:
                                    self._matrix = np.vstack([self._matrix, new_emb])
                            return s_id, s_prev, False
        except Exception as e:
            logger.warning("Stranger concurrency check error: %s", e)

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
            "camera_id": str(cam_id or ""),
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


def get_stranger_manager() -> StrangerManager:
    return StrangerManager.get_instance()


def merge_duplicate_strangers(sim_threshold: float = 0.33, max_time_diff_sec: float = 600.0) -> int:
    """
    Scans all 'new' stranger_logs and groups/merges duplicates.
    Merges their seen_counts, updates the earliest event_time and latest last_seen,
    and deletes redundant duplicate rows.
    Returns the number of duplicate records merged.
    """
    rows = db._execute_query(
        """
        SELECT id, camera_id, image_path, face_embedding, seen_count, event_time, last_seen
        FROM stranger_logs
        WHERE status = 'new' AND face_embedding IS NOT NULL
        ORDER BY id ASC
        """,
        fetchall=True, commit=False
    )
    if not rows or len(rows) <= 1:
        return 0

    clusters = []
    for r in rows:
        raw_emb = r.get("face_embedding")
        if not raw_emb or len(raw_emb) != 512 * 4:
            continue
        emb = np.frombuffer(raw_emb, dtype=np.float32)
        norm = float(np.linalg.norm(emb))
        if norm <= 1e-6 or not np.isfinite(norm):
            continue
        emb = emb / norm
        r["emb"] = emb

        matched_c = None
        for c in clusters:
            sim = float(np.dot(c["emb"], emb))
            tdiff = 999999.0
            if r.get("event_time") and c.get("last_seen"):
                try:
                    tdiff = abs((r["event_time"] - c["last_seen"]).total_seconds())
                except Exception:
                    tdiff = 999999.0
            same_cam = (str(r.get("camera_id") or "") == str(c.get("camera_id") or ""))

            # Match if high similarity OR if on same camera within time window with reasonable similarity
            if sim >= sim_threshold:
                matched_c = c
                break
            elif same_cam and tdiff <= 30.0 and sim >= 0.18:
                matched_c = c
                break
            elif same_cam and tdiff <= 120.0 and sim >= 0.24:
                matched_c = c
                break
            elif same_cam and tdiff <= max_time_diff_sec and sim >= 0.28:
                matched_c = c
                break

        if matched_c:
            matched_c["members"].append(r)
            matched_c["seen_count"] += (r.get("seen_count") or 1)
            if r.get("event_time") and (not matched_c["event_time"] or r["event_time"] < matched_c["event_time"]):
                matched_c["event_time"] = r["event_time"]
            if r.get("last_seen") and (not matched_c["last_seen"] or r["last_seen"] > matched_c["last_seen"]):
                matched_c["last_seen"] = r["last_seen"]
            # Adapt embedding
            matched_c["emb"] = (0.80 * matched_c["emb"] + 0.20 * emb)
            m_norm = float(np.linalg.norm(matched_c["emb"]))
            if m_norm > 1e-6:
                matched_c["emb"] = matched_c["emb"] / m_norm
        else:
            clusters.append({
                "rep_id": r["id"],
                "camera_id": r.get("camera_id"),
                "emb": emb,
                "image_path": r.get("image_path"),
                "seen_count": r.get("seen_count") or 1,
                "event_time": r.get("event_time"),
                "last_seen": r.get("last_seen"),
                "members": [r]
            })

    total_merged = 0
    for c in clusters:
        if len(c["members"]) > 1:
            rep_id = c["rep_id"]
            dup_ids = [m["id"] for m in c["members"] if m["id"] != rep_id]
            total_merged += len(dup_ids)

            # Update master record with accumulated seen_count, event_time, last_seen
            db._execute_query(
                """
                UPDATE stranger_logs
                SET seen_count = %s,
                    event_time = %s,
                    last_seen = %s,
                    face_embedding = %s
                WHERE id = %s
                """,
                (c["seen_count"], c["event_time"], c["last_seen"], c["emb"].tobytes(), rep_id),
                commit=True
            )

            # Delete duplicate rows
            for did in dup_ids:
                db._execute_query("DELETE FROM stranger_logs WHERE id = %s", (did,), commit=True)

    if total_merged > 0:
        get_stranger_manager().reload()
        logger.info("Merged %d duplicate stranger logs into %d unique identities.", total_merged, len(clusters))

    return total_merged


